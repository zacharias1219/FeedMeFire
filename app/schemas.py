"""Pydantic contracts: API requests and the strict report output schema (PRD §9.5, §13)."""
from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

ItemType = Literal["video_title", "channel_name", "interest"]
Intensity = Literal["gentle", "spicy", "savage"]
Language = Literal["en", "hi", "ta", "te", "bn", "mr"]
ConsentType = Literal["terms_privacy", "own_sample", "provider_send", "entertainment"]
REQUIRED_CONSENTS: tuple[str, ...] = ("terms_privacy", "own_sample", "provider_send", "entertainment")
DISCLAIMER = ("AI-generated entertainment based only on the sample you selected. "
              "Not a psychological assessment or a record of your actual viewing history.")
ALIAS_RE = re.compile(r"^item_\d{2,3}$")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- requests -----------------------------------------------------------------------------------
class SessionCreate(Strict):
    age_attested: bool = False


class ConsentIn(Strict):
    consent_type: ConsentType


class ItemIn(Strict):
    item_type: ItemType
    text: str = Field(max_length=2000)  # hard cap; the 3–200 rule is enforced per item with a friendly reason


class SampleCreate(Strict):
    source: Literal["manual", "demo"] = "manual"
    items: list[ItemIn] = Field(default_factory=list, max_length=500)


class ItemUpdate(Strict):
    id: str
    text: str = Field(max_length=2000)


class SamplePatch(Strict):
    add: list[ItemIn] = Field(default_factory=list, max_length=500)
    update: list[ItemUpdate] = Field(default_factory=list, max_length=500)
    remove: list[str] = Field(default_factory=list, max_length=500)
    clear: bool = False


class ReportCreate(Strict):
    sample_id: str
    approved_sample_hash: str
    intensity: Intensity = "spicy"
    language: Language = "en"


class RegenerateIn(Strict):
    intensity: Intensity


class FeedbackIn(Strict):
    is_funny: bool | None = None
    is_specific: bool | None = None
    is_too_harsh: bool | None = None
    comment: Annotated[str, StringConstraints(max_length=500)] | None = None


class EventIn(Strict):
    name: Literal["export_downloaded"]


# --- report output ------------------------------------------------------------------------------
Alias = Annotated[str, StringConstraints(pattern=r"^item_\d{2,3}$")]
Evidence = Annotated[list[Alias], Field(min_length=1, max_length=4)]


class Persona(Strict):
    label: Annotated[str, StringConstraints(min_length=1, max_length=60)]
    description: Annotated[str, StringConstraints(min_length=1, max_length=200)]


class Observation(Strict):
    title: Annotated[str, StringConstraints(min_length=1, max_length=80)]
    joke: Annotated[str, StringConstraints(min_length=1, max_length=300)]
    evidence_item_ids: Evidence


class Contradiction(Strict):
    joke: Annotated[str, StringConstraints(min_length=1, max_length=250)]
    evidence_item_ids: Evidence


class ReportOut(Strict):
    schema_version: Literal["1.0"]
    headline: Annotated[str, StringConstraints(min_length=1, max_length=100)]
    persona: Persona
    opening_roast: Annotated[str, StringConstraints(min_length=1, max_length=500)]
    observations: Annotated[list[Observation], Field(min_length=4, max_length=6)]
    contradictions: Annotated[list[Contradiction], Field(min_length=2, max_length=2)]
    affectionate_closer: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    share_quote: Annotated[str, StringConstraints(min_length=1, max_length=160)]
    disclaimer: Annotated[str, StringConstraints(min_length=1, max_length=400)]

    @model_validator(mode="after")
    def _enough_distinct_evidence(self):
        if len(self.evidence_ids()) < 6:
            raise ValueError("at least six distinct items must support the report")
        return self

    def evidence_ids(self) -> set[str]:
        ids: set[str] = set()
        for o in self.observations:
            ids.update(o.evidence_item_ids)
        for c in self.contradictions:
            ids.update(c.evidence_item_ids)
        return ids

    def narrative_text(self) -> list[str]:
        parts = [self.headline, self.persona.label, self.persona.description, self.opening_roast,
                 self.affectionate_closer, self.share_quote]
        for o in self.observations:
            parts += [o.title, o.joke]
        parts += [c.joke for c in self.contradictions]
        return parts

    def word_count(self) -> int:
        return sum(len(p.split()) for p in self.narrative_text())


def validate_evidence(report: ReportOut, valid_aliases: set[str]) -> list[str]:
    """Evidence validation proves references exist; whether they *support* the joke needs evaluation."""
    return sorted(report.evidence_ids() - valid_aliases)
