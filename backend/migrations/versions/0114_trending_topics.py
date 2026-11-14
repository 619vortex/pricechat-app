"""Trending Topics (app/trending): settings, source signals, scored topics and suggested tweets.

Production ran this revision on 2026-11-14 (Railway deploy 569ccbf5): never change its revision or down_revision. The Kiwi release
(release/kiwi-hardening) chains its own migrations (0115, 0116) after 0114, so merging keeps one head without touching this file.

Revision ID: 0114_trending_topics
Revises: 0111_vpn_server_traffic_allowance
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0114_trending_topics'
down_revision: str | None = '0111_vpn_server_traffic_allowance'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _times() -> list[sa.Column]:
    return [sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False)]


def upgrade() -> None:
    op.create_table(
        "tt_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("mode", sa.String(10), nullable=False, server_default="automatic"),
        sa.Column("auto_confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("live", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("kill_switch", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("hot_score", sa.Float(), nullable=False, server_default="75"),
        sa.Column("suggest_score", sa.Float(), nullable=False, server_default="45"),
        sa.Column("max_per_day", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("gap_minutes", sa.Integer(), nullable=False, server_default="180"),
        sa.Column("topic_cooldown_hours", sa.Integer(), nullable=False, server_default="24"),
        sa.Column("newsdesk_gap_minutes", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("shared_daily_cap", sa.Integer(), nullable=False, server_default="20"),
        sa.Column("categories", sa.JSON(), nullable=False),
        sa.Column("sources", sa.JSON(), nullable=False),
        sa.Column("state", sa.JSON(), nullable=False),
    )
    op.create_table(
        "tt_signals",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("external_id", sa.String(300), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("url", sa.String(500), nullable=False),
        sa.Column("outlet", sa.String(80), nullable=False),
        sa.Column("reliable", sa.Boolean(), nullable=False),
        sa.Column("category", sa.String(16)),
        sa.Column("engagement", sa.Float(), nullable=False),
        sa.Column("history", sa.JSON(), nullable=False),
        sa.Column("extra", sa.JSON(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
        *_times(),
        sa.UniqueConstraint("source", "external_id", name="uq_tt_signals_source_external"),
    )
    op.create_index("ix_tt_signals_last_seen", "tt_signals", ["last_seen"])
    op.create_table(
        "tt_topics",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("key", sa.String(200), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("category", sa.String(16), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("peak_score", sa.Float(), nullable=False),
        sa.Column("parts", sa.JSON(), nullable=False),
        sa.Column("signal_ids", sa.JSON(), nullable=False),
        sa.Column("sources", sa.JSON(), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("posted_at", sa.DateTime(timezone=True)),
        *_times(),
        sa.UniqueConstraint("key"),
    )
    op.create_index("ix_tt_topics_status", "tt_topics", ["status", "score"])
    op.create_table(
        "tt_posts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("topic_id", sa.Integer(), nullable=False),
        sa.Column("category", sa.String(16), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("original_text", sa.Text()),
        sa.Column("angle", sa.String(24), nullable=False),
        sa.Column("writer", sa.String(10), nullable=False),
        sa.Column("fingerprint", sa.Text(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("sources", sa.JSON(), nullable=False),
        sa.Column("source_text", sa.Text(), nullable=False),
        sa.Column("checks", sa.JSON(), nullable=False),
        sa.Column("hold_reason", sa.Text()),
        sa.Column("note", sa.Text()),
        sa.Column("error", sa.Text()),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("x_id", sa.String(32)),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("metrics_at", sa.DateTime(timezone=True)),
        *_times(),
    )
    op.create_index("ix_tt_posts_status", "tt_posts", ["status", "created_at"])
    op.create_index("ix_tt_posts_topic", "tt_posts", ["topic_id"])


def downgrade() -> None:
    op.drop_index("ix_tt_posts_topic", "tt_posts")
    op.drop_index("ix_tt_posts_status", "tt_posts")
    op.drop_table("tt_posts")
    op.drop_index("ix_tt_topics_status", "tt_topics")
    op.drop_table("tt_topics")
    op.drop_index("ix_tt_signals_last_seen", "tt_signals")
    op.drop_table("tt_signals")
    op.drop_table("tt_settings")
