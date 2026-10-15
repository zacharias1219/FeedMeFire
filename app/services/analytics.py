"""Product events. Names only; never item text, roast text or sensitive classifications (PRD §18.1)."""
from __future__ import annotations

from sqlalchemy.orm import Session

from ..models import AnalyticsEvent

EVENTS = {"landing_viewed", "demo_opened", "sample_started", "sample_approved", "generation_requested",
          "generation_completed", "generation_failed", "report_viewed", "export_downloaded",
          "feedback_submitted", "data_deleted"}


def track(db: Session, name: str) -> None:
    if name not in EVENTS:
        raise ValueError(f"unknown analytics event {name!r}")
    db.add(AnalyticsEvent(name=name))
