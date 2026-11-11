"""Build Instagram captions and small text helpers."""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from .models import RepoInfo

MAX_CAPTION = 2200
CTA = "رابط مشاريعنا في السيرة الذاتية"
FIXED_HASHTAGS = {
    "ai": ["الذكاء_الاصطناعي", "تقنية", "أخبار_التقنية"],
    "ecommerce": ["التجارة_الإلكترونية", "تجارة_إلكترونية", "ريادة_الأعمال"],
    "companies": ["أخبار_الشركات", "تقنية", "أخبار_التقنية"],
    "github": ["جيت_هاب", "برمجة", "مفتوح_المصدر"],
    "weekly": ["ملخص_الأسبوع", "أخبار_التقنية", "الذكاء_الاصطناعي"],
}
MONTHS_AR = [
    "يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو",
    "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر",
]


def hashtags(topic_tags: list[str], category: str, limit: int = 8) -> str:
    tags: list[str] = []
    seen: set[str] = set()
    for tag in [*topic_tags, *FIXED_HASHTAGS.get(category, [])]:
        clean = tag.strip().lstrip("#")
        if clean and clean not in seen:
            seen.add(clean)
            tags.append("#" + clean)
        if len(tags) >= limit:
            break
    return " ".join(tags)


def _fit(text: str) -> str:
    return text if len(text) <= MAX_CAPTION else text[: MAX_CAPTION - 1].rstrip() + "…"


def build_news_caption(content: dict[str, Any], source_name: str, category: str) -> str:
    return _fit(
        "\n\n".join(
            [
                content["hook"],
                content["summary"],
                f"المصدر: {source_name}",
                CTA,
                hashtags(content["hashtags"], category),
            ]
        )
    )


def build_github_caption(content: dict[str, Any], repo: RepoInfo) -> str:
    stats = f"⭐ {repo.stars:,}" + (f" · {repo.language}" if repo.language else "")
    return _fit(
        "\n\n".join(
            [
                content["hook"],
                content["what_it_does"],
                f"{repo.full_name}\n{stats}\nالمصدر: GitHub — github.com/{repo.full_name}",
                CTA,
                hashtags(content["hashtags"], "github"),
            ]
        )
    )


def build_weekly_caption(entries: list[dict[str, Any]], date_range: str) -> str:
    footer = "\n\n".join([CTA, hashtags([], "weekly")])
    head = f"ملخص الأسبوع ({date_range}): أهم ما نشرناه"
    lines = [f"{index}. {entry['headline']}" for index, entry in enumerate(entries, start=1)]
    while lines:
        text = head + "\n\n" + "\n".join(lines) + "\n\n" + footer
        if len(text) <= MAX_CAPTION:
            return text
        lines.pop()
    return head + "\n\n" + footer


def shorten_summary(summary: str, max_chars: int = 230) -> str:
    sentences = [s.strip() for s in re.split(r"(?<=[.!؟?])\s+", summary.strip()) if s.strip()]
    result = ""
    for sentence in sentences[:2]:
        candidate = (result + " " + sentence).strip()
        if len(candidate) > max_chars and result:
            break
        result = candidate
    if len(result) > max_chars:
        result = result[: max_chars - 1].rstrip() + "…"
    return result


def format_date_range(start: date, end: date) -> str:
    if start.month == end.month and start.year == end.year:
        return f"{start.day}–{end.day} {MONTHS_AR[end.month - 1]} {end.year}"
    return (
        f"{start.day} {MONTHS_AR[start.month - 1]} – "
        f"{end.day} {MONTHS_AR[end.month - 1]} {end.year}"
    )
