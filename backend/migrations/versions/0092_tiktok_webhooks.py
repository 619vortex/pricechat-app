"""TikTok webhooks (/api/webhooks/tiktok): bm_tiktok_webhook_events, one row per accepted delivery (duplicate protection)

Revision ID: 0092_tiktok_webhooks
Revises: 0091_committee
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0092_tiktok_webhooks'
down_revision: str | None = '0091_committee'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "bm_tiktok_webhook_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("body_sha256", sa.String(64), nullable=False),
        sa.Column("event", sa.String(64), nullable=False),
        sa.Column("user_openid", sa.String(128)),
        sa.Column("create_time", sa.BigInteger()),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("body_sha256", name="uq_bm_tiktok_webhook_events_body"),
    )


def downgrade() -> None:
    op.drop_table("bm_tiktok_webhook_events")
