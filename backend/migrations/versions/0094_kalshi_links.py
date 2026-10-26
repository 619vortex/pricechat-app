"""Earn › Kalshi: members' read-only Kalshi API links (kalshi_links) + kalshi_bot.members_open

Revision ID: 0094_kalshi_links
Revises: 0093_kalshi_bot
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0094_kalshi_links'
down_revision: str | None = '0093_kalshi_bot'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("kalshi_bot") as b:
        b.add_column(sa.Column("members_open", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_table(
        "kalshi_links",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("member_id", sa.Integer(), sa.ForeignKey("members.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("env", sa.String(16), nullable=False),
        sa.Column("key_id", sa.String(64), nullable=False),
        sa.Column("key_enc", sa.Text(), nullable=False),
        sa.Column("key_type", sa.String(16), nullable=False),
        sa.Column("connected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_ok_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
    )


def downgrade() -> None:
    op.drop_table("kalshi_links")
    with op.batch_alter_table("kalshi_bot") as b:
        b.drop_column("members_open")
