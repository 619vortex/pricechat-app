"""Lighter (zk-rollup perp DEX).

Endpoints (all verified live, 2026-10-21; public, no auth), base {url}/api/v1:
- GET /orderBooks                                   -> symbol, market_id, market_type, status, taker_fee, maker_fee (percent)
- GET /orderBookDetails?market_id=                  -> mark_price, index_price, last_trade_price
- GET /fundings?market_id=&resolution=1h&start_timestamp=&end_timestamp=&count_back=
                                                    -> settled funding: timestamp (s), rate (PERCENT), direction long|short
- GET /orderBookOrders?market_id=&limit=<=250       -> resting orders per side (one row per order: merged by price here)

Funding interval: derived from the spacing of the venue's own settled-funding timestamps. On 2026-10-21 that spacing was
ONE HOUR, which contradicts the brief's "every 8 hours"; the code reports what the venue does, see README "Uncertain".
Rate units: `rate` is read as a percent (0.0012 -> 0.000012 per interval) and signed by `direction` ("long": longs pay).
The latest settled rate is used (kind "last_settled"): no public predicted-rate field was found.

Account data (positions, balance, open orders) and orders need Lighter's authenticated API / lighter-sdk==1.1.6 (Phase 4);
those methods raise NotImplementedError until the account endpoints are confirmed."""

import time
from datetime import datetime, timezone
from typing import Any

from fundarb.models import Balance, FeeSchedule, FundingRate, OpenOrder, OrderBook, Position
from fundarb.symbols import base_of, canonical
from fundarb.venues.base import PerpVenue, VenueError, VenueUnavailable, interval_from_timestamps

MARKETS_TTL = 3600.0


class LighterVenue(PerpVenue):
    name = "lighter"

    @property
    def base_url(self) -> str:
        return self.settings.lighter_url.rstrip("/") + "/api/v1"

    async def _get(self, path: str, **params: Any) -> Any:
        data = await self._request("GET", path, params=params or None)
        if isinstance(data, dict) and data.get("code") not in (None, 200):
            raise VenueError(f"lighter: {path} -> code {data.get('code')}: {data.get('message', '')}")
        return data

    async def ping(self) -> None:
        await self._markets(force=True)

    async def _markets(self, force: bool = False) -> dict[str, dict[str, Any]]:
        """canonical symbol -> orderBooks row, active perps only."""
        async def fetch() -> dict[str, dict[str, Any]]:
            rows = (await self._get("/orderBooks")).get("order_books") or []
            return {canonical(r["symbol"]): r for r in rows if r.get("market_type") == "perp" and r.get("status") == "active"}
        if force:
            return self._cache.put("markets", await fetch())
        return await self._cached("markets", MARKETS_TTL, fetch)

    async def _market_id(self, symbol: str) -> int:
        m = (await self._markets()).get(canonical(base_of(symbol)))
        if m is None:
            raise VenueUnavailable(f"lighter: no active perp {symbol}")
        return int(m["market_id"])

    async def symbols(self) -> list[str]:
        return sorted(await self._markets())

    async def _details(self, symbol: str) -> dict[str, Any]:
        mid = await self._market_id(symbol)

        async def fetch() -> dict[str, Any]:
            rows = (await self._get("/orderBookDetails", market_id=mid)).get("order_book_details") or []
            if not rows:
                raise VenueUnavailable(f"lighter: no details for {symbol}")
            return rows[0]
        return await self._cached(("details", mid), 5.0, fetch)

    async def _fundings(self, symbol: str) -> list[dict[str, Any]]:
        mid, now = await self._market_id(symbol), int(time.time())

        async def fetch() -> list[dict[str, Any]]:
            rows = (await self._get("/fundings", market_id=mid, resolution="1h", start_timestamp=now - 24 * 3600, end_timestamp=now,
                                    count_back=24)).get("fundings") or []
            return sorted(rows, key=lambda r: int(r["timestamp"]))
        return await self._cached(("fundings", mid), 60.0, fetch)

    async def get_funding_interval_hours(self, symbol: str | None = None) -> float:
        sym = symbol or "BTC-PERP"

        async def fetch() -> float:
            return interval_from_timestamps([int(r["timestamp"]) for r in await self._fundings(sym)])
        return await self._cached(("interval", sym), self.settings.interval_refresh_seconds, fetch)

    async def funding_history(self, symbol: str, days: float = 7.0) -> list[tuple[float, float]]:
        now = int(time.time())
        rows = (await self._get("/fundings", market_id=await self._market_id(symbol), resolution="1h", start_timestamp=int(now - days * 86400),
                                end_timestamp=now, count_back=int(days * 24) + 2)).get("fundings") or []
        sign = lambda r: 1.0 if str(r.get("direction", "long")).lower() == "long" else -1.0  # noqa: E731
        return sorted((float(r["timestamp"]), sign(r) * float(r["rate"]) / 100) for r in rows)

    async def get_funding_rate(self, symbol: str) -> FundingRate:
        rows = await self._fundings(symbol)
        if not rows:
            raise VenueUnavailable(f"lighter: no settled funding for {symbol} in the last 24 h")
        last = rows[-1]
        sign = 1.0 if str(last.get("direction", "long")).lower() == "long" else -1.0
        return FundingRate(sign * float(last["rate"]) / 100, await self.get_funding_interval_hours(symbol),
                           "derived: spacing of /fundings timestamps", "last_settled",
                           datetime.fromtimestamp(int(last["timestamp"]), tz=timezone.utc))

    async def get_mark_price(self, symbol: str) -> float:
        return float((await self._details(symbol))["mark_price"])

    async def get_index_price(self, symbol: str) -> float:
        return float((await self._details(symbol))["index_price"])

    async def get_orderbook(self, symbol: str, depth: int | None = None) -> OrderBook:
        data = await self._get("/orderBookOrders", market_id=await self._market_id(symbol), limit=min(250, max(1, depth or 250)))
        row = lambda o: (float(o["price"]), float(o["remaining_base_amount"]))  # noqa: E731
        return OrderBook.build(self.name, symbol, [row(o) for o in data.get("bids") or []], [row(o) for o in data.get("asks") or []],
                               depth=depth)

    async def get_fee_schedule(self) -> FeeSchedule:
        async def fetch() -> FeeSchedule:
            per = {s: (float(m["maker_fee"]) * 100, float(m["taker_fee"]) * 100) for s, m in (await self._markets()).items()}
            if not per:
                raise VenueUnavailable("lighter: no markets to read fees from")
            maker, taker = per.get("BTC-PERP", next(iter(per.values())))
            return FeeSchedule(maker, taker, "api: /orderBooks maker_fee/taker_fee (percent)", per)
        return await self._cached("fees", self.settings.fee_refresh_seconds, fetch)

    # TODO(phase 4): Lighter's account endpoints and lighter-sdk==1.1.6 signer. Needed from the operator: the account index,
    # an API key for it, and confirmation of the account/position response fields.
    async def get_position(self, symbol: str) -> Position | None:
        raise NotImplementedError("lighter: account endpoints not wired yet (needs FUNDARB_LIGHTER_ACCOUNT_INDEX + confirmed fields)")

    async def get_account_balance(self) -> Balance:
        raise NotImplementedError("lighter: account endpoints not wired yet (needs FUNDARB_LIGHTER_ACCOUNT_INDEX + confirmed fields)")

    async def get_open_orders(self, symbol: str | None = None) -> list[OpenOrder]:
        raise NotImplementedError("lighter: account endpoints not wired yet (needs FUNDARB_LIGHTER_ACCOUNT_INDEX + confirmed fields)")
