"""Gemini adapter (REST). The model is configuration, never hard-coded elsewhere."""
from __future__ import annotations

import httpx

from ..config import settings
from .base import Provider, ProviderResult, ProviderTimeout, ProviderUnavailable, output_budget

BASE = "https://generativelanguage.googleapis.com/v1beta/models"


class GeminiProvider(Provider):
    name = "gemini"

    def __init__(self, api_key: str | None = None, model: str | None = None):
        self.api_key = api_key or settings.gemini_api_key
        self.model = model or settings.gemini_model

    def _post(self, body: dict, timeout: float) -> dict:
        try:
            r = httpx.post(f"{BASE}/{self.model}:generateContent", json=body, timeout=timeout,
                           headers={"x-goog-api-key": self.api_key, "content-type": "application/json"})
        except httpx.TimeoutException as e:
            raise ProviderTimeout("provider timed out") from e
        except httpx.HTTPError as e:
            raise ProviderUnavailable("provider unreachable") from e
        if r.status_code >= 400:
            # Never include the response body: it can echo prompt content.
            raise ProviderUnavailable(f"provider returned HTTP {r.status_code}")
        return r.json()

    def generate(self, system: str, user: str, *, timeout: float) -> ProviderResult:
        data = self._post({
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "maxOutputTokens": output_budget(user),
                "temperature": settings.temperature,
            },
        }, timeout)
        try:
            text = "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
        except (KeyError, IndexError, TypeError):
            text = ""  # blocked or empty candidate: surfaces as INVALID_MODEL_OUTPUT upstream
        usage = data.get("usageMetadata") or {}
        return ProviderResult(text, int(usage.get("promptTokenCount", 0)),
                              int(usage.get("candidatesTokenCount", 0)))

    def moderate(self, text: str, *, timeout: float) -> bool:
        data = self._post({
            "systemInstruction": {"parts": [{"text":
                "You are a strict content-safety reviewer. Reply with exactly SAFE or UNSAFE. UNSAFE if the text "
                "contains hate, slurs, attacks on protected characteristics, diagnoses, body-shaming, sexual "
                "humiliation, threats, self-harm encouragement, or invented personal facts."}]},
            "contents": [{"role": "user", "parts": [{"text": text}]}],
            "generationConfig": {"maxOutputTokens": 8, "temperature": 0},
        }, timeout)
        try:
            verdict = data["candidates"][0]["content"]["parts"][0]["text"].strip().upper()
        except (KeyError, IndexError, TypeError):
            return False
        return verdict.startswith("SAFE")
