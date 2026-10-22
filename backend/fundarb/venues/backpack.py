"""Backpack Exchange.

Public endpoints (verified live, 2026-10-21; no auth), base {url}:
- GET /api/v1/markets                    -> per market: symbol, marketType ("PERP"), fundingInterval (MILLISECONDS) ...
- GET /api/v1/markPrices?symbol=         -> fundingRate (running interval), markPrice, indexPrice, nextFundingTimestamp
- GET /api/v1/fundingRates?symbol=       -> settled history: fundingRate, intervalEndTimestamp
- GET /api/v1/depth?symbol=              -> asks [[price, size]] best-first, bids [[price, size]] WORST-first (re-sorted here)
Funding interval: read from each market's `fundingInterval` at runtime (3,600,000 ms = 1 h on all 103 perps, 2026-10-21) and
logged at WARNING on first read, as the brief requires.

Authenticated (ED25519 key, X-API-Key / X-Signature / X-Timestamp / X-Window): fees, positions, balance, orders. Not wired:
the brief asks to mirror a specific public Backpack<->Hyperliquid reference implementation, which hasn't been provided."""

import logging
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from fundarb.models import Balance, FeeSchedule, FundingRate, OpenOrder, OrderBook, Position
from fundarb.symbols import base_of, canonical
from fundarb.venues.base import PerpVenue, VenueUnavailable

log = logging.getLogger("fundarb.venues.backpack")
MARKETS_TTL = 3600.0


def backpack_symbol(symbol: str) -> str:
    """For messages only: the real request always uses the market's own name (_native)."""
    return f"{base_of(symbol)}_USDC_PERP"


class BackpackVenue(PerpVenue):
    name = "backpack"

    def __init__(self, *a: Any, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self._logged_intervals = False

    @property
    def base_url(self) -> str:
        return self.settings.backpack_url

    async def _get(self, path: str, **params: Any) -> Any:
        return await self._request("GET", path, params=params or None)

    async def ping(self) -> None:
        await self._markets(force=True)

    async def _markets(self, force: bool = False) -> dict[str, dict[str, Any]]:
        async def fetch() -> dict[str, dict[str, Any]]:
            rows = await self._get("/api/v1/markets") or []
            out = {}
            for r in rows:
                parts = str(r.get("symbol", "")).split("_")
                if r.get("marketType") == "PERP" and len(parts) == 3 and parts[1:] == ["USDC", "PERP"]:
                    out[canonical(parts[0])] = r
            if not self._logged_intervals and out:
                seen = Counter(r.get("fundingInterval") for r in out.values())
                log.warning("backpack funding intervals read from /api/v1/markets fundingInterval: %s",
                            ", ".join(f"{v} ms ({(v or 0) / 3_600_000:g} h) x{n}" for v, n in seen.items()))
                self._logged_intervals = True
            return out
        if force:
            return self._cache.put("markets", await fetch())
        return await self._cached("markets", MARKETS_TTL, fetch)

    async def _market(self, symbol: str) -> dict[str, Any]:
        m = (await self._markets()).get(canonical(base_of(symbol)))
        if m is None:
            raise VenueUnavailable(f"backpack: no USDC perp {backpack_symbol(symbol)}")
        return m

    async def symbols(self) -> list[str]:
        return sorted(await self._markets())

    async def get_funding_interval_hours(self, symbol: str | None = None) -> float:
        m = await self._market(symbol or "BTC-PERP")
        ms = m.get("fundingInterval")
        if not isinstance(ms, (int, float)) or ms <= 0:
            raise VenueUnavailable(f"backpack: /api/v1/markets has no usable fundingInterval for {m.get('symbol')}")
        return ms / 3_600_000

    async def _native(self, symbol: str) -> str:
        """The venue's own market name (e.g. kPEPE_USDC_PERP for 1000PEPE-PERP), never rebuilt from the canonical one."""
        return str((await self._market(symbol))["symbol"])

    async def funding_history(self, symbol: str, days: float = 7.0) -> list[tuple[float, float]]:
        """GET /api/v1/fundingRates (newest first; intervalEndTimestamp is UTC without a zone)."""
        hours = await self.get_funding_interval_hours(symbol)
        rows = await self._get("/api/v1/fundingRates", symbol=await self._native(symbol), limit=min(1000, int(days * 24 / hours) + 2))
        out = []
        for r in rows or []:
            ts = datetime.fromisoformat(str(r["intervalEndTimestamp"])).replace(tzinfo=timezone.utc).timestamp()
            out.append((ts, float(r["fundingRate"])))
        return sorted(out)

    async def _mark(self, symbol: str) -> dict[str, Any]:
        sym = await self._native(symbol)

        async def fetch() -> dict[str, Any]:
            rows = await self._get("/api/v1/markPrices", symbol=sym)
            row = next((r for r in rows or [] if r.get("symbol") == sym), None)
            if row is None:
                raise VenueUnavailable(f"backpack: no mark price for {sym}")
            return row
        return await self._cached(("mark", sym), 5.0, fetch)

    async def get_funding_rate(self, symbol: str) -> FundingRate:
        await self._market(symbol)
        row = await self._mark(symbol)
        return FundingRate(float(row["fundingRate"]), await self.get_funding_interval_hours(symbol), "api: /api/v1/markets fundingInterval",
                           "predicted", datetime.now(timezone.utc))

    async def funding_board(self, symbols: list[str] | None = None) -> dict[str, FundingRate]:
        """All USDC perps from one /api/v1/markPrices call (no symbol), each with its market's fundingInterval."""
        markets = await self._markets()
        by_native = {m["symbol"]: s for s, m in markets.items()}
        now = datetime.now(timezone.utc)
        out: dict[str, FundingRate] = {}
        for r in await self._get("/api/v1/markPrices") or []:
            sym = by_native.get(r.get("symbol"))
            ms = (markets.get(sym) or {}).get("fundingInterval") if sym else None
            if sym is None or r.get("fundingRate") is None or not isinstance(ms, (int, float)) or ms <= 0:
                continue
            if symbols is not None and sym not in symbols:
                continue
            out[sym] = FundingRate(float(r["fundingRate"]), ms / 3_600_000, "api: /api/v1/markets fundingInterval", "predicted", now)
        return out

    async def get_mark_price(self, symbol: str) -> float:
        return float((await self._mark(symbol))["markPrice"])

    async def get_index_price(self, symbol: str) -> float:
        return float((await self._mark(symbol))["indexPrice"])

    async def get_orderbook(self, symbol: str, depth: int | None = None) -> OrderBook:
        d = await self._get("/api/v1/depth", symbol=await self._native(symbol))
        ts = d.get("timestamp")
        when = datetime.fromtimestamp(int(ts) / 1_000_000, tz=timezone.utc) if ts else None  # microseconds
        return OrderBook.build(self.name, symbol, [(float(p), float(s)) for p, s in d.get("bids") or []],
                               [(float(p), float(s)) for p, s in d.get("asks") or []], when, depth)

    async def get_fee_schedule(self) -> FeeSchedule:
        # TODO: Backpack serves the fee tier only to an authenticated account. Needed: FUNDARB_BACKPACK_API_KEY/SECRET and the
        # reference implementation's signing code (or Backpack's signing spec) to wire it without guessing.
        raise VenueUnavailable("backpack: fees are only served to an authenticated account; signing not wired yet")

    async def get_position(self, symbol: str) -> Position | None:
        raise NotImplementedError("backpack: needs an authenticated account (signing not wired yet)")

    async def get_account_balance(self) -> Balance:
        raise NotImplementedError("backpack: needs an authenticated account (signing not wired yet)")

    async def get_open_orders(self, symbol: str | None = None) -> list[OpenOrder]:
        raise NotImplementedError("backpack: needs an authenticated account (signing not wired yet)")
