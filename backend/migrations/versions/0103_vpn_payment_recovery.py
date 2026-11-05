"""Kiwi VPN payments: asking the provider about unfinished payments, notes for Admin, and expiry reminders that go once each.

Revision ID: 0103_vpn_payment_recovery
Revises: 0102_desktop_codes
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0103_vpn_payment_recovery'
down_revision: str | None = '0102_desktop_codes'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("vpn_payments", sa.Column("checked_at", sa.DateTime(timezone=True)))
    op.add_column("vpn_payments", sa.Column("note", sa.String(200)))
    op.add_column("vpn_accounts", sa.Column("reminded_until", sa.DateTime(timezone=True)))
    op.add_column("vpn_accounts", sa.Column("reminder_stage", sa.Integer()))


def downgrade() -> None:
    with op.batch_alter_table("vpn_accounts") as batch:
        batch.drop_column("reminder_stage")
        batch.drop_column("reminded_until")
    with op.batch_alter_table("vpn_payments") as batch:
        batch.drop_column("note")
        batch.drop_column("checked_at")
