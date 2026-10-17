"""Job queue on PostgreSQL (PRD §11.3): atomic claim, lease + heartbeat, reclaim, bounded retries."""
from __future__ import annotations

import random
import threading
from datetime import timedelta

from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session

from ..config import settings
from ..db import session_scope, utcnow
from ..models import GenerationJob

ACTIVE = ("queued", "validating", "generating", "checking")
IN_FLIGHT = ("validating", "generating", "checking")
TERMINAL = ("ready", "failed", "cancelled", "expired")

STAGE_LABELS = {
    "queued": "Waiting in line",
    "validating": "Checking your approved sample",
    "generating": "Writing your roast",
    "checking": "Running safety and grounding checks",
    "ready": "Ready",
    "failed": "Failed",
    "cancelled": "Cancelled",
    "expired": "Expired",
}

# Legal transitions. Anything else is a bug and raises.
TRANSITIONS: dict[str, set[str]] = {
    "queued": {"validating", "cancelled", "failed", "expired"},
    "validating": {"generating", "queued", "cancelled", "failed", "expired"},
    "generating": {"checking", "queued", "cancelled", "failed", "expired"},
    "checking": {"ready", "generating", "queued", "cancelled", "failed", "expired"},
    "ready": set(), "failed": set(), "cancelled": set(), "expired": set(),
}


class IllegalTransition(Exception):
    pass


def check_transition(old: str, new: str) -> None:
    if new not in TRANSITIONS.get(old, set()):
        raise IllegalTransition(f"{old} -> {new}")


def backoff_seconds(attempt: int) -> float:
    """Exponential backoff with jitter: ~2s, ~4s, ~8s ..."""
    return min(2.0 * 2 ** max(0, attempt - 1), 60.0) + random.uniform(0, 1.0)


def claim_next(db: Session, owner: str) -> GenerationJob | None:
    """Atomically claim one eligible job: queued and due, or in flight with an expired lease."""
    now = utcnow()
    q = (select(GenerationJob)
         .where(or_(and_(GenerationJob.status == "queued", GenerationJob.run_after <= now),
                    and_(GenerationJob.status.in_(IN_FLIGHT), GenerationJob.lease_expires_at < now)))
         .order_by(GenerationJob.created_at).limit(1).with_for_update(skip_locked=True))
    job = db.execute(q).scalar_one_or_none()
    if job is None:
        db.rollback()
        return None
    # Compare-and-swap: the SELECT above may be seen by several workers (SQLite ignores SKIP LOCKED), so the
    # claim only counts if the row is still exactly as we read it. Exactly one UPDATE can win.
    seen_status, seen_lease, seen_attempts = job.status, job.lease_expires_at, job.attempt_count
    attempts = seen_attempts + 1
    values: dict = {"attempt_count": attempts}
    if attempts > settings.max_attempts:  # abandoned too many times: stop spending
        values.update(status="failed", error_code=job.error_code or "PROVIDER_UNAVAILABLE", completed_at=now,
                      lease_owner=None, lease_expires_at=None)
    else:
        values.update(status="validating", lease_owner=owner,
                      lease_expires_at=now + timedelta(seconds=settings.lease_seconds),
                      started_at=job.started_at or now)
    lease_match = (GenerationJob.lease_expires_at.is_(None) if seen_lease is None
                   else GenerationJob.lease_expires_at == seen_lease)
    won = db.execute(update(GenerationJob).where(
        GenerationJob.id == job.id, GenerationJob.status == seen_status,
        GenerationJob.attempt_count == seen_attempts, lease_match).values(**values)).rowcount == 1
    db.commit()
    if not won or attempts > settings.max_attempts:
        return None
    db.refresh(job)
    return job


class Heartbeat:
    """Extends the lease while a long provider call runs; stops when the lease is lost."""

    def __init__(self, job_id: str, owner: str):
        self.job_id, self.owner = job_id, owner
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name=f"hb-{job_id[:8]}")

    def _run(self) -> None:
        interval = max(0.2, settings.lease_seconds / 3)
        while not self._stop.wait(interval):
            try:
                with session_scope() as db:
                    res = db.execute(update(GenerationJob).where(
                        GenerationJob.id == self.job_id, GenerationJob.lease_owner == self.owner,
                        GenerationJob.status.in_(IN_FLIGHT)
                    ).values(lease_expires_at=utcnow() + timedelta(seconds=settings.lease_seconds)))
                    if res.rowcount == 0:
                        return
            except Exception:
                pass  # a missed beat is recoverable; the lease just may expire sooner

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
