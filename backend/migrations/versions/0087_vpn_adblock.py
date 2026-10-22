"""Kiwi VPN: ad-blocking servers, owner alerts for servers, and a per-member monthly data cap

- vpn_servers.adblock: the node agent reports its resolver blocks ads & trackers (members' configs then use its DNS)
- vpn_servers.address6: the agent's IPv6 pool ("" = IPv4 only); a device's v6 sits at its v4's offset in it
- vpn_servers.fail_streak: syncs in a row that couldn't reach the agent (2 → the owner is told it's offline)
- vpn_settings.monthly_cap_gb: data per member per calendar month (UTC), 0 = unlimited
- vpn_devices.paused_until: the member hit the cap; the peer is off the node until then (the 1st of next month)

Revision ID: 0087_vpn_adblock
Revises: 0086_vpn
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0087_vpn_adblock'
down_revision: str | None = '0086_vpn'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("vpn_servers", sa.Column("adblock", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("vpn_servers", sa.Column("address6", sa.String(64), nullable=False, server_default=""))
    op.add_column("vpn_servers", sa.Column("fail_streak", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("vpn_settings", sa.Column("monthly_cap_gb", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("vpn_devices", sa.Column("paused_until", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_column("vpn_devices", "paused_until")
    op.drop_column("vpn_settings", "monthly_cap_gb")
    op.drop_column("vpn_servers", "fail_streak")
    op.drop_column("vpn_servers", "address6")
    op.drop_column("vpn_servers", "adblock")
