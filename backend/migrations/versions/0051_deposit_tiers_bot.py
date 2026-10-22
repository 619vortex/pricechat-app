"""Earn: $10 minimum deposit, the trading bot's Telegram username and Hyperliquid account (public track record)

Revision ID: 0051
Revises: 0050
Create Date: 2026-10-15 16:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0051'
down_revision: str | None = '0050'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('tb_settings', schema=None) as batch_op:
        batch_op.add_column(sa.Column('bot_username', sa.String(length=64), server_default='iqleveling_bot', nullable=False))
        batch_op.add_column(sa.Column('bot_wallet', sa.String(length=42), nullable=True))
    op.execute(sa.text("UPDATE tb_settings SET min_deposit_usd = 10 WHERE min_deposit_usd = 100"))
    # wallets checked against $100 that already hold $10+ in deposits now meet the minimum
    op.execute(sa.text("UPDATE tb_wallets SET deposit_status = 'verified' WHERE deposit_status = 'insufficient' AND deposit_usd >= 10"))


def downgrade() -> None:
    with op.batch_alter_table('tb_settings', schema=None) as batch_op:
        batch_op.drop_column('bot_wallet')
        batch_op.drop_column('bot_username')
