"""Telegram bot: community ideas, Price Chat's own open-interest history, and where main-chat chart replies go

- ts_ideas: /idea BTC long ... (members' opinions, separate from signals, never traded)
- ts_oi_snapshots: open interest + funding of the top markets every 15 minutes (Hyperliquid keeps no OI history)
- ts_settings.chart_routing: "anywhere" (as before) | "bot_chat"

Revision ID: 0068
Revises: 0067
Create Date: 2026-10-19 08:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0068'
down_revision: str | None = '0067'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("ts_settings", sa.Column("chart_routing", sa.String(16), nullable=False, server_default="anywhere"))
    op.create_table(
        "ts_ideas",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("coin", sa.String(32), nullable=False),
        sa.Column("side", sa.String(5), nullable=False),
        sa.Column("price", sa.Float(), nullable=False),
        sa.Column("target", sa.Float()),
        sa.Column("invalidation", sa.Float()),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("author", sa.String(64)),
        sa.Column("member_id", sa.Integer(), sa.ForeignKey("members.id", ondelete="SET NULL")),
        sa.Column("chat_id", sa.BigInteger()),
        sa.Column("status", sa.String(10), nullable=False, server_default="open"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_ts_ideas_coin_id", "ts_ideas", ["coin", "id"])
    op.create_index("ix_ts_ideas_tg_user", "ts_ideas", ["telegram_user_id"])
    op.create_table(
        "ts_oi_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("coin", sa.String(32), nullable=False),
        sa.Column("mark", sa.Float(), nullable=False),
        sa.Column("oi_usd", sa.Float(), nullable=False),
        sa.Column("funding", sa.Float(), nullable=False),
    )
    op.create_index("ix_ts_oi_coin_at", "ts_oi_snapshots", ["coin", "at"])


def downgrade() -> None:
    op.drop_index("ix_ts_oi_coin_at", "ts_oi_snapshots")
    op.drop_table("ts_oi_snapshots")
    op.drop_index("ix_ts_ideas_tg_user", "ts_ideas")
    op.drop_index("ix_ts_ideas_coin_id", "ts_ideas")
    op.drop_table("ts_ideas")
    op.drop_column("ts_settings", "chart_routing")
