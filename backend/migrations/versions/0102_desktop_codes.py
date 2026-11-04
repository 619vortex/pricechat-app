"""One-time sign-in codes for the Kiwi VPN desktop app, and named testers who may use the VPN while it's closed. Adds one table and one empty column.

Revision ID: 0102_desktop_codes
Revises: 0101_newsdesk
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0102_desktop_codes'
down_revision: str | None = '0101_newsdesk'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "member_desktop_codes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("challenge", sa.String(43), nullable=False),
        sa.Column("member_id", sa.Integer(), sa.ForeignKey("members.id", ondelete="CASCADE"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_member_desktop_codes_member_id", "member_desktop_codes", ["member_id"])
    op.add_column("vpn_accounts", sa.Column("tester_until", sa.DateTime(timezone=True)))


def downgrade() -> None:
    with op.batch_alter_table("vpn_accounts") as batch:
        batch.drop_column("tester_until")
    op.drop_table("member_desktop_codes")
