"""Kalshi bot reports (Admin › Trading › Kalshi bot): kalshi_bot (the connection, one row) + kalshi_reports

Revision ID: 0093_kalshi_bot
Revises: 0092_tiktok_webhooks
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0093_kalshi_bot'
down_revision: str | None = '0092_tiktok_webhooks'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "kalshi_bot",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("token_hash", sa.String(64)),
        sa.Column("token_created_at", sa.DateTime(timezone=True)),
        sa.Column("last_seen_at", sa.DateTime(timezone=True)),
        sa.Column("last_bot", sa.JSON()),
    )
    op.create_table(
        "kalshi_reports",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("env", sa.String(16), nullable=False, server_default=""),
        sa.Column("payload", sa.JSON(), nullable=False),
    )
    op.create_index("ix_kalshi_reports_received_at", "kalshi_reports", ["received_at"])
    op.create_index("ix_kalshi_reports_kind", "kalshi_reports", ["kind"])


def downgrade() -> None:
    op.drop_index("ix_kalshi_reports_kind", "kalshi_reports")
    op.drop_index("ix_kalshi_reports_received_at", "kalshi_reports")
    op.drop_table("kalshi_reports")
    op.drop_table("kalshi_bot")
