"""Orderly Network.

Public endpoints (verified live, 2026-10-21; no auth), base {url}:
- GET /v1/public/info                       -> rows[]: symbol, funding_period (HOURS), base_tick, min_notional ...
- GET /v1/public/info/{symbol}              -> the same for one symbol
- GET /v1/public/funding_rate/{symbol}      -> est_funding_rate (running interval), last_funding_rate, next_funding_time
- GET /v1/public/futures/{symbol}           -> mark_price, index_price, status
Funding interval: the `funding_period` field of /v1/public/info (8 on 2026-10-21).

Authenticated (Orderly key: ed25519, headers orderly-account-id / orderly-key / orderly-signature / orderly-timestamp):
- GET /v1/orderbook/{symbol}                -> answered "orderly-account-id header is empty" without auth (verified)
- fees (/v1/client/info), positions, balance, orders: not wired. The signing scheme isn't implemented here because it
  wasn't verified against a real account; those methods raise VenueUnavailable/NotImplementedError naming what's needed."""

from datetime import datetime, timezone
from typing import Any

from fundarb.models import Balance, FeeSchedule, FundingRate, OpenOrder, OrderBook, Position
from fundarb.symbols import base_of, canonical
from fundarb.venues.base import PerpVenue, VenueError, VenueUnavailable

MARKETS_TTL = 3600.0


def orderly_symbol(symbol: str) -> str:
    return f"PERP_{base_of(symbol)}_USDC"


class OrderlyVenue(PerpVenue):
    name = "orderly"

    @property
    def base_url(self) -> str:
        return self.settings.orderly_url

    async def _get(self, path: str, **params: Any) -> Any:
        data = await self._request("GET", path, params=params or None)
        if not isinstance(data, dict) or not data.get("success"):
            msg = data.get("message") if isinstance(data, dict) else str(data)[:200]
            raise VenueError(f"orderly: {path} -> {msg}")
        return data.get("data")

    async def ping(self) -> None:
        await self._markets(force=True)

    async def _markets(self, force: bool = False) -> dict[str, dict[str, Any]]:
        """canonical symbol -> /v1/public/info row (USDC perps; broker-specific listings like PERP_X_USDC_mythos are skipped)."""
        async def fetch() -> dict[str, dict[str, Any]]:
            rows = (await self._get("/v1/public/info") or {}).get("rows") or []
            out = {}
            for r in rows:
                parts = str(r.get("symbol", "")).split("_")
                if len(parts) == 3 and parts[0] == "PERP" and parts[2] == "USDC":
                    out[canonical(parts[1])] = r
            return out
        if force:
            return self._cache.put("markets", await fetch())
        return await self._cached("markets", MARKETS_TTL, fetch)

    async def _market(self, symbol: str) -> dict[str, Any]:
        m = (await self._markets()).get(canonical(base_of(symbol)))
        if m is None:
            raise VenueUnavailable(f"orderly: no USDC perp {orderly_symbol(symbol)}")
        return m

    async def symbols(self) -> list[str]:
        return sorted(await self._markets())

    async def get_funding_interval_hours(self, symbol: str | None = None) -> float:
        m = await self._market(symbol or "BTC-PERP")
        hours = m.get("funding_period")
        if not isinstance(hours, (int, float)) or hours <= 0:
            raise VenueUnavailable(f"orderly: /v1/public/info has no usable funding_period for {m.get('symbol')}")
        return float(hours)

    async def _native(self, symbol: str) -> str:
        return str((await self._market(symbol))["symbol"])

    async def funding_history(self, symbol: str, days: float = 7.0) -> list[tuple[float, float]]:
        """GET /v1/public/funding_rate_history (start_t / end_t in MILLISECONDS; verified: seconds return nothing)."""
        import time

        now_ms = int(time.time() * 1000)
        d = await self._get("/v1/public/funding_rate_history", symbol=await self._native(symbol), start_t=now_ms - int(days * 86_400_000),
                            end_t=now_ms, page=1, size=500)
        return sorted((r["funding_rate_timestamp"] / 1000, float(r["funding_rate"])) for r in (d or {}).get("rows") or [])

    async def funding_cap(self, symbol: str) -> tuple[float, float] | None:
        """(floor, cap) per interval from /v1/public/info (floor_funding / cap_funding), or None."""
        m = await self._market(symbol)
        lo, hi = m.get("floor_funding"), m.get("cap_funding")
        return (float(lo), float(hi)) if isinstance(lo, (int, float)) and isinstance(hi, (int, float)) else None

    async def _futures(self, symbol: str) -> dict[str, Any]:
        sym = await self._native(symbol)
        return await self._cached(("futures", sym), 5.0, lambda: self._get(f"/v1/public/futures/{sym}"))

    async def get_funding_rate(self, symbol: str) -> FundingRate:
        d = await self._get(f"/v1/public/funding_rate/{await self._native(symbol)}")
        if d.get("est_funding_rate") is None:
            raise VenueUnavailable(f"orderly: no est_funding_rate for {symbol}")
        at = d.get("est_funding_rate_timestamp")
        return FundingRate(float(d["est_funding_rate"]), await self.get_funding_interval_hours(symbol), "api: /v1/public/info funding_period",
                           "predicted", datetime.fromtimestamp(at / 1000, tz=timezone.utc) if at else datetime.now(timezone.utc))

    async def funding_board(self, symbols: list[str] | None = None) -> dict[str, FundingRate]:
        """All USDC perps from one /v1/public/funding_rates call, each with its own funding_period."""
        markets = await self._markets()
        rows = (await self._get("/v1/public/funding_rates") or {}).get("rows") or []
        by_native = {m["symbol"]: s for s, m in markets.items()}
        out: dict[str, FundingRate] = {}
        for r in rows:
            sym = by_native.get(r.get("symbol"))
            hours = (markets.get(sym) or {}).get("funding_period") if sym else None
            if sym is None or r.get("est_funding_rate") is None or not isinstance(hours, (int, float)) or hours <= 0:
                continue
            if symbols is not None and sym not in symbols:
                continue
            at = r.get("est_funding_rate_timestamp")
            out[sym] = FundingRate(float(r["est_funding_rate"]), float(hours), "api: /v1/public/info funding_period", "predicted",
                                   datetime.fromtimestamp(at / 1000, tz=timezone.utc) if at else datetime.now(timezone.utc))
        return out

    async def get_mark_price(self, symbol: str) -> float:
        return float((await self._futures(symbol))["mark_price"])

    async def get_index_price(self, symbol: str) -> float:
        return float((await self._futures(symbol))["index_price"])

    async def get_orderbook(self, symbol: str, depth: int | None = None) -> OrderBook:
        # TODO: Orderly serves its order book only to an authenticated account (verified: "orderly-account-id header is empty").
        # Needed: FUNDARB_ORDERLY_ACCOUNT_ID + an Orderly key/secret, and the request-signing scheme confirmed on that account.
        raise VenueUnavailable("orderly: the order book needs an authenticated Orderly account (FUNDARB_ORDERLY_ACCOUNT_ID + key/secret); "
                               "signing not wired yet")

    async def get_fee_schedule(self) -> FeeSchedule:
        raise VenueUnavailable("orderly: fees are only served to an authenticated account (/v1/client/info); signing not wired yet")

    async def get_position(self, symbol: str) -> Position | None:
        raise NotImplementedError("orderly: needs an authenticated Orderly account (signing not wired yet)")

    async def get_account_balance(self) -> Balance:
        raise NotImplementedError("orderly: needs an authenticated Orderly account (signing not wired yet)")

    async def get_open_orders(self, symbol: str | None = None) -> list[OpenOrder]:
        raise NotImplementedError("orderly: needs an authenticated Orderly account (signing not wired yet)")
