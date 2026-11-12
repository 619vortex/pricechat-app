"""A Kiwi VPN server's monthly traffic allowance, for bandwidth alerts. 0 = no alert.

Revision ID: 0111_vpn_server_traffic_allowance
Revises: 0110_vpn_support_reports
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0111_vpn_server_traffic_allowance'
down_revision: str | None = '0110_vpn_support_reports'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("vpn_servers") as batch:
        batch.add_column(sa.Column("traffic_included_gb", sa.Integer(), server_default="0", nullable=False))


def downgrade() -> None:
    with op.batch_alter_table("vpn_servers") as batch:
        batch.drop_column("traffic_included_gb")
