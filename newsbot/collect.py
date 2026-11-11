"""Fetch news (RSS), Hacker News signals, article text and GitHub repositories."""

from __future__ import annotations

import html
import logging
import os
import re
import time
import urllib.parse
import urllib.robotparser
from datetime import datetime, timedelta, timezone
from typing import Any

import feedparser
import requests
import trafilatura

from .config import USER_AGENT, redact
from .models import Item, RepoInfo
from .state import State

log = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"
_DROP_PARAMS = {"fbclid", "gclid", "ref", "ref_src", "mc_cid", "mc_eid"}
_robots_cache: dict[str, urllib.robotparser.RobotFileParser | None] = {}


def normalize_url(url: str) -> str:
    parts = urllib.parse.urlsplit(url.strip())
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    kept = [
        (key, value)
        for key, value in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in _DROP_PARAMS
    ]
    path = parts.path.rstrip("/") or "/"
    return urllib.parse.urlunsplit(
        (parts.scheme.lower() or "https", host, path, urllib.parse.urlencode(kept), "")
    )


def host_of(url: str) -> str:
    host = urllib.parse.urlsplit(url).netloc.lower().split(":")[0]
    return host[4:] if host.startswith("www.") else host


def domain_matches(host: str, domain: str) -> bool:
    domain = domain.lower()
    if domain.startswith("www."):
        domain = domain[4:]
    return host == domain or host.endswith("." + domain)


def strip_html(text: str) -> str:
    cleaned = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", html.unescape(cleaned)).strip()


def contains_term(text: str, term: str) -> bool:
    term = term.strip().lower()
    if not term:
        return False
    pattern = r"(?<!\w)" + re.escape(term) + r"(?!\w)"
    return re.search(pattern, text.lower()) is not None


def _entry_datetime(entry: Any) -> datetime | None:
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        value = entry.get(key)
        if value:
            return datetime(*value[:6], tzinfo=timezone.utc)
    return None


def fetch_feed(
    session: requests.Session, source: dict[str, Any], limits: dict[str, Any]
) -> list[Item]:
    response = session.get(
        source["url"],
        timeout=limits["timeout_seconds"],
        headers={
            "User-Agent": USER_AGENT,
            "Accept": (
                "application/rss+xml, application/atom+xml, "
                "application/xml;q=0.9, text/xml;q=0.8, */*;q=0.5"
            ),
        },
    )
    response.raise_for_status()
    parsed = feedparser.parse(response.content)
    if not parsed.entries:
        raise ValueError("feed has no entries")
    categories = [source["category"], *source["also_categories"]]
    domains = [source["domain"], *source["aliases"]]
    items: list[Item] = []
    for entry in parsed.entries[: limits["max_items_per_feed"]]:
        title = strip_html(entry.get("title", ""))
        link = (entry.get("link") or "").strip()
        published = _entry_datetime(entry)
        if not title or not link or published is None:
            continue
        host = host_of(link)
        if not any(domain_matches(host, domain) for domain in domains):
            continue
        summary = strip_html(entry.get("summary") or entry.get("description") or "")
        items.append(
            Item(
                source_id=source["id"],
                source_name=source["name"],
                domain=source["domain"].lower(),
                tier=int(source["tier"]),
                official=bool(source["official"]),
                categories=list(categories),
                title=title,
                url=normalize_url(link),
                published=published,
                summary=summary[:1500],
            )
        )
    return items


def fetch_all(sources_cfg: dict[str, Any]) -> tuple[list[Item], list[dict[str, Any]]]:
    session = requests.Session()
    items: list[Item] = []
    health: list[dict[str, Any]] = []
    for source in sources_cfg["sources"]:
        try:
            fetched = fetch_feed(session, source, sources_cfg["limits"])
            items.extend(fetched)
            health.append(
                {"id": source["id"], "ok": True, "count": len(fetched), "error": ""}
            )
        except Exception as exc:  # noqa: BLE001 - one dead feed must not stop the run
            health.append(
                {
                    "id": source["id"],
                    "ok": False,
                    "count": 0,
                    "error": redact(str(exc))[:200],
                }
            )
        time.sleep(0.4)
    return items, health


def fetch_hn_urls(signals_cfg: list[dict[str, Any]]) -> set[str]:
    urls: set[str] = set()
    session = requests.Session()
    for signal in signals_cfg:
        if signal.get("type") != "hn":
            continue
        try:
            ids = session.get(signal["url"], timeout=20).json()[: signal.get("top_n", 60)]
            for story_id in ids:
                data = session.get(
                    signal["item_url"].format(id=story_id), timeout=15
                ).json()
                url = (data or {}).get("url")
                if url:
                    urls.add(normalize_url(url))
        except Exception as exc:  # noqa: BLE001
            log.warning("Hacker News signal failed: %s", redact(str(exc)))
    return urls


def _robots_allows(session: requests.Session, url: str) -> bool:
    parts = urllib.parse.urlsplit(url)
    origin = f"{parts.scheme}://{parts.netloc}"
    if origin not in _robots_cache:
        parser: urllib.robotparser.RobotFileParser | None = None
        try:
            response = session.get(
                origin + "/robots.txt", timeout=10, headers={"User-Agent": USER_AGENT}
            )
            if response.status_code == 200:
                parser = urllib.robotparser.RobotFileParser()
                parser.parse(response.text.splitlines())
        except requests.RequestException:
            parser = None
        _robots_cache[origin] = parser
    parser = _robots_cache[origin]
    return True if parser is None else parser.can_fetch(USER_AGENT, url)


def fetch_article_text(url: str, max_chars: int, timeout: int = 20) -> str:
    session = requests.Session()
    try:
        if not _robots_allows(session, url):
            return ""
        response = session.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT})
        response.raise_for_status()
        text = trafilatura.extract(
            response.text,
            include_comments=False,
            include_tables=False,
            favor_precision=True,
        )
        return (text or "")[:max_chars]
    except Exception as exc:  # noqa: BLE001
        log.info("Could not read article %s: %s", url, redact(str(exc))[:120])
        return ""


def _github_headers(raw: bool = False) -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github.raw+json" if raw else "application/vnd.github+json",
        "User-Agent": USER_AGENT,
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def fetch_github_candidates(
    cfg: dict[str, Any],
    state: State,
    banned: dict[str, list[str]],
    now: datetime,
) -> list[RepoInfo]:
    since = (now - timedelta(days=int(cfg["window_days"]))).strftime("%Y-%m-%d")
    query = f"created:>{since} stars:>{int(cfg['min_stars'])}"
    response = requests.get(
        f"{GITHUB_API}/search/repositories",
        params={
            "q": query,
            "sort": "stars",
            "order": "desc",
            "per_page": int(cfg["candidates"]),
        },
        headers=_github_headers(),
        timeout=30,
    )
    response.raise_for_status()
    repos: list[RepoInfo] = []
    for raw in response.json().get("items", []):
        if raw.get("fork") or raw.get("archived") or raw.get("disabled"):
            continue
        description = (raw.get("description") or "").strip()
        license_info = raw.get("license") or {}
        if not description or not license_info:
            continue
        full_name = raw["full_name"]
        if state.repo_recently_posted(full_name):
            continue
        topics = list(raw.get("topics") or [])
        blob = f"{full_name} {description} {' '.join(topics)}"
        if any(contains_term(blob, term) for term in banned["source_blocklist"]):
            continue
        repos.append(
            RepoInfo(
                full_name=full_name,
                html_url=raw["html_url"],
                description=description,
                stars=int(raw.get("stargazers_count", 0)),
                language=raw.get("language"),
                pushed_at=str(raw.get("pushed_at") or ""),
                license_name=license_info.get("spdx_id") or license_info.get("name"),
                topics=topics,
            )
        )
    boost = {topic.lower() for topic in cfg["boost_topics"]}
    repos.sort(
        key=lambda repo: (len(boost & {t.lower() for t in repo.topics}), repo.stars),
        reverse=True,
    )
    return repos


def fetch_readme(full_name: str, max_chars: int) -> str:
    try:
        response = requests.get(
            f"{GITHUB_API}/repos/{full_name}/readme",
            headers=_github_headers(raw=True),
            timeout=30,
        )
        response.raise_for_status()
        return response.text[:max_chars]
    except requests.RequestException as exc:
        log.info("README unavailable for %s: %s", full_name, redact(str(exc))[:120])
        return ""
