"""News Desk engagement per published post and platform (Telegram views/forwards/replies/reactions, X impressions/reposts/replies/likes).

Revision ID: 0112_newsdesk_engagement
Revises: 0111_vpn_server_traffic_allowance
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0112_newsdesk_engagement'
down_revision: str | None = '0111_vpn_server_traffic_allowance'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "nd_engagement",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_id", sa.Integer()),
        sa.Column("platform", sa.String(10), nullable=False),
        sa.Column("post_ref", sa.String(64), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("measured_at", sa.DateTime(timezone=True)),
        sa.Column("views", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("shares", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("replies", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reactions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rate", sa.Float()),
        sa.Column("features", sa.JSON(), nullable=False),
        sa.Column("raw", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("platform", "post_ref"),
    )
    op.create_index("ix_nd_engagement_event", "nd_engagement", ["event_id"])


def downgrade() -> None:
    op.drop_index("ix_nd_engagement_event", "nd_engagement")
    op.drop_table("nd_engagement")
