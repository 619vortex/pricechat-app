"""Earn › Funding Arb: a READ-ONLY funding-rate board across Hyperliquid, Lighter, Orderly and Backpack (fundarb adapters).

For every perp listed on at least two venues: each venue's funding rate normalized to an hourly rate and an APR (using the
interval the venue itself reports), and the gross spread between the highest and lowest (short the high one, long the low
one). Gross means before fees, slippage, depth limits and the risk of the spread closing: the signal engine (fundarb Phase 2)
adds those. Nothing here places an order. Refreshed at most every CACHE_SECONDS; a venue that fails is named with its reason."""

import asyncio
import time
from datetime import datetime, timezone
from typing import Any

from fundarb.config import Settings
from fundarb.models import HOURS_PER_YEAR, FundingRate
from fundarb.venues import BackpackVenue, HyperliquidVenue, LighterVenue, OrderlyVenue, PerpVenue

CACHE_SECONDS = 120
MAX_LIGHTER = 40  # Lighter has no bulk funding endpoint: one request per symbol, so only symbols other venues also list
TOP = 40
LIGHTER_CONCURRENCY = 4
LIGHTER_TIMEOUT = 20.0  # seconds for the whole Lighter read (the board's lock is held meanwhile): the rates read so far on timeout

_cache: dict[str, Any] = {"at": 0.0, "data": None}
_lock = asyncio.Lock()


async def _read(v: PerpVenue, symbols: list[str] | None = None) -> tuple[dict[str, FundingRate], str | None]:
    try:
        await v.connect()
        return await v.funding_board(symbols), None
    except Exception as exc:  # noqa: BLE001 - one venue failing must not hide the others
        return {}, f"{type(exc).__name__}: {str(exc)[:200]}"
    finally:
        await v.disconnect()


async def _read_lighter(v: LighterVenue, wanted: list[str]) -> tuple[dict[str, FundingRate], str | None]:
    """Lighter, one request per symbol: only symbols it lists, at most MAX_LIGHTER, a few at a time, within LIGHTER_TIMEOUT."""
    out: dict[str, FundingRate] = {}
    sem = asyncio.Semaphore(LIGHTER_CONCURRENCY)

    async def one(sym: str) -> None:
        async with sem:
            try:
                out[sym] = await v.get_funding_rate(sym)
            except Exception:  # noqa: BLE001 - one symbol failing is left out, like the venue's own funding_board
                pass

    async def read_all() -> None:
        listed = set(await v.symbols())
        await asyncio.gather(*(one(sym) for sym in [w for w in wanted if w in listed][:MAX_LIGHTER]))

    try:
        await v.connect()
        await asyncio.wait_for(read_all(), LIGHTER_TIMEOUT)
        return out, None
    except asyncio.TimeoutError:  # slow venue: keep what arrived
        return dict(out), (None if out else f"TimeoutError: no Lighter rates within {LIGHTER_TIMEOUT:.0f}s")
    except Exception as exc:  # noqa: BLE001 - one venue failing must not hide the others
        return dict(out), f"{type(exc).__name__}: {str(exc)[:200]}"
    finally:
        await v.disconnect()


async def build(settings: Settings | None = None, transport: Any = None) -> dict[str, Any]:
    s = settings or Settings()
    bulk = {"hyperliquid": HyperliquidVenue(s, transport=transport), "backpack": BackpackVenue(s, transport=transport),
            "orderly": OrderlyVenue(s, transport=transport)}
    got = dict(zip(bulk, await asyncio.gather(*(_read(v) for v in bulk.values()))))
    counts: dict[str, int] = {}
    for rates, _ in got.values():
        for sym in rates:
            counts[sym] = counts.get(sym, 0) + 1
    lighter = LighterVenue(s, transport=transport)
    got["lighter"] = await _read_lighter(lighter, sorted(counts, key=lambda k: (-counts[k], k)))
    rows = []
    symbols = {sym for rates, _ in got.values() for sym in rates}
    for sym in symbols:
        legs = [{"venue": name, "rate_raw": f.rate_raw, "interval_hours": f.interval_hours, "interval_source": f.interval_source, "kind": f.kind,
                 "hourly": f.rate_raw / f.interval_hours, "apr_pct": round(f.rate_raw / f.interval_hours * HOURS_PER_YEAR * 100, 3)}
                for name, (rates, _) in got.items() if (f := rates.get(sym))]
        if len(legs) < 2:
            continue
        hi, lo = max(legs, key=lambda l: l["hourly"]), min(legs, key=lambda l: l["hourly"])
        rows.append({"symbol": sym, "legs": sorted(legs, key=lambda l: -l["hourly"]), "short": hi["venue"], "long": lo["venue"],
                     "gross_spread_apr_pct": round((hi["hourly"] - lo["hourly"]) * HOURS_PER_YEAR * 100, 3)})
    rows.sort(key=lambda r: -r["gross_spread_apr_pct"])
    return {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "venues": {name: {"ok": err is None, "symbols": len(rates), "error": err,
                          "interval_hours": sorted({f.interval_hours for f in rates.values()}),
                          "interval_source": next(iter(rates.values())).interval_source if rates else None}
                   for name, (rates, err) in got.items()},
        "rows": rows[:TOP],
        "pairs_found": len(rows),
        "note": "Gross funding spread before fees, slippage, depth and the chance the spread closes. Read-only: no trades.",
    }


async def board(force: bool = False, transport: Any = None) -> dict[str, Any]:
    async with _lock:
        if not force and _cache["data"] is not None and time.monotonic() - _cache["at"] < CACHE_SECONDS:
            return _cache["data"]
        try:
            data = await build(transport=transport)
        except Exception:  # noqa: BLE001 - a failed refresh keeps showing the last good board
            if _cache["data"] is None:
                raise
            return _cache["data"]
        _cache.update(at=time.monotonic(), data=data)
        return data
