"""Rule-based safety evaluation of generated reports (PRD §8).

This is a deterministic first line of defence, NOT a complete moderation system. It deliberately
errs toward rejecting. It must be backed by the human-reviewed evaluation set (PRD §20) and,
optionally, a provider moderation pass (SAFETY_JUDGE_ENABLED). Matches are reported as category
codes only, never as content, so logs and retries never carry the rejected text.
"""
from __future__ import annotations

import re

from ..schemas import ReportOut

_RULES: dict[str, re.Pattern] = {k: re.compile(v, re.I) for k, v in {
    "markup_or_link": r"<\s*/?\s*[a-z][^>]*>|https?://|\bwww\.|\]\(|javascript:|\bdata:text",
    "diagnosis": r"diagnos\w*|\bdisorder\b|\badhd\b|depress(?:ed|ion|ive)|\bbipolar\b|\bocd\b|\bptsd\b|clinical\w*|"
                 r"mental (?:illness|health (?:issue|problem|crisis))|\btherapy\b|\bneurotic\b|\bpsychotic\b",
    "addiction_or_danger": r"addict\w*|\bcriminal\w*|\bdangerous\b|psychopath\w*|sociopath\w*|\bcreep\b|\bpredator\b",
    "protected_trait": r"muslim|christian|hindu|jewish|sikh|buddhist|atheist|\bcaste\b|\bgay\b|lesbian|bisexual|"
                       r"transgender|disabled|autistic|republican|democrat|\bmaga\b|leftist|right[- ]wing|"
                       r"\bracial\b|\bethnic\w*",
    "body_shaming": r"\bfat\b|obese|\bugly\b|overweight|\bskinny\b|\bbald\b|\bgross looking\b",
    "sexual": r"\bsexual\w*|\bhorny\b|\bporn\w*|\bnsfw\b|\bnaked\b",
    "threat_or_self_harm": r"kill yourself|\bkys\b|end it all|hurt yourself|self[- ]?harm|\bsuicid\w*|go die",
    "profanity": r"\bfuck\w*|\bshit\w*|\bbitch\w*|\basshole\w*|\bbastard\w*|\bdick\b|\bcunt\b",
    "biographical_claim": r"you(?:'re| are) (?:unemployed|jobless|broke|poor|single|lonely|divorced|failing)|"
                          r"\byour (?:boss|wife|husband|girlfriend|boyfriend|\bex\b|mom|dad|salary|rent|landlord|therapist)\b|"
                          r"you (?:have no|don't have any) (?:friends|job|life)",
    "history_claim": r"watch history|watch[- ]?time|you(?:'ve| have) watched|you watched|hours (?:a day )?(?:of )?watching|"
                     r"\bscreen[- ]?time\b",
}.items()}

_NEGATIVE = re.compile(r"\b(?:trash|garbage|terrible|awful|talentless|cringe|hack|annoying|overrated|fraud|scam|"
                       r"clown|pathetic|stupid|idiot\w*|losers?|worthless)\b", re.I)
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def evaluate(report: ReportOut, *, allowed_terms: set[str] | None = None,
             channel_names: list[str] | None = None) -> list[str]:
    """Return a sorted list of violated category codes (empty = safe).

    `allowed_terms`: words the user typed themselves. Echoing the user's own wording (e.g. a
    video literally titled "Fat loss meal prep") is not an attack, so those exact words are exempt.
    """
    allowed = {t.lower() for t in (allowed_terms or set())}
    violations: set[str] = set()
    parts = report.narrative_text()
    text = "\n".join(parts)
    for code, rx in _RULES.items():
        for m in rx.finditer(text):
            token = m.group(0).lower().strip()
            if code in ("markup_or_link",) or token not in allowed:
                violations.add(code)
                break
    # Named creators may be referenced, never insulted (PRD §8.2).
    for name in {n.strip() for n in (channel_names or []) if len(n.strip()) >= 3}:
        rx = re.compile(re.escape(name), re.I)
        for part in parts:
            for sentence in _SENTENCE.split(part):
                if rx.search(sentence) and _NEGATIVE.search(sentence):
                    violations.add("creator_attack")
    return sorted(violations)
