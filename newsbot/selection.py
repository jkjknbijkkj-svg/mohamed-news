"""Choose which news item to publish: filtering, clustering, scoring."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from rapidfuzz import fuzz

from .collect import contains_term
from .models import Candidate, Item
from .state import State, from_iso

NEWS_CATEGORIES = {"ai", "ecommerce", "companies"}


def _similar(first: str, second: str) -> bool:
    if len(first.split()) < 4 or len(second.split()) < 4:
        return first.strip().lower() == second.strip().lower()
    return fuzz.token_set_ratio(first, second) >= 82


def _cluster(items: list[Item]) -> list[list[Item]]:
    clusters: list[list[Item]] = []
    for item in sorted(items, key=lambda it: it.published):
        for cluster in clusters:
            if any(
                item.url == other.url or _similar(item.title, other.title)
                for other in cluster[:6]
            ):
                cluster.append(item)
                break
        else:
            clusters.append([item])
    return clusters


def _choose_primary(items: list[Item]) -> Item:
    return min(
        items,
        key=lambda it: (not it.official, it.tier, -it.published.timestamp()),
    )


def _score(
    cluster: list[Item],
    primary: Item,
    category: str,
    cfg: dict[str, Any],
    hn_urls: set[str],
    now: datetime,
) -> float:
    scoring = cfg["scoring"]
    tier_weights = scoring["tier_weights"]
    best_tier = min(item.tier for item in cluster)
    score = float(tier_weights.get(str(best_tier), 1))
    if any(item.official for item in cluster):
        score += scoring["official_bonus"]
    domains = {item.domain for item in cluster}
    score += scoring["extra_source_bonus"] * (len(domains) - 1)
    blob = f"{primary.title} {primary.summary}"
    keywords = cfg["impact_keywords"].get(category, [])
    hits = sum(1 for keyword in keywords if contains_term(blob, keyword))
    score += scoring["impact_keyword_bonus"] * min(hits, scoring["impact_keyword_cap"])
    if any(item.url in hn_urls for item in cluster):
        score += scoring["hn_signal_bonus"]
    age_hours = max((now - primary.published).total_seconds() / 3600, 0.0)
    score += 3 * 0.5 ** (age_hours / scoring["recency_half_life_hours"])
    return score


def pick_candidates(
    items: list[Item],
    category: str,
    state: State,
    banned: dict[str, list[str]],
    cfg: dict[str, Any],
    hn_urls: set[str],
    now: datetime,
    limit: int = 5,
) -> list[Candidate]:
    max_age = cfg["limits"]["max_age_hours"]
    fresh = [
        item
        for item in items
        if 0 <= (now - item.published).total_seconds() / 3600 <= max_age
        or (now - item.published).total_seconds() < 0 and (item.published - now) < timedelta(hours=1)
    ]
    recent_titles = state.recent_titles(days=7, now=now)
    result: list[Candidate] = []
    for cluster in _cluster(fresh):
        eligible = [item for item in cluster if category in item.categories]
        if not eligible:
            continue
        domains = {item.domain for item in cluster}
        official = any(item.official for item in cluster)
        if not official and len(domains) < 2:
            continue
        if any(state.is_seen(item.url) for item in cluster):
            continue
        primary = _choose_primary(eligible)
        title_lower = primary.title.lower()
        if any(pattern in title_lower for pattern in cfg["low_value_patterns"]):
            continue
        blob = f"{primary.title} {primary.summary}"
        if any(contains_term(blob, term) for term in banned["source_blocklist"]):
            continue
        if any(_similar(primary.title, title) and fuzz.token_set_ratio(primary.title, title) >= 85 for title in recent_titles):
            continue
        score = _score(cluster, primary, category, cfg, hn_urls, now)
        result.append(Candidate(primary, cluster, score, len(domains)))
    result.sort(key=lambda candidate: candidate.score, reverse=True)
    return result[:limit]


def pick_weekly(
    state: State, now: datetime, max_items: int = 8, min_items: int = 4
) -> list[dict[str, Any]]:
    cutoff = now - timedelta(days=7)
    entries = [
        entry
        for entry in state.data["published"]
        if entry.get("category") in NEWS_CATEGORIES
        and entry.get("headline")
        and entry.get("summary")
        and entry.get("timestamp")
        and from_iso(entry["timestamp"]) >= cutoff
    ]
    entries.sort(
        key=lambda entry: (int(entry.get("reach", 0)), entry["timestamp"]),
        reverse=True,
    )
    picked: list[dict[str, Any]] = []
    per_category: dict[str, int] = {}
    for entry in entries:
        if per_category.get(entry["category"], 0) >= 3:
            continue
        picked.append(entry)
        per_category[entry["category"]] = per_category.get(entry["category"], 0) + 1
        if len(picked) >= max_items:
            break
    return picked if len(picked) >= min_items else []
