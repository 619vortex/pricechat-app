"""youtube integration: platform-independent published content for the website

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-03 13:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0010'
down_revision: str | None = '0009'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('content_items',
    sa.Column('platform', sa.Enum('x', 'telegram', 'youtube', name='platform', native_enum=False, create_constraint=False, length=32), nullable=False),
    sa.Column('external_id', sa.String(length=64), nullable=False),
    sa.Column('channel_id', sa.String(length=64), nullable=True),
    sa.Column('content_type', sa.String(length=32), nullable=False),
    sa.Column('type_source', sa.String(length=16), server_default='auto', nullable=False),
    sa.Column('type_reason', sa.Text(), server_default='', nullable=False),
    sa.Column('title', sa.String(length=512), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('url', sa.String(length=512), nullable=False),
    sa.Column('thumbnail_url', sa.String(length=512), nullable=True),
    sa.Column('thumbnails', sa.JSON(), nullable=False),
    sa.Column('duration_seconds', sa.Integer(), nullable=True),
    sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('privacy_status', sa.String(length=16), nullable=False),
    sa.Column('available', sa.Boolean(), server_default='1', nullable=False),
    sa.Column('unavailable_since', sa.DateTime(timezone=True), nullable=True),
    sa.Column('website_visible', sa.Boolean(), server_default=sa.false(), nullable=False),
    sa.Column('visibility_source', sa.String(length=16), server_default='auto', nullable=False),
    sa.Column('featured', sa.Boolean(), server_default=sa.false(), nullable=False),
    sa.Column('tags', sa.JSON(), nullable=False),
    sa.Column('metrics', sa.JSON(), nullable=False),
    sa.Column('metrics_updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_synced_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('distribution', sa.JSON(), nullable=False),
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('platform', 'external_id')
    )
    op.create_index(op.f('ix_content_items_platform'), 'content_items', ['platform'], unique=False)
    op.create_index(op.f('ix_content_items_channel_id'), 'content_items', ['channel_id'], unique=False)
    op.create_index(op.f('ix_content_items_published_at'), 'content_items', ['published_at'], unique=False)
    op.create_table('content_item_metrics',
    sa.Column('item_id', sa.Integer(), nullable=False),
    sa.Column('captured_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('views', sa.Integer(), nullable=True),
    sa.Column('likes', sa.Integer(), nullable=True),
    sa.Column('comments', sa.Integer(), nullable=True),
    sa.Column('id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['item_id'], ['content_items.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_content_item_metrics_item_id'), 'content_item_metrics', ['item_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_content_item_metrics_item_id'), table_name='content_item_metrics')
    op.drop_table('content_item_metrics')
    op.drop_index(op.f('ix_content_items_published_at'), table_name='content_items')
    op.drop_index(op.f('ix_content_items_channel_id'), table_name='content_items')
    op.drop_index(op.f('ix_content_items_platform'), table_name='content_items')
    op.drop_table('content_items')
