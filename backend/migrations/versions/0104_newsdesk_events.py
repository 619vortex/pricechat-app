"""The News Desk's shared event ledger: one editorial decision per story or market move, delivered by X and Telegram independently.

Revision ID: 0104_newsdesk_events
Revises: 0103_newsdesk_telegram
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0104_newsdesk_events'
down_revision: str | None = '0103_newsdesk_telegram'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "nd_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("key", sa.String(200), nullable=False, unique=True),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("parent_id", sa.Integer()),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("headline", sa.String(300), nullable=False),
        sa.Column("summary", sa.Text(), server_default="", nullable=False),
        sa.Column("topic", sa.String(16), server_default="", nullable=False),
        sa.Column("label", sa.String(24), server_default="", nullable=False),
        sa.Column("evidence", sa.String(16), server_default="", nullable=False),
        sa.Column("category", sa.String(16), server_default="viral", nullable=False),
        sa.Column("virality", sa.Float(), server_default="0", nullable=False),
        sa.Column("parts", sa.JSON(), nullable=False),
        sa.Column("sources", sa.JSON(), nullable=False),
        sa.Column("facts", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(12), server_default="candidate", nullable=False),
        sa.Column("slot", sa.String(10), server_default="", nullable=False),
        sa.Column("first_seen", sa.DateTime(timezone=True)),
        sa.Column("released_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("why", sa.Text(), server_default="", nullable=False),
        sa.Column("x_status", sa.String(12), server_default="pending", nullable=False),
        sa.Column("x_post_id", sa.Integer()),
        sa.Column("x_note", sa.Text(), server_default="", nullable=False),
        sa.Column("tg_status", sa.String(12), server_default="pending", nullable=False),
        sa.Column("tg_post_id", sa.Integer()),
        sa.Column("tg_message_id", sa.BigInteger()),
        sa.Column("tg_note", sa.Text(), server_default="", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_nd_events_status", "nd_events", ["status", "released_at"])


def downgrade() -> None:
    op.drop_index("ix_nd_events_status", table_name="nd_events")
    op.drop_table("nd_events")
