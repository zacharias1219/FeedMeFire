from __future__ import annotations

import json
import os
import uuid

os.environ.setdefault("ENV", "test")
os.environ.setdefault("PROVIDER", "fake")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import db as dbm  # noqa: E402
from app.config import settings  # noqa: E402
from app.models import Base  # noqa: E402
from app.providers.base import Provider, ProviderResult  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    """Every test gets an empty database. Set TEST_DATABASE_URL to run the suite against PostgreSQL."""
    url = os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{tmp_path}/test.db"
    dbm.rebind(url)
    Base.metadata.drop_all(dbm.engine)
    Base.metadata.create_all(dbm.engine)
    monkeypatch.setattr(settings, "provider", "fake")
    monkeypatch.setattr(settings, "env", "test")
    monkeypatch.setattr(settings, "lease_seconds", 30)
    yield
    dbm.engine.dispose()


def make_client() -> "Api":
    from app.main import app
    return Api(TestClient(app, base_url="http://testserver"))


class Api:
    """Thin client that carries the session cookie and CSRF token like the browser app does."""

    def __init__(self, tc: TestClient):
        self.tc, self.csrf = tc, None

    def start(self, age=True):
        r = self.tc.post("/api/v1/session", json={"age_attested": age})
        if r.status_code == 201:
            self.csrf = r.json()["csrf_token"]
        return r

    def h(self, extra=None):
        h = {"X-CSRF-Token": self.csrf or ""}
        h.update(extra or {})
        return h

    def get(self, path):
        return self.tc.get(path)

    def post(self, path, json=None, headers=None):
        return self.tc.post(path, json=json if json is not None else {}, headers=self.h(headers))

    def patch(self, path, json):
        return self.tc.patch(path, json=json, headers=self.h())

    def delete(self, path):
        return self.tc.delete(path, headers=self.h())

    def consent_all(self):
        for c in ("terms_privacy", "own_sample", "provider_send", "entertainment"):
            assert self.post("/api/v1/consents", {"consent_type": c}).status_code == 201

    def make_sample(self, n=15, approve=True, texts=None):
        types = ["video_title", "channel_name", "interest"]
        items = texts or [{"item_type": types[i % 3], "text": f"Sample thing number {i} about gardening-{i}"} for i in range(n)]
        r = self.post("/api/v1/samples", {"items": items})
        assert r.status_code == 201, r.text
        s = r.json()
        if approve and s["eligible_item_count"] >= settings.min_items:
            a = self.post(f"/api/v1/samples/{s['id']}/approve")
            assert a.status_code == 200, a.text
            s = a.json()
        return s

    def queue(self, sample, intensity="spicy", key=None):
        return self.post("/api/v1/reports", {"sample_id": sample["id"], "approved_sample_hash": sample["approved_sample_hash"],
                                              "intensity": intensity},
                         headers={"Idempotency-Key": key or str(uuid.uuid4())})


@pytest.fixture
def api():
    a = make_client()
    assert a.start().status_code == 201
    return a


@pytest.fixture
def ready_api(api):
    api.consent_all()
    return api


class Scripted(Provider):
    """Provider whose responses are scripted per call; records everything it receives."""
    name = "scripted"
    model = "scripted-1"

    def __init__(self, *responses, on_call=None):
        self.responses = list(responses)
        self.calls: list[tuple[str, str]] = []
        self.on_call = on_call

    def generate(self, system, user, *, timeout):
        self.calls.append((system, user))
        if self.on_call:
            self.on_call(len(self.calls))
        r = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(r, Exception):
            raise r
        text = r(user) if callable(r) else r
        return ProviderResult(text if isinstance(text, str) else json.dumps(text), 100, 200)


def good_report(user: str, **over) -> dict:
    """A valid, safe report built from the aliases present in the request payload."""
    ids = [i["id"] for i in json.loads(user)["sample_items"]]
    rep = {
        "schema_version": "1.0", "headline": "Your garden has opinions",
        "persona": {"label": "The Patient Planter", "description": "A fictional viewing persona."},
        "opening_roast": "You chose a very leafy sample and committed to the bit.",
        "observations": [{"title": f"Pattern {n}", "joke": f"Items {n} keep showing up like a houseplant you forgot to water.",
                          "evidence_item_ids": [ids[n], ids[n + 1]]} for n in range(4)],
        "contradictions": [{"joke": "Calm gardening, frantic tab count.", "evidence_item_ids": [ids[4], ids[5]]},
                           {"joke": "Slow growth, fast scrolling.", "evidence_item_ids": [ids[6], ids[7]]}],
        "affectionate_closer": "May your seedlings outlive your queue.",
        "share_quote": "My queue grows faster than my garden.",
        "disclaimer": "whatever the model says is replaced",
    }
    rep.update(over)
    return rep


@pytest.fixture
def use_provider(monkeypatch):
    def _use(p):
        monkeypatch.setattr("app.providers.get_provider", lambda: p)
        return p
    return _use
