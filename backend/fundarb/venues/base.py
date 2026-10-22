"""PerpVenue: the one interface every venue adapter implements (Module 1), plus shared plumbing: an HTTP session with
retry + exponential backoff, TTL caches for slow-changing data (fees, funding intervals, markets) and the order gate.

Errors are explicit: a venue that can't answer raises VenueUnavailable with the reason (never a silent zero). Orders go
through `_order_gate`, which refuses unless LIVE_TRADING and LIVE_CONFIRM_TOKEN are both set; Phase 1 adapters then refuse
anyway (NotImplementedError: execution is Phase 4)."""

import asyncio
import logging
import random
import statistics
import time
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import httpx

from fundarb.config import Settings
from fundarb.models import Balance, FeeSchedule, FundingRate, OpenOrder, OrderBook, Position, VenueQuote, utcnow

log = logging.getLogger("fundarb.venues")
T = TypeVar("T")


class VenueError(Exception):
    """The venue answered with an error, or something unparseable."""


class VenueUnavailable(VenueError):
    """The venue can't give this right now, or not without credentials; the message says which and why."""


class TradingDisabled(Exception):
    """Order placement is gated off (Phase 1-3, or LIVE_TRADING / LIVE_CONFIRM_TOKEN not both set)."""


class TTLCache:
    def __init__(self) -> None:
        self._d: dict[Any, tuple[float, Any]] = {}

    def get(self, key: Any, ttl: float) -> Any | None:
        hit = self._d.get(key)
        return hit[1] if hit and time.monotonic() - hit[0] < ttl else None

    def put(self, key: Any, value: Any) -> Any:
        self._d[key] = (time.monotonic(), value)
        return value


async def with_backoff(fn: Callable[[], Awaitable[T]], attempts: int = 4, base: float = 0.5, cap: float = 8.0,
                       sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> T:
    """Retries transport errors, 429 and 5xx with exponential backoff + jitter. 4xx (other than 429) is not retried."""
    for i in range(attempts):
        try:
            return await fn()
        except httpx.TransportError:
            if i == attempts - 1:
                raise
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            if (code != 429 and code < 500) or i == attempts - 1:
                raise
        await sleep(min(cap, base * 2 ** i) * (0.5 + random.random() / 2))
    raise AssertionError("unreachable")


def interval_from_timestamps(ts_seconds: list[float]) -> float:
    """The venue's settlement cadence in hours, from the spacing of its own funding-history timestamps (median, so one gap
    or duplicate doesn't change it), rounded to the nearest quarter hour."""
    ts = sorted(set(ts_seconds))
    if len(ts) < 3:
        raise VenueUnavailable("not enough funding history to tell the funding interval")
    gaps = [b - a for a, b in zip(ts, ts[1:])]
    return round(statistics.median(gaps) / 3600 * 4) / 4


class PerpVenue(ABC):
    name: str = "venue"

    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.settings = settings
        self._transport = transport
        self._client: httpx.AsyncClient | None = None
        self._cache = TTLCache()
        self.connected = False

    # --- lifecycle --------------------------------------------------------------------------------------------------------

    @property
    @abstractmethod
    def base_url(self) -> str: ...

    async def connect(self) -> None:
        """Opens the HTTP session and proves the venue answers (with backoff); subclasses may start streams too."""
        if self._client is None:
            self._client = httpx.AsyncClient(base_url=self.base_url, timeout=self.settings.http_timeout, transport=self._transport,
                                             headers={"User-Agent": "fundarb/0.1 (read-only)", "Accept": "application/json"})
        await with_backoff(self.ping)
        self.connected = True
        log.info("connected", extra={"venue": self.name})

    async def disconnect(self) -> None:
        self.connected = False
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> "PerpVenue":
        await self.connect()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.disconnect()

    @abstractmethod
    async def ping(self) -> None:
        """A cheap public call that proves the venue is reachable."""

    # --- HTTP -------------------------------------------------------------------------------------------------------------

    async def _request(self, method: str, path: str, **kw: Any) -> Any:
        if self._client is None:
            raise VenueUnavailable(f"{self.name}: not connected (call connect() first)")

        async def go() -> Any:
            r = await self._client.request(method, path, **kw)  # type: ignore[union-attr]
            r.raise_for_status()
            return r.json()
        try:
            return await with_backoff(go)
        except httpx.HTTPStatusError as exc:
            body = exc.response.text[:300]
            raise VenueError(f"{self.name}: {method} {path} -> HTTP {exc.response.status_code}: {body}") from None
        except (httpx.TransportError, ValueError) as exc:
            raise VenueUnavailable(f"{self.name}: {method} {path} failed ({type(exc).__name__})") from None

    async def _cached(self, key: Any, ttl: float, fetch: Callable[[], Awaitable[T]]) -> T:
        hit = self._cache.get(key, ttl)
        if hit is not None:
            return hit
        return self._cache.put(key, await fetch())

    # --- market data (public) -----------------------------------------------------------------------------------------

    @abstractmethod
    async def symbols(self) -> list[str]:
        """Canonical symbols of the venue's active perps."""

    @abstractmethod
    async def get_funding_rate(self, symbol: str) -> FundingRate: ...

    @abstractmethod
    async def get_funding_interval_hours(self, symbol: str | None = None) -> float: ...

    @abstractmethod
    async def funding_history(self, symbol: str, days: float = 7.0) -> list[tuple[float, float]]:
        """The venue's settled funding for the last `days`: [(settlement time in unix seconds, rate per interval)], oldest
        first. Rates are signed like FundingRate.rate_raw (positive: longs pay)."""

    @abstractmethod
    async def get_mark_price(self, symbol: str) -> float: ...

    @abstractmethod
    async def get_index_price(self, symbol: str) -> float: ...

    @abstractmethod
    async def get_orderbook(self, symbol: str, depth: int | None = None) -> OrderBook: ...

    @abstractmethod
    async def get_fee_schedule(self) -> FeeSchedule:
        """maker/taker in bps (per symbol where the venue differs). Raises VenueUnavailable when the venue only tells an
        authenticated account; fee tiers are never assumed."""

    # --- account (authenticated) --------------------------------------------------------------------------------------

    @abstractmethod
    async def get_position(self, symbol: str) -> Position | None: ...

    @abstractmethod
    async def get_account_balance(self) -> Balance: ...

    @abstractmethod
    async def get_open_orders(self, symbol: str | None = None) -> list[OpenOrder]: ...

    # --- orders (Phase 4) -----------------------------------------------------------------------------------------------

    def _order_gate(self) -> None:
        if not self.settings.live_armed:
            raise TradingDisabled(f"{self.name}: orders are disabled (needs LIVE_TRADING=true and LIVE_CONFIRM_TOKEN)")
        raise NotImplementedError(f"{self.name}: order execution is Phase 4 and not built yet")

    async def place_order(self, symbol: str, side: str, size: float, price: float | None = None, *, post_only: bool = True,
                          reduce_only: bool = False, client_id: str | None = None) -> str:
        self._order_gate()
        raise AssertionError("unreachable")

    async def cancel_order(self, symbol: str, order_id: str) -> None:
        self._order_gate()

    async def cancel_all(self, symbol: str | None = None) -> None:
        self._order_gate()

    # --- the normalized quote -----------------------------------------------------------------------------------------

    async def quote(self, symbol: str) -> VenueQuote:
        """Everything Module 2 needs for one symbol. Fees that need credentials become None with the reason in fee_source;
        any other missing piece raises."""
        funding, mark, index, book = await asyncio.gather(self.get_funding_rate(symbol), self.get_mark_price(symbol),
                                                          self.get_index_price(symbol), self.get_orderbook(symbol, self.settings.book_depth))
        try:
            fees = await self.get_fee_schedule()
            pair, source = fees.for_symbol(symbol), fees.source
        except VenueUnavailable as exc:
            pair, source = None, f"unavailable: {exc}"
        return VenueQuote.build(self.name, symbol, funding, mark, index, book, pair, source, utcnow())

    async def funding_board(self, symbols: list[str] | None = None) -> dict[str, FundingRate]:
        """Funding for many symbols at once (the venue's active perps, or just `symbols`). Venues with a bulk endpoint
        override this; the default asks one symbol at a time and leaves out the ones that fail."""
        wanted = symbols if symbols is not None else await self.symbols()
        listed = set(await self.symbols())
        out: dict[str, FundingRate] = {}
        for s in wanted:
            if s not in listed:
                continue
            try:
                out[s] = await self.get_funding_rate(s)
            except VenueError:
                continue
        return out
