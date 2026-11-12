"""/signal cards: every card shown (LONG, SHORT, WAIT) for the public record and the card buttons (app/tradingos/signalcard.py).

Revision ID: 0109_signal_cards
Revises: 0108_newsdesk_render_jobs
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0109_signal_cards'
down_revision: str | None = '0108_newsdesk_render_jobs'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ts_signal_cards",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("venue", sa.String(16), nullable=False),
        sa.Column("coin", sa.String(64), nullable=False),
        sa.Column("tag", sa.String(64), nullable=False),
        sa.Column("timeframe", sa.String(4), nullable=False),
        sa.Column("verdict", sa.String(5), nullable=False),
        sa.Column("price", sa.Float(), nullable=False),
        sa.Column("confidence", sa.String(8), nullable=False, server_default=""),
        sa.Column("plan", sa.JSON()),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("idea_id", sa.Integer()),
        sa.Column("chat_type", sa.String(8), nullable=False),
        sa.Column("status", sa.String(8), nullable=False, server_default="OPEN"),
        sa.Column("result", sa.JSON()),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_ts_signal_cards_status", "ts_signal_cards", ["status"])
    op.create_index("ix_ts_signal_cards_dedupe", "ts_signal_cards", ["coin", "timeframe", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_ts_signal_cards_dedupe", "ts_signal_cards")
    op.drop_index("ix_ts_signal_cards_status", "ts_signal_cards")
    op.drop_table("ts_signal_cards")
