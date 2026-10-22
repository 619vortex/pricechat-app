"""Phase 2 scan: every venue's funding in bulk, the biggest current spreads shortlisted, then each shortlisted symbol's 7-day
history, fees and caps read, every venue pair evaluated (signals.py), and the leaders priced against live order books.
Read-only. A venue that fails is reported with its reason; the scan continues with the others."""

from __future__ import annotations

import asyncio
from typing import Any

from fundarb.config import Settings
from fundarb.models import FundingRate
from fundarb.signals import Leg, Opportunity, SignalConfig, best_pairs, hourly_series, rank_key
from fundarb.venues import VENUES, PerpVenue, VenueError, VenueUnavailable


async def _gather_limited(coros: list[Any], limit: int = 8) -> list[Any]:
    sem = asyncio.Semaphore(limit)

    async def one(c: Any) -> Any:
        async with sem:
            try:
                return await c
            except Exception as exc:  # noqa: BLE001 - one symbol failing never stops the scan
                return exc
    return await asyncio.gather(*(one(c) for c in coros))


async def scan(settings: Settings | None = None, cfg: SignalConfig = SignalConfig(), top: int = 10, shortlist: int = 25,
               transport: Any = None, venues: list[str] | None = None) -> dict[str, Any]:
    s = settings or Settings()
    vs: dict[str, PerpVenue] = {n: VENUES[n](s, transport=transport) for n in (venues or list(VENUES))}
    errors: dict[str, str] = {}
    try:
        for n, v in list(vs.items()):
            try:
                await v.connect()
            except Exception as exc:  # noqa: BLE001
                errors[n] = f"connect failed: {exc}"
                vs.pop(n)
        bulk = {n: v for n, v in vs.items() if n != "lighter"}
        boards = dict(zip(bulk, await _gather_limited([v.funding_board() for v in bulk.values()])))
        rates: dict[str, dict[str, FundingRate]] = {}
        for n, b in boards.items():
            if isinstance(b, Exception):
                errors[n] = f"{type(b).__name__}: {b}"
            else:
                rates[n] = b
        if "lighter" in vs:  # no bulk endpoint: only the symbols the others list, most-listed first
            counts: dict[str, int] = {}
            for b in rates.values():
                for sym in b:
                    counts[sym] = counts.get(sym, 0) + 1
            wanted = sorted(counts, key=lambda k: (-counts[k], k))[: shortlist * 3]
            try:
                rates["lighter"] = await vs["lighter"].funding_board(wanted)
            except VenueError as exc:
                errors["lighter"] = str(exc)
        # shortlist by the current spread
        spread: dict[str, float] = {}
        for sym in {x for b in rates.values() for x in b}:
            hs = [b[sym].rate_raw / b[sym].interval_hours for b in rates.values() if sym in b]
            if len(hs) >= 2:
                spread[sym] = max(hs) - min(hs)
        picked = sorted(spread, key=lambda k: -spread[k])[:shortlist]
        fees: dict[str, Any] = {}
        for n, v in vs.items():
            try:
                fees[n] = await v.get_fee_schedule()
            except VenueUnavailable as exc:
                fees[n] = str(exc)

        async def legs_for(sym: str) -> list[Leg]:
            out = []
            for n, b in rates.items():
                if sym not in b:
                    continue
                v = vs[n]
                hist = await v.funding_history(sym)
                fee = fees.get(n)
                cap = await v.funding_cap(sym) if hasattr(v, "funding_cap") else None
                out.append(Leg(n, b[sym], hourly_series(hist, b[sym].interval_hours),
                               taker_bps=fee.for_symbol(sym)[1] if not isinstance(fee, str) and fee is not None else None,
                               fee_source=fee.source if not isinstance(fee, str) and fee is not None else (fee or ""), cap=cap))
            return out
        leg_sets = dict(zip(picked, await _gather_limited([legs_for(sym) for sym in picked])))
        best: list[tuple[Opportunity, list[Leg]]] = []
        for sym, legs in leg_sets.items():
            if isinstance(legs, Exception) or len(legs) < 2:
                continue
            best.append((best_pairs(sym, legs, cfg)[0], legs))
        best.sort(key=lambda x: rank_key(x[0]))
        # price the leaders against live order books (two books per opportunity)
        final: list[Opportunity] = []
        for opp, legs in best[: top * 2]:
            by = {leg.venue: leg for leg in legs}
            pair = [by[opp.long], by[opp.short]]
            books = await _gather_limited([vs[leg.venue].get_orderbook(opp.symbol, s.book_depth) for leg in pair])
            for leg, book in zip(pair, books):
                leg.book = None if isinstance(book, Exception) else book
            final.append(best_pairs(opp.symbol, pair, cfg)[0])
        final.sort(key=rank_key)
        all_best = [o for o, _ in best]
        return {"opportunities": [o.as_dict() for o in final[:top]], "errors": errors, "shortlisted": len(picked),
                "persistent": sum(1 for o in all_best if o.persistent), "transient": sum(1 for o in all_best if not o.persistent),
                "config": cfg.__dict__ | {"capital_per_notional": cfg.capital_per_notional},
                "fees": {n: (f if isinstance(f, str) else f.source) for n, f in fees.items()}}
    finally:
        for v in vs.values():
            await v.disconnect()
