"""Index tr_positions.signal_id: the members' signal feed counts who is in each signal on every poll

Revision ID: 0095_tr_positions_signal_index
Revises: 0094_kalshi_links
"""

from collections.abc import Sequence

from alembic import op


revision: str = '0095_tr_positions_signal_index'
down_revision: str | None = '0094_kalshi_links'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_tr_positions_signal", "tr_positions", ["signal_id"])


def downgrade() -> None:
    op.drop_index("ix_tr_positions_signal", table_name="tr_positions")
