"""SQLAlchemy ORM for S0. Versioned objects are append-only at application level (ADR-0006)."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from .ids import utcnow


class Base(DeclarativeBase):
    pass


def _ts() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class Workspace(Base):
    __tablename__ = "workspaces"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    stopped: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    stop_reason: Mapped[str | None] = mapped_column(Text)
    capability_level: Mapped[str] = mapped_column(String(40), nullable=False, default="development_mock")
    created_at: Mapped[datetime] = _ts()


class User(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("workspace_id", "username"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    username: Mapped[str] = mapped_column(String(120), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = _ts()


class Session_(Base):
    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(String(128), primary_key=True)  # sha256 of the cookie token
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    csrf_token: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = _ts()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Grant(Base):
    __tablename__ = "grants"
    __table_args__ = (UniqueConstraint("workspace_id", "user_id", "role"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    role: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = _ts()


class Institute(Base):
    __tablename__ = "institutes"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    remit: Mapped[str] = mapped_column(Text, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class MandateVersion(Base):
    __tablename__ = "mandate_versions"
    __table_args__ = (UniqueConstraint("mandate_id", "version"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    mandate_id: Mapped[str] = mapped_column(String(40), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    document: Mapped[dict] = mapped_column(JSONB, nullable=False)  # question space, goal, method frame, ...
    document_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    budget_limit_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    created_by: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = _ts()


class HumanDecision(Base):
    __tablename__ = "human_decisions"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    decision_type: Mapped[str] = mapped_column(String(40), nullable=False)
    object_type: Mapped[str] = mapped_column(String(40), nullable=False)
    object_id: Mapped[str] = mapped_column(String(40), nullable=False)
    object_version: Mapped[int] = mapped_column(Integer, nullable=False)
    object_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    option: Mapped[str] = mapped_column(String(20), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    alternatives_presented: Mapped[list] = mapped_column(JSONB, nullable=False)
    remaining_objections: Mapped[list] = mapped_column(JSONB, nullable=False)
    consequences: Mapped[str] = mapped_column(Text, nullable=False)
    conditions: Mapped[list] = mapped_column(JSONB, nullable=False)
    decided_by: Mapped[str] = mapped_column(String(40), nullable=False)
    decided_at: Mapped[datetime] = _ts()


class PolicyVersion(Base):
    __tablename__ = "policy_versions"
    __table_args__ = (UniqueConstraint("workspace_id", "revision"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    revision: Mapped[str] = mapped_column(String(120), nullable=False)
    document: Mapped[dict] = mapped_column(JSONB, nullable=False)
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    created_by: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = _ts()
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    activated_by: Mapped[str | None] = mapped_column(String(40))


class ModelCatalogRevision(Base):
    __tablename__ = "model_catalog_revisions"
    __table_args__ = (UniqueConstraint("workspace_id", "revision"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    revision: Mapped[str] = mapped_column(String(120), nullable=False)
    document: Mapped[dict] = mapped_column(JSONB, nullable=False)
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    created_by: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = _ts()
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    activated_by: Mapped[str | None] = mapped_column(String(40))


class PricingVersion(Base):
    __tablename__ = "pricing_versions"
    __table_args__ = (UniqueConstraint("workspace_id", "pricing_ref"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    pricing_ref: Mapped[str] = mapped_column(String(120), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    input_per_million_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    output_per_million_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_note: Mapped[str] = mapped_column(Text, nullable=False)  # where the price comes from, dated
    created_by: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = _ts()


class BudgetAccount(Base):
    __tablename__ = "budget_accounts"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("budget_accounts.id"))
    mandate_version_id: Mapped[str | None] = mapped_column(ForeignKey("mandate_versions.id"))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    limit_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    protected_kind: Mapped[str | None] = mapped_column(String(40))  # foundational | replication (Art. 8)
    depth: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = _ts()


class Reservation(Base):
    __tablename__ = "reservations"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    account_id: Mapped[str] = mapped_column(ForeignKey("budget_accounts.id"), nullable=False)
    task_id: Mapped[str | None] = mapped_column(String(40))
    routing_decision_id: Mapped[str | None] = mapped_column(String(40))  # the route this reservation is bound to (S1.1)
    purpose: Mapped[str] = mapped_column(String(20), nullable=False)  # generation | review | selector
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="reserved")
    settled_minor: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = _ts()


Index("ix_reservations_account_state", Reservation.account_id, Reservation.state)


class CostEntry(Base):
    __tablename__ = "cost_entries"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    account_id: Mapped[str] = mapped_column(ForeignKey("budget_accounts.id"), nullable=False)
    reservation_id: Mapped[str | None] = mapped_column(ForeignKey("reservations.id"))
    invocation_id: Mapped[str | None] = mapped_column(String(80))
    amount_minor: Mapped[int | None] = mapped_column(BigInteger)  # NULL = open / unknown, never 0 (A14)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    provider_currency: Mapped[str | None] = mapped_column(String(3))
    fx_rate: Mapped[str | None] = mapped_column(String(40))  # decimal text, recorded not assumed
    state: Mapped[str] = mapped_column(String(20), nullable=False)  # open | settled
    created_at: Mapped[datetime] = _ts()


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    mandate_version_id: Mapped[str] = mapped_column(ForeignKey("mandate_versions.id"), nullable=False)
    institute_id: Mapped[str] = mapped_column(ForeignKey("institutes.id"), nullable=False)
    budget_account_id: Mapped[str] = mapped_column(ForeignKey("budget_accounts.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    work_state: Mapped[str] = mapped_column(String(20), nullable=False, default="planned")
    created_at: Mapped[datetime] = _ts()


class Task(Base):
    __tablename__ = "tasks"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False)
    parent_task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"))
    budget_account_id: Mapped[str] = mapped_column(ForeignKey("budget_accounts.id"), nullable=False)
    task_need: Mapped[dict] = mapped_column(JSONB, nullable=False)
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="planned")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    hold_reason: Mapped[str | None] = mapped_column(Text)  # e.g. unresolved protected refusal (S1.1): blocks every model call
    hold_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _ts()


class AgentProfileVersion(Base):
    __tablename__ = "agent_profile_versions"
    __table_args__ = (UniqueConstraint("workspace_id", "profile_id", "version"), UniqueConstraint("workspace_id", "ref"))
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    ref: Mapped[str] = mapped_column(String(120), nullable=False)  # "<profile_id>@<version>", the contract reference
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    profile_id: Mapped[str] = mapped_column(String(80), nullable=False)
    version: Mapped[str] = mapped_column(String(40), nullable=False)
    document: Mapped[dict] = mapped_column(JSONB, nullable=False)
    operating_status: Mapped[str] = mapped_column(String(20), nullable=False)
    qualification_status: Mapped[str] = mapped_column(String(30), nullable=False)
    qualified_task_classes: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)  # scope of automatic activation (S3)
    created_by: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = _ts()


class SkillVersion(Base):
    __tablename__ = "skill_versions"
    __table_args__ = (UniqueConstraint("workspace_id", "skill_id", "version"), UniqueConstraint("workspace_id", "ref"))
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    ref: Mapped[str] = mapped_column(String(120), nullable=False)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    skill_id: Mapped[str] = mapped_column(String(80), nullable=False)
    version: Mapped[str] = mapped_column(String(40), nullable=False)
    document: Mapped[dict] = mapped_column(JSONB, nullable=False)
    operating_status: Mapped[str] = mapped_column(String(20), nullable=False)
    instructions: Mapped[str | None] = mapped_column(Text)  # package content bound by package_sha256 (S3)
    created_by: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = _ts()


class RouteProposalRecord(Base):
    """Every proposal is kept, including rejected ones, so the decision note survives (A02)."""

    __tablename__ = "route_proposals"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), nullable=False)
    proposal: Mapped[dict] = mapped_column(JSONB, nullable=False)
    assessments: Mapped[list] = mapped_column(JSONB, nullable=False)
    outcome: Mapped[str] = mapped_column(String(20), nullable=False)  # authorized | rejected
    error_code: Mapped[str | None] = mapped_column(String(60))
    routing_decision_id: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = _ts()


class RoutingDecisionRecord(Base):
    __tablename__ = "routing_decisions"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), nullable=False)
    document: Mapped[dict] = mapped_column(JSONB, nullable=False)
    state: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = _ts()


class RunAttempt(Base):
    __tablename__ = "run_attempts"
    __table_args__ = (UniqueConstraint("task_id", "attempt_no"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), nullable=False)
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    routing_decision_id: Mapped[str] = mapped_column(ForeignKey("routing_decisions.id"), nullable=False)
    agent_profile_version_id: Mapped[str] = mapped_column(String(80), nullable=False)
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="created")
    end_reason: Mapped[str | None] = mapped_column(String(30))
    started_at: Mapped[datetime] = _ts()
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LLMInvocation(Base):
    __tablename__ = "llm_invocations"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)  # idempotent invocation id
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    deployment_id: Mapped[str] = mapped_column(String(120), nullable=False)
    routing_decision_id: Mapped[str] = mapped_column(String(40), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(20), nullable=False)
    effective_model_id: Mapped[str | None] = mapped_column(String(200))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _ts()
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    occurred_at: Mapped[datetime] = _ts()
    actor_type: Mapped[str] = mapped_column(String(20), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(80), nullable=False)
    command: Mapped[str] = mapped_column(String(80), nullable=False)
    object_type: Mapped[str] = mapped_column(String(40), nullable=False)
    object_id: Mapped[str] = mapped_column(String(80), nullable=False)
    object_version: Mapped[str | None] = mapped_column(String(40))
    policy_decision: Mapped[str] = mapped_column(String(40), nullable=False)  # allowed | blocked:<code>
    correlation_id: Mapped[str] = mapped_column(String(80), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))


Index("ix_audit_workspace_time", AuditEvent.workspace_id, AuditEvent.occurred_at)


class OutboxEvent(Base):
    __tablename__ = "outbox_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    object_type: Mapped[str] = mapped_column(String(40), nullable=False)
    object_id: Mapped[str] = mapped_column(String(80), nullable=False)
    object_version: Mapped[str | None] = mapped_column(String(40))
    project_id: Mapped[str | None] = mapped_column(String(40))
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = _ts()
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"
    __table_args__ = (UniqueConstraint("workspace_id", "actor_id", "command", "key"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(80), nullable=False)
    command: Mapped[str] = mapped_column(String(80), nullable=False)
    key: Mapped[str] = mapped_column(String(200), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response_status: Mapped[int] = mapped_column(Integer, nullable=False)
    response_body: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = _ts()


# ---------------------------------------------------------------- S1 additions


class ProcessingProfile(Base):
    """Data-processing authorization for a provider access (chapter 8, 12): region and permitted data classes."""

    __tablename__ = "processing_profiles"
    __table_args__ = (UniqueConstraint("workspace_id", "ref"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    ref: Mapped[str] = mapped_column(String(120), nullable=False)
    provider_id: Mapped[str] = mapped_column(String(120), nullable=False)
    region: Mapped[str] = mapped_column(String(80), nullable=False)
    allowed_data_labels: Mapped[list] = mapped_column(JSONB, nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False)
    approved_by: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = _ts()


class QualificationRecord(Base):
    """Evidence that a canonical model is fit for a task class (chapter 8). Never produced by the candidate itself."""

    __tablename__ = "qualification_records"
    __table_args__ = (UniqueConstraint("workspace_id", "ref"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    ref: Mapped[str] = mapped_column(String(120), nullable=False)
    canonical_model_id: Mapped[str] = mapped_column(String(200), nullable=False)
    task_class: Mapped[str] = mapped_column(String(120), nullable=False)
    suite_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    result_summary: Mapped[str] = mapped_column(Text, nullable=False)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    evaluated_by: Mapped[str] = mapped_column(String(200), nullable=False)
    evaluated_by_kind: Mapped[str] = mapped_column(String(20), nullable=False)  # model | human
    # bootstrap (human approval of a canonical model for ONE checking role and task class) | combination (factory evidence for a producer
    # combination) | legacy_unbound (pre-0008 model-evaluated record without combination: history only, never applies)
    kind: Mapped[str] = mapped_column(String(20), nullable=False, default="bootstrap")
    # bootstrap: reviewer | evaluator (required); combination: producer. NULL = undeclared (pre-0008 bootstrap): applies to nothing.
    role: Mapped[str | None] = mapped_column(String(20))
    profile_ref: Mapped[str | None] = mapped_column(String(120))  # combination: the evaluated profile (required)
    skill_refs: Mapped[list | None] = mapped_column(JSONB)  # combination: the skills actually used, exact set (required, may be empty)
    created_by: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = _ts()


class ArtifactVersion(Base):
    """Frozen output of a run; the review object is bound to its hash (Art. 17, I05)."""

    __tablename__ = "artifact_versions"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"))
    run_attempt_id: Mapped[str | None] = mapped_column(ForeignKey("run_attempts.id"))
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id"))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    content: Mapped[dict] = mapped_column(JSONB, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    producer_canonical_model_id: Mapped[str] = mapped_column(String(200), nullable=False)
    synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = _ts()


class ReviewReportRecord(Base):
    __tablename__ = "review_reports"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), nullable=False)
    run_attempt_id: Mapped[str] = mapped_column(ForeignKey("run_attempts.id"), nullable=False)
    artifact_version_id: Mapped[str] = mapped_column(ForeignKey("artifact_versions.id"), nullable=False)
    artifact_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    reviewer_deployment_id: Mapped[str] = mapped_column(String(120), nullable=False)
    reviewer_canonical_model_id: Mapped[str] = mapped_column(String(200), nullable=False)
    reviewer_effective_model_id: Mapped[str] = mapped_column(String(200), nullable=False)
    producer_canonical_model_id: Mapped[str] = mapped_column(String(200), nullable=False)
    invocation_id: Mapped[str] = mapped_column(String(80), nullable=False)
    verdict: Mapped[str] = mapped_column(String(20), nullable=False)
    consistent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)  # verdict vs. findings (S1.1 finding 4)
    document: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = _ts()


class FindingRecord(Base):
    """An objection (Art. 18): subject, argument, scope, responsible handling and status. Never deleted."""

    __tablename__ = "findings"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    review_report_id: Mapped[str] = mapped_column(ForeignKey("review_reports.id"), nullable=False)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), nullable=False)
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    argument: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    scope: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open")  # open | fixed | rejected | limited | escalated
    created_at: Mapped[datetime] = _ts()


# ---------------------------------------------------------------- S2 additions


class Source(Base):
    """A source identity per project (normalized URL). Versions carry the fetched content (chapter 14)."""

    __tablename__ = "sources"
    __table_args__ = (UniqueConstraint("project_id", "normalized_url"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False)
    normalized_url: Mapped[str] = mapped_column(Text, nullable=False)
    host: Mapped[str] = mapped_column(String(253), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")  # active | retracted
    retraction_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _ts()


class SourceVersion(Base):
    __tablename__ = "source_versions"
    __table_args__ = (UniqueConstraint("source_id", "version"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    fetched_at: Mapped[datetime] = _ts()
    final_url: Mapped[str] = mapped_column(Text, nullable=False)
    http_status: Mapped[int] = mapped_column(Integer, nullable=False)
    content_type: Mapped[str] = mapped_column(String(120), nullable=False)
    raw_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    text_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    extracted_text: Mapped[str] = mapped_column(Text, nullable=False)
    extractor_version: Mapped[str] = mapped_column(String(40), nullable=False)
    redirect_chain: Mapped[list] = mapped_column(JSONB, nullable=False)
    fetched_by_task_id: Mapped[str | None] = mapped_column(String(40))


class SourcePassage(Base):
    """A locatable passage: character offsets into the extracted text of one source version (chapter 14)."""

    __tablename__ = "source_passages"
    __table_args__ = (UniqueConstraint("source_version_id", "ordinal"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    source_version_id: Mapped[str] = mapped_column(ForeignKey("source_versions.id"), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    start_offset: Mapped[int] = mapped_column(Integer, nullable=False)
    end_offset: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    text_sha256: Mapped[str] = mapped_column(String(64), nullable=False)


class ClaimVersion(Base):
    __tablename__ = "claim_versions"
    __table_args__ = (UniqueConstraint("claim_id", "version"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), nullable=False)
    run_attempt_id: Mapped[str] = mapped_column(ForeignKey("run_attempts.id"), nullable=False)
    artifact_version_id: Mapped[str] = mapped_column(ForeignKey("artifact_versions.id"), nullable=False)
    claim_id: Mapped[str] = mapped_column(String(40), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    local_id: Mapped[str] = mapped_column(String(80), nullable=False)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    declared_kind: Mapped[str] = mapped_column(String(30), nullable=False)
    stored_kind: Mapped[str] = mapped_column(String(30), nullable=False)
    epistemic_status: Mapped[str] = mapped_column(String(20), nullable=False)
    assessment_ref: Mapped[str] = mapped_column(String(80), nullable=False)
    validity_conditions: Mapped[str] = mapped_column(Text, nullable=False)
    flags: Mapped[list] = mapped_column(JSONB, nullable=False)
    producer_canonical_model_id: Mapped[str] = mapped_column(String(200), nullable=False)
    reassessment_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = _ts()


class EvidenceLink(Base):
    __tablename__ = "evidence_links"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    claim_version_id: Mapped[str] = mapped_column(ForeignKey("claim_versions.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)  # source_passage | own_run | experiment_run
    source_passage_id: Mapped[str | None] = mapped_column(ForeignKey("source_passages.id"))
    run_attempt_id: Mapped[str | None] = mapped_column(ForeignKey("run_attempts.id"))
    experiment_run_id: Mapped[str | None] = mapped_column(ForeignKey("experiment_runs.id"))  # S4: a measured, completed sandbox run
    note: Mapped[str] = mapped_column(Text, nullable=False)


class DependencyEdge(Base):
    __tablename__ = "dependency_edges"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    from_claim_version_id: Mapped[str] = mapped_column(ForeignKey("claim_versions.id"), nullable=False)
    to_claim_version_id: Mapped[str] = mapped_column(ForeignKey("claim_versions.id"), nullable=False)
    relation: Mapped[str] = mapped_column(String(20), nullable=False, default="supports")


class ReassessmentTask(Base):
    """Closes the gap between storing a correction event and acting on it (Art. 23, chapter 14, T09)."""

    __tablename__ = "reassessment_tasks"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False)
    trigger: Mapped[str] = mapped_column(String(40), nullable=False)
    trigger_object_id: Mapped[str] = mapped_column(String(80), nullable=False)
    affected_claim_version_ids: Mapped[list] = mapped_column(JSONB, nullable=False)
    responsible_role: Mapped[str] = mapped_column(String(40), nullable=False)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="open")
    created_at: Mapped[datetime] = _ts()


class Approval(Base):
    """Binds artifact id, version hash, purpose and target to a human decision (chapter 15, T08)."""

    __tablename__ = "approvals"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    artifact_version_id: Mapped[str] = mapped_column(ForeignKey("artifact_versions.id"), nullable=False)
    artifact_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    purpose: Mapped[str] = mapped_column(String(40), nullable=False)
    human_decision_id: Mapped[str] = mapped_column(ForeignKey("human_decisions.id"), nullable=False)
    option: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = _ts()


class ReleaseAttempt(Base):
    __tablename__ = "release_attempts"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    artifact_version_id: Mapped[str] = mapped_column(ForeignKey("artifact_versions.id"), nullable=False)
    artifact_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    approval_id: Mapped[str] = mapped_column(ForeignKey("approvals.id"), nullable=False)
    target: Mapped[str] = mapped_column(Text, nullable=False)
    human_decision_id: Mapped[str] = mapped_column(ForeignKey("human_decisions.id"), nullable=False)
    state: Mapped[str] = mapped_column(String(30), nullable=False)  # recorded_no_channel (S2) | sent | failed
    created_at: Mapped[datetime] = _ts()


class WorkflowRun(Base):
    __tablename__ = "workflow_runs"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)  # Temporal workflow id
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), nullable=False)
    task_queue: Mapped[str] = mapped_column(String(80), nullable=False)
    started_by: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[datetime] = _ts()


# ---------------------------------------------------------------- S3 additions: agent/skill factory


class NeedRecord(Base):
    """Why a unit exists: triggering task, gap, alternatives considered, expected benefit, decision (chapter 7, Annex G)."""

    __tablename__ = "need_records"
    __table_args__ = (UniqueConstraint("workspace_id", "ref"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    ref: Mapped[str] = mapped_column(String(120), nullable=False)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id"))
    task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"))
    gap: Mapped[str] = mapped_column(Text, nullable=False)
    requested_specialization: Mapped[str] = mapped_column(String(200), nullable=False)
    task_class: Mapped[str] = mapped_column(String(120), nullable=False)
    alternatives_considered: Mapped[list] = mapped_column(JSONB, nullable=False)
    expected_benefit: Mapped[str] = mapped_column(Text, nullable=False)
    decision: Mapped[str] = mapped_column(String(20), nullable=False)  # reuse | extend_skill | new_agent | new_skill
    reuse_ref: Mapped[str | None] = mapped_column(String(120))
    created_by: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[datetime] = _ts()


class QualificationSuite(Base):
    """Fixed, versioned test material. Protected suites hide their cases from producers (Art. 19, T05, T10)."""

    __tablename__ = "qualification_suites"
    __table_args__ = (UniqueConstraint("workspace_id", "ref", "version"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    ref: Mapped[str] = mapped_column(String(120), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    task_class: Mapped[str] = mapped_column(String(120), nullable=False)
    evaluator_task_class: Mapped[str] = mapped_column(String(120), nullable=False, default="evaluation")
    threshold_percent: Mapped[int] = mapped_column(Integer, nullable=False)
    protected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # the cases themselves live in the vault (schema `vault`, own role, S4); the application schema keeps hash and count only
    case_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cases_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[datetime] = _ts()


class EvaluationRun(Base):
    """One qualification run of a candidate (profile or skill) with one deployment on one suite version, judged by a distinct model."""

    __tablename__ = "evaluation_runs"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), nullable=False)
    candidate_kind: Mapped[str] = mapped_column(String(10), nullable=False)  # agent | skill
    candidate_ref: Mapped[str] = mapped_column(String(120), nullable=False)
    candidate_deployment_id: Mapped[str] = mapped_column(String(120), nullable=False)
    candidate_canonical_model_id: Mapped[str] = mapped_column(String(200), nullable=False)
    evaluator_deployment_id: Mapped[str | None] = mapped_column(String(120))
    evaluator_canonical_model_id: Mapped[str | None] = mapped_column(String(200))
    suite_id: Mapped[str] = mapped_column(ForeignKey("qualification_suites.id"), nullable=False)
    suite_ref: Mapped[str] = mapped_column(String(120), nullable=False)
    suite_version: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(20), nullable=False)  # running | passed | failed | aborted
    score: Mapped[int | None] = mapped_column(Integer)
    baseline_score: Mapped[int | None] = mapped_column(Integer)
    regression: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    activated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    activation_note: Mapped[str | None] = mapped_column(Text)
    qualification_ref: Mapped[str | None] = mapped_column(String(120))
    composition_key: Mapped[str | None] = mapped_column(String(400))  # profile+skills+task class the candidate was built from
    baseline_comparable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)  # regression judged only on identical material
    case_results: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    context_manifest: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)  # what each evaluator call received (A11)
    created_by: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[datetime] = _ts()
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RetirementRecord(Base):
    __tablename__ = "retirement_records"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    candidate_kind: Mapped[str] = mapped_column(String(10), nullable=False)
    candidate_ref: Mapped[str] = mapped_column(String(120), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    successor_ref: Mapped[str | None] = mapped_column(String(120))
    new_status: Mapped[str] = mapped_column(String(20), nullable=False)  # dormant | archived
    decided_by: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[datetime] = _ts()


class SandboxAcceptance(Base):
    """Human acceptance of ONE sandbox path: runner kind, image digest and host, on the strength of a passed probe (chapter 13, [T7])."""

    __tablename__ = "sandbox_acceptances"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    runner_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    image: Mapped[str] = mapped_column(String(300), nullable=False)
    host_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    probe_report: Mapped[dict] = mapped_column(JSONB, nullable=False)
    probe_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False)
    accepted_by: Mapped[str] = mapped_column(String(40), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_by: Mapped[str | None] = mapped_column(String(40))
    revoke_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _ts()


class ExperimentRun(Base):
    """One run of untrusted code in the sandbox. Everything measured comes from the trusted runner; stdout is content (chapter 13, T04)."""

    __tablename__ = "experiment_runs"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), nullable=False)
    acceptance_id: Mapped[str | None] = mapped_column(ForeignKey("sandbox_acceptances.id"))
    runner_kind: Mapped[str] = mapped_column(String(20), nullable=False)  # docker (measures) | mock (fixture)
    image: Mapped[str] = mapped_column(String(300), nullable=False)
    host_fingerprint: Mapped[str | None] = mapped_column(String(64))
    program_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    argv: Mapped[list] = mapped_column(JSONB, nullable=False)
    input_manifest: Mapped[list] = mapped_column(JSONB, nullable=False)
    input_manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    limits: Mapped[dict] = mapped_column(JSONB, nullable=False)
    state: Mapped[str] = mapped_column(String(30), nullable=False)  # running | completed | failed | timed_out | oom_killed | output_limit_exceeded | runner_unavailable
    measured: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    exit_code: Mapped[int | None] = mapped_column(Integer)
    timed_out: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    oom_killed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stdout_excerpt: Mapped[str] = mapped_column(Text, nullable=False, default="")
    stderr_excerpt: Mapped[str] = mapped_column(Text, nullable=False, default="")
    outputs: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    notes: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    created_by: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = _ts()
