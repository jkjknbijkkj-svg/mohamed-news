"""Telegram notifications. Never raises: a failed alert must not break a run."""

from __future__ import annotations

import html
import logging
import time

import requests

from .config import Settings, redact

log = logging.getLogger(__name__)


def send(settings: Settings, text: str, parse_html: bool = True) -> bool:
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        log.warning("Telegram is not configured; message not sent")
        return False
    payload: dict[str, object] = {
        "chat_id": settings.telegram_chat_id,
        "text": text[:4000],
        "disable_web_page_preview": True,
    }
    if parse_html:
        payload["parse_mode"] = "HTML"
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    for attempt in range(3):
        try:
            response = requests.post(url, json=payload, timeout=20)
            if response.status_code == 200:
                return True
            if response.status_code == 429:
                time.sleep(5)
        except requests.RequestException as exc:
            log.warning("Telegram send failed: %s", redact(str(exc))[:150])
        time.sleep(2 * (attempt + 1))
    return False


def esc(text: str) -> str:
    return html.escape(text)
