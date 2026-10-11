"""Status axes and lifecycle enums. The three axes of Art. 13 are separate types on purpose."""
from __future__ import annotations

from enum import StrEnum


class WorkState(StrEnum):
    """Axis 1: project work status (architecture chapter 5)."""

    PLANNED = "planned"
    RUNNING = "running"
    WAITING = "waiting"
    FINISHED = "finished"
    ABORTED = "aborted"


class EpistemicStatus(StrEnum):
    """Axis 2: status of a claim, always relative to an assessment version."""

    HYPOTHESIS = "hypothesis"
    OBSERVATION = "observation"
    DERIVATION = "derivation"
    SUPPORTED = "supported"
    CONTESTED = "contested"
    REFUTED = "refuted"
    UNRESOLVED = "unresolved"


class ReleaseState(StrEnum):
    """Axis 3: release status of an artifact version."""

    DRAFT = "draft"
    IN_REVIEW = "in_review"
    RELEASED = "released"
    BLOCKED = "blocked"
    SUPERSEDED = "superseded"


class MandateState(StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class ActivationState(StrEnum):
    """For policies and catalog revisions: a draft is never used at runtime."""

    DRAFT = "draft"
    ACTIVE = "active"
    RETIRED = "retired"


class OperatingStatus(StrEnum):
    PLANNED = "planned"
    IN_REVIEW = "in_review"
    ACTIVE = "active"
    DORMANT = "dormant"
    BLOCKED = "blocked"
    ARCHIVED = "archived"


class QualificationStatus(StrEnum):
    NOT_EVALUATED = "not_evaluated"
    EXPERIMENTAL_LIMITED = "experimental_limited"
    TASK_QUALIFIED = "task_qualified"


class RunState(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    FINISHED = "finished"
    CANCELLED = "cancelled"
    FAILED = "failed"
    OUTCOME_UNKNOWN = "outcome_unknown"


class RunEndReason(StrEnum):
    """Distinct end reasons (chapter 16). A reached round limit is never a research result."""

    RESULT = "result"
    STOP = "stop"
    SUSPEND = "suspend"
    DEADLINE = "deadline"
    PROVIDER_ERROR = "provider_error"
    BUDGET_EXHAUSTED = "budget_exhausted"
    OUTCOME_UNKNOWN = "outcome_unknown"
    ROUND_LIMIT = "round_limit"


class ReservationState(StrEnum):
    RESERVED = "reserved"
    SETTLED = "settled"
    RELEASED = "released"
    OPEN_UNKNOWN = "open_unknown"


class InvocationState(StrEnum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    OUTCOME_UNKNOWN = "outcome_unknown"


class DecisionOption(StrEnum):
    """The four real options of Art. 11. None is preselected anywhere."""

    ACCEPT = "accept"
    ACCEPT_LIMITED = "accept_limited"
    REVISE = "revise"
    REJECT = "reject"


class ActorType(StrEnum):
    USER = "user"
    SERVICE_PRINCIPAL = "service_principal"
    SYSTEM = "system"
