"""Central configuration: paths, environment settings, JSON config loaders."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
MEDIA_DIR = ROOT / "media"
ASSETS_DIR = ROOT / "assets"
TEMPLATES_DIR = ROOT / "templates"
DOCS_DIR = ROOT / "docs"
OUT_DIR = ROOT / "out"

TIMEZONE_NAME = "Africa/Algiers"
TZ = ZoneInfo(TIMEZONE_NAME)

ACCOUNT_HANDLE = "@_mohamednews_"
ACCOUNT_NAME_AR = "محمد للأخبار"

DEFAULT_REPOSITORY = "jkjknbijkkj-svg/mohamed-news"
DEFAULT_BRANCH = "main"
USER_AGENT = (
    "mohamed-news-bot/1.0 (+https://github.com/jkjknbijkkj-svg/mohamed-news)"
)

VALID_CATEGORIES = {"ai", "ecommerce", "companies"}
VALID_FORMATS = {"single", "carousel", "weekly"}
SECRET_ENV_NAMES = (
    "GEMINI_API_KEY",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "IG_ACCESS_TOKEN",
    "IG_USER_ID",
    "GH_PAT",
    "BACKUP_LLM_API_KEY",
)


class ConfigError(Exception):
    """Raised when configuration or environment is missing or invalid."""


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer") from exc


@dataclass(frozen=True)
class Settings:
    gemini_api_key: str = field(default="", repr=False)
    telegram_bot_token: str = field(default="", repr=False)
    telegram_chat_id: str = field(default="", repr=False)
    ig_access_token: str = field(default="", repr=False)
    ig_user_id: str = field(default="", repr=False)
    gh_pat: str = field(default="", repr=False)
    backup_llm_api_key: str = field(default="", repr=False)
    backup_llm_base_url: str = ""
    backup_llm_model: str = ""
    gemini_model: str = "gemini-3.6-flash"
    gemini_fallback_models: tuple[str, ...] = ()
    llm_daily_budget: int = 16
    ig_graph_base: str = "https://graph.instagram.com"
    ig_api_version: str = ""
    repository: str = DEFAULT_REPOSITORY
    branch: str = DEFAULT_BRANCH
    image_host_mode: str = "raw"

    @classmethod
    def from_env(cls, require: tuple[str, ...] = ()) -> "Settings":
        env = os.environ
        missing = [name for name in require if not env.get(name, "").strip()]
        if missing:
            raise ConfigError(
                "Missing required environment variables: " + ", ".join(missing)
            )

        def get(name: str, default: str = "") -> str:
            return env.get(name, default).strip()

        fallbacks = tuple(
            item.strip()
            for item in get("GEMINI_FALLBACK_MODELS").split(",")
            if item.strip()
        )
        host_mode = get("IMAGE_HOST_MODE", "raw").lower()
        if host_mode not in {"raw", "pages"}:
            raise ConfigError("IMAGE_HOST_MODE must be 'raw' or 'pages'")

        return cls(
            gemini_api_key=get("GEMINI_API_KEY"),
            telegram_bot_token=get("TELEGRAM_BOT_TOKEN"),
            telegram_chat_id=get("TELEGRAM_CHAT_ID"),
            ig_access_token=get("IG_ACCESS_TOKEN"),
            ig_user_id=get("IG_USER_ID"),
            gh_pat=get("GH_PAT"),
            backup_llm_api_key=get("BACKUP_LLM_API_KEY"),
            backup_llm_base_url=get("BACKUP_LLM_BASE_URL"),
            backup_llm_model=get("BACKUP_LLM_MODEL"),
            gemini_model=get("GEMINI_MODEL", "gemini-3.6-flash"),
            gemini_fallback_models=fallbacks,
            llm_daily_budget=_env_int("LLM_DAILY_BUDGET", 16),
            ig_graph_base=get("IG_GRAPH_BASE") or ("https://graph.facebook.com" if get("IG_ACCESS_TOKEN").startswith("EAA") else "https://graph.instagram.com"),
            ig_api_version=get("IG_API_VERSION"),
            repository=get("GITHUB_REPOSITORY", DEFAULT_REPOSITORY),
            branch=get("GITHUB_BRANCH", DEFAULT_BRANCH),
            image_host_mode=host_mode,
        )

    @property
    def owner(self) -> str:
        return self.repository.split("/", 1)[0]

    @property
    def repo_name(self) -> str:
        return self.repository.split("/", 1)[1]

    def graph_url(self, path: str) -> str:
        base = self.ig_graph_base.rstrip("/")
        version = f"/{self.ig_api_version.strip('/')}" if self.ig_api_version else ""
        return f"{base}{version}/{path.lstrip('/')}"

    def media_url(self, relative_path: str) -> str:
        rel = relative_path.lstrip("/")
        if self.image_host_mode == "pages":
            return f"https://{self.owner}.github.io/{self.repo_name}/{rel}"
        return (
            f"https://raw.githubusercontent.com/{self.repository}/"
            f"{self.branch}/{rel}"
        )


def redact(text: str) -> str:
    """Replace any secret value found in the environment with ***."""
    result = text
    for name in SECRET_ENV_NAMES:
        value = os.environ.get(name, "").strip()
        if len(value) >= 6:
            result = result.replace(value, "***")
    return result


def load_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError as exc:
        raise ConfigError(f"Missing config file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Invalid JSON in {path}: {exc}") from exc


def parse_hhmm(value: str) -> tuple[int, int]:
    try:
        hour_text, minute_text = value.split(":")
        hour, minute = int(hour_text), int(minute_text)
    except (ValueError, AttributeError) as exc:
        raise ConfigError(f"Invalid time '{value}', expected HH:MM") from exc
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ConfigError(f"Invalid time '{value}', out of range")
    return hour, minute


def load_sources() -> dict[str, Any]:
    data = load_json(CONFIG_DIR / "sources.json")
    sources = data.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ConfigError("config/sources.json has no sources")
    seen_ids: set[str] = set()
    for src in sources:
        for key in ("id", "name", "category", "type", "url", "domain"):
            if not src.get(key):
                raise ConfigError(f"Source is missing '{key}': {src}")
        if src["id"] in seen_ids:
            raise ConfigError(f"Duplicate source id: {src['id']}")
        seen_ids.add(src["id"])
        if src["category"] not in VALID_CATEGORIES:
            raise ConfigError(f"Bad category in source {src['id']}")
        src.setdefault("official", False)
        src.setdefault("tier", 3)
        src.setdefault("aliases", [])
        src.setdefault("also_categories", [])
        for extra in src["also_categories"]:
            if extra not in VALID_CATEGORIES:
                raise ConfigError(f"Bad also_category in source {src['id']}")
    for key in ("limits", "scoring", "github", "impact_keywords"):
        if key not in data:
            raise ConfigError(f"config/sources.json is missing '{key}'")
    data.setdefault("low_value_patterns", [])
    data.setdefault("signals", [])
    return data


def load_schedule() -> dict[str, Any]:
    data = load_json(CONFIG_DIR / "schedule.json")
    overrides: dict[str, str] = {}
    overrides_path = DATA_DIR / "schedule_overrides.json"
    if overrides_path.exists():
        overrides = load_json(overrides_path).get("slot_categories", {})

    slots = data.get("slots")
    if not isinstance(slots, list) or not slots:
        raise ConfigError("config/schedule.json has no slots")
    seen: set[str] = set()
    for slot in slots:
        for key in ("id", "time", "category", "format", "days"):
            if key not in slot:
                raise ConfigError(f"Slot is missing '{key}': {slot}")
        if slot["id"] in seen:
            raise ConfigError(f"Duplicate slot id: {slot['id']}")
        seen.add(slot["id"])
        parse_hhmm(slot["time"])
        if slot["format"] not in VALID_FORMATS:
            raise ConfigError(f"Bad format in slot {slot['id']}")
        if not all(isinstance(d, int) and 0 <= d <= 6 for d in slot["days"]):
            raise ConfigError(f"Bad days in slot {slot['id']}")
        slot.setdefault("reassignable", False)
        override = overrides.get(slot["id"])
        if slot["reassignable"] and override in VALID_CATEGORIES:
            slot["category"] = override

    window = data.setdefault("window", {})
    window.setdefault("early_minutes", 40)
    window.setdefault("late_minutes", 90)
    return data


def load_banned() -> dict[str, list[str]]:
    data = load_json(CONFIG_DIR / "banned_terms.json")
    result: dict[str, list[str]] = {}
    for key in (
        "source_blocklist",
        "rumor_phrases_ar",
        "advice_phrases_ar",
        "spam_phrases_ar",
    ):
        values = data.get(key, [])
        if not isinstance(values, list):
            raise ConfigError(f"banned_terms.json '{key}' must be a list")
        result[key] = [str(v).lower() for v in values]
    return result


def load_links() -> dict[str, str]:
    data = load_json(CONFIG_DIR / "links.json")
    return {
        "dz_url": str(data.get("dz_url", "")).strip(),
        "world_url": str(data.get("world_url", "")).strip(),
    }
