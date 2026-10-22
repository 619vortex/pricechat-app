"""Play Market (virtual): Play USDT wallets, ledger and listings for giveaway cards; auto-sell duplicates opt-in

Revision ID: 0038
Revises: 0037
Create Date: 2026-10-10 10:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op



revision: str = '0038'
down_revision: str | None = '0037'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('members', sa.Column('auto_sell_dupes', sa.Boolean(), server_default=sa.false(), nullable=False))
    op.create_table(
        'play_wallets',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('member_id', sa.Integer(), sa.ForeignKey('members.id', ondelete='CASCADE'), nullable=False, unique=True),
        sa.Column('balance_cents', sa.BigInteger(), server_default='0', nullable=False),
        sa.Column('version', sa.Integer(), server_default='0', nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        'play_ledger',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('member_id', sa.Integer(), sa.ForeignKey('members.id', ondelete='CASCADE'), nullable=False),
        sa.Column('delta_cents', sa.BigInteger(), nullable=False),
        sa.Column('kind', sa.String(16), nullable=False),
        sa.Column('ref', sa.String(64), nullable=False),
        sa.Column('card_id', sa.Integer(), sa.ForeignKey('pokemon_cards.id', ondelete='SET NULL')),
        sa.Column('note', sa.String(200), nullable=False, server_default=''),
        sa.Column('at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('member_id', 'kind', 'ref'),
    )
    op.create_index('ix_play_ledger_member_at', 'play_ledger', ['member_id', 'at'])
    op.create_table(
        'play_listings',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('seller_id', sa.Integer(), sa.ForeignKey('members.id', ondelete='CASCADE'), nullable=False),
        sa.Column('card_id', sa.Integer(), sa.ForeignKey('pokemon_cards.id', ondelete='CASCADE'), nullable=False),
        sa.Column('tier', sa.String(10), nullable=False),
        sa.Column('pack_key', sa.String(16), nullable=False),
        sa.Column('price_cents', sa.BigInteger(), nullable=False),
        sa.Column('status', sa.String(10), nullable=False, server_default='open'),
        sa.Column('buyer_id', sa.Integer(), sa.ForeignKey('members.id', ondelete='SET NULL')),
        sa.Column('version', sa.Integer(), server_default='0', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('closed_at', sa.DateTime(timezone=True)),
    )
    op.create_index('ix_play_listings_status_at', 'play_listings', ['status', 'created_at'])
    op.create_index('ix_play_listings_seller', 'play_listings', ['seller_id', 'status'])


def downgrade() -> None:
    op.drop_index('ix_play_listings_seller', 'play_listings')
    op.drop_index('ix_play_listings_status_at', 'play_listings')
    op.drop_table('play_listings')
    op.drop_index('ix_play_ledger_member_at', 'play_ledger')
    op.drop_table('play_ledger')
    op.drop_table('play_wallets')
    op.drop_column('members', 'auto_sell_dupes')
