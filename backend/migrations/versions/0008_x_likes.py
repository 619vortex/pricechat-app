"""x likes pilot: relationship memory for likes

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-03 11:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0008'
down_revision: str | None = '0007'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('social_relationships', schema=None) as batch_op:
        batch_op.add_column(sa.Column('last_liked_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('last_liked_post_id', sa.String(length=64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('social_relationships', schema=None) as batch_op:
        batch_op.drop_column('last_liked_post_id')
        batch_op.drop_column('last_liked_at')
