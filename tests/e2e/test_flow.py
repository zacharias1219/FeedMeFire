"""Browser end-to-end: landing -> full roast -> export -> regenerate -> feedback -> delete; plus demo, mobile, keyboard."""
import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e

ITEMS = "\n".join(f"Cozy woodworking project {i}" for i in range(12))


def collect_errors(page):
    errs = []
    page.on("pageerror", lambda e: errs.append(str(e)))
    page.on("console", lambda m: errs.append(m.text) if m.type == "error" and "fonts.g" not in m.text
            and "net::ERR" not in m.text and "cloudfront" not in m.text and "Failed to load resource" not in m.text else None)
    return errs


def test_landing_renders_and_links_to_app(server, browser):
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    errs = collect_errors(page)
    page.goto(server + "/")
    page.wait_for_selector(".card")
    assert page.locator(".card").count() == 37
    assert page.locator("text=Never reads your watch history").is_visible()
    assert page.get_by_role("link", name="Start roast").get_attribute("href") == "/start"
    assert page.locator(".legal").inner_text().count("YouTube") == 1
    assert errs == []


def test_full_manual_flow(server, browser, tmp_path):
    page = browser.new_page(viewport={"width": 1100, "height": 900}, accept_downloads=True)
    errs = collect_errors(page)
    page.goto(server + "/start")
    expect(page.locator("#demo-banner")).to_be_visible()                      # PROVIDER=fake here: the canned generator is announced
    # gate: nothing pre-checked, continue disabled
    assert page.locator("#gate-go").is_disabled()
    page.check("#chk-age"); page.check("#chk-terms")
    page.click("#gate-go")
    # input: too few -> cannot review as ready; add 12 via bulk
    page.fill("#add-text", "https://youtube.com/watch?v=abc"); page.click("#add-form button[type=submit]")
    expect(page.locator("#input-err")).to_contain_text("link")
    page.fill("#bulk-text", "\n".join(ITEMS.split("\n")[:9]))
    page.select_option("#add-type", "interest")
    page.check("input[name=paste-mode][value=plain]")
    page.click("#bulk-add")
    page.click("#bulk-confirm")
    expect(page.locator("#input-count")).to_contain_text("Add 1 more")
    for t in ("Quiet carpentry channels", "Hand plane restoration", "Sharpening for beginners"):
        page.fill("#add-text", t); page.click("#add-form button[type=submit]")
        expect(page.locator("#chips")).to_contain_text(t)
    page.click("#to-review")
    page.wait_for_selector("#items li")
    expect(page.locator("#approve")).to_be_enabled()
    page.fill("#items li:first-child input", "How I beat depression")  # sensitive edit gets flagged and excluded
    page.locator("#items li:first-child input").dispatch_event("change")
    page.wait_for_selector(".badge.warn")
    page.click("#approve")
    page.wait_for_selector("#step-prefs:not([hidden])")
    expect(page.locator("#start")).to_be_disabled()
    page.check("#c-own"); page.check("#c-provider"); page.check("#c-fun")
    page.check("input[value=gentle]")
    assert page.locator("#lang option").all_inner_texts() == ["English", "हिन्दी (Hindi)", "தமிழ் (Tamil)", "తెలుగు (Telugu)", "বাংলা (Bengali)", "मराठी (Marathi)"]
    assert page.locator("#lang").input_value() == "en"                       # English by default
    expect(page.locator("#lang-note")).to_contain_text("demo generator always writes English")
    page.select_option("#lang", "hi")
    page.click("#start")
    page.wait_for_selector("#step-results:not([hidden])", timeout=30000)
    assert page.locator(".card-obs").count() >= 4
    expect(page.locator("#res-intensity")).to_contain_text("Gentle roast")
    expect(page.locator("#report-body")).to_have_attribute("lang", "hi")
    expect(page.locator("#res-source")).to_contain_text("built-in demo generator")
    page.locator(".card-obs details summary").first.click()
    assert page.locator(".card-obs .evidence li").first.is_visible()
    assert "depression" not in page.locator("#report-body").inner_text().lower()
    # export: only persona + quote on the card; download works
    with page.expect_download() as dl:
        page.click("#download")
    path = tmp_path / "card.png"; dl.value.save_as(str(path))
    assert path.stat().st_size > 5000
    # regenerate with a different intensity; previous report remains until replaced
    page.select_option("#regen-int", "savage")
    page.click("#regen")
    expect(page.locator("#res-intensity")).to_contain_text("Savage", timeout=30000)
    expect(page.locator("#regen-panel")).to_be_hidden()  # one regeneration per report
    # feedback
    page.check("input[name=funny][value='1']"); page.fill("#fb-comment", "ha")
    page.click("#fb-form button[type=submit]")
    expect(page.locator("#fb-ok")).to_contain_text("separately")
    # reload resumes on the result
    page.reload(); page.wait_for_selector("#step-results:not([hidden])")
    # delete
    page.click("#delete"); page.click("dialog button[value=confirm]")
    page.wait_for_selector("#step-deleted:not([hidden])")
    page.goto(server + "/start")
    expect(page.locator("#step-gate")).to_be_visible()
    assert errs == [], errs


def test_demo_page_needs_no_ai_and_exports(server, browser):
    page = browser.new_page(viewport={"width": 390, "height": 800}, accept_downloads=True)
    errs = collect_errors(page)
    page.goto(server + "/demo")
    page.wait_for_selector(".card-obs")
    assert "fictional" in page.content().lower()
    with page.expect_download():
        page.click("#download")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert errs == []


def test_mobile_360_no_horizontal_scroll_and_keyboard_gate(server, browser):
    page = browser.new_page(viewport={"width": 360, "height": 740})
    page.goto(server + "/start")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.keyboard.press("Tab"); page.keyboard.press("Tab")  # skip link, then brand...
    page.focus("#chk-age"); page.keyboard.press("Space")
    page.focus("#chk-terms"); page.keyboard.press("Space")
    expect(page.locator("#gate-go")).to_be_enabled()
    page.focus("#gate-go"); page.keyboard.press("Enter")
    page.wait_for_selector("#step-input:not([hidden])")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert page.evaluate("document.activeElement.id") == "input-h"  # focus moves to the new step heading


def test_landing_phone_layout(server, browser):
    page = browser.new_page(viewport={"width": 375, "height": 780})
    page.goto(server + "/")
    page.wait_for_selector(".cta2")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def test_paste_whole_youtube_page_and_multiline_paste_into_single_box(server, browser):
    import pathlib
    text = (pathlib.Path(__file__).parent.parent / "fixtures/yt_history_paste.txt").read_text()
    page = browser.new_page(viewport={"width": 1000, "height": 900})
    page.goto(server + "/start")
    page.check("#chk-age"); page.check("#chk-terms"); page.click("#gate-go")
    page.fill("#bulk-text", text)
    page.click("#bulk-add")
    expect(page.locator("#bulk-summary")).to_contain_text("8 videos, 7 channels")
    assert page.locator("#preview-chips li").count() == 15
    page.locator("#preview-chips li .x").first.click()            # drop one before adding
    page.click("#bulk-confirm")
    expect(page.locator("#bulk-result")).to_contain_text("Added 14 items")
    expect(page.locator("#input-count")).to_contain_text("14 added")
    expect(page.locator("#chips")).not_to_contain_text("383k")
    # a multi-line paste straight into the single-item box is parsed too, never collapsed into one item
    page.click("#use-demo")
    page.evaluate("""() => { const dt = new DataTransfer(); dt.setData('text', 'Alpha Video One\\nBeta Channel\\n10k\\n\\n1:00\\nGamma Video Two\\nDelta Channel\\n5k\\n\\n2:00');
      document.querySelector('#add-text').dispatchEvent(new ClipboardEvent('paste', {clipboardData: dt, bubbles: true, cancelable: true})); }""")
    expect(page.locator("#bulk-summary")).to_contain_text("2 videos, 2 channels")
    page.click("#bulk-confirm")
    expect(page.locator("#chips")).to_contain_text("Gamma Video Two")
