"""Kiwi AI Investment Committee backtest (owner's Phase 9). Research only: nothing here places, changes or cancels an order.

What is compared, on the same coins, timeframe and period of real Hyperliquid history:
  1. committee_rules  the deterministic committee (app.committee.orchestrator.run_committee, rules backend) at every point of the
                      decision grid. LONG / SHORT -> the plan copied from the evidence scenario -> the Kiwi risk gate
                      (app.committee.risk_gate.evaluate, the same code as live, read-only) -> simulated only when APPROVED.
  2. committee_llm    (mode="llm" only) the same committee with the configured model backend on a small, evenly spaced sample
                      of the grid, under a budget cap, with ANONYMISED evidence (see "LLM contamination control").
  3. kiwi_*           the live published Kiwi strategies, replayed by the existing lab (app.tradingos.lab.candidates with the
                      LIVE variants: breakout_4h_v1 on 4h and trend_pullback_v1 on 1h, the live trade management: stop for the
                      whole size, TP1 half at 2R, TP2 rest at 3R). Their own cadence (every closed candle), same window.
  4. ema_cross        simple technical baseline: EMA20 crossing EMA50 on the run timeframe, checked at the same decision
                      grid (a cross since the previous grid point), stop 2 x ATR14, single exit at 2R, same costs.
  5. buy_hold         per coin and an equal-weight basket, long from the first decision to the end (price return net of one
                      round trip of fees + slippage; perp version also net of the real hourly funding a long paid).

Honest-by-construction (the same rules as app.tradingos.backtest / lab, whose functions are reused, not copied):
  - decisions on CLOSED candles only; the evidence is built by app.committee.evidence.build_evidence in mode="backtest" with
    as_of = the decision time, served by a TimeMachine (an httpx transport over the stored history) that refuses every candle
    not closed by as_of, every funding row and every Fear & Greed value after as_of. News, CoinGecko, exchange listings, the
    trade tape and community counts have no point-in-time history and are "unavailable" (evidence does this); nothing is
    backfilled. Price Chat's own OI snapshots are not used (an isolated database), so every decision sees the same sources.
  - entry = the NEXT candle's open plus slippage (backtest.simulate); skipped if that opens beyond the stop or beyond TP1;
    a candle touching stop and target counts as the stop first; a gap through the stop fills at the open.
  - committee trade management = the live execution.protect model: stop for the whole size, TP1 = the plan's first target for
    half, TP2 = its second target for the rest (one target: all at TP1), no break-even move; plus a 14-day time stop (live has
    none; a finite replay needs one; time exits are reported).
  - costs: taker fee 0.045% per fill, slippage per side 0.02% BTC/ETH, 0.05% others, real hourly Hyperliquid funding.
  - portfolio (every strategy alike): backtest.portfolio - at most 2 open, one per coin, 0.5% of equity risked per trade,
    no new trades on a day after -2%.
  - the risk gate runs against an isolated in-memory database: the reference paper account with an empty book (the live paper
    book and live kitchen signals are "now", not "then"), the owner's current risk settings copied in, the global pause ignored.

Evaluation (all pre-declared, nothing tuned; the committee has no tuned parameters):
  - chronological split: SELECTION = the first 60% of the period, HOLDOUT = the last 40%; walk-forward: 4 consecutive folds.
  - variants, declared before running and reported for information only (nothing is selected from them):
      committee_rules_ungated = every committee LONG / SHORT simulated even when the risk gate rejected it (the gate's effect).
  - regime (per decision, from BTC 1d candles closed by the decision time): EMA50 slope = EMA50 now / EMA50 5 days earlier - 1,
    ATR% = ATR14 / close, its percentile rank among the previous 365 days. high-vol if rank >= 0.80; otherwise trend-up if the
    slope >= +1%, trend-down if <= -1%, else range; "unknown" with fewer than 60 daily candles.
  - metrics: trades, win rate, avg R, net P&L in R and in % of equity (0.5% risk per trade, additive), Sharpe = mean / sample
    stdev (ddof 1) of DAILY P&L in % of equity (a trade's P&L on its exit day, every calendar day of the window counted, zero
    days included) x sqrt(365); max drawdown of the additive equity curve (% of equity, peak to trough); profit factor = sum of
    winning R / |sum of losing R|; fees / slippage / funding in % of equity (fees and slippage estimated from the entry
    notional for both legs, funding = the remainder of the simulator's exact total cost); turnover = traded notional
    (entry + exit) / equity per year; inference cost; 95% bootstrap CI of the mean R (2000 resamples, seed 7, percentile).
  - verdict per strategy, only one of:
      "positive in sample and out of sample with CI excluding zero" - selection mean R > 0, holdout mean R > 0 on >= 30 holdout
        trades, and the holdout 95% CI of the mean R above zero;
      "edge not distinguishable from zero" - otherwise, when the mean R over the whole period is > 0;
      "no evidence of edge" - otherwise (mean R <= 0, or no trades).
    A favourable backtest is never a claim of profitability: it is a hypothesis for paper trading.

LLM contamination control (mode="llm"): the model's training data knows what BTC did on real dates, so the evidence it sees is
ANONYMISED: the asset is "ASSET", every price is rebased so that the first candle shown closes at 100 (all frames with the same
factor; the technical section is recomputed from the rebased candles so its texts carry rebased numbers), and every timestamp
becomes relative ("t-0" = the decision, "t-6" = six run-timeframe candles earlier). A leak check refuses a pack that still
contains the symbol, its name, a date or a real price level (differential: a number matching a real level must also survive a
second anonymisation at another base, so a coin trading near 100 isn't refused for a coincidence). This does NOT fully remove memorisation (shapes, volatility,
funding and Fear & Greed values can still be recognised), so LLM results on periods before the model's knowledge cutoff stay
suspect. The trade is always planned and gated on the REAL evidence (the plan is a deterministic copy of its scenario).

Run: python -m app.committee.backtest --help
"""

from __future__ import annotations

import argparse
import asyncio
import bisect
import json
import math
import random
import re
import statistics
import sys
import threading
import time
from collections import Counter, defaultdict
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import httpx

from app.tradingos import backtest as bt
from app.tradingos import history
from app.tradingos import intraday as ix
from app.tradingos.market import INTERVALS, Candle

HL_HOST = "api.hyperliquid.xyz"
FNG_HOST = "api.alternative.me"
FNG_URL = "https://api.alternative.me/fng/"
DAY = 86_400_000
DEFAULT_COINS = ("BTC", "ETH", "SOL", "HYPE")
TIMEFRAMES = ("1h", "4h", "1d")
DATA_TFS = ("1h", "4h", "1d")
WARMUP = 200  # run-timeframe candles before the first decision (EMA200 of the run frame)
MAX_HOLD_MIN = 14 * 24 * 60
SELECTION_SHARE = 0.6
FOLDS = 4
MIN_HOLDOUT_TRADES = 30
LLM_MAX_DECISIONS = 60
BOOT, BOOT_SEED = 2000, 7
REGIMES = ("trend-up", "trend-down", "range", "high-vol", "unknown")
REGIME_SLOPE_DAYS, REGIME_SLOPE_BAND, REGIME_RANK_DAYS, REGIME_HIGH_VOL = 5, 0.01, 365, 0.80
KIWI_VARIANTS = {"kiwi_breakout_4h_v1": "breakout LIVE", "kiwi_trend_pullback_v1_1h": "pullback 1h LIVE"}
VERDICTS = ("no evidence of edge", "edge not distinguishable from zero", "positive in sample and out of sample with CI excluding zero")
CONTAMINATION_NOTE = ("LLM evidence was anonymised (ASSET, prices rebased to 100 at the first candle shown, relative times). This does "
                      "not fully remove memorisation: price shapes, volatility, funding and Fear & Greed values can still be "
                      "recognised, so LLM results on periods before the model's knowledge cutoff remain suspect.")
LIMITATIONS = [
    "News, fundamentals (CoinGecko, exchange listings, volume), the trade tape and community counts have no point-in-time "
    "history: those committee sections are 'unavailable' in every backtest decision (the live committee sees more). Not backfilled.",
    "Hyperliquid serves at most 5000 candles per interval: the 1h alignment frame exists only for roughly the last 208 days, so "
    "older decisions are made without it (counted in decisions.with_1h_frame).",
    "Coins are chosen today (survivorship); HYPE trades only since late 2024.",
    "Hyperliquid's lot size (szDecimals) is today's, injected into the evidence for the risk gate (backtest evidence has no "
    "Hyperliquid meta); max leverage is not injected, so the gate uses the reference account's 3x cap.",
    "The risk gate runs on an empty reference paper book (each decision alone); portfolio limits are applied afterwards by "
    "backtest.portfolio. Current owner risk settings are applied to the whole history; the global pause is ignored.",
    "Fees and slippage per strategy are estimated from the entry notional for both legs; funding is the remainder of the "
    "simulator's exact cost, so it absorbs that small estimation error.",
    "P&L is additive at 0.5% risk per trade; the live gate's notional cap (allocation x leverage) is not applied to the R results.",
    "Buy-and-hold carries 100% notional exposure; the strategies carry 0.5% risk per trade: compare risk-adjusted numbers, "
    "not raw returns.",
    "A backtest is not evidence of future profitability.",
]


# ---------------------------------------------------------------- config
@dataclass
class Config:
    coins: list[str] = field(default_factory=lambda: list(DEFAULT_COINS))
    timeframe: str = "4h"
    every: int = 6  # decide every N-th closed candle of the timeframe (aligned to multiples of N x span since the epoch)
    mode: str = "rules"  # rules | llm
    llm_max_decisions: int = 20
    llm_max_usd: float = 0.0  # 0 = a paid backend is refused (local / Vortex models cost nothing)
    start: str | None = None  # ISO date; default: as early as the history allows (after the warm-up)
    end: str | None = None
    depth: str = "quick"

    @classmethod
    def parse(cls, d: dict[str, Any]) -> Config:
        c = cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})
        c.coins = [str(x).strip().upper().removeprefix("$") for x in c.coins if str(x).strip()][:8]
        if not c.coins or any(not re.fullmatch(r"[A-Z0-9]{2,12}", x) for x in c.coins):
            raise ValueError("coins: 1-8 tickers like BTC")
        if c.timeframe not in TIMEFRAMES:
            raise ValueError(f"timeframe must be one of {', '.join(TIMEFRAMES)}")
        if not 1 <= int(c.every) <= 48:
            raise ValueError("every must be 1-48 candles")
        if c.mode not in ("rules", "llm"):
            raise ValueError("mode must be rules or llm")
        if not 1 <= int(c.llm_max_decisions) <= LLM_MAX_DECISIONS:
            raise ValueError(f"llm_max_decisions must be 1-{LLM_MAX_DECISIONS}")
        if not 0 <= float(c.llm_max_usd) <= 5:
            raise ValueError("llm_max_usd must be 0-5")
        for k in ("start", "end"):
            if getattr(c, k):
                _ms(getattr(c, k))
        c.every, c.llm_max_decisions, c.llm_max_usd = int(c.every), int(c.llm_max_decisions), float(c.llm_max_usd)
        c.depth = "quick" if c.depth != "full" else "full"
        return c


def _ms(s: str) -> int:
    d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return int((d if d.tzinfo else d.replace(tzinfo=timezone.utc)).timestamp() * 1000)


def iso(ms: int | float | None) -> str | None:
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------- data: stored Hyperliquid history (history.py format)
@dataclass
class Dataset:
    """Candles per (coin, interval), hourly funding per coin, daily Fear & Greed; saved in history.py's file format."""

    dir: Path
    candles: dict[tuple[str, str], list[Candle]] = field(default_factory=dict)
    funding: dict[str, list[tuple[int, float]]] = field(default_factory=dict)
    fng: list[tuple[int, int, str]] = field(default_factory=list)  # (unix seconds, value, label)

    def save(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        for (coin, tf), cs in self.candles.items():
            (self.dir / f"{coin}_{tf}.json").write_text(json.dumps([[c.t, c.o, c.h, c.l, c.c, c.v] for c in cs]))
        for coin, rows in self.funding.items():
            (self.dir / f"{coin}_funding.json").write_text(json.dumps([[t, r] for t, r in rows]))
        (self.dir / "fear_greed.json").write_text(json.dumps([list(r) for r in self.fng]))

    @classmethod
    def load(cls, d: Path, coins: list[str]) -> Dataset:
        ds = cls(Path(d))
        for coin in dict.fromkeys([*coins, "BTC"]):
            for tf in DATA_TFS:
                p = ds.dir / f"{coin}_{tf}.json"
                if p.exists():
                    ds.candles[(coin, tf)] = _read_candles(p)
            p = ds.dir / f"{coin}_funding.json"
            ds.funding[coin] = sorted((int(t), float(r)) for t, r in json.loads(p.read_text())) if p.exists() else []
        p = ds.dir / "fear_greed.json"
        ds.fng = sorted((int(a), int(b), str(c)) for a, b, c in json.loads(p.read_text())) if p.exists() else []
        return ds

    def series(self, coin: str, tf: str) -> ix.Series:
        return ix.series(self.candles.get((coin, tf), []), INTERVALS[tf] * 1000)

    def truncated(self, cut_ms: int, d: Path) -> Dataset:
        """Tests: the same data with nothing after cut_ms (candles closed by then, funding / F&G published by then)."""
        return Dataset(Path(d), {k: [c for c in v if c.t + INTERVALS[k[1]] * 1000 <= cut_ms] for k, v in self.candles.items()},
                       {k: [r for r in v if r[0] <= cut_ms] for k, v in self.funding.items()}, [r for r in self.fng if r[0] * 1000 <= cut_ms])


def _read_candles(p: Path) -> list[Candle]:
    rows = json.loads(p.read_text())
    return sorted((Candle(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])) for r in rows), key=lambda c: c.t)


def data_dir() -> Path:
    return history.DIR / "committee"


def _post(c: httpx.Client, url: str, body: dict[str, Any]) -> Any:
    for attempt in range(6):
        r = c.post(url, json=body)
        if r.status_code == 429:
            time.sleep(2 + attempt * 3)
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError("Hyperliquid kept rate-limiting")


def fetch_dataset(coins: list[str], d: Path | None = None, seed: Path | None = None, *, client: httpx.Client | None = None,
                  log: Any = print) -> Dataset:
    """Download (politely: one request at a time, 0.3 s apart, backing off on 429) the longest history Hyperliquid serves:
    1h / 4h / 1d candles (<= 5000 each), hourly funding paged from the first 4h candle, and the whole daily Fear & Greed series.
    Files already in `d` (or in `seed`, e.g. history.DIR) are merged in, so the stored history only grows."""
    d = Path(d or data_dir())
    old = Dataset.load(d, coins) if d.exists() else Dataset(d)
    seeded = Dataset.load(Path(seed), coins) if seed and Path(seed).exists() else Dataset(d)
    ds = Dataset(d)
    own = client is None
    c = client or httpx.Client(timeout=60, headers={"User-Agent": "PriceChat/1.0 research backtest"})
    now = int(time.time() * 1000)
    try:
        for coin in dict.fromkeys([*coins, "BTC"]):
            for tf in DATA_TFS:
                span = INTERVALS[tf] * 1000
                rows = _post(c, history.API, {"type": "candleSnapshot", "req": {"coin": coin, "interval": tf,
                                                                                  "startTime": now - span * history.MAX_CANDLES, "endTime": now}})
                fresh = {int(r["t"]): Candle(int(r["t"]), float(r["o"]), float(r["h"]), float(r["l"]), float(r["c"]), float(r["v"]))
                         for r in rows if int(r["t"]) + span <= now}  # closed candles only
                merged = {c_.t: c_ for src in (seeded, old) for c_ in src.candles.get((coin, tf), [])}
                merged.update(fresh)
                ds.candles[(coin, tf)] = [merged[t] for t in sorted(merged)]
                log(f"{coin} {tf}: {len(ds.candles[(coin, tf)])} candles")
                time.sleep(0.3)
            have = {t: r for src in (seeded, old) for t, r in src.funding.get(coin, [])}
            first = ds.candles[(coin, "4h")][0].t if ds.candles.get((coin, "4h")) else now - 210 * DAY
            # page forward from the first 4h candle; a stretch already stored without hourly gaps is skipped
            t, gap = first, 2 * 3_600_000
            while t < now - gap:
                known = sorted(have)
                j = bisect.bisect_left(known, t)
                if j < len(known) and known[j] - t <= gap:
                    while j + 1 < len(known) and known[j + 1] - known[j] <= gap:
                        j += 1
                    t = known[j] + 1
                    continue
                rows = _post(c, history.API, {"type": "fundingHistory", "coin": coin, "startTime": t})
                if not rows:
                    break
                for r in rows:
                    have[int(r["time"])] = float(r["fundingRate"])
                nxt = int(rows[-1]["time"]) + 1
                time.sleep(0.3)
                if nxt <= t:
                    break
                t = nxt
            ds.funding[coin] = sorted(have.items())
            log(f"{coin} funding: {len(ds.funding[coin])} hourly rows")
        r = c.get(FNG_URL, params={"limit": 0})
        r.raise_for_status()
        ds.fng = sorted((int(x["timestamp"]), int(x["value"]), str(x.get("value_classification") or "")) for x in r.json().get("data") or [])
        log(f"Fear & Greed: {len(ds.fng)} days")
    finally:
        if own:
            c.close()
    ds.save()
    return ds


# ---------------------------------------------------------------- the time machine (point-in-time data server)
class TimeMachine:
    """An httpx transport answering the Hyperliquid info calls and alternative.me's Fear & Greed from a Dataset, as of `cutoff`
    (ms): candles only when CLOSED by then, funding rows and F&G values only when published by then. Everything later is
    refused at the source, whatever the caller asks for. `served_max` records the latest timestamp it ever handed out."""

    def __init__(self, ds: Dataset) -> None:
        self.ds = ds
        self.cutoff: int | None = None
        self.requests: Counter[str] = Counter()
        self.served_max: dict[str, int] = {}

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def _served(self, kind: str, t: int) -> None:
        self.served_max[kind] = max(self.served_max.get(kind, 0), t)

    def handle(self, req: httpx.Request) -> httpx.Response:
        if self.cutoff is None:
            return httpx.Response(503, text="time machine: no cutoff set")
        cut = self.cutoff
        if req.url.host == HL_HOST and req.method == "POST":
            body = json.loads(req.content or b"{}")
            kind = body.get("type")
            self.requests[str(kind)] += 1
            if kind == "candleSnapshot":
                r = body.get("req") or {}
                tf = r.get("interval")
                if tf not in INTERVALS:
                    return httpx.Response(400, json={"error": "bad interval"})
                span = INTERVALS[tf] * 1000
                lo, hi = int(r.get("startTime") or 0), min(int(r.get("endTime") or cut), cut)
                out = [c for c in self.ds.candles.get((str(r.get("coin")).upper(), tf), []) if lo <= c.t <= hi and c.t + span <= cut]
                for c in out[-1:]:
                    self._served("candles", c.t + span)
                return httpx.Response(200, json=[{"t": c.t, "T": c.t + span - 1, "s": r.get("coin"), "i": tf, "o": str(c.o), "h": str(c.h),
                                                  "l": str(c.l), "c": str(c.c), "v": str(c.v), "n": 0} for c in out[-history.MAX_CANDLES:]])
            if kind == "fundingHistory":
                lo, hi = int(body.get("startTime") or 0), min(int(body.get("endTime") or cut), cut)
                rows = [(t, f) for t, f in self.ds.funding.get(str(body.get("coin")).upper(), []) if lo <= t <= hi][:500]
                for t, _ in rows[-1:]:
                    self._served("funding", t)
                return httpx.Response(200, json=[{"coin": body.get("coin"), "fundingRate": str(f), "premium": "0", "time": t} for t, f in rows])
            return httpx.Response(404, json={"error": f"time machine: {kind} has no point-in-time history"})
        if req.url.host == FNG_HOST:
            self.requests["fear_greed"] += 1
            rows = [r for r in self.ds.fng if r[0] * 1000 <= cut]
            limit = int(req.url.params.get("limit") or 0)
            rows = rows[-limit:] if limit > 0 else rows
            for r in rows[-1:]:
                self._served("fear_greed", r[0] * 1000)
            return httpx.Response(200, json={"name": "Fear and Greed Index", "metadata": {"error": None},
                                             "data": [{"value": str(v), "value_classification": lbl, "timestamp": str(ts)} for ts, v, lbl in reversed(rows)]})
        self.requests["other"] += 1
        return httpx.Response(404, text="time machine: no point-in-time source")


def _evict_cache() -> None:
    """Drop the evidence layer's cached candles / funding / F&G answers (keys carry no cutoff for F&G), before and after each
    decision, so one decision's data never reaches another one, nor a live run in the same process."""
    from app.committee.evidence import common

    with common._lock:
        for k in [k for k in common._cache if k.startswith(("hlc:", "hlfund:", "fng:"))]:
            common._cache.pop(k, None)


# ---------------------------------------------------------------- regimes (pre-declared in the module docstring)
class Regimes:
    def __init__(self, btc_1d: list[Candle]) -> None:
        self.s = ix.series(btc_1d, DAY)
        n = len(self.s.t)
        atrp = [(a / c if a and c else None) for a, c in zip(self.s.atr, self.s.c)]
        self.label: list[str] = []
        for i in range(n):
            e, e0 = self.s.e50[i], self.s.e50[i - REGIME_SLOPE_DAYS] if i >= REGIME_SLOPE_DAYS else None
            prev = [x for x in atrp[max(0, i - REGIME_RANK_DAYS):i] if x is not None]
            if i < 60 or not e or not e0 or atrp[i] is None or len(prev) < 30:
                self.label.append("unknown")
                continue
            rank = sum(1 for x in prev if x < atrp[i]) / len(prev)
            slope = e / e0 - 1
            self.label.append("high-vol" if rank >= REGIME_HIGH_VOL else "trend-up" if slope >= REGIME_SLOPE_BAND
                              else "trend-down" if slope <= -REGIME_SLOPE_BAND else "range")

    def at(self, t_ms: int) -> str:
        k = ix.last_closed(self.s, t_ms)
        return self.label[k] if k >= 0 else "unknown"


# ---------------------------------------------------------------- metrics (conventions in the module docstring)
def sharpe(daily: list[float]) -> float | None:
    if len(daily) < 20:
        return None
    sd = statistics.stdev(daily)
    return round(statistics.fmean(daily) / sd * math.sqrt(365), 3) if sd > 0 else None


def max_drawdown(pnl_seq: list[float]) -> float:
    """Peak-to-trough of the additive equity curve built from the P&L sequence (same units), starting at 0."""
    eq = peak = mdd = 0.0
    for x in pnl_seq:
        eq += x
        peak = max(peak, eq)
        mdd = max(mdd, peak - eq)
    return round(mdd, 4)


def max_drawdown_pct(values: list[float]) -> float:
    """Largest fall from a running peak, in % of that peak (compounded value series, e.g. buy-and-hold)."""
    peak, mdd = -math.inf, 0.0
    for v in values:
        peak = max(peak, v)
        if peak > 0:
            mdd = max(mdd, (peak - v) / peak * 100)
    return round(mdd, 3)


def profit_factor(rs: list[float]) -> float | None:
    win, loss = sum(x for x in rs if x > 0), -sum(x for x in rs if x <= 0)
    return round(win / loss, 3) if loss > 0 else None


def bootstrap_ci(rs: list[float], n: int = BOOT, seed: int = BOOT_SEED) -> list[float] | None:
    if len(rs) < 2:
        return None
    rnd = random.Random(seed)
    means = sorted(statistics.fmean(rnd.choices(rs, k=len(rs))) for _ in range(n))
    return [round(means[int(0.025 * n)], 4), round(means[int(0.975 * n) - 1], 4)]


def _cost_split(t: bt.Trade) -> tuple[float, float, float]:
    """(fees, slippage, funding) in R: fees and slippage from the entry notional for both legs, funding the remainder."""
    R = abs(t.entry - t.stop) or 1e-12
    coin = t.coin
    fees = 2 * bt.FEE * t.entry / R
    sl = 2 * bt.slip(coin) * t.entry / R
    return fees, sl, t.cost_r - fees - sl


def daily_pnl(ts: list[bt.Trade], start_ms: int, end_ms: int) -> list[float]:
    days: dict[int, float] = defaultdict(float)
    for t in ts:
        days[t.t_exit // DAY] += t.r_net * bt.RISK_PCT
    return [days.get(d, 0.0) for d in range(start_ms // DAY, end_ms // DAY + 1)]


def trade_metrics(ts: list[bt.Trade], start_ms: int, end_ms: int, inference_usd: float = 0.0) -> dict[str, Any]:
    ts = sorted(ts, key=lambda x: x.t_exit)
    n = len(ts)
    years = max((end_ms - start_ms) / (365 * DAY), 1e-9)
    out: dict[str, Any] = {"trades": n, "window": [iso(start_ms), iso(end_ms)], "inference_usd": round(inference_usd, 4)}
    if not n:
        return out
    r = [t.r_net for t in ts]
    split = [_cost_split(t) for t in ts]
    notional = [bt.RISK_PCT / 100 / (abs(t.entry - t.stop) / t.entry) for t in ts]  # position notional / equity
    out.update({
        "net_r": round(sum(r), 3), "avg_r": round(statistics.fmean(r), 4), "win_rate": round(sum(1 for x in r if x > 0) / n * 100, 1),
        "net_pnl_pct": round(sum(r) * bt.RISK_PCT, 3), "profit_factor": profit_factor(r),
        "sharpe_daily_ann": sharpe(daily_pnl(ts, start_ms, end_ms)), "max_dd_pct": round(max_drawdown([x * bt.RISK_PCT for x in r]), 3),
        "fees_pct": round(sum(f for f, _, _ in split) * bt.RISK_PCT, 3), "slippage_pct": round(sum(s for _, s, _ in split) * bt.RISK_PCT, 3),
        "funding_pct": round(sum(fu for _, _, fu in split) * bt.RISK_PCT, 3),
        "turnover_x_per_year": round(sum(2 * x for x in notional) / years, 2),
        "ci95_mean_r": bootstrap_ci(r), "avg_hold_h": round(statistics.fmean(t.hold_min for t in ts) / 60, 1),
        "exits": dict(Counter(t.exit for t in ts)), "long_short": dict(Counter(t.side for t in ts)),
    })
    return out


def verdict(sel: dict[str, Any], hold: dict[str, Any], full: dict[str, Any]) -> tuple[str, str]:
    s, h, f = sel.get("avg_r"), hold.get("avg_r"), full.get("avg_r")
    ci = hold.get("ci95_mean_r")
    if (s is not None and s > 0 and h is not None and h > 0 and hold.get("trades", 0) >= MIN_HOLDOUT_TRADES and ci and ci[0] > 0):
        return VERDICTS[2], f"selection {s:+.3f}R, holdout {h:+.3f}R on {hold['trades']} trades, holdout CI {ci}"
    if f is not None and f > 0:
        why = (f"mean {f:+.3f}R over {full['trades']} trades; selection {s if s is None else f'{s:+.3f}'}R, holdout "
               f"{h if h is None else f'{h:+.3f}'}R ({hold.get('trades', 0)} trades), holdout CI {ci}")
        return VERDICTS[1], why
    return VERDICTS[0], ("no trades" if not full.get("trades") else f"mean {f:+.3f}R over {full['trades']} trades")


@dataclass
class Period:
    start: int
    end: int

    @property
    def cut(self) -> int:
        return self.start + int((self.end - self.start) * SELECTION_SHARE)

    def folds(self, n: int = FOLDS) -> list[tuple[int, int]]:
        step = (self.end - self.start) / n
        return [(int(self.start + k * step), int(self.start + (k + 1) * step)) for k in range(n)]


def evaluate(ts: list[bt.Trade], p: Period, inference_usd: float = 0.0, note: str = "") -> dict[str, Any]:
    ts = [t for t in ts if p.start <= t.t_entry <= p.end]
    allm = trade_metrics(ts, p.start, p.end, inference_usd)
    sel = trade_metrics([t for t in ts if t.t_entry < p.cut], p.start, p.cut)
    hold = trade_metrics([t for t in ts if t.t_entry >= p.cut], p.cut, p.end)
    v, why = verdict(sel, hold, allm)
    groups: dict[str, dict[str, list[bt.Trade]]] = {"by_regime": defaultdict(list), "by_coin": defaultdict(list)}
    for t in ts:
        groups["by_regime"][t.regime].append(t)
        groups["by_coin"][t.coin].append(t)
    keep = ("trades", "net_r", "avg_r", "win_rate", "net_pnl_pct", "profit_factor", "ci95_mean_r")
    out = {"all": allm, "selection": sel, "holdout": hold,
           "folds": [{"fold": k + 1, **trade_metrics([t for t in ts if a <= t.t_entry < b or (k == FOLDS - 1 and t.t_entry == b)], a, b)}
                     for k, (a, b) in enumerate(p.folds())],
           "verdict": v, "verdict_reason": why}
    for g, d in groups.items():
        out[g] = {k: {kk: vv for kk, vv in trade_metrics(v_, p.start, p.end).items() if kk in keep} for k, v_ in sorted(d.items())}
    if note:
        out["note"] = note
    return out


# ---------------------------------------------------------------- decisions
@dataclass
class Decision:
    coin: str
    t: int  # decision time = close of run-timeframe candle i (ms)
    i: int
    action: str  # LONG | SHORT | WAIT | ERROR
    rating: str | None = None
    exec_status: str | None = None
    exec_reasons: list[str] = field(default_factory=list)
    plan: dict[str, Any] | None = None
    regime: str = "unknown"
    author: str = "rules"
    with_1h: bool = False
    calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    note: str | None = None


def grid(cfg: Config, ds: Dataset) -> list[tuple[str, int, int]]:
    """(coin, candle index, decision time) for every N-th closed candle (closes at multiples of N x span), after the warm-up."""
    span = INTERVALS[cfg.timeframe] * 1000
    lo = _ms(cfg.start) if cfg.start else 0
    hi = _ms(cfg.end) if cfg.end else 1 << 62
    out = []
    for coin in cfg.coins:
        cs = ds.candles.get((coin, cfg.timeframe), [])
        for i in range(WARMUP, len(cs) - 1):  # the last candle has no next open to enter at
            t = cs[i].t + span
            if t % (cfg.every * span) == 0 and lo <= t <= hi:
                out.append((coin, i, t))
    return sorted(out, key=lambda x: (x[2], x[0]))


def _gate_db(real_db: Any = None) -> Any:
    """An isolated in-memory database for the risk gate: no paper book, no kitchen signals (they are 'now', not 'then'); the
    owner's current risk settings copied in (global pause off)."""
    from sqlalchemy import select
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    import app.models  # noqa: F401 - every table
    from app.db.base import Base
    from app.db.session import build_engine
    from app.models.tradingos import TsSettings

    eng = build_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng, expire_on_commit=False)()
    if real_db is not None:
        try:
            src = real_db.scalar(select(TsSettings).limit(1))
        except Exception:  # noqa: BLE001 - no settings table: the defaults
            src = None
        if src is not None:
            s.add(TsSettings(strategies=dict(src.strategies or {}), max_spread_bps=src.max_spread_bps, min_volume_usd=src.min_volume_usd,
                             min_rr=src.min_rr, paused=False))
            s.commit()
    return s


def _has_1h(ev: dict[str, Any]) -> bool:
    fr = ((((ev.get("sections") or {}).get("technical") or {}).get("facts") or {}).get("frames") or {})
    return bool(fr.get("1h"))


async def _evidence(tm: TimeMachine, http: httpx.AsyncClient, db: Any, coin: str, tf: str, t: int) -> dict[str, Any]:
    from app.committee.evidence import build_evidence

    tm.cutoff = t
    _evict_cache()
    try:
        return await build_evidence(db, coin, tf, as_of=datetime.fromtimestamp(t / 1000, tz=timezone.utc), mode="backtest", http=http)
    finally:
        _evict_cache()
        tm.cutoff = None


def instrument_specs(coins: list[str]) -> dict[str, int]:
    """Hyperliquid's lot size (szDecimals) per coin, from the current market snapshot: a static instrument spec, not market
    data. Backtest evidence has no Hyperliquid meta (no point-in-time history), and without it the risk gate sizes with a
    default of 2 decimals, which rounds a BTC order to zero and rejects every BTC trade as "under the $10 minimum"."""
    from app.tradingos import market

    try:
        _, ms = market.data().markets()
    except Exception:  # noqa: BLE001 - unreadable: the gate's own default, reported in the result
        return {}
    return {c: ms[c].sz_decimals for c in coins if c in ms}


def _with_specs(ev: dict[str, Any], coin: str, specs: dict[str, int]) -> dict[str, Any]:
    if coin not in specs:
        return ev
    f = ev["sections"]["fundamental"]
    f["facts"]["sz_decimals"] = specs[coin]
    f["limitations"] = [*f["limitations"], "sz_decimals: Hyperliquid's current lot size (a static instrument spec), for the risk gate's sizing"]
    return ev


async def rules_decisions(cfg: Config, ds: Dataset, points: list[tuple[str, int, int]], gate_db: Any, regimes: Regimes,
                          progress: Any = None, specs: dict[str, int] | None = None
                          ) -> tuple[list[Decision], dict[tuple[str, int], dict[str, Any]], TimeMachine]:
    """The deterministic committee at every point. Also returns the evidence packs (by coin, time) for the LLM sample."""
    from app.committee import llm, orchestrator, roles

    specs = instrument_specs(cfg.coins) if specs is None else specs
    tm = TimeMachine(ds)
    out: list[Decision] = []
    packs: dict[tuple[str, int], dict[str, Any]] = {}
    async with httpx.AsyncClient(transport=tm.transport()) as http:
        for n, (coin, i, t) in enumerate(points):
            try:
                ev = _with_specs(await _evidence(tm, http, gate_db, coin, cfg.timeframe, t), coin, specs)
                run = await orchestrator.run_committee(gate_db, coin, cfg.timeframe, mode="backtest",
                                                       as_of=datetime.fromtimestamp(t / 1000, tz=timezone.utc), evidence=ev,
                                                       backend=llm.Backend(), persist=False, use_cache=False, depth=cfg.depth)
            except Exception as e:  # noqa: BLE001 - recorded as an ERROR decision, never a crash of the whole backtest
                out.append(Decision(coin, t, i, "ERROR", regime=regimes.at(t), note=f"{type(e).__name__}: {str(e)[:160]}"))
                continue
            r = run.result
            if cfg.mode == "llm":
                packs[(coin, t)] = ev
            ex = r.get("execution") or {}
            action = r["verdict"]["action"]
            out.append(Decision(coin, t, i, action, r["verdict"].get("rating"), ex.get("status"), list(ex.get("reasons") or [])[:3],
                                roles.plan_from(ev, action), regimes.at(t), "rules", _has_1h(ev)))
            if progress and n % 25 == 0:
                progress(n + 1, len(points))
    return out, packs, tm


# ---------------------------------------------------------------- LLM: anonymised evidence
_ISO = re.compile(r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?")


def _names(symbol: str) -> list[str]:
    from app.tradingos.tickers import NAMES

    return sorted({symbol.upper(), *(k for k, v in NAMES.items() if v == symbol.upper())}, key=len, reverse=True)


def _scrub(x: Any, symbol: str, as_of_ms: int, span_ms: int) -> Any:
    pats = [re.compile(rf"(?<![A-Za-z0-9]){re.escape(n)}(?![A-Za-z0-9])", re.I) for n in _names(symbol)]

    def rel(m: re.Match[str]) -> str:
        try:
            k = (as_of_ms - _ms(m.group(0).replace(" ", "T"))) / span_ms
        except ValueError:
            return "t-?"
        return f"t-{k:g}" if k == int(k) else f"t-{k:.1f}"

    def walk(v: Any) -> Any:
        if isinstance(v, str):
            v = _ISO.sub(rel, v)
            for p in pats:
                v = p.sub("ASSET", v)
            return v
        if isinstance(v, dict):
            return {walk(k) if isinstance(k, str) else k: walk(vv) for k, vv in v.items()}
        if isinstance(v, list):
            return [walk(vv) for vv in v]
        return v

    return walk(x)


def anonymise(ev: dict[str, Any], ds: Dataset, coin: str, tf: str, t: int, base: float = 100.0) -> tuple[dict[str, Any], float]:
    """The pack an LLM sees: ASSET, prices x k (first run-timeframe candle shown closes at 100), relative times. The technical
    section is RECOMPUTED from the rebased candles (texts then carry rebased numbers); fundamentals / news stay unavailable;
    sentiment keeps funding rates and Fear & Greed values (no prices). Returns (pack, k)."""
    from app.committee.evidence import sentiment, technical
    from app.committee.evidence.pack import _section
    from app.tradingos import readout

    frames = {f: technical.closed_by(ds.candles.get((coin, f), []), f, t)[-technical.CANDLES:] for f in technical.frames_for(tf)}
    run = frames.get(tf) or []
    if not run:
        raise ValueError("no run-timeframe candles to anonymise")
    k = base / run[0].c
    reb = {f: [Candle(c.t, c.o * k, c.h * k, c.l * k, c.c * k, c.v) for c in cs] for f, cs in frames.items()}
    price = reb[tf][-1].c
    as_of = datetime.fromtimestamp(t / 1000, tz=timezone.utc)
    tech = technical.build(reb, tf, price, as_of)
    refs = [{"id": "P1", "source": "perpetual close price (anonymised)", "url": None, "ts": iso(t), "fetched_at": None}]
    frame_ref = {}
    for n, f in enumerate([f for f in reb if reb[f]], start=1):
        frame_ref[f] = f"T{n}"
        refs.append({"id": f"T{n}", "source": f"{f} candles (anonymised)", "url": None, "ts": (tech["facts"]["frames"].get(f) or {}).get("last_closed"),
                     "fetched_at": None})
    verdict_, note = readout.chart_rules(reb[tf])
    sref = f"T{len(frame_ref) + 1}"
    refs.append({"id": sref, "source": "Price Chat signal rules (trend_pullback_v1, chart rules)", "url": None, "ts": iso(t), "fetched_at": None})
    tech["facts"]["signal"] = {"rule": "trend_pullback_v1", "verdict": verdict_, "first_fail": note if verdict_ == "WAIT" else None,
                               "scope": "chart rules", "ref": sref}
    tsec = _section(tech["status"], tech["limitations"])
    tsec.update(facts=tech["facts"], lean=tech["lean"], score=tech["score"], refs=[*frame_ref.values(), sref])
    tsec["reasons"] = [{"text": w, "refs": [frame_ref[w.split()[0]]] if w.split()[0] in frame_ref else []} for w in tech["why"]]
    tsec["reasons"].append({"text": f"trend_pullback_v1 (chart rules): {verdict_}" + (f", first failing check: {note}" if verdict_ == "WAIT" else ""),
                            "refs": [sref]})
    real_s = ((ev.get("sections") or {}).get("sentiment") or {})
    rf = real_s.get("facts") or {}
    sf: dict[str, Any] = {kk: rf[kk] for kk in ("funding_1h_pct", "funding_24h_avg_pct", "funding_7d_avg_pct") if kk in rf}
    srefs = []
    if sf:
        refs.append({"id": "S1", "source": "perpetual funding history", "url": None, "ts": None, "fetched_at": None})
        sf["funding_ref"] = "S1"
        srefs.append("S1")
    if rf.get("fear_greed"):
        g = rf["fear_greed"]
        refs.append({"id": "S2", "source": "Crypto Fear & Greed Index (daily)", "url": None, "ts": g.get("ts"), "fetched_at": None})
        sf["fear_greed"] = {"value": g.get("value"), "label": g.get("label"), "ts": g.get("ts"), "value_7d_ago": g.get("value_7d_ago"), "ref": "S2"}
        srefs.append("S2")
    lean_, score, why = sentiment.lean(sf)
    ssec = _section("partial" if srefs else "unavailable", list(real_s.get("limitations") or []))
    ssec.update(facts=sf, lean=lean_, score=score, refs=srefs,
                reasons=[{"text": w, "refs": ["S1"] if "unding" in w and "S1" in srefs else ["S2"] if "Fear" in w and "S2" in srefs else []} for w in why])
    lim = "no point-in-time history (backtest)"
    pack = {"asset": {"symbol": "ASSET", "venue": "hyperliquid", "market_type": "perp", "market_id": "ASSET", "label": "perpetual (anonymised)",
                      "hl_tradable": True, "alternatives": []},
            "timeframe": tf, "as_of": iso(t), "mode": "backtest",
            "price": {"value": round(price, 6), "kind": "close", "source": "anonymised", "ts": iso(t), "stale": tech["status"] == "stale", "ref": "P1"},
            "sections": {"technical": tsec, "fundamental": _section("unavailable", [f"fundamentals: {lim}"]),
                         "news": _section("unavailable", [f"news: {lim}"]), "sentiment": ssec},
            "refs": refs, "built_at": None, "degraded": []}
    return _scrub(pack, coin, t, INTERVALS[tf] * 1000), k


_NUM = re.compile(r"(?<![A-Za-z])[-+]?\d[\d,]*\.?\d*")


PROBE_BASE = 137.0


def leak_check(pack: dict[str, Any], symbol: str, real_levels: list[float], probe: dict[str, Any] | None = None) -> list[str]:
    """Problems that would let a model recognise the asset / the date: its ticker or name, a calendar date, a real price level
    (any number within 0.05% of one, at >= 4 significant figures). With `probe` (the same evidence anonymised at another base,
    PROBE_BASE), a matching number counts only when it also appears unchanged in the probe: a rebased number moves with the
    base, a leaked real price doesn't. That keeps an asset trading near 100 (rebased numbers land near real ones by
    coincidence) from being refused for nothing."""
    text = json.dumps(pack)
    probe_nums = {m.group(0).replace(",", "") for m in _NUM.finditer(json.dumps(probe))} if probe is not None else None
    out = []
    for n in _names(symbol):
        if re.search(rf"(?<![A-Za-z0-9]){re.escape(n)}(?![A-Za-z0-9])", text, re.I):
            out.append(f"name {n}")
    if re.search(r"\d{4}-\d{2}-\d{2}", text):
        out.append("calendar date")
    levels = [abs(x) for x in real_levels if x and abs(x) >= 1]
    for m in _NUM.finditer(text):
        s = m.group(0).replace(",", "")
        try:
            v = abs(float(s))
        except ValueError:
            continue
        if len(s.replace(".", "").replace("-", "").replace("+", "").lstrip("0")) < 4:
            continue
        if any(abs(v - lv) <= lv * 5e-4 for lv in levels) and (probe_nums is None or s in probe_nums):
            out.append(f"real price {s}")
            break
    return out


def _real_levels(ev: dict[str, Any]) -> list[float]:
    vals: list[float] = []
    t = ((ev.get("sections") or {}).get("technical") or {}).get("facts") or {}
    vals.append(float((ev.get("price") or {}).get("value") or 0))
    for f in (t.get("frames") or {}).values():
        if f:
            vals += [float(f[k]) for k in ("close", "ema20", "ema50", "ema200", "bb_upper", "bb_lower", "bb_mid") if f.get(k)]
    vals += [float(x) for x in (t.get("support") or []) + (t.get("resistance") or [])]
    for s in (t.get("scenarios") or {}).values():
        if s:
            vals += [float(s["trigger"]), float(s["invalidation"]), *map(float, s["targets"])]
    return vals


async def llm_decisions(cfg: Config, ds: Dataset, sample: list[Decision], packs: dict[tuple[str, int], dict[str, Any]], gate_db: Any,
                        backend: Any, real_db: Any = None) -> tuple[list[Decision], dict[str, Any]]:
    """The LLM committee on the sample (anonymised evidence), the trade planned and gated on the real evidence."""
    from app.committee import llm, orchestrator, risk_gate, roles

    out: list[Decision] = []
    info = {"backend": backend.name, "models": backend.models(), "calls": 0, "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0,
            "anonymisation_refused": 0, "stopped": None}
    for d in sample:
        if backend.paid and info["cost_usd"] >= cfg.llm_max_usd:
            info["stopped"] = f"budget cap ${cfg.llm_max_usd:.2f} reached"
            break
        if real_db is not None:
            why = llm.over_budget(real_db, backend.paid)
            if why:
                info["stopped"] = why
                break
        ev = packs.get((d.coin, d.t))
        if ev is None:
            continue
        try:
            anon, _ = anonymise(ev, ds, d.coin, cfg.timeframe, d.t)
        except ValueError as e:
            out.append(replace(d, action="ERROR", author="llm", note=str(e)))
            continue
        problems = leak_check(anon, d.coin, _real_levels(ev), anonymise(ev, ds, d.coin, cfg.timeframe, d.t, PROBE_BASE)[0])
        if problems:
            info["anonymisation_refused"] += 1
            out.append(replace(d, action="ERROR", author="llm", note="anonymisation refused: " + ", ".join(problems)))
            continue
        try:
            run = await orchestrator.run_committee(gate_db, "ASSET", cfg.timeframe, mode="backtest", evidence=anon, backend=backend,
                                                   persist=False, use_cache=False, depth=cfg.depth)
        except Exception as e:  # noqa: BLE001
            out.append(replace(d, action="ERROR", author="llm", note=f"{type(e).__name__}"))
            continue
        r, meta = run.result, run.result["meta"]
        action = r["verdict"]["action"]
        plan = roles.plan_from(ev, action)
        ex = risk_gate.evaluate(gate_db, ev, action, plan, stale_reason=roles.data_guard(ev))
        for k in ("calls", "tokens_in", "tokens_out"):
            info[k] += int(meta.get(k) or 0)
        info["cost_usd"] = round(info["cost_usd"] + float(meta.get("cost_usd") or 0), 6)
        if real_db is not None and meta.get("calls"):
            llm.record(real_db, int(meta.get("tokens_in") or 0), int(meta.get("tokens_out") or 0), float(meta.get("cost_usd") or 0))
        authors = {(v or {}).get("author") for v in (r.get("analysts") or {}).values()} | {r["verdict"].get("author")}
        out.append(replace(d, action=action, rating=r["verdict"].get("rating"), exec_status=ex["status"], exec_reasons=ex["reasons"][:3],
                           plan=plan if action in ("LONG", "SHORT") else None, author=",".join(sorted(a for a in authors if a)),
                           calls=int(meta.get("calls") or 0), tokens_in=int(meta.get("tokens_in") or 0), tokens_out=int(meta.get("tokens_out") or 0),
                           cost_usd=float(meta.get("cost_usd") or 0), note="; ".join(meta.get("degraded") or [])[:300] or None))
    return out, info


def sample_of(ds: list[Decision], n: int) -> list[Decision]:
    """n decisions evenly spaced through the (time-ordered) grid; deterministic."""
    ok = [d for d in ds if d.action != "ERROR"]
    if len(ok) <= n:
        return ok
    return [ok[round(k * (len(ok) - 1) / (n - 1))] for k in range(n)] if n > 1 else ok[:1]


# ---------------------------------------------------------------- trades
@dataclass
class Book:
    """Per coin: run-timeframe series, funding, and the stored data needed to simulate."""

    series: dict[str, ix.Series]
    funding: dict[str, list[tuple[int, float]]]


def committee_trades(decisions: list[Decision], book: Book, name: str, gated: bool = True) -> tuple[list[bt.Trade], Counter[str]]:
    trades: list[bt.Trade] = []
    skipped: Counter[str] = Counter()
    last_exit: dict[str, int] = {}
    for d in sorted(decisions, key=lambda x: x.t):
        if d.action not in ("LONG", "SHORT") or not d.plan:
            continue
        if gated and d.exec_status != "APPROVED":
            continue
        if d.t < last_exit.get(d.coin, 0):
            skipped["a position on this coin is still open"] += 1
            continue
        f, fund = book.series[d.coin], book.funding.get(d.coin, [])
        i = d.i
        if i + 1 >= len(f.t):
            skipped["no next candle"] += 1
            continue
        long, sg, sl = d.action == "LONG", (1 if d.action == "LONG" else -1), bt.slip(d.coin)
        entry = f.o[i + 1] * (1 + sg * sl)
        stop, tgs = float(d.plan["stop"]), [float(x) for x in d.plan["targets"]]
        if (long and entry <= stop) or (not long and entry >= stop):
            skipped["opened beyond the stop"] += 1
            continue
        if (long and entry >= tgs[0]) or (not long and entry <= tgs[0]):
            skipped["opened beyond TP1"] += 1
            continue
        if len(tgs) >= 2:
            r = bt.simulate(f, i, d.action, stop, 1.0, 0.0, MAX_HOLD_MIN, d.coin, fund, fixed_targets=(tgs[0], tgs[1]))
        else:
            r = bt.simulate(f, i, d.action, stop, None, 0.0, MAX_HOLD_MIN, d.coin, fund, fixed_targets=(tgs[0], tgs[0]))
        if r is None:
            skipped["history ended before the trade finished"] += 1
            continue
        trades.append(bt.Trade(d.coin, name, d.action, d.regime, d.t, r["t_entry"], r["t_exit"], r["entry"], stop, r["r_net"], r["r_gross"],
                               r["cost_r"], r["exit"], r["hold_min"]))
        last_exit[d.coin] = r["t_exit"]
    return trades, skipped


def ema_cross_trades(points: list[tuple[str, int, int]], book: Book, regimes: Regimes) -> list[bt.Trade]:
    """EMA20 / EMA50 cross since the previous grid point -> that side; stop 2 x ATR14, single exit at 2R, 14-day time stop."""
    out: list[bt.Trade] = []
    state: dict[str, bool | None] = {}
    last_exit: dict[str, int] = {}
    for coin, i, t in points:
        f = book.series[coin]
        e20, e50, atr = f.e20[i], f.e50[i], f.atr[i]
        now = None if e20 is None or e50 is None else e20 > e50
        prev = state.get(coin)
        state[coin] = now
        if now is None or prev is None or now == prev or not atr or t < last_exit.get(coin, 0):
            continue
        side = "LONG" if now else "SHORT"
        stop = f.c[i] - (1 if now else -1) * 2 * atr
        r = bt.simulate(f, i, side, stop, None, 2.0, MAX_HOLD_MIN, coin, book.funding.get(coin, []))
        if r is None:
            continue
        out.append(bt.Trade(coin, "ema_cross", side, regimes.at(t), t, r["t_entry"], r["t_exit"], r["entry"], stop, r["r_net"], r["r_gross"],
                            r["cost_r"], r["exit"], r["hold_min"]))
        last_exit[coin] = r["t_exit"]
    return out


_HISTORY_LOCK = threading.Lock()


@contextmanager
def _history_dir(d: Path) -> Iterator[None]:
    """The lab reads history.DIR: point it at the backtest's dataset while the Kiwi strategies are replayed (research code only
    reads history.DIR; live trading never does)."""
    with _HISTORY_LOCK:
        old = history.DIR
        history.DIR = Path(d)
        try:
            yield
        finally:
            history.DIR = old


def kiwi_trades(coins: list[str], ds: Dataset, p: Period, regimes: Regimes) -> dict[str, tuple[list[bt.Trade], str]]:
    from app.tradingos import lab

    out: dict[str, tuple[list[bt.Trade], str]] = {}
    cache: dict[Any, Any] = {}
    with _history_dir(ds.dir):
        for name, vname in KIWI_VARIANTS.items():
            v = next(x for x in lab.VARIANTS if x.name == vname)
            have = [c for c in coins if ds.candles.get((c, v.tf))]
            if not have or not ds.candles.get(("BTC", v.tf)):
                out[name] = ([], f"no {v.tf} history")
                continue
            ts = [replace(t, regime=regimes.at(t.t_signal)) for t in lab.candidates(have, v, cache) if p.start <= t.t_signal <= p.end]
            first = min((ds.candles[(c, v.tf)][0].t for c in have), default=p.start)
            note = f"lab variant '{vname}' ({v.tf}, TP1 {v.exit.tp1_r:g}R half / TP2 {v.exit.tp2_r:g}R rest, no time exit), every closed {v.tf} candle"
            if first > p.start:
                note += f"; {v.tf} history starts {iso(first)[:10]} (Hyperliquid serves 5000 candles), so it covers less of the window"
            out[name] = (ts, note)
    return out


def buy_hold(ds: Dataset, coins: list[str], tf: str, starts: dict[str, int], p: Period, regimes: Regimes) -> dict[str, Any]:
    """Long from the first decision (entry at the next open + slippage + fee) to the last close (exit - slippage - fee); the
    perp version also pays the real hourly funding a long paid. Daily series = run-timeframe closes at 00:00 UTC."""
    span = INTERVALS[tf] * 1000
    out: dict[str, Any] = {}
    curves: dict[str, dict[int, float]] = {}
    for coin in coins:
        cs = [c for c in ds.candles.get((coin, tf), []) if c.t >= starts.get(coin, p.start) and c.t + span <= p.end]
        if len(cs) < 2:
            continue
        sl = bt.slip(coin)
        o0, c1 = cs[0].o, cs[-1].c
        gross = c1 / o0 - 1
        net = (c1 * (1 - sl)) / (o0 * (1 + sl)) - 1 - 2 * bt.FEE
        fund = sum(r for t, r in ds.funding.get(coin, []) if cs[0].t < t <= cs[-1].t + span)
        daily = {(c.t + span) // DAY: c.c for c in cs if (c.t + span) % DAY == 0}
        curves[coin] = daily
        vals = [daily[d] for d in sorted(daily)]
        rets = [vals[k] / vals[k - 1] - 1 for k in range(1, len(vals))]
        cut_px = next((c.c for c in reversed(cs) if c.t + span <= p.cut), None)
        out[coin] = {"window": [iso(cs[0].t), iso(cs[-1].t + span)], "return_pct": round(gross * 100, 2), "net_return_pct": round(net * 100, 2),
                     "net_return_perp_pct": round((net - fund) * 100, 2), "funding_paid_pct": round(fund * 100, 2),
                     "sharpe_daily_ann": sharpe([r * 100 for r in rets]), "max_dd_pct": max_drawdown_pct(vals),
                     "selection_return_pct": round((cut_px / o0 - 1) * 100, 2) if cut_px and cs[0].t < p.cut else None,
                     "holdout_return_pct": round((c1 / cut_px - 1) * 100, 2) if cut_px and cs[0].t < p.cut else None,
                     "folds_return_pct": _fold_returns(cs, span, p)}
    common = sorted(set.intersection(*(set(v) for v in curves.values()))) if curves else []
    if len(common) > 2:
        base = {c: curves[c][common[0]] for c in curves}
        val = [statistics.fmean(curves[c][d] / base[c] for c in curves) for d in common]
        rets = [val[k] / val[k - 1] - 1 for k in range(1, len(val))]
        by_reg: dict[str, list[float]] = defaultdict(list)
        for k in range(1, len(common)):
            by_reg[regimes.at(common[k - 1] * DAY)].append(rets[k - 1] * 100)
        out["basket_equal_weight"] = {"coins": sorted(curves), "window": [iso(common[0] * DAY), iso(common[-1] * DAY)],
                                      "return_pct": round((val[-1] - 1) * 100, 2), "sharpe_daily_ann": sharpe([r * 100 for r in rets]),
                                      "max_dd_pct": max_drawdown_pct(val), "note": "no rebalancing; before fees (one round trip ~0.1-0.2%)",
                                      "mean_daily_return_pct_by_regime": {k: {"days": len(v), "mean_pct": round(statistics.fmean(v), 4)}
                                                                          for k, v in sorted(by_reg.items())}}
    return out


def _fold_returns(cs: list[Candle], span: int, p: Period) -> list[float | None]:
    out: list[float | None] = []
    for a, b in p.folds():
        w = [c for c in cs if a <= c.t and c.t + span <= b]
        out.append(round((w[-1].c / w[0].o - 1) * 100, 2) if len(w) > 1 else None)
    return out


# ---------------------------------------------------------------- the run
def _summary(ds_: list[Decision], skipped: Counter[str]) -> dict[str, Any]:
    acts = Counter(d.action for d in ds_)
    ex = Counter(d.exec_status for d in ds_ if d.action in ("LONG", "SHORT"))
    rej: Counter[str] = Counter()
    for d in ds_:
        if d.action in ("LONG", "SHORT") and d.exec_status == "REJECTED":
            rej[re.sub(r"[-+]?\$?\d[\d,.]*%?x?", "#", d.exec_reasons[0]) if d.exec_reasons else "?"] += 1
    errors = Counter((d.note or "")[:80] for d in ds_ if d.action == "ERROR")
    return {"total": len(ds_), "actions": dict(acts), "proposals_execution": dict(ex), "risk_gate_rejections": dict(rej.most_common(12)),
            "skipped_after_approval": dict(skipped), "errors": dict(errors.most_common(5)), "with_1h_frame": sum(1 for d in ds_ if d.with_1h),
            "by_regime": dict(Counter(d.regime for d in ds_))}


def _compact(ts: list[bt.Trade]) -> list[list[Any]]:
    return [[t.coin, t.side, iso(t.t_signal), iso(t.t_exit), round(t.entry, 6), round(t.stop, 6), round(t.r_net, 3), t.exit, t.regime] for t in ts]


async def run_backtest(cfg: Config, ds: Dataset, *, real_db: Any = None, backend: Any = None, provider: Any = None, gate: Any = None,
                       progress: Any = None) -> dict[str, Any]:
    """The whole comparison (see the module docstring). `backend` (llm mode): a committee llm.Backend; default llm.choose()."""
    from app.committee import llm

    started = time.time()
    if not ds.candles.get(("BTC", "1d")):
        raise ValueError("the dataset has no BTC 1d candles (needed for the regimes)")
    missing = [c for c in cfg.coins if not ds.candles.get((c, cfg.timeframe))]
    if missing:
        raise ValueError(f"no {cfg.timeframe} history for {', '.join(missing)}")
    regimes = Regimes(ds.candles[("BTC", "1d")])
    points = grid(cfg, ds)
    if not points:
        raise ValueError("no decision points (history too short for the warm-up, or the start / end window is empty)")
    span = INTERVALS[cfg.timeframe] * 1000
    end = min(max(ds.candles[(c, cfg.timeframe)][-1].t + span for c in cfg.coins), _ms(cfg.end) if cfg.end else 1 << 62)
    p = Period(points[0][2], end)
    starts = {c: min((t for cc, _, t in points if cc == c), default=p.start) for c in cfg.coins}
    book = Book({c: ds.series(c, cfg.timeframe) for c in cfg.coins}, {c: ds.funding.get(c, []) for c in cfg.coins})
    gate_db = _gate_db(real_db)
    try:
        if cfg.mode == "llm":
            # rules decisions are needed only on the sample: pick the sample from the grid first (evenly spaced), then decide
            pre = [Decision(c, t, i, "WAIT") for c, i, t in points]
            chosen = {(d.coin, d.t) for d in sample_of(pre, cfg.llm_max_decisions)}
            points_run = [x for x in points if (x[0], x[2]) in chosen]
        else:
            points_run = points
        specs = instrument_specs(cfg.coins)
        rules, packs, tm = await rules_decisions(cfg, ds, points_run, gate_db, regimes, progress, specs)
        result: dict[str, Any] = {"config": asdict(cfg), "period": {"start": iso(p.start), "end": iso(p.end), "selection_until": iso(p.cut),
                                                                    "folds": [[iso(a), iso(b)] for a, b in p.folds()]},
                                  "methodology": __doc__.split("Run:")[0].strip(), "limitations": list(LIMITATIONS)}
        strategies: dict[str, Any] = {}
        c_tr, c_skip = committee_trades(rules, book, "committee_rules")
        u_tr, _ = committee_trades(rules, book, "committee_rules_ungated", gated=False)
        decisions = {"committee_rules": _summary(rules, c_skip)}
        strategies["committee_rules"] = evaluate(bt.portfolio(c_tr), p, 0.0, "deterministic committee, rules backend; trades only when the "
                                                 "Kiwi risk gate APPROVED" + (" (llm mode: the same sample as the LLM)" if cfg.mode == "llm" else ""))
        strategies["committee_rules_ungated"] = evaluate(bt.portfolio(u_tr), p, 0.0, "pre-declared variant, information only: every "
                                                         "committee LONG / SHORT with a plan, risk gate ignored")
        if cfg.mode == "llm":
            info: dict[str, Any]
            be = backend
            why = None
            if be is None:
                be, why = await llm.choose(real_db if real_db is not None else gate_db, provider, gate)
            if be.name == "rules":
                info = {"backend": "rules", "skipped": f"no model backend available ({why or 'rules backend configured'}); LLM mode not run"}
                l_dec: list[Decision] = []
            elif be.paid and cfg.llm_max_usd <= 0:
                info = {"backend": be.name, "skipped": "a paid backend needs llm_max_usd > 0; LLM mode not run"}
                l_dec = []
            else:
                l_dec, info = await llm_decisions(cfg, ds, rules, packs, gate_db, be, real_db)
            info["contamination_note"] = CONTAMINATION_NOTE
            l_tr, l_skip = committee_trades(l_dec, book, "committee_llm")
            decisions["committee_llm"] = _summary(l_dec, l_skip)
            strategies["committee_llm"] = evaluate(bt.portfolio(l_tr), p, float(info.get("cost_usd") or 0.0),
                                                   f"LLM committee ({info.get('backend')}), anonymised evidence, {len(l_dec)} sampled decisions")
            agree = sum(1 for a, b in zip(sorted(rules, key=lambda d: (d.t, d.coin)), sorted(l_dec, key=lambda d: (d.t, d.coin)))
                        if (a.coin, a.t) == (b.coin, b.t) and a.action == b.action)
            info["agreement_with_rules"] = f"{agree}/{len(l_dec)} same action"
            result["llm"] = info
            result["trades_llm"] = _compact(l_tr)
        for name, (ts, note) in kiwi_trades(cfg.coins, ds, p, regimes).items():
            strategies[name] = evaluate(bt.portfolio(ts), p, 0.0, note)
        strategies["ema_cross"] = evaluate(bt.portfolio(ema_cross_trades(points, book, regimes)), p, 0.0,
                                           f"EMA20/EMA50 cross on {cfg.timeframe} checked every {cfg.every} candles, stop 2 x ATR14, exit 2R")
        result["strategies"] = strategies
        result["buy_hold"] = buy_hold(ds, cfg.coins, cfg.timeframe, starts, p, regimes)
        result["decisions"] = decisions
        result["regimes"] = {"definition": "BTC 1d: high-vol if ATR14% rank >= 0.80 of the previous 365 days; else trend-up / trend-down "
                                           "if EMA50 5-day slope >= +1% / <= -1%; else range",
                             "decision_points": dict(Counter(regimes.at(t) for _, _, t in points))}
        result["inference"] = {"rules_usd": 0.0, "llm": {k: result.get("llm", {}).get(k) for k in ("backend", "calls", "tokens_in", "tokens_out", "cost_usd")}
                               if cfg.mode == "llm" else None}
        result["instrument_specs"] = {"sz_decimals": specs, "note": "Hyperliquid's current lot sizes (static), injected into the "
                                      "backtest evidence for the risk gate's sizing" if specs else "unavailable: the gate's default sizing was used"}
        result["point_in_time"] = {"time_machine_requests": dict(tm.requests), "evidence_mode": "backtest",
                                   "check": "every candle served closed by its decision time; funding / F&G published by then"}
        result["trades_committee_rules"] = _compact(c_tr)
        result["verdicts"] = {k: v["verdict"] for k, v in strategies.items()}
        result["duration_s"] = round(time.time() - started, 1)
        return result
    finally:
        gate_db.close()


# ---------------------------------------------------------------- markdown tables
def _f(v: Any, nd: int = 2) -> str:
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.{nd}f}"
    if isinstance(v, list):
        return "[" + ", ".join(_f(x, nd) for x in v) + "]"
    return str(v)


def markdown(r: dict[str, Any]) -> str:
    L: list[str] = []
    cfg, per = r["config"], r["period"]
    L.append(f"# Kiwi AI Investment Committee backtest ({cfg['mode']} mode)\n")
    L.append(f"Coins {', '.join(cfg['coins'])} · {cfg['timeframe']} · decision every {cfg['every']} candles · period {per['start']} -> "
             f"{per['end']} · selection until {per['selection_until']} (60/40) · {FOLDS} walk-forward folds · run time {r.get('duration_s')} s\n")
    L.append("Never read as a claim of profitability. Verdicts are only: " + " / ".join(f"'{v}'" for v in VERDICTS) + ".\n")
    L.append("## Strategies, whole period\n")
    L.append("| strategy | trades | win % | avg R | net R | net % eq | PF | Sharpe | max DD % | fees % | slip % | funding % | turnover x/yr | inference $ | 95% CI mean R | verdict |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for k, v in r["strategies"].items():
        a = v["all"]
        L.append(f"| {k} | {a.get('trades', 0)} | {_f(a.get('win_rate'), 1)} | {_f(a.get('avg_r'), 3)} | {_f(a.get('net_r'))} | {_f(a.get('net_pnl_pct'))} | "
                 f"{_f(a.get('profit_factor'))} | {_f(a.get('sharpe_daily_ann'))} | {_f(a.get('max_dd_pct'))} | {_f(a.get('fees_pct'))} | "
                 f"{_f(a.get('slippage_pct'))} | {_f(a.get('funding_pct'))} | {_f(a.get('turnover_x_per_year'))} | {_f(a.get('inference_usd'), 4)} | "
                 f"{_f(a.get('ci95_mean_r'), 3)} | {v['verdict']} |")
    L.append("\n## Selection (first 60%) vs holdout (last 40%)\n")
    L.append("| strategy | sel trades | sel avg R | sel net % | sel Sharpe | hold trades | hold avg R | hold net % | hold Sharpe | hold 95% CI | verdict reason |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for k, v in r["strategies"].items():
        s, h = v["selection"], v["holdout"]
        L.append(f"| {k} | {s.get('trades', 0)} | {_f(s.get('avg_r'), 3)} | {_f(s.get('net_pnl_pct'))} | {_f(s.get('sharpe_daily_ann'))} | {h.get('trades', 0)} | "
                 f"{_f(h.get('avg_r'), 3)} | {_f(h.get('net_pnl_pct'))} | {_f(h.get('sharpe_daily_ann'))} | {_f(h.get('ci95_mean_r'), 3)} | {v['verdict_reason']} |")
    L.append("\n## Walk-forward folds (avg R / trades / net % equity)\n")
    folds = r["period"]["folds"]
    L.append("| strategy | " + " | ".join(f"fold {k + 1} ({a[:10]} -> {b[:10]})" for k, (a, b) in enumerate(folds)) + " |")
    L.append("|---|" + "---|" * len(folds))
    for k, v in r["strategies"].items():
        L.append(f"| {k} | " + " | ".join(f"{_f(f.get('avg_r'), 3)} / {f.get('trades', 0)} / {_f(f.get('net_pnl_pct'))}" for f in v["folds"]) + " |")
    L.append("\n## By market regime (avg R / trades / win %)\n")
    regs = [x for x in REGIMES if any(x in v["by_regime"] for v in r["strategies"].values())]
    L.append("| strategy | " + " | ".join(regs) + " |")
    L.append("|---|" + "---|" * len(regs))
    for k, v in r["strategies"].items():
        L.append(f"| {k} | " + " | ".join((lambda m: f"{_f(m.get('avg_r'), 3)} / {m.get('trades', 0)} / {_f(m.get('win_rate'), 1)}" if m else "-")(v["by_regime"].get(x))
                                          for x in regs) + " |")
    L.append(f"\nDecision points per regime: {r['regimes']['decision_points']}. Definition: {r['regimes']['definition']}.\n")
    L.append("## By coin (avg R / trades / net % equity)\n")
    coins = cfg["coins"]
    L.append("| strategy | " + " | ".join(coins) + " |")
    L.append("|---|" + "---|" * len(coins))
    for k, v in r["strategies"].items():
        L.append(f"| {k} | " + " | ".join((lambda m: f"{_f(m.get('avg_r'), 3)} / {m.get('trades', 0)} / {_f(m.get('net_pnl_pct'))}" if m else "-")(v["by_coin"].get(c))
                                          for c in coins) + " |")
    L.append("\n## Buy and hold\n")
    L.append("| asset | window | price return % | net of fees+slip % | perp, net of funding % | Sharpe | max DD % | selection % | holdout % | folds % |")
    L.append("|---|---|---|---|---|---|---|---|---|---|")
    for k, v in r["buy_hold"].items():
        if k == "basket_equal_weight":
            continue
        L.append(f"| {k} | {v['window'][0][:10]} -> {v['window'][1][:10]} | {_f(v['return_pct'])} | {_f(v['net_return_pct'])} | {_f(v['net_return_perp_pct'])} | "
                 f"{_f(v['sharpe_daily_ann'])} | {_f(v['max_dd_pct'])} | {_f(v['selection_return_pct'])} | {_f(v['holdout_return_pct'])} | {_f(v['folds_return_pct'])} |")
    b = r["buy_hold"].get("basket_equal_weight")
    if b:
        L.append(f"| basket {'/'.join(b['coins'])} | {b['window'][0][:10]} -> {b['window'][1][:10]} | {_f(b['return_pct'])} | - | - | "
                 f"{_f(b['sharpe_daily_ann'])} | {_f(b['max_dd_pct'])} | - | - | - |")
        L.append(f"\nBasket mean daily return by regime: {b['mean_daily_return_pct_by_regime']}\n")
    L.append("## Committee decisions\n")
    for k, d in r["decisions"].items():
        L.append(f"**{k}**: {d['total']} decisions, actions {d['actions']}, execution of LONG/SHORT proposals {d['proposals_execution']}, "
                 f"with the 1h frame {d['with_1h_frame']}, errors {d['errors'] or 0}.\n")
        if d["risk_gate_rejections"]:
            L.append("Risk-gate rejections (first reason, numbers masked): " + "; ".join(f"{n}x {why}" for why, n in d["risk_gate_rejections"].items()) + "\n")
        if d["skipped_after_approval"]:
            L.append(f"Approved but not traded: {d['skipped_after_approval']}\n")
    if r.get("llm"):
        L.append("## LLM mode\n")
        L.append(json.dumps({k: v for k, v in r["llm"].items() if k != "contamination_note"}) + "\n")
        L.append(r["llm"]["contamination_note"] + "\n")
    L.append("## Notes per strategy\n")
    for k, v in r["strategies"].items():
        if v.get("note"):
            L.append(f"- **{k}**: {v['note']}")
    L.append("\n## Limitations\n")
    L += [f"- {x}" for x in r["limitations"]]
    L.append(f"\nInstrument specs: {r.get('instrument_specs')}\n")
    L.append(f"Point-in-time: {r['point_in_time']}\n")
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------- background runs (owner API)
_TASKS: dict[int, asyncio.Task] = {}


def running(db: Any) -> Any:
    from sqlalchemy import select

    from app.models.committee import CmBacktest

    return db.scalar(select(CmBacktest).where(CmBacktest.status.in_(("queued", "running"))).limit(1))


def start(db: Any, cfg: Config, provider: Any = None, gate: Any = None) -> int:
    """Queue a backtest (one at a time) and return its id. Must be called from a running event loop."""
    from app.models.committee import CmBacktest

    row = CmBacktest(config=asdict(cfg), status="queued")
    db.add(row)
    db.commit()
    _TASKS[row.id] = asyncio.get_running_loop().create_task(_background(row.id, cfg, provider, gate))
    return row.id


async def _background(row_id: int, cfg: Config, provider: Any, gate: Any) -> None:
    import logging

    from app.db import session as dbs
    from app.models.committee import CmBacktest

    log = logging.getLogger(__name__)
    db = dbs.SessionLocal()
    try:
        row = db.get(CmBacktest, row_id)
        if row is None:
            return
        row.status = "running"
        db.commit()

        def note(done: int, total: int) -> None:
            pass  # progress is not written per decision (one row update per run keeps the database quiet)

        try:
            d = data_dir()
            ds = await asyncio.to_thread(fetch_dataset, cfg.coins, d, history.DIR, log=lambda m: log.info("committee backtest data: %s", m))
            if cfg.mode == "rules":  # CPU-bound: its own thread and event loop, so the server keeps answering
                def work() -> dict[str, Any]:
                    s2 = dbs.SessionLocal()
                    try:
                        return asyncio.run(run_backtest(cfg, ds, real_db=s2, progress=note))
                    finally:
                        s2.close()
                result = await asyncio.to_thread(work)
            else:
                result = await run_backtest(cfg, ds, real_db=db, provider=provider, gate=gate, progress=note)
            row.result, row.status = result, "done"
        except Exception as e:  # noqa: BLE001 - recorded on the row
            log.exception("committee backtest %s failed", row_id)
            row.result, row.status = {"error": f"{type(e).__name__}: {str(e)[:300]}"}, "failed"
        db.commit()
    finally:
        db.close()
        _TASKS.pop(row_id, None)


async def wait(row_id: int) -> None:
    t = _TASKS.get(row_id)
    if t is not None:
        await t


# ---------------------------------------------------------------- CLI
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.committee.backtest", description="Kiwi AI Investment Committee backtest (research only)")
    ap.add_argument("--coins", default=",".join(DEFAULT_COINS))
    ap.add_argument("--timeframe", default="4h", choices=TIMEFRAMES)
    ap.add_argument("--every", type=int, default=6)
    ap.add_argument("--mode", default="rules", choices=("rules", "llm"))
    ap.add_argument("--llm-max-decisions", type=int, default=20)
    ap.add_argument("--llm-max-usd", type=float, default=0.0, help="0 refuses paid backends")
    ap.add_argument("--start")
    ap.add_argument("--end")
    ap.add_argument("--data-dir", default=str(data_dir()))
    ap.add_argument("--seed-dir", default=str(history.DIR), help="existing history.py files to merge in")
    ap.add_argument("--no-fetch", action="store_true", help="use the stored dataset as it is")
    ap.add_argument("--out", help="write the result JSON here")
    ap.add_argument("--md", help="write the markdown tables here")
    a = ap.parse_args(argv)
    cfg = Config.parse({"coins": a.coins.split(","), "timeframe": a.timeframe, "every": a.every, "mode": a.mode,
                        "llm_max_decisions": a.llm_max_decisions, "llm_max_usd": a.llm_max_usd, "start": a.start, "end": a.end})
    ds = Dataset.load(Path(a.data_dir), cfg.coins) if a.no_fetch else fetch_dataset(cfg.coins, Path(a.data_dir), Path(a.seed_dir))
    backend = None
    provider = None
    if cfg.mode == "llm":
        from app.config.settings import get_settings
        from app.providers.factory import create_provider

        provider = create_provider(get_settings())

    def prog(done: int, total: int) -> None:
        print(f"  decisions {done}/{total}", flush=True)

    r = asyncio.run(run_backtest(cfg, ds, backend=backend, provider=provider, progress=prog))
    if a.out:
        Path(a.out).write_text(json.dumps(r, indent=1, default=str))
    md = markdown(r)
    if a.md:
        Path(a.md).write_text(md, encoding="utf-8")
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
