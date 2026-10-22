"""Trading OS: settings, signals (model signals, not trades), members' actions on signals, and the audit log."""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, Boolean, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, false, true
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin, UTCDateTime, utcnow


class TsSettings(IdMixin, TimestampMixin, Base):
    """One row. Admin → Trading OS."""

    __tablename__ = "ts_settings"

    scanner_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true(), nullable=False)
    auto_publish: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)  # off: admin approves each
    paused: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)  # global stop for new signals
    publish_chat: Mapped[str] = mapped_column(String(64), default="@botpricechat", server_default="@botpricechat", nullable=False)
    # the main community chat: signal OPEN / CLOSED notices go here too, at the same time as the signal group ("" = off)
    main_chat: Mapped[str] = mapped_column(String(64), default="@thepricechat", server_default="@thepricechat", nullable=False)
    timeframe: Mapped[str] = mapped_column(String(4), default="1h", server_default="1h", nullable=False)
    universe_size: Mapped[int] = mapped_column(Integer, default=20, server_default="20", nullable=False)
    min_volume_usd: Mapped[float] = mapped_column(Float, default=50_000_000, server_default="50000000", nullable=False)
    max_spread_bps: Mapped[float] = mapped_column(Float, default=8.0, server_default="8", nullable=False)
    max_open_signals: Mapped[int] = mapped_column(Integer, default=3, server_default="3", nullable=False)
    min_rr: Mapped[float] = mapped_column(Float, default=1.6, server_default="1.6", nullable=False)  # after estimated costs
    strategies: Mapped[dict[str, Any]] = mapped_column(JSON, default=lambda: {"trend_pullback_v1": True}, nullable=False)
    allowed_coins: Mapped[str] = mapped_column(Text, default="", server_default="", nullable=False)  # empty = the top markets by volume
    live_allowlist: Mapped[str] = mapped_column(Text, default="", server_default="", nullable=False)  # usernames allowed LIVE mode
    # where chart / weekly replies go when someone asks for them in the main community chat: "anywhere" answers in that chat
    # (as before); "bot_chat" answers there with a short pointer and posts the chart in the signal group (Bot Chat) instead
    chart_routing: Mapped[str] = mapped_column(String(16), default="anywhere", server_default="anywhere", nullable=False)
    # the daily brief in publish_chat (markets, the best /signal setup + chart, the track record): on/off, its UTC hour, and
    # the UTC date it last went out (so a restart never posts it twice)
    brief_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true(), nullable=False)
    brief_hour: Mapped[int] = mapped_column(Integer, default=8, server_default="8", nullable=False)
    brief_sent_on: Mapped[str | None] = mapped_column(String(10))  # YYYY-MM-DD


class TsSignal(IdMixin, TimestampMixin, Base):
    """A model signal. Its status follows the market (a hypothetical trade), never anyone's real execution."""

    __tablename__ = "ts_signals"
    __table_args__ = (Index("ix_ts_signals_status", "status"), Index("ix_ts_signals_coin", "coin"))

    number: Mapped[int] = mapped_column(Integer, unique=True, nullable=False)  # #001, #002...
    coin: Mapped[str] = mapped_column(String(32), nullable=False)
    side: Mapped[str] = mapped_column(String(5), nullable=False)  # LONG | SHORT
    timeframe: Mapped[str] = mapped_column(String(4), nullable=False)
    strategy: Mapped[str] = mapped_column(String(48), nullable=False)
    entry_lo: Mapped[float] = mapped_column(Float, nullable=False)
    entry_hi: Mapped[float] = mapped_column(Float, nullable=False)
    stop: Mapped[float] = mapped_column(Float, nullable=False)
    target: Mapped[float] = mapped_column(Float, nullable=False)
    lev_lo: Mapped[int] = mapped_column(Integer, nullable=False)
    lev_hi: Mapped[int] = mapped_column(Integer, nullable=False)
    rr: Mapped[float] = mapped_column(Float, nullable=False)  # after estimated fees and spread
    # PENDING_REVIEW (not published) · WAITING · ACTIVE · TARGET_HIT · STOP_HIT · CLOSED · EXPIRED · CANCELLED
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str] = mapped_column(Text, default="", nullable=False)  # the short explanation (from the rules)
    analysis: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)  # indicator values at creation
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    approved_by: Mapped[str | None] = mapped_column(String(64))
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    activated_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    closed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    exit_price: Mapped[float | None] = mapped_column(Float)
    model_pnl_pct: Mapped[float | None] = mapped_column(Float)  # the signal's own result, unlevered, after estimated costs
    last_price: Mapped[float | None] = mapped_column(Float)
    last_checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    tg_chat: Mapped[str | None] = mapped_column(String(64))
    tg_message_id: Mapped[int | None] = mapped_column(BigInteger)
    tg_error: Mapped[str | None] = mapped_column(Text)


class TsSignalAction(IdMixin, TimestampMixin, Base):
    """A member's answer to a signal: enter / reject / auto (one per person per signal: duplicates are ignored)."""

    __tablename__ = "ts_signal_actions"
    __table_args__ = (UniqueConstraint("signal_id", "who", name="uq_ts_signal_actions_signal_who"),)

    signal_id: Mapped[int] = mapped_column(ForeignKey("ts_signals.id", ondelete="CASCADE"), nullable=False)
    who: Mapped[str] = mapped_column(String(48), nullable=False)  # "m:<member id>" or "tg:<telegram user id>"
    member_id: Mapped[int | None] = mapped_column(ForeignKey("members.id", ondelete="SET NULL"))
    telegram_user_id: Mapped[int | None] = mapped_column(BigInteger)
    action: Mapped[str] = mapped_column(String(8), nullable=False)  # enter | reject | auto
    verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)  # a linked Kiwi Passport with access


class TsAudit(IdMixin, TimestampMixin, Base):
    __tablename__ = "ts_audit"

    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(48), nullable=False)
    signal_id: Mapped[int | None] = mapped_column(ForeignKey("ts_signals.id", ondelete="SET NULL"))
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class KbEvent(IdMixin, TimestampMixin, Base):
    """Kiwi Brain: the durable decision timeline. One row per real event: a market observation, a strategy evaluation, a
    signal, a risk decision, an order, an exchange response or a position change. member_id NULL = a public, account-free
    event (market / strategy / signal); otherwise it belongs to that member only and is never shown to anyone else."""

    __tablename__ = "kb_events"

    at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, index=True)  # when it happened
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # market|strategy|signal|risk|order|exchange|position|system
    member_id: Mapped[int | None] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"))
    signal_id: Mapped[int | None] = mapped_column(ForeignKey("ts_signals.id", ondelete="SET NULL"))
    coin: Mapped[str | None] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)  # the evidence (numbers, reasons), never secrets
    source: Mapped[str | None] = mapped_column(String(64))  # e.g. "hyperliquid:info" / "risk-engine" / "hyperliquid:exchange"
    source_at: Mapped[datetime | None] = mapped_column(UTCDateTime())  # the data's own timestamp (e.g. the last closed candle)

    __table_args__ = (Index("ix_kb_events_member_id_id", "member_id", "id"), Index("ix_kb_events_kind_at", "kind", "at"),
                      Index("ix_kb_events_source_id", "source", "id"))


class TsKitchen(IdMixin, TimestampMixin, Base):
    """Kiwi Kitchen: the 15m/30m strategy's current state per market (SEARCHING · COOKING · READY · BURNT). Every change is
    also a kb_events row (source "kitchen"), which is what the Earn page streams; this row is only the latest one."""

    __tablename__ = "ts_kitchen"

    coin: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    state: Mapped[str] = mapped_column(String(12), nullable=False)
    candle_t: Mapped[int | None] = mapped_column(BigInteger)  # open time (ms) of the closed 15m candle this state is about
    signal_id: Mapped[int | None] = mapped_column(ForeignKey("ts_signals.id", ondelete="SET NULL"))
    event_id: Mapped[int | None] = mapped_column(Integer)  # the kb_events row that announced this state
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)  # the evaluation (checks, market, proposal)


class KbConfig(IdMixin, TimestampMixin, Base):
    """Teach Your Kiwi: one VERSION of a member's own strategy configuration (filters on top of the published signals, applied
    to their AUTO entries). Never edited: every change is a new version, so any earlier one can be restored."""

    __tablename__ = "kb_configs"

    member_id: Mapped[int] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    note: Mapped[str | None] = mapped_column(Text)  # what changed, or the instruction it was interpreted from

    __table_args__ = (Index("ix_kb_configs_member_version", "member_id", "version", unique=True),)


class TsIdea(IdMixin, TimestampMixin, Base):
    """A community trading idea (/idea BTC long ...): a member's opinion, kept apart from the bot's signals and never traded."""

    __tablename__ = "ts_ideas"
    __table_args__ = (Index("ix_ts_ideas_coin_id", "coin", "id"), Index("ix_ts_ideas_tg_user", "telegram_user_id"))

    coin: Mapped[str] = mapped_column(String(32), nullable=False)
    side: Mapped[str] = mapped_column(String(5), nullable=False)  # LONG | SHORT
    price: Mapped[float] = mapped_column(Float, nullable=False)  # Hyperliquid mark when it was shared (for "since shared")
    target: Mapped[float | None] = mapped_column(Float)
    invalidation: Mapped[float | None] = mapped_column(Float)
    note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    author: Mapped[str | None] = mapped_column(String(64))  # the Telegram display name it was shared under
    member_id: Mapped[int | None] = mapped_column(ForeignKey("members.id", ondelete="SET NULL"))
    chat_id: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(10), default="open", server_default="open", nullable=False)  # open | removed


class TsOiSnapshot(IdMixin, Base):
    """Open interest and funding for the top Hyperliquid markets every 15 minutes (Hyperliquid has no OI history, so Price
    Chat keeps its own, for charts and the weekly summary). Kept 35 days."""

    __tablename__ = "ts_oi_snapshots"
    __table_args__ = (Index("ix_ts_oi_coin_at", "coin", "at"),)

    at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    coin: Mapped[str] = mapped_column(String(32), nullable=False)
    mark: Mapped[float] = mapped_column(Float, nullable=False)
    oi_usd: Mapped[float] = mapped_column(Float, nullable=False)
    funding: Mapped[float] = mapped_column(Float, nullable=False)  # per hour


class TsDecision(IdMixin, TimestampMixin, Base):
    """One structured trade decision: a kitchen candidate (a 15m entry model fired on a closed candle) with its full plan,
    every check, and what was decided (READY or REJECTED, with the failing check). One row per strategy, market and candle.
    Rejected and shadow candidates get a SHADOW outcome (what the plan would have done on the candles that followed, with
    fees, slippage and funding): hypothetical, kept apart from real trading results."""

    __tablename__ = "ts_decisions"
    __table_args__ = (UniqueConstraint("strategy", "coin", "candle_t", name="uq_ts_decisions_candidate"),
                      Index("ix_ts_decisions_outcome", "outcome"), Index("ix_ts_decisions_created", "created_at"))

    strategy: Mapped[str] = mapped_column(String(48), nullable=False)
    version: Mapped[str] = mapped_column(String(24), nullable=False)  # the strategy mode/config it ran with, e.g. "degen"
    coin: Mapped[str] = mapped_column(String(32), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(4), nullable=False)
    candle_t: Mapped[int] = mapped_column(BigInteger, nullable=False)  # open time (ms) of the closed setup candle
    model: Mapped[str] = mapped_column(String(24), nullable=False)
    direction: Mapped[str] = mapped_column(String(5), nullable=False)  # LONG | SHORT
    regime: Mapped[str | None] = mapped_column(String(20))
    regime_confidence: Mapped[float | None] = mapped_column(Float)  # 0-1, share of the regime's own conditions that hold
    # WAITING_CONFIRMATION (a check is pending) · READY (every check passed) · REJECTED (a check failed)
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    decision: Mapped[str] = mapped_column(String(8), nullable=False)  # TAKE | REJECT | WAIT
    rejected_by: Mapped[str | None] = mapped_column(String(24))  # the first failing check's id
    reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    shadow: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)  # True: nothing was proposed (strategy off or rejected)
    signal_id: Mapped[int | None] = mapped_column(ForeignKey("ts_signals.id", ondelete="SET NULL"))
    entry_reasons: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    ref: Mapped[float] = mapped_column(Float, nullable=False)  # the setup candle's close (entry is the next open)
    stop: Mapped[float] = mapped_column(Float, nullable=False)
    tp1: Mapped[float] = mapped_column(Float, nullable=False)
    tp2: Mapped[float] = mapped_column(Float, nullable=False)
    r1: Mapped[float] = mapped_column(Float, nullable=False)
    r2: Mapped[float] = mapped_column(Float, nullable=False)
    max_hold_min: Mapped[int] = mapped_column(Integer, nullable=False)
    est_cost_r: Mapped[float | None] = mapped_column(Float)  # fees + slippage + spread + funding, in R (before the trade)
    est_fees_r: Mapped[float | None] = mapped_column(Float)
    est_slippage_r: Mapped[float | None] = mapped_column(Float)
    rr_net: Mapped[float | None] = mapped_column(Float)  # TP1 in R after the estimated costs
    score: Mapped[int | None] = mapped_column(Integer)  # intraday.quality 0-100 (NOT a probability)
    ml_p: Mapped[float | None] = mapped_column(Float)
    checks: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)  # [{id, label, status, detail}]
    market: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)  # mark, spread, funding, OI at the decision
    # the outcome: PENDING (not resolved yet) · DONE · NO_FILL (opened beyond the stop) · NO_DATA (candles unavailable)
    outcome: Mapped[str] = mapped_column(String(8), default="PENDING", server_default="PENDING", nullable=False)
    r_net: Mapped[float | None] = mapped_column(Float)
    r_gross: Mapped[float | None] = mapped_column(Float)
    cost_r: Mapped[float | None] = mapped_column(Float)
    mae_r: Mapped[float | None] = mapped_column(Float)  # maximum adverse excursion while it was open, in R (<= 0)
    mfe_r: Mapped[float | None] = mapped_column(Float)  # maximum favorable excursion, in R (>= 0)
    exit_kind: Mapped[str | None] = mapped_column(String(16))
    hold_min: Mapped[float | None] = mapped_column(Float)
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class TsBookSnapshot(IdMixin, Base):
    """Resting liquidity around the price for a few markets every 2 minutes, binned per exchange, for liquidity heatmaps.
    Each venue's bins only cover the range its public book shows (`cover`); nothing is filled in beyond it. Kept 2 days."""

    __tablename__ = "ts_book_snapshots"
    __table_args__ = (Index("ix_ts_book_coin_at", "coin", "at"),)

    at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    coin: Mapped[str] = mapped_column(String(32), nullable=False)
    mid: Mapped[float] = mapped_column(Float, nullable=False)
    step: Mapped[float] = mapped_column(Float, nullable=False)  # bin width (price)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)  # {venue: {"bins": [[px, usd]...], "cover": [lo, hi]}}


class TsLiquidation(IdMixin, Base):
    """A liquidation an exchange reported (OKX publishes filled liquidation orders over its public API). Observed data:
    one row per reported fill, de-duplicated. Kept 35 days."""

    __tablename__ = "ts_liquidations"
    __table_args__ = (UniqueConstraint("venue", "coin", "t", "side", "px", "qty", name="uq_ts_liq_fill"),
                      Index("ix_ts_liq_coin_t", "coin", "t"))

    venue: Mapped[str] = mapped_column(String(24), nullable=False)
    coin: Mapped[str] = mapped_column(String(32), nullable=False)
    t: Mapped[int] = mapped_column(BigInteger, nullable=False)  # ms
    side: Mapped[str] = mapped_column(String(8), nullable=False)  # the liquidated position: long | short
    px: Mapped[float] = mapped_column(Float, nullable=False)
    qty: Mapped[float] = mapped_column(Float, nullable=False)  # coins
    usd: Mapped[float] = mapped_column(Float, nullable=False)


class TsPriceAlert(IdMixin, TimestampMixin, Base):
    """A Telegram user's price alert on a Hyperliquid perp: fires once by private message when the price crosses `price`
    (above / below) or enters the zone price..price_hi (a /signal entry zone, with its plan). Expires after 30 days."""

    __tablename__ = "ts_price_alerts"
    __table_args__ = (Index("ix_ts_price_alerts_status", "status"), Index("ix_ts_price_alerts_user", "telegram_user_id"))

    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    coin: Mapped[str] = mapped_column(String(32), nullable=False)
    kind: Mapped[str] = mapped_column(String(5), nullable=False)  # above | below | zone
    price: Mapped[float] = mapped_column(Float, nullable=False)  # the level (zone: its low)
    price_hi: Mapped[float | None] = mapped_column(Float)  # zone: its high
    last_price: Mapped[float] = mapped_column(Float, nullable=False)  # the mark at the last check: crossings are measured from it
    plan: Mapped[dict[str, Any] | None] = mapped_column(JSON)  # zone alerts: {side, tf, stop, target}
    status: Mapped[str] = mapped_column(String(10), default="active", server_default="active", nullable=False)  # active | fired | off | expired | blocked
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    fired_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    fired_price: Mapped[float | None] = mapped_column(Float)


class TsSignalIdea(IdMixin, TimestampMixin, Base):
    """A plan /signal showed (a full setup or a WATCH idea), graded later on real candles: the track record. No user ids,
    only where it was asked (group | private | brief). Analysis on request: never published as a signal, never traded."""

    __tablename__ = "ts_signal_ideas"
    __table_args__ = (Index("ix_ts_signal_ideas_status", "status"),
                      Index("ix_ts_signal_ideas_dedupe", "coin", "timeframe", "side", "created_at"))

    venue: Mapped[str] = mapped_column(String(16), nullable=False)  # hyperliquid | binance | bybit | okx | dex
    kind: Mapped[str] = mapped_column(String(8), nullable=False)  # perp | spot
    coin: Mapped[str] = mapped_column(String(64), nullable=False)  # the venue's own symbol (to read its candles)
    tag: Mapped[str] = mapped_column(String(64), nullable=False)  # the listing tag shown, e.g. $BTCUSD
    timeframe: Mapped[str] = mapped_column(String(4), nullable=False)
    side: Mapped[str] = mapped_column(String(5), nullable=False)  # LONG | SHORT
    entry_lo: Mapped[float] = mapped_column(Float, nullable=False)
    entry_hi: Mapped[float] = mapped_column(Float, nullable=False)
    stop: Mapped[float] = mapped_column(Float, nullable=False)
    target: Mapped[float] = mapped_column(Float, nullable=False)
    idea: Mapped[str] = mapped_column(Text, default="", nullable=False)
    family: Mapped[str] = mapped_column(String(12), nullable=False)  # pullback | breakout | range | momentum | full | other
    full: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)  # passed every rule (else a WATCH idea)
    score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    chat_type: Mapped[str] = mapped_column(String(8), nullable=False)  # group | private | brief
    # OPEN (waiting for the entry) · FILLED · TARGET · STOP · EXPIRED · SKIPPED (no candles to grade it on, e.g. DEX)
    status: Mapped[str] = mapped_column(String(8), default="OPEN", server_default="OPEN", nullable=False)
    filled_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    r: Mapped[float | None] = mapped_column(Float)  # result in R from the zone's mid: TARGET +reward, STOP -1, EXPIRED (filled) at the close


class TsAutoDelete(IdMixin, Base):
    """A bot message in the main chat, deleted at delete_at (tradingos.autodelete)."""

    __tablename__ = "ts_autodelete"
    __table_args__ = (Index("ix_ts_autodelete_due", "delete_at"),)

    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    delete_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class TsAssistConvo(IdMixin, Base):
    """One /help <trade> check (tradingos/assist.py): the parsed trade and its FACTS. Replies to its answers are follow-ups
    for 30 minutes, from the asker only."""

    __tablename__ = "ts_assist_convos"

    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    tg_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    coin: Mapped[str] = mapped_column(String(32), nullable=False)
    side: Mapped[str] = mapped_column(String(5), nullable=False)  # long | short
    trade: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)  # what the parser read (re-priced for follow-ups)
    facts: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    followups: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)


class TsAssistJob(IdMixin, Base):
    """One turn of a check: the bot's message (placeholder → Vortex's answer, or the fallback) and the job Vortex claims.
    status: pending · claimed · answered · expired · failed · fallback (Vortex wasn't around: answered by the bot at once)."""

    __tablename__ = "ts_assist_jobs"
    __table_args__ = (Index("ix_ts_assist_jobs_status", "status"), Index("ix_ts_assist_jobs_msg", "chat_id", "message_id"),
                      Index("ix_ts_assist_jobs_user", "tg_user_id", "created_at"))

    public_id: Mapped[str] = mapped_column(String(24), unique=True, nullable=False)  # "j_…": what Vortex sees
    convo_id: Mapped[int] = mapped_column(ForeignKey("ts_assist_convos.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)  # assess | followup
    status: Mapped[str] = mapped_column(String(10), nullable=False)
    tg_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_id: Mapped[int | None] = mapped_column(BigInteger)  # the bot's message
    reply_to: Mapped[int | None] = mapped_column(BigInteger)  # the asker's message
    question: Mapped[str] = mapped_column(Text, nullable=False)
    facts: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    fallback: Mapped[str] = mapped_column(Text, nullable=False)  # the bot's own assessment (Telegram HTML)
    answer: Mapped[str | None] = mapped_column(Text)  # what the asker got, as plain text (follow-up history)
    claims: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    lease_until: Mapped[datetime | None] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)


class TsAssistState(IdMixin, Base):
    """One row: when Vortex last asked for work (its long-poll), so the bot knows whether to wait for it."""

    __tablename__ = "ts_assist_state"

    last_seen_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
