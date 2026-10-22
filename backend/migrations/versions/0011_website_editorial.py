"""website: editorial placement on content items (series, category) and website settings

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-04 13:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0011'
down_revision: str | None = '0010'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('content_items') as batch:
        batch.add_column(sa.Column('series', sa.String(length=32), nullable=True))
        batch.add_column(sa.Column('category', sa.String(length=16), nullable=True))
        batch.create_index(batch.f('ix_content_items_series'), ['series'], unique=False)
        batch.create_index(batch.f('ix_content_items_category'), ['category'], unique=False)
    op.create_table('website_settings',
    sa.Column('sections', sa.JSON(), nullable=False),
    sa.Column('links', sa.JSON(), nullable=False),
    sa.Column('site_url', sa.String(length=256), nullable=True),
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:
    op.drop_table('website_settings')
    with op.batch_alter_table('content_items') as batch:
        batch.drop_index(batch.f('ix_content_items_category'))
        batch.drop_index(batch.f('ix_content_items_series'))
        batch.drop_column('category')
        batch.drop_column('series')
