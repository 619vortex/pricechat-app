"""pokemon market pulse: public X posts for the sidebar (context only) + feed config

Revision ID: 0023
Revises: 0022
Create Date: 2026-10-08 15:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0023'
down_revision: str | None = '0022'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('pokemon_pulse_posts',
    sa.Column('post_id', sa.String(length=32), nullable=False),
    sa.Column('author_id', sa.String(length=32), nullable=False),
    sa.Column('username', sa.String(length=64), nullable=False),
    sa.Column('display_name', sa.String(length=128), nullable=False),
    sa.Column('avatar_url', sa.String(length=512), nullable=True),
    sa.Column('verified', sa.Boolean(), server_default=sa.text('0'), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('created_at_x', sa.DateTime(timezone=True), nullable=False),
    sa.Column('lang', sa.String(length=8), nullable=True),
    sa.Column('url', sa.String(length=256), nullable=False),
    sa.Column('media_url', sa.String(length=512), nullable=True),
    sa.Column('likes', sa.Integer(), nullable=False),
    sa.Column('reposts', sa.Integer(), nullable=False),
    sa.Column('replies', sa.Integer(), nullable=False),
    sa.Column('quotes', sa.Integer(), nullable=False),
    sa.Column('categories', sa.JSON(), nullable=False),
    sa.Column('relevance', sa.Float(), nullable=False),
    sa.Column('text_key', sa.String(length=64), nullable=False),
    sa.Column('fetched_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('post_id')
    )
    op.create_index('ix_pokemon_pulse_created', 'pokemon_pulse_posts', ['created_at_x'], unique=False)
    op.create_index(op.f('ix_pokemon_pulse_posts_text_key'), 'pokemon_pulse_posts', ['text_key'], unique=False)
    op.create_table('pokemon_pulse_config',
    sa.Column('enabled', sa.Boolean(), server_default=sa.text('0'), nullable=False),
    sa.Column('trusted_accounts', sa.JSON(), nullable=False),
    sa.Column('since_id', sa.String(length=32), nullable=True),
    sa.Column('last_refresh_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.Column('last_result', sa.JSON(), nullable=False),
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:
    op.drop_table('pokemon_pulse_config')
    op.drop_index(op.f('ix_pokemon_pulse_posts_text_key'), table_name='pokemon_pulse_posts')
    op.drop_index('ix_pokemon_pulse_created', table_name='pokemon_pulse_posts')
    op.drop_table('pokemon_pulse_posts')
