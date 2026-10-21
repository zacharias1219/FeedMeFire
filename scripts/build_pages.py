"""Render the static, server-free part of RoastMyFeed (landing, demo, info pages) for GitHub Pages.

    python scripts/build_pages.py [outdir]      env: PAGES_BASE=/repo-name  APP_URL=https://your-live-app

GitHub Pages cannot run the FastAPI app, sessions, queue or AI provider, so /start becomes a short notice (or a
redirect to APP_URL when a hosted backend exists). Everything else is the same templates the server uses.
"""
from __future__ import annotations

import os
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jinja2 import Environment, FileSystemLoader, select_autoescape  # noqa: E402

from app.config import settings  # noqa: E402

PAGES = {"demo.html": "demo", "how.html": "how-it-works", "safety.html": "safety", "privacy.html": "privacy",
         "terms.html": "terms", "support.html": "support"}
LINK = re.compile(r'(?P<attr>\b(?:href|src))="/(?!/)')


def _prefix(html: str, base: str) -> str:
    """Root-relative links only work at a domain root; Pages serves project sites under /repo-name."""
    return LINK.sub(lambda m: f'{m.group("attr")}="{base}/', html)


def _asset_version(static: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    for p in sorted(static.rglob("*")):
        if p.is_file() and p.suffix in (".js", ".css", ".json"):
            h.update(p.relative_to(static).as_posix().encode()); h.update(p.read_bytes())
    return h.hexdigest()[:10]


def build(out: Path, base: str = "", app_url: str = "") -> list[str]:
    base = base.rstrip("/")
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    static_src = ROOT / "app" / "static"
    shutil.copytree(static_src, out / "static", ignore=shutil.ignore_patterns("landing.html"))
    (out / ".nojekyll").write_text("")

    env = Environment(loader=FileSystemLoader(ROOT / "app" / "templates"), autoescape=select_autoescape(["html"]))
    env.globals["asset_v"] = _asset_version(static_src)
    ctx = {"s": settings, "provider": settings.provider_display_name}
    written: list[str] = []

    def write(rel: str, html: str) -> None:
        dest = out / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(_prefix(html, base), encoding="utf-8")
        written.append(rel)

    for tpl, slug in PAGES.items():
        write(f"{slug}/index.html", env.get_template(tpl).render(**ctx))
    write("404.html", env.get_template("404.html").render(**ctx))

    if app_url:  # a hosted backend exists: send "Start" visitors to it
        write("start/index.html", f'<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="0; url={app_url}">'
                                  f'<title>Redirecting</title><a href="{app_url}">Continue to RoastMyFeed</a>')
    else:
        write("start/index.html", env.get_template("static_start.html").render(**ctx))

    landing = (static_src / "landing.html").read_text(encoding="utf-8")
    if app_url:
        landing = landing.replace('href="/start"', f'href="{app_url}"')
    write("index.html", landing)
    return written


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "dist"
    files = build(target, os.environ.get("PAGES_BASE", ""), os.environ.get("APP_URL", ""))
    print(f"built {len(files)} pages into {target} (base='{os.environ.get('PAGES_BASE', '')}')")
