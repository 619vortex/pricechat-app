"""provider market valuations (e.g. Scrydex) kept apart from sales, and provider sync logs

Revision ID: 0022
Revises: 0021
Create Date: 2026-10-08 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0022'
down_revision: str | None = '0021'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('pokemon_provider_valuations',
    sa.Column('provider', sa.String(length=32), nullable=False),
    sa.Column('card_id', sa.Integer(), nullable=False),
    sa.Column('provider_card_id', sa.String(length=64), nullable=False),
    sa.Column('provider_variant', sa.String(length=64), nullable=False),
    sa.Column('grading_company', sa.String(length=8), nullable=False),
    sa.Column('grade', sa.String(length=8), nullable=False),
    sa.Column('day', sa.Date(), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('market_cents', sa.Integer(), nullable=True),
    sa.Column('low_cents', sa.Integer(), nullable=True),
    sa.Column('mid_cents', sa.Integer(), nullable=True),
    sa.Column('high_cents', sa.Integer(), nullable=True),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('trends', sa.JSON(), nullable=True),
    sa.Column('fetched_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['card_id'], ['pokemon_cards.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('provider', 'card_id', 'grading_company', 'grade', 'day')
    )
    op.create_index('ix_pokemon_provval_card_grade_day', 'pokemon_provider_valuations', ['card_id', 'grading_company', 'grade', 'day'], unique=False)
    op.create_table('pokemon_sync_logs',
    sa.Column('provider', sa.String(length=32), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('requests', sa.Integer(), nullable=False),
    sa.Column('cache_hits', sa.Integer(), nullable=False),
    sa.Column('summary', sa.JSON(), nullable=False),
    sa.Column('entries', sa.JSON(), nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_pokemon_sync_logs_provider'), 'pokemon_sync_logs', ['provider'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_pokemon_sync_logs_provider'), table_name='pokemon_sync_logs')
    op.drop_table('pokemon_sync_logs')
    op.drop_index('ix_pokemon_provval_card_grade_day', table_name='pokemon_provider_valuations')
    op.drop_table('pokemon_provider_valuations')
