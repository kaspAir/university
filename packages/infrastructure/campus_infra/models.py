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
    created_by: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = _ts()


class ArtifactVersion(Base):
    """Frozen output of a run; the review object is bound to its hash (Art. 17, I05)."""

    __tablename__ = "artifact_versions"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), nullable=False)
    run_attempt_id: Mapped[str] = mapped_column(ForeignKey("run_attempts.id"), nullable=False)
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
