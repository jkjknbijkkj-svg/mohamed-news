from newsbot.optimizer import compute_change
from newsbot.slots import Slot
from newsbot.state import State, now_utc, to_iso

EVERY_DAY = tuple(range(7))


def test_moves_one_cell_from_weakest_to_strongest(tmp_path):
    state = State.load(tmp_path / "s.json")
    now = now_utc()
    for index in range(4):
        for category, reach in (("ai", 1000), ("ecommerce", 100), ("companies", 500)):
            state.data["published"].append(
                {
                    "category": category,
                    "reach": reach,
                    "timestamp": to_iso(now),
                    "date": f"2026-11-0{index + 1}",
                    "slot": f"{category}{index}",
                }
            )
    slots = [
        Slot("s0800", 8, 0, "ai", "single", EVERY_DAY, True),
        Slot("s1230", 12, 30, "ecommerce", "single", EVERY_DAY, True),
        Slot("s1800", 18, 0, "companies", "single", EVERY_DAY, True),
    ]
    overrides, text = compute_change(state, slots, {}, now)
    assert text is not None
    assert overrides == {"s1230:6": "ai"}


def test_no_change_without_enough_data(tmp_path):
    state = State.load(tmp_path / "s.json")
    slots = [Slot("s0800", 8, 0, "ai", "single", EVERY_DAY, True)]
    overrides, text = compute_change(state, slots, {}, now_utc())
    assert text is None and overrides == {}
