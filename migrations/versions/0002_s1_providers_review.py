"""S1: processing profiles, qualification records, artifact versions, review reports, findings.

Revision ID: 0002_s1_providers_review
Revises: 0001_s0_core
Create Date: 2026-10-11
"""
from __future__ import annotations

import os

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002_s1_providers_review"
down_revision = "0001_s0_core"
branch_labels = None
depends_on = None

APP_ROLE = os.environ.get("CAMPUS_APP_DB_ROLE", "campus_app")
NEW_TABLES = ("processing_profiles", "qualification_records", "artifact_versions", "review_reports", "findings")


def upgrade() -> None:
    ts = sa.DateTime(timezone=True)
    op.create_table(
        "processing_profiles",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("workspace_id", sa.String(40), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("ref", sa.String(120), nullable=False),
        sa.Column("provider_id", sa.String(120), nullable=False),
        sa.Column("region", sa.String(80), nullable=False),
        sa.Column("allowed_data_labels", postgresql.JSONB, nullable=False),
        sa.Column("note", sa.Text, nullable=False),
        sa.Column("approved_by", sa.String(40), nullable=False),
        sa.Column("created_at", ts, nullable=False),
        sa.UniqueConstraint("workspace_id", "ref"),
    )
    op.create_table(
        "qualification_records",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("workspace_id", sa.String(40), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("ref", sa.String(120), nullable=False),
        sa.Column("canonical_model_id", sa.String(200), nullable=False),
        sa.Column("task_class", sa.String(120), nullable=False),
        sa.Column("suite_ref", sa.String(200), nullable=False),
        sa.Column("result_summary", sa.Text, nullable=False),
        sa.Column("passed", sa.Boolean, nullable=False),
        sa.Column("evaluated_by", sa.String(200), nullable=False),
        sa.Column("evaluated_by_kind", sa.String(20), nullable=False),
        sa.Column("created_by", sa.String(40), nullable=False),
        sa.Column("created_at", ts, nullable=False),
        sa.UniqueConstraint("workspace_id", "ref"),
    )
    op.create_table(
        "artifact_versions",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("workspace_id", sa.String(40), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("task_id", sa.String(40), sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("run_attempt_id", sa.String(40), sa.ForeignKey("run_attempts.id"), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("content", postgresql.JSONB, nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("producer_canonical_model_id", sa.String(200), nullable=False),
        sa.Column("synthetic", sa.Boolean, nullable=False),
        sa.Column("created_at", ts, nullable=False),
    )
    op.create_table(
        "review_reports",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("workspace_id", sa.String(40), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("task_id", sa.String(40), sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("run_attempt_id", sa.String(40), sa.ForeignKey("run_attempts.id"), nullable=False),
        sa.Column("artifact_version_id", sa.String(40), sa.ForeignKey("artifact_versions.id"), nullable=False),
        sa.Column("artifact_sha256", sa.String(64), nullable=False),
        sa.Column("reviewer_deployment_id", sa.String(120), nullable=False),
        sa.Column("reviewer_canonical_model_id", sa.String(200), nullable=False),
        sa.Column("reviewer_effective_model_id", sa.String(200), nullable=False),
        sa.Column("producer_canonical_model_id", sa.String(200), nullable=False),
        sa.Column("invocation_id", sa.String(80), nullable=False),
        sa.Column("verdict", sa.String(20), nullable=False),
        sa.Column("document", postgresql.JSONB, nullable=False),
        sa.Column("created_at", ts, nullable=False),
    )
    op.create_table(
        "findings",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("workspace_id", sa.String(40), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("review_report_id", sa.String(40), sa.ForeignKey("review_reports.id"), nullable=False),
        sa.Column("task_id", sa.String(40), sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("subject", sa.Text, nullable=False),
        sa.Column("argument", sa.Text, nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("scope", sa.Text, nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", ts, nullable=False),
    )
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": APP_ROLE}).scalar():
        for table in NEW_TABLES:
            op.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO "{APP_ROLE}"')
        # Findings keep their history: no DELETE for the application role (Art. 18, T06).
        op.execute(f'REVOKE DELETE ON findings FROM "{APP_ROLE}"')


def downgrade() -> None:
    for table in reversed(NEW_TABLES):
        op.drop_table(table)
