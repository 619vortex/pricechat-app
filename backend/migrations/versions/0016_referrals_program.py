"""referrals, program settings (growth), telegram channel state

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-05 18:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0016'
down_revision: str | None = '0015'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _ts() -> list[sa.Column]:
    return [sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False)]


def upgrade() -> None:
    op.create_table('member_referral_codes',
    sa.Column('member_id', sa.Integer(), nullable=False),
    sa.Column('code', sa.String(length=24), nullable=False),
    sa.Column('clicks', sa.Integer(), server_default='0', nullable=False),
    *_ts(),
    sa.ForeignKeyConstraint(['member_id'], ['members.id'], name=op.f('fk_member_referral_codes_member_id_members'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_member_referral_codes')),
    sa.UniqueConstraint('member_id', name=op.f('uq_member_referral_codes_member_id')),
    sa.UniqueConstraint('code', name=op.f('uq_member_referral_codes_code'))
    )
    op.create_table('member_referrals',
    sa.Column('referrer_id', sa.Integer(), nullable=True),
    sa.Column('referred_id', sa.Integer(), nullable=True),
    sa.Column('code', sa.String(length=24), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('reason', sa.String(length=64), nullable=True),
    sa.Column('browser_hash', sa.String(length=64), nullable=True),
    sa.Column('qualified_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('qualifying_event_type', sa.String(length=48), nullable=True),
    sa.Column('referrer_event_id', sa.Integer(), nullable=True),
    sa.Column('welcome_event_id', sa.Integer(), nullable=True),
    sa.Column('referrer_xp', sa.Integer(), server_default='0', nullable=False),
    sa.Column('welcome_xp', sa.Integer(), server_default='0', nullable=False),
    *_ts(),
    sa.ForeignKeyConstraint(['referrer_id'], ['members.id'], name=op.f('fk_member_referrals_referrer_id_members'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['referred_id'], ['members.id'], name=op.f('fk_member_referrals_referred_id_members'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_member_referrals')),
    sa.UniqueConstraint('referred_id', name=op.f('uq_member_referrals_referred_id'))
    )
    op.create_index(op.f('ix_member_referrals_referrer_id'), 'member_referrals', ['referrer_id'], unique=False)
    op.create_index(op.f('ix_member_referrals_status'), 'member_referrals', ['status'], unique=False)
    op.create_table('member_program_settings',
    sa.Column('referrals_enabled', sa.Boolean(), nullable=False),
    sa.Column('referral_qualifying_events', sa.JSON(), nullable=False),
    sa.Column('referral_min_age_hours', sa.Integer(), nullable=False),
    sa.Column('referral_daily_limit', sa.Integer(), nullable=False),
    sa.Column('updates_channel', sa.String(length=64), nullable=True),
    sa.Column('channel_join_xp', sa.Integer(), nullable=False),
    sa.Column('discussion_xp', sa.Integer(), nullable=False),
    sa.Column('discussion_window_days', sa.Integer(), nullable=False),
    sa.Column('discussion_daily_limit', sa.Integer(), nullable=False),
    sa.Column('leaderboard_periods', sa.JSON(), nullable=False),
    sa.Column('channel_id', sa.BigInteger(), nullable=True),
    sa.Column('channel_title', sa.String(length=128), nullable=True),
    sa.Column('channel_linked_chat_id', sa.BigInteger(), nullable=True),
    sa.Column('channel_bot_can_verify', sa.Boolean(), nullable=False),
    sa.Column('channel_checked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('channel_error', sa.Text(), nullable=True),
    *_ts(),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_member_program_settings'))
    )


def downgrade() -> None:
    op.drop_table('member_program_settings')
    op.drop_index(op.f('ix_member_referrals_status'), table_name='member_referrals')
    op.drop_index(op.f('ix_member_referrals_referrer_id'), table_name='member_referrals')
    op.drop_table('member_referrals')
    op.drop_table('member_referral_codes')
