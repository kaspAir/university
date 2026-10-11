"""S0 core schema (frozen baseline): identity, mandates, policy, catalog, budget, tasks, registry, routing, journal, audit, outbox.

Revision ID: 0001_s0_core
Revises: None
Create Date: 2026-10-11

The baseline is written out explicitly and never derived from the live ORM metadata, so later columns and tables
arrive only through later revisions (lesson from the S1/S2 additions). Database-level protections follow:
an append-only trigger on audit_events and privileges for the application role (ADR-0004).
"""
from __future__ import annotations

import os

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_s0_core"
down_revision = None
branch_labels = None
depends_on = None

APP_ROLE = os.environ.get("CAMPUS_APP_DB_ROLE", "campus_app")

S0_TABLES = (
    "workspaces",
    "agent_profile_versions",
    "audit_events",
    "human_decisions",
    "idempotency_keys",
    "institutes",
    "llm_invocations",
    "mandate_versions",
    "model_catalog_revisions",
    "outbox_events",
    "policy_versions",
    "pricing_versions",
    "skill_versions",
    "users",
    "budget_accounts",
    "grants",
    "sessions",
    "projects",
    "reservations",
    "cost_entries",
    "tasks",
    "route_proposals",
    "routing_decisions",
    "run_attempts",
)


def upgrade() -> None:
    op.create_table('workspaces',
    sa.Column('id', sa.String(length=40), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('stopped', sa.Boolean(), nullable=False),
    sa.Column('stop_reason', sa.Text(), nullable=True),
    sa.Column('capability_level', sa.String(length=40), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('agent_profile_versions',
    sa.Column('id', sa.String(length=40), nullable=False),
    sa.Column('ref', sa.String(length=120), nullable=False),
    sa.Column('workspace_id', sa.String(length=40), nullable=False),
    sa.Column('profile_id', sa.String(length=80), nullable=False),
    sa.Column('version', sa.String(length=40), nullable=False),
    sa.Column('document', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('operating_status', sa.String(length=20), nullable=False),
    sa.Column('qualification_status', sa.String(length=30), nullable=False),
    sa.Column('created_by', sa.String(length=40), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('workspace_id', 'profile_id', 'version'),
    sa.UniqueConstraint('workspace_id', 'ref')
    )
    op.create_table('audit_events',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('workspace_id', sa.String(length=40), nullable=False),
    sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('actor_type', sa.String(length=20), nullable=False),
    sa.Column('actor_id', sa.String(length=80), nullable=False),
    sa.Column('command', sa.String(length=80), nullable=False),
    sa.Column('object_type', sa.String(length=40), nullable=False),
    sa.Column('object_id', sa.String(length=80), nullable=False),
    sa.Column('object_version', sa.String(length=40), nullable=True),
    sa.Column('policy_decision', sa.String(length=40), nullable=False),
    sa.Column('correlation_id', sa.String(length=80), nullable=False),
    sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_audit_workspace_time', 'audit_events', ['workspace_id', 'occurred_at'], unique=False)
    op.create_table('human_decisions',
    sa.Column('id', sa.String(length=40), nullable=False),
    sa.Column('workspace_id', sa.String(length=40), nullable=False),
    sa.Column('decision_type', sa.String(length=40), nullable=False),
    sa.Column('object_type', sa.String(length=40), nullable=False),
    sa.Column('object_id', sa.String(length=40), nullable=False),
    sa.Column('object_version', sa.Integer(), nullable=False),
    sa.Column('object_hash', sa.String(length=64), nullable=False),
    sa.Column('option', sa.String(length=20), nullable=False),
    sa.Column('rationale', sa.Text(), nullable=False),
    sa.Column('alternatives_presented', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('remaining_objections', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('consequences', sa.Text(), nullable=False),
    sa.Column('conditions', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('decided_by', sa.String(length=40), nullable=False),
    sa.Column('decided_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('idempotency_keys',
    sa.Column('id', sa.String(length=40), nullable=False),
    sa.Column('workspace_id', sa.String(length=40), nullable=False),
    sa.Column('actor_id', sa.String(length=80), nullable=False),
    sa.Column('command', sa.String(length=80), nullable=False),
    sa.Column('key', sa.String(length=200), nullable=False),
    sa.Column('request_hash', sa.String(length=64), nullable=False),
    sa.Column('response_status', sa.Integer(), nullable=False),
    sa.Column('response_body', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('workspace_id', 'actor_id', 'command', 'key')
    )
    op.create_table('institutes',
    sa.Column('id', sa.String(length=80), nullable=False),
    sa.Column('workspace_id', sa.String(length=40), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('remit', sa.Text(), nullable=False),
    sa.Column('sort_order', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('llm_invocations',
    sa.Column('id', sa.String(length=80), nullable=False),
    sa.Column('workspace_id', sa.String(length=40), nullable=False),
    sa.Column('deployment_id', sa.String(length=120), nullable=False),
    sa.Column('routing_decision_id', sa.String(length=40), nullable=False),
    sa.Column('request_hash', sa.String(length=64), nullable=False),
    sa.Column('state', sa.String(length=20), nullable=False),
    sa.Column('effective_model_id', sa.String(length=200), nullable=True),
    sa.Column('input_tokens', sa.Integer(), nullable=True),
    sa.Column('output_tokens', sa.Integer(), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('mandate_versions',
    sa.Column('id', sa.String(length=40), nullable=False),
    sa.Column('workspace_id', sa.String(length=40), nullable=False),
    sa.Column('mandate_id', sa.String(length=40), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('document', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('document_hash', sa.String(length=64), nullable=False),
    sa.Column('budget_limit_minor', sa.BigInteger(), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('state', sa.String(length=20), nullable=False),
    sa.Column('created_by', sa.String(length=40), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('mandate_id', 'version')
    )
    op.create_table('model_catalog_revisions',
    sa.Column('id', sa.String(length=40), nullable=False),
    sa.Column('workspace_id', sa.String(length=40), nullable=False),
    sa.Column('revision', sa.String(length=120), nullable=False),
    sa.Column('document', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('state', sa.String(length=20), nullable=False),
    sa.Column('created_by', sa.String(length=40), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('activated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('activated_by', sa.String(length=40), nullable=True),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('workspace_id', 'revision')
    )
    op.create_table('outbox_events',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('workspace_id', sa.String(length=40), nullable=False),
    sa.Column('event_type', sa.String(length=80), nullable=False),
    sa.Column('object_type', sa.String(length=40), nullable=False),
    sa.Column('object_id', sa.String(length=80), nullable=False),
    sa.Column('object_version', sa.String(length=40), nullable=True),
    sa.Column('project_id', sa.String(length=40), nullable=True),
    sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('dispatched_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('policy_versions',
    sa.Column('id', sa.String(length=40), nullable=False),
    sa.Column('workspace_id', sa.String(length=40), nullable=False),
    sa.Column('revision', sa.String(length=120), nullable=False),
    sa.Column('document', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('state', sa.String(length=20), nullable=False),
    sa.Column('created_by', sa.String(length=40), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('activated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('activated_by', sa.String(length=40), nullable=True),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('workspace_id', 'revision')
    )
    op.create_table('pricing_versions',
    sa.Column('id', sa.String(length=40), nullable=False),
    sa.Column('workspace_id', sa.String(length=40), nullable=False),
    sa.Column('pricing_ref', sa.String(length=120), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('input_per_million_minor', sa.BigInteger(), nullable=False),
    sa.Column('output_per_million_minor', sa.BigInteger(), nullable=False),
    sa.Column('source_note', sa.Text(), nullable=False),
    sa.Column('created_by', sa.String(length=40), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('workspace_id', 'pricing_ref')
    )
    op.create_table('skill_versions',
    sa.Column('id', sa.String(length=40), nullable=False),
    sa.Column('ref', sa.String(length=120), nullable=False),
    sa.Column('workspace_id', sa.String(length=40), nullable=False),
    sa.Column('skill_id', sa.String(length=80), nullable=False),
    sa.Column('version', sa.String(length=40), nullable=False),
    sa.Column('document', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('operating_status', sa.String(length=20), nullable=False),
    sa.Column('created_by', sa.String(length=40), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('workspace_id', 'ref'),
    sa.UniqueConstraint('workspace_id', 'skill_id', 'version')
    )
    op.create_table('users',
    sa.Column('id', sa.String(length=40), nullable=False),
    sa.Column('workspace_id', sa.String(length=40), nullable=False),
    sa.Column('username', sa.String(length=120), nullable=False),
    sa.Column('display_name', sa.String(length=200), nullable=False),
    sa.Column('password_hash', sa.Text(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('workspace_id', 'username')
    )
    op.create_table('budget_accounts',
    sa.Column('id', sa.String(length=40), nullable=False),
    sa.Column('workspace_id', sa.String(length=40), nullable=False),
    sa.Column('parent_id', sa.String(length=40), nullable=True),
    sa.Column('mandate_version_id', sa.String(length=40), nullable=True),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('limit_minor', sa.BigInteger(), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('protected_kind', sa.String(length=40), nullable=True),
    sa.Column('depth', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['mandate_version_id'], ['mandate_versions.id'], ),
    sa.ForeignKeyConstraint(['parent_id'], ['budget_accounts.id'], ),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('grants',
    sa.Column('id', sa.String(length=40), nullable=False),
    sa.Column('workspace_id', sa.String(length=40), nullable=False),
    sa.Column('user_id', sa.String(length=40), nullable=False),
    sa.Column('role', sa.String(length=40), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('workspace_id', 'user_id', 'role')
    )
    op.create_table('sessions',
    sa.Column('id', sa.String(length=128), nullable=False),
    sa.Column('user_id', sa.String(length=40), nullable=False),
    sa.Column('csrf_token', sa.String(length=128), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('projects',
    sa.Column('id', sa.String(length=40), nullable=False),
    sa.Column('workspace_id', sa.String(length=40), nullable=False),
    sa.Column('mandate_version_id', sa.String(length=40), nullable=False),
    sa.Column('institute_id', sa.String(length=80), nullable=False),
    sa.Column('budget_account_id', sa.String(length=40), nullable=False),
    sa.Column('title', sa.String(length=300), nullable=False),
    sa.Column('work_state', sa.String(length=20), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['budget_account_id'], ['budget_accounts.id'], ),
    sa.ForeignKeyConstraint(['institute_id'], ['institutes.id'], ),
    sa.ForeignKeyConstraint(['mandate_version_id'], ['mandate_versions.id'], ),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('reservations',
    sa.Column('id', sa.String(length=40), nullable=False),
    sa.Column('workspace_id', sa.String(length=40), nullable=False),
    sa.Column('account_id', sa.String(length=40), nullable=False),
    sa.Column('task_id', sa.String(length=40), nullable=True),
    sa.Column('purpose', sa.String(length=20), nullable=False),
    sa.Column('amount_minor', sa.BigInteger(), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('state', sa.String(length=20), nullable=False),
    sa.Column('settled_minor', sa.BigInteger(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['budget_accounts.id'], ),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_reservations_account_state', 'reservations', ['account_id', 'state'], unique=False)
    op.create_table('cost_entries',
    sa.Column('id', sa.String(length=40), nullable=False),
    sa.Column('workspace_id', sa.String(length=40), nullable=False),
    sa.Column('account_id', sa.String(length=40), nullable=False),
    sa.Column('reservation_id', sa.String(length=40), nullable=True),
    sa.Column('invocation_id', sa.String(length=80), nullable=True),
    sa.Column('amount_minor', sa.BigInteger(), nullable=True),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('provider_currency', sa.String(length=3), nullable=True),
    sa.Column('fx_rate', sa.String(length=40), nullable=True),
    sa.Column('state', sa.String(length=20), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['budget_accounts.id'], ),
    sa.ForeignKeyConstraint(['reservation_id'], ['reservations.id'], ),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('tasks',
    sa.Column('id', sa.String(length=40), nullable=False),
    sa.Column('workspace_id', sa.String(length=40), nullable=False),
    sa.Column('project_id', sa.String(length=40), nullable=False),
    sa.Column('parent_task_id', sa.String(length=40), nullable=True),
    sa.Column('budget_account_id', sa.String(length=40), nullable=False),
    sa.Column('task_need', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('state', sa.String(length=20), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['budget_account_id'], ['budget_accounts.id'], ),
    sa.ForeignKeyConstraint(['parent_task_id'], ['tasks.id'], ),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('route_proposals',
    sa.Column('id', sa.String(length=40), nullable=False),
    sa.Column('workspace_id', sa.String(length=40), nullable=False),
    sa.Column('task_id', sa.String(length=40), nullable=False),
    sa.Column('proposal', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('assessments', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('outcome', sa.String(length=20), nullable=False),
    sa.Column('error_code', sa.String(length=60), nullable=True),
    sa.Column('routing_decision_id', sa.String(length=40), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], ),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('routing_decisions',
    sa.Column('id', sa.String(length=40), nullable=False),
    sa.Column('workspace_id', sa.String(length=40), nullable=False),
    sa.Column('task_id', sa.String(length=40), nullable=False),
    sa.Column('document', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('state', sa.String(length=20), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], ),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('run_attempts',
    sa.Column('id', sa.String(length=40), nullable=False),
    sa.Column('workspace_id', sa.String(length=40), nullable=False),
    sa.Column('task_id', sa.String(length=40), nullable=False),
    sa.Column('attempt_no', sa.Integer(), nullable=False),
    sa.Column('routing_decision_id', sa.String(length=40), nullable=False),
    sa.Column('agent_profile_version_id', sa.String(length=80), nullable=False),
    sa.Column('state', sa.String(length=20), nullable=False),
    sa.Column('end_reason', sa.String(length=30), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['routing_decision_id'], ['routing_decisions.id'], ),
    sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], ),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('task_id', 'attempt_no')
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION audit_events_immutable() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'audit_events is append-only (ADR-0004)';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_events_no_update_delete
        BEFORE UPDATE OR DELETE ON audit_events
        FOR EACH ROW EXECUTE FUNCTION audit_events_immutable();
        """
    )
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": APP_ROLE}).scalar():
        op.execute(f'GRANT USAGE ON SCHEMA public TO "{APP_ROLE}"')
        op.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO "{APP_ROLE}"')
        op.execute(f'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO "{APP_ROLE}"')
        op.execute(f'REVOKE UPDATE, DELETE ON audit_events FROM "{APP_ROLE}"')


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_events_no_update_delete ON audit_events")
    op.execute("DROP FUNCTION IF EXISTS audit_events_immutable()")
    for table in reversed(S0_TABLES):
        op.drop_table(table)
