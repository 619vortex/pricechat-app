"""Kiwi VPN billing: vpn_accounts (a Passport member or an anonymous key), vpn_payments; devices belong to an account

Revision ID: 0097_vpn_accounts_and_payments
Revises: 0096_kiwi_vpn_not_rank_based
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0097_vpn_accounts_and_payments'
down_revision: str | None = '0096_kiwi_vpn_not_rank_based'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "vpn_accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("member_id", sa.Integer(), sa.ForeignKey("members.id", ondelete="CASCADE"), unique=True),
        sa.Column("key_hash", sa.String(64), unique=True),
        sa.Column("status", sa.String(12), nullable=False, server_default="active"),
        sa.Column("paid_until", sa.DateTime(timezone=True)),
        sa.Column("lapsed_at", sa.DateTime(timezone=True)),
        sa.Column("stripe_customer", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "vpn_payments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("vpn_accounts.id", ondelete="SET NULL")),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("order_ref", sa.String(80), nullable=False, unique=True),
        sa.Column("provider_ref", sa.String(80)),
        sa.Column("network", sa.String(16)),
        sa.Column("amount_usd", sa.Float(), nullable=False),
        sa.Column("paid_usd", sa.Float(), nullable=False, server_default="0"),
        sa.Column("seconds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(12), nullable=False, server_default="pending"),
        sa.Column("pay_address", sa.String(128)),
        sa.Column("pay_amount", sa.Float()),
        sa.Column("credited_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_vpn_payments_account_id", "vpn_payments", ["account_id"])
    with op.batch_alter_table("vpn_devices") as b:
        b.add_column(sa.Column("account_id", sa.Integer()))
    op.execute(sa.text("INSERT INTO vpn_accounts (member_id, status, created_at, updated_at) "
                       "SELECT DISTINCT member_id, 'active', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP FROM vpn_devices"))
    op.execute(sa.text("UPDATE vpn_devices SET account_id = (SELECT id FROM vpn_accounts WHERE vpn_accounts.member_id = vpn_devices.member_id)"))
    with op.batch_alter_table("vpn_devices") as b:
        b.drop_index("ix_vpn_devices_member_id")
        b.drop_column("member_id")
        b.alter_column("account_id", existing_type=sa.Integer(), nullable=False)
        b.create_foreign_key("fk_vpn_devices_account_id_vpn_accounts", "vpn_accounts", ["account_id"], ["id"], ondelete="CASCADE")
        b.create_index("ix_vpn_devices_account_id", ["account_id"])


def downgrade() -> None:
    with op.batch_alter_table("vpn_devices") as b:
        b.add_column(sa.Column("member_id", sa.Integer()))
    op.execute(sa.text("UPDATE vpn_devices SET member_id = (SELECT member_id FROM vpn_accounts WHERE vpn_accounts.id = vpn_devices.account_id)"))
    op.execute(sa.text("DELETE FROM vpn_devices WHERE member_id IS NULL"))  # anonymous devices have no member to return to
    with op.batch_alter_table("vpn_devices") as b:
        b.drop_index("ix_vpn_devices_account_id")
        b.drop_constraint("fk_vpn_devices_account_id_vpn_accounts", type_="foreignkey")
        b.drop_column("account_id")
        b.alter_column("member_id", existing_type=sa.Integer(), nullable=False)
        b.create_foreign_key("fk_vpn_devices_member_id_members", "members", ["member_id"], ["id"], ondelete="CASCADE")
        b.create_index("ix_vpn_devices_member_id", ["member_id"])
    op.drop_table("vpn_payments")
    op.drop_table("vpn_accounts")
