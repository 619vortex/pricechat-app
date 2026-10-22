"""kiwi arena: classes and saved heroes (one per member and class)

Revision ID: 0065
Revises: 0064
Create Date: 2026-10-17 20:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0065'
down_revision: str | None = '0064'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'arena_heroes',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('member_id', sa.Integer(), sa.ForeignKey('members.id', ondelete='CASCADE'), nullable=False),
        sa.Column('hero_class', sa.String(length=12), nullable=False),
        sa.Column('level', sa.Integer(), server_default='1', nullable=False),
        sa.Column('xp', sa.Integer(), server_default='0', nullable=False),
        sa.Column('runs', sa.Integer(), server_default='0', nullable=False),
        sa.Column('kills', sa.Integer(), server_default='0', nullable=False),
        sa.Column('best_wave', sa.Integer(), server_default='0', nullable=False),
        sa.UniqueConstraint('member_id', 'hero_class', name='ux_arena_heroes_member_class'),
    )
    with op.batch_alter_table('arena_runs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('hero_class', sa.String(length=12), server_default='knight', nullable=False))


def downgrade() -> None:
    with op.batch_alter_table('arena_runs', schema=None) as batch_op:
        batch_op.drop_column('hero_class')
    op.drop_table('arena_heroes')
