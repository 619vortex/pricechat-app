"""The normalized data model every venue adapter produces (Module 2)."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

HOURS_PER_YEAR = 8760


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class Level:
    price: float
    size: float  # base units

    @property
    def notional(self) -> float:
        return self.price * self.size


@dataclass(frozen=True)
class OrderBook:
    venue: str
    symbol: str
    bids: tuple[Level, ...]  # best (highest) first
    asks: tuple[Level, ...]  # best (lowest) first
    timestamp_utc: datetime

    @staticmethod
    def build(venue: str, symbol: str, bids: list[tuple[float, float]], asks: list[tuple[float, float]], ts: datetime | None = None,
              depth: int | None = None) -> "OrderBook":
        """Sorts each side best-first and merges equal prices (some venues return one row per order, or the worst bid first)."""
        def side(rows: list[tuple[float, float]], reverse: bool) -> tuple[Level, ...]:
            merged: dict[float, float] = {}
            for p, s in rows:
                if p > 0 and s > 0:
                    merged[p] = merged.get(p, 0.0) + s
            out = [Level(p, s) for p, s in sorted(merged.items(), reverse=reverse)]
            return tuple(out[:depth] if depth else out)
        return OrderBook(venue, symbol, side(bids, True), side(asks, False), ts or utcnow())

    @property
    def bid(self) -> float | None:
        return self.bids[0].price if self.bids else None

    @property
    def ask(self) -> float | None:
        return self.asks[0].price if self.asks else None

    @property
    def mid(self) -> float | None:
        return (self.bids[0].price + self.asks[0].price) / 2 if self.bids and self.asks else None

    def depth_within(self, pct: float = 1.0) -> tuple[float, float, bool]:
        """(bid-side USD, ask-side USD, complete) within `pct`% of mid. `complete` is False when the fetched levels end before
        the band does, i.e. the venue may have more depth than counted (never more than it reports)."""
        mid = self.mid
        if mid is None:
            return 0.0, 0.0, False
        lo, hi = mid * (1 - pct / 100), mid * (1 + pct / 100)
        bid_usd = sum(lv.notional for lv in self.bids if lv.price >= lo)
        ask_usd = sum(lv.notional for lv in self.asks if lv.price <= hi)
        complete = bool(self.bids and self.asks and self.bids[-1].price < lo and self.asks[-1].price > hi)
        return bid_usd, ask_usd, complete


@dataclass(frozen=True)
class FundingRate:
    rate_raw: float  # per funding interval, signed: positive = longs pay shorts
    interval_hours: float
    interval_source: str  # where the interval came from (an API field, or the spacing of the venue's own funding history)
    kind: Literal["predicted", "last_settled"]  # the rate for the running interval, or the last one paid
    as_of: datetime


@dataclass(frozen=True)
class FeeSchedule:
    maker_bps: float
    taker_bps: float
    source: str
    per_symbol: dict[str, tuple[float, float]] = field(default_factory=dict)  # symbol -> (maker_bps, taker_bps) where the venue differs

    def for_symbol(self, symbol: str) -> tuple[float, float]:
        return self.per_symbol.get(symbol, (self.maker_bps, self.taker_bps))


@dataclass(frozen=True)
class Position:
    symbol: str
    size: float  # signed base units: positive long, negative short
    entry_price: float | None
    unrealized_pnl: float | None


@dataclass(frozen=True)
class Balance:
    equity_usd: float
    available_usd: float | None


@dataclass(frozen=True)
class OpenOrder:
    symbol: str
    order_id: str
    side: Literal["buy", "sell"]
    price: float
    size: float


@dataclass(frozen=True)
class VenueQuote:
    """One venue's view of one perp, normalized. funding_rate_hourly / funding_apr are derived, never fetched."""
    venue: str
    symbol: str  # canonical, e.g. "BTC-PERP"
    funding_rate_raw: float
    funding_interval_hours: float
    funding_rate_hourly: float
    funding_apr: float
    mark_price: float
    index_price: float
    bid: float
    ask: float
    depth_usd_1pct: float  # min(bid side, ask side) notional within 1% of mid
    maker_bps: float | None  # None only when the venue needs credentials to tell us (see fee_source); never a silent 0
    taker_bps: float | None
    timestamp_utc: datetime
    # provenance (additions to the brief's schema, so every number can be audited)
    funding_interval_source: str = ""
    funding_kind: str = "predicted"
    fee_source: str = ""
    depth_complete: bool = True

    @staticmethod
    def build(venue: str, symbol: str, funding: FundingRate, mark: float, index: float, book: OrderBook,
              fees: tuple[float, float] | None, fee_source: str, ts: datetime | None = None) -> "VenueQuote":
        if funding.interval_hours <= 0:
            raise ValueError(f"{venue} {symbol}: funding interval must be positive, got {funding.interval_hours}")
        if book.bid is None or book.ask is None:
            raise ValueError(f"{venue} {symbol}: empty order book")
        hourly = funding.rate_raw / funding.interval_hours
        bid_usd, ask_usd, complete = book.depth_within(1.0)
        return VenueQuote(venue=venue, symbol=symbol, funding_rate_raw=funding.rate_raw, funding_interval_hours=funding.interval_hours,
                          funding_rate_hourly=hourly, funding_apr=hourly * HOURS_PER_YEAR, mark_price=mark, index_price=index,
                          bid=book.bid, ask=book.ask, depth_usd_1pct=min(bid_usd, ask_usd),
                          maker_bps=fees[0] if fees else None, taker_bps=fees[1] if fees else None, timestamp_utc=ts or utcnow(),
                          funding_interval_source=funding.interval_source, funding_kind=funding.kind, fee_source=fee_source,
                          depth_complete=complete)
