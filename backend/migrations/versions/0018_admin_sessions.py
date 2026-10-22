"""admin sessions: the owner's login for the internal dashboard and admin APIs

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-06 16:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0018'
down_revision: str | None = '0017'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('admin_sessions',
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('username', sa.String(length=64), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_admin_sessions')),
    sa.UniqueConstraint('token_hash', name=op.f('uq_admin_sessions_token_hash'))
    )
    with op.batch_alter_table('admin_sessions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_admin_sessions_expires_at'), ['expires_at'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('admin_sessions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_admin_sessions_expires_at'))
    op.drop_table('admin_sessions')
