"""Referral click tracking: one row per click on a tracked referral link (placement, time, coarse source; no IP / UA / member)

Revision ID: 0082_referral_clicks
Revises: 0078
Create Date: 2026-10-22 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0082_referral_clicks'
down_revision: str | None = '0078'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tb_referral_clicks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("placement", sa.String(32), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
    )
    op.create_index("ix_tb_referral_clicks_at", "tb_referral_clicks", ["at"])


def downgrade() -> None:
    op.drop_index("ix_tb_referral_clicks_at", "tb_referral_clicks")
    op.drop_table("tb_referral_clicks")
