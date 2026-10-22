"""pokemon holdings: why a value is what it is (diagnostics) and when the card's prices were last fetched

Revision ID: 0027
Revises: 0026
Create Date: 2026-10-09 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0027'
down_revision: str | None = '0026'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # additive only: existing holdings keep every value; the next valuation fills the reason
    op.add_column('pokemon_holdings', sa.Column('value_reason', sa.String(length=32), nullable=True))
    op.add_column('pokemon_holdings', sa.Column('prices_checked_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('pokemon_holdings', 'prices_checked_at')
    op.drop_column('pokemon_holdings', 'value_reason')
