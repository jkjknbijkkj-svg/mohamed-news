"""Pure validation rules for model output. Every rule is unit-tested."""

from __future__ import annotations

import re
from typing import Any

ARABIC_LETTERS = re.compile(r"[\u0621-\u064A\u0671-\u06D3]")
LATIN_LETTERS = re.compile(r"[A-Za-z]")
LATIN_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:[.\-+][A-Za-z0-9]+)*")
NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
EMOJI = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF]")
URL = re.compile(r"https?://|www\.", re.I)
HASHTAG_OK = re.compile(r"^[\u0621-\u064A\u0671-\u06D3_]{2,30}$")
SENTENCE_SPLIT = re.compile(r"[.!؟?]+")
ALLOWED_LATIN = {"ai", "api", "gpu", "cpu", "ceo", "usd", "eur", "llm"}
_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
UNSAFE_COMMAND = re.compile(r"\bsudo\b|rm\s+-rf|\|\s*(sh|bash)\b|chmod\s+777", re.I)


def normalize_digits(text: str) -> str:
    return text.translate(_DIGITS).replace("٫", ".").replace("٬", ",")


def numbers_in(text: str) -> set[str]:
    found: set[str] = set()
    for raw in NUMBER.findall(normalize_digits(text)):
        found.add(raw.replace(",", ""))
        found.add(raw.replace(",", "."))
    return found


def arabic_ratio(text: str) -> float:
    arabic = len(ARABIC_LETTERS.findall(text))
    latin = len(LATIN_LETTERS.findall(text))
    total = arabic + latin
    return 1.0 if total == 0 else arabic / total


def _clean_str(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _clean_tags(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    tags = []
    for tag in value:
        text = re.sub(r"\s+", "_", _clean_str(tag).lstrip("#"))
        if text:
            tags.append(text)
    return tags


def normalize_news(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "headline": _clean_str(data.get("headline")),
        "summary": _clean_str(data.get("summary")),
        "hook": _clean_str(data.get("hook")),
        "alt_text": _clean_str(data.get("alt_text")),
        "hashtags": _clean_tags(data.get("hashtags")),
    }


def normalize_github(data: dict[str, Any]) -> dict[str, Any]:
    commands = data.get("how_to_start")
    if not isinstance(commands, list):
        commands = []
    return {
        "tagline": _clean_str(data.get("tagline")),
        "what_it_does": _clean_str(data.get("what_it_does")),
        "who_for": _clean_str(data.get("who_for")),
        "how_to_start": [_clean_str(c) for c in commands if _clean_str(c)][:3],
        "hook": _clean_str(data.get("hook")),
        "alt_text": _clean_str(data.get("alt_text")),
        "hashtags": _clean_tags(data.get("hashtags")),
    }


def _common_checks(
    texts: dict[str, str], source_text: str, banned: dict[str, list[str]]
) -> list[str]:
    problems: list[str] = []
    for key, value in texts.items():
        if not value:
            problems.append(f"missing field: {key}")
    joined = " ".join(texts.values())
    if URL.search(joined):
        problems.append("text contains a URL")
    if EMOJI.search(joined):
        problems.append("text contains emoji")
    if arabic_ratio(joined) < 0.7:
        problems.append("text must be Modern Standard Arabic (too many Latin letters)")
    extra_numbers = numbers_in(joined) - numbers_in(source_text)
    if extra_numbers:
        problems.append("numbers not found in the source: " + ", ".join(sorted(extra_numbers)))
    source_lower = source_text.lower()
    unknown = sorted(
        {
            token
            for token in LATIN_TOKEN.findall(joined)
            if len(token) >= 2
            and token.lower() not in ALLOWED_LATIN
            and token.lower() not in source_lower
        }
    )
    if unknown:
        problems.append("Latin names not found in the source: " + ", ".join(unknown))
    lowered = joined.lower()
    for key in ("rumor_phrases_ar", "advice_phrases_ar", "spam_phrases_ar"):
        for phrase in banned.get(key, []):
            if phrase and phrase in lowered:
                problems.append(f"forbidden phrase: {phrase}")
    return problems


def _check_tags(tags: list[str]) -> list[str]:
    problems: list[str] = []
    if not 3 <= len(tags) <= 6:
        problems.append("hashtags must be 3 to 6 Arabic words")
    for tag in tags:
        if not HASHTAG_OK.match(tag):
            problems.append(f"hashtag must be Arabic letters and underscores only: {tag}")
    return problems


def _sentence_count(text: str) -> int:
    return len([part for part in SENTENCE_SPLIT.split(text) if part.strip()])


def validate_news(
    data: dict[str, Any], source_text: str, banned: dict[str, list[str]]
) -> list[str]:
    texts = {key: data.get(key, "") for key in ("headline", "summary", "hook", "alt_text")}
    problems = _common_checks(texts, source_text, banned)
    words = len(texts["headline"].split())
    if not 6 <= words <= 14:
        problems.append(f"headline has {words} words, it must have 8 to 12")
    summary = texts["summary"]
    if summary and not 80 <= len(summary) <= 420:
        problems.append(f"summary has {len(summary)} characters, it must have 80 to 380")
    if summary and not 2 <= _sentence_count(summary) <= 5:
        problems.append("summary must have 3 to 4 sentences")
    if len(texts["hook"]) > 140:
        problems.append("hook is too long")
    if len(texts["alt_text"]) > 200:
        problems.append("alt_text is too long")
    problems.extend(_check_tags(data.get("hashtags", [])))
    return problems


def validate_github(
    data: dict[str, Any],
    source_text: str,
    readme: str,
    banned: dict[str, list[str]],
) -> list[str]:
    texts = {
        key: data.get(key, "")
        for key in ("tagline", "what_it_does", "who_for", "hook", "alt_text")
    }
    problems = _common_checks(texts, source_text, banned)
    words = len(texts["tagline"].split())
    if not 6 <= words <= 14:
        problems.append(f"tagline has {words} words, it must have 8 to 12")
    if texts["what_it_does"] and not 60 <= len(texts["what_it_does"]) <= 360:
        problems.append("what_it_does must have 60 to 320 characters")
    if len(texts["who_for"]) > 240:
        problems.append("who_for is too long")
    if len(texts["hook"]) > 140:
        problems.append("hook is too long")
    if len(texts["alt_text"]) > 200:
        problems.append("alt_text is too long")
    for command in data.get("how_to_start", []):
        if command not in readme:
            problems.append(f"command is not literally in the README: {command[:40]}")
        if len(command) > 90:
            problems.append("a command is too long")
        if UNSAFE_COMMAND.search(command):
            problems.append("an unsafe command was suggested")
    problems.extend(_check_tags(data.get("hashtags", [])))
    return problems
