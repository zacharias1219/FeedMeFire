"""The Pages build must be self-contained and work under a /repo-name sub-path."""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import build_pages  # noqa: E402


def test_static_site_builds_with_prefixed_links(tmp_path):
    out = tmp_path / "dist"
    files = build_pages.build(out, "/FeedMeFire")
    assert {"index.html", "demo/index.html", "how-it-works/index.html", "start/index.html", "404.html"} <= set(files)
    assert (out / ".nojekyll").exists() and (out / "static/demo.json").exists() and not (out / "static/landing.html").exists()
    for f in out.rglob("*.html"):
        html = f.read_text()
        assert not re.search(r'(?:href|src)="/(?!FeedMeFire/|/)', html), f   # nothing root-relative left
    assert 'href="/FeedMeFire/demo"' in (out / "index.html").read_text()
    assert "static preview" in (out / "start/index.html").read_text().lower()
    assert "/static/js/demo.js?v=" in (out / "demo/index.html").read_text()


def test_app_url_redirects_start_and_landing_cta(tmp_path):
    out = tmp_path / "dist"
    build_pages.build(out, "/FeedMeFire", "https://roast.example.com/start")
    assert "https://roast.example.com/start" in (out / "start/index.html").read_text()
    assert 'href="https://roast.example.com/start"' in (out / "index.html").read_text()


def test_demo_fetch_is_relative_to_the_module():
    js = (ROOT / "app/static/js/demo.js").read_text()
    assert "import.meta.url" in js and "'/static/demo.json'" not in js
