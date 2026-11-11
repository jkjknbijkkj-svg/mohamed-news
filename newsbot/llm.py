"""Gemini client with retries, a per-model daily budget and an optional backup provider."""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime
from typing import Any, Callable
from zoneinfo import ZoneInfo

import requests

from .config import Settings, redact
from .models import CATEGORY_LABELS_AR, RepoInfo
from .state import State

log = logging.getLogger(__name__)
PACIFIC = ZoneInfo("America/Los_Angeles")
RETRY_DELAYS = (5, 20, 45)
TRANSIENT_MARKERS = (
    "503", "500", "502", "504", "unavailable", "internal", "deadline",
    "timed out", "timeout", "overloaded", "connection",
)


class LLMUnavailable(Exception):
    """No configured model can serve the request right now."""


class LLMBadOutput(Exception):
    """The model answered but not with usable JSON."""


class LLMError(Exception):
    """Error from one provider."""


class TransientLLMError(LLMError):
    """Temporary provider error (overload, timeout): worth retrying."""


class QuotaExceeded(Exception):
    """Provider quota exhausted."""


SYSTEM_PROMPT = (
    "You are the Arabic news editor of an Instagram news account. "
    "You write in Modern Standard Arabic (Fusha) only, never in dialect.\n"
    "Hard rules:\n"
    "1. Use ONLY facts that appear in SOURCE_TEXT. Never add numbers, names, dates, "
    "quotes, causes or predictions that are not literally in SOURCE_TEXT.\n"
    "2. Keep brand, product and company names in Latin letters exactly as written in "
    "SOURCE_TEXT. Do not transliterate them.\n"
    "3. Neutral tone. No opinions, no hype, no emojis, no hashtags inside text fields, "
    "no URLs.\n"
    "4. No investment or stock advice. Do not repeat rumors; if SOURCE_TEXT says "
    "something is unconfirmed, leave it out.\n"
    "5. Rewrite in your own words and keep it short. Never copy sentences from "
    "SOURCE_TEXT.\n"
    "6. Output a single JSON object and nothing else."
)

NEWS_TEMPLATE = """Write a post for the category "<<CATEGORY>>".
SOURCE_NAME: <<SOURCE_NAME>>
SOURCE_TITLE: <<TITLE>>
SOURCE_TEXT:
<<TEXT>>

Return JSON with exactly these keys:
"headline": Arabic headline of 8 to 12 words,
"summary": Arabic summary of 3 to 4 sentences, at most 380 characters,
"hook": one short Arabic opening sentence (at most 120 characters) that makes the reader curious without exaggeration,
"hashtags": list of 3 to 4 Arabic hashtag words without the # sign, using underscores instead of spaces,
"alt_text": Arabic image description of at most 180 characters.
<<FEEDBACK>>"""

GITHUB_TEMPLATE = """Write a post about an open-source GitHub repository for Arabic-speaking developers and builders.
REPOSITORY: <<NAME>>
DESCRIPTION: <<DESCRIPTION>>
README_TEXT (this is SOURCE_TEXT):
<<TEXT>>

Return JSON with exactly these keys:
"tagline": Arabic one-line tagline of 8 to 12 words,
"what_it_does": Arabic explanation of 2 to 3 sentences, at most 320 characters,
"who_for": Arabic sentence or two saying who should use it, at most 200 characters,
"how_to_start": list of at most 3 shell commands copied LITERALLY from README_TEXT, or an empty list if README_TEXT has none,
"hook": one short Arabic opening sentence (at most 120 characters),
"hashtags": list of 3 to 4 Arabic hashtag words without the # sign, using underscores instead of spaces,
"alt_text": Arabic image description of at most 180 characters.
<<FEEDBACK>>"""


def parse_json(text: str) -> dict[str, Any]:
    cleaned = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", text.strip(), flags=re.S)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, flags=re.S)
        if not match:
            raise LLMBadOutput("the answer is not JSON") from None
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise LLMBadOutput("the answer is not valid JSON") from exc
    if not isinstance(data, dict):
        raise LLMBadOutput("the JSON answer is not an object")
    return data


def _is_transient(message: str) -> bool:
    lowered = message.lower()
    return any(marker in lowered for marker in TRANSIENT_MARKERS)


class LLMClient:
    def __init__(self, settings: Settings, state: State) -> None:
        self.settings = settings
        self.state = state
        self.models = [settings.gemini_model, *settings.gemini_fallback_models]
        self._client: Any = None

    @staticmethod
    def _quota_date() -> str:
        return datetime.now(PACIFIC).date().isoformat()

    def _gemini(self) -> Any:
        if self._client is None:
            from google import genai

            self._client = genai.Client(api_key=self.settings.gemini_api_key)
        return self._client

    def _call_gemini(self, model: str, system: str, user: str) -> str:
        from google.genai import types

        try:
            response = self._gemini().models.generate_content(
                model=model,
                contents=user,
                config=types.GenerateContentConfig(
                    system_instruction=system,
                    response_mime_type="application/json",
                    temperature=0.2,
                    max_output_tokens=8192,
                ),
            )
        except Exception as exc:  # noqa: BLE001
            message = str(exc)
            if "429" in message or "RESOURCE_EXHAUSTED" in message:
                raise QuotaExceeded(redact(message)[:200]) from exc
            if _is_transient(message):
                raise TransientLLMError(redact(message)[:300]) from exc
            raise LLMError(redact(message)[:300]) from exc
        text = getattr(response, "text", None) or ""
        if not text.strip():
            raise TransientLLMError("empty response")
        return text

    def _call_backup(self, system: str, user: str) -> str:
        s = self.settings
        try:
            response = requests.post(
                s.backup_llm_base_url.rstrip("/") + "/chat/completions",
                headers={"Authorization": f"Bearer {s.backup_llm_api_key}"},
                json={
                    "model": s.backup_llm_model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "temperature": 0.2,
                    "response_format": {"type": "json_object"},
                },
                timeout=90,
            )
        except requests.RequestException as exc:
            raise TransientLLMError(redact(str(exc))[:200]) from exc
        if response.status_code == 429:
            raise QuotaExceeded("backup provider quota exceeded")
        if response.status_code >= 500:
            raise TransientLLMError(f"backup provider HTTP {response.status_code}")
        if response.status_code >= 400:
            raise LLMError(f"backup provider HTTP {response.status_code}")
        try:
            return response.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, ValueError) as exc:
            raise LLMError("unexpected backup provider response") from exc

    @staticmethod
    def _with_retries(call: Callable[[], str], label: str) -> str:
        last: TransientLLMError | None = None
        for delay in (0, *RETRY_DELAYS):
            if delay:
                log.info("%s is busy, retrying in %s seconds", label, delay)
                time.sleep(delay)
            try:
                return call()
            except TransientLLMError as exc:
                last = exc
        raise last or TransientLLMError("provider unavailable")

    def generate_json(self, system: str, user: str) -> dict[str, Any]:
        date = self._quota_date()
        budget = self.settings.llm_daily_budget
        reasons: list[str] = []
        for model in self.models:
            if self.state.llm_used(date, model) >= budget:
                reasons.append(f"{model}: daily budget reached")
                continue
            try:
                text = self._with_retries(
                    lambda: self._call_gemini(model, system, user), model
                )
            except QuotaExceeded:
                self.state.llm_add(date, model, budget)
                reasons.append(f"{model}: quota exceeded")
                continue
            except LLMError as exc:
                reasons.append(f"{model}: {exc}")
                continue
            self.state.llm_add(date, model)
            return parse_json(text)
        s = self.settings
        if s.backup_llm_base_url and s.backup_llm_api_key and s.backup_llm_model:
            if self.state.llm_used(date, "backup") < budget:
                try:
                    text = self._with_retries(
                        lambda: self._call_backup(system, user), "backup"
                    )
                except (QuotaExceeded, LLMError) as exc:
                    reasons.append(f"backup: {exc}")
                else:
                    self.state.llm_add(date, "backup")
                    return parse_json(text)
        raise LLMUnavailable("; ".join(reasons) or "no model is configured")


def generate_news(
    llm: LLMClient,
    category: str,
    source_name: str,
    title: str,
    text: str,
    feedback: str = "",
) -> dict[str, Any]:
    user = (
        NEWS_TEMPLATE.replace("<<CATEGORY>>", CATEGORY_LABELS_AR[category])
        .replace("<<SOURCE_NAME>>", source_name)
        .replace("<<TITLE>>", title)
        .replace("<<TEXT>>", text[:9000])
        .replace("<<FEEDBACK>>", feedback)
    )
    return llm.generate_json(SYSTEM_PROMPT, user)


def generate_github(llm: LLMClient, repo: RepoInfo, feedback: str = "") -> dict[str, Any]:
    user = (
        GITHUB_TEMPLATE.replace("<<NAME>>", repo.full_name)
        .replace("<<DESCRIPTION>>", repo.description)
        .replace("<<TEXT>>", repo.readme[:9000])
        .replace("<<FEEDBACK>>", feedback)
    )
    return llm.generate_json(SYSTEM_PROMPT, user)


def list_models(settings: Settings) -> list[str]:
    from google import genai

    client = genai.Client(api_key=settings.gemini_api_key)
    return sorted(str(getattr(model, "name", model)) for model in client.models.list())
