"""Kiwi VPN servers can be shown to the owner and testers only while they're tried out.

Revision ID: 0105_vpn_server_testers_only
Revises: 0104_newsdesk_events
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0105_vpn_server_testers_only'
down_revision: str | None = '0104_newsdesk_events'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # existing servers stay visible to everyone
    with op.batch_alter_table("vpn_servers") as batch:
        batch.add_column(sa.Column("testers_only", sa.Boolean(), server_default=sa.false(), nullable=False))


def downgrade() -> None:
    with op.batch_alter_table("vpn_servers") as batch:
        batch.drop_column("testers_only")
