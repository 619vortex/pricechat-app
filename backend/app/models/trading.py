"""Trading OS execution records: each member's trading account (mode, API wallets, limits), pending trade confirmations,
every order sent (with Hyperliquid's own answer), and the positions the bot manages. Paper, testnet and live are stored
apart by `mode` and never mixed in performance."""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, Boolean, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, false
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin, UTCDateTime

MODES = ("PAPER", "TESTNET", "LIVE")


class TrAccount(IdMixin, TimestampMixin, Base):
    __tablename__ = "tr_accounts"

    member_id: Mapped[int] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), unique=True, nullable=False)
    mode: Mapped[str] = mapped_column(String(8), default="PAPER", server_default="PAPER", nullable=False)
    master_address: Mapped[str | None] = mapped_column(String(42))  # the member's verified wallet (the Hyperliquid account)
    # API wallets ("agents"), one per network, approved by the member's own wallet signature. Keys encrypted at rest.
    testnet_agent: Mapped[str | None] = mapped_column(String(42))
    testnet_agent_key: Mapped[str | None] = mapped_column(Text)
    testnet_approved_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    mainnet_agent: Mapped[str | None] = mapped_column(String(42))
    mainnet_agent_key: Mapped[str | None] = mapped_column(Text)
    mainnet_approved_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    live_checks: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)  # the pre-live checklist results
    live_enabled_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    # limits (owner-configured, within hard system caps)
    max_leverage: Mapped[int] = mapped_column(Integer, default=3, server_default="3", nullable=False)
    max_loss_per_trade_usd: Mapped[float] = mapped_column(Float, default=5.0, server_default="5", nullable=False)
    max_allocation_pct: Mapped[float] = mapped_column(Float, default=10.0, server_default="10", nullable=False)  # margin, % of equity
    max_positions: Mapped[int] = mapped_column(Integer, default=2, server_default="2", nullable=False)
    daily_loss_limit_usd: Mapped[float] = mapped_column(Float, default=15.0, server_default="15", nullable=False)
    allowed_coins: Mapped[str] = mapped_column(Text, default="", server_default="", nullable=False)  # empty = any published signal
    auto_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)
    auto_until: Mapped[datetime | None] = mapped_column(UTCDateTime())
    paused: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)  # STOP NEW TRADES
    paused_reason: Mapped[str | None] = mapped_column(Text)
    paper_equity: Mapped[float] = mapped_column(Float, default=1000.0, server_default="1000", nullable=False)
    # trading style (Earn settings). Defaults keep an existing account exactly as it was: 1h signals, standard frequency.
    timeframe_pref: Mapped[str] = mapped_column(String(8), default="1h", server_default="1h", nullable=False)  # 1h | 15m | both
    frequency: Mapped[str] = mapped_column(String(8), default="standard", server_default="standard", nullable=False)  # standard | active
    max_trades_per_day: Mapped[int] = mapped_column(Integer, default=6, server_default="6", nullable=False)  # used by "active"
    cooldown_minutes: Mapped[int] = mapped_column(Integer, default=30, server_default="30", nullable=False)  # per coin after a close ("active")
    # 15m trading on TESTNET / LIVE money needs this explicit opt-in (paper never does)
    intraday_live_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class TrIntent(IdMixin, TimestampMixin, Base):
    """A trade preview waiting for the member's explicit confirmation (single use, short-lived)."""

    __tablename__ = "tr_intents"
    __table_args__ = (Index("ix_tr_intents_token", "token", unique=True),)

    account_id: Mapped[int] = mapped_column(ForeignKey("tr_accounts.id", ondelete="CASCADE"), nullable=False)
    signal_id: Mapped[int] = mapped_column(ForeignKey("ts_signals.id", ondelete="CASCADE"), nullable=False)
    token: Mapped[str] = mapped_column(String(32), nullable=False)
    mode: Mapped[str] = mapped_column(String(8), nullable=False)
    plan: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(12), default="PENDING", nullable=False)  # PENDING | CONFIRMED | CANCELLED | EXPIRED
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class TrPosition(IdMixin, TimestampMixin, Base):
    """A bot-managed position. One per account per signal (that's the duplicate-trade guard)."""

    __tablename__ = "tr_positions"
    __table_args__ = (UniqueConstraint("account_id", "signal_id", name="uq_tr_positions_account_signal"), Index("ix_tr_positions_status", "status"),
                      Index("ix_tr_positions_signal", "signal_id"))

    account_id: Mapped[int] = mapped_column(ForeignKey("tr_accounts.id", ondelete="CASCADE"), nullable=False)
    signal_id: Mapped[int] = mapped_column(ForeignKey("ts_signals.id", ondelete="CASCADE"), nullable=False)
    mode: Mapped[str] = mapped_column(String(8), nullable=False)
    coin: Mapped[str] = mapped_column(String(32), nullable=False)
    side: Mapped[str] = mapped_column(String(5), nullable=False)
    # OPENING (order sent) · OPEN (fill confirmed + protection in place) · CLOSED · FAILED (no fill) · PROTECTION_FAILED
    status: Mapped[str] = mapped_column(String(18), nullable=False)
    size: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)  # filled, confirmed by the exchange (paper: simulated)
    entry_px: Mapped[float | None] = mapped_column(Float)
    leverage: Mapped[int] = mapped_column(Integer, nullable=False)
    stop: Mapped[float] = mapped_column(Float, nullable=False)
    tp1: Mapped[float] = mapped_column(Float, nullable=False)
    tp2: Mapped[float] = mapped_column(Float, nullable=False)
    max_loss_usd: Mapped[float] = mapped_column(Float, nullable=False)
    via: Mapped[str] = mapped_column(String(8), nullable=False)  # enter | auto
    exit_px: Mapped[float | None] = mapped_column(Float)
    realized_pnl: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)  # after fees; live/testnet from exchange fills
    fees: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    funding: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    tp1_done: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    close_reason: Mapped[str | None] = mapped_column(String(24))
    error: Mapped[str | None] = mapped_column(Text)
    # the member's private "ORDER UP" / "KIWI FINISHED COOKING" notifications, each sent once
    announced_open_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    announced_close_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class TrOrder(IdMixin, TimestampMixin, Base):
    """Every order the bot sends, with a client order id (idempotency) and Hyperliquid's own answer."""

    __tablename__ = "tr_orders"
    __table_args__ = (Index("ix_tr_orders_position", "position_id"),)

    position_id: Mapped[int] = mapped_column(ForeignKey("tr_positions.id", ondelete="CASCADE"), nullable=False)
    cloid: Mapped[str] = mapped_column(String(34), unique=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)  # entry | sl | tp1 | tp2 | close
    is_buy: Mapped[bool] = mapped_column(Boolean, nullable=False)
    size: Mapped[float] = mapped_column(Float, nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)  # limit / trigger price
    reduce_only: Mapped[bool] = mapped_column(Boolean, nullable=False)
    # SENT · RESTING · FILLED · PARTIAL · REJECTED · CANCELLED · ERROR (never FILLED without the exchange saying so)
    status: Mapped[str] = mapped_column(String(10), nullable=False)
    oid: Mapped[int | None] = mapped_column(BigInteger)
    filled_size: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    avg_px: Mapped[float | None] = mapped_column(Float)
    response: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
