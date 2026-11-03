"""Kiwi VPN is free until Admin turns payments on: vpn_settings.payments_enabled; devices made in Admin belong to an owner account

Revision ID: 0098_vpn_payments_switch
Revises: 0097_vpn_accounts_and_payments
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0098_vpn_payments_switch'
down_revision: str | None = '0097_vpn_accounts_and_payments'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("vpn_settings", sa.Column("payments_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("vpn_accounts", sa.Column("is_owner", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.execute(sa.text(
        "UPDATE entitlements SET description = 'A private WireGuard VPN (up to 3 devices). Not unlocked by rank.' "
        "WHERE key = 'kiwi_vpn' AND description = 'A private WireGuard VPN (up to 3 devices), $9.99 a month. Not unlocked by rank.'"))


def downgrade() -> None:
    op.execute(sa.text(
        "UPDATE entitlements SET description = 'A private WireGuard VPN (up to 3 devices), $9.99 a month. Not unlocked by rank.' "
        "WHERE key = 'kiwi_vpn' AND description = 'A private WireGuard VPN (up to 3 devices). Not unlocked by rank.'"))
    with op.batch_alter_table("vpn_accounts") as batch:
        batch.drop_column("is_owner")
    with op.batch_alter_table("vpn_settings") as batch:
        batch.drop_column("payments_enabled")
