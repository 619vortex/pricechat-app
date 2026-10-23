"""Kiwi AI Investment Committee (app/committee): research runs, the LLM jobs Vortex answers, usage counters, outcomes, and the
tables later stages fill (paper book, backtests). Research only: nothing here is an order, a position or a member account;
the paper book is its own table and never touches tr_positions."""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, Boolean, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, UTCDateTime, utcnow


class CmRun(IdMixin, Base):
    """One committee run. status: queued · running · done · failed. `result` is the public run JSON (no member data)."""

    __tablename__ = "cm_runs"
    __table_args__ = (Index("ix_cm_runs_market", "symbol", "timeframe", "created_at"), Index("ix_cm_runs_status", "status"),
                      Index("ix_cm_runs_tg", "requested_by_tg", "created_at"))

    public_id: Mapped[str] = mapped_column(String(24), unique=True, nullable=False)  # "cm_<hex>"
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    venue: Mapped[str | None] = mapped_column(String(16))
    market_type: Mapped[str | None] = mapped_column(String(8))
    market_id: Mapped[str | None] = mapped_column(String(128))
    timeframe: Mapped[str] = mapped_column(String(4), nullable=False)
    mode: Mapped[str] = mapped_column(String(8), nullable=False)  # live | backtest
    depth: Mapped[str] = mapped_column(String(6), nullable=False)  # full | quick
    status: Mapped[str] = mapped_column(String(10), nullable=False)
    requested_by_tg: Mapped[int | None] = mapped_column(BigInteger)
    requested_by_member: Mapped[int | None] = mapped_column(Integer)
    chat_id: Mapped[int | None] = mapped_column(BigInteger)
    message_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    price_at: Mapped[float | None] = mapped_column(Float)
    verdict: Mapped[str | None] = mapped_column(String(5))  # LONG | SHORT | WAIT
    rating: Mapped[str | None] = mapped_column(String(12))
    exec_status: Mapped[str | None] = mapped_column(String(16))
    backend: Mapped[str | None] = mapped_column(String(12))
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    calls: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)


class CmLlmJob(IdMixin, Base):
    """One LLM call handed to Vortex over /api/vortex/claim. status: pending · claimed · answered · failed · expired.
    The answer is raw text for the committee (grounded there), never posted anywhere as is."""

    __tablename__ = "cm_llm_jobs"
    __table_args__ = (Index("ix_cm_llm_jobs_status", "status", "created_at"),)

    public_id: Mapped[str] = mapped_column(String(24), unique=True, nullable=False)  # "l_<hex>"
    run_id: Mapped[int | None] = mapped_column(ForeignKey("cm_runs.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(24), nullable=False)
    tier: Mapped[str] = mapped_column(String(10), nullable=False)  # fast | deep | synthesis
    status: Mapped[str] = mapped_column(String(10), nullable=False)
    system: Mapped[str] = mapped_column(Text, nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    schema: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    max_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    temperature: Mapped[float] = mapped_column(Float, nullable=False)
    answer: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(String(64))
    tokens_in: Mapped[int | None] = mapped_column(Integer)
    tokens_out: Mapped[int | None] = mapped_column(Integer)
    claims: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    leased_until: Mapped[datetime | None] = mapped_column(UTCDateTime())
    answered_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class CmUsage(IdMixin, Base):
    """Daily counters (UTC day). key: "tg:<id>" / "m:<id>" (runs a person started: count) or "global" (LLM calls, tokens,
    USD of the day, so a restart never resets the budgets)."""

    __tablename__ = "cm_usage"
    __table_args__ = (UniqueConstraint("day", "key", name="uq_cm_usage_day_key"),)

    day: Mapped[str] = mapped_column(String(10), nullable=False)  # YYYY-MM-DD
    key: Mapped[str] = mapped_column(String(32), nullable=False)
    tg_user_id: Mapped[int | None] = mapped_column(BigInteger)
    member_id: Mapped[int | None] = mapped_column(Integer)
    count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    calls: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)


class CmOutcome(IdMixin, Base):
    """What the market did after a run, from Hyperliquid candles: price_from is the first close AFTER the run was created."""

    __tablename__ = "cm_outcomes"
    __table_args__ = (UniqueConstraint("run_id", "horizon", name="uq_cm_outcomes_run_horizon"),)

    run_id: Mapped[int] = mapped_column(ForeignKey("cm_runs.id", ondelete="CASCADE"), nullable=False)
    horizon: Mapped[str] = mapped_column(String(4), nullable=False)  # 1h | 4h | 24h | 7d
    price_from: Mapped[float] = mapped_column(Float, nullable=False)
    price_to: Mapped[float] = mapped_column(Float, nullable=False)
    ret_pct: Mapped[float] = mapped_column(Float, nullable=False)
    direction_ok: Mapped[bool | None] = mapped_column(Boolean)  # None for WAIT (no position; the move is still recorded)
    mfe_pct: Mapped[float | None] = mapped_column(Float)
    mae_pct: Mapped[float | None] = mapped_column(Float)
    resolved_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)


class CmPaperTrade(IdMixin, Base):
    """The committee's own paper book (stage 2). status: open · closed · rejected. Never a tr_positions row."""

    __tablename__ = "cm_paper_trades"
    __table_args__ = (Index("ix_cm_paper_trades_status", "status", "decided_at"),)

    run_id: Mapped[int | None] = mapped_column(ForeignKey("cm_runs.id", ondelete="SET NULL"))
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    side: Mapped[str] = mapped_column(String(5), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False)
    decided_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    entry_ref: Mapped[float | None] = mapped_column(Float)
    entry_fill: Mapped[float | None] = mapped_column(Float)
    size_usd: Mapped[float | None] = mapped_column(Float)
    stop: Mapped[float | None] = mapped_column(Float)
    targets: Mapped[list[float] | None] = mapped_column(JSON)
    exit_fill: Mapped[float | None] = mapped_column(Float)
    exit_reason: Mapped[str | None] = mapped_column(String(32))
    fees_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    funding_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    slippage_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    pnl_usd: Mapped[float | None] = mapped_column(Float)
    pnl_r: Mapped[float | None] = mapped_column(Float)
    risk_check: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    model_versions: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    research_cost_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    opened_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    closed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class CmBacktest(IdMixin, Base):
    """A committee backtest (stage 2). status: queued · running · done · failed."""

    __tablename__ = "cm_backtests"

    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(10), nullable=False)
