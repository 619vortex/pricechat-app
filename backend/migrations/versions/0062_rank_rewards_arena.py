"""rank rewards: reward catalog, beginner content, reward claims + audit, Kiwi Arena runs, display-name cooldown / accent;
Earn unlocks at Scout Kiwi (rank 4)

Revision ID: 0062
Revises: 0061
Create Date: 2026-10-17 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0062'
down_revision: str | None = '0061'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _ts() -> list[sa.Column]:
    return [sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False)]


def upgrade() -> None:
    op.create_table('rank_rewards',
    sa.Column('rank_key', sa.String(length=32), nullable=False),
    sa.Column('kind', sa.String(length=24), nullable=False),
    sa.Column('title', sa.String(length=96), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('enabled', sa.Boolean(), server_default=sa.true(), nullable=False),
    *_ts(),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('rank_key')
    )
    op.create_table('reward_settings',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('knight_status', sa.String(length=16), server_default='NOT_CONFIGURED', nullable=False),
    sa.Column('knight_amount_usd', sa.Integer(), server_default='10', nullable=False),
    sa.Column('knight_terms', sa.Text(), nullable=False),
    sa.Column('knight_regions', sa.Text(), nullable=False),
    sa.Column('knight_expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('knight_budget', sa.Integer(), server_default='0', nullable=False),
    sa.Column('vip_invite_enc', sa.Text(), nullable=True),
    sa.Column('vip_description', sa.Text(), nullable=False),
    sa.Column('arena_kills_per_xp', sa.Integer(), server_default='10', nullable=False),
    sa.Column('arena_run_xp_cap', sa.Integer(), server_default='10', nullable=False),
    sa.Column('arena_daily_xp_cap', sa.Integer(), server_default='20', nullable=False),
    sa.Column('arena_min_run_seconds', sa.Integer(), server_default='45', nullable=False),
    sa.Column('arena_runs_per_hour', sa.Integer(), server_default='12', nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('beginner_content',
    sa.Column('title', sa.String(length=160), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('video_url', sa.String(length=500), nullable=True),
    sa.Column('thumbnail_url', sa.String(length=500), nullable=True),
    sa.Column('guide_text', sa.Text(), nullable=False),
    sa.Column('position', sa.Integer(), server_default='0', nullable=False),
    sa.Column('published', sa.Boolean(), server_default=sa.false(), nullable=False),
    *_ts(),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('reward_claims',
    sa.Column('public_id', sa.String(length=36), nullable=False),
    sa.Column('member_id', sa.Integer(), nullable=False),
    sa.Column('reward_key', sa.String(length=32), nullable=False),
    sa.Column('status', sa.String(length=12), nullable=False),
    sa.Column('rank_level', sa.Integer(), nullable=False),
    sa.Column('wallet', sa.String(length=42), nullable=True),
    sa.Column('member_note', sa.Text(), nullable=False),
    sa.Column('admin_note', sa.Text(), nullable=False),
    sa.Column('fulfillment_ref', sa.String(length=200), nullable=True),
    sa.Column('reviewed_by', sa.String(length=64), nullable=True),
    sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
    *_ts(),
    sa.ForeignKeyConstraint(['member_id'], ['members.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('public_id'),
    sa.UniqueConstraint('member_id', 'reward_key', name='ux_reward_claims_member_reward')
    )
    op.create_index('ix_reward_claims_status', 'reward_claims', ['reward_key', 'status'])
    op.create_table('reward_audit',
    sa.Column('claim_id', sa.Integer(), nullable=True),
    sa.Column('member_id', sa.Integer(), nullable=True),
    sa.Column('actor', sa.String(length=64), nullable=False),
    sa.Column('action', sa.String(length=48), nullable=False),
    sa.Column('details', sa.JSON(), nullable=False),
    *_ts(),
    sa.ForeignKeyConstraint(['claim_id'], ['reward_claims.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['member_id'], ['members.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_reward_audit_claim_id', 'reward_audit', ['claim_id'])
    op.create_index('ix_reward_audit_member_id', 'reward_audit', ['member_id'])
    op.create_table('arena_runs',
    sa.Column('public_id', sa.String(length=36), nullable=False),
    sa.Column('member_id', sa.Integer(), nullable=False),
    sa.Column('seed', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=12), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_beat_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('beats', sa.Integer(), nullable=False),
    sa.Column('active_beats', sa.Integer(), nullable=False),
    sa.Column('kills_reported', sa.Integer(), nullable=False),
    sa.Column('kills_accepted', sa.Integer(), nullable=False),
    sa.Column('inputs_reported', sa.Integer(), nullable=False),
    sa.Column('best_wave', sa.Integer(), nullable=False),
    sa.Column('xp_awarded', sa.Integer(), nullable=False),
    sa.Column('flags', sa.JSON(), nullable=False),
    *_ts(),
    sa.ForeignKeyConstraint(['member_id'], ['members.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('public_id')
    )
    op.create_index('ix_arena_runs_member_started', 'arena_runs', ['member_id', 'started_at'])
    with op.batch_alter_table('members', schema=None) as batch_op:
        batch_op.add_column(sa.Column('display_name_changed_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('passport_accent', sa.String(length=16), nullable=True))
    # Earn (the trading bot) is the Scout Kiwi reward: raise a lower requirement to rank 4. Members who already connected
    # the bot or trade keep their access (tradingbot.service.grandfathered), so nobody loses control of open positions.
    op.execute("UPDATE tb_settings SET min_rank_level = 4 WHERE min_rank_level < 4")
    with op.batch_alter_table('tb_settings', schema=None) as batch_op:
        batch_op.alter_column('min_rank_level', server_default='4')


def downgrade() -> None:
    with op.batch_alter_table('tb_settings', schema=None) as batch_op:
        batch_op.alter_column('min_rank_level', server_default='3')
    with op.batch_alter_table('members', schema=None) as batch_op:
        batch_op.drop_column('passport_accent')
        batch_op.drop_column('display_name_changed_at')
    op.drop_index('ix_arena_runs_member_started', table_name='arena_runs')
    op.drop_table('arena_runs')
    op.drop_index('ix_reward_audit_member_id', table_name='reward_audit')
    op.drop_index('ix_reward_audit_claim_id', table_name='reward_audit')
    op.drop_table('reward_audit')
    op.drop_index('ix_reward_claims_status', table_name='reward_claims')
    op.drop_table('reward_claims')
    op.drop_table('beginner_content')
    op.drop_table('reward_settings')
    op.drop_table('rank_rewards')
