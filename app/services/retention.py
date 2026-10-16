"""Deletion, expiry cleanup and the deletion ledger (PRD §7.11, §14)."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import delete, exists, select, update
from sqlalchemy.orm import Session

from ..config import settings
from ..crypto import hmac_hex
from ..db import utcnow
from ..models import (AnalyticsEvent, DeletionLedger, Feedback, GenerationJob, RateEvent, Report, Sample,
                      SessionModel)
from . import analytics
from .jobs import ACTIVE


def session_ref(session_id: str) -> str:
    return hmac_hex("deleted-session", session_id)


def delete_session_data(db: Session, session_id: str) -> None:
    """One transaction: cancel jobs, then delete the session (FK cascade removes everything it owns).

    A worker that is mid-generation can no longer persist: its job row is gone, and its final write
    takes a row lock and re-checks liveness, so the two orderings both end with nothing stored.
    The ledger keeps only an HMAC of the session id, never any content.
    """
    now = utcnow()
    db.execute(update(GenerationJob).where(GenerationJob.session_id == session_id, GenerationJob.status.in_(ACTIVE))
               .values(status="cancelled", cancelled_at=now))
    db.execute(delete(SessionModel).where(SessionModel.id == session_id))
    db.add(DeletionLedger(session_ref=session_ref(session_id)))
    analytics.track(db, "data_deleted")


def reapply_deletions(db: Session) -> int:
    """After restoring a backup, delete anything the ledger says was already deleted."""
    refs = set(db.scalars(select(DeletionLedger.session_ref)))
    n = 0
    for sid in list(db.scalars(select(SessionModel.id))):
        if session_ref(sid) in refs:
            db.execute(delete(SessionModel).where(SessionModel.id == sid))
            n += 1
    return n


def cleanup(db: Session) -> dict[str, int]:
    """Physically remove expired rows. Reads already refuse expired data; this just reclaims it."""
    now = utcnow()
    out = {}
    out["samples"] = db.execute(delete(Sample).where(Sample.expires_at <= now)).rowcount  # cascades items, jobs
    out["reports"] = db.execute(delete(Report).where(Report.expires_at <= now)).rowcount
    out["feedback"] = db.execute(delete(Feedback).where(Feedback.expires_at <= now)).rowcount
    # A session row is kept while its (separately retained) feedback still exists.
    has_feedback = exists().where(Feedback.session_id == SessionModel.id)
    out["sessions"] = db.execute(delete(SessionModel).where(SessionModel.expires_at <= now, ~has_feedback)).rowcount
    out["rate_events"] = db.execute(delete(RateEvent).where(RateEvent.created_at < now - timedelta(days=2))).rowcount
    out["analytics"] = db.execute(delete(AnalyticsEvent).where(
        AnalyticsEvent.created_at < now - timedelta(days=settings.metrics_ttl_days))).rowcount
    # Ledger entries must outlive the 7-day backup window.
    out["ledger"] = db.execute(delete(DeletionLedger).where(DeletionLedger.deleted_at < now - timedelta(days=14))).rowcount
    return out
