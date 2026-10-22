"""Kiwi VPN: settings, WireGuard servers (node agents) and members' devices (public keys only).

Revision ID: 0086_vpn
Revises: 0084_autodelete
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0086_vpn'
down_revision: str | None = '0084_autodelete'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "vpn_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("min_rank", sa.String(16), nullable=False, server_default="warrior"),
        sa.Column("max_devices", sa.Integer(), nullable=False, server_default="3"),
    )
    op.create_table(
        "vpn_servers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(48), nullable=False),
        sa.Column("region", sa.String(48), nullable=False),
        sa.Column("country_code", sa.String(2), nullable=False),
        sa.Column("agent_url", sa.String(255), nullable=False),
        sa.Column("agent_token_enc", sa.Text(), nullable=False),
        sa.Column("cert_sha256", sa.String(64), nullable=False),
        sa.Column("capacity", sa.Integer(), nullable=False, server_default="250"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("public_key", sa.String(64), nullable=False),
        sa.Column("endpoint_host", sa.String(255), nullable=False),
        sa.Column("listen_port", sa.Integer(), nullable=False, server_default="51820"),
        sa.Column("address", sa.String(64), nullable=False),
        sa.Column("dns", sa.JSON(), nullable=False),
        sa.Column("online", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("last_seen", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "vpn_devices",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("member_id", sa.Integer(), sa.ForeignKey("members.id", ondelete="CASCADE"), nullable=False),
        sa.Column("server_id", sa.Integer(), sa.ForeignKey("vpn_servers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(32), nullable=False),
        sa.Column("public_key", sa.String(64), nullable=False),
        sa.Column("ip", sa.String(45), nullable=False),
        sa.Column("last_handshake", sa.DateTime(timezone=True)),
        sa.Column("usage_month", sa.String(7), nullable=False, server_default=""),
        sa.Column("rx_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("tx_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("rx_counter", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("tx_counter", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("server_id", "ip", name="uq_vpn_devices_server_ip"),
        sa.UniqueConstraint("public_key", name="uq_vpn_devices_public_key"),
    )
    op.create_index("ix_vpn_devices_member_id", "vpn_devices", ["member_id"])
    op.create_index("ix_vpn_devices_server_id", "vpn_devices", ["server_id"])


def downgrade() -> None:
    op.drop_index("ix_vpn_devices_server_id", table_name="vpn_devices")
    op.drop_index("ix_vpn_devices_member_id", table_name="vpn_devices")
    op.drop_table("vpn_devices")
    op.drop_table("vpn_servers")
    op.drop_table("vpn_settings")
