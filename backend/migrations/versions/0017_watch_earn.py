"""watch & earn: configurable play time before a Passport video pays XP

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-06 12:20:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0017'
down_revision: str | None = '0016'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('reputation_settings', schema=None) as batch_op:
        batch_op.add_column(sa.Column('watch_seconds', sa.Integer(), server_default='30', nullable=False))


def downgrade() -> None:
    with op.batch_alter_table('reputation_settings', schema=None) as batch_op:
        batch_op.drop_column('watch_seconds')
