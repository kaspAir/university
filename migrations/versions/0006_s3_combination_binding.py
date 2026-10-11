"""S3 review: bind qualification records to the combination profile + skills (+ canonical model + task class).

Revision ID: 0006_s3_combination_binding
Revises: 0005_s3_factory
Create Date: 2026-10-12
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006_s3_combination_binding"
down_revision = "0005_s3_factory"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("qualification_records", sa.Column("profile_ref", sa.String(120), nullable=True))  # NULL = applies to any profile (bootstrap/human)
    op.add_column("qualification_records", sa.Column("skill_refs", postgresql.JSONB, nullable=True))  # NULL = no skill binding
    op.add_column("evaluation_runs", sa.Column("composition_key", sa.String(400), nullable=True))
    op.add_column("evaluation_runs", sa.Column("baseline_comparable", sa.Boolean, nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("evaluation_runs", "baseline_comparable")
    op.drop_column("evaluation_runs", "composition_key")
    op.drop_column("qualification_records", "skill_refs")
    op.drop_column("qualification_records", "profile_ref")
