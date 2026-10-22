"""Earn › Arbitrage. Lunch Bot (app/arbitrage/lunch.py) is PAPER ONLY: it records what it would have bought and sold on
pump.fun launches at the real on-chain prices, and never holds a key or sends a transaction."""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Float, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin, UTCDateTime


class ArbLunchTrade(IdMixin, TimestampMixin, Base):
    """One paper position on a pump.fun launch, by one strategy ("starter": early momentum; "graduation": a nearly full
    curve, held to graduation), closed at graduation, target, stop or timeout. Each strategy takes a launch at most once."""
    __tablename__ = "arb_lunch_trades"
    __table_args__ = (UniqueConstraint("mint", "strategy", name="uq_arb_lunch_trades_mint_strategy"), Index("ix_arb_lunch_trades_status", "status"))

    strategy: Mapped[str] = mapped_column(String(16), default="starter", server_default="starter", nullable=False)
    mint: Mapped[str] = mapped_column(String(64), nullable=False)
    bonding_curve: Mapped[str] = mapped_column(String(64), nullable=False)
    symbol: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    image_uri: Mapped[str | None] = mapped_column(String(500))
    launched_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    status: Mapped[str] = mapped_column(String(12), default="open", nullable=False)  # open | closed
    opened_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    entry_sol: Mapped[float] = mapped_column(Float, nullable=False)  # SOL spent including fees
    tokens: Mapped[float] = mapped_column(Float, nullable=False)  # whole tokens received
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)  # SOL per token, the curve's price before the buy
    entry_mc_sol: Mapped[float] = mapped_column(Float, nullable=False)
    last_price: Mapped[float | None] = mapped_column(Float)  # latest curve / pool price (SOL per token)
    last_value_sol: Mapped[float | None] = mapped_column(Float)  # what selling everything now would return, after fees
    peak_value_sol: Mapped[float | None] = mapped_column(Float)
    last_checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    closed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    exit_sol: Mapped[float | None] = mapped_column(Float)  # SOL back after fees
    exit_reason: Mapped[str | None] = mapped_column(String(16))  # graduated | target | stop | timeout | gone
    pnl_sol: Mapped[float | None] = mapped_column(Float)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)  # entry signal, pool, costs


class ArbLunchGraduation(IdMixin, TimestampMixin, Base):
    """A launch that completed its bonding curve: the curve's last price vs the first price its PumpSwap pool showed."""
    __tablename__ = "arb_lunch_graduations"
    __table_args__ = (UniqueConstraint("mint", name="uq_arb_lunch_graduations_mint"),)

    mint: Mapped[str] = mapped_column(String(64), nullable=False)
    symbol: Mapped[str] = mapped_column(String(40), nullable=False)
    curve_price: Mapped[float] = mapped_column(Float, nullable=False)  # SOL per token at completion
    pool_price: Mapped[float | None] = mapped_column(Float)  # SOL per token, first pool price GeckoTerminal reported
    gap_pct: Mapped[float | None] = mapped_column(Float)
    pool_address: Mapped[str | None] = mapped_column(String(64))
    pool_dex: Mapped[str | None] = mapped_column(String(40))
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    priced_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class ArbLunchDay(IdMixin, TimestampMixin, Base):
    """Daily counters: launches the scanner saw, how many passed the entry rule."""
    __tablename__ = "arb_lunch_days"
    __table_args__ = (UniqueConstraint("day", name="uq_arb_lunch_days_day"),)

    day: Mapped[str] = mapped_column(String(10), nullable=False)
    launches_seen: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    entries: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
