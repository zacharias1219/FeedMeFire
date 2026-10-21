"""Sarvam adapter (mocked SDK) and the Indian-language flow (PRD: language list on the preferences screen)."""
import json
import types
import uuid

import httpx
import pytest

from app import providers
from app.config import settings
from app.languages import LANGUAGES, disclaimer_for
from app.providers.base import ProviderTimeout, ProviderUnavailable, output_budget
from app.providers.sarvam import SarvamProvider, extract_json
from app.schemas import ReportOut
from app.services import prompt
from tests.conftest import Scripted, good_report, make_client
from tests.test_api import run_all


def reply(content, prompt_tokens=11, completion_tokens=22, finish="stop"):
    return types.SimpleNamespace(
        choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=content, reasoning_content="private thoughts"), finish_reason=finish)],
        usage=types.SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens))


class FakeSarvam:
    calls: list = []
    outcome = None  # reply or exception to raise

    def __init__(self, api_subscription_key=None, **_):
        self.key = api_subscription_key
        self.chat = types.SimpleNamespace(completions=self._completions)

    def _completions(self, **kw):
        FakeSarvam.calls.append({"key": self.key, **kw})
        if isinstance(FakeSarvam.outcome, Exception):
            raise FakeSarvam.outcome
        return FakeSarvam.outcome


@pytest.fixture
def sarvam(monkeypatch):
    FakeSarvam.calls, FakeSarvam.outcome = [], reply('{"ok": true}')
    monkeypatch.setattr("sarvamai.SarvamAI", FakeSarvam)
    monkeypatch.setattr(settings, "sarvam_api_key", "test-key-123")
    return FakeSarvam


# ---------- adapter ----------
def test_generate_sends_system_and_user_messages_and_returns_usage(sarvam):
    p = SarvamProvider()
    out = p.generate("SYS", json.dumps({"output_language": "English"}), timeout=42)
    c = sarvam.calls[0]
    assert c["key"] == "test-key-123" and c["model"] == "sarvam-105b-conversations"
    assert [m["role"] for m in c["messages"]] == ["system", "user"] and c["messages"][0]["content"] == "SYS"
    assert c["request_options"] == {"timeout_in_seconds": 42, "max_retries": 0}
    assert "response_format" not in c and c["max_tokens"] == settings.max_output_tokens
    assert (out.text, out.input_tokens, out.output_tokens) == ('{"ok": true}', 11, 22)


def test_json_mode_is_opt_in(sarvam, monkeypatch):
    monkeypatch.setattr(settings, "sarvam_json_mode", True)
    SarvamProvider().generate("s", "{}", timeout=5)
    assert sarvam.calls[0]["response_format"] == {"type": "json_object"}


@pytest.mark.parametrize("raw,expected", [
    ('<think>hmm {"x":0}</think>{"a": 1}', '{"a": 1}'),
    ('```json\n{"a": 1}\n```', '{"a": 1}'),
    ('reasoning that never opened</think>\n{"a": 1}', '{"a": 1}'),
    ('Here you go: {"a": {"b": 2}} thanks', '{"a": {"b": 2}}'),
    ('no json here', 'no json here'),
])
def test_extract_json(raw, expected):
    assert extract_json(raw) == expected


def test_reasoning_content_is_never_used(sarvam):
    sarvam.outcome = reply('{"a": 1}')
    assert "private thoughts" not in SarvamProvider().generate("s", "{}", timeout=5).text


def test_error_mapping_and_missing_key(sarvam, monkeypatch):
    from sarvamai.core.api_error import ApiError
    sarvam.outcome = ApiError(status_code=429, body={"echo": "SECRET PROMPT TEXT"})
    with pytest.raises(ProviderUnavailable) as e:
        SarvamProvider().generate("s", "{}", timeout=5)
    assert "429" in str(e.value) and "SECRET" not in str(e.value)
    sarvam.outcome = httpx.ReadTimeout("slow")
    with pytest.raises(ProviderTimeout):
        SarvamProvider().generate("s", "{}", timeout=5)
    sarvam.outcome = httpx.ConnectError("down")
    with pytest.raises(ProviderUnavailable):
        SarvamProvider().generate("s", "{}", timeout=5)
    monkeypatch.setattr(settings, "sarvam_api_key", "")
    with pytest.raises(ProviderUnavailable):
        SarvamProvider().generate("s", "{}", timeout=5)
    assert len(sarvam.calls) == 3  # the keyless call never reached the SDK


def test_moderation_verdicts(sarvam):
    sarvam.outcome = reply("<think>checking</think>SAFE")
    assert SarvamProvider().moderate("text", timeout=5) is True
    sarvam.outcome = reply("UNSAFE")
    assert SarvamProvider().moderate("text", timeout=5) is False
    sarvam.outcome = reply("")
    assert SarvamProvider().moderate("text", timeout=5) is False      # no verdict = not safe


def test_factory_config_and_budgets(monkeypatch):
    monkeypatch.setattr(settings, "provider", "sarvam")
    assert isinstance(providers.get_provider(), SarvamProvider) and settings.provider_display_name == "Sarvam AI"
    monkeypatch.setattr(settings, "env", "prod")
    with pytest.raises(RuntimeError, match="SARVAM_API_KEY"):
        settings.validate_for_runtime()
    assert output_budget(json.dumps({"output_language": "English"})) == settings.max_output_tokens
    assert output_budget(json.dumps({"output_language": "Hindi (हिन्दी), written in its native script"})) == settings.max_output_tokens_indic


# ---------- languages ----------
def test_language_list_and_prompt():
    assert list(LANGUAGES) == ["en", "hi", "ta", "te", "bn", "mr"]
    assert all(disclaimer_for(c) for c in LANGUAGES) and disclaimer_for("hi") != disclaimer_for("en")
    doc = json.loads(prompt.build_user_message([{"id": "item_01", "type": "interest", "text": "x"}], "spicy", "ta"))
    assert doc["output_language"].startswith("Tamil (தமிழ்)") and "native script" in doc["output_language"]
    assert "own script" in prompt.SYSTEM_PROMPT and json.loads(prompt.build_user_message([], "spicy", "en"))["output_language"] == "English"


def test_config_endpoint_lists_languages(api):
    cfg = api.get("/api/v1/config").json()
    assert [l["code"] for l in cfg["languages"]] == ["en", "hi", "ta", "te", "bn", "mr"] and cfg["provider"] == "fake"


def test_unsupported_language_rejected(ready_api):
    s = ready_api.make_sample(15)
    r = ready_api.post("/api/v1/reports", {"sample_id": s["id"], "approved_sample_hash": s["approved_sample_hash"], "language": "fr"},
                       headers={"Idempotency-Key": str(uuid.uuid4())})
    assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_ERROR"


class Judged(Scripted):
    def __init__(self, *a, verdict=True, **k):
        super().__init__(*a, **k)
        self.verdict, self.judged = verdict, []

    def moderate(self, text, *, timeout):
        self.judged.append(text)
        return self.verdict


def _hindi(user):
    return good_report(user, headline="आपकी क्यू अब एक जीवनशैली है", opening_roast="आपने बहुत सारे ट्यूटोरियल चुने और सबको कल पर टाल दिया।")


def test_hindi_flow_localised_disclaimer_language_stored_and_judge_forced(ready_api, use_provider):
    p = use_provider(Judged(lambda u: _hindi(u)))
    s = ready_api.make_sample(15)
    r = ready_api.post("/api/v1/reports", {"sample_id": s["id"], "approved_sample_hash": s["approved_sample_hash"],
                                           "intensity": "spicy", "language": "hi"}, headers={"Idempotency-Key": str(uuid.uuid4())})
    assert r.status_code == 202
    run_all()
    assert "Hindi" in json.loads(p.calls[0][1])["output_language"]
    assert len(p.judged) == 1                                    # moderation is mandatory outside English
    rid = ready_api.get("/api/v1/session").json()["latest_report_id"]
    rep = ready_api.get(f"/api/v1/reports/{rid}").json()
    assert rep["language"] == "hi" and rep["report"]["disclaimer"] == disclaimer_for("hi")
    assert rep["report"]["headline"] == "आपकी क्यू अब एक जीवनशैली है"


def test_english_does_not_force_the_judge(ready_api, use_provider):
    p = use_provider(Judged(lambda u: good_report(u)))
    s = ready_api.make_sample(15)
    ready_api.queue(s)
    run_all()
    assert p.judged == []


def test_non_english_report_rejected_by_judge_is_never_shown(ready_api, use_provider):
    p = use_provider(Judged(lambda u: _hindi(u), verdict=False))
    s = ready_api.make_sample(15)
    jid = ready_api.post("/api/v1/reports", {"sample_id": s["id"], "approved_sample_hash": s["approved_sample_hash"], "language": "ta"},
                         headers={"Idempotency-Key": str(uuid.uuid4())}).json()["job_id"]
    run_all()
    job = ready_api.get(f"/api/v1/jobs/{jid}").json()
    assert job["status"] == "failed" and job["error"]["code"] == "UNSAFE_OUTPUT" and len(p.calls) == 2
    assert ready_api.get("/api/v1/session").json()["latest_report_id"] is None


def test_regeneration_keeps_the_original_language(ready_api, use_provider):
    p = use_provider(Judged(lambda u: _hindi(u)))
    s = ready_api.make_sample(15)
    ready_api.post("/api/v1/reports", {"sample_id": s["id"], "approved_sample_hash": s["approved_sample_hash"], "intensity": "spicy", "language": "bn"},
                   headers={"Idempotency-Key": str(uuid.uuid4())})
    run_all()
    rid = ready_api.get("/api/v1/session").json()["latest_report_id"]
    ready_api.post(f"/api/v1/reports/{rid}/regenerate", {"intensity": "savage"}, headers={"Idempotency-Key": str(uuid.uuid4())})
    run_all()
    assert "Bengali" in json.loads(p.calls[-1][1])["output_language"]
    new = ready_api.get("/api/v1/session").json()["latest_report_id"]
    assert ready_api.get(f"/api/v1/reports/{new}").json()["language"] == "bn"


def test_real_sdk_request_and_response_shapes_against_a_fake_http_server(monkeypatch):
    """Runs the genuine sarvamai client (no SDK mock) against an in-process HTTP transport."""
    import sarvamai
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"], seen["headers"], seen["body"] = str(request.url), dict(request.headers), json.loads(request.content)
        return httpx.Response(200, json={
            "id": "c1", "object": "chat.completion", "created": 1, "model": "sarvam-105b-conversations",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {
                "role": "assistant", "content": '<think>x</think>{"hello": "नमस्ते"}', "reasoning_content": "thinking"}}],
            "usage": {"prompt_tokens": 7, "completion_tokens": 9, "total_tokens": 16}})

    real = sarvamai.SarvamAI
    monkeypatch.setattr("sarvamai.SarvamAI", lambda api_subscription_key=None, **k: real(
        api_subscription_key=api_subscription_key, httpx_client=httpx.Client(transport=httpx.MockTransport(handler))))
    monkeypatch.setattr(settings, "sarvam_api_key", "k-123")
    out = SarvamProvider().generate("SYSTEM", json.dumps({"output_language": "Hindi (हिन्दी)"}), timeout=30)
    assert out.text == '{"hello": "नमस्ते"}' and (out.input_tokens, out.output_tokens) == (7, 9)
    assert seen["url"].endswith("/v1/chat/completions") and "k-123" in " ".join(seen["headers"].values())
    b = seen["body"]
    assert b["model"] == "sarvam-105b-conversations" and b["max_tokens"] == settings.max_output_tokens_indic
    assert [m["role"] for m in b["messages"]] == ["system", "user"] and "response_format" not in b


# ---------- provider selection: a key alone must be enough; the canned generator must never be a silent surprise ----------
@pytest.mark.parametrize("provider,sarvam_key,gemini_key,expected", [
    ("auto", "", "", "fake"), ("auto", "k", "", "sarvam"), ("auto", "", "g", "gemini"), ("auto", "k", "g", "sarvam"),
    ("fake", "k", "", "fake"), ("sarvam", "k", "", "sarvam"), ("gemini", "", "g", "gemini"),
])
def test_provider_resolution_matrix(monkeypatch, provider, sarvam_key, gemini_key, expected):
    monkeypatch.setattr(settings, "provider", provider)
    monkeypatch.setattr(settings, "sarvam_api_key", sarvam_key)
    monkeypatch.setattr(settings, "gemini_api_key", gemini_key)
    assert settings.resolved_provider == expected and settings.is_demo_generator == (expected == "fake")
    assert providers.get_provider().name == expected


def test_default_is_auto_and_explicit_provider_without_key_fails_at_startup(monkeypatch):
    from app.config import Settings
    assert Settings.model_fields["provider"].default == "auto"            # (the test env itself pins PROVIDER=fake)
    monkeypatch.setattr(settings, "provider", "sarvam"); monkeypatch.setattr(settings, "sarvam_api_key", "")
    with pytest.raises(RuntimeError, match="SARVAM_API_KEY is empty"):
        settings.validate_for_runtime()
    monkeypatch.setattr(settings, "provider", "auto"); monkeypatch.setattr(settings, "env", "prod")
    with pytest.raises(RuntimeError, match="demo generator is not allowed in production"):
        settings.validate_for_runtime()


def test_demo_generator_is_announced_everywhere(monkeypatch):
    from fastapi.testclient import TestClient
    from app import main
    from app.main import app
    monkeypatch.setattr(settings, "provider", "auto"); monkeypatch.setattr(settings, "sarvam_api_key", "")
    c = make_client().tc
    assert c.get("/api/v1/config").json()["is_demo_generator"] is True
    ready = c.get("/health/ready").json()
    assert ready["provider"] == "fake" and ready["demo_generator"] is True
    warned = []
    monkeypatch.setattr(main.log, "warning", lambda msg, *a, **k: warned.append(msg % a if a else msg))
    with TestClient(app):                                                    # runs the startup (lifespan) code
        pass
    assert any("BUILT-IN DEMO GENERATOR" in w and "SARVAM_API_KEY" in w for w in warned)
    monkeypatch.setattr(settings, "sarvam_api_key", "k")
    assert c.get("/api/v1/config").json()["provider"] == "sarvam" and c.get("/api/v1/config").json()["is_demo_generator"] is False


def test_report_response_names_the_provider_and_model(ready_api):
    s = ready_api.make_sample(15)
    ready_api.queue(s)
    run_all()
    rep = ready_api.get(f"/api/v1/reports/{ready_api.get('/api/v1/session').json()['latest_report_id']}").json()
    assert rep["provider"] == "fake" and rep["model"] == "fake-roaster-1"


def test_base_url_and_reasoning_effort_reach_the_sdk_and_truncation_is_logged(monkeypatch, caplog):
    import logging
    seen = {}

    class Capture(FakeSarvam):
        def __init__(self, **kw):
            seen.update(kw)
            super().__init__(api_subscription_key=kw.get("api_subscription_key"))

    monkeypatch.setattr("sarvamai.SarvamAI", Capture)
    FakeSarvam.calls, FakeSarvam.outcome = [], reply("", finish="length")
    monkeypatch.setattr(settings, "sarvam_api_key", "k")
    monkeypatch.setattr(settings, "sarvam_base_url", "http://proxy.local:9/")
    monkeypatch.setattr(settings, "sarvam_reasoning_effort", "low")
    caplog.set_level(logging.INFO)
    out = SarvamProvider().generate("s", "{}", timeout=5)
    assert seen["environment"].base == "http://proxy.local:9" and FakeSarvam.calls[0]["reasoning_effort"] == "low"
    assert out.text == "" and "finish_reason=length" in caplog.text and "MAX_OUTPUT_TOKENS" in caplog.text


# ---------- misconfiguration must be loud and immediate (a missing SDK used to surface only as a generic crash) ----------
@pytest.fixture
def no_sdk(monkeypatch):
    """Pretend `sarvamai` is not installed for this Python."""
    monkeypatch.setitem(__import__("sys").modules, "sarvamai", None)
    monkeypatch.setattr(settings, "provider", "sarvam")
    monkeypatch.setattr(settings, "sarvam_api_key", "k")


def test_startup_check_names_the_python_and_the_fix(no_sdk):
    with pytest.raises(RuntimeError) as e:
        providers.check_ready()
    msg = str(e.value)
    assert "sarvamai" in msg and "pip install -r requirements.txt" in msg and __import__("sys").executable in msg


def test_readiness_probe_fails_when_the_sdk_is_missing(no_sdk):
    r = make_client().tc.get("/health/ready")
    assert r.status_code == 503 and r.json()["provider_ok"] is False and "sys" not in json.dumps(r.json())


def test_missing_sdk_fails_the_job_immediately_and_clearly(ready_api, no_sdk, caplog):
    import logging
    caplog.set_level(logging.ERROR)
    s = ready_api.make_sample(15)
    jid = ready_api.queue(s).json()["job_id"]
    run_all()
    job = ready_api.get(f"/api/v1/jobs/{jid}").json()
    assert job["status"] == "failed" and job["error"]["code"] == "PROVIDER_MISCONFIGURED" and job["error"]["retryable"] is False
    assert job["attempt_count"] == 1                                         # no pointless retries
    assert "provider misconfigured" in caplog.text and "sarvamai" in caplog.text and "pip install -r requirements.txt" in caplog.text


def test_unexpected_crash_logs_type_module_and_location_but_never_content(ready_api, use_provider, caplog):
    import logging
    caplog.set_level(logging.ERROR)

    class Boom(Scripted):
        def generate(self, system, user, *, timeout):
            raise ModuleNotFoundError("secret user text zebra-77", name="some_missing_pkg")
    use_provider(Boom(lambda u: {}))
    s = ready_api.make_sample(15, texts=[{"item_type": "interest", "text": f"zebra-77 topic {i}"} for i in range(12)])
    jid = ready_api.queue(s).json()["job_id"]
    run_all()
    assert ready_api.get(f"/api/v1/jobs/{jid}").json()["error"]["code"] == "INTERNAL_ERROR"
    assert "ModuleNotFoundError" in caplog.text and "some_missing_pkg" in caplog.text and "test_sarvam.py" in caplog.text
    assert "zebra-77" not in caplog.text and "secret user text" not in caplog.text


def test_error_handling_survives_a_sdk_layout_change(sarvam, monkeypatch):
    """If sarvamai.core.api_error moves in a future SDK version, HTTP errors must still map cleanly."""
    monkeypatch.setitem(__import__("sys").modules, "sarvamai.core.api_error", None)
    err = Exception("boom"); err.status_code = 503
    sarvam.outcome = err
    with pytest.raises(ProviderUnavailable, match="503"):
        SarvamProvider().generate("s", "{}", timeout=5)
    sarvam.outcome = ValueError("a real bug")
    with pytest.raises(ValueError):                                          # unknown failures still surface (and get logged)
        SarvamProvider().generate("s", "{}", timeout=5)
