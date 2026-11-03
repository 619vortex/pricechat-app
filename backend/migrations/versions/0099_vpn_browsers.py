"""Kiwi VPN browser extension: vpn_browsers (a login on one server's proxy, hash only) and each server's proxy address

Revision ID: 0099_vpn_browsers
Revises: 0098_vpn_payments_switch
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0099_vpn_browsers'
down_revision: str | None = '0098_vpn_payments_switch'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("vpn_servers", sa.Column("proxy_host", sa.String(255), nullable=False, server_default=""))
    op.add_column("vpn_servers", sa.Column("proxy_port", sa.Integer(), nullable=False, server_default="443"))
    op.create_table(
        "vpn_browsers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("vpn_accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("server_id", sa.Integer(), sa.ForeignKey("vpn_servers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("username", sa.String(40), nullable=False, unique=True),
        sa.Column("secret_hash", sa.String(64), nullable=False),
        sa.Column("last_active", sa.DateTime(timezone=True)),
        sa.Column("usage_month", sa.String(7), nullable=False, server_default=""),
        sa.Column("rx_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("tx_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("rx_counter", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("tx_counter", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_vpn_browsers_account_id", "vpn_browsers", ["account_id"])
    op.create_index("ix_vpn_browsers_server_id", "vpn_browsers", ["server_id"])


def downgrade() -> None:
    op.drop_table("vpn_browsers")
    with op.batch_alter_table("vpn_servers") as batch:
        batch.drop_column("proxy_port")
        batch.drop_column("proxy_host")
