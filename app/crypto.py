"""Application-level encryption at rest (PRD §15.3). Keys live outside the database."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json

from cryptography.fernet import Fernet, MultiFernet

from .config import settings


def _fernet() -> MultiFernet:
    keys = [k.strip() for k in settings.encryption_keys.split(",") if k.strip()]
    if not keys:  # dev/test only; validate_for_runtime() rejects this in prod
        keys = [base64.urlsafe_b64encode(hashlib.sha256(settings.app_secret.encode()).digest()).decode()]
    return MultiFernet([Fernet(k.encode()) for k in keys])


def encrypt_text(text: str) -> str:
    return _fernet().encrypt(text.encode()).decode()


def decrypt_text(token: str) -> str:
    return _fernet().decrypt(token.encode()).decode()


def encrypt_json(obj) -> str:
    return encrypt_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")))


def decrypt_json(token: str):
    return json.loads(decrypt_text(token))


def hmac_hex(*parts: str) -> str:
    return hmac.new(settings.app_secret.encode(), "|".join(parts).encode(), hashlib.sha256).hexdigest()


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()
