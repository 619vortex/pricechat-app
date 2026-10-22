"""pilot: last successful check for X monitoring rules and watched accounts

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-03 08:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0005'
down_revision: str | None = '0004'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('monitor_rules', schema=None) as batch_op:
        batch_op.add_column(sa.Column('last_success_at', sa.DateTime(timezone=True), nullable=True))
    with op.batch_alter_table('watched_accounts', schema=None) as batch_op:
        batch_op.add_column(sa.Column('last_success_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('watched_accounts', schema=None) as batch_op:
        batch_op.drop_column('last_success_at')
    with op.batch_alter_table('monitor_rules', schema=None) as batch_op:
        batch_op.drop_column('last_success_at')
