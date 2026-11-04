"""Kiwi VPN's access rules: a 30-day free trial, one per account (vpn_settings.trial_enabled, Admin can switch it off). Closes public access
and sets the device limit to 3, as the owner asked at launch; Admin can change both. Downgrading doesn't reopen it.

Revision ID: 0100_vpn_trial
Revises: 0099_vpn_browsers
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0100_vpn_trial'
down_revision: str | None = '0099_vpn_browsers'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(sa.text(
        "UPDATE entitlements SET description = 'A private WireGuard VPN (up to 3 devices). Free for Royal Kiwis and above.' "
        "WHERE key = 'kiwi_vpn' AND description = 'A private WireGuard VPN (up to 3 devices). Not unlocked by rank.'"))
    op.add_column("vpn_settings", sa.Column("trial_enabled", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("vpn_accounts", sa.Column("trial_started_at", sa.DateTime(timezone=True)))
    op.add_column("vpn_accounts", sa.Column("trial_ends_at", sa.DateTime(timezone=True)))
    op.execute(sa.text("UPDATE vpn_settings SET enabled = :off, max_devices = 3").bindparams(off=False))


def downgrade() -> None:
    op.execute(sa.text(
        "UPDATE entitlements SET description = 'A private WireGuard VPN (up to 3 devices). Not unlocked by rank.' "
        "WHERE key = 'kiwi_vpn' AND description = 'A private WireGuard VPN (up to 3 devices). Free for Royal Kiwis and above.'"))
    with op.batch_alter_table("vpn_accounts") as batch:
        batch.drop_column("trial_ends_at")
        batch.drop_column("trial_started_at")
    with op.batch_alter_table("vpn_settings") as batch:
        batch.drop_column("trial_enabled")
