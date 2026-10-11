"""S3: agent/skill factory: need records, qualification suites, evaluation runs, retirement records; activation scope and
skill instructions.

Revision ID: 0005_s3_factory
Revises: 0004_s1_1_hardening
Create Date: 2026-10-11
"""
from __future__ import annotations

import os

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005_s3_factory"
down_revision = "0004_s1_1_hardening"
branch_labels = None
depends_on = None

APP_ROLE = os.environ.get("CAMPUS_APP_DB_ROLE", "campus_app")
NEW_TABLES = ("need_records", "qualification_suites", "evaluation_runs", "retirement_records")


def upgrade() -> None:
    ts = sa.DateTime(timezone=True)
    op.add_column("agent_profile_versions", sa.Column("qualified_task_classes", postgresql.JSONB, nullable=False, server_default="[]"))
    op.add_column("skill_versions", sa.Column("instructions", sa.Text, nullable=True))
    op.create_table(
        "need_records",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("workspace_id", sa.String(40), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("ref", sa.String(120), nullable=False),
        sa.Column("project_id", sa.String(40), sa.ForeignKey("projects.id")),
        sa.Column("task_id", sa.String(40), sa.ForeignKey("tasks.id")),
        sa.Column("gap", sa.Text, nullable=False),
        sa.Column("requested_specialization", sa.String(200), nullable=False),
        sa.Column("task_class", sa.String(120), nullable=False),
        sa.Column("alternatives_considered", postgresql.JSONB, nullable=False),
        sa.Column("expected_benefit", sa.Text, nullable=False),
        sa.Column("decision", sa.String(20), nullable=False),
        sa.Column("reuse_ref", sa.String(120)),
        sa.Column("created_by", sa.String(80), nullable=False),
        sa.Column("created_at", ts, nullable=False),
        sa.UniqueConstraint("workspace_id", "ref"),
    )
    op.create_table(
        "qualification_suites",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("workspace_id", sa.String(40), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("ref", sa.String(120), nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("task_class", sa.String(120), nullable=False),
        sa.Column("evaluator_task_class", sa.String(120), nullable=False),
        sa.Column("threshold_percent", sa.Integer, nullable=False),
        sa.Column("protected", sa.Boolean, nullable=False),
        sa.Column("cases", postgresql.JSONB, nullable=False),
        sa.Column("cases_sha256", sa.String(64), nullable=False),
        sa.Column("created_by", sa.String(80), nullable=False),
        sa.Column("created_at", ts, nullable=False),
        sa.UniqueConstraint("workspace_id", "ref", "version"),
    )
    op.create_table(
        "evaluation_runs",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("workspace_id", sa.String(40), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("project_id", sa.String(40), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("task_id", sa.String(40), sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("candidate_kind", sa.String(10), nullable=False),
        sa.Column("candidate_ref", sa.String(120), nullable=False),
        sa.Column("candidate_deployment_id", sa.String(120), nullable=False),
        sa.Column("candidate_canonical_model_id", sa.String(200), nullable=False),
        sa.Column("evaluator_deployment_id", sa.String(120)),
        sa.Column("evaluator_canonical_model_id", sa.String(200)),
        sa.Column("suite_id", sa.String(40), sa.ForeignKey("qualification_suites.id"), nullable=False),
        sa.Column("suite_ref", sa.String(120), nullable=False),
        sa.Column("suite_version", sa.Integer, nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("score", sa.Integer),
        sa.Column("baseline_score", sa.Integer),
        sa.Column("regression", sa.Boolean, nullable=False),
        sa.Column("activated", sa.Boolean, nullable=False),
        sa.Column("activation_note", sa.Text),
        sa.Column("qualification_ref", sa.String(120)),
        sa.Column("case_results", postgresql.JSONB, nullable=False),
        sa.Column("context_manifest", postgresql.JSONB, nullable=False),
        sa.Column("created_by", sa.String(80), nullable=False),
        sa.Column("created_at", ts, nullable=False),
        sa.Column("ended_at", ts),
    )
    op.create_table(
        "retirement_records",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("workspace_id", sa.String(40), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("candidate_kind", sa.String(10), nullable=False),
        sa.Column("candidate_ref", sa.String(120), nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("successor_ref", sa.String(120)),
        sa.Column("new_status", sa.String(20), nullable=False),
        sa.Column("decided_by", sa.String(80), nullable=False),
        sa.Column("created_at", ts, nullable=False),
    )
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": APP_ROLE}).scalar():
        for table in NEW_TABLES:
            op.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO "{APP_ROLE}"')
        for table in ("qualification_suites", "evaluation_runs", "retirement_records", "need_records"):
            op.execute(f'REVOKE DELETE ON {table} FROM "{APP_ROLE}"')  # test material and evaluation history stay (T10)
        op.execute(f'REVOKE UPDATE ON qualification_suites FROM "{APP_ROLE}"')  # a changed criterion is a new version


def downgrade() -> None:
    for table in reversed(NEW_TABLES):
        op.drop_table(table)
    op.drop_column("skill_versions", "instructions")
    op.drop_column("agent_profile_versions", "qualified_task_classes")
