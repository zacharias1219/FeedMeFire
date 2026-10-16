"""Deterministic offline generator for development, demos and CI. No data leaves the process.

It builds a valid, safe, sample-specific report from the supplied items, so the whole pipeline
(validation, evidence checks, safety, persistence) is exercised without a network call.
"""
from __future__ import annotations

import json

from ..schemas import DISCLAIMER
from .base import Provider, ProviderResult


def _snip(text: str, n: int = 38) -> str:
    text = text.strip()
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


class FakeProvider(Provider):
    name = "fake"
    model = "fake-roaster-1"

    TONE = {
        "gentle": ("quietly", "adorably", "lovingly"),
        "spicy": ("suspiciously", "aggressively", "relentlessly"),
        "savage": ("shamelessly", "ferociously", "unapologetically"),
    }

    def generate(self, system: str, user: str, *, timeout: float) -> ProviderResult:
        doc = json.loads(user)
        items = doc["sample_items"]
        level = next((k for k in self.TONE if doc["intensity"].lower().startswith(k)), "spicy")
        adv = self.TONE[level]
        ids = [i["id"] for i in items]
        by_type: dict[str, list[dict]] = {}
        for i in items:
            by_type.setdefault(i["type"], []).append(i)

        def pick(n: int) -> list[dict]:  # spread evidence across the whole sample
            return [items[(n * 3 + k * 5) % len(items)] for k in range(2)]

        obs = []
        titles = ["The pattern is the plot", "Your queue has a theme", "Curiosity, but make it a lifestyle",
                  "Specific tastes, specific consequences", "A very committed rotation", "Range, technically"]
        for n in range(min(6, max(4, len(items) // 3))):
            a, b = pick(n)
            if a["id"] == b["id"]:
                b = items[(items.index(a) + 1) % len(items)]
            obs.append({
                "title": titles[n],
                "joke": (f"You {adv[n % 3]} pair “{_snip(a['text'])}” with “{_snip(b['text'])}”, "
                         f"and somehow call that a balanced diet."),
                "evidence_item_ids": [a["id"], b["id"]],
            })
        first, last = items[0], items[-1]
        mid = items[len(items) // 2]
        contradictions = [
            {"joke": f"“{_snip(first['text'])}” says focus; “{_snip(mid['text'])}” says tab number forty.",
             "evidence_item_ids": [first["id"], mid["id"]]},
            {"joke": f"You picked “{_snip(last['text'])}” like a plan, then “{_snip(items[1]['text'])}” like a detour.",
             "evidence_item_ids": [last["id"], items[1]["id"]]},
        ]
        # Make sure at least six distinct items back the report, whatever the sample shape.
        seen = {e for o in obs for e in o["evidence_item_ids"]} | {e for c in contradictions for e in c["evidence_item_ids"]}
        for extra in ids:
            if len(seen) >= 6:
                break
            if extra not in seen:
                obs[0]["evidence_item_ids"].append(extra) if len(obs[0]["evidence_item_ids"]) < 4 else \
                    obs[1]["evidence_item_ids"].append(extra)
                seen.add(extra)
        top = max(by_type, key=lambda t: len(by_type[t]))
        persona = {"video_title": "The Title Collector", "channel_name": "The Channel Loyalist",
                   "interest": "The Interest Sampler"}[top]
        report = {
            "schema_version": "1.0",
            "headline": f"Your taste, {adv[0]} on trial",
            "persona": {"label": persona, "description": "A fictional viewing persona, not a personality assessment."},
            "opening_roast": (f"Out of {len(items)} things you chose, a clear theme emerged: you do not do "
                              "things halfway, you do them in tabs."),
            "observations": obs,
            "contradictions": contradictions,
            "affectionate_closer": "At least your curiosity has excellent stamina.",
            "share_quote": f"My taste has range, and I have {len(items)} receipts.",
            "disclaimer": DISCLAIMER,
        }
        text = json.dumps(report, ensure_ascii=False)
        return ProviderResult(text, input_tokens=len(user) // 4, output_tokens=len(text) // 4)
