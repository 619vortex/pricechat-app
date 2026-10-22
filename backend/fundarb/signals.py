"""Phase 2 signal engine: funding-rate spreads ranked by what they actually earn, how reliably, and at what cost.

For each symbol on 2+ venues and each venue pair, orientation is chosen so the SHORT leg is the venue with the higher
hourly-normalized rate (symmetric: when funding is negative, the long leg is the one being paid). Then:

- the spread's own 7-day history (every venue's settled funding, expanded to hourly so 1 h and 8 h venues line up):
  mean, standard deviation, the share of hours it paid, and the current reading's z-score against that history;
- capital-adjusted APR: there is no cross-margining between venues, so each leg posts its own margin plus a buffer:
      capital per $1 of notional = 2 legs x (1 / leverage) x (1 + buffer)
      capital_apr = gross_apr / capital per $1      (leverage and buffer are settings, never assumed silently);
- costs: taker fees from each venue's fee schedule (None when the venue only tells an authenticated account: the net
  figures are then marked unknown, never zero), and slippage derived from order-book depth for the intended size
  (size = `depth_fraction` of the thinner book's ±1% depth; beyond `max_depth_share` it is rejected);
- break-even hours = round-trip costs / the current hourly spread; rejected beyond `max_breakeven_hours`;
- ranking by a variance-adjusted carry: the capital-adjusted 7-day mean APR minus `stability_k` standard deviations
  (a lower bound on typical carry), so a steady 15% +/- 1.5% ranks above a jumpy 25% +/- 8%;
- flags: funding at a venue's cap (a crowded, squeeze-prone reading), fee-subsidized legs (zero fees today; the edge
  is shown again with `post_subsidy_taker_bps` so the subsidy's end is visible), few samples, extreme z-scores.

Nothing here places an order."""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import Any

from fundarb.models import HOURS_PER_YEAR, FundingRate, OrderBook


@dataclass(frozen=True)
class SignalConfig:
    leverage: float = 2.0  # per leg (no cross-margining)
    buffer: float = 0.25  # idle collateral per venue for mark-price swings, as a share of the margin
    expected_hold_hours: float = 168.0
    depth_fraction: float = 0.05  # size each leg at 5% of the thinner book's ±1% depth
    max_depth_share: float = 0.10  # never more than 10% of that depth
    max_breakeven_hours: float = 72.0
    stability_k: float = 2.0  # rank on mean - k x std (capital-adjusted APR)
    min_samples: int = 72  # hours of overlapping history before the stats count
    persistence_windows: int = 3  # the spread paid in each of the last N hours
    extreme_z: float = 2.0
    post_subsidy_taker_bps: float | None = 4.5  # stress test for zero-fee venues (an assumption, shown as such)

    @property
    def capital_per_notional(self) -> float:
        return 2 * (1 / self.leverage) * (1 + self.buffer)


def hourly_series(history: list[tuple[float, float]], interval_hours: float) -> dict[int, float]:
    """{hour bucket (unix hours): hourly rate}: each settlement's rate spread evenly over the hours of its interval, so an
    8 h venue and a 1 h venue can be subtracted hour by hour."""
    out: dict[int, float] = {}
    span = max(1, int(round(interval_hours)))
    for ts, rate in history:
        end = int(ts // 3600)
        for h in range(end - span + 1, end + 1):
            out[h] = rate / interval_hours
    return out


@dataclass
class Leg:
    venue: str
    rate: FundingRate
    hourly: dict[int, float] = field(default_factory=dict)
    taker_bps: float | None = None
    fee_source: str = ""
    book: OrderBook | None = None
    cap: tuple[float, float] | None = None  # (floor, cap) per interval

    @property
    def now_hourly(self) -> float:
        return self.rate.rate_raw / self.rate.interval_hours

    @property
    def at_cap(self) -> bool:
        if not self.cap:
            return False
        lo, hi = self.cap
        r = self.rate.rate_raw
        return (hi > 0 and r >= hi * 0.9) or (lo < 0 and r <= lo * 0.9)


def _slippage_bps(book: OrderBook | None, size_usd: float) -> float | None:
    """Average slippage for a market order of `size_usd`, walking the book (half the spread included)."""
    if book is None or book.mid is None or size_usd <= 0:
        return None
    worst = 0.0
    for side in (book.asks, book.bids):
        left, cost = size_usd, 0.0
        for lv in side:
            take = min(left, lv.notional)
            cost += take * abs(lv.price - book.mid) / book.mid
            left -= take
            if left <= 0:
                break
        if left > 0:
            return None  # the fetched book can't fill it
        worst = max(worst, cost / size_usd * 1e4)
    return worst


@dataclass
class Opportunity:
    symbol: str
    long: str
    short: str
    gross_apr: float  # current spread, % per year of notional
    capital_apr: float
    mean_apr_7d: float | None
    std_apr_7d: float | None
    z: float | None
    paid_share: float | None  # share of the last 7 days' hours in which this orientation was paid
    persistent: bool
    samples: int
    score: float | None  # capital-adjusted mean - k x std
    size_usd: float | None
    depth_usd: float | None
    costs_bps: float | None  # round trip: entry + exit, both legs, fees + slippage
    breakeven_hours: float | None
    net_apr: float | None  # gross minus annualized round-trip costs over the expected hold
    net_capital_apr: float | None
    post_subsidy_net_apr: float | None
    flags: list[str] = field(default_factory=list)
    rejected: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in self.__dict__.items()}


def evaluate(symbol: str, a: Leg, b: Leg, cfg: SignalConfig = SignalConfig()) -> Opportunity:
    """One venue pair for one symbol, oriented so the short leg has the higher hourly rate."""
    long, short = (a, b) if a.now_hourly <= b.now_hourly else (b, a)
    spread_h = short.now_hourly - long.now_hourly
    gross = spread_h * HOURS_PER_YEAR * 100
    cap_mult = 1 / cfg.capital_per_notional
    hours = sorted(set(long.hourly) & set(short.hourly))
    series = [short.hourly[h] - long.hourly[h] for h in hours]
    flags: list[str] = []
    mean = std = z = paid = score = None
    persistent = False
    if len(series) >= cfg.min_samples:
        mean_h, std_h = statistics.fmean(series), statistics.pstdev(series)
        mean, std = mean_h * HOURS_PER_YEAR * 100, std_h * HOURS_PER_YEAR * 100
        z = (spread_h - mean_h) / std_h if std_h > 0 else None
        paid = sum(1 for s in series if s > 0) / len(series)
        persistent = len(series) >= cfg.persistence_windows and all(s > 0 for s in series[-cfg.persistence_windows:])
        score = (mean - cfg.stability_k * std) * cap_mult
        if z is not None and abs(z) >= cfg.extreme_z:
            flags.append(f"extreme reading ({z:+.1f}σ): expect it to drift back toward {mean:.1f}% APR")
    else:
        flags.append(f"only {len(series)} hours of shared history (needs {cfg.min_samples})")
    for leg in (long, short):
        if leg.at_cap:
            flags.append(f"{leg.venue} funding at its cap: a crowded, squeeze-prone reading")
        if leg.taker_bps == 0:
            flags.append(f"{leg.venue} fees are zero today (a subsidy that can end)")
    # costs: fees from the venues, slippage from depth for the intended size
    depths = [leg.book.depth_within(1.0) for leg in (long, short) if leg.book is not None]
    depth = min(min(bd, ad) for bd, ad, _ in depths) if len(depths) == 2 else None
    size = depth * cfg.depth_fraction if depth else None
    slips = [_slippage_bps(leg.book, size) for leg in (long, short)] if size else [None, None]
    fees = [leg.taker_bps for leg in (long, short)]
    costs = breakeven = net = net_cap = post = None
    rejected = None
    if None in fees:
        missing = [leg.venue for leg in (long, short) if leg.taker_bps is None]
        flags.append(f"fees unknown on {', '.join(missing)} (needs an authenticated account): net figures not computed")
    if None in slips:
        flags.append("order book missing or too thin to price the size: net figures not computed")
    if None not in fees and None not in slips:
        costs = 2 * (sum(fees) + sum(slips))  # entry + exit, symmetric
        net = gross - costs / 1e4 * (HOURS_PER_YEAR / cfg.expected_hold_hours) * 100
        net_cap = net * cap_mult
        breakeven = (costs / 1e4) / spread_h if spread_h > 0 else math.inf
        if cfg.post_subsidy_taker_bps is not None and 0 in fees:
            stressed = 2 * (sum(f if f else cfg.post_subsidy_taker_bps for f in fees) + sum(slips))
            post = gross - stressed / 1e4 * (HOURS_PER_YEAR / cfg.expected_hold_hours) * 100
        if breakeven > cfg.max_breakeven_hours:
            rejected = f"break-even {breakeven:.0f} h > {cfg.max_breakeven_hours:.0f} h"
    if spread_h <= 0:
        rejected = "no spread"
    elif depth is not None and size is not None and size > depth * cfg.max_depth_share:
        rejected = "size above the depth cap"
    return Opportunity(symbol=symbol, long=long.venue, short=short.venue, gross_apr=gross, capital_apr=gross * cap_mult, mean_apr_7d=mean,
                       std_apr_7d=std, z=z, paid_share=paid, persistent=persistent, samples=len(series), score=score, size_usd=size,
                       depth_usd=depth, costs_bps=costs, breakeven_hours=breakeven, net_apr=net, net_capital_apr=net_cap,
                       post_subsidy_net_apr=post, flags=flags, rejected=rejected)


def best_pairs(symbol: str, legs: list[Leg], cfg: SignalConfig = SignalConfig()) -> list[Opportunity]:
    """Every venue pair for the symbol, best first (variance-adjusted score, then capital APR)."""
    out = [evaluate(symbol, a, b, cfg) for i, a in enumerate(legs) for b in legs[i + 1:]]
    return sorted(out, key=rank_key)


def rank_key(o: Opportunity) -> tuple[Any, ...]:
    """Not rejected first, then the variance-adjusted score (persistence matters), then capital APR."""
    return (o.rejected is not None, -(o.score if o.score is not None else -1e9), -o.capital_apr)
