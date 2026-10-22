"""Kiwi Trading Engine v2, stage 1: structured trade decisions with shadow outcomes

- ts_decisions: one row per kitchen candidate (strategy, market, closed setup candle): the full plan, every check, the
  decision (READY / REJECTED + the failing check) and, once the candles after it exist, what the plan would have done
  (hypothetical, after fees, slippage and funding; MAE / MFE)

Revision ID: 0069
Revises: 0068
Create Date: 2026-10-19 14:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0069'
down_revision: str | None = '0068'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ts_decisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("strategy", sa.String(48), nullable=False),
        sa.Column("version", sa.String(24), nullable=False),
        sa.Column("coin", sa.String(32), nullable=False),
        sa.Column("timeframe", sa.String(4), nullable=False),
        sa.Column("candle_t", sa.BigInteger(), nullable=False),
        sa.Column("model", sa.String(24), nullable=False),
        sa.Column("direction", sa.String(5), nullable=False),
        sa.Column("regime", sa.String(20)),
        sa.Column("regime_confidence", sa.Float()),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("decision", sa.String(8), nullable=False),
        sa.Column("rejected_by", sa.String(24)),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("shadow", sa.Boolean(), nullable=False),
        sa.Column("signal_id", sa.Integer(), sa.ForeignKey("ts_signals.id", ondelete="SET NULL")),
        sa.Column("entry_reasons", sa.JSON(), nullable=False),
        sa.Column("ref", sa.Float(), nullable=False),
        sa.Column("stop", sa.Float(), nullable=False),
        sa.Column("tp1", sa.Float(), nullable=False),
        sa.Column("tp2", sa.Float(), nullable=False),
        sa.Column("r1", sa.Float(), nullable=False),
        sa.Column("r2", sa.Float(), nullable=False),
        sa.Column("max_hold_min", sa.Integer(), nullable=False),
        sa.Column("est_cost_r", sa.Float()),
        sa.Column("est_fees_r", sa.Float()),
        sa.Column("est_slippage_r", sa.Float()),
        sa.Column("rr_net", sa.Float()),
        sa.Column("score", sa.Integer()),
        sa.Column("ml_p", sa.Float()),
        sa.Column("checks", sa.JSON(), nullable=False),
        sa.Column("market", sa.JSON(), nullable=False),
        sa.Column("outcome", sa.String(8), nullable=False, server_default="PENDING"),
        sa.Column("r_net", sa.Float()),
        sa.Column("r_gross", sa.Float()),
        sa.Column("cost_r", sa.Float()),
        sa.Column("mae_r", sa.Float()),
        sa.Column("mfe_r", sa.Float()),
        sa.Column("exit_kind", sa.String(16)),
        sa.Column("hold_min", sa.Float()),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("strategy", "coin", "candle_t", name="uq_ts_decisions_candidate"),
    )
    op.create_index("ix_ts_decisions_outcome", "ts_decisions", ["outcome"])
    op.create_index("ix_ts_decisions_created", "ts_decisions", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_ts_decisions_created", "ts_decisions")
    op.drop_index("ix_ts_decisions_outcome", "ts_decisions")
    op.drop_table("ts_decisions")
