"""Instagram publishing through the official Platform API."""

from __future__ import annotations

import logging
import time
from typing import Any

import requests

from .config import Settings, redact
from .state import State, now_utc, to_iso

log = logging.getLogger(__name__)


class PublishError(Exception):
    """Publishing or API call failed."""


class QuotaError(PublishError):
    """The daily publishing quota is exhausted."""


class InstagramPublisher:
    def __init__(self, settings: Settings) -> None:
        self.s = settings
        self.session = requests.Session()
        self.ai_flag = True

    def _call(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        retries: int = 3,
    ) -> dict[str, Any]:
        url = self.s.graph_url(path)
        token = self.s.ig_access_token
        for attempt in range(retries):
            try:
                if method == "GET":
                    response = self.session.get(
                        url, params={**(params or {}), "access_token": token}, timeout=60
                    )
                else:
                    response = self.session.post(
                        url, data={**(data or {}), "access_token": token}, timeout=60
                    )
            except requests.RequestException as exc:
                if attempt == retries - 1:
                    raise PublishError(redact(str(exc))[:300]) from exc
                time.sleep(2 ** (attempt + 1))
                continue
            if response.status_code >= 500 and attempt < retries - 1:
                time.sleep(2 ** (attempt + 1))
                continue
            try:
                body = response.json()
            except ValueError:
                body = {}
            if response.status_code >= 400 or "error" in body:
                error = body.get("error", {})
                raise PublishError(
                    redact(
                        f"HTTP {response.status_code} code={error.get('code')} "
                        f"sub={error.get('error_subcode')}: "
                        f"{error.get('message', response.text[:200])}"
                    )[:400]
                )
            return body
        raise PublishError("request failed")

    def check_quota(self) -> None:
        try:
            body = self._call(
                "GET",
                f"{self.s.ig_user_id}/content_publishing_limit",
                params={"fields": "quota_usage,config"},
            )
        except PublishError as exc:
            log.warning("Publishing quota check unavailable: %s", exc)
            return
        for row in body.get("data", []):
            usage = int(row.get("quota_usage", 0))
            total = int((row.get("config") or {}).get("quota_total", 100))
            if usage >= total - 1:
                raise QuotaError(f"publishing quota reached: {usage}/{total}")

    def _create(self, data: dict[str, Any]) -> str:
        payload = dict(data)
        if self.ai_flag:
            payload["is_ai_generated"] = "true"
        try:
            return self._call("POST", f"{self.s.ig_user_id}/media", data=payload)["id"]
        except PublishError as exc:
            if self.ai_flag and "is_ai_generated" in str(exc):
                log.warning("is_ai_generated is not accepted by this API; continuing without it")
                self.ai_flag = False
                return self._call("POST", f"{self.s.ig_user_id}/media", data=dict(data))["id"]
            raise

    def wait_ready(self, container_id: str, timeout: int = 180) -> None:
        deadline = time.time() + timeout
        status = ""
        while time.time() < deadline:
            body = self._call("GET", container_id, params={"fields": "status_code"})
            status = str(body.get("status_code", ""))
            if status == "FINISHED":
                return
            if status in {"ERROR", "EXPIRED"}:
                raise PublishError(f"container {container_id} status {status}")
            time.sleep(5)
        raise PublishError(f"container {container_id} not ready, last status {status}")

    def permalink(self, media_id: str) -> str:
        try:
            return str(self._call("GET", media_id, params={"fields": "permalink"}).get("permalink", ""))
        except PublishError:
            return ""

    def publish_post(
        self,
        image_urls: list[str],
        caption: str,
        alt_texts: list[str],
        state: State,
        date_iso: str,
        slot_id: str,
    ) -> dict[str, str]:
        pending = state.get_pending(date_iso, slot_id) or {}
        creation_id = pending.get("creation_id")
        if not creation_id:
            if len(image_urls) == 1:
                creation_id = self._create(
                    {"image_url": image_urls[0], "caption": caption, "alt_text": alt_texts[0][:400]}
                )
            else:
                children: list[str] = []
                for url, alt in zip(image_urls, alt_texts):
                    children.append(
                        self._create(
                            {"image_url": url, "is_carousel_item": "true", "alt_text": alt[:400]}
                        )
                    )
                for child in children:
                    self.wait_ready(child)
                creation_id = self._create(
                    {"media_type": "CAROUSEL", "children": ",".join(children), "caption": caption}
                )
            state.set_pending(date_iso, slot_id, {"creation_id": creation_id, "created_at": to_iso(now_utc())})
            state.save()
        self.wait_ready(creation_id)
        media_id = self._call(
            "POST", f"{self.s.ig_user_id}/media_publish", data={"creation_id": creation_id}
        )["id"]
        return {"ig_media_id": str(media_id), "permalink": self.permalink(str(media_id))}

    def media_insights(self, media_id: str) -> dict[str, int]:
        last: PublishError | None = None
        body: dict[str, Any] = {}
        for metrics in ("reach,likes,comments,shares,saved", "reach"):
            try:
                body = self._call("GET", f"{media_id}/insights", params={"metric": metrics})
                break
            except PublishError as exc:
                last = exc
        else:
            raise last or PublishError("insights unavailable")
        result: dict[str, int] = {}
        for row in body.get("data", []):
            values = row.get("values") or []
            if values:
                result[row["name"]] = int(values[0].get("value", 0))
            elif "total_value" in row:
                result[row["name"]] = int(row["total_value"].get("value", 0))
        return result

    def account_info(self) -> dict[str, Any]:
        return self._call("GET", self.s.ig_user_id, params={"fields": "username,followers_count"})
