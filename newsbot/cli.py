"""Command line entry points: run, maintenance, check-feeds, list-models."""

from __future__ import annotations

import argparse
import html
import json
import logging
import sys
import time
from datetime import datetime
from typing import Any

from . import collect, host, insights, linkpage, notify, optimizer
from .config import (
    DATA_DIR,
    OUT_DIR,
    TZ,
    ConfigError,
    Settings,
    load_sources,
    redact,
)
from .igtoken import TokenError, maybe_refresh, validate_token
from .llm import LLMClient
from .pipeline import build_plan
from .publish import InstagramPublisher
from .render import Renderer
from .slots import category_for, load_slots, resolve_slot, scheduled_at, slot_by_id
from .state import (
    MAX_FAILURES_BEFORE_PAUSE,
    State,
    is_paused,
    now_utc,
    set_paused,
    to_iso,
)

log = logging.getLogger("newsbot")

LIVE_REQUIRED = (
    "GEMINI_API_KEY",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "IG_ACCESS_TOKEN",
    "IG_USER_ID",
)
MAINTENANCE_REQUIRED = LIVE_REQUIRED + ("GH_PAT",)


class _RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = ()
        return True


def setup_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.addFilter(_RedactFilter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)


def write_heartbeat(moment: datetime) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "heartbeat.json").write_text(
        json.dumps({"last_run": to_iso(moment)}) + "\n", encoding="utf-8"
    )


def wait_until(target: datetime, early_minutes: int) -> None:
    remaining = (target - datetime.now(TZ)).total_seconds()
    if remaining > 0:
        time.sleep(min(remaining, (early_minutes + 1) * 60))


def _handle_failure(
    settings: Settings,
    state: State,
    slot_id: str,
    date_iso: str,
    stage: str,
    exc: Exception,
    dry: bool,
) -> int:
    message = redact(f"{type(exc).__name__}: {exc}")[:400]
    log.error("Failure at stage '%s': %s", stage, message)
    if dry:
        return 1
    failures = state.register_failure()
    state.save()
    notify.send(
        settings,
        "⚠️ <b>فشل النشر</b>\n"
        f"الموعد: {slot_id} ({date_iso})\nالمرحلة: {stage}\n"
        f"السبب: {html.escape(message)}\nإخفاقات متتالية: {failures}",
    )
    if failures >= MAX_FAILURES_BEFORE_PAUSE:
        set_paused(f"Paused after {failures} consecutive failures at {to_iso(now_utc())}")
        notify.send(
            settings,
            "⛔ <b>أُوقف النشر تلقائيًا</b> بعد 3 إخفاقات متتالية. "
            "احذف الملف data/PAUSED من المستودع لاستئنافه بعد إصلاح السبب.",
        )
    try:
        host.git_commit_push(["data"], "state: failure bookkeeping")
    except Exception as push_exc:  # noqa: BLE001
        log.error("Could not commit failure state: %s", redact(str(push_exc)))
    return 1


def _daily_summary(settings: Settings, state: State, date_iso: str) -> None:
    published = [e for e in state.data["published"] if e.get("date") == date_iso]
    notify.send(
        settings,
        f"📊 <b>ملخص اليوم {date_iso}</b>\nمنشورات اليوم: {len(published)}\n"
        f"إخفاقات متتالية: {state.consecutive_failures}",
    )


def cmd_run(args: argparse.Namespace) -> int:
    dry = bool(args.dry_run)
    settings = Settings.from_env(require=("GEMINI_API_KEY",) if dry else LIVE_REQUIRED)
    if not dry and is_paused():
        log.error("Posting is paused: data/PAUSED exists. Delete it to resume.")
        return 0
    state = State.load()
    slots, window = load_slots()
    now_local = datetime.now(TZ)
    if args.slot == "auto":
        due = resolve_slot(now_local, slots, window, state.is_published)
        if due is None:
            log.info("No slot is due right now.")
            return 0
        slot, target = due
    else:
        slot = slot_by_id(slots, args.slot)
        target = scheduled_at(slot, now_local.date())
    date_iso = target.date().isoformat()
    if state.is_published(date_iso, slot.id):
        log.info("Slot %s on %s is already published.", slot.id, date_iso)
        return 0
    category = category_for(slot, target.weekday())
    out_dir = OUT_DIR / date_iso / slot.id
    stage = "init"
    log.info("Running slot %s (%s) for %s, dry_run=%s", slot.id, category, date_iso, dry)
    try:
        if not dry:
            stage = "token"
            validate_token(settings)
        stage = "content"
        llm = LLMClient(settings, state)
        with Renderer() as renderer:
            plan = build_plan(
                state=state, llm=llm, renderer=renderer, slot=slot, category=category,
                date_iso=date_iso, out_dir=out_dir, now=now_utc(), now_local=now_local,
            )
        if dry:
            log.info("Dry run complete. %d image(s) in %s", len(plan.images), out_dir)
            print("\n--- CAPTION ---\n" + plan.caption + "\n---------------")
            return 0
        stage = "host"
        relative = host.stage_images(plan.images, date_iso, slot.id)
        host.git_commit_push(relative, f"media: {date_iso} {slot.id}")
        urls = [settings.media_url(rel) for rel in relative]
        host.wait_for_urls(urls)
        stage = "publish"
        publisher = InstagramPublisher(settings)
        publisher.check_quota()
        wait_until(target, int(window["early_minutes"]))
        result = publisher.publish_post(urls, plan.caption, plan.alt_texts, state, date_iso, slot.id)
    except Exception as exc:  # noqa: BLE001
        return _handle_failure(settings, state, slot.id, date_iso, stage, exc, dry)

    entry: dict[str, Any] = {
        **plan.entry,
        "date": date_iso,
        "slot": slot.id,
        "timestamp": to_iso(now_utc()),
        "ig_media_id": result["ig_media_id"],
        "permalink": result["permalink"],
    }
    state.record_published(entry)
    for url in plan.entry.get("source_urls", []):
        state.mark_seen(url)
    if plan.entry.get("repo"):
        state.mark_repo(plan.entry["repo"])
    state.register_success()
    state.prune()
    write_heartbeat(now_utc())
    state.save()
    try:
        host.git_commit_push(["data"], f"state: published {date_iso} {slot.id}")
    except Exception as exc:  # noqa: BLE001
        notify.send(
            settings,
            "⚠️ نُشر المنشور لكن تعذّر حفظ الحالة في المستودع. راجع السجل لتفادي التكرار.\n"
            + html.escape(redact(str(exc))[:200]),
        )
    log.info("Published %s: %s", slot.id, result["permalink"])
    if slot.id == "s2100":
        _daily_summary(settings, state, date_iso)
    return 0


def cmd_maintenance(args: argparse.Namespace) -> int:
    settings = Settings.from_env(require=MAINTENANCE_REQUIRED)
    state = State.load()
    now = now_utc()
    sources_cfg = load_sources()
    _, health = collect.fetch_all(sources_cfg)
    dead = [row["id"] for row in health if not row["ok"]]
    if dead:
        notify.send(settings, "⚠️ خلاصات لا تعمل حاليًا: " + html.escape(", ".join(dead)))
    try:
        validate_token(settings)
        status = maybe_refresh(settings, state, now)
        log.info("Token status: %s", status)
    except TokenError as exc:
        age = state.token_age_days(now)
        notify.send(
            settings,
            "⚠️ <b>مشكلة في رمز إنستغرام</b>\n" + html.escape(redact(str(exc))[:300])
            + (f"\nعمر الرمز: {age:.0f} يومًا" if age is not None else ""),
        )
    previous = None
    if state.data["insights"]:
        previous = state.data["insights"][-1].get("followers")
    snapshot = insights.collect_insights(settings, state, now)
    slots, _ = load_slots()
    overrides = optimizer.load_overrides()
    updated, change_text = optimizer.compute_change(state, slots, overrides, now)
    if change_text:
        optimizer.save_overrides(updated)
    linkpage.generate()
    removed = host.cleanup_old_media(now.date())
    log.info("Removed %d old media folders", removed)
    state.prune(now)
    write_heartbeat(now)
    state.save()
    host.git_commit_push(["data", "media", "docs"], "maintenance: weekly update")
    notify.send(settings, insights.weekly_report(state, snapshot, previous, change_text, now))
    return 0


def cmd_check_feeds(args: argparse.Namespace) -> int:
    _, health = collect.fetch_all(load_sources())
    bad = 0
    for row in health:
        mark = "OK  " if row["ok"] else "FAIL"
        extra = f"{row['count']} items" if row["ok"] else row["error"]
        print(f"{mark} {row['id']:<22} {extra}")
        bad += 0 if row["ok"] else 1
    print(f"\n{len(health) - bad} working, {bad} failing")
    return 0


def cmd_list_models(args: argparse.Namespace) -> int:
    from .llm import list_models

    settings = Settings.from_env(require=("GEMINI_API_KEY",))
    for name in list_models(settings):
        print(name)
    return 0


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    parser = argparse.ArgumentParser(prog="newsbot")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="publish the slot that is due")
    run.add_argument("--slot", default="auto", help="auto or a slot id such as s0800")
    run.add_argument("--dry-run", action="store_true", help="render only, never publish")
    sub.add_parser("maintenance", help="weekly report, optimizer, token refresh")
    sub.add_parser("check-feeds", help="check every RSS feed")
    sub.add_parser("list-models", help="list Gemini models available to your key")
    args = parser.parse_args(argv)
    handlers = {
        "run": cmd_run,
        "maintenance": cmd_maintenance,
        "check-feeds": cmd_check_feeds,
        "list-models": cmd_list_models,
    }
    try:
        return handlers[args.command](args)
    except ConfigError as exc:
        log.error("Configuration error: %s", exc)
        return 2
