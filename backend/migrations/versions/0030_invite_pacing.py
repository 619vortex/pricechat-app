"""invite bot: group posts master switch + configurable gap between invitations

Revision ID: 0030
Revises: 0029
Create Date: 2026-10-09 14:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0030'
down_revision: str | None = '0029'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # additive: group posts start switched OFF (DM-first); 5 minutes between invitations
    op.add_column('invite_settings', sa.Column('group_posts_enabled', sa.Boolean(), server_default=sa.false(), nullable=False))
    op.add_column('invite_settings', sa.Column('dm_interval_seconds', sa.Integer(), server_default='300', nullable=False))


def downgrade() -> None:
    op.drop_column('invite_settings', 'dm_interval_seconds')
    op.drop_column('invite_settings', 'group_posts_enabled')
