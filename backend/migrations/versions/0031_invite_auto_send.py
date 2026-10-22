"""invite bot: Auto / Manual sending

Revision ID: 0031
Revises: 0030
Create Date: 2026-10-09 15:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0031'
down_revision: str | None = '0030'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # additive: Auto on (the bot sends to people who asked, at the configured pace); off = approve manually
    op.add_column('invite_settings', sa.Column('auto_send', sa.Boolean(), server_default=sa.true(), nullable=False))


def downgrade() -> None:
    op.drop_column('invite_settings', 'auto_send')
