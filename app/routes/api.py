"""Versioned JSON API (PRD §13). Every private route needs a session; every mutation needs CSRF."""
from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, Header, Request, Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import settings
from ..crypto import decrypt_json, decrypt_text, encrypt_text
from ..db import get_db, hours_from_now, utcnow
from ..errors import AppError, job_error
from ..models import Consent, Feedback, GenerationJob, Report, Sample, SessionModel
from ..schemas import (REQUIRED_CONSENTS, ConsentIn, EventIn, FeedbackIn, RegenerateIn, ReportCreate, SampleCreate,
                       SamplePatch, SessionCreate)
from ..services import analytics, ratelimit, retention, samples, sessions
from ..services.jobs import ACTIVE, STAGE_LABELS
from .deps import session_read, session_write, session_write_allow_expired

router = APIRouter(prefix="/api/v1")


# --- public config ------------------------------------------------------------------------------
@router.get("/config")
def public_config():
    return {
        "provider_name": settings.provider_display_name,
        "policy_version": settings.policy_version,
        "provider_disclosure_version": settings.provider_disclosure_version,
        "min_items": settings.min_items, "max_submitted_items": settings.max_submitted_items,
        "max_model_items": settings.max_model_items, "max_model_chars": settings.max_model_chars,
        "session_ttl_hours": settings.session_ttl_hours, "report_ttl_hours": settings.report_ttl_hours,
        "generations_per_session": settings.gen_per_session_24h,
        "features": {"generation": settings.generation_enabled, "regeneration": settings.regeneration_enabled},
        "support_contact": settings.support_contact,
        "required_consents": list(REQUIRED_CONSENTS),
    }


# --- session ------------------------------------------------------------------------------------
def _session_view(db: Session, s: SessionModel) -> dict:
    consents = list(db.scalars(select(Consent.consent_type).where(Consent.session_id == s.id)))
    sample_id = db.scalar(select(Sample.id).where(Sample.session_id == s.id, Sample.expires_at > utcnow())
                          .order_by(Sample.created_at.desc()).limit(1))
    report_id = db.scalar(select(Report.id).where(Report.session_id == s.id, Report.expires_at > utcnow())
                          .order_by(Report.created_at.desc()).limit(1))
    job_id = db.scalar(select(GenerationJob.id).where(GenerationJob.session_id == s.id, GenerationJob.status.in_(ACTIVE))
                       .order_by(GenerationJob.created_at.desc()).limit(1))
    return {"csrf_token": sessions.csrf_token(s), "expires_at": s.expires_at.isoformat() + "Z",
            "age_attested": s.age_attested_at is not None, "consents": consents,
            "required_consents": list(REQUIRED_CONSENTS), "sample_id": sample_id,
            "latest_report_id": report_id, "active_job_id": job_id}


@router.post("/session", status_code=201)
def create_session(body: SessionCreate, request: Request, response: Response, db: Session = Depends(get_db)):
    sessions.check_origin(request)
    if not body.age_attested:
        raise AppError("AGE_ATTESTATION_REQUIRED")
    s = sessions.create_session(db, response)
    db.commit()
    return _session_view(db, s)


@router.get("/session")
def read_session(s: SessionModel = Depends(session_read), db: Session = Depends(get_db)):
    return _session_view(db, s)


@router.delete("/session/data")
def delete_data(response: Response, s: SessionModel = Depends(session_write_allow_expired), db: Session = Depends(get_db)):
    """Never gated by a kill switch."""
    retention.delete_session_data(db, s.id)
    db.commit()
    sessions.clear_cookie(response)
    return {"status": "deleted",
            "message": "Your samples, reports, queued work and feedback were deleted and your session ended.",
            "note": "Images you already downloaded cannot be removed remotely. "
                    "Backups age out within 7 days, and a deletion ledger prevents deleted data from reappearing."}


# --- consents -----------------------------------------------------------------------------------
@router.post("/consents", status_code=201)
def record_consent(body: ConsentIn, s: SessionModel = Depends(session_write), db: Session = Depends(get_db)):
    existing = db.scalar(select(Consent).where(Consent.session_id == s.id, Consent.consent_type == body.consent_type))
    if existing is None:
        db.add(Consent(session_id=s.id, consent_type=body.consent_type, policy_version=settings.policy_version,
                       provider_disclosure_version=settings.provider_disclosure_version))
        db.commit()
    return {"consent_type": body.consent_type, "recorded": True}


# --- samples ------------------------------------------------------------------------------------
@router.post("/samples", status_code=201)
def create_sample(body: SampleCreate, s: SessionModel = Depends(session_write), db: Session = Depends(get_db)):
    sample, rejected, dupes = samples.create_sample(db, s, body.source, body.items)
    analytics.track(db, "sample_started")
    db.commit()
    return {**samples.serialize(db, sample), "rejected": rejected, "duplicates_skipped": dupes}


@router.get("/samples/{sample_id}")
def read_sample(sample_id: str, s: SessionModel = Depends(session_read), db: Session = Depends(get_db)):
    return samples.serialize(db, samples.get_owned_sample(db, s, sample_id))


@router.patch("/samples/{sample_id}")
def patch_sample(sample_id: str, body: SamplePatch, s: SessionModel = Depends(session_write),
                 db: Session = Depends(get_db)):
    sample = samples.get_owned_sample(db, s, sample_id)
    rejected, dupes = samples.patch_sample(db, sample, add=body.add, update=body.update, remove=body.remove,
                                           clear=body.clear)
    db.commit()
    return {**samples.serialize(db, sample), "rejected": rejected, "duplicates_skipped": dupes}


@router.post("/samples/{sample_id}/approve")
def approve_sample(sample_id: str, s: SessionModel = Depends(session_write), db: Session = Depends(get_db)):
    sample = samples.get_owned_sample(db, s, sample_id)
    samples.approve(db, sample)
    analytics.track(db, "sample_approved")
    db.commit()
    return samples.serialize(db, sample)


# --- generation ---------------------------------------------------------------------------------
def _job_view(j: GenerationJob) -> dict:
    return {"id": j.id, "status": j.status, "stage_label": STAGE_LABELS.get(j.status, j.status), "kind": j.kind,
            "intensity": j.intensity, "report_id": j.report_id, "attempt_count": j.attempt_count,
            "error": job_error(j.error_code), "created_at": j.created_at.isoformat() + "Z"}


def _get_owned_job(db: Session, s: SessionModel, job_id: str) -> GenerationJob:
    j = db.get(GenerationJob, job_id)
    if j is None or j.session_id != s.id:
        raise AppError("NOT_FOUND")
    return j


def _require_consents(db: Session, s: SessionModel) -> None:
    have = set(db.scalars(select(Consent.consent_type).where(Consent.session_id == s.id)))
    missing = [c for c in REQUIRED_CONSENTS if c not in have]
    if missing:
        raise AppError("CONSENT_REQUIRED", details={"missing": missing})


def _enforce_limits(db: Session, s: SessionModel, request: Request) -> str:
    """Cost and abuse guards, evaluated before any job is created. Returns the hashed IP key."""
    now = utcnow()
    day = now - timedelta(hours=24)
    per_session = db.scalar(select(func.count()).select_from(GenerationJob).where(
        GenerationJob.session_id == s.id, GenerationJob.created_at >= day))
    if per_session >= settings.gen_per_session_24h:
        raise AppError("RATE_LIMITED", f"You have used all {settings.gen_per_session_24h} roasts for this session. "
                                       "Regenerations count toward this limit.", retryable=False)
    if db.scalar(select(func.count()).select_from(GenerationJob).where(GenerationJob.session_id == s.id,
                                                                        GenerationJob.status.in_(ACTIVE))):
        raise AppError("GENERATION_IN_PROGRESS")
    ipk = ratelimit.ip_key(request)
    if ratelimit.count(db, ipk, "gen", 3600) >= settings.gen_per_ip_hour:
        raise AppError("RATE_LIMITED", "Too many roast requests from this network. Try again in an hour.")
    if db.scalar(select(func.count()).select_from(GenerationJob).where(GenerationJob.created_at >= day)) \
            >= settings.global_daily_generation_limit:
        raise AppError("RATE_LIMITED", "We have reached today's capacity. Please come back tomorrow.")
    if settings.global_daily_budget_usd > 0:
        spent = db.scalar(select(func.coalesce(func.sum(Report.estimated_cost), 0.0)).where(Report.created_at >= day))
        if spent >= settings.global_daily_budget_usd:
            raise AppError("RATE_LIMITED", "We have reached today's budget. Please come back tomorrow.")
    queued = db.scalar(select(func.count()).select_from(GenerationJob).where(GenerationJob.status == "queued"))
    if queued >= settings.queue_capacity:
        raise AppError("QUEUE_FULL")  # reject gracefully rather than accept work we cannot serve
    return ipk


def _enqueue(db: Session, s: SessionModel, sample: Sample, *, key: str, intensity: str, language: str, kind: str,
             source_report_id: str | None, ipk: str) -> GenerationJob:
    job = GenerationJob(session_id=s.id, sample_id=sample.id, kind=kind, source_report_id=source_report_id,
                        intensity=intensity, language=language, idempotency_key=key,
                        approved_sample_hash=sample.approved_sample_hash)
    db.add(job)
    ratelimit.record(db, ipk, "gen")
    analytics.track(db, "generation_requested")
    db.flush()
    return job


def _idempotent(db: Session, s: SessionModel, key: str) -> GenerationJob | None:
    return db.scalar(select(GenerationJob).where(GenerationJob.session_id == s.id, GenerationJob.idempotency_key == key))


@router.post("/reports", status_code=202)
def queue_report(body: ReportCreate, request: Request, idempotency_key: str | None = Header(None),
                 s: SessionModel = Depends(session_write), db: Session = Depends(get_db)):
    if not idempotency_key or not (8 <= len(idempotency_key) <= 64):
        raise AppError("IDEMPOTENCY_KEY_REQUIRED")
    existing = _idempotent(db, s, idempotency_key)
    if existing:  # a retried request returns the same job instead of spending twice
        return {"job_id": existing.id, "status": existing.status}
    if not settings.generation_enabled:
        raise AppError("FEATURE_DISABLED", "Roast generation is temporarily paused.")
    _require_consents(db, s)
    sample = samples.get_owned_sample(db, s, body.sample_id)
    if sample.eligible_item_count < settings.min_items:  # no provider call can follow
        n = samples.needed_more(sample)
        raise AppError("INSUFFICIENT_ELIGIBLE_ITEMS", f"Add at least {n} more eligible item{'s' if n != 1 else ''} to continue.")
    if sample.status != "approved":
        raise AppError("SAMPLE_NOT_APPROVED")
    if body.approved_sample_hash != sample.approved_sample_hash or samples.compute_hash(db, sample) != sample.approved_sample_hash:
        raise AppError("SAMPLE_CHANGED")
    ipk = _enforce_limits(db, s, request)
    try:
        job = _enqueue(db, s, sample, key=idempotency_key, intensity=body.intensity, language=body.language,
                       kind="create", source_report_id=None, ipk=ipk)
        db.commit()
    except IntegrityError:  # lost a race with an identical request
        db.rollback()
        job = _idempotent(db, s, idempotency_key)
        if job is None:
            raise
    return {"job_id": job.id, "status": job.status}


@router.get("/jobs/{job_id}")
def read_job(job_id: str, s: SessionModel = Depends(session_read), db: Session = Depends(get_db)):
    return _job_view(_get_owned_job(db, s, job_id))


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str, s: SessionModel = Depends(session_write), db: Session = Depends(get_db)):
    job = _get_owned_job(db, s, job_id)
    if job.status in ACTIVE:
        job.status, job.cancelled_at, job.lease_owner, job.lease_expires_at = "cancelled", utcnow(), None, None
        db.commit()  # an in-flight worker fails its next ownership check and writes nothing
    return _job_view(job)


# --- reports ------------------------------------------------------------------------------------
def _get_owned_report(db: Session, s: SessionModel, report_id: str) -> Report:
    r = db.get(Report, report_id)
    if r is None or r.session_id != s.id:
        raise AppError("NOT_FOUND")
    if r.expires_at <= utcnow():  # unreadable the moment it expires, even before physical cleanup
        raise AppError("REPORT_EXPIRED")
    return r


def _regen_state(db: Session, s: SessionModel, r: Report) -> dict:
    day = utcnow() - timedelta(hours=24)
    used = db.scalar(select(func.count()).select_from(GenerationJob).where(
        GenerationJob.session_id == s.id, GenerationJob.created_at >= day))
    taken = db.scalar(select(func.count()).select_from(GenerationJob).where(
        GenerationJob.source_report_id == r.id, GenerationJob.status.in_(ACTIVE + ("ready",)))) > 0
    sample = db.get(Sample, r.sample_id)
    job = db.get(GenerationJob, r.generation_job_id)
    intact = bool(sample and sample.status == "approved" and job and sample.approved_sample_hash == job.approved_sample_hash)
    can = (settings.regeneration_enabled and settings.generation_enabled and not r.is_regeneration and not taken and intact
           and used < settings.gen_per_session_24h)
    return {"can_regenerate": can, "regeneration_used": taken, "is_regeneration": r.is_regeneration,
            "attempts_remaining": max(0, settings.gen_per_session_24h - used)}


@router.get("/reports/{report_id}")
def read_report(report_id: str, s: SessionModel = Depends(session_read), db: Session = Depends(get_db)):
    r = _get_owned_report(db, s, report_id)
    payload = decrypt_json(r.report_ciphertext)
    analytics.track(db, "report_viewed")
    db.commit()
    return {"id": r.id, "sample_id": r.sample_id, "intensity": r.intensity, "report": payload["report"],
            "evidence": payload["evidence"], "schema_version": r.schema_version, "provider": r.provider,
            "created_at": r.created_at.isoformat() + "Z", "expires_at": r.expires_at.isoformat() + "Z",
            **_regen_state(db, s, r)}


@router.post("/reports/{report_id}/regenerate", status_code=202)
def regenerate(report_id: str, body: RegenerateIn, request: Request, idempotency_key: str | None = Header(None),
               s: SessionModel = Depends(session_write), db: Session = Depends(get_db)):
    if not idempotency_key or not (8 <= len(idempotency_key) <= 64):
        raise AppError("IDEMPOTENCY_KEY_REQUIRED")
    existing = _idempotent(db, s, idempotency_key)
    if existing:
        return {"job_id": existing.id, "status": existing.status}
    if not (settings.regeneration_enabled and settings.generation_enabled):
        raise AppError("FEATURE_DISABLED", "Regeneration is temporarily paused.")
    r = _get_owned_report(db, s, report_id)
    _require_consents(db, s)
    if body.intensity == r.intensity:
        raise AppError("VALIDATION_ERROR", "Choose a different intensity to regenerate.")
    state = _regen_state(db, s, r)
    if not state["can_regenerate"]:
        if state["attempts_remaining"] == 0:
            raise AppError("RATE_LIMITED", "No roasts left in this session.", retryable=False)
        raise AppError("REGENERATION_NOT_ALLOWED")
    sample = samples.get_owned_sample(db, s, r.sample_id)
    if samples.compute_hash(db, sample) != sample.approved_sample_hash:
        raise AppError("SAMPLE_CHANGED")
    ipk = _enforce_limits(db, s, request)
    try:
        job = _enqueue(db, s, sample, key=idempotency_key, intensity=body.intensity, language="en",
                       kind="regenerate", source_report_id=r.id, ipk=ipk)
        db.commit()
    except IntegrityError:
        db.rollback()
        job = _idempotent(db, s, idempotency_key)
        if job is None:
            raise
    return {"job_id": job.id, "status": job.status}


@router.post("/reports/{report_id}/feedback", status_code=201)
def submit_feedback(report_id: str, body: FeedbackIn, s: SessionModel = Depends(session_write),
                    db: Session = Depends(get_db)):
    r = _get_owned_report(db, s, report_id)
    db.add(Feedback(session_id=s.id, report_id=r.id, is_funny=body.is_funny, is_specific=body.is_specific,
                    is_too_harsh=body.is_too_harsh,
                    comment_ciphertext=encrypt_text(body.comment.strip()) if body.comment and body.comment.strip() else None,
                    expires_at=utcnow() + timedelta(days=settings.feedback_ttl_days)))
    analytics.track(db, "feedback_submitted")
    db.commit()
    return {"status": "received",
            "note": f"Feedback text is retained separately from your report for up to {settings.feedback_ttl_days} days "
                    "unless you delete your data sooner."}


@router.post("/events", status_code=204)
def client_event(body: EventIn, s: SessionModel = Depends(session_write), db: Session = Depends(get_db)):
    analytics.track(db, body.name)
    db.commit()
    return Response(status_code=204)
