"""Kiwi VPN server health history: one row per server per 5-minute sync, kept 14 days (app/vpn/health.py).

Numbered 0115 (first written as 0112): production already ran 0114_trending_topics (feat/trending-topics, based on 0111), so the
Kiwi VPN migrations follow it. No schema change from the 0112 version.

Revision ID: 0115_vpn_server_checks
Revises: 0114_trending_topics
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0115_vpn_server_checks'
down_revision: str | None = '0114_trending_topics'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "vpn_server_checks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("server_id", sa.Integer(), sa.ForeignKey("vpn_servers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column("ms", sa.Integer()),
        sa.Column("peers", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("live", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.String(120)),
    )
    op.create_index("ix_vpn_server_checks_server_at", "vpn_server_checks", ["server_id", "at"])


def downgrade() -> None:
    op.drop_index("ix_vpn_server_checks_server_at", table_name="vpn_server_checks")
    op.drop_table("vpn_server_checks")
