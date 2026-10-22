"""pokemon top 100: daily snapshots of the eligible Top 100 + enter / leave events

Revision ID: 0024
Revises: 0023
Create Date: 2026-10-08 18:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0024'
down_revision: str | None = '0023'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('pokemon_top100_snapshots',
    sa.Column('day', sa.Date(), nullable=False),
    sa.Column('card_id', sa.Integer(), nullable=False),
    sa.Column('rank', sa.Integer(), nullable=False),
    sa.Column('market_cap_cents', sa.BigInteger(), nullable=False),
    sa.Column('price_cents', sa.BigInteger(), nullable=False),
    sa.Column('price_source', sa.String(length=32), nullable=False),
    sa.Column('population', sa.Integer(), nullable=False),
    sa.Column('history_points', sa.Integer(), nullable=False),
    sa.Column('score', sa.Integer(), nullable=False),
    sa.Column('confidence', sa.String(length=8), nullable=False),
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['card_id'], ['pokemon_cards.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('day', 'card_id')
    )
    op.create_index('ix_pokemon_top100_day', 'pokemon_top100_snapshots', ['day'], unique=False)
    op.create_index(op.f('ix_pokemon_top100_snapshots_card_id'), 'pokemon_top100_snapshots', ['card_id'], unique=False)
    op.create_table('pokemon_top100_events',
    sa.Column('at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('card_id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=8), nullable=False),
    sa.Column('rank', sa.Integer(), nullable=True),
    sa.Column('reasons', sa.JSON(), nullable=False),
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['card_id'], ['pokemon_cards.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_pokemon_top100_events_at', 'pokemon_top100_events', ['at'], unique=False)
    op.create_index(op.f('ix_pokemon_top100_events_card_id'), 'pokemon_top100_events', ['card_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_pokemon_top100_events_card_id'), table_name='pokemon_top100_events')
    op.drop_index('ix_pokemon_top100_events_at', table_name='pokemon_top100_events')
    op.drop_table('pokemon_top100_events')
    op.drop_index(op.f('ix_pokemon_top100_snapshots_card_id'), table_name='pokemon_top100_snapshots')
    op.drop_index('ix_pokemon_top100_day', table_name='pokemon_top100_snapshots')
    op.drop_table('pokemon_top100_snapshots')
