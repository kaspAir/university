"""S5: controlled research pilot: pre-registered protocols, arms, attached tasks, blinded independent evaluations, platform evidence.

Revision ID: 0010_s5_pilot
Revises: 0009_s4_sandbox_vault
Create Date: 2026-10-12
"""
from __future__ import annotations

import os

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0010_s5_pilot"
down_revision = "0009_s4_sandbox_vault"
branch_labels = None
depends_on = None

APP_ROLE = os.environ.get("CAMPUS_APP_DB_ROLE", "campus_app")
NEW_TABLES = ("platform_evidence", "pilot_studies", "pilot_arms", "pilot_arm_projects", "pilot_arm_tasks", "pilot_evaluations")


def upgrade() -> None:
    ts = sa.DateTime(timezone=True)
    op.create_table(
        "platform_evidence",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("workspace_id", sa.String(40), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("build_sha", sa.String(64), nullable=False),
        sa.Column("source", sa.String(200), nullable=False),
        sa.Column("summary", postgresql.JSONB, nullable=False),
        sa.Column("covered_ids", postgresql.JSONB, nullable=False),
        sa.Column("failed_ids", postgresql.JSONB, nullable=False),
        sa.Column("report_sha256", sa.String(64), nullable=False),
        sa.Column("imported_by", sa.String(40), nullable=False),
        sa.Column("created_at", ts, nullable=False),
    )
    op.create_table(
        "pilot_studies",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("workspace_id", sa.String(40), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("mandate_version_id", sa.String(40), sa.ForeignKey("mandate_versions.id"), nullable=False),
        sa.Column("ref", sa.String(120), nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("protocol", postgresql.JSONB, nullable=False),
        sa.Column("preregistration_sha256", sa.String(64), nullable=False),
        sa.Column("evaluation_suite_id", sa.String(40), sa.ForeignKey("qualification_suites.id")),
        sa.Column("budget_account_id", sa.String(40), sa.ForeignKey("budget_accounts.id"), nullable=False),
        sa.Column("evaluation_project_id", sa.String(40), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("decision_id", sa.String(40), sa.ForeignKey("human_decisions.id")),
        sa.Column("evidence_id", sa.String(40), sa.ForeignKey("platform_evidence.id")),
        sa.Column("self_control", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_by", sa.String(40), nullable=False),
        sa.Column("created_at", ts, nullable=False),
        sa.Column("released_at", ts),
        sa.Column("closed_at", ts),
        sa.UniqueConstraint("workspace_id", "ref", "version"),
    )
    op.create_table(
        "pilot_arms",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("pilot_id", sa.String(40), sa.ForeignKey("pilot_studies.id"), nullable=False),
        sa.Column("condition", sa.String(40), nullable=False),
        sa.UniqueConstraint("pilot_id", "condition"),
    )
    op.create_table(
        "pilot_arm_projects",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("arm_id", sa.String(40), sa.ForeignKey("pilot_arms.id"), nullable=False),
        sa.Column("project_id", sa.String(40), sa.ForeignKey("projects.id"), nullable=False, unique=True),
    )
    op.create_table(
        "pilot_arm_tasks",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("arm_id", sa.String(40), sa.ForeignKey("pilot_arms.id"), nullable=False),
        sa.Column("task_id", sa.String(40), sa.ForeignKey("tasks.id"), nullable=False, unique=True),
        sa.Column("repetition", sa.Integer, nullable=False),
        sa.Column("created_at", ts, nullable=False),
    )
    op.create_table(
        "pilot_evaluations",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("workspace_id", sa.String(40), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("pilot_id", sa.String(40), sa.ForeignKey("pilot_studies.id"), nullable=False),
        sa.Column("arm_id", sa.String(40), sa.ForeignKey("pilot_arms.id"), nullable=False),
        sa.Column("task_id", sa.String(40), sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("repetition", sa.Integer, nullable=False),
        sa.Column("state", sa.String(30), nullable=False),
        sa.Column("evaluator_deployment_id", sa.String(120)),
        sa.Column("evaluator_canonical_model_id", sa.String(200)),
        sa.Column("excluded_canonical_model_ids", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("case_results", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("context_manifest", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("blinded", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("score", sa.Integer),
        sa.Column("passed", sa.Boolean),
        sa.Column("note", sa.Text, nullable=False, server_default=""),
        sa.Column("created_by", sa.String(40), nullable=False),
        sa.Column("created_at", ts, nullable=False),
    )
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": APP_ROLE}).scalar():
        for table in NEW_TABLES:
            op.execute(f'GRANT SELECT, INSERT, UPDATE ON {table} TO "{APP_ROLE}"')


def downgrade() -> None:
    for table in reversed(NEW_TABLES):
        op.drop_table(table)
