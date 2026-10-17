"""Input validation, normalisation, sensitive-item flagging and deterministic sampling (PRD §7.3–7.4, §8.3)."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from ..config import settings
from ..crypto import hmac_hex

ITEM_TYPES = ("video_title", "channel_name", "interest")
MIN_TEXT, MAX_TEXT = 3, 200

# Control characters, zero-width and bidi-override characters. \t and \n become spaces below.
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f​-‏‪-‮⁠-⁤﻿]")
_NOT_A_TITLE = re.compile(r"^(?:(?:\d{1,2}:)?\d{1,2}:\d{2}|\d[\d.,]*\s*[kKmMbB]?(?:\s*(?:views?|subscribers?))?)$")
_URL_ONLY = re.compile(r"^(?:[a-z][a-z0-9+.-]*://|www\.)\S+$|^\S+\.[a-z]{2,}/\S*$", re.I)


def clean_text(raw: str) -> str:
    """Strip control characters and normalise whitespace only; wording is otherwise preserved."""
    return " ".join(_CONTROL.sub("", raw or "").split())


def dedupe_key(item_type: str, text: str) -> str:
    """Hash for duplicate detection without plaintext indexing."""
    norm = unicodedata.normalize("NFKC", text).casefold()
    return hmac_hex("item", item_type, norm)


def validate_item(item_type: str, raw: str) -> tuple[str | None, str | None]:
    """Return (clean_text, None) or (None, reason)."""
    if item_type not in ITEM_TYPES:
        return None, "Unsupported item type."
    text = clean_text(raw)
    if _URL_ONLY.match(text):
        return None, "That looks like a link. Paste the video title or channel name instead."
    if _NOT_A_TITLE.match(text):
        return None, "That looks like a number or duration, not a title or name."
    if len(text) < MIN_TEXT:
        return None, f"Use at least {MIN_TEXT} characters."
    if len(text) > MAX_TEXT:
        return None, f"Use at most {MAX_TEXT} characters."
    return text, None


# --- sensitive-item flagging -------------------------------------------------------------------
# Deliberately conservative keyword screens. They are NOT reliable detection of every sensitive
# item and the UI says so; flagged items are excluded from the model-bound sample.
_SENSITIVE = {
    "health": r"depress\w*|anxiety|suicid\w*|self[- ]?harm|bipolar|schizo\w*|adhd|autis\w*|ocd|eating disorder|"
              r"anorexi\w*|bulimi\w*|cancer|diagnos\w*|therapy|panic attack|mental (?:health|illness)|trauma|ptsd|"
              r"\bhiv\b|\baids\b|disabilit\w*|addict\w*",
    "religion": r"christian\w*|muslim\w*|islam\w*|hindu\w*|jewish|judaism|buddhis\w*|sikh\w*|atheis\w*|bible|quran|"
                r"church|mosque|religio\w*|\bcaste\b",
    "sexuality": r"\bgay\b|lesbian|bisexual|transgender|lgbt\w*|queer|sexual\w*|porn\w*|nsfw|onlyfans|fetish\w*|erotic\w*",
    "politics": r"republican\w*|democrat\w*|\bmaga\b|\btrump\b|\bbiden\b|\bmodi\b|\bliberals?\b|conservative\w*|"
                r"leftist|right[- ]wing|left[- ]wing|socialis\w*|communis\w*|election\w*|abortion|immigration",
    "ethnicity": r"racial|racis\w*|ethnic\w*|white supremac\w*|nazi\w*",
    "substances": r"cocaine|heroin|\bmeth\b|overdose|\bdrugs?\b",
    "personal_info": r"[\w.+-]+@[\w-]+\.[\w.]+|(?<!\d)(?:\+?\d[\d\s().-]{8,}\d)(?!\d)",
}
_SENSITIVE_RE = {k: re.compile(v, re.I) for k, v in _SENSITIVE.items()}
CATEGORY_LABELS = {
    "health": "health", "religion": "religion or belief", "sexuality": "sexuality or adult content",
    "politics": "politics", "ethnicity": "ethnicity", "substances": "substances",
    "personal_info": "personal information",
}


def sensitive_category(text: str) -> str | None:
    for cat, rx in _SENSITIVE_RE.items():
        if rx.search(text):
            return cat
    return None


# --- deterministic, type-balanced sampling -------------------------------------------------------
@dataclass
class Candidate:
    id: str
    item_type: str
    text: str
    flagged: bool
    position: int


def select_for_model(items: list[Candidate], max_items: int | None = None, max_chars: int | None = None) -> list[Candidate]:
    """Round-robin across item types in submission order until the item or character cap is hit.

    Deterministic: the same items always yield the same sample, so the user can see and steer it.
    """
    max_items = max_items or settings.max_model_items
    max_chars = max_chars or settings.max_model_chars
    pools = {t: [i for i in sorted(items, key=lambda x: x.position) if i.item_type == t and not i.flagged]
             for t in ITEM_TYPES}
    chosen: list[Candidate] = []
    chars = 0
    cursor = {t: 0 for t in ITEM_TYPES}
    progressed = True
    while progressed and len(chosen) < max_items:
        progressed = False
        for t in ITEM_TYPES:
            if len(chosen) >= max_items:
                break
            if cursor[t] >= len(pools[t]):
                continue
            nxt = pools[t][cursor[t]]
            cursor[t] += 1
            progressed = True
            if chars + len(nxt.text) > max_chars:
                continue  # skip an item that would bust the budget; shorter ones may still fit
            chosen.append(nxt)
            chars += len(nxt.text)
    return chosen


def alias_for(selection_order: int) -> str:
    return f"item_{selection_order + 1:02d}"


def sample_hash(entries: list[tuple[str, str, str]]) -> str:
    """entries = [(alias, item_type, text)] in selection order."""
    from ..crypto import sha256_hex
    return sha256_hex("\n".join(f"{a}|{t}|{x}" for a, t, x in entries))
