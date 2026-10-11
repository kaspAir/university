"""S3 review, part 2: qualification records carry an explicit kind.

  bootstrap   - a human-defined role approval (canonical model + task class), e.g. the first evaluator or reviewer
  combination - a factory result binding profile AND the actually used skills AND the canonical model AND the task class

Revision ID: 0007_s3_record_kinds
Revises: 0006_s3_combination_binding
Create Date: 2026-10-12
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007_s3_record_kinds"
down_revision = "0006_s3_combination_binding"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("qualification_records", sa.Column("kind", sa.String(20), nullable=False, server_default="bootstrap"))
    op.execute("UPDATE qualification_records SET kind = 'combination' WHERE profile_ref IS NOT NULL OR skill_refs IS NOT NULL")


def downgrade() -> None:
    op.drop_column("qualification_records", "kind")
