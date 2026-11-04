"""The News Desk: settings, stories read, and posts. Starts in dry run; adds tables only.

Revision ID: 0101_newsdesk
Revises: 0100_vpn_trial
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0101_newsdesk'
down_revision: str | None = '0100_vpn_trial'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "nd_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("automation", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("mode", sa.String(10), nullable=False, server_default="dry_run"),
        sa.Column("kill_switch", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("paused_until", sa.DateTime(timezone=True)),
        sa.Column("pause_reason", sa.Text()),
        sa.Column("target_per_day", sa.Integer(), nullable=False, server_default="12"),
        sa.Column("max_breaking_per_day", sa.Integer(), nullable=False, server_default="6"),
        sa.Column("breaking_gap_minutes", sa.Integer(), nullable=False, server_default="20"),
        sa.Column("sensitivity", sa.String(10), nullable=False, server_default="medium"),
        sa.Column("categories", sa.JSON(), nullable=False),
        sa.Column("feeds", sa.JSON(), nullable=False),
        sa.Column("watchlist", sa.JSON(), nullable=False),
        sa.Column("state", sa.JSON(), nullable=False),
    )
    op.create_table(
        "nd_stories",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("link", sa.String(500), nullable=False, unique=True),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("source", sa.String(60), nullable=False),
        sa.Column("tier", sa.Integer(), nullable=False),
        sa.Column("category", sa.String(16)),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("fingerprint", sa.Text(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("used", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_nd_stories_created", "nd_stories", ["created_at"])
    op.create_table(
        "nd_posts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("category", sa.String(16), nullable=False),
        sa.Column("priority", sa.String(10), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("original_text", sa.Text()),
        sa.Column("key", sa.String(160), nullable=False),
        sa.Column("fingerprint", sa.Text(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("facts", sa.JSON(), nullable=False),
        sa.Column("sources", sa.JSON(), nullable=False),
        sa.Column("checks", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("not_before", sa.DateTime(timezone=True)),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text()),
        sa.Column("x_id", sa.String(32)),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_nd_posts_status", "nd_posts", ["status", "created_at"])
    op.create_index("ix_nd_posts_key", "nd_posts", ["key", "created_at"])


def downgrade() -> None:
    op.drop_table("nd_posts")
    op.drop_table("nd_stories")
    op.drop_table("nd_settings")
