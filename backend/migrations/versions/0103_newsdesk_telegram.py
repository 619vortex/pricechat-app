"""The News Desk's Telegram lane: its settings and what it sent. Adds two tables; the X tables aren't changed.

Revision ID: 0103_newsdesk_telegram
Revises: 0102_desktop_codes
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0103_newsdesk_telegram'
down_revision: str | None = '0102_desktop_codes'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "nd_telegram_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("max_per_day", sa.Integer(), server_default="10", nullable=False),
        sa.Column("gap_minutes", sa.Integer(), server_default="45", nullable=False),
        sa.Column("state", sa.JSON(), nullable=False),
    )
    op.create_table(
        "nd_telegram_posts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("story_link", sa.String(500), nullable=False, unique=True),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("headline", sa.String(200), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("source", sa.String(60), nullable=False),
        sa.Column("sources", sa.JSON(), nullable=False),
        sa.Column("fingerprint", sa.Text(), server_default="", nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("writer", sa.String(10)),
        sa.Column("chat", sa.String(64)),
        sa.Column("chat_id", sa.BigInteger()),
        sa.Column("message_id", sa.BigInteger()),
        sa.Column("error", sa.Text()),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_nd_telegram_posts_status", "nd_telegram_posts", ["status", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_nd_telegram_posts_status", table_name="nd_telegram_posts")
    op.drop_table("nd_telegram_posts")
    op.drop_table("nd_telegram_settings")
