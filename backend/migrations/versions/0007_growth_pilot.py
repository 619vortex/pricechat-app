"""growth pilot: telegram account mode, early community signals, preference controls, usage counters

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-03 09:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0007'
down_revision: str | None = '0006'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('telegram_account',
    sa.Column('api_id', sa.Integer(), nullable=True),
    sa.Column('status', sa.String(length=32), server_default='not_configured', nullable=False),
    sa.Column('user_id', sa.BigInteger(), nullable=True),
    sa.Column('username', sa.String(length=128), nullable=True),
    sa.Column('display_name', sa.String(length=256), nullable=True),
    sa.Column('phone_hint', sa.String(length=16), nullable=True),
    sa.Column('authorized_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_success_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.Column('last_error_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('requests_total', sa.Integer(), server_default='0', nullable=False),
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('operator_preferences',
    sa.Column('key', sa.String(length=96), nullable=False),
    sa.Column('state', sa.String(length=16), server_default='active', nullable=False),
    sa.Column('reset_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('key')
    )
    op.create_table('usage_counters',
    sa.Column('day', sa.String(length=10), nullable=False),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('key', sa.String(length=64), nullable=False),
    sa.Column('count', sa.Integer(), server_default='0', nullable=False),
    sa.Column('seconds', sa.Float(), server_default='0', nullable=False),
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('day', 'kind', 'key')
    )
    op.create_table('discovery_ignores',
    sa.Column('profile_id', sa.Integer(), nullable=False),
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['profile_id'], ['social_profiles.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('profile_id')
    )
    with op.batch_alter_table('telegram_sources', schema=None) as batch_op:
        batch_op.add_column(sa.Column('via', sa.String(length=16), server_default='bot', nullable=False))
    with op.batch_alter_table('cross_signals', schema=None) as batch_op:
        batch_op.add_column(sa.Column('early_signal', sa.Boolean(), server_default=sa.false(), nullable=False))
        batch_op.add_column(sa.Column('lead_minutes', sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('cross_signals', schema=None) as batch_op:
        batch_op.drop_column('lead_minutes')
        batch_op.drop_column('early_signal')
    with op.batch_alter_table('telegram_sources', schema=None) as batch_op:
        batch_op.drop_column('via')
    op.drop_table('discovery_ignores')
    op.drop_table('usage_counters')
    op.drop_table('operator_preferences')
    op.drop_table('telegram_account')
