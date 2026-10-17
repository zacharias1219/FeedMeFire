"""Unit tests: validation, sampling, schema, evidence, safety, crypto, cost, jobs (PRD §20)."""
import json

import pytest
from pydantic import ValidationError

from app import crypto
from app.config import settings
from app.schemas import DISCLAIMER, ReportOut, validate_evidence
from app.services import items as it, safety
from app.services.cost import estimate_cost
from app.services.jobs import IllegalTransition, backoff_seconds, check_transition
from tests.conftest import good_report


# ---- input validation / normalisation / dedupe ----
def test_clean_text_strips_control_and_normalises_whitespace():
    assert it.clean_text("  Hello\x00   ​World\n\tagain\x07 ") == "Hello World again"


def test_validate_item_rules():
    assert it.validate_item("video_title", "ab")[1]
    for junk in ("289", "6:30", "383k", "1:02:03", "6.2K views", "40m"):
        assert it.validate_item("video_title", junk)[1], junk   # metadata is never an item
    assert it.validate_item("video_title", "x" * 201)[1]
    assert it.validate_item("bogus", "hello there")[1]
    assert it.validate_item("video_title", "https://youtube.com/watch?v=abc")[1]
    assert it.validate_item("video_title", "www.example.com")[1]
    assert it.validate_item("video_title", "youtube.com/watch?v=abc")[1]
    assert it.validate_item("video_title", "How to cook rice") == ("How to cook rice", None)
    # wording is preserved apart from whitespace
    assert it.validate_item("interest", "  ThE   Best  ")[0] == "ThE Best"


def test_dedupe_key_is_case_and_type_sensitive():
    assert it.dedupe_key("interest", "Cooking") == it.dedupe_key("interest", "cooking")
    assert it.dedupe_key("interest", "Cooking") != it.dedupe_key("video_title", "Cooking")


def test_sensitive_flagging():
    assert it.sensitive_category("my depression journey") == "health"
    assert it.sensitive_category("call me on +91 98765 43210") == "personal_info"
    assert it.sensitive_category("mail me a@b.com") == "personal_info"
    assert it.sensitive_category("Sourdough for beginners") is None


# ---- sampling ----
def _cands(n_each=30):
    out, pos = [], 0
    for t in it.ITEM_TYPES:
        for i in range(n_each):
            out.append(it.Candidate(f"{t}-{i}", t, f"{t} text {i}", False, pos))
            pos += 1
    return out


def test_sampling_is_capped_balanced_and_deterministic():
    c = _cands(30)
    a, b = it.select_for_model(c), it.select_for_model(list(reversed(c)))
    assert [x.id for x in a] == [x.id for x in b]
    assert len(a) == 60
    counts = {t: sum(1 for x in a if x.item_type == t) for t in it.ITEM_TYPES}
    assert set(counts.values()) == {20}


def test_sampling_excludes_flagged_and_respects_char_budget():
    c = _cands(5)
    c[0].flagged = True
    assert "video_title-0" not in {x.id for x in it.select_for_model(c)}
    long = [it.Candidate(f"i{n}", "interest", "x" * 200, False, n) for n in range(20)]
    assert len(it.select_for_model(long, max_chars=1000)) == 5


def test_alias_and_hash_stable():
    assert it.alias_for(0) == "item_01" and it.alias_for(11) == "item_12"
    e = [("item_01", "interest", "a"), ("item_02", "interest", "b")]
    assert it.sample_hash(e) == it.sample_hash(list(e))
    assert it.sample_hash(e) != it.sample_hash(e[::-1])


# ---- output schema ----
USER = json.dumps({"sample_items": [{"id": f"item_{n + 1:02d}", "type": "interest", "text": f"t{n}"} for n in range(12)]})


def test_good_report_validates():
    r = ReportOut.model_validate(good_report(USER))
    assert len(r.evidence_ids()) >= 6 and r.word_count() > 20


@pytest.mark.parametrize("mut", [
    {"observations": []},
    {"contradictions": [{"joke": "x", "evidence_item_ids": ["item_01"]}]},
    {"headline": "x" * 101},
    {"share_quote": "x" * 161},
    {"extra": 1},
    {"schema_version": "2.0"},
])
def test_report_schema_rejects(mut):
    with pytest.raises(ValidationError):
        ReportOut.model_validate(good_report(USER, **mut))


def test_report_needs_six_distinct_evidence_items():
    rep = good_report(USER)
    for o in rep["observations"]:
        o["evidence_item_ids"] = ["item_01", "item_02"]
    for c in rep["contradictions"]:
        c["evidence_item_ids"] = ["item_01", "item_02"]
    with pytest.raises(ValidationError):
        ReportOut.model_validate(rep)


def test_evidence_ids_must_reference_sample():
    r = ReportOut.model_validate(good_report(USER))
    assert validate_evidence(r, {f"item_{n:02d}" for n in range(1, 13)}) == []
    assert validate_evidence(r, {"item_01"}) != []


# ---- safety ----
def _report(**over):
    return ReportOut.model_validate(good_report(USER, **over))


def test_safe_report_passes():
    assert safety.evaluate(_report()) == []


@pytest.mark.parametrize("text,code", [
    ("You probably have ADHD, honestly.", "diagnosis"),
    ("Your addiction to tutorials is clear.", "addiction_or_danger"),
    ("Typical of a Muslim viewer.", "protected_trait"),
    ("You are fat and watch cooking videos.", "body_shaming"),
    ("Go kill yourself, tutorial hoarder.", "threat_or_self_harm"),
    ("What the fuck is this queue", "profanity"),
    ("See https://evil.example for more", "markup_or_link"),
    ("<script>alert(1)</script>", "markup_or_link"),
    ("You are unemployed and it shows.", "biographical_claim"),
    ("Your watch history says it all.", "history_claim"),
])
def test_safety_categories(text, code):
    assert code in safety.evaluate(_report(opening_roast=text))


def test_safety_exempts_the_users_own_wording_but_not_links():
    r = _report(opening_roast="You queued a fat loss meal prep video.")
    assert "body_shaming" in safety.evaluate(r)
    assert "body_shaming" not in safety.evaluate(r, allowed_terms={"fat", "loss", "meal", "prep"})
    r2 = _report(opening_roast="See https://x.example")
    assert "markup_or_link" in safety.evaluate(r2, allowed_terms={"https"})


def test_safety_flags_creator_attacks_only():
    nice = _report(opening_roast="You adore Desk Setup Daily.")
    mean = _report(opening_roast="Desk Setup Daily is trash. You watch it anyway.")
    assert safety.evaluate(nice, channel_names=["Desk Setup Daily"]) == []
    assert "creator_attack" in safety.evaluate(mean, channel_names=["Desk Setup Daily"])


# ---- crypto / cost / jobs ----
def test_encryption_round_trip_and_rotation(monkeypatch):
    from cryptography.fernet import Fernet
    k1, k2 = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "encryption_keys", k1)
    token = crypto.encrypt_text("secret roast")
    assert "secret" not in token and crypto.decrypt_text(token) == "secret roast"
    monkeypatch.setattr(settings, "encryption_keys", f"{k2},{k1}")  # rotate: new key first, old still decrypts
    assert crypto.decrypt_text(token) == "secret roast"
    assert crypto.decrypt_json(crypto.encrypt_json({"a": [1, "é"]})) == {"a": [1, "é"]}


def test_cost_estimate(monkeypatch):
    monkeypatch.setattr(settings, "price_input_per_mtok", 0.30)
    monkeypatch.setattr(settings, "price_output_per_mtok", 2.50)
    assert estimate_cost(1_000_000, 1_000_000) == 2.8
    assert estimate_cost(0, 0) == 0


def test_job_state_transitions():
    check_transition("queued", "validating")
    check_transition("checking", "ready")
    check_transition("checking", "generating")  # repair / safer retry
    for bad in [("ready", "queued"), ("queued", "ready"), ("cancelled", "generating"), ("failed", "queued")]:
        with pytest.raises(IllegalTransition):
            check_transition(*bad)


def test_backoff_grows_with_jitter():
    assert 2 <= backoff_seconds(1) <= 3 and 4 <= backoff_seconds(2) <= 5 and backoff_seconds(10) <= 61


def test_prod_config_rejects_unsafe_defaults(monkeypatch):
    monkeypatch.setattr(settings, "env", "prod")
    with pytest.raises(RuntimeError):
        settings.validate_for_runtime()
    monkeypatch.setattr(settings, "env", "test")
    monkeypatch.setattr(settings, "youtube_connect_enabled", True)
    with pytest.raises(RuntimeError):
        settings.validate_for_runtime()


def test_fake_provider_output_is_valid_and_safe():
    from app.providers.fake import FakeProvider
    from app.services import prompt
    items = [{"id": f"item_{n + 1:02d}", "type": ("video_title", "channel_name", "interest")[n % 3],
              "text": f"Thing {n} about woodworking"} for n in range(10)]
    for level in ("gentle", "spicy", "savage"):
        out = FakeProvider().generate("", prompt.build_user_message(items, level, "en"), timeout=5)
        r = ReportOut.model_validate_json(out.text)
        assert validate_evidence(r, {i["id"] for i in items}) == [] and safety.evaluate(r) == []


def test_demo_report_is_valid_grounded_and_safe():
    import pathlib
    d = json.loads((pathlib.Path(__file__).parent.parent / "app/static/demo.json").read_text())
    r = ReportOut.model_validate(d["report"])
    assert set(d["evidence"]) == r.evidence_ids() and safety.evaluate(r, allowed_terms=set()) == []
    assert r.disclaimer == DISCLAIMER
