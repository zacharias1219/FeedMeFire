"""Operational metrics (PRD §21). Counts and timings only; never content."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import utcnow
from ..models import AnalyticsEvent, Feedback, GenerationJob, Report, Sample, SessionModel


def _pct(values: list[float], p: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    return round(values[min(len(values) - 1, int(p * len(values)))], 2)


def collect(db: Session) -> dict:
    now = utcnow()
    since = now - timedelta(hours=24)
    jobs = list(db.scalars(select(GenerationJob).where(GenerationJob.created_at >= since)))
    by_status: dict[str, int] = {}
    for j in jobs:
        by_status[j.status] = by_status.get(j.status, 0) + 1
    done = [j for j in jobs if j.status in ("ready", "failed")]
    ok = [j for j in jobs if j.status == "ready"]
    queued = list(db.scalars(select(GenerationJob.created_at).where(GenerationJob.status == "queued")))
    latencies = [(j.completed_at - j.created_at).total_seconds() for j in ok if j.completed_at]
    errors: dict[str, int] = {}
    for j in jobs:
        if j.error_code:
            errors[j.error_code] = errors.get(j.error_code, 0) + 1
    reports = list(db.scalars(select(Report).where(Report.created_at >= since)))
    expired_lag = [(now - t).total_seconds() for t in db.scalars(select(Sample.expires_at).where(Sample.expires_at <= now))]
    fb = list(db.scalars(select(Feedback).where(Feedback.created_at >= since)))
    events = dict(db.execute(select(AnalyticsEvent.name, func.count()).where(AnalyticsEvent.created_at >= since)
                             .group_by(AnalyticsEvent.name)).all())

    def rate(pred):
        answered = [f for f in fb if pred(f) is not None]
        return None if not answered else {"n": len(answered), "share": round(sum(1 for f in answered if pred(f)) / len(answered), 3)}

    return {
        "queue_depth": len(queued),
        "oldest_queued_age_s": round(max(((now - t).total_seconds() for t in queued), default=0), 1),
        "jobs_24h": by_status,
        "generation_success_rate": round(len(ok) / len(done), 3) if done else None,
        "latency_s": {"p50": _pct(latencies, .5), "p95": _pct(latencies, .95)},
        "retry_rate": round(sum(1 for j in jobs if j.attempt_count > 1) / len(jobs), 3) if jobs else None,
        "possible_duplicate_cost_jobs": sum(1 for j in jobs if j.possible_duplicate_cost),
        "error_codes_24h": errors,
        "tokens_24h": {"input": sum(r.input_tokens for r in reports), "output": sum(r.output_tokens for r in reports)},
        "estimated_cost_24h": round(sum(r.estimated_cost for r in reports), 4),
        "mean_cost_per_report": round(sum(r.estimated_cost for r in reports) / len(reports), 5) if reports else None,
        "cleanup_lag_s": round(max(expired_lag, default=0), 1),
        "active_sessions": db.scalar(select(func.count()).select_from(SessionModel).where(SessionModel.expires_at > now)),
        "feedback": {"funny": rate(lambda f: f.is_funny), "specific": rate(lambda f: f.is_specific),
                     "too_harsh": rate(lambda f: f.is_too_harsh)},
        "events_24h": events,
    }


def prometheus(m: dict) -> str:
    lines = [f"rmf_queue_depth {m['queue_depth']}", f"rmf_oldest_queued_age_seconds {m['oldest_queued_age_s']}",
             f"rmf_cleanup_lag_seconds {m['cleanup_lag_s']}", f"rmf_estimated_cost_24h {m['estimated_cost_24h']}"]
    if m["generation_success_rate"] is not None:
        lines.append(f"rmf_generation_success_rate {m['generation_success_rate']}")
    for k, v in m["jobs_24h"].items():
        lines.append(f'rmf_jobs_24h{{status="{k}"}} {v}')
    for k, v in m["error_codes_24h"].items():
        lines.append(f'rmf_job_errors_24h{{code="{k}"}} {v}')
    return "\n".join(lines) + "\n"
