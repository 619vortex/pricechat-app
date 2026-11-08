"""Telegram group rules: the strikes behind the 12 h / 24 h / permanent mute ladder (app/telegram/moderation.py).

Revision ID: 0106_telegram_strikes
Revises: 0105_vpn_server_testers_only
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0106_telegram_strikes'
down_revision: str | None = '0105_vpn_server_testers_only'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tg_strikes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("user_name", sa.String(128)),
        sa.Column("rule", sa.Integer()),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("by", sa.String(32), nullable=False),
        sa.Column("muted_until", sa.DateTime(timezone=True)),
        sa.Column("permanent", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("cleared", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_tg_strikes_chat_user", "tg_strikes", ["chat_id", "user_id"])


def downgrade() -> None:
    op.drop_index("ix_tg_strikes_chat_user", table_name="tg_strikes")
    op.drop_table("tg_strikes")
