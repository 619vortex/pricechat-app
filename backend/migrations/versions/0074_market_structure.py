"""Market structure: order-book snapshots (liquidity heatmaps) and exchange-reported liquidations

Revision ID: 0074
Revises: 0073
Create Date: 2026-10-20 15:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0074'
down_revision: str | None = '0073'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ts_book_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("coin", sa.String(32), nullable=False),
        sa.Column("mid", sa.Float(), nullable=False),
        sa.Column("step", sa.Float(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
    )
    op.create_index("ix_ts_book_coin_at", "ts_book_snapshots", ["coin", "at"])
    op.create_table(
        "ts_liquidations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("venue", sa.String(24), nullable=False),
        sa.Column("coin", sa.String(32), nullable=False),
        sa.Column("t", sa.BigInteger(), nullable=False),
        sa.Column("side", sa.String(8), nullable=False),
        sa.Column("px", sa.Float(), nullable=False),
        sa.Column("qty", sa.Float(), nullable=False),
        sa.Column("usd", sa.Float(), nullable=False),
        sa.UniqueConstraint("venue", "coin", "t", "side", "px", "qty", name="uq_ts_liq_fill"),
    )
    op.create_index("ix_ts_liq_coin_t", "ts_liquidations", ["coin", "t"])


def downgrade() -> None:
    op.drop_index("ix_ts_liq_coin_t", "ts_liquidations")
    op.drop_table("ts_liquidations")
    op.drop_index("ix_ts_book_coin_at", "ts_book_snapshots")
    op.drop_table("ts_book_snapshots")
