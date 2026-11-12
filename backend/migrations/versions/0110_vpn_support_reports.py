"""Kiwi VPN support: bug reports and feedback members send from the apps, listed in Admin.

Numbered 0110 because feat/kiwi-production (not merged) already uses 0108 and 0109. It revises main's head 0107: if
feat/kiwi-production merges first, change down_revision below to '0109_vpn_server_checks' (one head, no data change).

Revision ID: 0110_vpn_support_reports
Revises: 0107_vpn_payment_recovery
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0110_vpn_support_reports'
down_revision: str | None = '0107_vpn_payment_recovery'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "vpn_support_reports",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("member_id", sa.Integer(), sa.ForeignKey("members.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(12), nullable=False),
        sa.Column("source", sa.String(12), nullable=False, server_default="web"),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("app_version", sa.String(32)),
        sa.Column("diagnostics", sa.Text()),
        sa.Column("done_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_vpn_support_reports_member_id", "vpn_support_reports", ["member_id"])


def downgrade() -> None:
    op.drop_table("vpn_support_reports")
