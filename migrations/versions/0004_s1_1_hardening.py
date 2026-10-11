"""S1.1 hardening: task holds, route-bound reservations, review consistency flag.

Revision ID: 0004_s1_1_hardening
Revises: 0003_s2_sources_claims_dossier
Create Date: 2026-10-11
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004_s1_1_hardening"
down_revision = "0003_s2_sources_claims_dossier"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("hold_reason", sa.Text, nullable=True))
    op.add_column("tasks", sa.Column("hold_since", sa.DateTime(timezone=True), nullable=True))
    op.add_column("reservations", sa.Column("routing_decision_id", sa.String(40), nullable=True))
    op.create_index("ix_reservations_decision_purpose", "reservations", ["routing_decision_id", "purpose"])
    op.add_column("review_reports", sa.Column("consistent", sa.Boolean, nullable=False, server_default=sa.true()))


def downgrade() -> None:
    op.drop_column("review_reports", "consistent")
    op.drop_index("ix_reservations_decision_purpose", table_name="reservations")
    op.drop_column("reservations", "routing_decision_id")
    op.drop_column("tasks", "hold_since")
    op.drop_column("tasks", "hold_reason")
