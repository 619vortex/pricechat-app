"""/signal track record and the daily brief

- ts_signal_ideas: every plan /signal showed (no user ids), graded on real candles (FILLED → TARGET / STOP, or EXPIRED)
- ts_settings.brief_enabled / brief_hour / brief_sent_on: the daily brief in the signal group, once a day

Revision ID: 0080_signal_ideas
Revises: 0078
Create Date: 2026-10-22 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0080_signal_ideas'
down_revision: str | None = '0078'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("ts_settings", sa.Column("brief_enabled", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("ts_settings", sa.Column("brief_hour", sa.Integer(), nullable=False, server_default="8"))
    op.add_column("ts_settings", sa.Column("brief_sent_on", sa.String(10)))
    op.create_table(
        "ts_signal_ideas",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("venue", sa.String(16), nullable=False),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("coin", sa.String(64), nullable=False),
        sa.Column("tag", sa.String(64), nullable=False),
        sa.Column("timeframe", sa.String(4), nullable=False),
        sa.Column("side", sa.String(5), nullable=False),
        sa.Column("entry_lo", sa.Float(), nullable=False),
        sa.Column("entry_hi", sa.Float(), nullable=False),
        sa.Column("stop", sa.Float(), nullable=False),
        sa.Column("target", sa.Float(), nullable=False),
        sa.Column("idea", sa.Text(), nullable=False),
        sa.Column("family", sa.String(12), nullable=False),
        sa.Column("full", sa.Boolean(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("chat_type", sa.String(8), nullable=False),
        sa.Column("status", sa.String(8), nullable=False, server_default="OPEN"),
        sa.Column("filled_at", sa.DateTime(timezone=True)),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("r", sa.Float()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_ts_signal_ideas_status", "ts_signal_ideas", ["status"])
    op.create_index("ix_ts_signal_ideas_dedupe", "ts_signal_ideas", ["coin", "timeframe", "side", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_ts_signal_ideas_dedupe", "ts_signal_ideas")
    op.drop_index("ix_ts_signal_ideas_status", "ts_signal_ideas")
    op.drop_table("ts_signal_ideas")
    op.drop_column("ts_settings", "brief_sent_on")
    op.drop_column("ts_settings", "brief_hour")
    op.drop_column("ts_settings", "brief_enabled")
