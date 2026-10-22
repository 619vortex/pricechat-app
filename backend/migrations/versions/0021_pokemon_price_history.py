"""pokemon price history engine: valuation points, population history, market indexes

Revision ID: 0021
Revises: 0020
Create Date: 2026-10-08 09:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0021'
down_revision: str | None = '0020'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('pokemon_valuation_points',
    sa.Column('card_id', sa.Integer(), nullable=False),
    sa.Column('grading_company', sa.String(length=8), nullable=False),
    sa.Column('grade', sa.String(length=8), nullable=False),
    sa.Column('day', sa.Date(), nullable=False),
    sa.Column('market_price_cents', sa.Integer(), nullable=False),
    sa.Column('confidence', sa.String(length=8), nullable=True),
    sa.Column('sales_used', sa.Integer(), nullable=False),
    sa.Column('sales_on_day', sa.Integer(), nullable=False),
    sa.Column('volume_on_day_cents', sa.Integer(), nullable=False),
    sa.Column('algorithm', sa.String(length=160), nullable=False),
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['card_id'], ['pokemon_cards.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('card_id', 'grading_company', 'grade', 'day')
    )
    op.create_index('ix_pokemon_points_card_grade_day', 'pokemon_valuation_points', ['card_id', 'grading_company', 'grade', 'day'], unique=False)
    op.create_index('ix_pokemon_points_grade_day', 'pokemon_valuation_points', ['grading_company', 'grade', 'day'], unique=False)
    op.create_table('pokemon_population_history',
    sa.Column('card_id', sa.Integer(), nullable=False),
    sa.Column('grading_company', sa.String(length=8), nullable=False),
    sa.Column('grade', sa.String(length=8), nullable=False),
    sa.Column('count', sa.Integer(), nullable=False),
    sa.Column('source', sa.String(length=32), nullable=False),
    sa.Column('day', sa.Date(), nullable=False),
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['card_id'], ['pokemon_cards.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('card_id', 'grading_company', 'grade', 'source', 'day')
    )
    op.create_index('ix_pokemon_pophist_card_grade_day', 'pokemon_population_history', ['card_id', 'grading_company', 'grade', 'day'], unique=False)
    op.create_index('ix_pokemon_sales_grade_date', 'pokemon_sales', ['grading_company', 'grade', 'sale_date'], unique=False)
    op.create_index('ix_pokemon_sales_date', 'pokemon_sales', ['sale_date'], unique=False)
    # data: the population reports already stored become the first history rows (one per report day)
    op.execute("""
        INSERT INTO pokemon_population_history (card_id, grading_company, grade, count, source, day, created_at, updated_at)
        SELECT card_id, grading_company, grade, count, source, date(as_of), created_at, updated_at FROM pokemon_populations
    """)
    # valuation points are derived data: the app rebuilds them from pokemon_sales on start (history.ensure_built)


def downgrade() -> None:
    op.drop_index('ix_pokemon_sales_date', table_name='pokemon_sales')
    op.drop_index('ix_pokemon_sales_grade_date', table_name='pokemon_sales')
    op.drop_index('ix_pokemon_pophist_card_grade_day', table_name='pokemon_population_history')
    op.drop_table('pokemon_population_history')
    op.drop_index('ix_pokemon_points_grade_day', table_name='pokemon_valuation_points')
    op.drop_index('ix_pokemon_points_card_grade_day', table_name='pokemon_valuation_points')
    op.drop_table('pokemon_valuation_points')
