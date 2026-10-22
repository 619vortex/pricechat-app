"""growth dashboard: audience snapshots (X followers, Telegram members)

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-03 12:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0009'
down_revision: str | None = '0008'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('growth_snapshots',
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('subject', sa.String(length=64), nullable=False),
    sa.Column('label', sa.String(length=256), nullable=False),
    sa.Column('taken_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('followers', sa.Integer(), nullable=True),
    sa.Column('following', sa.Integer(), nullable=True),
    sa.Column('posts', sa.Integer(), nullable=True),
    sa.Column('members', sa.Integer(), nullable=True),
    sa.Column('messages', sa.Integer(), nullable=True),
    sa.Column('source', sa.String(length=32), nullable=False),
    sa.Column('id', sa.Integer(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_growth_snapshots_kind_subject_taken', 'growth_snapshots', ['kind', 'subject', 'taken_at'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_growth_snapshots_kind_subject_taken', table_name='growth_snapshots')
    op.drop_table('growth_snapshots')
