"""G0 Market Memory collectors. Public endpoints only, no keys:
- Hyperliquid info API: daily candles (px:<COIN>:close / :vol) for every perp with meaningful volume; funding history (fund:<COIN>).
- Coinbase Exchange public candles (cb:<PAIR>:close): code kept but OFF. Coinbase is NOT cleared (licence research 2026-11-25), so it
  never runs in production and is not in the default steps.
- alternative.me Fear & Greed, full history (fng).
- Price Chat's own tables, rolled up daily BEFORE their 35-day pruning: ts_oi_snapshots -> oi:<COIN>; ts_liquidations -> liq:<COIN>:long/short
  (store-only: the liquidation feeds aren't cleared, so no detector reads liq:*).
- breadth:* computed from the stored Hyperliquid closes.

Robustness: a weight-based rate limiter for Hyperliquid (well under its 1200/min), retries with back-off on 429/5xx/network errors, idempotent
upserts (re-running a day overwrites it), small batch commits, and only COMPLETE UTC days are stored (today's candle is still moving).
A source whose licence isn't cleared never runs in production (sources.may_collect). Every step reports; one failing source never stops
the others. Nothing here publishes anything.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.growth import sources, store
from app.models.market_memory import MmSeries

log = logging.getLogger(__name__)

HL_API = "https://api.hyperliquid.xyz/info"
CB_API = "https://api.exchange.coinbase.com"
FNG_API = "https://api.alternative.me/fng/"
UA = "PriceChat-MarketMemory/1.0"
DAY_MS = 86_400_000
# Hyperliquid's XYZ (HIP-3) perpetuals that track traditional markets, stored as px:<key>:close. Keys never contain ":" or a domain-like
# name (the X link check reads "trade.xyz" as a web address). History: GOLD since 2025-12-22, XYZ100 since 2025-10-13, SP500 since 2026-03-18.
XYZ_MARKETS = {"xyz:GOLD": "XYZ-GOLD", "xyz:SILVER": "XYZ-SILVER", "xyz:XYZ100": "XYZ-NDX", "xyz:SP500": "XYZ-SPX"}
XYZ_COIN = {v: k for k, v in XYZ_MARKETS.items()}
HL_MIN_VOLUME_USD = 250_000.0  # "meaningful volume": 24h notional at least $250k (about 100 perps in a quiet market)
HL_WEIGHT_PER_MIN = 900  # Hyperliquid allows 1200 per IP per minute; we stay well under it
COINBASE_PAIRS = ("BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD", "DOGE-USD", "ADA-USD", "AVAX-USD", "LINK-USD", "LTC-USD", "BCH-USD",
                  "DOT-USD", "UNI-USD", "AAVE-USD", "XLM-USD", "ATOM-USD", "NEAR-USD", "HBAR-USD", "SUI-USD", "SHIB-USD", "ETH-BTC")
CB_START = date(2015, 7, 20)  # Coinbase Exchange's first BTC-USD daily candles


class CollectError(Exception):
    pass


def today_utc(now: datetime | None = None) -> date:
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc).date()


def _day(ms: int | float) -> date:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).date()


def _ms(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp() * 1000)


class Limiter:
    """A sliding one-minute weight budget."""

    def __init__(self, per_minute: int, sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic) -> None:
        self.per_minute = per_minute
        self.sleep = sleep
        self.clock = clock
        self.spent: list[tuple[float, int]] = []
        self._lock = threading.Lock()

    def take(self, weight: int) -> None:
        with self._lock:
            while True:
                now = self.clock()
                self.spent = [(t, w) for t, w in self.spent if now - t < 60.0]
                if sum(w for _, w in self.spent) + weight <= self.per_minute or not self.spent:
                    self.spent.append((now, weight))
                    return
                self.sleep(max(0.05, 60.0 - (now - self.spent[0][0])))


class Http:
    """One client for every collector: retries (429 / 5xx / network) with back-off; injectable transport for tests."""

    def __init__(self, transport: httpx.BaseTransport | None = None, sleep: Callable[[float], None] = time.sleep, retries: int = 4,
                 hl_weight_per_min: int = HL_WEIGHT_PER_MIN) -> None:
        self.c = httpx.Client(timeout=30.0, transport=transport, headers={"User-Agent": UA})
        self.sleep = sleep
        self.retries = retries
        self.hl = Limiter(hl_weight_per_min, sleep=sleep)
        self.calls = 0

    def _send(self, method: str, url: str, **kw: Any) -> Any:
        delay = 2.0
        last = ""
        for attempt in range(self.retries + 1):
            try:
                self.calls += 1
                r = self.c.request(method, url, **kw)
            except httpx.HTTPError as e:
                last = type(e).__name__
            else:
                if r.status_code == 200:
                    try:
                        return r.json()
                    except ValueError as e:
                        raise CollectError(f"{url}: not JSON") from e
                if r.status_code == 404:
                    raise CollectError(f"{url}: 404")
                last = f"HTTP {r.status_code}"
                if r.status_code not in (429, 500, 502, 503, 504):
                    raise CollectError(f"{url}: {last}")
            if attempt < self.retries:
                self.sleep(delay)
                delay = min(delay * 2, 60.0)
        raise CollectError(f"{url}: {last} after {self.retries + 1} tries")

    def hl_post(self, body: dict[str, Any], weight: int = 20) -> Any:
        self.hl.take(weight)
        return self._send("POST", HL_API, json=body)

    def get(self, url: str, params: dict[str, Any] | None = None) -> Any:
        return self._send("GET", url, params=params)


# --- Hyperliquid -----------------------------------------------------------------------------------------------------------------


def hl_universe(http: Http, min_volume: float = HL_MIN_VOLUME_USD) -> list[tuple[str, float]]:
    """(coin, 24h notional USD) for listed perps with at least min_volume, most traded first."""
    out = http.hl_post({"type": "metaAndAssetCtxs"})
    if not (isinstance(out, list) and len(out) == 2):
        raise CollectError("unexpected metaAndAssetCtxs answer")
    meta, ctxs = out
    coins = []
    for u, c in zip(meta.get("universe", []), ctxs):
        if u.get("isDelisted"):
            continue
        try:
            vol = float(c.get("dayNtlVlm") or 0)
        except (TypeError, ValueError):
            vol = 0.0
        if vol >= min_volume:
            coins.append((str(u["name"]), vol))
    return sorted(coins, key=lambda x: -x[1])


def hl_daily_candles(http: Http, coin: str, start: date, end: date) -> list[tuple[date, float, float]]:
    """(day, close, USD volume) for complete UTC days in [start, end). Candles with no trades (n == 0: Hyperliquid's pre-listing prices)
    are skipped, so stored history only holds days the market actually traded."""
    if start >= end:
        return []
    days = (end - start).days
    data = http.hl_post({"type": "candleSnapshot", "req": {"coin": coin, "interval": "1d", "startTime": _ms(start), "endTime": _ms(end) - 1}},
                        weight=20 + days // 60 + 1)
    out = []
    for k in data or []:
        try:
            t, c, v, n = int(k["t"]), float(k["c"]), float(k["v"]), int(k.get("n") or 0)
        except (KeyError, TypeError, ValueError):
            continue
        d = _day(t)
        if n <= 0 or c <= 0 or d >= end or d < start:
            continue
        out.append((d, c, v * c))
    return out


def hl_funding_daily(http: Http, coin: str, start: date, end: date) -> list[tuple[date, float]]:
    """Daily mean of the hourly funding rate for complete days in [start, end), paging 500 entries per call."""
    t = _ms(start)
    stop = _ms(end)
    by_day: dict[date, list[float]] = {}
    for _ in range(400):  # a hard stop: 400 pages = 200,000 hours
        page = http.hl_post({"type": "fundingHistory", "coin": coin, "startTime": t, "endTime": stop - 1}, weight=45)
        if not page:
            break
        last = t
        for p in page:
            try:
                ts, rate = int(p["time"]), float(p["fundingRate"])
            except (KeyError, TypeError, ValueError):
                continue
            last = max(last, ts)
            if ts < stop:
                by_day.setdefault(_day(ts), []).append(rate)
        if len(page) < 500 or last + 1 >= stop or last + 1 <= t:
            break
        t = last + 1
    return [(d, sum(v) / len(v)) for d, v in sorted(by_day.items()) if d < end and len(v) >= 12]  # a day needs at least half its hours


# --- Coinbase / alternative.me ---------------------------------------------------------------------------------------------------


def cb_daily(http: Http, pair: str, start: date, end: date) -> list[tuple[date, float]]:
    """(day, close) for complete days in [start, end), 300 days per call."""
    out: dict[date, float] = {}
    s = start
    while s < end:
        e = min(end, s + timedelta(days=299))
        rows = http.get(f"{CB_API}/products/{pair}/candles", {"granularity": 86400, "start": f"{s.isoformat()}T00:00:00Z",
                                                                "end": f"{e.isoformat()}T00:00:00Z"})
        for r in rows or []:
            try:
                d, close = datetime.fromtimestamp(int(r[0]), tz=timezone.utc).date(), float(r[4])
            except (IndexError, TypeError, ValueError):
                continue
            if start <= d < end and close > 0:
                out[d] = close
        s = e + timedelta(days=1) if e > s else end
        http.sleep(0.15)  # Coinbase public: 10 requests / second; stay far below
    return sorted(out.items())


def fng_history(http: Http, limit: int = 0) -> list[tuple[date, float]]:
    data = http.get(FNG_API, {"limit": limit, "format": "json"})
    out = []
    for r in (data or {}).get("data") or []:
        try:
            out.append((_day(int(r["timestamp"]) * 1000), float(r["value"])))
        except (KeyError, TypeError, ValueError):
            continue
    return sorted(out)


# --- Price Chat's own tables (rolled up before pruning) --------------------------------------------------------------------------


def rollup_oi(db: Session, today: date, days: int = 40) -> int:
    """oi:<COIN> = the day's LAST open-interest snapshot (USD), for complete days in the last `days`."""
    from app.models.tradingos import TsOiSnapshot

    since = datetime.combine(today - timedelta(days=days), datetime.min.time(), tzinfo=timezone.utc)
    upto = datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc)
    last: dict[tuple[str, date], tuple[datetime, float]] = {}
    for at, coin, oi in db.execute(select(TsOiSnapshot.at, TsOiSnapshot.coin, TsOiSnapshot.oi_usd)
                                   .where(TsOiSnapshot.at >= since, TsOiSnapshot.at < upto)):
        at = at if at.tzinfo else at.replace(tzinfo=timezone.utc)
        k = (coin.upper(), at.date())
        if k not in last or at > last[k][0]:
            last[k] = (at, float(oi))
    per: dict[str, list[tuple[date, float]]] = {}
    for (coin, d), (_, v) in last.items():
        per.setdefault(coin, []).append((d, v))
    return sum(store.upsert(db, f"oi:{c}", pts, "derived_hl") for c, pts in per.items())


def rollup_liquidations(db: Session, today: date, days: int = 40) -> int:
    """liq:<COIN>:long / :short = liquidated USD per side per complete day."""
    from app.models.tradingos import TsLiquidation

    since, upto = _ms(today - timedelta(days=days)), _ms(today)
    sums: dict[tuple[str, str, date], float] = {}
    for coin, side, t, usd in db.execute(select(TsLiquidation.coin, TsLiquidation.side, TsLiquidation.t, TsLiquidation.usd)
                                         .where(TsLiquidation.t >= since, TsLiquidation.t < upto)):
        if side not in ("long", "short"):
            continue
        k = (coin.upper(), side, _day(t))
        sums[k] = sums.get(k, 0.0) + float(usd or 0)
    per: dict[str, list[tuple[date, float]]] = {}
    for (coin, side, d), v in sums.items():
        per.setdefault(f"liq:{coin}:{side}", []).append((d, v))
    return sum(store.upsert(db, k, pts, "liquidations") for k, pts in per.items())


# --- breadth ---------------------------------------------------------------------------------------------------------------------


def compute_breadth(closes: dict[str, list[tuple[date, float]]], since: date | None = None) -> dict[str, list[tuple[date, float]]]:
    """From px:<COIN>:close series: per day, the share (0..100) of perps above their 50-day / 200-day average, the share up over 7 days,
    and how many perps each share is out of. A coin counts for a measure only once it has the full lookback."""
    idx: dict[str, dict[date, int]] = {}
    vals: dict[str, list[float]] = {}
    days_all: set[date] = set()
    for k, pts in closes.items():
        idx[k] = {d: i for i, (d, _) in enumerate(pts)}
        vals[k] = [v for _, v in pts]
        days_all.update(d for d, _ in pts)
    out: dict[str, list[tuple[date, float]]] = {"breadth:above50": [], "breadth:above200": [], "breadth:up7d": [], "breadth:n50": [],
                                                 "breadth:n200": [], "breadth:n7": []}
    prefix: dict[str, list[float]] = {}
    for k, v in vals.items():
        acc = [0.0]
        for x in v:
            acc.append(acc[-1] + x)
        prefix[k] = acc
    for d in sorted(days_all):
        if since and d < since:
            continue
        a50 = n50 = a200 = n200 = u7 = n7 = 0
        for k in vals:
            i = idx[k].get(d)
            if i is None:
                continue
            v, p = vals[k], prefix[k]
            if i >= 49:
                n50 += 1
                a50 += v[i] > (p[i + 1] - p[i - 49]) / 50
            if i >= 199:
                n200 += 1
                a200 += v[i] > (p[i + 1] - p[i - 199]) / 200
            if i >= 7 and pts_gap_ok(closes[k], i, 7):
                n7 += 1
                u7 += v[i] > v[i - 7]
        if n50 >= 20:
            out["breadth:above50"].append((d, 100.0 * a50 / n50))
            out["breadth:n50"].append((d, float(n50)))
        if n200 >= 20:
            out["breadth:above200"].append((d, 100.0 * a200 / n200))
            out["breadth:n200"].append((d, float(n200)))
        if n7 >= 20:
            out["breadth:up7d"].append((d, 100.0 * u7 / n7))
            out["breadth:n7"].append((d, float(n7)))
    return out


def pts_gap_ok(pts: list[tuple[date, float]], i: int, n: int) -> bool:
    """The point n rows back is exactly n calendar days back (no missing days in between)."""
    return (pts[i][0] - pts[i - n][0]).days == n


# --- the orchestrator ------------------------------------------------------------------------------------------------------------


def collect(db: Session, *, full: bool = False, http: Http | None = None, settings: Any = None, now: datetime | None = None,
            funding_coins: int = 40, funding_days: int = 180, max_coins: int | None = None, progress: Callable[[str], None] | None = None,
            steps: tuple[str, ...] = ("hyperliquid", "macro", "funding", "fng", "oi", "liquidations", "breadth")) -> dict[str, Any]:
    """One collection pass. Incremental by default: each series restarts a few days before its newest stored day (or from its full history
    when it has none, so the first daily run is its own backfill). full=True re-reads full history for every series."""
    http = http or Http()
    say = progress or (lambda m: log.info("market memory: %s", m))
    end = today_utc(now)
    report: dict[str, Any] = {"day": end.isoformat(), "written": {}, "errors": [], "skipped": []}

    def allowed(source: str, step: str) -> bool:
        if sources.may_collect(source, settings):
            return True
        report["skipped"].append(f"{step}: source {source} not cleared for production")
        return False

    def start_for(series: str, default: date) -> date:
        if full:
            return default
        last = store.last_day(db, series)
        return default if last is None else max(default, last - timedelta(days=3))

    def add(step: str, n: int) -> None:
        report["written"][step] = report["written"].get(step, 0) + n

    universe: list[tuple[str, float]] = []
    if ("hyperliquid" in steps or "funding" in steps) and allowed("hyperliquid", "hyperliquid"):
        try:
            universe = hl_universe(http)
            if max_coins:
                universe = universe[:max_coins]
            report["universe"] = len(universe)
        except CollectError as e:
            report["errors"].append(f"hyperliquid universe: {e}")
    if "hyperliquid" in steps:
        for i, (coin, _) in enumerate(universe):
            try:
                st = start_for(f"px:{coin}:close", end - timedelta(days=4990))
                rows = hl_daily_candles(http, coin, st, end)
                add("hyperliquid", store.upsert(db, f"px:{coin}:close", [(d, c) for d, c, _ in rows], "hyperliquid"))
                store.upsert(db, f"px:{coin}:vol", [(d, v) for d, _, v in rows], "hyperliquid")
            except CollectError as e:
                report["errors"].append(f"candles {coin}: {e}")
            if i % 20 == 0:
                say(f"hyperliquid candles {i + 1}/{len(universe)}")
    if "macro" in steps and allowed("hyperliquid", "macro"):
        for coin, key in XYZ_MARKETS.items():  # gold, silver, the Nasdaq-100 and the S&P 500 perpetuals (Hyperliquid's XYZ markets)
            try:
                st = start_for(f"px:{key}:close", end - timedelta(days=4990))
                rows = hl_daily_candles(http, coin, st, end)
                add("macro", store.upsert(db, f"px:{key}:close", [(d, c) for d, c, _ in rows], "hyperliquid"))
            except CollectError as e:
                report["errors"].append(f"candles {coin}: {e}")
    if "funding" in steps:
        for i, (coin, _) in enumerate(universe[:funding_coins]):
            try:
                st = start_for(f"fund:{coin}", end - timedelta(days=funding_days))
                add("funding", store.upsert(db, f"fund:{coin}", hl_funding_daily(http, coin, st, end), "hyperliquid"))
            except CollectError as e:
                report["errors"].append(f"funding {coin}: {e}")
            if i % 10 == 0:
                say(f"hyperliquid funding {i + 1}/{min(funding_coins, len(universe))}")
    if "coinbase" in steps and allowed("coinbase", "coinbase"):
        for pair in COINBASE_PAIRS:
            try:
                st = start_for(f"cb:{pair}:close", CB_START)
                add("coinbase", store.upsert(db, f"cb:{pair}:close", cb_daily(http, pair, st, end), "coinbase"))
            except CollectError as e:
                report["errors"].append(f"coinbase {pair}: {e}")
        say("coinbase done")
    if "fng" in steps and allowed("alternative_me", "fng"):
        try:
            pts = [(d, v) for d, v in fng_history(http, 0 if full or store.last_day(db, "fng") is None else 10) if d < end]
            add("fng", store.upsert(db, "fng", pts, "alternative_me"))
        except CollectError as e:
            report["errors"].append(f"fng: {e}")
    if "oi" in steps and allowed("derived_hl", "oi"):
        try:
            add("oi", rollup_oi(db, end))
        except Exception as e:  # noqa: BLE001 - a rollup failing must not stop the others
            db.rollback()
            report["errors"].append(f"oi rollup: {type(e).__name__}")
    if "liquidations" in steps and allowed("liquidations", "liquidations"):
        try:
            add("liquidations", rollup_liquidations(db, end))
        except Exception as e:  # noqa: BLE001
            db.rollback()
            report["errors"].append(f"liquidation rollup: {type(e).__name__}")
    if "breadth" in steps and allowed("derived_hl", "breadth"):
        closes = {k: v for k, v in store.load_prefix(db, "px:").items() if k.endswith(":close")}
        since = None if full or store.last_day(db, "breadth:above50") is None else end - timedelta(days=10)
        for k, pts in compute_breadth(closes, since).items():
            add("breadth", store.upsert(db, k, pts, "derived_hl"))
    report["http_calls"] = http.calls
    report["rows"] = int(db.scalar(select(func.count()).select_from(MmSeries)) or 0)
    return report
