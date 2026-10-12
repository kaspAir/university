"""S3 review, part 3: bootstrap approvals are bound to a checking role; legacy unbound records never become approvals.

  role          reviewer | evaluator for bootstrap approvals, producer for combination evidence
  legacy_unbound model-evaluated records from before 0007 that carried neither profile nor skills. Migration 0007 had classified them as
                 `bootstrap`; they are not human approvals and are kept for history only (`_record_applies` never accepts them).

Human bootstrap records from before this revision keep kind `bootstrap` with role NULL: they apply to nothing until the research director
re-enters them with an explicit role. Nothing is inferred.

Revision ID: 0008_s3_role_bound_bootstrap
Revises: 0007_s3_record_kinds
Create Date: 2026-10-12
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008_s3_role_bound_bootstrap"
down_revision = "0007_s3_record_kinds"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("qualification_records", sa.Column("role", sa.String(20), nullable=True))
    op.execute("UPDATE qualification_records SET role = 'producer' WHERE kind = 'combination'")
    op.execute("UPDATE qualification_records SET kind = 'legacy_unbound' WHERE kind = 'bootstrap' AND evaluated_by_kind <> 'human'")


def downgrade() -> None:
    op.execute("UPDATE qualification_records SET kind = 'bootstrap' WHERE kind = 'legacy_unbound'")
    op.drop_column("qualification_records", "role")
