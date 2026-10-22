"""Owner alerts: where the owner's private Telegram alerts go (one row) and each alert key's 6 h cooldown

Revision ID: 0079_owner_alerts
Revises: 0078
Create Date: 2026-10-22 10:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0079_owner_alerts'
down_revision: str | None = '0078'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "owner_alert_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("telegram_chat", sa.String(64), nullable=False, server_default=""),
    )
    op.create_table(
        "owner_alerts",
        sa.Column("key", sa.String(128), primary_key=True),
        sa.Column("last_sent_at", sa.DateTime(timezone=True)),
        sa.Column("sent_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("open", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_table("owner_alerts")
    op.drop_table("owner_alert_settings")
