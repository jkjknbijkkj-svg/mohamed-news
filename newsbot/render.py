"""Render posts to 1080x1350 JPEG images with HTML templates and Chromium."""

from __future__ import annotations

import base64
import io
import logging
import re
from pathlib import Path
from typing import Any, Callable

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup, escape
from PIL import Image
from playwright.sync_api import sync_playwright

from .config import ACCOUNT_HANDLE, ACCOUNT_NAME_AR, ASSETS_DIR, TEMPLATES_DIR
from .models import CATEGORY_LABELS_AR, RepoInfo

log = logging.getLogger(__name__)
FONTS_DIR = ASSETS_DIR / "fonts"
MAX_JPEG_BYTES = 7_500_000

CATEGORY_STYLE: dict[str, dict[str, str]] = {
    "ai": {"label": CATEGORY_LABELS_AR["ai"], "from_color": "#1e1b4b", "to_color": "#4f46e5", "accent": "#a5b4fc"},
    "ecommerce": {"label": CATEGORY_LABELS_AR["ecommerce"], "from_color": "#052e16", "to_color": "#16a34a", "accent": "#86efac"},
    "companies": {"label": CATEGORY_LABELS_AR["companies"], "from_color": "#0c1b33", "to_color": "#0ea5e9", "accent": "#7dd3fc"},
    "github": {"label": CATEGORY_LABELS_AR["github"], "from_color": "#0b0f14", "to_color": "#374151", "accent": "#fbbf24"},
    "weekly": {"label": CATEGORY_LABELS_AR["weekly"], "from_color": "#3b0764", "to_color": "#be185d", "accent": "#f9a8d4"},
}

CLOSING_SUB = (
    "أخبار الذكاء الاصطناعي والتجارة الإلكترونية والشركات ومستودعات GitHub، يوميًا وبالعربية."
)

LATIN_RUN = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9.\-+_/']*(?: [A-Za-z0-9][A-Za-z0-9.\-+_/']*)*"
)


class TextTooLongError(Exception):
    """The text does not fit its box even at the minimum font size."""


def latin_bdi(value: Any) -> Markup:
    text = str(value)
    parts: list[str] = []
    last = 0
    for match in LATIN_RUN.finditer(text):
        parts.append(str(escape(text[last : match.start()])))
        parts.append('<bdi dir="ltr">' + str(escape(match.group(0))) + "</bdi>")
        last = match.end()
    parts.append(str(escape(text[last:])))
    return Markup("".join(parts))


def _font_face_css() -> str:
    fonts = sorted(FONTS_DIR.glob("Cairo*.ttf"))
    if not fonts:
        raise FileNotFoundError(
            "Cairo font not found in assets/fonts. See SETUP_AR.md (step: download Cairo)."
        )
    data = base64.b64encode(fonts[0].read_bytes()).decode("ascii")
    return (
        "@font-face{font-family:'Cairo';font-weight:200 1000;font-style:normal;"
        "src:url(data:font/ttf;base64,%s) format('truetype');}" % data
    )


def _logo_data_uri() -> str:
    path = ASSETS_DIR / "logo.png"
    if not path.exists():
        return ""
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def _to_jpeg(png_bytes: bytes) -> bytes:
    image = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    for quality in (92, 88, 82, 76):
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=quality, optimize=True)
        if buffer.tell() <= MAX_JPEG_BYTES:
            return buffer.getvalue()
    raise RuntimeError("image is larger than 7.5 MB even at the lowest JPEG quality")


class Renderer:
    def __init__(self) -> None:
        self.env = Environment(
            loader=FileSystemLoader(str(TEMPLATES_DIR)),
            autoescape=select_autoescape(["html"]),
        )
        self.env.filters["bdi"] = latin_bdi
        self._font_css = _font_face_css()
        self._logo = _logo_data_uri()
        self._pw: Any = None
        self._browser: Any = None
        self._page: Any = None

    def __enter__(self) -> "Renderer":
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(args=["--no-sandbox"])
        self._page = self._browser.new_page(
            viewport={"width": 1080, "height": 1350}, device_scale_factor=1
        )
        return self

    def __exit__(self, *exc_info: Any) -> None:
        for closer in (
            getattr(self._page, "close", None),
            getattr(self._browser, "close", None),
            getattr(self._pw, "stop", None),
        ):
            try:
                if closer:
                    closer()
            except Exception:  # noqa: BLE001
                pass

    def render(self, template: str, context: dict[str, Any], out_path: Path) -> Path:
        base = {
            "font_css": self._font_css,
            "logo": self._logo,
            "wordmark": ACCOUNT_NAME_AR,
            "handle": ACCOUNT_HANDLE,
        }
        html = self.env.get_template(template).render(**base, **context)
        page = self._page
        page.set_content(html, wait_until="load")
        page.evaluate("async () => { await document.fonts.ready; return true; }")
        overflow = page.evaluate("window.fitAll()")
        if overflow:
            raise TextTooLongError("text does not fit: " + ", ".join(overflow))
        jpeg = _to_jpeg(page.screenshot(type="png"))
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(jpeg)
        return out_path


def render_news(
    renderer: Renderer,
    category: str,
    headline: str,
    summary: str,
    source_name: str,
    out_path: Path,
) -> Path:
    return renderer.render(
        "news.html",
        {
            "style": CATEGORY_STYLE[category],
            "headline": headline,
            "summary": summary,
            "source_name": source_name,
        },
        out_path,
    )


def render_github(
    renderer: Renderer,
    repo: RepoInfo,
    content: dict[str, Any],
    out_dir: Path,
    stem: str,
) -> list[Path]:
    style = CATEGORY_STYLE["github"]
    name = repo.full_name
    stats = [
        ("النجوم", f"{repo.stars:,}"),
        ("اللغة", repo.language or "—"),
        ("آخر تحديث", repo.pushed_at[:10] or "—"),
        ("الرخصة", repo.license_name or "—"),
    ]
    commands = [c for c in content.get("how_to_start", []) if c]
    slides: list[tuple[str, dict[str, Any]]] = [
        (
            "github_cover.html",
            {
                "repo_name": name,
                "tagline": content["tagline"],
                "stars": f"{repo.stars:,}",
                "language": repo.language or "",
            },
        ),
        ("github_slide.html", {"title": "ماذا يفعل؟", "body": content["what_it_does"], "footer_left": name}),
        ("github_slide.html", {"title": "لمن يصلح؟", "body": content["who_for"], "footer_left": name}),
    ]
    if commands:
        slides.append(("github_slide.html", {"title": "كيف تبدأ؟", "commands": commands, "footer_left": name}))
    else:
        slides.append(("github_slide.html", {"title": "بطاقة المستودع", "stats": stats, "footer_left": name}))
    slides.append(
        (
            "closing.html",
            {"heading": "تابعنا ليصلك كل جديد", "sub": CLOSING_SUB},
        )
    )
    paths: list[Path] = []
    for index, (template, context) in enumerate(slides, start=1):
        paths.append(
            renderer.render(template, {"style": style, **context}, out_dir / f"{stem}-{index}.jpg")
        )
    return paths


def render_weekly(
    renderer: Renderer,
    entries: list[dict[str, Any]],
    date_range: str,
    out_dir: Path,
    stem: str,
    shorten: Callable[[str], str],
) -> list[Path]:
    style = CATEGORY_STYLE["weekly"]
    paths = [
        renderer.render(
            "summary_cover.html",
            {"style": style, "count": len(entries), "date_range": date_range},
            out_dir / f"{stem}-1.jpg",
        )
    ]
    for index, entry in enumerate(entries, start=1):
        paths.append(
            renderer.render(
                "summary_item.html",
                {
                    "style": style,
                    "index": index,
                    "item_label": CATEGORY_LABELS_AR.get(entry["category"], ""),
                    "headline": entry["headline"],
                    "summary": shorten(entry["summary"]),
                },
                out_dir / f"{stem}-{index + 1}.jpg",
            )
        )
    paths.append(
        renderer.render(
            "closing.html",
            {"style": style, "heading": "تابعنا ليصلك كل جديد", "sub": CLOSING_SUB},
            out_dir / f"{stem}-{len(entries) + 2}.jpg",
        )
    )
    return paths
