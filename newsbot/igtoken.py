"""Instagram token validation, refresh, and GitHub secret update."""

from __future__ import annotations

import base64
import logging
import time
from datetime import datetime
from typing import Any

import requests

from .config import USER_AGENT, Settings, redact
from .state import State, now_utc, to_iso

log = logging.getLogger(__name__)
REFRESH_AFTER_DAYS = 6.5
WARN_DAYS_LEFT = 10


class TokenError(Exception):
    """Token invalid, or refresh failed."""


def validate_token(settings: Settings) -> dict[str, Any]:
    try:
        response = requests.get(
            settings.graph_url(settings.ig_user_id),
            params={"fields": "username", "access_token": settings.ig_access_token},
            timeout=30,
        )
    except requests.RequestException as exc:
        raise TokenError(redact(str(exc))[:200]) from exc
    if response.status_code >= 400:
        raise TokenError(
            f"token check failed: HTTP {response.status_code} {redact(response.text)[:200]}"
        )
    return response.json()


def token_days_left(settings: Settings) -> float | None:
    """Days until a Facebook-type token expires; None when unknown or never."""
    token = settings.ig_access_token
    try:
        response = requests.get(
            "https://graph.facebook.com/debug_token",
            params={"input_token": token, "access_token": token},
            timeout=30,
        )
        data = response.json().get("data", {})
        expires = int(data.get("expires_at", 0))
        if expires == 0:
            return None
        return (expires - time.time()) / 86400
    except Exception:  # noqa: BLE001
        return None


def refresh_token(settings: Settings) -> str:
    url = settings.ig_graph_base.rstrip("/") + "/refresh_access_token"
    try:
        response = requests.get(
            url,
            params={"grant_type": "ig_refresh_token", "access_token": settings.ig_access_token},
            timeout=30,
        )
    except requests.RequestException as exc:
        raise TokenError(redact(str(exc))[:200]) from exc
    if response.status_code >= 400:
        raise TokenError(
            f"refresh failed: HTTP {response.status_code} {redact(response.text)[:200]}"
        )
    token = response.json().get("access_token")
    if not token:
        raise TokenError("refresh answer has no access_token")
    return str(token)


def update_github_secret(settings: Settings, name: str, value: str) -> None:
    from nacl import encoding, public

    headers = {
        "Authorization": f"Bearer {settings.gh_pat}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": USER_AGENT,
    }
    base = f"https://api.github.com/repos/{settings.repository}/actions/secrets"
    key_response = requests.get(f"{base}/public-key", headers=headers, timeout=30)
    if key_response.status_code != 200:
        raise TokenError(
            f"could not read the repository public key: HTTP {key_response.status_code}"
        )
    key = key_response.json()
    public_key = public.PublicKey(key["key"].encode("utf-8"), encoding.Base64Encoder())
    sealed = public.SealedBox(public_key).encrypt(value.encode("utf-8"))
    payload = {
        "encrypted_value": base64.b64encode(sealed).decode("utf-8"),
        "key_id": key["key_id"],
    }
    put = requests.put(f"{base}/{name}", headers=headers, json=payload, timeout=30)
    if put.status_code not in (201, 204):
        raise TokenError(f"GitHub secret update failed: HTTP {put.status_code}")


def maybe_refresh(settings: Settings, state: State, now: datetime | None = None) -> str:
    moment = now or now_utc()
    token = state.token
    if settings.ig_access_token.startswith("EAA"):
        # Facebook-type token: it cannot be refreshed here, only watched.
        token["issued_at"] = token.get("issued_at") or to_iso(moment)
        days = token_days_left(settings)
        if days is not None and days < WARN_DAYS_LEFT:
            raise TokenError(
                f"The token expires in {days:.0f} days. Generate a new token and save it."
            )
        return "facebook-token"
    if not token.get("issued_at") and not token.get("last_refresh"):
        token["issued_at"] = to_iso(moment)
        return "initialized"
    age = state.token_age_days(moment)
    if age is not None and age < REFRESH_AFTER_DAYS:
        return "fresh"
    try:
        new_token = refresh_token(settings)
        update_github_secret(settings, "IG_ACCESS_TOKEN", new_token)
    except Exception as exc:  # noqa: BLE001
        token["last_refresh_error"] = redact(str(exc))[:300]
        raise TokenError(token["last_refresh_error"]) from exc
    token["last_refresh"] = to_iso(moment)
    token["last_refresh_error"] = None
    return "refreshed"
