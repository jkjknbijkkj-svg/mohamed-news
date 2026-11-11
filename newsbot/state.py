"""Persistent state stored in data/state.json (committed by the workflow)."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import DATA_DIR

STATE_PATH = DATA_DIR / "state.json"
PAUSE_PATH = DATA_DIR / "PAUSED"

SEEN_URL_DAYS = 30
REPO_COOLDOWN_DAYS = 90
MAX_PUBLISHED = 400
MAX_INSIGHTS = 60
LLM_USAGE_DAYS = 7
MAX_FAILURES_BEFORE_PAUSE = 3


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def to_iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def from_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _default_state() -> dict[str, Any]:
    return {
        "version": 1,
        "published": [],
        "pending": {},
        "seen_urls": {},
        "posted_repos": {},
        "consecutive_failures": 0,
        "token": {
            "issued_at": None,
            "last_refresh": None,
            "last_refresh_error": None,
        },
        "llm_usage": {},
        "insights": [],
    }


class State:
    def __init__(self, data: dict[str, Any], path: Path = STATE_PATH) -> None:
        self.data = data
        self.path = path

    @classmethod
    def load(cls, path: Path = STATE_PATH) -> "State":
        data = _default_state()
        if path.exists():
            with path.open("r", encoding="utf-8") as fh:
                loaded = json.load(fh)
            if isinstance(loaded, dict):
                data.update(loaded)
        defaults = _default_state()
        for key, value in defaults.items():
            data.setdefault(key, value)
        for key, value in defaults["token"].items():
            data["token"].setdefault(key, value)
        return cls(data, path)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=self.path.parent, prefix=".state-", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self.data, fh, ensure_ascii=False, indent=2, sort_keys=True)
                fh.write("\n")
            os.replace(tmp_name, self.path)
        except BaseException:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
            raise

    # ----- published posts and idempotency -------------------------------

    @staticmethod
    def slot_key(date: str, slot_id: str) -> str:
        return f"{date}|{slot_id}"

    def is_published(self, date: str, slot_id: str) -> bool:
        return any(
            entry.get("date") == date and entry.get("slot") == slot_id
            for entry in self.data["published"]
        )

    def get_pending(self, date: str, slot_id: str) -> dict[str, Any] | None:
        return self.data["pending"].get(self.slot_key(date, slot_id))

    def set_pending(self, date: str, slot_id: str, info: dict[str, Any]) -> None:
        self.data["pending"][self.slot_key(date, slot_id)] = info

    def clear_pending(self, date: str, slot_id: str) -> None:
        self.data["pending"].pop(self.slot_key(date, slot_id), None)

    def record_published(self, entry: dict[str, Any]) -> None:
        for key in ("date", "slot"):
            if key not in entry:
                raise ValueError(f"Published entry is missing '{key}'")
        self.data["published"].append(entry)
        self.clear_pending(entry["date"], entry["slot"])
        if len(self.data["published"]) > MAX_PUBLISHED:
            self.data["published"] = self.data["published"][-MAX_PUBLISHED:]

    def recent_titles(self, days: int = 7, now: datetime | None = None) -> list[str]:
        cutoff = (now or now_utc()) - timedelta(days=days)
        titles: list[str] = []
        for entry in self.data["published"]:
            stamp = entry.get("timestamp")
            title = entry.get("title")
            if not stamp or not title:
                continue
            if from_iso(stamp) >= cutoff:
                titles.append(title)
        return titles

    # ----- seen URLs and GitHub repositories ----------------------------

    def is_seen(self, url: str) -> bool:
        return url in self.data["seen_urls"]

    def mark_seen(self, url: str, when: datetime | None = None) -> None:
        self.data["seen_urls"][url] = to_iso(when or now_utc())

    def repo_recently_posted(self, full_name: str) -> bool:
        return full_name.lower() in self.data["posted_repos"]

    def mark_repo(self, full_name: str, when: datetime | None = None) -> None:
        self.data["posted_repos"][full_name.lower()] = to_iso(when or now_utc())

    # ----- failures -------------------------------------------------------

    @property
    def consecutive_failures(self) -> int:
        return int(self.data["consecutive_failures"])

    def register_failure(self) -> int:
        self.data["consecutive_failures"] = self.consecutive_failures + 1
        return self.consecutive_failures

    def register_success(self) -> None:
        self.data["consecutive_failures"] = 0

    # ----- LLM usage budget -----------------------------------------------

    def llm_used(self, date: str, model: str | None = None) -> int:
        day = self.data["llm_usage"].get(date, {})
        if model is None:
            return sum(int(count) for count in day.values())
        return int(day.get(model, 0))

    def llm_add(self, date: str, model: str, count: int = 1) -> None:
        day = self.data["llm_usage"].setdefault(date, {})
        day[model] = int(day.get(model, 0)) + count

    # ----- token bookkeeping ---------------------------------------------

    @property
    def token(self) -> dict[str, Any]:
        return self.data["token"]

    def token_age_days(self, now: datetime | None = None) -> float | None:
        reference = self.token.get("last_refresh") or self.token.get("issued_at")
        if not reference:
            return None
        return ((now or now_utc()) - from_iso(reference)).total_seconds() / 86400

    # ----- insights --------------------------------------------------------

    def add_insights_snapshot(self, snapshot: dict[str, Any]) -> None:
        self.data["insights"].append(snapshot)
        if len(self.data["insights"]) > MAX_INSIGHTS:
            self.data["insights"] = self.data["insights"][-MAX_INSIGHTS:]

    # ----- maintenance ------------------------------------------------------

    def prune(self, now: datetime | None = None) -> None:
        moment = now or now_utc()
        seen_cutoff = moment - timedelta(days=SEEN_URL_DAYS)
        self.data["seen_urls"] = {
            url: stamp
            for url, stamp in self.data["seen_urls"].items()
            if from_iso(stamp) >= seen_cutoff
        }
        repo_cutoff = moment - timedelta(days=REPO_COOLDOWN_DAYS)
        self.data["posted_repos"] = {
            name: stamp
            for name, stamp in self.data["posted_repos"].items()
            if from_iso(stamp) >= repo_cutoff
        }
        usage_dates = sorted(self.data["llm_usage"])[-LLM_USAGE_DAYS:]
        self.data["llm_usage"] = {
            day: self.data["llm_usage"][day] for day in usage_dates
        }


def is_paused() -> bool:
    return PAUSE_PATH.exists()


def set_paused(reason: str) -> None:
    PAUSE_PATH.parent.mkdir(parents=True, exist_ok=True)
    PAUSE_PATH.write_text(reason.strip() + "\n", encoding="utf-8")
