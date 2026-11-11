"""Weekly schedule optimizer: move one weekday slot from the weakest to the strongest category."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from .config import DATA_DIR
from .models import CATEGORY_LABELS_AR, WEEKDAYS_AR
from .slots import Slot, category_for, day_overrides
from .state import State, from_iso

SINGLE_CATEGORIES = ("ai", "ecommerce", "companies")
MIN_POSTS_PER_CATEGORY = 3
MIN_WEEKLY_CELLS = 4
MIN_ADVANTAGE = 1.15


def load_overrides() -> dict[str, str]:
    return day_overrides()


def save_overrides(overrides: dict[str, str]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / "schedule_overrides.json"
    path.write_text(
        json.dumps({"slot_categories": overrides}, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def category_averages(state: State, now: datetime, days: int = 14) -> dict[str, float]:
    cutoff = now - timedelta(days=days)
    sums: dict[str, float] = {}
    counts: dict[str, int] = {}
    for entry in state.data["published"]:
        category = entry.get("category")
        stamp = entry.get("timestamp")
        if category not in SINGLE_CATEGORIES or "reach" not in entry or not stamp:
            continue
        if from_iso(stamp) < cutoff:
            continue
        sums[category] = sums.get(category, 0.0) + float(entry["reach"])
        counts[category] = counts.get(category, 0) + 1
    return {c: sums[c] / counts[c] for c in counts if counts[c] >= MIN_POSTS_PER_CATEGORY}


def compute_change(
    state: State, slots: list[Slot], overrides: dict[str, str], now: datetime
) -> tuple[dict[str, str], str | None]:
    averages = category_averages(state, now)
    if len(averages) < len(SINGLE_CATEGORIES):
        return overrides, None
    best = max(averages, key=lambda c: averages[c])
    worst = min(averages, key=lambda c: averages[c])
    if best == worst or averages[best] < averages[worst] * MIN_ADVANTAGE:
        return overrides, None
    cells: list[tuple[str, int, str]] = []
    for slot in slots:
        if not slot.reassignable:
            continue
        for weekday in slot.days:
            cells.append((slot.id, weekday, category_for(slot, weekday, overrides)))
    worst_cells = [cell for cell in cells if cell[2] == worst]
    if len(worst_cells) <= MIN_WEEKLY_CELLS:
        return overrides, None
    slot_id, weekday, _ = sorted(worst_cells, key=lambda cell: (cell[1], cell[0]))[-1]
    updated: dict[str, Any] = dict(overrides)
    updated[f"{slot_id}:{weekday}"] = best
    text = (
        f"نُقل موعد {slot_id} يوم {WEEKDAYS_AR[weekday]} من «{CATEGORY_LABELS_AR[worst]}» "
        f"إلى «{CATEGORY_LABELS_AR[best]}»"
    )
    return updated, text
