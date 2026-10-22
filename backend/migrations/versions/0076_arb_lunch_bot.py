"""Earn › Arbitrage: Lunch Bot paper trades, graduation gaps and daily counters

Revision ID: 0076
Revises: 0075
Create Date: 2026-10-21 16:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0076'
down_revision: str | None = '0075'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _stamps() -> list[sa.Column]:
    return [sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())]


def upgrade() -> None:
    op.create_table(
        "arb_lunch_trades",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("mint", sa.String(64), nullable=False),
        sa.Column("bonding_curve", sa.String(64), nullable=False),
        sa.Column("symbol", sa.String(40), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("image_uri", sa.String(500)),
        sa.Column("launched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(12), nullable=False, server_default="open"),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("entry_sol", sa.Float(), nullable=False),
        sa.Column("tokens", sa.Float(), nullable=False),
        sa.Column("entry_price", sa.Float(), nullable=False),
        sa.Column("entry_mc_sol", sa.Float(), nullable=False),
        sa.Column("last_price", sa.Float()),
        sa.Column("last_value_sol", sa.Float()),
        sa.Column("peak_value_sol", sa.Float()),
        sa.Column("last_checked_at", sa.DateTime(timezone=True)),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.Column("exit_sol", sa.Float()),
        sa.Column("exit_reason", sa.String(16)),
        sa.Column("pnl_sol", sa.Float()),
        sa.Column("detail", sa.JSON(), nullable=False),
        *_stamps(),
        sa.UniqueConstraint("mint", name="uq_arb_lunch_trades_mint"),
    )
    op.create_index("ix_arb_lunch_trades_status", "arb_lunch_trades", ["status"])
    op.create_table(
        "arb_lunch_graduations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("mint", sa.String(64), nullable=False),
        sa.Column("symbol", sa.String(40), nullable=False),
        sa.Column("curve_price", sa.Float(), nullable=False),
        sa.Column("pool_price", sa.Float()),
        sa.Column("gap_pct", sa.Float()),
        sa.Column("pool_address", sa.String(64)),
        sa.Column("pool_dex", sa.String(40)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("priced_at", sa.DateTime(timezone=True)),
        *_stamps(),
        sa.UniqueConstraint("mint", name="uq_arb_lunch_graduations_mint"),
    )
    op.create_table(
        "arb_lunch_days",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("day", sa.String(10), nullable=False),
        sa.Column("launches_seen", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("entries", sa.Integer(), nullable=False, server_default="0"),
        *_stamps(),
        sa.UniqueConstraint("day", name="uq_arb_lunch_days_day"),
    )


def downgrade() -> None:
    op.drop_table("arb_lunch_days")
    op.drop_table("arb_lunch_graduations")
    op.drop_index("ix_arb_lunch_trades_status", table_name="arb_lunch_trades")
    op.drop_table("arb_lunch_trades")
