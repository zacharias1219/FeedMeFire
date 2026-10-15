"""Abuse and cost protection (PRD §15.4). DB-backed so limits hold across web replicas."""
from __future__ import annotations

from datetime import timedelta

from fastapi import Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..crypto import hmac_hex
from ..db import utcnow
from ..models import RateEvent


def client_ip(request: Request) -> str:
    if settings.trust_proxy:
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def ip_key(request: Request) -> str:
    """Salted hash: raw IP addresses are never stored."""
    return hmac_hex("ip", client_ip(request))


def count(db: Session, key: str, kind: str, window_seconds: int) -> int:
    since = utcnow() - timedelta(seconds=window_seconds)
    return db.scalar(select(func.count()).select_from(RateEvent).where(
        RateEvent.key_hash == key, RateEvent.kind == kind, RateEvent.created_at >= since)) or 0


def record(db: Session, key: str, kind: str) -> None:
    db.add(RateEvent(key_hash=key, kind=kind))
