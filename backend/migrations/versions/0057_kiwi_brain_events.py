"""kiwi brain: the durable decision timeline

Revision ID: 0057
Revises: 0056
Create Date: 2026-10-16 08:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0057'
down_revision: str | None = '0056'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('kb_events',
    sa.Column('at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('member_id', sa.Integer(), nullable=True),
    sa.Column('signal_id', sa.Integer(), nullable=True),
    sa.Column('coin', sa.String(length=32), nullable=True),
    sa.Column('title', sa.String(length=240), nullable=False),
    sa.Column('data', sa.JSON(), nullable=False),
    sa.Column('source', sa.String(length=64), nullable=True),
    sa.Column('source_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['member_id'], ['members.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['signal_id'], ['ts_signals.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('kb_events', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_kb_events_at'), ['at'], unique=False)
        batch_op.create_index('ix_kb_events_member_id_id', ['member_id', 'id'], unique=False)
        batch_op.create_index('ix_kb_events_kind_at', ['kind', 'at'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('kb_events', schema=None) as batch_op:
        batch_op.drop_index('ix_kb_events_kind_at')
        batch_op.drop_index('ix_kb_events_member_id_id')
        batch_op.drop_index(batch_op.f('ix_kb_events_at'))
    op.drop_table('kb_events')
