from __future__ import annotations

import secrets
from datetime import UTC, datetime


def new_id(prefix: str) -> str:
    """Opaque, prefixed identifier (e.g. M-3f9a1c2d7e). Display IDs for the UI are a later concern."""
    return f"{prefix}-{secrets.token_hex(5)}"


def utcnow() -> datetime:
    return datetime.now(UTC)
