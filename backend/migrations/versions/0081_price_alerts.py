"""Telegram price alerts: /alert BTC 85000 and the "Alert me at entry" button under /signal (one private message, once)

Revision ID: 0081_price_alerts
Revises: 0078
Create Date: 2026-10-22 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0081_price_alerts'
down_revision: str | None = '0079_owner_alerts'  # re-chained to 0080_signal_ideas once that lands
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ts_price_alerts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("coin", sa.String(32), nullable=False),
        sa.Column("kind", sa.String(5), nullable=False),
        sa.Column("price", sa.Float(), nullable=False),
        sa.Column("price_hi", sa.Float(), nullable=True),
        sa.Column("last_price", sa.Float(), nullable=False),
        sa.Column("plan", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(10), nullable=False, server_default="active"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fired_price", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_ts_price_alerts_status", "ts_price_alerts", ["status"])
    op.create_index("ix_ts_price_alerts_user", "ts_price_alerts", ["telegram_user_id"])


def downgrade() -> None:
    op.drop_index("ix_ts_price_alerts_user", table_name="ts_price_alerts")
    op.drop_index("ix_ts_price_alerts_status", table_name="ts_price_alerts")
    op.drop_table("ts_price_alerts")
