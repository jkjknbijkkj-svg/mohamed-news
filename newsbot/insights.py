"""Collect Instagram insights and build the weekly Telegram report."""

from __future__ import annotations

import html
import logging
from datetime import datetime, timedelta
from typing import Any

from .config import Settings
from .publish import InstagramPublisher, PublishError
from .state import State, from_iso

log = logging.getLogger(__name__)


def collect_insights(settings: Settings, state: State, now: datetime) -> dict[str, Any]:
    publisher = InstagramPublisher(settings)
    cutoff = now - timedelta(days=14)
    measured = 0
    for entry in state.data["published"]:
        media_id = entry.get("ig_media_id")
        stamp = entry.get("timestamp")
        if not media_id or not stamp or from_iso(stamp) < cutoff:
            continue
        try:
            metrics = publisher.media_insights(media_id)
        except PublishError as exc:
            log.warning("Insights unavailable for %s: %s", media_id, exc)
            continue
        entry["reach"] = metrics.get("reach", 0)
        entry["likes"] = metrics.get("likes", 0)
        entry["comments"] = metrics.get("comments", 0)
        entry["shares"] = metrics.get("shares", 0)
        entry["saved"] = metrics.get("saved", 0)
        measured += 1
    followers = None
    try:
        followers = publisher.account_info().get("followers_count")
    except PublishError as exc:
        log.warning("Account info unavailable: %s", exc)
    snapshot = {"date": now.date().isoformat(), "followers": followers, "posts_measured": measured}
    state.add_insights_snapshot(snapshot)
    return snapshot


def engagement_rate(entries: list[dict[str, Any]]) -> float | None:
    reach = sum(int(e.get("reach", 0)) for e in entries)
    if reach <= 0:
        return None
    interactions = sum(
        int(e.get("likes", 0)) + int(e.get("comments", 0)) + int(e.get("saved", 0)) + int(e.get("shares", 0))
        for e in entries
    )
    return interactions / reach * 100


def weekly_report(
    state: State,
    snapshot: dict[str, Any],
    previous_followers: int | None,
    change_text: str | None,
    now: datetime,
) -> str:
    cutoff = now - timedelta(days=7)
    recent = [
        entry
        for entry in state.data["published"]
        if entry.get("timestamp") and from_iso(entry["timestamp"]) >= cutoff
    ]
    top = sorted(recent, key=lambda entry: int(entry.get("reach", 0)), reverse=True)[:3]
    lines = ["<b>تقرير الأسبوع — محمد للأخبار</b>"]
    followers = snapshot.get("followers")
    if followers is not None:
        delta = "" if previous_followers is None else f" ({int(followers) - int(previous_followers):+d})"
        lines.append(f"المتابعون: {followers}{delta}")
    lines.append(f"عدد المنشورات هذا الأسبوع: {len(recent)}")
    rate = engagement_rate(recent)
    if rate is not None:
        lines.append(f"معدل التفاعل: {rate:.1f}%")
    if top:
        lines.append("")
        lines.append("<b>أفضل المنشورات (حسب الوصول):</b>")
        for index, entry in enumerate(top, start=1):
            lines.append(
                f"{index}. {html.escape(str(entry.get('headline', '')))} — {int(entry.get('reach', 0))}\n"
                f"{html.escape(str(entry.get('permalink', '')))}"
            )
    if change_text:
        lines.append("")
        lines.append("<b>تعديل الجدول:</b> " + html.escape(change_text))
    return "\n".join(lines)
