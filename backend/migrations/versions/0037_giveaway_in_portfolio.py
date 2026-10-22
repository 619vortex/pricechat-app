"""pack giveaway: opt-in to show the virtual pulls in the member's Pokémon Portfolio (a separate, virtual section)

Revision ID: 0037
Revises: 0036
Create Date: 2026-10-10 09:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0037'
down_revision: str | None = '0036'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('members', sa.Column('giveaway_in_portfolio', sa.Boolean(), server_default=sa.false(), nullable=False))


def downgrade() -> None:
    op.drop_column('members', 'giveaway_in_portfolio')
