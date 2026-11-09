"""Earn › Funding Arb: a READ-ONLY funding-rate board across Hyperliquid, Lighter, Orderly and Backpack (fundarb adapters).

For every perp listed on at least two venues: each venue's funding rate normalized to an hourly rate and an APR (using the
interval the venue itself reports), and the gross spread between the highest and lowest (short the high one, long the low
one). Gross means before fees, slippage, depth limits and the risk of the spread closing: the signal engine (fundarb Phase 2)
adds those. Nothing here places an order. Refreshed at most every CACHE_SECONDS; a venue that fails is named with its reason.

The widest HISTORY_ROWS gaps also get the spread's own 7-day record (fundarb.signals.evaluate over both legs' settled
funding): average APR, its swing, the share of hours it paid, today vs that average, and the APR on the capital both legs
tie up. History is read in the background and cached per (venue, symbol) for HISTORY_SECONDS, so the board never waits
for it: a row without history yet shows its live gap only, and the board is rebuilt as soon as the history arrives."""

import asyncio
import math
import time
from datetime import datetime, timezone
from typing import Any

from fundarb.config import Settings
from fundarb.models import HOURS_PER_YEAR, FundingRate
from fundarb.signals import Leg, SignalConfig, evaluate, hourly_series
from fundarb.venues import BackpackVenue, HyperliquidVenue, LighterVenue, OrderlyVenue, PerpVenue

CACHE_SECONDS = 120
MAX_LIGHTER = 40  # Lighter has no bulk funding endpoint: one request per symbol, so only symbols other venues also list
TOP = 40
LIGHTER_CONCURRENCY = 4
LIGHTER_TIMEOUT = 20.0  # seconds for the whole Lighter read (the board's lock is held meanwhile): the rates read so far on timeout

HISTORY_DAYS = 7.0
HISTORY_SECONDS = 1800  # each venue's settled funding for a symbol is re-read at most every 30 min
HISTORY_ROWS = 25  # the widest gaps get the 7-day record (two history reads each, cached)
HISTORY_CONCURRENCY = 4
HISTORY_TIMEOUT = 90.0  # seconds for one background history pass: what arrived by then is kept
CFG = SignalConfig()  # leverage, buffer and stability_k: shown on the page as the board's assumptions
VENUES: dict[str, type[PerpVenue]] = {"hyperliquid": HyperliquidVenue, "backpack": BackpackVenue, "orderly": OrderlyVenue, "lighter": LighterVenue}

_cache: dict[str, Any] = {"at": 0.0, "data": None}
_lock = asyncio.Lock()
_history: dict[tuple[str, str], tuple[float, list[tuple[float, float]]]] = {}  # (venue, symbol) -> (read at, [(ts, rate)])
_history_task: asyncio.Task[None] | None = None


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


def _fresh_history(venue: str, sym: str) -> list[tuple[float, float]] | None:
    hit = _history.get((venue, sym))
    return hit[1] if hit and time.monotonic() - hit[0] < HISTORY_SECONDS else None


async def fill_history(keys: list[tuple[str, str]], settings: Settings | None = None, transport: Any = None) -> None:
    """Reads each (venue, symbol)'s last HISTORY_DAYS of settled funding into the cache, a few at a time, within HISTORY_TIMEOUT.
    A read that fails is cached empty (retried after HISTORY_SECONDS), so one bad market never stalls the rest."""
    s = settings or Settings()
    venues = {name: VENUES[name](s, transport=transport) for name in {v for v, _ in keys} if name in VENUES}
    sem = asyncio.Semaphore(HISTORY_CONCURRENCY)

    async def one(venue: str, sym: str) -> None:
        async with sem:
            try:
                rows = await venues[venue].funding_history(sym, HISTORY_DAYS)
            except Exception:  # noqa: BLE001 - one market failing is left out, like the board's own reads
                rows = []
            _history[(venue, sym)] = (time.monotonic(), rows)

    async def read_all() -> None:
        up = []
        for name, v in venues.items():
            try:
                await v.connect()
                up.append(name)
            except Exception:  # noqa: BLE001 - an offline venue just has no history this pass
                pass
        await asyncio.gather(*(one(v, sym) for v, sym in keys if v in up))

    try:
        await asyncio.wait_for(read_all(), HISTORY_TIMEOUT)
    except asyncio.TimeoutError:
        pass
    finally:
        for v in venues.values():
            await v.disconnect()


def _history_stats(row: dict[str, Any], rates: dict[tuple[str, str], FundingRate]) -> dict[str, Any] | None:
    """The row's long/short pair over the last 7 days (fundarb.signals.evaluate), or None while either leg's history is missing."""
    sym, legs = row["symbol"], []
    for venue in (row["long"], row["short"]):
        hist = _fresh_history(venue, sym)
        if hist is None:
            return None
        f = rates[(venue, sym)]
        legs.append(Leg(venue=venue, rate=f, hourly=hourly_series(hist, f.interval_hours)))
    o = evaluate(sym, legs[0], legs[1], CFG)
    if o.mean_apr_7d is None:  # too little shared history to say anything
        return {"hours": o.samples}
    return {"hours": o.samples, "mean_apr_pct": round(o.mean_apr_7d, 3), "std_apr_pct": round(o.std_apr_7d or 0.0, 3),
            "z": None if o.z is None else round(o.z, 2), "paid_share": None if o.paid_share is None else round(o.paid_share, 3),
            "score_pct": None if o.score is None else round(o.score, 3)}


def _start_history(keys: list[tuple[str, str]], settings: Settings, transport: Any) -> None:
    """One background pass at a time; when it lands, the cached board is marked stale so the next request shows the stats."""
    global _history_task
    if not keys or (_history_task is not None and not _history_task.done()):
        return

    async def run() -> None:
        await fill_history(keys, settings, transport)
        _cache["at"] = 0.0

    _history_task = asyncio.get_running_loop().create_task(run())


async def build(settings: Settings | None = None, transport: Any = None, wait_history: bool = False) -> dict[str, Any]:
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
    rates = {(name, sym): f for name, (got_rates, _) in got.items() for sym, f in got_rates.items()}
    missing = sorted({(v, r["symbol"]) for r in rows[:HISTORY_ROWS] for v in (r["long"], r["short"]) if _fresh_history(v, r["symbol"]) is None})
    if missing and wait_history:
        await fill_history(missing, s, transport)
    elif missing:
        _start_history(missing, s, transport)
    cap_mult = 1 / CFG.capital_per_notional
    for i, r in enumerate(rows):
        r["capital_apr_pct"] = round(r["gross_spread_apr_pct"] * cap_mult, 3)
        if i < HISTORY_ROWS:
            r["stats"] = _history_stats(r, rates)

    def steadiest(r: dict[str, Any]) -> tuple[float, float]:
        score = (r.get("stats") or {}).get("score_pct")
        return (-score if score is not None else math.inf, -r["gross_spread_apr_pct"])

    # steadiest carry first (the 7-day average minus stability_k swings, on capital), then the rest by today's gap
    rows.sort(key=steadiest)
    return {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "venues": {name: {"ok": err is None, "symbols": len(rates), "error": err,
                          "interval_hours": sorted({f.interval_hours for f in rates.values()}),
                          "interval_source": next(iter(rates.values())).interval_source if rates else None}
                   for name, (rates, err) in got.items()},
        "rows": rows[:TOP],
        "pairs_found": len(rows),
        "assumptions": {"leverage": CFG.leverage, "buffer": CFG.buffer, "capital_per_notional": round(CFG.capital_per_notional, 4),
                        "stability_k": CFG.stability_k, "history_days": HISTORY_DAYS},
        "note": "Gross funding spread before fees, slippage, depth and the chance the spread closes. Read-only: no trades.",
    }


_refresh_task: asyncio.Task[Any] | None = None


async def board(force: bool = False, transport: Any = None) -> dict[str, Any]:
    """The board, rebuilt at most every CACHE_SECONDS. Past that age the last board is returned at once and ONE background task
    rebuilds it (no visitor waits seconds for every venue); only the very first board (nothing cached yet) is waited for."""
    global _refresh_task
    if not force and _cache["data"] is not None and time.monotonic() - _cache["at"] >= CACHE_SECONDS:
        if _refresh_task is None or _refresh_task.done():
            _refresh_task = asyncio.get_running_loop().create_task(_rebuild(False, transport))
        return _cache["data"]
    return await _rebuild(force, transport)


async def _rebuild(force: bool, transport: Any) -> dict[str, Any]:
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
