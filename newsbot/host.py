"""Image hosting through the repository itself, and git helpers."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from datetime import date, datetime
from pathlib import Path

import requests

from .config import MEDIA_DIR, ROOT, redact

log = logging.getLogger(__name__)


class HostError(Exception):
    """Raised when staging, pushing or checking hosted images fails."""


def in_ci() -> bool:
    return os.environ.get("GITHUB_ACTIONS") == "true"


def media_relpath(date_iso: str, slot_id: str, index: int) -> str:
    return f"media/{date_iso[:4]}/{date_iso[5:7]}/{date_iso[8:10]}/{slot_id}-{index}.jpg"


def stage_images(paths: list[Path], date_iso: str, slot_id: str) -> list[str]:
    relative: list[str] = []
    for index, path in enumerate(paths, start=1):
        rel = media_relpath(date_iso, slot_id, index)
        destination = ROOT / rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, destination)
        relative.append(rel)
    return relative


def run_git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    process = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
    if check and process.returncode != 0:
        raise HostError(redact(f"git {' '.join(args)} failed: {process.stderr.strip()[:300]}"))
    return process


def git_commit_push(paths: list[str], message: str) -> bool:
    if not in_ci() and os.environ.get("ALLOW_LOCAL_PUBLISH") != "1":
        raise HostError("Refusing to commit and push outside GitHub Actions.")
    run_git("add", "-A", "--", *paths)
    status = run_git("status", "--porcelain", "--", *paths)
    if not status.stdout.strip():
        return False
    run_git("commit", "-m", message)
    for attempt in range(3):
        run_git("pull", "--rebase", "--autostash", check=False)
        push = run_git("push", check=False)
        if push.returncode == 0:
            return True
        time.sleep(3 * (attempt + 1))
    raise HostError("git push failed after 3 attempts")


def wait_for_urls(urls: list[str], timeout: int = 180, interval: int = 6) -> None:
    deadline = time.time() + timeout
    pending = list(urls)
    while pending and time.time() < deadline:
        still: list[str] = []
        for url in pending:
            try:
                response = requests.get(url, timeout=20, stream=True)
                ok = response.status_code == 200 and response.headers.get(
                    "Content-Type", ""
                ).lower().startswith("image/")
                response.close()
            except requests.RequestException:
                ok = False
            if not ok:
                still.append(url)
        pending = still
        if pending:
            time.sleep(interval)
    if pending:
        raise HostError(f"{len(pending)} image URL(s) are not reachable yet")


def cleanup_old_media(today: date, days: int = 14) -> int:
    removed = 0
    if not MEDIA_DIR.exists():
        return removed
    for year_dir in sorted(MEDIA_DIR.iterdir()):
        if not year_dir.is_dir():
            continue
        for month_dir in sorted(year_dir.iterdir()):
            if not month_dir.is_dir():
                continue
            for day_dir in sorted(month_dir.iterdir()):
                try:
                    day = datetime.strptime(
                        f"{year_dir.name}-{month_dir.name}-{day_dir.name}", "%Y-%m-%d"
                    ).date()
                except ValueError:
                    continue
                if (today - day).days > days:
                    shutil.rmtree(day_dir, ignore_errors=True)
                    removed += 1
            if month_dir.exists() and not any(month_dir.iterdir()):
                month_dir.rmdir()
        if year_dir.exists() and not any(year_dir.iterdir()):
            year_dir.rmdir()
    return removed
