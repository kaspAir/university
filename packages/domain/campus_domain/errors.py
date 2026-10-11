"""Typed campus errors. Mirrors the unified error object of architecture chapter 17."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class ErrorCode(StrEnum):
    UNAUTHENTICATED = "UNAUTHENTICATED"
    FORBIDDEN = "FORBIDDEN"
    CSRF_FAILED = "CSRF_FAILED"
    NOT_FOUND = "NOT_FOUND"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    VERSION_CONFLICT = "VERSION_CONFLICT"
    IDEMPOTENCY_MISMATCH = "IDEMPOTENCY_MISMATCH"
    MANDATE_NOT_APPROVED = "MANDATE_NOT_APPROVED"
    POLICY_NOT_ACTIVE = "POLICY_NOT_ACTIVE"
    LIMITS_NOT_SET = "LIMITS_NOT_SET"
    CATALOG_NOT_ACTIVE = "CATALOG_NOT_ACTIVE"
    AGENT_NOT_REGISTERED = "AGENT_NOT_REGISTERED"
    QUALIFICATION_MISSING = "QUALIFICATION_MISSING"
    NO_ELIGIBLE_MODEL = "NO_ELIGIBLE_MODEL"
    NO_ELIGIBLE_SELECTOR = "NO_ELIGIBLE_SELECTOR"
    REVIEW_DIVERSITY_UNSATISFIED = "REVIEW_DIVERSITY_UNSATISFIED"
    PROPOSAL_INVALID = "PROPOSAL_INVALID"
    ROUTE_NOT_AUTHORIZED = "ROUTE_NOT_AUTHORIZED"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    PRICING_UNKNOWN = "PRICING_UNKNOWN"
    DATA_EXPORT_DENIED = "DATA_EXPORT_DENIED"
    ADAPTER_NOT_APPROVED = "ADAPTER_NOT_APPROVED"
    OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"
    INVALID_TRANSITION = "INVALID_TRANSITION"
    WORKSPACE_STOPPED = "WORKSPACE_STOPPED"


_HTTP_STATUS: dict[ErrorCode, int] = {
    ErrorCode.UNAUTHENTICATED: 401,
    ErrorCode.FORBIDDEN: 403,
    ErrorCode.CSRF_FAILED: 403,
    ErrorCode.DATA_EXPORT_DENIED: 403,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.VALIDATION_ERROR: 422,
}


@dataclass
class CampusError(Exception):
    """A blocked or failed action. Never swallowed, never turned into a silent fallback."""

    code: ErrorCode
    message: str
    blocked_action: str | None = None
    retryable: bool = False
    required_resolution: str | None = None
    policy_version: str | None = None
    details: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        super().__init__(f"{self.code.value}: {self.message}")

    @property
    def http_status(self) -> int:
        return _HTTP_STATUS.get(self.code, 409)

    def to_object(self, trace_id: str) -> dict:
        """The wire format of chapter 17. No foreign object titles, no secrets."""
        return {
            "code": self.code.value,
            "message": self.message,
            "trace_id": trace_id,
            "blocked_action": self.blocked_action,
            "retryable": self.retryable,
            "required_resolution": self.required_resolution,
            "policy_version": self.policy_version,
        }
