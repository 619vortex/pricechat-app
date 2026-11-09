"""Market Data Service: real Hyperliquid perpetuals data through the official read-only info API.

Markets (universe + live contexts: mark/mid/oracle price, funding, open interest, 24h volume, impact prices), candles
(1m/5m/15m/1h/4h/1d), the order book (spread, depth), and a user's positions / open orders. Short TTL caches keep
us well inside Hyperliquid's rate limits; every answer carries its fetch time and anything older than STALE_AFTER
is refused for signal work ("stale-price protection"). Never invents a number: on an API error it raises.
"""

import threading
import time
from dataclasses import dataclass
from typing import Any

import httpx

API = "https://api.hyperliquid.xyz/info"
INTERVALS = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "4h": 14400, "1d": 86400, "1w": 604800}
STALE_AFTER = 120.0  # seconds: older market data is refused for signals
TTL = {"markets": 15.0, "book": 5.0, "candles": 30.0, "mids": 2.0}


class MarketError(Exception):
    """Hyperliquid couldn't be read (or answered something unexpected). Callers show this, never a made-up number."""


@dataclass
class Market:
    coin: str
    mark: float
    mid: float | None
    oracle: float
    prev_day: float
    funding: float  # per hour (Hyperliquid's funding rate field)
    open_interest: float  # in coins
    day_volume: float  # USD notional
    max_leverage: int
    impact_bid: float | None
    impact_ask: float | None
    sz_decimals: int

    @property
    def change_24h(self) -> float:
        return (self.mark - self.prev_day) / self.prev_day * 100 if self.prev_day else 0.0

    @property
    def spread_bps(self) -> float | None:
        """Impact spread (what a typical market order would cross), in basis points of mid."""
        if self.impact_bid and self.impact_ask and self.mid:
            return (self.impact_ask - self.impact_bid) / self.mid * 10_000
        return None

    @property
    def oi_usd(self) -> float:
        return self.open_interest * self.mark


@dataclass
class Candle:
    t: int  # open time, ms
    o: float
    h: float
    l: float  # noqa: E741
    c: float
    v: float


def _f(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


class MarketData:
    def __init__(self, url: str = API, transport: httpx.BaseTransport | None = None, timeout: float = 15.0) -> None:
        self.url = url
        self._http = httpx.Client(timeout=timeout, transport=transport)
        self._cache: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def _post(self, body: dict[str, Any]) -> Any:
        try:
            r = self._http.post(self.url, json=body)
        except httpx.HTTPError as e:
            raise MarketError(f"Hyperliquid couldn't be reached ({type(e).__name__}).") from e
        if r.status_code == 429:
            raise MarketError("Hyperliquid is rate-limiting us; try again in a moment.")
        if r.status_code != 200:
            raise MarketError(f"Hyperliquid answered {r.status_code}.")
        try:
            return r.json()
        except ValueError as e:
            raise MarketError("Hyperliquid sent something that isn't JSON.") from e

    def _cached(self, key: str, ttl: float, fetch: Any) -> tuple[float, Any]:
        now = time.time()
        with self._lock:
            hit = self._cache.get(key)
            if hit and now - hit[0] < ttl:
                return hit
        val = fetch()
        with self._lock:
            self._cache[key] = (time.time(), val)
        return self._cache[key]

    # ------------------------------------------------------------------ markets
    def markets(self) -> tuple[float, dict[str, Market]]:
        """(fetched_at, coin -> Market) for every listed (not delisted) perpetual."""
        def fetch() -> dict[str, Market]:
            out = self._post({"type": "metaAndAssetCtxs"})
            if not (isinstance(out, list) and len(out) == 2):
                raise MarketError("Unexpected markets answer from Hyperliquid.")
            meta, ctxs = out
            res = {}
            for u, c in zip(meta.get("universe", []), ctxs):
                if u.get("isDelisted"):
                    continue
                imp = c.get("impactPxs") or [None, None]
                res[u["name"].upper()] = Market(
                    coin=u["name"], mark=_f(c.get("markPx")), mid=_f(c.get("midPx")) or None, oracle=_f(c.get("oraclePx")),
                    prev_day=_f(c.get("prevDayPx")), funding=_f(c.get("funding")), open_interest=_f(c.get("openInterest")),
                    day_volume=_f(c.get("dayNtlVlm")), max_leverage=int(u.get("maxLeverage") or 1),
                    impact_bid=_f(imp[0]) or None, impact_ask=_f(imp[1]) or None, sz_decimals=int(u.get("szDecimals") or 0))
            if not res:
                raise MarketError("Hyperliquid returned no markets.")
            return res
        return self._cached("markets", TTL["markets"], fetch)

    def market(self, coin: str) -> Market:
        _, ms = self.markets()
        m = ms.get(coin.upper().lstrip("$"))
        if m is None:
            raise MarketError(f"{coin.upper()} isn't a Hyperliquid perpetual.")
        return m

    def top(self, n: int = 20, min_volume: float = 0) -> list[Market]:
        _, ms = self.markets()
        return sorted((m for m in ms.values() if m.day_volume >= min_volume and m.mark > 0), key=lambda m: -m.day_volume)[:n]

    # ------------------------------------------------------------------ candles / book
    def candles(self, coin: str, interval: str, count: int = 200) -> list[Candle]:
        if interval not in INTERVALS:
            raise MarketError(f"Interval must be one of {', '.join(INTERVALS)}.")
        coin = self.market(coin).coin  # validates and normalises the name

        def fetch() -> list[Candle]:
            end = int(time.time() * 1000)
            start = end - INTERVALS[interval] * 1000 * (count + 2)
            rows = self._post({"type": "candleSnapshot", "req": {"coin": coin, "interval": interval, "startTime": start, "endTime": end}})
            if not isinstance(rows, list):
                raise MarketError("Unexpected candles answer from Hyperliquid.")
            return [Candle(int(r["t"]), _f(r["o"]), _f(r["h"]), _f(r["l"]), _f(r["c"]), _f(r["v"])) for r in rows][-count:]
        _, out = self._cached(f"candles:{coin}:{interval}:{count}", TTL["candles"], fetch)
        if not out:
            raise MarketError(f"No {interval} candles for {coin}.")
        return out

    def book(self, coin: str) -> dict[str, float]:
        """Best bid/ask, spread (bps) and USD depth within 0.5% of mid on each side."""
        coin = self.market(coin).coin

        def fetch() -> dict[str, float]:
            b = self._post({"type": "l2Book", "coin": coin})
            bids, asks = (b.get("levels") or [[], []])[:2]
            if not bids or not asks:
                raise MarketError(f"Empty order book for {coin}.")
            bb, ba = _f(bids[0]["px"]), _f(asks[0]["px"])
            mid = (bb + ba) / 2
            depth = lambda side, lo, hi: sum(_f(x["px"]) * _f(x["sz"]) for x in side if lo <= _f(x["px"]) <= hi)  # noqa: E731
            return {"bid": bb, "ask": ba, "mid": mid, "spread_bps": (ba - bb) / mid * 10_000,
                    "bid_depth_usd": depth(bids, mid * 0.995, mid), "ask_depth_usd": depth(asks, mid, mid * 1.005)}
        return self._cached(f"book:{coin}", TTL["book"], fetch)[1]

    def levels(self, coin: str, sig_figs: int = 5) -> dict[str, list[tuple[float, float]]]:
        """The order book's price levels (bids high→low, asks low→high) as (price, size in coins), for depth charts.
        Fewer significant figures = wider buckets = a deeper view of the book."""
        coin = self.market(coin).coin

        def fetch() -> dict[str, list[tuple[float, float]]]:
            b = self._post({"type": "l2Book", "coin": coin, "nSigFigs": sig_figs})
            bids, asks = (b.get("levels") or [[], []])[:2]
            if not bids or not asks:
                raise MarketError(f"Empty order book for {coin}.")
            return {"bids": [(_f(x["px"]), _f(x["sz"])) for x in bids], "asks": [(_f(x["px"]), _f(x["sz"])) for x in asks]}
        return self._cached(f"levels:{coin}:{sig_figs}", TTL["book"], fetch)[1]

    def funding_history(self, coin: str, hours: int = 168) -> list[tuple[int, float]]:
        """Hourly funding rates (ms, rate per hour) over the last `hours` hours, oldest first. Hyperliquid keeps funding
        history; it has no open-interest history (only the current value, in markets())."""
        coin = self.market(coin).coin

        def fetch() -> list[tuple[int, float]]:
            start = int(time.time() * 1000) - hours * 3_600_000
            rows = self._post({"type": "fundingHistory", "coin": coin, "startTime": start})
            if not isinstance(rows, list):
                raise MarketError("Unexpected funding answer from Hyperliquid.")
            return [(int(r["time"]), _f(r["fundingRate"])) for r in rows]
        return self._cached(f"funding:{coin}:{hours}", 300.0, fetch)[1]

    def mids(self) -> tuple[float, dict[str, float]]:
        """(fetched_at, coin -> mid price) for every market: light enough to refresh every couple of seconds."""
        def fetch() -> dict[str, float]:
            out = self._post({"type": "allMids"})
            if not isinstance(out, dict):
                raise MarketError("Unexpected prices answer from Hyperliquid.")
            return {k.upper(): _f(v) for k, v in out.items() if not k.startswith("@")}
        return self._cached("mids", TTL["mids"], fetch)

    def fresh(self) -> bool:
        """Is the cached market snapshot recent enough to act on?"""
        hit = self._cache.get("markets")
        return bool(hit) and time.time() - hit[0] < STALE_AFTER

    # ------------------------------------------------------------------ a user's account (read-only)
    def positions(self, address: str) -> dict[str, Any]:
        out = self._post({"type": "clearinghouseState", "user": address})
        if not isinstance(out, dict):
            raise MarketError("Unexpected account answer from Hyperliquid.")
        pos = []
        for p in out.get("assetPositions", []):
            q = p.get("position") or {}
            size = _f(q.get("szi"))
            if size == 0:
                continue
            pos.append({"coin": q.get("coin"), "size": size, "side": "LONG" if size > 0 else "SHORT", "entry": _f(q.get("entryPx")),
                        "value": _f(q.get("positionValue")), "pnl": _f(q.get("unrealizedPnl")), "liq": _f(q.get("liquidationPx")) or None,
                        "leverage": (q.get("leverage") or {}).get("value")})
        ms = out.get("marginSummary") or {}
        return {"equity": _f(ms.get("accountValue")), "margin_used": _f(ms.get("totalMarginUsed")), "positions": pos}


_shared: MarketData | None = None


def data() -> MarketData:
    """The process-wide market data client (shared cache)."""
    global _shared
    if _shared is None:
        _shared = MarketData()
    return _shared


TESTNET_API = "https://api.hyperliquid-testnet.xyz/info"
_testnet: MarketData | None = None


def testnet() -> MarketData:
    """Hyperliquid testnet's own markets: its prices, szDecimals, max leverage and listings all differ from mainnet's."""
    global _testnet
    if _testnet is None:
        _testnet = MarketData(TESTNET_API)
    return _testnet


def for_mode(mode: str | None) -> MarketData:
    """The market an account's orders really trade on: testnet for TESTNET; mainnet for LIVE and PAPER (paper simulates on
    real prices)."""
    return testnet() if mode == "TESTNET" else data()


def fmt_price(p: float) -> str:
    """Prices the way traders read them: more decimals for small prices."""
    if p >= 1000:
        return f"{p:,.1f}"
    if p >= 1:
        return f"{p:,.3f}".rstrip("0").rstrip(".") if p < 100 else f"{p:,.2f}"
    if p <= 0:
        return "0"
    import math

    decimals = min(12, max(6, -math.floor(math.log10(p)) + 3))  # at least 4 significant digits: 0.000005234, not 0.000005
    return f"{p:.{decimals}f}".rstrip("0").rstrip(".")
