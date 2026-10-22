"""Main-chat tidy-up: the bot's messages waiting to be deleted (tradingos.autodelete).

Revision ID: 0084_autodelete
Revises: 0083_funding_alerts
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0084_autodelete'
down_revision: str | None = '0083_funding_alerts'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ts_autodelete",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column("delete_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_ts_autodelete_due", "ts_autodelete", ["delete_at"])


def downgrade() -> None:
    op.drop_index("ix_ts_autodelete_due", table_name="ts_autodelete")
    op.drop_table("ts_autodelete")
