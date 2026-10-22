"""social posts: attached media (photos / video previews) for Read & Earn

Revision ID: 0063
Revises: 0062
Create Date: 2026-10-17 15:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0063'
down_revision: str | None = '0062'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('social_posts', schema=None) as batch_op:
        batch_op.add_column(sa.Column('media', sa.JSON(), server_default='[]', nullable=False))


def downgrade() -> None:
    with op.batch_alter_table('social_posts', schema=None) as batch_op:
        batch_op.drop_column('media')
