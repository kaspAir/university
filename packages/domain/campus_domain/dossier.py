"""Dossier and approval rules (chapters 6, 15, 22; I05, T08)."""
from __future__ import annotations

import hashlib
import json

from .errors import CampusError, ErrorCode


def canonical_json(document: dict) -> str:
    return json.dumps(document, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def document_hash(document: dict) -> str:
    return hashlib.sha256(canonical_json(document).encode("utf-8")).hexdigest()


def check_approval_binding(expected_hash: str, artifact_hash: str) -> None:
    """A decision binds the exact version (hash). A changed content is a new approval question."""
    if expected_hash != artifact_hash:
        raise CampusError(ErrorCode.VERSION_CONFLICT,
                          "Die Freigabe bezieht sich auf eine andere Fassung (Hash weicht ab); geänderter Inhalt braucht eine neue Freigabe (I05)",
                          blocked_action="artifact.approve")


def release_allowed(*, approval_hash: str | None, artifact_hash: str, approval_option: str | None) -> None:
    """External release needs a result approval of exactly this version (T08)."""
    if approval_hash is None or approval_option not in {"accept", "accept_limited"}:
        raise CampusError(ErrorCode.FORBIDDEN, "Keine fachliche Ergebnisfreigabe für diese Fassung", blocked_action="publication.release",
                          required_resolution="Ergebnisfreigabe (artifact.approve) für genau diese Fassung einholen")
    if approval_hash != artifact_hash:
        raise CampusError(ErrorCode.VERSION_CONFLICT, "Die vorhandene Freigabe gilt für eine frühere Fassung; neue Fassung, neue Freigabe (T08)",
                          blocked_action="publication.release")
