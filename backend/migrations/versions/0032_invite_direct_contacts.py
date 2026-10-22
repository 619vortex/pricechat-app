"""invite bot: optionally invite people who message the account directly (without watched-group activity)

Revision ID: 0032
Revises: 0031
Create Date: 2026-10-09 16:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0032'
down_revision: str | None = '0031'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('invite_settings', sa.Column('invite_direct_contacts', sa.Boolean(), server_default=sa.false(), nullable=False))


def downgrade() -> None:
    op.drop_column('invite_settings', 'invite_direct_contacts')
