"""Pokémon Paper Exchange (PAPER TRADING ONLY): synthetic contracts tracking the reference price of 10 graded cards.

No real money: paper USDT is a game balance (never deposited, withdrawn, redeemed or settled). Contracts are game
instruments; they convey no ownership of any card. Integers only: prices / money in cents of paper USDT, quantities in
whole contracts. Portable column types (moves to PostgreSQL unchanged).

Double-entry ledger: every balance change is an ExEntry inside an ExJournal whose entries sum to zero per asset (paper
USDT comes from the treasury account and goes back to it; contracts move between accounts). ExBalance is the running
total (available + held) per account and asset, always equal to the ledger sum (reconciliation checks it).
"""

from datetime import date, datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, Boolean, Date, ForeignKey, Index, Integer, String, UniqueConstraint, false
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, UTCDateTime


class ExMarket(IdMixin, Base):
    """One contract market: an exact card + grader + grade, 1/1,000 of its reference value (fixed multiplier)."""

    __tablename__ = "ex_markets"

    symbol: Mapped[str] = mapped_column(String(16), unique=True, nullable=False)
    card_id: Mapped[int] = mapped_column(ForeignKey("pokemon_cards.id", ondelete="RESTRICT"), nullable=False)
    grading_company: Mapped[str] = mapped_column(String(8), nullable=False)
    grade: Mapped[str] = mapped_column(String(8), nullable=False)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    multiplier_den: Mapped[int] = mapped_column(Integer, nullable=False, default=1000)  # 1 contract = card value / 1000
    tick_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    min_qty: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    max_qty: Mapped[int] = mapped_column(Integer, nullable=False, default=1000)
    taker_fee_bps: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    maker_fee_bps: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="active")  # active | paused (admin) ; oracle can halt
    sort: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # the oracle's latest reference (contract cents = card cents / multiplier_den), and how trustworthy it is
    ref_contract_cents: Mapped[int | None] = mapped_column(BigInteger)
    ref_status: Mapped[str] = mapped_column(String(12), nullable=False, default="insufficient")  # ok | stale | insufficient
    ref_confidence: Mapped[str | None] = mapped_column(String(8))  # high | medium | low
    ref_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_price_cents: Mapped[int | None] = mapped_column(BigInteger)  # last paper trade
    seq: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)  # bumped first in every matching transaction (write lock)


class ExAccount(IdMixin, Base):
    """A paper account: one per member, plus the system accounts (treasury, fees, the SIMULATED MARKET MAKER)."""

    __tablename__ = "ex_accounts"

    kind: Mapped[str] = mapped_column(String(10), nullable=False)  # user | maker | treasury | fees
    member_id: Mapped[int | None] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), unique=True)
    name: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class ExBalance(IdMixin, Base):
    __tablename__ = "ex_balances"
    __table_args__ = (UniqueConstraint("account_id", "asset"),)

    account_id: Mapped[int] = mapped_column(ForeignKey("ex_accounts.id", ondelete="CASCADE"), nullable=False)
    asset: Mapped[str] = mapped_column(String(16), nullable=False)  # "USDT" (paper, cents) or a market symbol (contracts)
    available: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    held: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)  # reserved by open orders


class ExJournal(IdMixin, Base):
    __tablename__ = "ex_journals"

    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # fund | trade | reset | seed
    ref: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)  # idempotency
    memo: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class ExEntry(IdMixin, Base):
    __tablename__ = "ex_entries"
    __table_args__ = (Index("ix_ex_entries_account", "account_id", "asset"), Index("ix_ex_entries_journal", "journal_id"))

    journal_id: Mapped[int] = mapped_column(ForeignKey("ex_journals.id", ondelete="CASCADE"), nullable=False)
    account_id: Mapped[int] = mapped_column(ForeignKey("ex_accounts.id", ondelete="CASCADE"), nullable=False)
    asset: Mapped[str] = mapped_column(String(16), nullable=False)
    delta: Mapped[int] = mapped_column(BigInteger, nullable=False)


class ExOrder(IdMixin, Base):
    __tablename__ = "ex_orders"
    __table_args__ = (UniqueConstraint("account_id", "client_id"),
                      Index("ix_ex_orders_book", "market_id", "status", "side", "price_cents", "id"),
                      Index("ix_ex_orders_account", "account_id", "status"))

    market_id: Mapped[int] = mapped_column(ForeignKey("ex_markets.id", ondelete="CASCADE"), nullable=False)
    account_id: Mapped[int] = mapped_column(ForeignKey("ex_accounts.id", ondelete="CASCADE"), nullable=False)
    client_id: Mapped[str] = mapped_column(String(40), nullable=False)  # idempotency key from the client (retries are no-ops)
    side: Mapped[str] = mapped_column(String(4), nullable=False)  # buy | sell
    type: Mapped[str] = mapped_column(String(6), nullable=False)  # limit | market
    price_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)  # limit price, or a market order's protection price
    qty: Mapped[int] = mapped_column(Integer, nullable=False)
    filled: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(10), nullable=False)  # open | filled | cancelled | rejected
    held: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)  # still reserved (USDT cents for buys, contracts for sells)
    reason: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class ExTrade(IdMixin, Base):
    __tablename__ = "ex_trades"
    __table_args__ = (Index("ix_ex_trades_market_at", "market_id", "at"), Index("ix_ex_trades_buyer", "buyer_account_id"),
                      Index("ix_ex_trades_seller", "seller_account_id"))

    market_id: Mapped[int] = mapped_column(ForeignKey("ex_markets.id", ondelete="CASCADE"), nullable=False)
    price_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    qty: Mapped[int] = mapped_column(Integer, nullable=False)
    buy_order_id: Mapped[int] = mapped_column(ForeignKey("ex_orders.id", ondelete="CASCADE"), nullable=False)
    sell_order_id: Mapped[int] = mapped_column(ForeignKey("ex_orders.id", ondelete="CASCADE"), nullable=False)
    buyer_account_id: Mapped[int] = mapped_column(ForeignKey("ex_accounts.id", ondelete="CASCADE"), nullable=False)
    seller_account_id: Mapped[int] = mapped_column(ForeignKey("ex_accounts.id", ondelete="CASCADE"), nullable=False)
    taker_side: Mapped[str] = mapped_column(String(4), nullable=False)
    buyer_fee_cents: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    seller_fee_cents: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    seller_realized_cents: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)  # the seller's realized P&L on this fill
    at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class ExPosition(IdMixin, Base):
    """Cost basis and realized P&L per account + market (average cost, fees included). Quantity lives in ExBalance."""

    __tablename__ = "ex_positions"
    __table_args__ = (UniqueConstraint("account_id", "market_id"),)

    account_id: Mapped[int] = mapped_column(ForeignKey("ex_accounts.id", ondelete="CASCADE"), nullable=False)
    market_id: Mapped[int] = mapped_column(ForeignKey("ex_markets.id", ondelete="CASCADE"), nullable=False)
    cost_cents: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)  # of the contracts still held
    realized_cents: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)


class ExOracleSnapshot(IdMixin, Base):
    """Every reference-price evaluation, with its provenance (which sales were used or rejected, the Scrydex estimate)."""

    __tablename__ = "ex_oracle_snapshots"
    __table_args__ = (Index("ix_ex_oracle_market_at", "market_id", "at"),)

    market_id: Mapped[int] = mapped_column(ForeignKey("ex_markets.id", ondelete="CASCADE"), nullable=False)
    at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    card_cents: Mapped[int | None] = mapped_column(BigInteger)  # the card's reference value (USD cents)
    contract_cents: Mapped[int | None] = mapped_column(BigInteger)
    raw_cents: Mapped[int | None] = mapped_column(BigInteger)  # before damping
    status: Mapped[str] = mapped_column(String(12), nullable=False)
    confidence: Mapped[str | None] = mapped_column(String(8))
    review: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)  # needs a person: a big jump, suspicious sales, sources disagree
    provenance: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class ExEquitySnapshot(IdMixin, Base):
    __tablename__ = "ex_equity_snapshots"
    __table_args__ = (UniqueConstraint("account_id", "day"),)

    account_id: Mapped[int] = mapped_column(ForeignKey("ex_accounts.id", ondelete="CASCADE"), nullable=False)
    day: Mapped[date] = mapped_column(Date, nullable=False)
    equity_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)


class ExAudit(IdMixin, Base):
    __tablename__ = "ex_audit"

    actor: Mapped[str] = mapped_column(String(40), nullable=False)
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    target: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class ExSetting(Base):
    """Small owner-controlled exchange settings and state (e.g. the auto-trader's switch and its price path), as JSON."""

    __tablename__ = "ex_settings"

    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


# --- Perpetuals (PAPER TRADING ONLY): long / short with 1-10x leverage, isolated margin, funding, liquidation ------------


class PerpMarket(IdMixin, Base):
    """A perpetual on one spot market's card. Its INDEX is the spot market's real-card reference (the oracle)."""

    __tablename__ = "perp_markets"

    symbol: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)  # e.g. CHAR1ST-PERP
    ex_market_id: Mapped[int] = mapped_column(ForeignKey("ex_markets.id", ondelete="CASCADE"), unique=True, nullable=False)
    max_leverage: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    mmr_bps: Mapped[int] = mapped_column(Integer, nullable=False, default=500)  # maintenance margin: 5% of notional
    taker_fee_bps: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    maker_fee_bps: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="active")
    last_price_cents: Mapped[int | None] = mapped_column(BigInteger)
    funding_ppm: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # the last hourly rate, parts per million
    last_funding_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    sort: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    seq: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)


class PerpOrder(IdMixin, Base):
    __tablename__ = "perp_orders"
    __table_args__ = (UniqueConstraint("account_id", "client_id"),
                      Index("ix_perp_orders_book", "market_id", "status", "side", "price_cents", "id"),
                      Index("ix_perp_orders_account", "account_id", "status"))

    market_id: Mapped[int] = mapped_column(ForeignKey("perp_markets.id", ondelete="CASCADE"), nullable=False)
    account_id: Mapped[int] = mapped_column(ForeignKey("ex_accounts.id", ondelete="CASCADE"), nullable=False)
    client_id: Mapped[str] = mapped_column(String(40), nullable=False)
    side: Mapped[str] = mapped_column(String(4), nullable=False)  # buy (long) | sell (short)
    type: Mapped[str] = mapped_column(String(6), nullable=False)  # limit | market
    price_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    qty: Mapped[int] = mapped_column(Integer, nullable=False)
    filled: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    leverage: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    reduce_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    status: Mapped[str] = mapped_column(String(10), nullable=False)  # open | filled | cancelled
    held: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)  # paper USDT reserved (margin for opening + fees)
    reason: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class PerpTrade(IdMixin, Base):
    __tablename__ = "perp_trades"
    __table_args__ = (Index("ix_perp_trades_market_at", "market_id", "at"), Index("ix_perp_trades_buyer", "buyer_account_id"),
                      Index("ix_perp_trades_seller", "seller_account_id"))

    market_id: Mapped[int] = mapped_column(ForeignKey("perp_markets.id", ondelete="CASCADE"), nullable=False)
    price_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    qty: Mapped[int] = mapped_column(Integer, nullable=False)
    buyer_account_id: Mapped[int] = mapped_column(ForeignKey("ex_accounts.id", ondelete="CASCADE"), nullable=False)
    seller_account_id: Mapped[int] = mapped_column(ForeignKey("ex_accounts.id", ondelete="CASCADE"), nullable=False)
    taker_side: Mapped[str] = mapped_column(String(4), nullable=False)
    buyer_fee_cents: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    seller_fee_cents: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    buyer_realized_cents: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    seller_realized_cents: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    kind: Mapped[str] = mapped_column(String(12), nullable=False, default="trade")  # trade | liquidation
    at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class PerpPosition(IdMixin, Base):
    """One isolated position per account and perp: signed size (+ long / - short), average entry, its own margin."""

    __tablename__ = "perp_positions"
    __table_args__ = (UniqueConstraint("account_id", "market_id"),)

    account_id: Mapped[int] = mapped_column(ForeignKey("ex_accounts.id", ondelete="CASCADE"), nullable=False)
    market_id: Mapped[int] = mapped_column(ForeignKey("perp_markets.id", ondelete="CASCADE"), nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    entry_cents: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)  # average entry price (per contract)
    margin_cents: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)  # this position's share of the margin escrow
    leverage: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    realized_cents: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    funding_cents: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)  # net funding received (+) or paid (-)
    liquidations: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class PerpFunding(IdMixin, Base):
    __tablename__ = "perp_funding"
    __table_args__ = (Index("ix_perp_funding_market_at", "market_id", "at"),)

    market_id: Mapped[int] = mapped_column(ForeignKey("perp_markets.id", ondelete="CASCADE"), nullable=False)
    at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    rate_ppm: Mapped[int] = mapped_column(Integer, nullable=False)
    index_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    mark_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    paid_cents: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)  # total moved from one side to the other


class PerpLiquidation(IdMixin, Base):
    __tablename__ = "perp_liquidations"
    __table_args__ = (Index("ix_perp_liq_account", "account_id"),)

    account_id: Mapped[int] = mapped_column(ForeignKey("ex_accounts.id", ondelete="CASCADE"), nullable=False)
    market_id: Mapped[int] = mapped_column(ForeignKey("perp_markets.id", ondelete="CASCADE"), nullable=False)
    at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    mark_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    margin_lost_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    insurance_cents: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)  # shortfall the insurance fund covered
