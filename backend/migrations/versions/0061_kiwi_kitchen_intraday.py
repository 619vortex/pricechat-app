"""kiwi kitchen: 15m/30m strategy state, trading-style settings, trade notifications

Revision ID: 0061
Revises: 0060
Create Date: 2026-10-17 09:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0061'
down_revision: str | None = '0060'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('ts_kitchen',
    sa.Column('coin', sa.String(length=32), nullable=False),
    sa.Column('state', sa.String(length=12), nullable=False),
    sa.Column('candle_t', sa.BigInteger(), nullable=True),
    sa.Column('signal_id', sa.Integer(), nullable=True),
    sa.Column('event_id', sa.Integer(), nullable=True),
    sa.Column('data', sa.JSON(), nullable=False),
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['signal_id'], ['ts_signals.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('coin')
    )
    with op.batch_alter_table('tr_accounts', schema=None) as batch_op:
        batch_op.add_column(sa.Column('timeframe_pref', sa.String(length=8), server_default='1h', nullable=False))
        batch_op.add_column(sa.Column('frequency', sa.String(length=8), server_default='standard', nullable=False))
        batch_op.add_column(sa.Column('max_trades_per_day', sa.Integer(), server_default='6', nullable=False))
        batch_op.add_column(sa.Column('cooldown_minutes', sa.Integer(), server_default='30', nullable=False))
        batch_op.add_column(sa.Column('intraday_live_at', sa.DateTime(timezone=True), nullable=True))
    with op.batch_alter_table('tr_positions', schema=None) as batch_op:
        batch_op.add_column(sa.Column('announced_open_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('announced_close_at', sa.DateTime(timezone=True), nullable=True))
    with op.batch_alter_table('kb_events', schema=None) as batch_op:
        batch_op.create_index('ix_kb_events_source_id', ['source', 'id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('kb_events', schema=None) as batch_op:
        batch_op.drop_index('ix_kb_events_source_id')
    with op.batch_alter_table('tr_positions', schema=None) as batch_op:
        batch_op.drop_column('announced_close_at')
        batch_op.drop_column('announced_open_at')
    with op.batch_alter_table('tr_accounts', schema=None) as batch_op:
        batch_op.drop_column('intraday_live_at')
        batch_op.drop_column('cooldown_minutes')
        batch_op.drop_column('max_trades_per_day')
        batch_op.drop_column('frequency')
        batch_op.drop_column('timeframe_pref')
    op.drop_table('ts_kitchen')
