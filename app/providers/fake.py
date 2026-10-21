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
        "spicy": ("shamelessly", "ferociously", "unapologetically"),  # what Savage used to be
        "savage": ("shamelessly", "ferociously", "unapologetically"),
    }

    # Savage: maximum heat, built from as many of the user's own items as possible. Safe by construction:
    # it attacks the queue and the habits, never identity, looks, health or worth.
    SAVAGE_TITLES = ["That is not a taste, that is a hostage note", "The plan, the excuse and the alibi",
                     "Consumption without a single result", "A rotation built to avoid starting",
                     "Four tabs deep and nothing begun", "Exhibit F: the pattern is the defendant"]
    SAVAGE_JOKES = [
        "“{a}”, “{b}”, “{c}” and “{d}”: that is not a taste, that is a hostage note written by an algorithm.",
        "You watch “{a}” like it will change your life, then “{b}” to make sure it never does. “{c}” and “{d}” are the alibis.",
        "“{a}” is the plan, “{b}” is the procrastination, “{c}” is the excuse, and “{d}” is you calling it research.",
        "Somewhere a creator is thanking you for the views on “{a}” and “{b}”. Nobody is thanking you for the results of “{c}” or “{d}”.",
        "You consume “{a}” and “{b}” like coffee: constantly, grudgingly, with zero visible improvement. “{c}” and “{d}” are the refills.",
        "“{a}”, “{b}”, “{c}”, “{d}”: four different hobbies, one shared outcome, which is you watching someone else do it.",
    ]
    SAVAGE_CONTRA = [
        "“{x}” says you want discipline. “{y}” says you want a nap with subtitles. The queue has decided, and it chose the nap.",
        "You keep “{x}” in the queue as proof of ambition and “{y}” as proof of reality. Reality has been winning for a while.",
    ]

    def _savage(self, items: list[dict], ids: list[str]) -> dict:
        n = len(items)
        cnt = 6 if n >= 12 else 4
        obs = []
        for k in range(cnt):
            four = [items[(4 * k + j) % n] for j in range(4)]
            obs.append({
                "title": self.SAVAGE_TITLES[k][:80],
                "joke": self.SAVAGE_JOKES[k].format(a=_snip(four[0]["text"], 34), b=_snip(four[1]["text"], 34),
                                                    c=_snip(four[2]["text"], 34), d=_snip(four[3]["text"], 34))[:300],
                "evidence_item_ids": [i["id"] for i in four],
            })
        used = 4 * cnt
        contra = []
        for k in range(2):
            x, y = items[(used + 2 * k) % n], items[(used + 2 * k + 1) % n]
            contra.append({"joke": self.SAVAGE_CONTRA[k].format(x=_snip(x["text"], 40), y=_snip(y["text"], 40))[:250],
                           "evidence_item_ids": [x["id"], y["id"]]})
        by_type: dict[str, int] = {}
        for i in items:
            by_type[i["type"]] = by_type.get(i["type"], 0) + 1
        top = max(by_type, key=by_type.get)
        persona = {"video_title": "The Unrepentant Title Collector", "channel_name": "The Channel Hostage",
                   "interest": "The Interest Hoarder"}[top]
        return {
            "schema_version": "1.0",
            "headline": "Your taste called. It wants a lawyer.",
            "persona": {"label": persona, "description": "A fictional viewing persona, not a personality assessment."},
            "opening_roast": (f"Out of {n} things you chose, every single one is a confession. You didn't build a taste, "
                              "you built an excuse, one tab at a time, and the evidence is merciless."),
            "observations": obs, "contradictions": contra,
            "affectionate_closer": "Be proud: very few people have turned procrastination into a full-time archive.",
            "share_quote": "I got roasted by my own queue and it was right.",
            "disclaimer": DISCLAIMER,
        }

    def generate(self, system: str, user: str, *, timeout: float) -> ProviderResult:
        doc = json.loads(user)
        items = doc["sample_items"]
        level = next((k for k in self.TONE if doc["intensity"].lower().startswith(k)), "spicy")
        adv = self.TONE[level]
        ids = [i["id"] for i in items]
        if level == "savage":
            text = json.dumps(self._savage(items, ids), ensure_ascii=False)
            return ProviderResult(text, input_tokens=len(user) // 4, output_tokens=len(text) // 4)
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
