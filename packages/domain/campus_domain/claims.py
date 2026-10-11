"""Claims as the core object (Art. 13–14; chapter 14). Three axes stay separate; declared kind is not stored kind."""
from __future__ import annotations

from dataclasses import dataclass

from .statuses import EpistemicStatus

KINDS_REQUIRING_EVIDENCE = {"observation", "computation"}

_STATUS_FOR_KIND = {
    "observation": EpistemicStatus.OBSERVATION,
    "computation": EpistemicStatus.DERIVATION,
    "derivation": EpistemicStatus.DERIVATION,
    "hypothesis": EpistemicStatus.HYPOTHESIS,
    "legal_assessment": EpistemicStatus.UNRESOLVED,  # agent consensus never makes law (Art. 13)
    "normative_proposal": EpistemicStatus.HYPOTHESIS,
}


@dataclass(frozen=True)
class Classification:
    declared_kind: str
    stored_kind: str
    epistemic_status: EpistemicStatus
    flags: tuple[str, ...]


def classify(declared_kind: str, valid_evidence_count: int, invalid_evidence_count: int) -> Classification:
    """T04: a measurement or observation without a real evidence link is stored as a hypothesis and flagged, never as
    observed. Invalid evidence references (invented passage ids) are flagged as well."""
    flags: list[str] = []
    stored_kind = declared_kind
    if invalid_evidence_count:
        flags.append("INVALID_EVIDENCE_REF")
    if declared_kind in KINDS_REQUIRING_EVIDENCE and valid_evidence_count == 0:
        flags.append("EVIDENCE_MISSING")
        stored_kind = "hypothesis"
    status = _STATUS_FOR_KIND[stored_kind]
    return Classification(declared_kind, stored_kind, status, tuple(flags))
