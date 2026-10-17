"""The paste parser is client-side JS; run it under Node against fixtures (skipped if Node is missing)."""
import json
import pathlib
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).parent
PARSER = ROOT.parent / "app/static/js/parse.js"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def parse(text, mode="videos", plain="interest", tmp=None):
    mjs = tmp / "parse.mjs"
    mjs.write_text(PARSER.read_text())
    runner = tmp / "run.mjs"
    runner.write_text("import {parseYouTubePaste as p} from './parse.mjs';\n"
                      "let s='';process.stdin.on('data',d=>s+=d).on('end',()=>{const a=JSON.parse(s);"
                      "console.log(JSON.stringify(p(a.text,a.mode,a.plain)))});")
    r = subprocess.run(["node", str(runner)], input=json.dumps({"text": text, "mode": mode, "plain": plain}),
                       capture_output=True, text=True, check=True)
    return json.loads(r.stdout)


def test_history_page_split_into_titles_and_channels(tmp_path):
    out = parse((ROOT / "fixtures/yt_history_paste.txt").read_text(), tmp=tmp_path)
    titles = [i["text"] for i in out["items"] if i["item_type"] == "video_title"]
    chans = [i["text"] for i in out["items"] if i["item_type"] == "channel_name"]
    assert len(titles) == 8 and len(chans) == 7           # trailing cut-off entry is kept as a title only
    assert "Last Video With No Metadata Yet" in titles
    assert {"Loaf Lab", "Tamil Kitchen", "Desi Beats"} <= set(chans)
    assert "இன்று சமையல் செய்வோம்" in titles and "Hindi Rap Reaction | हिंदी" in titles   # Indian scripts survive
    blob = "\n".join(i["text"] for i in out["items"])
    for junk in ("6:30", "383k", "40m", "8.8k"):
        assert junk not in blob.split("\n")


def test_noise_dates_and_inline_views_are_dropped(tmp_path):
    out = parse((ROOT / "fixtures/yt_history_paste_noise.txt").read_text(), tmp=tmp_path)
    assert [i["text"] for i in out["items"] if i["item_type"] == "video_title"] == ["Lo-fi Beats to Study To", "Mastering Excel in 20 Minutes"]
    assert [i["text"] for i in out["items"] if i["item_type"] == "channel_name"] == ["Chill Channel", "Office Hours"]


def test_unstructured_paste_is_a_title_list_and_plain_mode_uses_type(tmp_path):
    text = "Cooking at 2AM\nLo-fi for focus\nCooking at 2AM\n"
    assert [i["text"] for i in parse(text, tmp=tmp_path)["items"]] == ["Cooking at 2AM", "Lo-fi for focus"]  # deduped
    out = parse(text, "plain", "interest", tmp=tmp_path)
    assert {i["item_type"] for i in out["items"]} == {"interest"}


def test_subscriptions_mode_keeps_channel_names_only(tmp_path):
    text = "Loaf Lab\n@loaflab\n1.2M subscribers\nQuiet Desk\n@quietdesk\n"
    out = parse(text, "channels", tmp=tmp_path)
    assert [i["text"] for i in out["items"]] == ["Loaf Lab", "Quiet Desk"]
    assert {i["item_type"] for i in out["items"]} == {"channel_name"}


def test_overlong_lines_are_clipped_to_200(tmp_path):
    out = parse("x" * 500 + "\nChannel\n10k\n\n1:00\n", tmp=tmp_path)
    assert len(out["items"][0]["text"]) == 200
