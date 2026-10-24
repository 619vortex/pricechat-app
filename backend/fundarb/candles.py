"""Hourly closes per venue (Module 3's correlation matrix and the price-spread history). Public endpoints, verified live
2026-10-24 over a 30-day window:
- Hyperliquid  POST /info {"type": "candleSnapshot", "req": {coin, interval "1h", startTime ms, endTime ms}} -> [{t ms, c}]
               (perps by coin, "xyz:TSLA" for a builder dex, spot by "@<index>"); 721 bars for 30 days in one call
- Lighter      GET /api/v1/candles?market_id&resolution=1h&start_timestamp&end_timestamp&count_back -> {"c": [{t ms, c}]};
               at most 500 bars per call, so 30 days is read in pages (/api/v1/candlesticks answers 403)
- Backpack     GET /api/v1/klines?symbol&interval=1h&startTime&endTime (seconds) -> [{start "YYYY-MM-DD HH:MM:SS" UTC, close}]
- Orderly      GET /tv/history?symbol&resolution=60&from&to (seconds) -> {"s": "ok", "t": [s], "c": [...]}
Returned as {unix hour: close}. Cached per (venue, market) for `ttl` seconds: the correlation matrix is recomputed on a
slower cadence than the funding snapshot."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from fundarb.venues import BackpackVenue, HyperliquidVenue, LighterVenue, OrderlyVenue, PerpVenue

LIGHTER_PAGE = 500
_cache: dict[tuple[str, str, int], tuple[float, dict[int, float]]] = {}


def _hour(ts_seconds: float) -> int:
    return int(ts_seconds // 3600)


async def hourly_closes(v: PerpVenue, market: str, days: float = 30.0, ttl: float = 3600.0, symbol: str | None = None) -> dict[int, float]:
    """`market` is the venue's own name for Hyperliquid (coin / "@index"); for the others pass the canonical `symbol`
    ("BTC-PERP") and the adapter resolves its own market name or id."""
    key = (v.name, market, int(days))
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < ttl:
        return hit[1]
    now = int(time.time())
    start = now - int(days * 86400)
    out: dict[int, float] = {}
    if isinstance(v, HyperliquidVenue):
        rows = await v._info({"type": "candleSnapshot", "req": {"coin": market, "interval": "1h", "startTime": start * 1000, "endTime": now * 1000}})
        out = {_hour(int(r["t"]) / 1000): float(r["c"]) for r in rows or []}
    elif isinstance(v, LighterVenue):
        mid = await v._market_id(symbol or market)
        end = now
        while end > start:
            d = await v._get("/candles", market_id=mid, resolution="1h", start_timestamp=start, end_timestamp=end, count_back=LIGHTER_PAGE)
            rows = d.get("c") or []
            if not rows:
                break
            out.update({_hour(int(r["t"]) / 1000): float(r["c"]) for r in rows})
            first = min(int(r["t"]) // 1000 for r in rows)
            if first <= start or len(rows) < LIGHTER_PAGE:
                break
            end = first - 1
    elif isinstance(v, BackpackVenue):
        native = await v._native(symbol or market)
        rows = await v._get("/api/v1/klines", symbol=native, interval="1h", startTime=start, endTime=now)
        for r in rows or []:
            t = datetime.strptime(r["start"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
            out[_hour(t)] = float(r["close"])
    elif isinstance(v, OrderlyVenue):
        native = await v._native(symbol or market)
        # /tv/history answers {"s": "ok", ...} without the "success" field the adapter's _get requires: read it raw
        d: Any = await v._request("GET", "/tv/history", params={"symbol": native, "resolution": "60", "from": start, "to": now})
        if (d or {}).get("s") == "ok":
            out = {_hour(float(t)): float(c) for t, c in zip(d["t"], d["c"])}
    else:
        raise NotImplementedError(f"{v.name}: no hourly candle reader")
    _cache[key] = (time.monotonic(), out)
    return out
