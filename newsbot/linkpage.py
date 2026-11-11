"""Generate the link-in-bio page (docs/index.html) for GitHub Pages."""

from __future__ import annotations

import html

from .config import DOCS_DIR, load_links

TEMPLATE = """<!doctype html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>محمد للأخبار</title>
<style>
body { margin:0; min-height:100vh; display:flex; align-items:center; justify-content:center;
  background:linear-gradient(160deg,#1e1b4b,#4f46e5); font-family:system-ui,-apple-system,"Segoe UI",Tahoma,sans-serif; color:#fff; }
main { width:min(92vw,420px); text-align:center; padding:32px 0; }
h1 { font-size:28px; margin:0 0 8px; }
p { opacity:.9; margin:0 0 28px; line-height:1.7; }
.btn { display:block; padding:18px 20px; margin:14px 0; border-radius:16px; background:#fff; color:#1e1b4b;
  font-size:20px; font-weight:700; text-decoration:none; }
.soon { font-size:20px; }
</style>
</head>
<body>
<main>
<h1>محمد للأخبار</h1>
<p>أخبار الذكاء الاصطناعي والتجارة الإلكترونية والشركات ومستودعات GitHub</p>
{{BODY}}
</main>
</body>
</html>
"""


def generate() -> bool:
    """Write docs/index.html. Returns True when at least one project link is set."""
    links = load_links()
    buttons: list[tuple[str, str]] = []
    if links["dz_url"]:
        buttons.append(("للمتابعين من الجزائر", links["dz_url"]))
    if links["world_url"]:
        buttons.append(("لبقية العالم", links["world_url"]))
    if buttons:
        body = "".join(
            f'<a class="btn" href="{html.escape(url, quote=True)}">{html.escape(label)}</a>'
            for label, url in buttons
        )
    else:
        body = '<p class="soon">مشاريعنا قيد الإطلاق، تابعونا قريبًا.</p>'
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    (DOCS_DIR / "index.html").write_text(TEMPLATE.replace("{{BODY}}", body), encoding="utf-8")
    (DOCS_DIR / ".nojekyll").write_text("", encoding="utf-8")
    return bool(buttons)
