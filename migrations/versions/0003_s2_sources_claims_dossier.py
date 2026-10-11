"""S2: sources with versions and passages, claims with evidence and dependencies, reassessment tasks, approvals,
release attempts, workflow runs; dossier artifacts without task binding.

Revision ID: 0003_s2_sources_claims_dossier
Revises: 0002_s1_providers_review
Create Date: 2026-10-11
"""
from __future__ import annotations

import os

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003_s2_sources_claims_dossier"
down_revision = "0002_s1_providers_review"
branch_labels = None
depends_on = None

APP_ROLE = os.environ.get("CAMPUS_APP_DB_ROLE", "campus_app")
NEW_TABLES = ("sources", "source_versions", "source_passages", "claim_versions", "evidence_links", "dependency_edges",
              "reassessment_tasks", "approvals", "release_attempts", "workflow_runs")


def upgrade() -> None:
    ts = sa.DateTime(timezone=True)
    op.alter_column("artifact_versions", "task_id", existing_type=sa.String(40), nullable=True)
    op.alter_column("artifact_versions", "run_attempt_id", existing_type=sa.String(40), nullable=True)
    op.add_column("artifact_versions", sa.Column("project_id", sa.String(40), sa.ForeignKey("projects.id"), nullable=True))
    op.add_column("artifact_versions", sa.Column("version", sa.Integer, nullable=False, server_default="1"))

    op.create_table(
        "sources",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("workspace_id", sa.String(40), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("project_id", sa.String(40), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("normalized_url", sa.Text, nullable=False),
        sa.Column("host", sa.String(253), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("retraction_reason", sa.Text),
        sa.Column("created_at", ts, nullable=False),
        sa.UniqueConstraint("project_id", "normalized_url"),
    )
    op.create_table(
        "source_versions",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("workspace_id", sa.String(40), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("source_id", sa.String(40), sa.ForeignKey("sources.id"), nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("fetched_at", ts, nullable=False),
        sa.Column("final_url", sa.Text, nullable=False),
        sa.Column("http_status", sa.Integer, nullable=False),
        sa.Column("content_type", sa.String(120), nullable=False),
        sa.Column("raw_sha256", sa.String(64), nullable=False),
        sa.Column("raw_bytes", sa.Integer, nullable=False),
        sa.Column("text_sha256", sa.String(64), nullable=False),
        sa.Column("extracted_text", sa.Text, nullable=False),
        sa.Column("extractor_version", sa.String(40), nullable=False),
        sa.Column("redirect_chain", postgresql.JSONB, nullable=False),
        sa.Column("fetched_by_task_id", sa.String(40)),
        sa.UniqueConstraint("source_id", "version"),
    )
    op.create_table(
        "source_passages",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("workspace_id", sa.String(40), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("source_version_id", sa.String(40), sa.ForeignKey("source_versions.id"), nullable=False),
        sa.Column("ordinal", sa.Integer, nullable=False),
        sa.Column("start_offset", sa.Integer, nullable=False),
        sa.Column("end_offset", sa.Integer, nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("text_sha256", sa.String(64), nullable=False),
        sa.UniqueConstraint("source_version_id", "ordinal"),
    )
    op.create_table(
        "claim_versions",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("workspace_id", sa.String(40), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("project_id", sa.String(40), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("task_id", sa.String(40), sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("run_attempt_id", sa.String(40), sa.ForeignKey("run_attempts.id"), nullable=False),
        sa.Column("artifact_version_id", sa.String(40), sa.ForeignKey("artifact_versions.id"), nullable=False),
        sa.Column("claim_id", sa.String(40), nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("local_id", sa.String(80), nullable=False),
        sa.Column("statement", sa.Text, nullable=False),
        sa.Column("declared_kind", sa.String(30), nullable=False),
        sa.Column("stored_kind", sa.String(30), nullable=False),
        sa.Column("epistemic_status", sa.String(20), nullable=False),
        sa.Column("assessment_ref", sa.String(80), nullable=False),
        sa.Column("validity_conditions", sa.Text, nullable=False),
        sa.Column("flags", postgresql.JSONB, nullable=False),
        sa.Column("producer_canonical_model_id", sa.String(200), nullable=False),
        sa.Column("reassessment_required", sa.Boolean, nullable=False),
        sa.Column("created_at", ts, nullable=False),
        sa.UniqueConstraint("claim_id", "version"),
    )
    op.create_table(
        "evidence_links",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("workspace_id", sa.String(40), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("claim_version_id", sa.String(40), sa.ForeignKey("claim_versions.id"), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("source_passage_id", sa.String(40), sa.ForeignKey("source_passages.id")),
        sa.Column("run_attempt_id", sa.String(40), sa.ForeignKey("run_attempts.id")),
        sa.Column("note", sa.Text, nullable=False),
    )
    op.create_table(
        "dependency_edges",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("workspace_id", sa.String(40), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("from_claim_version_id", sa.String(40), sa.ForeignKey("claim_versions.id"), nullable=False),
        sa.Column("to_claim_version_id", sa.String(40), sa.ForeignKey("claim_versions.id"), nullable=False),
        sa.Column("relation", sa.String(20), nullable=False),
    )
    op.create_table(
        "reassessment_tasks",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("workspace_id", sa.String(40), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("project_id", sa.String(40), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("trigger", sa.String(40), nullable=False),
        sa.Column("trigger_object_id", sa.String(80), nullable=False),
        sa.Column("affected_claim_version_ids", postgresql.JSONB, nullable=False),
        sa.Column("responsible_role", sa.String(40), nullable=False),
        sa.Column("due_at", ts, nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("created_at", ts, nullable=False),
    )
    op.create_table(
        "approvals",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("workspace_id", sa.String(40), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("artifact_version_id", sa.String(40), sa.ForeignKey("artifact_versions.id"), nullable=False),
        sa.Column("artifact_sha256", sa.String(64), nullable=False),
        sa.Column("purpose", sa.String(40), nullable=False),
        sa.Column("human_decision_id", sa.String(40), sa.ForeignKey("human_decisions.id"), nullable=False),
        sa.Column("option", sa.String(20), nullable=False),
        sa.Column("created_at", ts, nullable=False),
    )
    op.create_table(
        "release_attempts",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("workspace_id", sa.String(40), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("artifact_version_id", sa.String(40), sa.ForeignKey("artifact_versions.id"), nullable=False),
        sa.Column("artifact_sha256", sa.String(64), nullable=False),
        sa.Column("approval_id", sa.String(40), sa.ForeignKey("approvals.id"), nullable=False),
        sa.Column("target", sa.Text, nullable=False),
        sa.Column("human_decision_id", sa.String(40), sa.ForeignKey("human_decisions.id"), nullable=False),
        sa.Column("state", sa.String(30), nullable=False),
        sa.Column("created_at", ts, nullable=False),
    )
    op.create_table(
        "workflow_runs",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("workspace_id", sa.String(40), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("project_id", sa.String(40), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("task_id", sa.String(40), sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("task_queue", sa.String(80), nullable=False),
        sa.Column("started_by", sa.String(80), nullable=False),
        sa.Column("created_at", ts, nullable=False),
    )
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": APP_ROLE}).scalar():
        for table in NEW_TABLES:
            op.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO "{APP_ROLE}"')
        # Versioned sources, claims and approvals are append-only for the application (ADR-0006, Art. 23).
        for table in ("source_versions", "source_passages", "claim_versions", "evidence_links", "approvals", "release_attempts"):
            op.execute(f'REVOKE DELETE ON {table} FROM "{APP_ROLE}"')


def downgrade() -> None:
    for table in reversed(NEW_TABLES):
        op.drop_table(table)
    op.drop_column("artifact_versions", "version")
    op.drop_column("artifact_versions", "project_id")
    op.alter_column("artifact_versions", "run_attempt_id", existing_type=sa.String(40), nullable=False)
    op.alter_column("artifact_versions", "task_id", existing_type=sa.String(40), nullable=False)
