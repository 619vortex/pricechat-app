"""Worker invites: rotate the "still a member" check; index telegram messages by author (engagement counts).

Revision ID: 0089_workers_checks
Revises: 0088_vortex_assist
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0089_workers_checks'
down_revision: str | None = '0088_vortex_assist'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("wk_submissions", sa.Column("member_checked_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_telegram_messages_author", "telegram_messages", ["author_id", "chat_id", "sent_at"])


def downgrade() -> None:
    op.drop_index("ix_telegram_messages_author", table_name="telegram_messages")
    op.drop_column("wk_submissions", "member_checked_at")
