"""marketplace: wallets proven by signature (EVM), and their one-time challenges

Revision ID: 0044
Revises: 0043
Create Date: 2026-10-11 13:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0044'
down_revision: str | None = '0043'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "market_wallets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("member_id", sa.Integer(), sa.ForeignKey("members.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chain", sa.String(length=12), nullable=False),
        sa.Column("address", sa.String(length=64), nullable=False),
        sa.Column("wallet", sa.String(length=40), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payout", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("chain", "address"),
    )
    op.create_index("ix_market_wallets_member_id", "market_wallets", ["member_id"])
    op.create_table(
        "market_wallet_challenges",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("member_id", sa.Integer(), sa.ForeignKey("members.id", ondelete="CASCADE"), nullable=False),
        sa.Column("address", sa.String(length=64), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_market_wallet_challenges_member_id", "market_wallet_challenges", ["member_id"])


def downgrade() -> None:
    op.drop_index("ix_market_wallet_challenges_member_id", table_name="market_wallet_challenges")
    op.drop_table("market_wallet_challenges")
    op.drop_index("ix_market_wallets_member_id", table_name="market_wallets")
    op.drop_table("market_wallets")
