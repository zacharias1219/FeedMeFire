"""Sarvam AI adapter (chat completions). Model, key and JSON mode are configuration."""
from __future__ import annotations

import logging
import re

import httpx

from ..config import settings
from .base import Provider, ProviderResult, ProviderTimeout, ProviderUnavailable, output_budget

log = logging.getLogger("rmf.provider")
_THINK = re.compile(r"<think>.*?</think>", re.S | re.I)
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.I)


def extract_json(text: str) -> str:
    """Reasoning models may wrap the answer in <think> blocks or code fences; keep just the JSON object."""
    text = _THINK.sub("", text or "")
    if "</think>" in text:  # an opening tag we never saw: everything before the closing tag is reasoning
        text = text.rsplit("</think>", 1)[1]
    text = _FENCE.sub("", text.strip())
    a, b = text.find("{"), text.rfind("}")
    return text[a:b + 1] if a != -1 and b > a else text.strip()


class SarvamProvider(Provider):
    name = "sarvam"

    def __init__(self, api_key: str | None = None, model: str | None = None):
        self.api_key = api_key if api_key is not None else settings.sarvam_api_key
        self.model = model or settings.sarvam_model

    def _chat(self, system: str, user: str, *, timeout: float, max_tokens: int, temperature: float, json_mode: bool = False):
        if not self.api_key:
            raise ProviderUnavailable("SARVAM_API_KEY is not configured")
        from sarvamai import SarvamAI
        from sarvamai.core.api_error import ApiError
        client_kwargs: dict = {"api_subscription_key": self.api_key}
        if settings.sarvam_base_url:  # proxy / local stand-in; production uses the SDK default
            from sarvamai.environment import SarvamAIEnvironment
            base = settings.sarvam_base_url.rstrip("/")
            client_kwargs["environment"] = SarvamAIEnvironment(base=base, creative=base, production=base)
        kwargs = dict(model=self.model, messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                      temperature=temperature, max_tokens=max_tokens,
                      request_options={"timeout_in_seconds": max(1, int(timeout)), "max_retries": 0})
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        if settings.sarvam_reasoning_effort in ("low", "high", "max"):
            kwargs["reasoning_effort"] = settings.sarvam_reasoning_effort
        try:
            return SarvamAI(**client_kwargs).chat.completions(**kwargs)
        except httpx.TimeoutException as e:
            raise ProviderTimeout("provider timed out") from e
        except ApiError as e:  # never include the body: it can echo prompt content
            raise ProviderUnavailable(f"provider returned HTTP {e.status_code}") from e
        except httpx.HTTPError as e:
            raise ProviderUnavailable("provider unreachable") from e

    def generate(self, system: str, user: str, *, timeout: float) -> ProviderResult:
        resp = self._chat(system, user, timeout=timeout, max_tokens=output_budget(user),
                          temperature=settings.temperature, json_mode=settings.sarvam_json_mode)
        try:
            content = resp.choices[0].message.content or ""  # reasoning_content is a separate field and is ignored
        except (AttributeError, IndexError, TypeError):
            content = ""
        usage = getattr(resp, "usage", None)
        # Diagnostics only (never content): lets an operator see truncation or empty answers in the logs.
        finish = getattr(resp.choices[0], "finish_reason", None) if getattr(resp, "choices", None) else None
        log.info("sarvam completion model=%s finish_reason=%s prompt_tokens=%s completion_tokens=%s chars=%d",
                 self.model, finish, getattr(usage, "prompt_tokens", None), getattr(usage, "completion_tokens", None), len(content))
        if not content.strip() or finish == "length":
            log.warning("sarvam returned %s (finish_reason=%s): raise MAX_OUTPUT_TOKENS_INDIC/MAX_OUTPUT_TOKENS or lower "
                        "SARVAM_REASONING_EFFORT", "an empty answer" if not content.strip() else "a truncated answer", finish)
        return ProviderResult(extract_json(content), int(getattr(usage, "prompt_tokens", 0) or 0),
                              int(getattr(usage, "completion_tokens", 0) or 0))

    def moderate(self, text: str, *, timeout: float) -> bool:
        resp = self._chat(
            "You are a strict content-safety reviewer. The text may be in any Indian language. Reply with exactly SAFE or "
            "UNSAFE. UNSAFE if it contains hate, slurs, attacks on protected characteristics (religion, caste, ethnicity, "
            "sexuality, disability), diagnoses, body-shaming, sexual humiliation, threats, self-harm encouragement, "
            "profanity, or invented personal facts about the reader.",
            text, timeout=timeout, max_tokens=400, temperature=0)
        try:
            verdict = _THINK.sub("", resp.choices[0].message.content or "").strip().upper()
        except (AttributeError, IndexError, TypeError):
            return False
        return verdict.startswith("SAFE")
