"""Data model (PRD §12). Ownership is enforced by foreign keys; deleting a session cascades."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (BigInteger, Boolean, DateTime, Float, ForeignKey, Index, Integer, String,
                        Text, UniqueConstraint)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from .db import utcnow


def new_id() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


CASCADE = {"ondelete": "CASCADE"}


class SessionModel(Base):
    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_secret_hash: Mapped[str] = mapped_column(String(64))  # raw secret lives only in the cookie
    prev_secret_hash: Mapped[str | None] = mapped_column(String(64))  # short grace after rotation
    rotated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime)
    age_attested_at: Mapped[datetime | None] = mapped_column(DateTime)


class Consent(Base):
    __tablename__ = "consents"
    __table_args__ = (UniqueConstraint("session_id", "consent_type"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id", **CASCADE), index=True)
    consent_type: Mapped[str] = mapped_column(String(32))
    policy_version: Mapped[str] = mapped_column(String(32))
    provider_disclosure_version: Mapped[str] = mapped_column(String(32))
    accepted_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Sample(Base):
    __tablename__ = "samples"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id", **CASCADE), index=True)
    source_type: Mapped[str] = mapped_column(String(16))  # manual | demo
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft | approved
    submitted_item_count: Mapped[int] = mapped_column(Integer, default=0)
    eligible_item_count: Mapped[int] = mapped_column(Integer, default=0)
    approved_sample_hash: Mapped[str | None] = mapped_column(String(64))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)


class SampleItem(Base):
    __tablename__ = "sample_items"
    __table_args__ = (UniqueConstraint("sample_id", "item_type", "normalized_text_hash"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    sample_id: Mapped[str] = mapped_column(ForeignKey("samples.id", **CASCADE), index=True)
    item_type: Mapped[str] = mapped_column(String(16))
    text_ciphertext: Mapped[str] = mapped_column(Text)
    normalized_text_hash: Mapped[str] = mapped_column(String(64))
    position: Mapped[int] = mapped_column(Integer)  # submission order
    selection_order: Mapped[int | None] = mapped_column(Integer)  # order in the model-bound sample
    is_sensitive_flagged: Mapped[bool] = mapped_column(Boolean, default=False)
    is_selected: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class GenerationJob(Base):
    __tablename__ = "generation_jobs"
    __table_args__ = (UniqueConstraint("session_id", "idempotency_key"),
                      Index("ix_jobs_status_run_after", "status", "run_after"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id", **CASCADE), index=True)
    sample_id: Mapped[str] = mapped_column(ForeignKey("samples.id", **CASCADE), index=True)
    kind: Mapped[str] = mapped_column(String(16), default="create")  # create | regenerate
    source_report_id: Mapped[str | None] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    intensity: Mapped[str] = mapped_column(String(16))
    language: Mapped[str] = mapped_column(String(8), default="en")
    idempotency_key: Mapped[str] = mapped_column(String(64))
    approved_sample_hash: Mapped[str] = mapped_column(String(64))
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    run_after: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    lease_owner: Mapped[str | None] = mapped_column(String(64))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime)
    error_code: Mapped[str | None] = mapped_column(String(40))
    report_id: Mapped[str | None] = mapped_column(String(36))
    # An ambiguous provider timeout means a retry may be billed twice (PRD §11.3).
    possible_duplicate_cost: Mapped[bool] = mapped_column(Boolean, default=False)


class Report(Base):
    __tablename__ = "reports"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id", **CASCADE), index=True)
    sample_id: Mapped[str] = mapped_column(ForeignKey("samples.id", **CASCADE), index=True)
    generation_job_id: Mapped[str] = mapped_column(ForeignKey("generation_jobs.id", **CASCADE))
    is_regeneration: Mapped[bool] = mapped_column(Boolean, default=False)
    intensity: Mapped[str] = mapped_column(String(16))
    report_ciphertext: Mapped[str] = mapped_column(Text)
    schema_version: Mapped[str] = mapped_column(String(8))
    prompt_version: Mapped[str] = mapped_column(String(32))
    provider: Mapped[str] = mapped_column(String(32))
    model_identifier: Mapped[str] = mapped_column(String(64))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost: Mapped[float] = mapped_column(Float, default=0.0)  # operational estimate, not an invoice
    pricing_version: Mapped[str] = mapped_column(String(32), default="unset")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)


class Feedback(Base):
    """Retained separately from the report (up to 30 days) and disclosed as such."""
    __tablename__ = "feedback"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id", **CASCADE), index=True)
    report_id: Mapped[str | None] = mapped_column(ForeignKey("reports.id", ondelete="SET NULL"))
    is_funny: Mapped[bool | None] = mapped_column(Boolean)
    is_specific: Mapped[bool | None] = mapped_column(Boolean)
    is_too_harsh: Mapped[bool | None] = mapped_column(Boolean)
    comment_ciphertext: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)


class RateEvent(Base):
    __tablename__ = "rate_events"
    __table_args__ = (Index("ix_rate_key_kind_time", "key_hash", "kind", "created_at"),)
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    key_hash: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(24))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class DeletionLedger(Base):
    """Remembers THAT a session was deleted (never what it held) so a backup restore can re-apply it."""
    __tablename__ = "deletion_ledger"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_ref: Mapped[str] = mapped_column(String(64), index=True)  # HMAC of the session id
    deleted_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AnalyticsEvent(Base):
    """Aggregate product metrics. Names only; never item text, roast text or classifications."""
    __tablename__ = "analytics_events"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(40), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
