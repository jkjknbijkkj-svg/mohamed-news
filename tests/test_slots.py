from datetime import datetime

from newsbot.config import TZ
from newsbot.slots import Slot, resolve_slot, scheduled_at

EVERY_DAY = tuple(range(7))
SLOTS = [
    Slot("s0800", 8, 0, "ai", "single", EVERY_DAY, True),
    Slot("s1800", 18, 0, "companies", "single", EVERY_DAY, True),
    Slot("weekly", 19, 0, "weekly", "weekly", (4,), False),
]
WINDOW = {"early_minutes": 40, "late_minutes": 90}


def at(year, month, day, hour, minute):
    return datetime(year, month, day, hour, minute, tzinfo=TZ)


def never_done(date_iso, slot_id):
    return False


def test_due_inside_window():
    result = resolve_slot(at(2026, 11, 9, 7, 45), SLOTS, WINDOW, never_done)
    assert result is not None and result[0].id == "s0800"


def test_too_early_returns_none():
    assert resolve_slot(at(2026, 11, 9, 7, 10), SLOTS, WINDOW, never_done) is None


def test_too_late_returns_none():
    assert resolve_slot(at(2026, 11, 9, 9, 45), SLOTS, WINDOW, never_done) is None


def test_friday_weekly_after_evening_slot_is_done():
    result = resolve_slot(
        at(2026, 11, 6, 18, 35), SLOTS, WINDOW, lambda d, s: s == "s1800"
    )
    assert result is not None and result[0].id == "weekly"


def test_monday_has_no_weekly():
    result = resolve_slot(
        at(2026, 11, 9, 18, 35), SLOTS, WINDOW, lambda d, s: s == "s1800"
    )
    assert result is None


def test_scheduled_at_uses_local_time():
    target = scheduled_at(SLOTS[0], datetime(2026, 11, 9).date())
    assert (target.hour, target.minute) == (8, 0)
