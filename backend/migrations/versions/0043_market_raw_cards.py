"""marketplace: raw cards (AI condition on the item), community condition grades and listing comments

Revision ID: 0043
Revises: 0042
Create Date: 2026-10-11 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0043'
down_revision: str | None = '0042'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("market_items") as b:
        b.add_column(sa.Column("condition_low", sa.Float(), nullable=True))
        b.add_column(sa.Column("condition_high", sa.Float(), nullable=True))
        b.add_column(sa.Column("condition_report", sa.JSON(), nullable=True))
    op.create_table(
        "market_condition_votes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("listing_id", sa.Integer(), sa.ForeignKey("market_listings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("member_id", sa.Integer(), sa.ForeignKey("members.id", ondelete="CASCADE"), nullable=False),
        sa.Column("estimate", sa.Integer(), nullable=False),
        sa.Column("note", sa.String(length=280), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("listing_id", "member_id"),
    )
    op.create_index("ix_market_condition_votes_listing_id", "market_condition_votes", ["listing_id"])
    op.create_table(
        "market_comments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("listing_id", sa.Integer(), sa.ForeignKey("market_listings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("member_id", sa.Integer(), sa.ForeignKey("members.id", ondelete="CASCADE"), nullable=False),
        sa.Column("parent_id", sa.Integer(), sa.ForeignKey("market_comments.id", ondelete="CASCADE"), nullable=True),
        sa.Column("body", sa.String(length=1000), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_market_comments_listing_id", "market_comments", ["listing_id"])


def downgrade() -> None:
    op.drop_index("ix_market_comments_listing_id", table_name="market_comments")
    op.drop_table("market_comments")
    op.drop_index("ix_market_condition_votes_listing_id", table_name="market_condition_votes")
    op.drop_table("market_condition_votes")
    with op.batch_alter_table("market_items") as b:
        b.drop_column("condition_report")
        b.drop_column("condition_high")
        b.drop_column("condition_low")
