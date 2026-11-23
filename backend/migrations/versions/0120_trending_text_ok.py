"""Trending Topics: a tweet with no picture and no link is held; the owner may send it as words alone (2026-11-23).

Adds tt_posts.text_ok (False for every existing tweet).

Revision ID: 0120_trending_text_ok
Revises: 0119_vpn_review_seats
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0120_trending_text_ok'
down_revision: str | None = '0119_vpn_review_seats'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tt_posts", sa.Column("text_ok", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table("tt_posts") as batch:
        batch.drop_column("text_ok")
