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
