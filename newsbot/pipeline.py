"""Build the content of one post: news, GitHub repository, or weekly summary."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from . import collect, selection
from .caption import (
    build_github_caption,
    build_news_caption,
    build_weekly_caption,
    format_date_range,
    shorten_summary,
)
from .config import load_banned, load_sources
from .llm import LLMBadOutput, LLMClient, generate_github, generate_news
from .models import Candidate, PostPlan
from .render import Renderer, TextTooLongError, render_github, render_news, render_weekly
from .slots import Slot
from .state import State
from .validate import normalize_github, normalize_news, validate_github, validate_news

log = logging.getLogger(__name__)
MAX_LLM_CALLS_PER_SLOT = 4


class NoContent(Exception):
    """No suitable content could be produced for this slot."""


def build_plan(
    *,
    state: State,
    llm: LLMClient,
    renderer: Renderer,
    slot: Slot,
    category: str,
    date_iso: str,
    out_dir: Path,
    now: datetime,
    now_local: datetime,
) -> PostPlan:
    if slot.format == "weekly":
        return _weekly(state, renderer, slot, out_dir, now, now_local)
    if slot.format == "carousel" or category == "github":
        return _github(state, llm, renderer, slot, out_dir, now)
    return _news(state, llm, renderer, slot, category, out_dir, now)


def _gather_text(candidate: Candidate, limits: dict[str, Any]) -> str | None:
    max_chars = int(limits["max_text_chars"])
    min_chars = int(limits["min_text_chars"])
    ordered = [candidate.primary] + [i for i in candidate.cluster if i is not candidate.primary]
    best = ""
    for item in ordered[:3]:
        text = collect.fetch_article_text(item.url, max_chars)
        if len(text) > len(best):
            best = text
        if len(best) >= min_chars:
            return best
    combined = " ".join(item.summary for item in candidate.cluster if item.summary)
    if len(combined) >= 250:
        return combined[:max_chars]
    return None


def _news(
    state: State,
    llm: LLMClient,
    renderer: Renderer,
    slot: Slot,
    category: str,
    out_dir: Path,
    now: datetime,
) -> PostPlan:
    sources_cfg = load_sources()
    banned = load_banned()
    items, health = collect.fetch_all(sources_cfg)
    failed = [row["id"] for row in health if not row["ok"]]
    if failed:
        log.warning("Feeds failing: %s", ", ".join(failed))
    if not items:
        raise NoContent("no feed items could be fetched")
    hn_urls = collect.fetch_hn_urls(sources_cfg["signals"])
    candidates = selection.pick_candidates(items, category, state, banned, sources_cfg, hn_urls, now)
    if not candidates:
        raise NoContent(f"no eligible candidates for category {category}")
    calls = 0
    last_problem = ""
    for candidate in candidates:
        text = _gather_text(candidate, sources_cfg["limits"])
        if text is None:
            last_problem = "source text too short"
            continue
        primary = candidate.primary
        source_text = f"{primary.title}\n{primary.summary}\n{text}"
        feedback = ""
        for _ in range(2):
            if calls >= MAX_LLM_CALLS_PER_SLOT:
                raise NoContent("LLM call limit reached for this slot: " + last_problem)
            calls += 1
            try:
                data = normalize_news(
                    generate_news(llm, category, primary.source_name, primary.title, text, feedback)
                )
            except LLMBadOutput as exc:
                last_problem = str(exc)
                feedback = f"The previous answer was invalid ({exc}). Return valid JSON only."
                continue
            problems = validate_news(data, source_text, banned)
            if not problems:
                try:
                    image = render_news(
                        renderer, category, data["headline"], data["summary"],
                        primary.source_name, out_dir / f"{slot.id}-1.jpg",
                    )
                except TextTooLongError:
                    problems = ["the text does not fit the image: shorten the headline and the summary"]
                else:
                    return PostPlan(
                        slot_id=slot.id,
                        category=category,
                        images=[image],
                        caption=build_news_caption(data, primary.source_name, category),
                        alt_texts=[data["alt_text"]],
                        entry={
                            "category": category,
                            "title": primary.title,
                            "headline": data["headline"],
                            "summary": data["summary"],
                            "source_name": primary.source_name,
                            "source_urls": [item.url for item in candidate.cluster],
                            "hashtags": data["hashtags"],
                        },
                    )
            last_problem = "; ".join(problems)
            feedback = "Fix these problems and return the full JSON again: " + last_problem
    raise NoContent("all candidates failed validation: " + last_problem)


def _github(
    state: State,
    llm: LLMClient,
    renderer: Renderer,
    slot: Slot,
    out_dir: Path,
    now: datetime,
) -> PostPlan:
    cfg = load_sources()["github"]
    banned = load_banned()
    repos = collect.fetch_github_candidates(cfg, state, banned, now)
    if not repos:
        raise NoContent("no GitHub repository candidates")
    calls = 0
    last_problem = ""
    for repo in repos[:3]:
        repo.readme = collect.fetch_readme(repo.full_name, int(cfg["readme_max_chars"]))
        if len(repo.readme) < 200:
            last_problem = f"{repo.full_name}: README too short"
            continue
        source_text = f"{repo.full_name}\n{repo.description}\n{repo.readme}"
        feedback = ""
        for _ in range(2):
            if calls >= MAX_LLM_CALLS_PER_SLOT:
                raise NoContent("LLM call limit reached for this slot: " + last_problem)
            calls += 1
            try:
                data = normalize_github(generate_github(llm, repo, feedback))
            except LLMBadOutput as exc:
                last_problem = str(exc)
                feedback = f"The previous answer was invalid ({exc}). Return valid JSON only."
                continue
            problems = validate_github(data, source_text, repo.readme, banned)
            if not problems:
                try:
                    images = render_github(renderer, repo, data, out_dir, slot.id)
                except TextTooLongError:
                    problems = ["the text does not fit the images: shorten every field"]
                else:
                    name = repo.full_name.split("/")[1]
                    return PostPlan(
                        slot_id=slot.id,
                        category="github",
                        images=images,
                        caption=build_github_caption(data, repo),
                        alt_texts=[f"الشريحة {i} من {len(images)} عن مستودع {name}" for i in range(1, len(images) + 1)],
                        entry={
                            "category": "github",
                            "title": repo.full_name,
                            "headline": data["tagline"],
                            "summary": data["what_it_does"],
                            "source_name": "GitHub",
                            "source_urls": [repo.html_url],
                            "repo": repo.full_name,
                            "hashtags": data["hashtags"],
                        },
                    )
            last_problem = "; ".join(problems)
            feedback = "Fix these problems and return the full JSON again: " + last_problem
    raise NoContent("all repositories failed validation: " + last_problem)


def _weekly(
    state: State,
    renderer: Renderer,
    slot: Slot,
    out_dir: Path,
    now: datetime,
    now_local: datetime,
) -> PostPlan:
    entries = selection.pick_weekly(state, now)
    if not entries:
        raise NoContent("not enough published posts this week for a summary")
    end = now_local.date()
    date_range = format_date_range(end - timedelta(days=6), end)
    images = render_weekly(renderer, entries, date_range, out_dir, slot.id, shorten_summary)
    return PostPlan(
        slot_id=slot.id,
        category="weekly",
        images=images,
        caption=build_weekly_caption(entries, date_range),
        alt_texts=[f"الشريحة {i} من {len(images)} من ملخص الأسبوع" for i in range(1, len(images) + 1)],
        entry={
            "category": "weekly",
            "title": f"weekly {date_range}",
            "headline": f"ملخص الأسبوع {date_range}",
            "source_urls": [],
        },
    )
