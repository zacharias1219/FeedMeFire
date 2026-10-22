"""The whole stack with a REAL provider path: browser -> app -> worker -> real sarvamai SDK -> HTTP -> (mock) Sarvam.
Only SARVAM_API_KEY is set: PROVIDER stays on its default (auto), which must pick Sarvam, not the canned generator."""
import json
import os
import socket
import subprocess
import sys
import time

import httpx
import pytest
from playwright.sync_api import expect

from tests.e2e.mock_sarvam import Mock

pytestmark = pytest.mark.e2e


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    mock = Mock()
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    env = {k: v for k, v in os.environ.items() if k not in ("PROVIDER", "SARVAM_API_KEY", "GEMINI_API_KEY")}
    env.update(ENV="test", DATABASE_URL=f"sqlite:///{tmp_path_factory.mktemp('stack') / 's.db'}", EMBEDDED_WORKER="true", POLL_INTERVAL_S="0.2",
               SARVAM_API_KEY="test-key-abc", SARVAM_BASE_URL=mock.url, NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost")
    p = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port), "--log-level", "warning"], env=env)
    url = f"http://127.0.0.1:{port}"
    for _ in range(80):
        try:
            if httpx.get(url + "/health/ready", trust_env=False).status_code == 200:
                break
        except Exception:
            time.sleep(0.25)
    yield url, mock
    p.terminate(); p.wait(timeout=10); mock.stop()


def roast(page, url, items, intensity, language):
    page.goto(url + "/start")
    page.check("#chk-age"); page.check("#chk-terms"); page.click("#gate-go")
    page.check("input[name=paste-mode][value=plain]")
    page.select_option("#add-type", "interest")
    page.fill("#bulk-text", "\n".join(items)); page.click("#bulk-add"); page.click("#bulk-confirm")
    expect(page.locator("#input-count")).to_contain_text(f"{len(items)} added")
    page.click("#to-review"); expect(page.locator("#approve")).to_be_enabled(); page.click("#approve")
    page.check(f"input[value={intensity}]"); page.select_option("#lang", language)
    page.check("#c-own"); page.check("#c-provider"); page.check("#c-fun")
    page.click("#start")
    expect(page.locator("#step-results")).to_be_visible(timeout=30000)
    return page.locator("#report-body").inner_text(), page.locator("#res-h").inner_text()


def test_auto_picks_sarvam_and_the_roast_is_really_written_from_the_users_items(stack, browser):
    url, mock = stack
    ready = httpx.get(url + "/health/ready", trust_env=False).json()
    assert ready["provider"] == "sarvam" and ready["demo_generator"] is False

    page = browser.new_page(viewport={"width": 1000, "height": 900})
    page.goto(url + "/start")
    expect(page.locator("#demo-banner")).to_be_hidden()                          # no "demo mode" warning
    a = [f"Zebra crossing study {i}" for i in range(12)]
    body_a, head_a = roast(page, url, a, "savage", "hi")
    assert "MOCKMODEL" in body_a and "नमस्ते" in head_a                            # came from the provider, in the chosen language
    assert any(x in body_a for x in a[:4])                                         # and uses the user's own items
    expect(page.locator("#res-source")).to_have_text("Written by Sarvam AI · sarvam-105b-conversations")

    gen = [r for r in mock.requests if not r["body"]["messages"][0]["content"].startswith("You are a strict")]
    mod = [r for r in mock.requests if r["body"]["messages"][0]["content"].startswith("You are a strict")]
    g = gen[-1]
    assert g["path"] == "/v1/chat/completions" and g["headers"]["api-subscription-key"] == "test-key-abc"
    assert g["body"]["model"] == "sarvam-105b-conversations"
    system, user = g["body"]["messages"][0]["content"], json.loads(g["body"]["messages"][1]["content"])
    assert "MAXIMUM HEAT" not in system and "MAXIMUM HEAT" in user["intensity"]    # the savage guide reaches the model
    assert "untrusted data" in system and user["output_language"].startswith("Hindi")
    assert {i["text"] for i in user["sample_items"]} == set(a) and user["min_distinct_items"] == 10
    assert g["body"]["max_tokens"] == 6000                                         # Indic token budget
    assert len(mod) >= 1                                                           # moderation forced outside English

    # a second, different sample must give a different roast: nothing is canned or cached
    page.click("#delete"); page.click("dialog button[value=confirm]"); expect(page.locator("#step-deleted")).to_be_visible()
    b = [f"Quokka sanctuary vlog {i}" for i in range(12)]
    body_b, head_b = roast(page, url, b, "gentle", "en")
    assert "MOCKMODEL" in body_b and any(x in body_b for x in b[:4])
    assert not any(x in body_b for x in a) and body_a != body_b and "नमस्ते" not in head_b
