"""invite your crew: telegram friend referrals

Revision ID: 0056
Revises: 0055
Create Date: 2026-10-15 21:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0056'
down_revision: str | None = '0055'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('member_crew_referrals',
    sa.Column('referrer_id', sa.Integer(), nullable=True),
    sa.Column('tg_user_id', sa.BigInteger(), nullable=False),
    sa.Column('code', sa.String(length=24), nullable=False),
    sa.Column('status', sa.String(length=12), nullable=False),
    sa.Column('reason', sa.String(length=48), nullable=True),
    sa.Column('joined_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('verified_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('checked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('xp', sa.Integer(), server_default='0', nullable=False),
    sa.Column('event_id', sa.Integer(), nullable=True),
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['referrer_id'], ['members.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('tg_user_id')
    )
    with op.batch_alter_table('member_crew_referrals', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_member_crew_referrals_referrer_id'), ['referrer_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_member_crew_referrals_status'), ['status'], unique=False)
    with op.batch_alter_table('member_program_settings', schema=None) as batch_op:
        batch_op.add_column(sa.Column('crew_chat', sa.String(length=64), server_default='@cryptopricechat', nullable=False))
        batch_op.add_column(sa.Column('crew_hold_hours', sa.Integer(), server_default='24', nullable=False))
        batch_op.add_column(sa.Column('crew_bonus_at', sa.Integer(), server_default='5', nullable=False))


def downgrade() -> None:
    with op.batch_alter_table('member_program_settings', schema=None) as batch_op:
        batch_op.drop_column('crew_bonus_at')
        batch_op.drop_column('crew_hold_hours')
        batch_op.drop_column('crew_chat')
    with op.batch_alter_table('member_crew_referrals', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_member_crew_referrals_status'))
        batch_op.drop_index(batch_op.f('ix_member_crew_referrals_referrer_id'))
    op.drop_table('member_crew_referrals')
