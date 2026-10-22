"""Market structure: what order books, derivatives positioning and trade flow show, from public exchange APIs only.

Venues (public, read-only, no keys): Hyperliquid (app/tradingos/market.py), Binance USDⓈ-M futures, Bybit linear, OKX
swaps, Deribit (BTC / ETH perpetual open interest). Each venue is optional: when one can't be reached (rate limit, region
block, a coin it doesn't list) it drops out and the result says so. Nothing is ever filled in.

Every result says what kind of number it is (`basis`):
  observed   - read directly from one exchange's feed (book levels, a candle's taker volume, a reported liquidation)
  aggregated - observed numbers from several exchanges added together (the venues are listed)
  estimate   - the output of a model (estimated liquidation zones): never a confirmed position
  inferred   - a pattern found in observed trades (TWAP-like slicing): never proof of who traded, or that one trader did

What isn't available, and why (shown to members instead of a made-up chart):
  - Binance and Bybit publish liquidations only on websockets, Hyperliquid not at all: reported liquidations are OKX's.
  - Hyperliquid publishes no open-interest history (Price Chat's own 15-minute record is in oi.py).
  - Public order books are shallow (Hyperliquid 20 levels a side, Binance futures 1000, OKX 5000): each book result
    carries the price range each venue covers.
"""

import logging
import math
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.tradingos.market import INTERVALS, MarketData, MarketError, data as hl_data

logger = logging.getLogger(__name__)
_warned: set[str] = set()
_status_logged = 0.0  # venue problems already logged by the recorder (once per process)
BINANCE = "https://fapi.binance.com"
BYBIT = "https://api.bybit.com"
OKX = "https://www.okx.com"
DERIBIT = "https://www.deribit.com"
VENUES = ("binance", "bybit", "okx", "hyperliquid", "deribit")
NAME = {"binance": "Binance", "bybit": "Bybit", "okx": "OKX", "hyperliquid": "Hyperliquid", "deribit": "Deribit"}
BASIS = {"observed": "Observed", "aggregated": "Aggregated", "estimate": "Model estimate", "inferred": "Inferred pattern"}

# exchange-published history intervals for each chart timeframe (the nearest one the venue has, never finer than asked)
OI_PERIOD = {"binance": {"1m": "5m", "5m": "5m", "15m": "15m", "30m": "30m", "1h": "1h", "4h": "4h", "1d": "1d"},
             "bybit": {"1m": "5min", "5m": "5min", "15m": "15min", "30m": "30min", "1h": "1h", "4h": "4h", "1d": "1d"},
             "okx": {"1m": "5m", "5m": "5m", "15m": "15m", "30m": "30m", "1h": "1H", "4h": "4H", "1d": "1D"}}
PERIOD_MS = {"5m": 300_000, "15m": 900_000, "30m": 1_800_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}
OKX_TAKER = {"5m": "5m", "15m": "15m", "30m": "30m", "1h": "1H", "4h": "4H", "1d": "1D"}  # no 1m

# estimated liquidation zones: the model's assumptions, printed on every chart that shows them
LEVERAGE_MIX = ((5, 0.20), (10, 0.30), (25, 0.25), (50, 0.15), (100, 0.10))
MAINT_MARGIN = 0.005
TWAP_MIN_PRINTS = 8
TWAP_MAX_CV = 0.15


class StructureError(Exception):
    """No venue could answer (the message says why)."""


def _f(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def symbols(coin: str) -> dict[str, str]:
    """A Hyperliquid coin name -> each venue's linear perpetual. Hyperliquid's "k" coins are per 1000 (kPEPE = 1000 PEPE)."""
    c = coin.upper().lstrip("$")
    base, thousand = (c[1:], True) if coin.startswith("k") and len(coin) > 1 else (c, False)
    b = f"1000{base}USDT" if thousand else f"{base}USDT"
    out = {"binance": b, "bybit": b, "okx": f"{base}-USDT-SWAP", "hyperliquid": coin}
    if base in ("BTC", "ETH"):
        out["deribit"] = f"{base}-PERPETUAL"
    return out


def _stamp(ms: float) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


@dataclass
class Venues:
    """The non-Hyperliquid REST clients with a short cache (all public endpoints, well inside their rate limits)."""

    transport: httpx.BaseTransport | None = None
    timeout: float = 8.0

    def __post_init__(self) -> None:
        self._http = httpx.Client(timeout=self.timeout, transport=self.transport, headers={"User-Agent": "PriceChat/1.0"})
        self._cache: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get(self, url: str, params: dict[str, Any], ttl: float = 20.0) -> Any:
        key = url + "?" + "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        with self._lock:
            hit = self._cache.get(key)
            if hit and time.time() - hit[0] < ttl:
                return hit[1]
        try:
            r = self._http.get(url, params=params)
        except httpx.HTTPError as e:
            raise StructureError(f"couldn't be reached ({type(e).__name__})") from e
        if r.status_code in (403, 451):
            raise StructureError("blocks requests from this server's region")
        if r.status_code == 429:
            raise StructureError("is rate-limiting us")
        if r.status_code != 200:
            raise StructureError(f"answered {r.status_code}")
        try:
            out = r.json()
        except ValueError as e:
            raise StructureError("sent something that isn't JSON") from e
        if isinstance(out, dict) and out.get("retCode") not in (None, 0):  # Bybit
            raise StructureError(f"said: {out.get('retMsg') or out.get('retCode')}")
        if isinstance(out, dict) and str(out.get("code", "0")) not in ("0", "") and "data" in out:  # OKX
            raise StructureError(f"said: {out.get('msg') or out.get('code')}")
        with self._lock:
            if len(self._cache) > 500:
                self._cache.clear()
            self._cache[key] = (time.time(), out)
        return out

    # ------------------------------------------------------------------ open interest
    def oi_now(self, venue: str, sym: str) -> float:
        """Current open interest in USD."""
        if venue == "binance":
            q = _f(self.get(f"{BINANCE}/fapi/v1/openInterest", {"symbol": sym}, 15)["openInterest"])
            return q * _f(self.get(f"{BINANCE}/fapi/v1/premiumIndex", {"symbol": sym}, 15)["markPrice"])
        if venue == "bybit":
            row = self.get(f"{BYBIT}/v5/market/tickers", {"category": "linear", "symbol": sym}, 15)["result"]["list"][0]
            return _f(row["openInterestValue"])
        if venue == "okx":
            return _f(self.get(f"{OKX}/api/v5/public/open-interest", {"instType": "SWAP", "instId": sym}, 15)["data"][0]["oiUsd"])
        if venue == "deribit":  # inverse perpetual: open interest is in USD
            return _f(self.get(f"{DERIBIT}/api/v2/public/get_book_summary_by_instrument", {"instrument_name": sym}, 15)["result"][0]["open_interest"])
        raise StructureError("has no open-interest feed here")

    def oi_history(self, venue: str, sym: str, tf: str, count: int) -> list[tuple[int, float]]:
        """(ms, USD) oldest first, as the exchange publishes it."""
        period = OI_PERIOD[venue][tf]
        if venue == "binance":
            rows = self.get(f"{BINANCE}/futures/data/openInterestHist", {"symbol": sym, "period": period, "limit": min(500, count)}, 60)
            return [(int(r["timestamp"]), _f(r["sumOpenInterestValue"])) for r in rows]
        if venue == "bybit":  # in coins: valued at Bybit's own close for the same interval
            oi = self.get(f"{BYBIT}/v5/market/open-interest", {"category": "linear", "symbol": sym, "intervalTime": period, "limit": min(200, count)}, 60)
            kl = self.get(f"{BYBIT}/v5/market/kline", {"category": "linear", "symbol": sym, "interval": _bybit_kline(period), "limit": min(1000, count + 5)}, 60)
            close = {int(k[0]): _f(k[4]) for k in kl["result"]["list"]}
            out = [(int(r["timestamp"]), _f(r["openInterest"]) * close[int(r["timestamp"])]) for r in oi["result"]["list"] if int(r["timestamp"]) in close]
            return sorted(out)
        if venue == "okx":  # 100 rows a page: page back with `end`
            rows: list[list[str]] = []
            end = None
            while len(rows) < count:
                p = {"instId": sym, "period": period, "limit": 100, **({"end": end} if end else {})}
                page = self.get(f"{OKX}/api/v5/rubik/stat/contracts/open-interest-history", p, 60)["data"]
                if not page:
                    break
                rows += page
                end = int(page[-1][0]) - 1
                if len(page) < 100:
                    break
            return sorted({int(r[0]): _f(r[3]) for r in rows}.items())[-count:]
        raise StructureError("publishes no open-interest history")

    def oi_history_okx_coins(self, sym: str, bar: str, count: int) -> list[tuple[int, float]]:
        """OKX open-interest snapshots in coins (oiCcy), oldest first."""
        rows: list[list[str]] = []
        end = None
        while len(rows) < count:
            p = {"instId": sym, "period": bar, "limit": 100, **({"end": end} if end else {})}
            page = self.get(f"{OKX}/api/v5/rubik/stat/contracts/open-interest-history", p, 60)["data"]
            if not page:
                break
            rows += page
            end = int(page[-1][0]) - 1
            if len(page) < 100:
                break
        return sorted({int(r[0]): _f(r[2]) for r in rows}.items())[-count:]

    # ------------------------------------------------------------------ taker flow (CVD)
    def taker_flow(self, venue: str, sym: str, tf: str, count: int) -> list[tuple[int, float, float]]:
        """(ms, taker buy USD, taker sell USD) per interval, oldest first."""
        if venue == "binance":
            rows = self.get(f"{BINANCE}/fapi/v1/klines", {"symbol": sym, "interval": tf, "limit": min(1500, count)}, 20)
            return [(int(k[0]), _f(k[10]), _f(k[7]) - _f(k[10])) for k in rows]  # quote volume, taker buy quote volume
        if venue == "okx":
            if tf not in OKX_TAKER:
                raise StructureError(f"has no {tf} taker volume (5m and up)")
            rows, end = [], None
            while len(rows) < count:
                p = {"instId": sym, "period": OKX_TAKER[tf], "unit": 2, "limit": 100, **({"end": end} if end else {})}
                page = self.get(f"{OKX}/api/v5/rubik/stat/taker-volume-contract", p, 30)["data"]
                if not page:
                    break
                rows += page
                end = int(page[-1][0]) - 1
                if len(page) < 100:
                    break
            return sorted((int(r[0]), _f(r[2]), _f(r[1])) for r in {r[0]: r for r in rows}.values())[-count:]
        raise StructureError("publishes no taker volume over REST")

    # ------------------------------------------------------------------ books
    def book(self, venue: str, sym: str) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
        """(bids, asks) as (price, size in coins of the venue's own contract), best first."""
        if venue == "binance":
            b = self.get(f"{BINANCE}/fapi/v1/depth", {"symbol": sym, "limit": 1000}, 4)
            return [(_f(p), _f(q)) for p, q in b["bids"]], [(_f(p), _f(q)) for p, q in b["asks"]]
        if venue == "bybit":
            b = self.get(f"{BYBIT}/v5/market/orderbook", {"category": "linear", "symbol": sym, "limit": 500}, 4)["result"]
            return [(_f(p), _f(q)) for p, q in b["b"]], [(_f(p), _f(q)) for p, q in b["a"]]
        if venue == "okx":
            ct = self.okx_ct_val(sym)
            b = self.get(f"{OKX}/api/v5/market/books-full", {"instId": sym, "sz": 5000}, 4)["data"][0]
            return [(_f(r[0]), _f(r[1]) * ct) for r in b["bids"]], [(_f(r[0]), _f(r[1]) * ct) for r in b["asks"]]
        raise StructureError("has no book feed here")

    def okx_ct_val(self, sym: str) -> float:
        row = self.get(f"{OKX}/api/v5/public/instruments", {"instType": "SWAP", "instId": sym}, 86_400)["data"][0]
        return _f(row["ctVal"]) or 1.0

    # ------------------------------------------------------------------ liquidations / trades
    def okx_liquidations(self, sym: str) -> list[dict[str, Any]]:
        """OKX's most recent filled liquidation orders (its public API keeps the last ~100)."""
        uly = sym.removesuffix("-SWAP")
        ct = self.okx_ct_val(sym)
        rows = self.get(f"{OKX}/api/v5/public/liquidation-orders", {"instType": "SWAP", "uly": uly, "state": "filled"}, 30)["data"]
        out = []
        for d in (rows[0].get("details") if rows else []) or []:
            px, qty = _f(d.get("bkPx")), _f(d.get("sz")) * ct
            if px > 0 and qty > 0 and d.get("posSide") in ("long", "short"):
                out.append({"venue": "okx", "t": int(d["ts"]), "side": d["posSide"], "px": px, "qty": qty, "usd": px * qty})
        return sorted(out, key=lambda r: r["t"])

    def binance_trades(self, sym: str, minutes: int) -> list[tuple[int, float, float, str]]:
        """Aggregated trades of the last `minutes` (ms, price, qty, aggressor side), oldest first."""
        end = int(time.time() * 1000)
        start = end - minutes * 60_000
        out: list[tuple[int, float, float, str]] = []
        frm = start
        for _ in range(40):
            rows = self.get(f"{BINANCE}/fapi/v1/aggTrades", {"symbol": sym, "startTime": frm, "endTime": min(end, frm + 3_599_000), "limit": 1000}, 60)
            if not rows:
                if frm + 3_599_000 >= end:
                    break
                frm += 3_599_000
                continue
            out += [(int(r["T"]), _f(r["p"]), _f(r["q"]), "sell" if r["m"] else "buy") for r in rows]
            frm = int(rows[-1]["T"]) + 1
            if len(rows) < 1000 and frm + 3_599_000 >= end:
                break
        return out


def _bybit_kline(period: str) -> str:
    return {"5min": "5", "15min": "15", "30min": "30", "1h": "60", "4h": "240", "1d": "D"}[period]


_venues: Venues | None = None


def venues() -> Venues:
    global _venues
    if _venues is None:
        _venues = Venues()
    return _venues


def _result(kind: str, basis: str, title: str, unit: str, used: list[str], skipped: dict[str, str], **extra: Any) -> dict[str, Any]:
    return {"kind": kind, "basis": basis, "basis_label": BASIS[basis], "title": title, "unit": unit,
            "venues": [NAME[v] for v in used], "skipped": {NAME[v]: why for v, why in skipped.items()},
            "fetched_at": int(time.time() * 1000), **extra}


# ---------------------------------------------------------------------------------------------------------- open interest
def open_interest(coin: str, tf: str = "1h", count: int = 120, vs: Venues | None = None, md: MarketData | None = None) -> dict[str, Any]:
    """Aggregated open interest: the current total across every venue that answers (incl. Hyperliquid and, for BTC / ETH,
    Deribit), and the history the exchanges publish themselves (Binance + Bybit + OKX), summed only at timestamps every
    included venue reported."""
    vs, md = vs or venues(), md or hl_data()
    sy = symbols(coin)
    now: dict[str, float] = {}
    skipped: dict[str, str] = {}
    for v in ("binance", "bybit", "okx", "deribit"):
        if v not in sy:
            continue
        try:
            now[v] = vs.oi_now(v, sy[v])
        except (StructureError, KeyError, IndexError, TypeError) as e:
            skipped[v] = str(e) if isinstance(e, StructureError) else "doesn't list this market"
    try:
        now["hyperliquid"] = md.market(coin).oi_usd
    except MarketError as e:
        skipped["hyperliquid"] = str(e)
    per: dict[str, list[tuple[int, float]]] = {}
    hskip: dict[str, str] = {}
    for v in ("binance", "bybit", "okx"):
        if v in skipped:
            continue
        try:
            h = vs.oi_history(v, sy[v], tf if tf in OI_PERIOD[v] else "1h", count)
            if len(h) >= 3:
                per[v] = h
        except (StructureError, KeyError, IndexError, TypeError) as e:
            hskip[v] = str(e) if isinstance(e, StructureError) else "unexpected answer"
    if not now:
        raise StructureError("No exchange answered with open interest: " + "; ".join(f"{NAME[v]} {w}" for v, w in skipped.items()))
    agg: list[tuple[int, float]] = []
    if per:
        common = set.intersection(*(set(t for t, _ in h) for h in per.values()))
        agg = sorted((t, sum(dict(h)[t] for h in per.values())) for t in common)
    notes = []
    if "hyperliquid" in now:
        notes.append("Hyperliquid publishes no OI history: it is in the current total only")
    if "deribit" in now:
        notes.append("Deribit (inverse perpetual) is in the current total only")
    return _result("open_interest", "aggregated" if len(now) > 1 else "observed", f"{coin} open interest", "USD", list(now), skipped,
                   total_usd=sum(now.values()), by_venue={NAME[v]: u for v, u in now.items()},
                   history=agg, history_venues=[NAME[v] for v in per], history_by_venue={NAME[v]: h for v, h in per.items()},
                   history_skipped={NAME[v]: w for v, w in hskip.items()}, period=OI_PERIOD["binance"].get(tf, "1h"), notes=notes)


# ---------------------------------------------------------------------------------------------------------- CVD
def cvd(coin: str, tf: str = "1h", count: int = 120, vs: Venues | None = None) -> dict[str, Any]:
    """Cumulative volume delta in USD: taker buys minus taker sells, interval by interval, summed from the start of the
    window. From Binance USDⓈ-M klines (taker buy quote volume) + OKX's taker volume, perpetuals only."""
    vs = vs or venues()
    sy = symbols(coin)
    per: dict[str, list[tuple[int, float, float]]] = {}
    skipped: dict[str, str] = {}
    for v in ("binance", "okx"):
        try:
            rows = vs.taker_flow(v, sy[v], tf, count)
            if len(rows) >= 3:
                per[v] = rows
        except (StructureError, KeyError, IndexError, TypeError) as e:
            skipped[v] = str(e) if isinstance(e, StructureError) else "doesn't list this market"
    if not per:
        raise StructureError("No taker-volume feed answered: " + "; ".join(f"{NAME[v]} {w}" for v, w in skipped.items()))
    common = sorted(set.intersection(*(set(r[0] for r in rows) for rows in per.values())))[-count:]
    run, pts, bars = 0.0, [], []
    for t in common:
        buy = sum(dict((r[0], r[1]) for r in rows)[t] for rows in per.values())
        sell = sum(dict((r[0], r[2]) for r in rows)[t] for rows in per.values())
        run += buy - sell
        pts.append((t, run))
        bars.append((t, buy, sell))
    buys, sells = sum(b for _, b, _ in bars), sum(s for _, _, s in bars)
    return _result("cvd", "aggregated" if len(per) > 1 else "observed", f"{coin} CVD (perps)", "USD", list(per), skipped,
                   points=pts, flow=bars, buy_usd=buys, sell_usd=sells, delta_usd=buys - sells, tf=tf,
                   start=common[0] if common else None, end=min(common[-1] + INTERVALS.get(tf, 3600) * 1000, int(time.time() * 1000)) if common else None,
                   notes=["CVD = taker buy volume minus taker sell volume, cumulated over the window (it starts at 0 at the left edge)"])


# ---------------------------------------------------------------------------------------------------------- order book
def nice_step(price: float, frac: float = 0.0005) -> float:
    raw = max(price * frac, 1e-12)
    e = 10 ** math.floor(math.log10(raw))
    return next(m * e for m in (1, 2, 2.5, 5, 10) if m * e >= raw)


def _bins(side: list[tuple[float, float]], step: float) -> dict[float, float]:
    out: dict[float, float] = {}
    for px, qty in side:
        b = round(math.floor(px / step) * step, 10)
        out[b] = out.get(b, 0.0) + px * qty
    return out


def walls_of(raw: dict[str, tuple[list[tuple[float, float]], list[tuple[float, float]]]], mid: float, top: int = 3,
             x_median: float = 10.0) -> list[dict[str, Any]]:
    """Liquidity walls: single resting price levels at least `x_median` times the venue's median level (past its first 3
    levels, where size always sits), the biggest few a side. Observed: each is one venue's level, as published."""
    out = []
    for v, (bids, asks) in raw.items():
        sizes = sorted(p * q for p, q in bids + asks)
        med = sizes[len(sizes) // 2] if sizes else 0.0
        for side, levels in (("bid", bids), ("ask", asks)):
            for px, q in sorted(levels[3:], key=lambda l: -l[0] * l[1])[:top]:
                usd = px * q
                if med and usd >= x_median * med:
                    out.append({"venue": NAME[v], "side": side, "px": px, "usd": usd, "x_median": round(usd / med, 1),
                                "dist_pct": round((px - mid) / mid * 100, 3)})
    return sorted(out, key=lambda w: -w["usd"])[:8]


def books(coin: str, vs: Venues | None = None, md: MarketData | None = None, venues_wanted: tuple[str, ...] = ("binance", "bybit", "okx", "hyperliquid"),
          step_frac: float = 0.0005) -> dict[str, Any]:
    """Every venue's book binned onto one price grid (USD per bin), the range each venue's public book covers, depth
    imbalance inside the range every venue covers, and the biggest resting orders ("walls") with where they sit."""
    vs, md = vs or venues(), md or hl_data()
    sy = symbols(coin)
    raw: dict[str, tuple[list[tuple[float, float]], list[tuple[float, float]]]] = {}
    skipped: dict[str, str] = {}
    for v in venues_wanted:
        try:
            if v == "hyperliquid":
                lv = md.levels(coin, 4)
                raw[v] = (lv["bids"], lv["asks"])
            else:
                raw[v] = vs.book(v, sy[v])
        except (StructureError, MarketError, KeyError, IndexError, TypeError) as e:
            skipped[v] = str(e) if isinstance(e, (StructureError, MarketError)) else "doesn't list this market"
    raw = {v: s for v, s in raw.items() if s[0] and s[1]}
    if not raw:
        raise StructureError("No order book answered: " + "; ".join(f"{NAME[v]} {w}" for v, w in skipped.items()))
    mids = sorted((s[0][0][0] + s[1][0][0]) / 2 for s in raw.values())
    mid = mids[len(mids) // 2]
    step = nice_step(mid, step_frac)
    venues_out: dict[str, Any] = {}
    for v, (bids, asks) in raw.items():
        lo, hi = bids[-1][0], asks[-1][0]
        merged = _bins(bids, step)
        for k, u in _bins(asks, step).items():
            merged[k] = merged.get(k, 0.0) + u
        venues_out[v] = {"bins": sorted(merged.items()),
                         "cover": [lo, hi], "cover_pct": [round((mid - lo) / mid * 100, 2), round((hi - mid) / mid * 100, 2)],
                         "bid_usd": sum(p * q for p, q in bids), "ask_usd": sum(p * q for p, q in asks)}
    common = (max(x["cover"][0] for x in venues_out.values()), min(x["cover"][1] for x in venues_out.values()))
    reach = min(mid - common[0], common[1] - mid)  # symmetric window every venue covers

    def depth(win: float) -> tuple[float, float]:
        bid = sum(p * q for s in raw.values() for p, q in s[0] if p >= mid - win)
        ask = sum(p * q for s in raw.values() for p, q in s[1] if p <= mid + win)
        return bid, ask

    imb = {}
    windows = [p for p in (0.1, 0.5, 1.0, 2.0) if mid * p / 100 <= reach] or [round(reach / mid * 100, 3)]
    for pct in windows:
        b, a = depth(mid * pct / 100)
        imb[f"{pct:g}%"] = {"bid_usd": b, "ask_usd": a, "imbalance": (b - a) / (b + a) if b + a else 0.0}
    agg: dict[float, dict[str, float]] = {}
    for v, x in venues_out.items():
        for px, usd in x["bins"]:
            agg.setdefault(px, {})[v] = usd
    rows = [(px, sum(by.values()), by) for px, by in agg.items()]
    walls = walls_of(raw, mid)
    notes = [f"{NAME[v]} book covers {x['cover_pct'][0]}% below to {x['cover_pct'][1]}% above mid" for v, x in venues_out.items()]
    notes.append("Imbalance is measured only inside the range every venue's book covers")
    return _result("book", "aggregated" if len(raw) > 1 else "observed", f"{coin} order book", "USD", list(raw), skipped,
                   mid=mid, step=step, by_venue={NAME[v]: x for v, x in venues_out.items()}, bins=sorted((px, u) for px, u, _ in rows),
                   imbalance=imb, walls=sorted(walls, key=lambda w: -w["usd"]), common_pct=round(reach / mid * 100, 3), notes=notes)


# ---------------------------------------------------------------------------------------------------------- liquidations
LIQ_STREAMS = ("okx", "binance", "bybit")


def liq_coin(coin: str) -> str:
    """The coin key liquidations are stored under: the venues' base symbol (kPEPE -> PEPE)."""
    from app.tradingos.liqtape import base_of

    return base_of(symbols(coin)["okx"])


def reported_liquidations(coin: str, since_ms: int, db: Any = None, vs: Venues | None = None,
                          streams: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """Liquidations the exchanges reported: Price Chat's record of the OKX, Binance and Bybit liquidation streams (and of
    OKX's public REST list), plus OKX's REST list right now. A venue counts only while its stream is connected or when it
    has records in the window; otherwise it is named as missing, with the reason."""
    from app.tradingos import liqtape

    vs = vs or venues()
    sy = symbols(coin)
    streams = streams if streams is not None else liqtape.tape().status()
    rows: dict[tuple[Any, ...], dict[str, Any]] = {}
    skipped: dict[str, str] = {}
    if db is not None:
        from sqlalchemy import select

        from app.models.tradingos import TsLiquidation

        for r in db.scalars(select(TsLiquidation).where(TsLiquidation.coin.in_({liq_coin(coin), coin.upper()}), TsLiquidation.t >= since_ms)):
            rows[(r.venue, r.t, r.side, r.px, r.qty)] = {"venue": r.venue, "t": r.t, "side": r.side, "px": r.px, "qty": r.qty, "usd": r.usd}
    okx_rest = True
    try:
        for r in vs.okx_liquidations(sy["okx"]):
            if r["t"] >= since_ms:
                rows[(r["venue"], r["t"], r["side"], r["px"], r["qty"])] = r
    except (StructureError, KeyError, IndexError, TypeError) as e:
        okx_rest = False
        skipped["okx"] = str(e) if isinstance(e, StructureError) else "doesn't list this market"
    out = sorted(rows.values(), key=lambda r: r["t"])
    have = {r["venue"] for r in out}
    used = [v for v in LIQ_STREAMS if v in have or (streams.get(v) or {}).get("connected") or (v == "okx" and okx_rest)]
    for v in LIQ_STREAMS:
        if v not in used and v not in skipped:
            err = (streams.get(v) or {}).get("error")
            skipped[v] = "liquidation stream not connected" + (f" ({err})" if err else "")
    skipped["hyperliquid"] = "doesn't publish market-wide liquidations"
    for v in used:
        skipped.pop(v, None)
    longs = sum(r["usd"] for r in out if r["side"] == "long")
    shorts = sum(r["usd"] for r in out if r["side"] == "short")
    by_venue = {NAME[v]: {"long_usd": sum(r["usd"] for r in out if r["venue"] == v and r["side"] == "long"),
                          "short_usd": sum(r["usd"] for r in out if r["venue"] == v and r["side"] == "short")} for v in used}
    first = out[0]["t"] if out else None
    notes = [f"Reported by {' + '.join(NAME[v] for v in used) or 'no exchange'}; other exchanges' liquidations are not included"]
    if "binance" in used:
        notes.append("Binance's stream shows at most one liquidation per market per second, so its total is a lower bound")
    if first and first > since_ms + 600_000:
        notes.append(f"Price Chat's record starts {_stamp(first)}")
    return _result("liquidations", "aggregated" if len(used) > 1 else "observed", f"{coin} reported liquidations", "USD", used, skipped,
                   events=out, long_usd=longs, short_usd=shorts, by_venue=by_venue, count=len(out), first=first,
                   last=out[-1]["t"] if out else None, notes=notes)


def _zone_rows(venue: str, vs: Venues, sym: str, period: str, count: int) -> list[tuple[int, float, float, float, float, float]]:
    """(interval end ms, open interest in coins, high, low, close, taker buy share) per interval, oldest first."""
    if venue == "binance":
        oi = vs.get(f"{BINANCE}/futures/data/openInterestHist", {"symbol": sym, "period": period, "limit": min(500, count)}, 60)
        kl = vs.get(f"{BINANCE}/fapi/v1/klines", {"symbol": sym, "interval": period, "limit": min(500, count + 2)}, 30)
        k_by = {int(k[0]) + PERIOD_MS[period]: k for k in kl}  # OI timestamps are the interval's end
        out = []
        for r in oi:
            k = k_by.get(int(r["timestamp"]))
            if k is not None:
                out.append((int(r["timestamp"]), _f(r["sumOpenInterest"]), _f(k[2]), _f(k[3]), _f(k[4]), _f(k[9]) / _f(k[5]) if _f(k[5]) else 0.5))
        return out
    if venue == "okx":  # an OI reading is a snapshot: pair it with the candle (and taker volume) that closed at that moment
        bar = OI_PERIOD["okx"][period]
        oi = vs.oi_history_okx_coins(sym, bar, count)
        kl = vs.get(f"{OKX}/api/v5/market/candles", {"instId": sym, "bar": bar, "limit": min(300, count + 2)}, 30)["data"]
        flow = {t: (b, s) for t, b, s in vs.taker_flow("okx", sym, period, count)}
        k_by = {int(k[0]): k for k in kl}
        out = []
        for t, q in oi:
            k, fl = k_by.get(t - PERIOD_MS[period]), flow.get(t - PERIOD_MS[period])
            if k is None:
                continue
            share = fl[0] / (fl[0] + fl[1]) if fl and fl[0] + fl[1] else 0.5
            out.append((t, q, _f(k[2]), _f(k[3]), _f(k[4]), share))
        return out
    raise StructureError("has no data for the zone model")


def liquidation_zones(coin: str, tf: str = "1h", count: int = 200, vs: Venues | None = None) -> dict[str, Any]:
    """ESTIMATED liquidation zones: a model, not positions. Each interval where open interest rose (Binance, or OKX when
    Binance can't be reached) is treated as new positions opened at that interval's close, split long/short by the
    interval's taker buy share, spread over a fixed leverage mix; a zone is removed once price trades through it, and zones
    shrink when open interest falls."""
    vs = vs or venues()
    sy = symbols(coin)
    period = OI_PERIOD["binance"].get(tf, "1h")
    rows: list[tuple[int, float, float, float, float, float]] = []
    skipped: dict[str, str] = {}
    venue = ""
    for v in ("binance", "okx"):
        try:
            rows = _zone_rows(v, vs, sy[v], period, count)
            if len(rows) >= 3:
                venue = v
                break
            skipped[v] = "not enough history"
        except (StructureError, KeyError, IndexError, TypeError) as e:
            skipped[v] = str(e) if isinstance(e, StructureError) else "doesn't list this market"
    if not venue:
        raise StructureError("No open-interest history for the zone model: " + "; ".join(f"{NAME[v]} {w}" for v, w in skipped.items()))
    zones: list[list[float]] = []  # [liq price, usd, side(+1 long / -1 short)]
    prev = None
    used = 0
    for _t, q, hi, lo, close, long_share in rows:
        zones = [z for z in zones if not ((z[2] > 0 and lo <= z[0]) or (z[2] < 0 and hi >= z[0]))]
        if prev is not None:
            dq = q - prev
            if dq > 0:
                for lev, w in LEVERAGE_MIX:
                    usd = dq * close * w
                    zones.append([close * (1 - 1 / lev + MAINT_MARGIN), usd * long_share, 1])
                    zones.append([close * (1 + 1 / lev - MAINT_MARGIN), usd * (1 - long_share), -1])
            elif dq < 0 and prev:
                keep = max(0.0, q / prev)
                for z in zones:
                    z[1] *= keep
            used += 1
        prev = q
    last = rows[-1][4]
    step = nice_step(last, 0.0025)
    agg: dict[tuple[float, int], float] = {}
    for px, usd, side in zones:
        if usd > 0:
            b = round(math.floor(px / step) * step, 10)
            agg[(b, side)] = agg.get((b, side), 0.0) + usd
    bins = sorted(({"px": px, "px_hi": px + step, "side": "long" if s > 0 else "short", "usd": u} for (px, s), u in agg.items()),
                  key=lambda z: z["px"])
    near = [z for z in bins if abs(z["px"] - last) / last <= 0.10]  # the zones a chart around the price can show
    top = sorted(near, key=lambda z: -z["usd"])[:6]
    return _result("liquidation_zones", "estimate", f"{coin} estimated liquidation zones", "USD", [venue], skipped,
                   price=last, step=step, bins=bins, top=top, intervals=used, period=period, start=rows[0][0], end=rows[-1][0],
                   assumptions=[f"Model estimate from {NAME[venue]} open-interest changes ({period}), not confirmed positions",
                                "New open interest is assumed opened at the interval's close, split long/short by its taker buy share",
                                "Leverage mix assumed: " + ", ".join(f"{lev}x {int(w * 100)}%" for lev, w in LEVERAGE_MIX),
                                f"Maintenance margin assumed {MAINT_MARGIN * 100:.1f}%; a zone is dropped once price trades through it"])


# ---------------------------------------------------------------------------------------------------------- TWAP-like slicing
def _prints(trades: list[tuple[Any, ...]]) -> list[tuple[int, float, float, str]]:
    """Fills of one taker order at the same millisecond and side, merged (one print = one aggressive order)."""
    out: dict[tuple[int, str], list[float]] = {}
    for t, px, q, side, *_ in trades:
        a = out.setdefault((t, side), [0.0, 0.0])
        a[0] += q
        a[1] += px * q
    return sorted((t, usd / q, q, side) for (t, side), (q, usd) in out.items() if q > 0)


def find_slicing(trades: list[tuple[Any, ...]], min_prints: int = TWAP_MIN_PRINTS, max_cv: float = TWAP_MAX_CV,
                 min_usd: float = 5_000, size_tol: float = 0.03, top_decile: bool = True) -> list[dict[str, Any]]:
    """Runs of same-side prints of near-identical size (within `size_tol`) at near-regular intervals (5 s to 10 min apart):
    the footprint of time-sliced execution. Pure function over observed trades (ms, price, qty, aggressor side, ...)."""
    ps = _prints(trades)
    if len(ps) < min_prints:
        return []
    notional = sorted(p[1] * p[2] for p in ps)
    floor = max(min_usd, notional[int(len(notional) * 0.9)] if top_decile and len(notional) > 20 else min_usd)
    found = []
    for side in ("buy", "sell"):
        big = sorted((p for p in ps if p[3] == side and p[1] * p[2] >= floor), key=lambda p: p[2])
        clusters: list[list[tuple[int, float, float, str]]] = []
        for p in big:  # sizes within size_tol of the cluster's first size
            if clusters and p[2] <= clusters[-1][0][2] * (1 + size_tol):
                clusters[-1].append(p)
            else:
                clusters.append([p])
        for cl in clusters:
            if len(cl) < min_prints:
                continue
            ts = sorted(p[0] for p in cl)[:600]
            best: list[int] = []
            for i in range(len(ts)):
                for j in range(i + 1, min(len(ts), i + 6)):
                    period = ts[j] - ts[i]
                    if not 5_000 <= period <= 600_000:
                        continue
                    run, nxt = [ts[i], ts[j]], ts[j] + period
                    for t in ts[j + 1:]:
                        if t > nxt + period * 0.2:
                            break
                        if abs(t - nxt) <= period * 0.2:
                            run.append(t)
                            nxt = t + period
                    if len(run) > len(best):
                        best = run
            if len(best) < min_prints:
                continue
            gaps = [b - a for a, b in zip(best, best[1:])]
            mean = sum(gaps) / len(gaps)
            cv = (sum((g - mean) ** 2 for g in gaps) / len(gaps)) ** 0.5 / mean if mean else 1.0
            if cv > max_cv:
                continue
            members = [p for p in cl if p[0] in set(best)]
            qty = sum(p[2] for p in members)
            usd = sum(p[1] * p[2] for p in members)
            found.append({"side": side, "prints": len(best), "interval_s": round(mean / 1000, 1), "regularity_cv": round(cv, 3),
                          "slice_qty": round(qty / len(members), 8), "total_qty": qty, "total_usd": usd, "start": best[0], "end": best[-1],
                          "times": best, "avg_px": usd / qty if qty else 0.0})
    return sorted(found, key=lambda f: -f["total_usd"])


def find_slicing_by_address(trades: list[tuple[int, float, float, str, str]], min_prints: int = TWAP_MIN_PRINTS) -> list[dict[str, Any]]:
    """The same footprint, one aggressor address at a time (Hyperliquid trades name both addresses): a run has to come
    from one address, so coincidences between unrelated traders drop out. An address is not an identity."""
    by: dict[str, list[tuple[int, float, float, str, str]]] = {}
    for tr in trades:
        if tr[4]:
            by.setdefault(tr[4], []).append(tr)
    found = []
    for addr, rows in by.items():
        if len(rows) < min_prints:
            continue
        for p in find_slicing(rows, min_prints, TWAP_MAX_CV, min_usd=500, size_tol=0.10, top_decile=False):
            found.append({**p, "address": f"{addr[:6]}…{addr[-4:]}"})
    return sorted(found, key=lambda f: -f["total_usd"])


def twap_patterns(coin: str, minutes: int = 30, vs: Venues | None = None, tape: Any = None) -> dict[str, Any]:
    """Time-sliced execution patterns of the last `minutes`: Binance USDⓈ-M trades (size + timing) and Hyperliquid's
    recorded trade tape (size + timing, per address). Inferred, never attributed to anyone."""
    from app.tradingos import hltape

    vs = vs or venues()
    tape = tape or hltape.tape()
    minutes = max(5, min(120, minutes))
    used: list[str] = []
    skipped: dict[str, str] = {}
    found: list[dict[str, Any]] = []
    n = 0
    t_lo: int | None = None
    t_hi: int | None = None
    try:
        trades = vs.binance_trades(symbols(coin)["binance"], minutes)
        if trades:
            used.append("binance")
            n += len(trades)
            found += [{**p, "venue": "Binance"} for p in find_slicing(trades)]
            t_lo, t_hi = trades[0][0], trades[-1][0]
        else:
            skipped["binance"] = "sent no trades"
    except (StructureError, KeyError) as e:
        skipped["binance"] = str(e) if isinstance(e, StructureError) else "doesn't list this market"
    rows, since = tape.get(coin)
    cut = (time.time() - minutes * 60) * 1000
    rows = [r for r in rows if r[0] >= cut]
    if rows and since:
        used.append("hyperliquid")
        n += len(rows)
        found += [{**p, "venue": "Hyperliquid"} for p in find_slicing_by_address(rows)]
        t_lo = min(t_lo or rows[0][0], rows[0][0])
        t_hi = max(t_hi or rows[-1][0], rows[-1][0])
    else:
        skipped["hyperliquid"] = (f"trade tape recorded for {', '.join(hltape.COINS)} only" if coin.upper() not in hltape.COINS
                                  else "trade tape not recording yet" + (f" ({tape.error})" if tape.error else ""))
    if not used:
        raise StructureError("No trade feed to scan: " + "; ".join(f"{NAME[v]} {w}" for v, w in skipped.items()))
    notes = ["Same-side prints of near-identical size at regular intervals: consistent with time-sliced (TWAP-style) execution. "
             "Price Chat can't tell who placed them; coincidences are possible"]
    if "hyperliquid" in used:
        notes.append("Hyperliquid runs come from one on-chain address each (an address, not an identity); tape recorded since "
                     + _stamp(since * 1000))
    return _result("twap", "inferred", f"{coin} TWAP-like execution", "USD", used, skipped,
                   patterns=sorted(found, key=lambda f: -f["total_usd"])[:5], trades=n, start=t_lo, end=t_hi, minutes=minutes, notes=notes)


# ---------------------------------------------------------------------------------------------------------- recorder
HEATMAP_COINS = ("BTC", "ETH", "SOL")
BOOK_KEEP = timedelta(days=2)
LIQ_KEEP = timedelta(days=35)


def store_liquidations(db: Any, rows: list[dict[str, Any]]) -> int:
    """Write reported liquidations, skipping ones already stored (the REST list and the stream overlap)."""
    from sqlalchemy import select

    from app.models.tradingos import TsLiquidation

    if not rows:
        return 0
    lo = min(r["t"] for r in rows)
    seen = {(r.venue, r.coin, r.t, r.side, r.px, r.qty) for r in db.scalars(select(TsLiquidation).where(TsLiquidation.t >= lo))}
    n = 0
    for r in rows:
        k = (r["venue"], r["coin"], r["t"], r["side"], r["px"], r["qty"])
        if k not in seen:
            seen.add(k)
            db.add(TsLiquidation(venue=r["venue"], coin=r["coin"], t=r["t"], side=r["side"], px=r["px"], qty=r["qty"], usd=r["usd"]))
            n += 1
    return n


def record(db: Any, coins: tuple[str, ...] = HEATMAP_COINS, vs: Venues | None = None, md: MarketData | None = None,
           now: datetime | None = None, liq_tape: Any = None) -> dict[str, Any]:
    """Every 2 minutes: each coin's binned books (heatmaps), the liquidations the streams buffered and OKX's REST list;
    then retention. The production recorder also keeps the Hyperliquid trade tape and the liquidation streams running."""
    from sqlalchemy import delete

    from app.db.base import utcnow
    from app.models.tradingos import TsBookSnapshot, TsLiquidation
    from app.tradingos import hltape, liqtape

    now = now or utcnow()
    real = vs is None
    vs = vs or venues()
    if real:  # not a test's fake exchanges
        hltape.ensure()
        try:
            top = tuple(symbols(m.coin)["bybit"] for m in (md or hl_data()).top(30))
        except MarketError:
            top = tuple(symbols(c)["bybit"] for c in coins)
        liq_tape = liqtape.ensure(top)
    books_n = 0
    pending: list[dict[str, Any]] = []  # every reported liquidation of this run, stored in ONE pass (REST and streams overlap)
    errors: dict[str, str] = {}
    for coin in coins:
        try:
            b = books(coin, vs, md, ("binance", "okx", "hyperliquid"))
            for v, why in b["skipped"].items():
                errors[f"{coin} book {v}"] = why
            db.add(TsBookSnapshot(at=now, coin=coin, mid=b["mid"], step=b["step"],
                                  data={k: {"bins": [[round(px, 10), round(u, 2)] for px, u in x["bins"]], "cover": x["cover"]}
                                        for k, x in b["by_venue"].items()}))
            books_n += 1
        except StructureError as e:
            errors[f"{coin} book"] = str(e)
        try:
            pending += [{**r, "coin": liq_coin(coin)} for r in vs.okx_liquidations(symbols(coin)["okx"])]
        except StructureError as e:
            errors[f"{coin} liquidations"] = str(e)
    streams = None
    if liq_tape is not None:
        pending += liq_tape.drain()
        streams = liq_tape.status()
        for v, st in streams.items():
            if not st["connected"] and st["error"]:
                errors[f"stream {v}"] = st["error"]
    for k, why in errors.items():
        if k.split(" ", 1)[1] not in _warned:
            _warned.add(k.split(" ", 1)[1])
            logger.warning("Market structure recorder: %s unavailable: %s", k, why)
    liqs = store_liquidations(db, pending)
    db.execute(delete(TsBookSnapshot).where(TsBookSnapshot.at < now - BOOK_KEEP))
    db.execute(delete(TsLiquidation).where(TsLiquidation.t < int((now - LIQ_KEEP).timestamp() * 1000)))
    db.commit()
    out: dict[str, Any] = {"books": books_n, "liquidations": liqs}
    if streams is not None:
        out["streams"] = {v: ("connected" if st["connected"] else "down") + f", {st['received']} received" for v, st in streams.items()}
        rows, since = hltape.tape().get("BTC")
        out["hl_tape"] = f"recording since {_stamp(since * 1000)}, {len(rows)} BTC trades" if since else "not recording"
        global _status_logged
        if time.time() - _status_logged >= 3600:  # once an hour: proof in the logs that the live feeds are delivering
            _status_logged = time.time()
            logger.info("Market structure streams: %s · Hyperliquid tape %s",
                        " · ".join(f"{NAME[v]} {x}" for v, x in out["streams"].items()), out["hl_tape"])
    if errors:
        out["errors"] = errors
    return out


def heatmap(db: Any, coin: str, since: datetime) -> dict[str, Any]:
    """Recorded book snapshots since `since`: [(ms, mid, step, [(px, usd)...])], each bin the sum of the venues whose
    public book reached that price at that moment."""
    from sqlalchemy import select

    from app.models.tradingos import TsBookSnapshot

    snaps = []
    covers: dict[str, list[float]] = {}
    for s in db.scalars(select(TsBookSnapshot).where(TsBookSnapshot.coin == coin.upper(), TsBookSnapshot.at >= since).order_by(TsBookSnapshot.at)):
        at = s.at if s.at.tzinfo else s.at.replace(tzinfo=timezone.utc)
        agg: dict[float, float] = {}
        for v, x in (s.data or {}).items():
            for px, u in x.get("bins") or []:
                agg[px] = agg.get(px, 0.0) + u
            c = x.get("cover") or [0, 0]
            covers.setdefault(v, []).append(((s.mid - c[0]) / s.mid * 100 + (c[1] - s.mid) / s.mid * 100) / 2 if s.mid else 0.0)
        snaps.append((int(at.timestamp() * 1000), s.mid, s.step, sorted(agg.items())))
    used = sorted(covers)
    return _result("heatmap", "aggregated" if len(used) > 1 else "observed", f"{coin} resting liquidity", "USD", [v for v in used if v in NAME], {},
                   snapshots=snaps, notes=[f"{NAME.get(v, v)} book reaches about ±{sum(c) / len(c):.2f}% of mid" for v, c in covers.items() if c]
                   + ["Recorded every 2 minutes by Price Chat; each venue counts only where its public book reaches"])
