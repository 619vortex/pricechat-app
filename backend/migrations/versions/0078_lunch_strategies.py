"""Lunch Bot strategies: each paper trade names its strategy; a launch can be taken once per strategy

Revision ID: 0078
Revises: 0077
Create Date: 2026-10-21 19:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0078'
down_revision: str | None = '0077'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("arb_lunch_trades") as b:  # batch: SQLite (the laptop) rebuilds the table to change a constraint
        b.add_column(sa.Column("strategy", sa.String(16), nullable=False, server_default="starter"))
        b.drop_constraint("uq_arb_lunch_trades_mint", type_="unique")
        b.create_unique_constraint("uq_arb_lunch_trades_mint_strategy", ["mint", "strategy"])


def downgrade() -> None:
    op.execute("DELETE FROM arb_lunch_trades WHERE strategy <> 'starter'")  # one trade per mint again: the other strategies' rows go
    with op.batch_alter_table("arb_lunch_trades") as b:
        b.drop_constraint("uq_arb_lunch_trades_mint_strategy", type_="unique")
        b.create_unique_constraint("uq_arb_lunch_trades_mint", ["mint"])
        b.drop_column("strategy")
