"""pack giveaway: Level-up packs (one boosted pack per Kiwi Passport level)

Revision ID: 0036
Revises: 0035
Create Date: 2026-10-09 18:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0036'
down_revision: str | None = '0035'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('member_pack_opens', sa.Column('bonus', sa.Boolean(), server_default=sa.false(), nullable=False))


def downgrade() -> None:
    op.drop_column('member_pack_opens', 'bonus')
