"""Earn: the trading bot is Price Chat's own @cryptopricechatbot (the third-party bot is removed)

Revision ID: 0054
Revises: 0053
Create Date: 2026-10-15 19:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0054'
down_revision: str | None = '0053'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(sa.text("UPDATE tb_settings SET bot_username = 'cryptopricechatbot' WHERE bot_username = 'iqleveling_bot'"))


def downgrade() -> None:
    pass
