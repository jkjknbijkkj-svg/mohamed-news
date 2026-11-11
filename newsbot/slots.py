"""Slot schedule helpers: which slot is due, and its category per weekday."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Callable

from .config import (
    DATA_DIR,
    TZ,
    VALID_CATEGORIES,
    load_json,
    load_schedule,
    parse_hhmm,
)


@dataclass(frozen=True)
class Slot:
    id: str
    hour: int
    minute: int
    category: str
    format: str
    days: tuple[int, ...]
    reassignable: bool


def load_slots() -> tuple[list[Slot], dict[str, Any]]:
    data = load_schedule()
    slots: list[Slot] = []
    for raw in data["slots"]:
        hour, minute = parse_hhmm(raw["time"])
        slots.append(
            Slot(
                id=raw["id"],
                hour=hour,
                minute=minute,
                category=raw["category"],
                format=raw["format"],
                days=tuple(raw["days"]),
                reassignable=bool(raw["reassignable"]),
            )
        )
    return slots, data["window"]


def day_overrides() -> dict[str, str]:
    """Per-weekday category overrides, keys look like 's0800:3'."""
    path = DATA_DIR / "schedule_overrides.json"
    if not path.exists():
        return {}
    table = load_json(path).get("slot_categories", {})
    return {key: value for key, value in table.items() if ":" in key}


def category_for(
    slot: Slot, weekday: int, overrides: dict[str, str] | None = None
) -> str:
    if slot.reassignable:
        table = overrides if overrides is not None else day_overrides()
        category = table.get(f"{slot.id}:{weekday}")
        if category in VALID_CATEGORIES:
            return category
    return slot.category


def slot_by_id(slots: list[Slot], slot_id: str) -> Slot:
    for slot in slots:
        if slot.id == slot_id:
            return slot
    raise ValueError(f"Unknown slot id: {slot_id}")


def scheduled_at(slot: Slot, day: date) -> datetime:
    return datetime(day.year, day.month, day.day, slot.hour, slot.minute, tzinfo=TZ)


def resolve_slot(
    now: datetime,
    slots: list[Slot],
    window: dict[str, Any],
    is_done: Callable[[str, str], bool],
) -> tuple[Slot, datetime] | None:
    """Pick the earliest slot that is inside its time window and not done yet."""
    early = timedelta(minutes=int(window["early_minutes"]))
    late = timedelta(minutes=int(window["late_minutes"]))
    local_now = now.astimezone(TZ)
    today = local_now.date()
    due: list[tuple[datetime, Slot]] = []
    for slot in slots:
        if local_now.weekday() not in slot.days:
            continue
        target = scheduled_at(slot, today)
        if target - early <= local_now <= target + late:
            if not is_done(today.isoformat(), slot.id):
                due.append((target, slot))
    if not due:
        return None
    due.sort(key=lambda pair: pair[0])
    target, slot = due[0]
    return slot, target
