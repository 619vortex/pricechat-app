"""Kiwi AI Investment Committee (app/committee): runs, Vortex LLM jobs, daily usage, outcomes, paper book, backtests

- cm_runs: one committee run (the public run JSON in `result`)
- cm_llm_jobs: LLM calls handed to Vortex over /api/vortex/claim
- cm_usage: per person / global daily counters (runs, LLM calls, tokens, USD): budgets survive restarts
- cm_outcomes: the market's move after a run (first close after the decision) at 1h / 4h / 24h / 7d
- cm_paper_trades, cm_backtests: filled by stage 2 (paper book, backtests); never touch tr_positions

Revision ID: 0091_committee
Revises: 0090_perf_indexes
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0091_committee'
down_revision: str | None = '0090_perf_indexes'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "cm_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(24), nullable=False, unique=True),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("venue", sa.String(16)),
        sa.Column("market_type", sa.String(8)),
        sa.Column("market_id", sa.String(128)),
        sa.Column("timeframe", sa.String(4), nullable=False),
        sa.Column("mode", sa.String(8), nullable=False),
        sa.Column("depth", sa.String(6), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("requested_by_tg", sa.BigInteger()),
        sa.Column("requested_by_member", sa.Integer()),
        sa.Column("chat_id", sa.BigInteger()),
        sa.Column("message_id", sa.BigInteger()),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("started_at", TS),
        sa.Column("finished_at", TS),
        sa.Column("price_at", sa.Float()),
        sa.Column("verdict", sa.String(5)),
        sa.Column("rating", sa.String(12)),
        sa.Column("exec_status", sa.String(16)),
        sa.Column("backend", sa.String(12)),
        sa.Column("cost_usd", sa.Float(), nullable=False),
        sa.Column("calls", sa.Integer(), nullable=False),
        sa.Column("result", sa.JSON()),
        sa.Column("error", sa.Text()),
    )
    op.create_index("ix_cm_runs_market", "cm_runs", ["symbol", "timeframe", "created_at"])
    op.create_index("ix_cm_runs_status", "cm_runs", ["status"])
    op.create_index("ix_cm_runs_tg", "cm_runs", ["requested_by_tg", "created_at"])
    op.create_table(
        "cm_llm_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(24), nullable=False, unique=True),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("cm_runs.id", ondelete="CASCADE")),
        sa.Column("role", sa.String(24), nullable=False),
        sa.Column("tier", sa.String(10), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("system", sa.Text(), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("schema", sa.JSON()),
        sa.Column("max_tokens", sa.Integer(), nullable=False),
        sa.Column("temperature", sa.Float(), nullable=False),
        sa.Column("answer", sa.Text()),
        sa.Column("model", sa.String(64)),
        sa.Column("tokens_in", sa.Integer()),
        sa.Column("tokens_out", sa.Integer()),
        sa.Column("claims", sa.Integer(), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("leased_until", TS),
        sa.Column("answered_at", TS),
    )
    op.create_index("ix_cm_llm_jobs_status", "cm_llm_jobs", ["status", "created_at"])
    op.create_table(
        "cm_usage",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("day", sa.String(10), nullable=False),
        sa.Column("key", sa.String(32), nullable=False),
        sa.Column("tg_user_id", sa.BigInteger()),
        sa.Column("member_id", sa.Integer()),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.Column("calls", sa.Integer(), nullable=False),
        sa.Column("tokens_in", sa.Integer(), nullable=False),
        sa.Column("tokens_out", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Float(), nullable=False),
        sa.UniqueConstraint("day", "key", name="uq_cm_usage_day_key"),
    )
    op.create_table(
        "cm_outcomes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("cm_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("horizon", sa.String(4), nullable=False),
        sa.Column("price_from", sa.Float(), nullable=False),
        sa.Column("price_to", sa.Float(), nullable=False),
        sa.Column("ret_pct", sa.Float(), nullable=False),
        sa.Column("direction_ok", sa.Boolean()),
        sa.Column("mfe_pct", sa.Float()),
        sa.Column("mae_pct", sa.Float()),
        sa.Column("resolved_at", TS, nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.UniqueConstraint("run_id", "horizon", name="uq_cm_outcomes_run_horizon"),
    )
    op.create_table(
        "cm_paper_trades",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("cm_runs.id", ondelete="SET NULL")),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("side", sa.String(5), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("decided_at", TS, nullable=False),
        sa.Column("entry_ref", sa.Float()),
        sa.Column("entry_fill", sa.Float()),
        sa.Column("size_usd", sa.Float()),
        sa.Column("stop", sa.Float()),
        sa.Column("targets", sa.JSON()),
        sa.Column("exit_fill", sa.Float()),
        sa.Column("exit_reason", sa.String(32)),
        sa.Column("fees_usd", sa.Float(), nullable=False),
        sa.Column("funding_usd", sa.Float(), nullable=False),
        sa.Column("slippage_usd", sa.Float(), nullable=False),
        sa.Column("pnl_usd", sa.Float()),
        sa.Column("pnl_r", sa.Float()),
        sa.Column("risk_check", sa.JSON()),
        sa.Column("model_versions", sa.JSON()),
        sa.Column("research_cost_usd", sa.Float(), nullable=False),
        sa.Column("opened_at", TS),
        sa.Column("closed_at", TS),
    )
    op.create_index("ix_cm_paper_trades_status", "cm_paper_trades", ["status", "decided_at"])
    op.create_table(
        "cm_backtests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON()),
        sa.Column("status", sa.String(10), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("cm_backtests")
    op.drop_index("ix_cm_paper_trades_status", table_name="cm_paper_trades")
    op.drop_table("cm_paper_trades")
    op.drop_table("cm_outcomes")
    op.drop_table("cm_usage")
    op.drop_index("ix_cm_llm_jobs_status", table_name="cm_llm_jobs")
    op.drop_table("cm_llm_jobs")
    op.drop_index("ix_cm_runs_tg", table_name="cm_runs")
    op.drop_index("ix_cm_runs_status", table_name="cm_runs")
    op.drop_index("ix_cm_runs_market", table_name="cm_runs")
    op.drop_table("cm_runs")
