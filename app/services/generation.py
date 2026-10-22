"""Generation pipeline (PRD §9.2). One structured call, bounded repair/safety retries; not agentic.

Selected input -> validation -> single call -> schema -> evidence -> safety -> persistence.

Execution is at-least-once. Ownership and deletion are re-checked before every external call and
before the final write, and no database transaction is open while the provider call runs.
"""
from __future__ import annotations

import json
import logging
import os
import re
import traceback
import time
from datetime import timedelta

from pydantic import ValidationError
from sqlalchemy import select, update

from .. import providers
from ..config import settings
from ..crypto import decrypt_text, encrypt_json
from ..db import hours_from_now, session_scope, utcnow
from ..models import GenerationJob, Report, Sample, SampleItem, SessionModel
from ..providers.base import ProviderMisconfigured, ProviderTimeout, ProviderUnavailable
from ..languages import disclaimer_for
from ..schemas import ReportOut, validate_evidence
from . import analytics, items as it, prompt, safety
from .cost import estimate_cost
from .jobs import ACTIVE, IN_FLIGHT, Heartbeat, backoff_seconds, check_transition

log = logging.getLogger("rmf.worker")


class Aborted(Exception):
    """The job was cancelled, deleted, expired or its lease was lost. Stop quietly; write nothing."""


class JobFailed(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _words(texts: list[str]) -> set[str]:
    return {w.lower() for t in texts for w in re.findall(r"[\w'-]+", t)}


def _guard(db, job_id: str, owner: str, *, lock: bool = False) -> GenerationJob:
    """Raise Aborted unless this worker still legitimately owns a live job in a live session."""
    q = select(GenerationJob).where(GenerationJob.id == job_id)
    if lock:
        q = q.with_for_update()
    job = db.execute(q).scalar_one_or_none()
    if job is None or job.status not in IN_FLIGHT or job.lease_owner != owner:
        raise Aborted("job gone, finished, cancelled or lease lost")
    now = utcnow()
    sess = db.get(SessionModel, job.session_id)
    if sess is None or sess.deleted_at is not None or sess.expires_at <= now:
        raise Aborted("session deleted or expired")
    sample = db.get(Sample, job.sample_id)
    if sample is None or sample.expires_at <= now:
        raise Aborted("sample gone or expired")
    return job


def _set_status(job_id: str, owner: str, new: str) -> None:
    with session_scope() as db:
        job = _guard(db, job_id, owner, lock=True)
        if job.status != new:
            check_transition(job.status, new)
            job.status = new


def _load(job_id: str, owner: str) -> tuple[dict, list[tuple[str, str, str]]]:
    """Read the approved sample into memory (short transaction), re-verifying it is unchanged."""
    with session_scope() as db:
        job = _guard(db, job_id, owner)
        sample = db.get(Sample, job.sample_id)
        if sample.status != "approved" or sample.approved_sample_hash != job.approved_sample_hash:
            raise JobFailed("SAMPLE_CHANGED")
        rows = list(db.scalars(select(SampleItem).where(SampleItem.sample_id == sample.id, SampleItem.is_selected.is_(True))
                               .order_by(SampleItem.selection_order)))
        entries = [(it.alias_for(r.selection_order), r.item_type, decrypt_text(r.text_ciphertext)) for r in rows]
        if it.sample_hash(entries) != job.approved_sample_hash:
            raise JobFailed("SAMPLE_CHANGED")
        if sample.eligible_item_count < settings.min_items or len(entries) < settings.min_items:
            raise JobFailed("INSUFFICIENT_ELIGIBLE_ITEMS")
        meta = {"intensity": job.intensity, "language": job.language, "kind": job.kind}
        return meta, entries


def _persist(job_id: str, owner: str, report: ReportOut, evidence: dict, usage: dict, provider, language: str = "en") -> str:
    """Final write. The row lock plus liveness re-check means a deleted/cancelled job cannot gain a report."""
    with session_scope() as db:
        job = _guard(db, job_id, owner, lock=True)
        check_transition(job.status, "ready")
        r = Report(session_id=job.session_id, sample_id=job.sample_id, generation_job_id=job.id,
                   is_regeneration=job.kind == "regenerate", intensity=job.intensity,
                   report_ciphertext=encrypt_json({"report": report.model_dump(), "evidence": evidence, "language": language}),
                   schema_version=report.schema_version, prompt_version=prompt.PROMPT_VERSION,
                   provider=provider.name, model_identifier=provider.model,
                   input_tokens=usage["in"], output_tokens=usage["out"],
                   estimated_cost=estimate_cost(usage["in"], usage["out"]), pricing_version=settings.pricing_version,
                   expires_at=hours_from_now(settings.report_ttl_hours))
        db.add(r)
        db.flush()
        job.status, job.report_id, job.completed_at = "ready", r.id, utcnow()
        job.lease_owner = job.lease_expires_at = None
        analytics.track(db, "generation_completed")
        return r.id


def _fail(job_id: str, owner: str, code: str) -> None:
    with session_scope() as db:
        try:
            job = _guard(db, job_id, owner, lock=True)
        except Aborted:
            return
        check_transition(job.status, "failed")
        job.status, job.error_code, job.completed_at = "failed", code, utcnow()
        job.lease_owner = job.lease_expires_at = None
        analytics.track(db, "generation_failed")


def _retry_or_fail(job_id: str, owner: str, code: str, *, ambiguous: bool = False) -> None:
    with session_scope() as db:
        try:
            job = _guard(db, job_id, owner, lock=True)
        except Aborted:
            return
        if ambiguous:
            job.possible_duplicate_cost = True  # a timed-out call may still have been billed
        if job.attempt_count < settings.max_attempts:
            check_transition(job.status, "queued")
            job.status, job.error_code = "queued", None
            job.run_after = utcnow() + timedelta(seconds=backoff_seconds(job.attempt_count))
            job.lease_owner = job.lease_expires_at = None
        else:
            check_transition(job.status, "failed")
            job.status, job.error_code, job.completed_at = "failed", code, utcnow()
            job.lease_owner = job.lease_expires_at = None
            analytics.track(db, "generation_failed")


def _call_pipeline(job_id: str, owner: str, provider, deadline: float) -> None:
    meta, entries = _load(job_id, owner)
    aliases = {a for a, _, _ in entries}
    texts = [x for _, _, x in entries]
    channels = [x for _, t, x in entries if t == "channel_name"]
    evidence = {a: {"item_type": t, "text": x} for a, t, x in entries}
    user_msg = prompt.build_user_message([{"id": a, "type": t, "text": x} for a, t, x in entries],
                                         meta["intensity"], meta["language"])
    _set_status(job_id, owner, "generating")

    need = prompt.min_evidence(meta["intensity"], len(entries))
    usage = {"in": 0, "out": 0}
    extra, repaired, safer = "", False, False
    while True:
        remaining = deadline - time.monotonic()
        if remaining < 3:
            raise JobFailed("GENERATION_TIMEOUT")
        with session_scope() as db:  # liveness re-check immediately before each external call
            _guard(db, job_id, owner)
        res = provider.generate(prompt.build_system(extra), user_msg, timeout=min(settings.provider_timeout_s, remaining))
        usage["in"] += res.input_tokens
        usage["out"] += res.output_tokens
        _set_status(job_id, owner, "checking")

        problem, detail = None, ""
        try:
            report = ReportOut.model_validate(json.loads(res.text))
            unknown = validate_evidence(report, aliases)
            if unknown:
                problem = "evidence_item_ids"
            elif len(report.evidence_ids()) < need:
                problem = "coverage"
        except (ValueError, ValidationError) as e:  # JSONDecodeError is a ValueError
            problem = ",".join(sorted({".".join(map(str, err["loc"][:2])) for err in e.errors()})) \
                if isinstance(e, ValidationError) else "json"
            if isinstance(e, ValidationError):  # locations and error types only, never model text
                detail = "; ".join(f'{".".join(map(str, er["loc"]))}:{er["type"]}' for er in e.errors()[:5])
        if problem:
            log.warning("model output rejected: %s [%s] (repair_attempted=%s)", problem, detail, repaired,
                        extra={"job_id": job_id})
            if repaired:
                raise JobFailed("INVALID_MODEL_OUTPUT")
            repaired = True
            extra = (prompt.COVERAGE_SUFFIX.format(n=need) if problem == "coverage"
                     else prompt.REPAIR_SUFFIX.format(locs=problem))
            _set_status(job_id, owner, "generating")
            continue

        report = report.model_copy(update={"disclaimer": disclaimer_for(meta["language"])})  # our wording, always
        violations = safety.evaluate(report, allowed_terms=_words(texts), channel_names=channels)
        # The rule-based checks are English-only, so for any other language a model moderation pass is mandatory.
        if not violations and (settings.safety_judge_enabled or meta["language"] != "en"):
            if not provider.moderate("\n".join(report.narrative_text()), timeout=min(20, max(3, deadline - time.monotonic()))):
                violations = ["judge_rejected"]
        if violations:
            log.warning("output flagged by safety: %s (retry_attempted=%s)", ", ".join(violations), safer,
                        extra={"job_id": job_id})
            if safer:
                raise JobFailed("UNSAFE_OUTPUT")  # rejected content is discarded, never shown or logged
            safer, extra = True, prompt.SAFER_SUFFIX.format(cats=", ".join(violations))
            _set_status(job_id, owner, "generating")
            continue
        break

    # Only evidence the report actually cites is stored, so "Why this joke?" shows just those items.
    cited = {a: evidence[a] for a in report.evidence_ids()}
    _persist(job_id, owner, report, cited, usage, provider, meta["language"])


def run_job(job_id: str, owner: str) -> None:
    """Execute one claimed job. Never raises: every outcome is recorded on the job row."""
    deadline = time.monotonic() + settings.job_timeout_s
    try:
        provider = providers.get_provider()
        if provider.name in settings.csv(settings.disabled_providers) or \
                prompt.PROMPT_VERSION in settings.csv(settings.disabled_prompt_versions):
            raise JobFailed("FEATURE_DISABLED")
        with Heartbeat(job_id, owner):
            _call_pipeline(job_id, owner, provider, deadline)
    except Aborted as e:
        log.info("job aborted", extra={"job_id": job_id, "reason": str(e)})
    except JobFailed as e:
        _fail(job_id, owner, e.code)
    except ProviderMisconfigured as e:  # our own message: safe to log; retrying cannot fix a setup problem
        log.error("provider misconfigured: %s", e, extra={"job_id": job_id})
        _fail(job_id, owner, "PROVIDER_MISCONFIGURED")
    except ProviderTimeout:
        _retry_or_fail(job_id, owner, "GENERATION_TIMEOUT", ambiguous=True)
    except ProviderUnavailable:
        _retry_or_fail(job_id, owner, "PROVIDER_UNAVAILABLE")
    except Exception as e:  # noqa: BLE001 - last resort. Log the type only: messages/tracebacks may hold content.
        # Type, the missing module name (ImportError only) and the failing location; never the message, which may hold content.
        tb = traceback.extract_tb(e.__traceback__)[-1] if e.__traceback__ else None
        where = f" at {os.path.basename(tb.filename)}:{tb.lineno} in {tb.name}" if tb else ""
        missing = f" (missing module '{e.name}')" if isinstance(e, ImportError) and e.name else ""
        log.error("job crashed: %s%s%s", type(e).__name__, missing, where, extra={"job_id": job_id})
        _fail(job_id, owner, "INTERNAL_ERROR")
