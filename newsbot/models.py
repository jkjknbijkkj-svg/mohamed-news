"""Shared data structures and Arabic labels."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

CATEGORY_LABELS_AR = {
    "ai": "الذكاء الاصطناعي",
    "ecommerce": "التجارة الإلكترونية",
    "companies": "أخبار الشركات",
    "github": "مستودع GitHub",
    "weekly": "ملخص الأسبوع",
}

WEEKDAYS_AR = ["الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"]


@dataclass
class Item:
    source_id: str
    source_name: str
    domain: str
    tier: int
    official: bool
    categories: list[str]
    title: str
    url: str
    published: datetime
    summary: str = ""


@dataclass
class Candidate:
    primary: Item
    cluster: list[Item]
    score: float
    distinct_domains: int


@dataclass
class RepoInfo:
    full_name: str
    html_url: str
    description: str
    stars: int
    language: str | None
    pushed_at: str
    license_name: str | None
    topics: list[str]
    readme: str = ""


@dataclass
class PostPlan:
    slot_id: str
    category: str
    images: list[Path]
    caption: str
    alt_texts: list[str]
    entry: dict[str, Any] = field(default_factory=dict)
