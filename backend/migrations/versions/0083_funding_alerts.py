"""Earn › Funding Arb alerts: the last alert posted per coin (its cooldown survives restarts)

Revision ID: 0083_funding_alerts
Revises: 0078
Create Date: 2026-10-22 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0083_funding_alerts'
down_revision: str | None = '0082_referral_clicks'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "arb_funding_alerts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("long_venue", sa.String(24), nullable=False),
        sa.Column("short_venue", sa.String(24), nullable=False),
        sa.Column("net_apr_pct", sa.Float(), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("symbol", name="uq_arb_funding_alerts_symbol"),
    )


def downgrade() -> None:
    op.drop_table("arb_funding_alerts")
