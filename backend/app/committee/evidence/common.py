"""Shared plumbing for the evidence pack: the error type, references, short TTL caches, the per-provider health record
that /committee status reads, and size-capped HTTP reads.

Honest by construction: a provider that fails records the failure and its section says "unavailable" with the reason;
nothing here ever substitutes a default number for a missing one."""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Any

import httpx

TIMEOUT = 8.0
UA = {"User-Agent": "PriceChat/1.0 (+https://pricechat.app)"}
MAX_BYTES = 2_000_000  # RSS and JSON bodies above this are refused (XML bombs, runaway responses)
PROVIDERS = ("hyperliquid", "venues", "coingecko", "news", "fear_greed", "community")
NO_HISTORY = "no point-in-time history (backtest)"


class EvidenceError(Exception):
    """The pack can't be built. code: unknown_symbol | ambiguous | no_data | stale. `choices` lists the candidates when
    a DEX ticker is shared by several tokens (each {"address", "label", "pool", "liquidity_usd"})."""

    def __init__(self, code: str, message: str, choices: list[dict[str, Any]] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.choices = choices or []


class FetchError(Exception):
    """One source couldn't be read; the text says why (shown as a limitation, never as data)."""


def iso(d: datetime | float | int | None) -> str | None:
    """UTC ISO-8601 with a Z. Accepts datetimes and unix seconds."""
    if d is None:
        return None
    if not isinstance(d, datetime):
        d = datetime.fromtimestamp(float(d), tz=timezone.utc)
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def aware(d: datetime) -> datetime:
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def rnd(x: float | None, sig: int = 6) -> float | None:
    """Round to `sig` significant figures (prices of any size keep their precision); None stays None."""
    if x is None:
        return None
    if x == 0:
        return 0.0
    from math import floor, log10

    return round(x, max(0, sig - 1 - floor(log10(abs(x)))))


# ---------------------------------------------------------------- references
class Refs:
    """Numbered references (T1, F1, N1, S1, P1...) for one pack: every fact names where it came from."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self._n: dict[str, int] = {}

    def add(self, prefix: str, source: str, url: str | None, ts: str | None, fetched_at: str | None) -> str:
        self._n[prefix] = self._n.get(prefix, 0) + 1
        rid = f"{prefix}{self._n[prefix]}"
        self.rows.append({"id": rid, "source": source, "url": url, "ts": ts, "fetched_at": fetched_at})
        return rid


# ---------------------------------------------------------------- caches + provider health
_lock = threading.Lock()
_cache: dict[str, tuple[float, Any]] = {}
_health: dict[str, dict[str, Any]] = {}  # name -> {"ok", "checked", "last_ok", "detail"}


def cache_get(key: str, ttl: float) -> Any:
    with _lock:
        hit = _cache.get(key)
    return hit[1] if hit and time.time() - hit[0] < ttl else None


def cache_put(key: str, value: Any) -> Any:
    with _lock:
        if len(_cache) > 400:
            _cache.clear()
        _cache[key] = (time.time(), value)
    return value


def mark(name: str, ok: bool, detail: str = "") -> None:
    """Record a provider's latest answer (or failure) for /committee status."""
    now = time.time()
    with _lock:
        h = _health.setdefault(name, {"ok": False, "checked": None, "last_ok": None, "detail": ""})
        h.update(ok=ok, checked=now, detail=detail[:200])
        if ok:
            h["last_ok"] = now


def health() -> dict[str, dict[str, Any]]:
    with _lock:
        return {k: dict(v) for k, v in _health.items()}


def reset() -> None:
    """Tests: forget caches and health."""
    with _lock:
        _cache.clear()
        _health.clear()


# ---------------------------------------------------------------- HTTP
def client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=TIMEOUT, headers=UA, follow_redirects=True)


async def read_capped(http: httpx.AsyncClient, method: str, url: str, *, what: str, **kw: Any) -> bytes:
    """The body of one request, refusing anything above MAX_BYTES. Raises FetchError with a short reason."""
    try:
        async with http.stream(method, url, **kw) as r:
            if r.status_code == 429:
                raise FetchError(f"{what} is rate-limiting us")
            if r.status_code in (403, 451):
                raise FetchError(f"{what} blocks requests from this server")
            if r.status_code != 200:
                raise FetchError(f"{what} answered {r.status_code}")
            declared = r.headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > MAX_BYTES:
                raise FetchError(f"{what} sent more than {MAX_BYTES // 1_000_000} MB")
            buf = bytearray()
            async for chunk in r.aiter_bytes():
                buf += chunk
                if len(buf) > MAX_BYTES:
                    raise FetchError(f"{what} sent more than {MAX_BYTES // 1_000_000} MB")
            return bytes(buf)
    except httpx.HTTPError as e:
        raise FetchError(f"{what} couldn't be reached ({type(e).__name__})") from e


async def get_json(http: httpx.AsyncClient, url: str, *, what: str, **kw: Any) -> Any:
    import json

    body = await read_capped(http, "GET", url, what=what, **kw)
    try:
        return json.loads(body)
    except ValueError as e:
        raise FetchError(f"{what} sent something that isn't JSON") from e


async def post_json(http: httpx.AsyncClient, url: str, payload: dict[str, Any], *, what: str) -> Any:
    import json

    body = await read_capped(http, "POST", url, what=what, json=payload)
    try:
        return json.loads(body)
    except ValueError as e:
        raise FetchError(f"{what} sent something that isn't JSON") from e
