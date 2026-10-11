"""Argon2 password hashing and server-side sessions (ADR-0002)."""
from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from sqlalchemy.orm import Session

from campus_infra.ids import utcnow
from campus_infra.models import Session_, User

_hasher = PasswordHasher()
SESSION_COOKIE = "campus_session"
CSRF_HEADER = "X-CSRF-Token"
MIN_PASSWORD_LENGTH = 12


def hash_password(password: str) -> str:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Passwort muss mindestens {MIN_PASSWORD_LENGTH} Zeichen haben")
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def open_session(db: Session, user: User, ttl_hours: int) -> tuple[str, str]:
    """Create a session row; return (cookie token, csrf token). Only the token hash is stored."""
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    db.add(
        Session_(
            id=_digest(token),
            user_id=user.id,
            csrf_token=csrf,
            expires_at=utcnow() + timedelta(hours=ttl_hours),
        )
    )
    db.flush()
    return token, csrf


def load_session(db: Session, token: str | None) -> Session_ | None:
    if not token:
        return None
    row = db.get(Session_, _digest(token))
    if row is None or row.revoked_at is not None or row.expires_at <= utcnow():
        return None
    return row


def revoke_session(db: Session, token: str | None) -> None:
    row = load_session(db, token)
    if row is not None:
        row.revoked_at = utcnow()
        db.flush()
