"""Anonymous, server-validated sessions (PRD §7.2, §15.1)."""
from __future__ import annotations

import hmac
import secrets
from datetime import timedelta

from fastapi import Request, Response
from sqlalchemy.orm import Session

from ..config import settings
from ..crypto import hmac_hex, sha256_hex
from ..db import hours_from_now, utcnow
from ..errors import AppError
from ..models import SessionModel

COOKIE = "rmf_session"
ROTATE_AFTER = timedelta(minutes=30)
ROTATE_GRACE = timedelta(seconds=60)  # parallel in-flight requests may still carry the old secret


def _set_cookie(response: Response, sid: str, secret: str, expires_at) -> None:
    max_age = max(1, int((expires_at - utcnow()).total_seconds()))
    response.set_cookie(COOKIE, f"{sid}.{secret}", max_age=max_age, httponly=True,
                        secure=settings.secure_cookie, samesite="strict", path="/")


def clear_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE, path="/", httponly=True, secure=settings.secure_cookie, samesite="strict")


def create_session(db: Session, response: Response) -> SessionModel:
    secret = secrets.token_urlsafe(32)
    s = SessionModel(session_secret_hash=sha256_hex(secret), expires_at=hours_from_now(settings.session_ttl_hours),
                     age_attested_at=utcnow())
    db.add(s)
    db.flush()
    _set_cookie(response, s.id, secret, s.expires_at)
    return s


def csrf_token(s: SessionModel) -> str:
    return hmac_hex("csrf", s.id)


def authenticate(request: Request, response: Response, db: Session, *, allow_expired: bool = False) -> SessionModel:
    raw = request.cookies.get(COOKIE, "")
    sid, _, secret = raw.partition(".")
    if not sid or not secret:
        raise AppError("SESSION_EXPIRED")
    s = db.get(SessionModel, sid)
    if s is None:
        raise AppError("SESSION_EXPIRED")
    digest = sha256_hex(secret)
    ok = hmac.compare_digest(digest, s.session_secret_hash)
    if not ok and s.prev_secret_hash and utcnow() - s.rotated_at < ROTATE_GRACE:
        ok = hmac.compare_digest(digest, s.prev_secret_hash)
    if not ok:
        raise AppError("SESSION_EXPIRED")
    if s.deleted_at is not None:
        raise AppError("DATA_DELETED")
    if s.expires_at <= utcnow() and not allow_expired:
        raise AppError("SESSION_EXPIRED")
    if utcnow() - s.rotated_at > ROTATE_AFTER and s.expires_at > utcnow():
        new = secrets.token_urlsafe(32)
        s.prev_secret_hash, s.session_secret_hash, s.rotated_at = s.session_secret_hash, sha256_hex(new), utcnow()
        _set_cookie(response, s.id, new, s.expires_at)
    return s


def check_csrf(request: Request, s: SessionModel) -> None:
    token = request.headers.get("x-csrf-token", "")
    if not hmac.compare_digest(token, csrf_token(s)):
        raise AppError("CSRF_FAILED")
    check_origin(request)


def check_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if origin:
        host = request.headers.get("host", "")
        allowed = {f"http://{host}", f"https://{host}"} | settings.csv(settings.allowed_origins)
        if settings.public_base_url:
            allowed.add(settings.public_base_url.rstrip("/"))
        if origin not in allowed:
            raise AppError("CSRF_FAILED")
    elif request.headers.get("sec-fetch-site") == "cross-site":
        raise AppError("CSRF_FAILED")
