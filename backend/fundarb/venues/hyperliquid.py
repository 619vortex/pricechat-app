"""Hyperliquid (reference venue for price: deepest book of the four).

Endpoints (all verified live, 2026-10-21; public, no auth):
- POST {url}/info {"type": "metaAndAssetCtxs"}            -> [meta.universe[], ctxs[]]: funding (current hour), markPx, oraclePx
- POST {url}/info {"type": "fundingHistory", "coin", "startTime"} -> settled rates with timestamps (the interval is derived
  from their spacing: hourly)
- POST {url}/info {"type": "l2Book", "coin"}               -> levels [bids best-first, asks best-first] (20 per side)
- POST {url}/info {"type": "userFees", "user"}             -> userCrossRate (taker) / userAddRate (maker) for that address
- POST {url}/info {"type": "clearinghouseState", "user"}   -> marginSummary.accountValue, withdrawable, assetPositions
- POST {url}/info {"type": "openOrders", "user"}           -> coin, side B/A, limitPx, sz, oid
- WS   {ws_url} subscribe {"type": "activeAssetCtx", "coin"} -> channel "activeAssetCtx", data.ctx like above
Account reads need only the account's public address (FUNDARB_HL_ACCOUNT_ADDRESS); orders (Phase 4) need an API wallet.
The index price is Hyperliquid's oracle price (oraclePx)."""

import asyncio
import contextlib
import json
import logging
import random
import time
from datetime import datetime, timezone
from typing import Any

from fundarb.models import Balance, FeeSchedule, FundingRate, OpenOrder, OrderBook, Position
from fundarb.symbols import canonical, hyperliquid_coin
from fundarb.venues.base import PerpVenue, VenueUnavailable, interval_from_timestamps

log = logging.getLogger("fundarb.venues.hyperliquid")
CTX_TTL = 5.0  # REST snapshot reuse window
WS_STALE = 30.0  # a streamed ctx older than this falls back to REST


class HyperliquidVenue(PerpVenue):
    name = "hyperliquid"

    def __init__(self, *a: Any, use_ws: bool = False, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self.use_ws = use_ws
        self._ws_ctx: dict[str, tuple[float, dict[str, Any]]] = {}  # coin -> (monotonic time, ctx)
        self._ws_task: asyncio.Task[None] | None = None
        self._ws_coins: set[str] = set()

    @property
    def base_url(self) -> str:
        return self.settings.hyperliquid_url

    async def _info(self, payload: dict[str, Any]) -> Any:
        return await self._request("POST", "/info", json=payload)

    async def ping(self) -> None:
        await self._snapshot(force=True)

    async def connect(self) -> None:
        await super().connect()
        if self.use_ws and self._ws_task is None:
            self._ws_task = asyncio.create_task(self._stream())

    async def disconnect(self) -> None:
        if self._ws_task is not None:
            self._ws_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._ws_task
            self._ws_task = None
        await super().disconnect()

    # --- snapshot (REST) and stream (WS) ------------------------------------------------------------------------------

    async def _snapshot(self, force: bool = False) -> dict[str, dict[str, Any]]:
        """coin -> asset ctx, from one metaAndAssetCtxs call (reused for CTX_TTL seconds)."""
        async def fetch() -> dict[str, dict[str, Any]]:
            data = await self._info({"type": "metaAndAssetCtxs"})
            try:
                meta, ctxs = data
                return {u["name"]: c for u, c in zip(meta["universe"], ctxs) if not u.get("isDelisted")}
            except (TypeError, ValueError, KeyError) as exc:
                raise VenueUnavailable(f"hyperliquid: unexpected metaAndAssetCtxs shape ({type(exc).__name__})") from None
        if force:
            return self._cache.put("snapshot", await fetch())
        return await self._cached("snapshot", CTX_TTL, fetch)

    async def _ctx(self, symbol: str) -> dict[str, Any]:
        coin = hyperliquid_coin(symbol)
        self._ws_coins.add(coin)
        hit = self._ws_ctx.get(coin)
        if hit and time.monotonic() - hit[0] < WS_STALE:
            return hit[1]
        ctx = (await self._snapshot()).get(coin)
        if ctx is None:
            raise VenueUnavailable(f"hyperliquid: no active perp {coin}")
        return ctx

    async def _stream(self) -> None:
        """activeAssetCtx for every coin asked about so far; reconnects with exponential backoff, forever, until cancelled."""
        import websockets  # only the stream needs it (optional dependency)

        attempt = 0
        while True:
            try:
                async with websockets.connect(self.settings.hyperliquid_ws_url, ping_interval=20) as ws:
                    subscribed: set[str] = set()
                    attempt = 0
                    while True:
                        for coin in self._ws_coins - subscribed:
                            await ws.send(json.dumps({"method": "subscribe", "subscription": {"type": "activeAssetCtx", "coin": coin}}))
                            subscribed.add(coin)
                        try:
                            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
                        except asyncio.TimeoutError:
                            continue
                        if msg.get("channel") == "activeAssetCtx":
                            d = msg.get("data") or {}
                            if d.get("coin") and isinstance(d.get("ctx"), dict):
                                self._ws_ctx[d["coin"]] = (time.monotonic(), d["ctx"])
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - any stream failure: back off and reconnect
                attempt += 1
                delay = min(60.0, 2 ** attempt) * (0.5 + random.random() / 2)
                log.warning("hyperliquid ws dropped (%s); reconnecting in %.1fs", type(exc).__name__, delay)
                await asyncio.sleep(delay)

    # --- market data ----------------------------------------------------------------------------------------------------

    async def symbols(self) -> list[str]:
        return sorted(canonical(c) for c in await self._snapshot())

    async def get_funding_interval_hours(self, symbol: str | None = None) -> float:
        coin = hyperliquid_coin(symbol) if symbol else "BTC"

        async def fetch() -> float:
            start = int((time.time() - 12 * 3600) * 1000)
            rows = await self._info({"type": "fundingHistory", "coin": coin, "startTime": start})
            return interval_from_timestamps([int(r["time"]) / 1000 for r in rows or []])
        return await self._cached(("interval", coin), self.settings.interval_refresh_seconds, fetch)

    async def funding_history(self, symbol: str, days: float = 7.0) -> list[tuple[float, float]]:
        start = int((time.time() - days * 86400) * 1000)
        rows = await self._info({"type": "fundingHistory", "coin": hyperliquid_coin(symbol), "startTime": start})
        return sorted((int(r["time"]) / 1000, float(r["fundingRate"])) for r in rows or [])

    async def get_funding_rate(self, symbol: str) -> FundingRate:
        ctx, hours = await self._ctx(symbol), await self.get_funding_interval_hours(symbol)
        return FundingRate(float(ctx["funding"]), hours, "derived: spacing of fundingHistory timestamps", "predicted",
                           datetime.now(timezone.utc))

    async def funding_board(self, symbols: list[str] | None = None) -> dict[str, FundingRate]:
        """Every perp from one metaAndAssetCtxs call. Hyperliquid settles all perps on one schedule, so the interval derived
        from BTC's funding history is used for each (one history call, cached)."""
        snap, hours, now = await self._snapshot(), await self.get_funding_interval_hours(), datetime.now(timezone.utc)
        out: dict[str, FundingRate] = {}
        for coin, ctx in snap.items():
            sym = canonical(coin)
            if ctx.get("funding") is None or (symbols is not None and sym not in symbols):
                continue
            out[sym] = FundingRate(float(ctx["funding"]), hours, "derived: spacing of fundingHistory timestamps (BTC)", "predicted", now)
        return out

    async def get_mark_price(self, symbol: str) -> float:
        return float((await self._ctx(symbol))["markPx"])

    async def get_index_price(self, symbol: str) -> float:
        return float((await self._ctx(symbol))["oraclePx"])

    async def get_orderbook(self, symbol: str, depth: int | None = None) -> OrderBook:
        data = await self._info({"type": "l2Book", "coin": hyperliquid_coin(symbol)})
        try:
            bids, asks = data["levels"]
        except (TypeError, KeyError, ValueError):
            raise VenueUnavailable(f"hyperliquid: no order book for {symbol}") from None
        ts = datetime.fromtimestamp(int(data.get("time", 0)) / 1000, tz=timezone.utc) if data.get("time") else None
        return OrderBook.build(self.name, symbol, [(float(l["px"]), float(l["sz"])) for l in bids],
                               [(float(l["px"]), float(l["sz"])) for l in asks], ts, depth)

    async def get_fee_schedule(self) -> FeeSchedule:
        """Your tier (FUNDARB_HL_ACCOUNT_ADDRESS: userAddRate / userCrossRate), else the published base tier from the same
        response's feeSchedule (add / cross): fetched from the venue either way, never assumed."""
        user = self.settings.hl_account_address

        async def fetch() -> FeeSchedule:
            d = await self._info({"type": "userFees", "user": user or "0x" + "0" * 40})
            try:
                if user:
                    return FeeSchedule(maker_bps=float(d["userAddRate"]) * 1e4, taker_bps=float(d["userCrossRate"]) * 1e4,
                                       source="api: userFees userAddRate/userCrossRate")
                base = d["feeSchedule"]
                return FeeSchedule(maker_bps=float(base["add"]) * 1e4, taker_bps=float(base["cross"]) * 1e4,
                                   source="api: userFees feeSchedule (published base tier; set FUNDARB_HL_ACCOUNT_ADDRESS for yours)")
            except (TypeError, KeyError, ValueError):
                raise VenueUnavailable("hyperliquid: userFees had no fee rates") from None
        return await self._cached("fees", self.settings.fee_refresh_seconds, fetch)

    # --- account ------------------------------------------------------------------------------------------------------

    def _user(self, what: str) -> str:
        if not self.settings.hl_account_address:
            raise VenueUnavailable(f"hyperliquid: set FUNDARB_HL_ACCOUNT_ADDRESS (your public address) to read {what}")
        return self.settings.hl_account_address

    async def get_position(self, symbol: str) -> Position | None:
        coin = hyperliquid_coin(symbol)
        state = await self._info({"type": "clearinghouseState", "user": self._user("positions")})
        for ap in state.get("assetPositions") or []:
            p = ap.get("position") or {}
            if p.get("coin") == coin and float(p.get("szi") or 0) != 0:
                return Position(symbol, float(p["szi"]), float(p["entryPx"]) if p.get("entryPx") else None,
                                float(p["unrealizedPnl"]) if p.get("unrealizedPnl") is not None else None)
        return None

    async def get_account_balance(self) -> Balance:
        state = await self._info({"type": "clearinghouseState", "user": self._user("the balance")})
        ms = state.get("marginSummary") or {}
        if "accountValue" not in ms:
            raise VenueUnavailable("hyperliquid: clearinghouseState had no marginSummary.accountValue")
        return Balance(float(ms["accountValue"]), float(state["withdrawable"]) if state.get("withdrawable") is not None else None)

    async def get_open_orders(self, symbol: str | None = None) -> list[OpenOrder]:
        rows = await self._info({"type": "openOrders", "user": self._user("open orders")})
        coin = hyperliquid_coin(symbol) if symbol else None
        return [OpenOrder(canonical(r["coin"]), str(r["oid"]), "buy" if r["side"] == "B" else "sell", float(r["limitPx"]), float(r["sz"]))
                for r in rows or [] if coin is None or r.get("coin") == coin]
