"""Play Market: standing bids (Play USDT held while open)

Revision ID: 0039
Revises: 0038
Create Date: 2026-10-10 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0039'
down_revision: str | None = '0038'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'play_bids',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('bidder_id', sa.Integer(), sa.ForeignKey('members.id', ondelete='CASCADE'), nullable=False),
        sa.Column('card_id', sa.Integer(), sa.ForeignKey('pokemon_cards.id', ondelete='CASCADE'), nullable=False),
        sa.Column('price_cents', sa.BigInteger(), nullable=False),
        sa.Column('status', sa.String(10), nullable=False, server_default='open'),
        sa.Column('seller_id', sa.Integer(), sa.ForeignKey('members.id', ondelete='SET NULL')),
        sa.Column('version', sa.Integer(), server_default='0', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('closed_at', sa.DateTime(timezone=True)),
    )
    op.create_index('ix_play_bids_card_status', 'play_bids', ['card_id', 'status', 'price_cents'])
    op.create_index('ix_play_bids_bidder', 'play_bids', ['bidder_id', 'status'])


def downgrade() -> None:
    op.drop_index('ix_play_bids_bidder', 'play_bids')
    op.drop_index('ix_play_bids_card_status', 'play_bids')
    op.drop_table('play_bids')
