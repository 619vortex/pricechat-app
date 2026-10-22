"""trading os: announce signal opens / closes in the main community chat too

Revision ID: 0064
Revises: 0063
Create Date: 2026-10-17 18:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0064'
down_revision: str | None = '0063'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('ts_settings', schema=None) as batch_op:
        batch_op.add_column(sa.Column('main_chat', sa.String(length=64), server_default='@cryptopricechat', nullable=False))


def downgrade() -> None:
    with op.batch_alter_table('ts_settings', schema=None) as batch_op:
        batch_op.drop_column('main_chat')
