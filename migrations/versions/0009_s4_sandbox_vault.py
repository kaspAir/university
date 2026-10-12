"""S4: isolated execution and separately protected evaluation material.

  * schema `vault` with `vault.suite_cases`: the cases of qualification suites move out of the application schema; the application
    role gets no privilege on the schema, the vault role may insert and read only (append-only material);
  * `qualification_suites.cases` is dropped (hash and count stay);
  * `sandbox_acceptances`: human acceptance of one sandbox path (runner kind, image digest, host fingerprint, probe report);
  * `experiment_runs`: runs of untrusted code in the sandbox with the trusted runner's measurements;
  * evidence links may point to an experiment run.

Revision ID: 0009_s4_sandbox_vault
Revises: 0008_s3_role_bound_bootstrap
Create Date: 2026-10-12
"""
from __future__ import annotations

import os

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0009_s4_sandbox_vault"
down_revision = "0008_s3_role_bound_bootstrap"
branch_labels = None
depends_on = None

APP_ROLE = os.environ.get("CAMPUS_APP_DB_ROLE", "campus_app")
VAULT_ROLE = os.environ.get("CAMPUS_VAULT_DB_ROLE", "campus_vault")


def _role_exists(name: str) -> bool:
    return bool(op.get_bind().execute(sa.text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": name}).scalar())


def upgrade() -> None:
    ts = sa.DateTime(timezone=True)
    # --- vault -------------------------------------------------------------------------------------------------------
    op.execute("CREATE SCHEMA vault")
    op.execute("REVOKE ALL ON SCHEMA vault FROM PUBLIC")
    op.create_table(
        "suite_cases",
        sa.Column("suite_id", sa.String(40), sa.ForeignKey("public.qualification_suites.id"), primary_key=True),
        sa.Column("workspace_id", sa.String(40), nullable=False),
        sa.Column("cases", postgresql.JSONB, nullable=False),
        sa.Column("cases_sha256", sa.String(64), nullable=False),
        sa.Column("created_at", ts, nullable=False, server_default=sa.func.now()),
        schema="vault",
    )
    op.execute("INSERT INTO vault.suite_cases (suite_id, workspace_id, cases, cases_sha256) SELECT id, workspace_id, cases, cases_sha256 FROM qualification_suites")
    op.add_column("qualification_suites", sa.Column("case_count", sa.Integer, nullable=False, server_default="0"))
    op.execute("UPDATE qualification_suites SET case_count = jsonb_array_length(cases)")
    op.drop_column("qualification_suites", "cases")
    if _role_exists(VAULT_ROLE):
        op.execute(f'GRANT USAGE ON SCHEMA vault TO "{VAULT_ROLE}"')
        op.execute(f'GRANT SELECT, INSERT ON vault.suite_cases TO "{VAULT_ROLE}"')
        op.execute(f'GRANT SELECT (id) ON public.qualification_suites TO "{VAULT_ROLE}"')  # foreign key check only
    if _role_exists(APP_ROLE):
        op.execute(f'REVOKE ALL ON SCHEMA vault FROM "{APP_ROLE}"')
    # --- sandbox acceptances -----------------------------------------------------------------------------------------
    op.create_table(
        "sandbox_acceptances",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("workspace_id", sa.String(40), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("runner_kind", sa.String(20), nullable=False),
        sa.Column("image", sa.String(300), nullable=False),
        sa.Column("host_fingerprint", sa.String(64), nullable=False),
        sa.Column("probe_report", postgresql.JSONB, nullable=False),
        sa.Column("probe_sha256", sa.String(64), nullable=False),
        sa.Column("note", sa.Text, nullable=False),
        sa.Column("accepted_by", sa.String(40), nullable=False),
        sa.Column("revoked_at", ts),
        sa.Column("revoked_by", sa.String(40)),
        sa.Column("revoke_reason", sa.Text),
        sa.Column("created_at", ts, nullable=False),
    )
    # --- experiment runs ---------------------------------------------------------------------------------------------
    op.create_table(
        "experiment_runs",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("workspace_id", sa.String(40), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("project_id", sa.String(40), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("task_id", sa.String(40), sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("acceptance_id", sa.String(40), sa.ForeignKey("sandbox_acceptances.id")),
        sa.Column("runner_kind", sa.String(20), nullable=False),
        sa.Column("image", sa.String(300), nullable=False),
        sa.Column("host_fingerprint", sa.String(64)),
        sa.Column("program_sha256", sa.String(64), nullable=False),
        sa.Column("argv", postgresql.JSONB, nullable=False),
        sa.Column("input_manifest", postgresql.JSONB, nullable=False),
        sa.Column("input_manifest_sha256", sa.String(64), nullable=False),
        sa.Column("limits", postgresql.JSONB, nullable=False),
        sa.Column("state", sa.String(30), nullable=False),
        sa.Column("measured", sa.Boolean, nullable=False),
        sa.Column("exit_code", sa.Integer),
        sa.Column("timed_out", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("oom_killed", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("started_at", ts),
        sa.Column("ended_at", ts),
        sa.Column("stdout_excerpt", sa.Text, nullable=False, server_default=""),
        sa.Column("stderr_excerpt", sa.Text, nullable=False, server_default=""),
        sa.Column("outputs", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("notes", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("created_by", sa.String(40), nullable=False),
        sa.Column("created_at", ts, nullable=False),
    )
    op.add_column("evidence_links", sa.Column("experiment_run_id", sa.String(40), sa.ForeignKey("experiment_runs.id")))
    if _role_exists(APP_ROLE):
        for table in ("sandbox_acceptances", "experiment_runs"):
            op.execute(f'GRANT SELECT, INSERT, UPDATE ON {table} TO "{APP_ROLE}"')


def downgrade() -> None:
    op.drop_column("evidence_links", "experiment_run_id")
    op.drop_table("experiment_runs")
    op.drop_table("sandbox_acceptances")
    op.add_column("qualification_suites", sa.Column("cases", postgresql.JSONB, nullable=False, server_default="[]"))
    op.execute("UPDATE qualification_suites q SET cases = v.cases FROM vault.suite_cases v WHERE v.suite_id = q.id")
    op.drop_column("qualification_suites", "case_count")
    op.drop_table("suite_cases", schema="vault")
    op.execute("DROP SCHEMA vault")
