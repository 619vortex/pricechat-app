"""member x tokens, telegram daily cap

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-04 21:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0015'
down_revision: str | None = '0014'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('member_x_tokens',
    sa.Column('member_id', sa.Integer(), nullable=False),
    sa.Column('x_user_id', sa.String(length=64), nullable=False),
    sa.Column('access_token', sa.Text(), nullable=False),
    sa.Column('refresh_token', sa.Text(), nullable=True),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('scopes', sa.String(length=256), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('last_error', sa.String(length=256), nullable=True),
    sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['member_id'], ['members.id'], name=op.f('fk_member_x_tokens_member_id_members'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_member_x_tokens')),
    sa.UniqueConstraint('member_id', name=op.f('uq_member_x_tokens_member_id'))
    )
    with op.batch_alter_table('reputation_settings', schema=None) as batch_op:
        batch_op.add_column(sa.Column('telegram_daily_cap', sa.Integer(), server_default='10', nullable=False))


def downgrade() -> None:
    with op.batch_alter_table('reputation_settings', schema=None) as batch_op:
        batch_op.drop_column('telegram_daily_cap')
    op.drop_table('member_x_tokens')
