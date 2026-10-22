"""hyperliquid affiliate wallet: hourly snapshots

Revision ID: 0059
Revises: 0058
Create Date: 2026-10-16 16:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0059'
down_revision: str | None = '0058'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('tb_affiliate_snapshots',
    sa.Column('wallet', sa.String(length=42), nullable=False),
    sa.Column('data', sa.JSON(), nullable=False),
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('tb_affiliate_snapshots', schema=None) as batch_op:
        batch_op.create_index('ix_tb_affiliate_snapshots_wallet_created', ['wallet', 'created_at'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('tb_affiliate_snapshots', schema=None) as batch_op:
        batch_op.drop_index('ix_tb_affiliate_snapshots_wallet_created')
    op.drop_table('tb_affiliate_snapshots')
