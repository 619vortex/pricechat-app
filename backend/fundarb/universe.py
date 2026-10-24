"""Module 1: universe discovery. Every Hyperliquid perp (the native dex plus every HIP-3 builder dex), classified, with the
per-market context read live. Read-only: nothing here can place an order.

Endpoints (POST {hyperliquid_url}/info; all public, verified live 2026-10-24):
- {"type": "perpDexs"}                    -> [null (the native dex), {name, fullName, deployer, assetToStreamingOiCap, ...}, ...]
- {"type": "perpCategories"}              -> [[coin, category], ...]: Hyperliquid's own HIP-3 registry of what each builder
                                             market is ("stocks", "indices", "commodities", "fx", "preipo", "crypto", "rates")
- {"type": "metaAndAssetCtxs", "dex": d}  -> [meta {universe[], collateralToken}, ctxs[]] for one dex ("" = native)
- {"type": "fundingHistory", "coin", "startTime"} -> settled rates; the spacing of their timestamps is the dex's interval
- {"type": "spotMeta"}                    -> tokens[] (index -> name: the collateral token each dex margins in)
- {"type": "perpsAtOpenInterestCap"}      -> native coins currently at their OI cap
One batched pass is 4 + 2 x (number of dexes) calls (the interval reads are cached for interval_refresh_seconds); nothing
is polled per market.

Schema drift guard: the field names every endpoint returned are hashed. When the hash differs from the one a human last
confirmed (universe_schema_path), the snapshot says so and `ranking_allowed` is False until `fundarb universe
--accept-schema` records the new one. A silent rename would otherwise turn into a column of zeroes."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

import httpx

from fundarb.capital import market_bucket
from fundarb.config import Settings
from fundarb.models import HOURS_PER_YEAR
from fundarb.venues.base import VenueUnavailable, interval_from_timestamps, with_backoff

log = logging.getLogger("fundarb.universe")
DEX_CONCURRENCY = 4
INTERVAL_LOOKBACK_HOURS = 12  # enough settlements to read an hourly or 8-hourly cadence


class AssetClass(str, Enum):
    CRYPTO = "CRYPTO"
    EQUITY = "EQUITY"
    INDEX = "INDEX"
    COMMODITY = "COMMODITY"
    FX = "FX"
    PREIPO = "PREIPO"
    RATES = "RATES"  # the registry's "rates" (treasury yields): not in the original brief's list, kept as its own class
    UNCLASSIFIED = "UNCLASSIFIED"  # a builder market the registry doesn't list: never guessed from its name


# perpCategories value -> class. Matched case-insensitively: the live registry writes both "stocks" and "stock", "fx" and "FX".
REGISTRY_CLASS: dict[str, AssetClass] = {
    "crypto": AssetClass.CRYPTO, "stocks": AssetClass.EQUITY, "stock": AssetClass.EQUITY, "indices": AssetClass.INDEX,
    "commodities": AssetClass.COMMODITY, "fx": AssetClass.FX, "preipo": AssetClass.PREIPO, "rates": AssetClass.RATES,
}


Override = dict[str, Any]  # {"class": "EQUITY", "pairing": bool, "reason": str, "decided": str}


def load_overrides(path: Path) -> dict[str, Override]:
    """The operator's classification decisions (classification_overrides.json). A malformed file fails fast: an override
    that silently didn't apply would put an excluded market back in the pairing pool."""
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, Override] = {}
    for name, o in (data.get("markets") or {}).items():
        AssetClass(o["class"])  # raises on an unknown class
        if not str(o.get("reason") or "").strip():
            raise ValueError(f"{path}: override for {name} needs a reason")
        out[name] = {"class": o["class"], "pairing": bool(o.get("pairing", True)), "reason": o["reason"], "decided": o.get("decided", "")}
    return out


def classify(namespace: str, raw_name: str, categories: dict[str, str], overrides: dict[str, Override] | None = None) -> tuple[AssetClass, str]:
    """(class, the rule that decided it). Exactly one class per market."""
    o = (overrides or {}).get(raw_name)
    if o is not None:
        # Rule 0: an operator decision (classification_overrides.json), with its stated reason.
        return AssetClass(o["class"]), f"override: {o['reason']}"
    if namespace == "":
        # Rule 1: the native dex lists only crypto perps (HIP-1 governance listings); the registry has no native entries.
        return AssetClass.CRYPTO, "native dex"
    cat = categories.get(raw_name)
    if cat is None:
        # Rule 2b: a builder market missing from Hyperliquid's registry stays unclassified (reported, never guessed).
        return AssetClass.UNCLASSIFIED, "not in perpCategories"
    cls = REGISTRY_CLASS.get(cat.strip().lower())
    if cls is None:
        # Rule 2c: a registry category this code doesn't know yet: unclassified until it is mapped.
        return AssetClass.UNCLASSIFIED, f"unknown registry category {cat!r}"
    # Rule 2a: builder market -> Hyperliquid's perpCategories registry.
    return cls, f"perpCategories={cat}"


def pairing_rule(raw_name: str, cls: AssetClass, overrides: dict[str, Override] | None = None) -> tuple[bool, str]:
    """Whether the market may enter the funding-arb pairing pool at all (the gates and pair selection come after)."""
    o = (overrides or {}).get(raw_name)
    if o is not None and not o["pairing"]:
        return False, f"excluded by override: {o['reason']}"
    if cls is AssetClass.UNCLASSIFIED:
        return False, "unclassified: excluded until the registry or an override classifies it"
    return True, "eligible"


@dataclass(frozen=True)
class Market:
    raw_name: str  # "BTC", "xyz:XYZ100", "xyz:TSLA"
    namespace: str  # "" for native, else the builder dex ("xyz", "flx", ...)
    builder: str | None  # the dex's fullName from perpDexs; None for native
    asset_class: AssetClass
    class_rule: str
    collateral_token: str  # what this dex margins in (USDC, USDH, USDe, USDT0, ...)
    funding_rate_raw: float | None  # per interval, signed: positive = longs pay shorts (ctx.funding, the running interval)
    funding_interval_hours: float | None  # read from the dex's own funding history; None if it couldn't be read
    funding_interval_source: str
    funding_rate_hourly: float | None
    funding_apr: float | None  # fraction per year (0.10 = 10%)
    premium: float | None
    oracle_price: float | None
    mark_price: float | None
    mark_oracle_basis_bps: float | None
    open_interest_usd: float | None  # ctx.openInterest (base units) x oracle price
    volume_24h_usd: float | None  # ctx.dayNtlVlm
    max_leverage: float  # meta.universe[].maxLeverage, live
    only_isolated: bool
    margin_mode: str  # meta.universe[].marginMode ("strictIsolated", "noCross") or "unset" when the field is absent
    spread_bps: float | None  # from ctx.impactPxs (impact bid/ask): an impact spread, not top of book
    spread_source: str
    depth_usd_1pct: float | None  # needs the order book: filled in Phase B for gate candidates only, None here
    listing_live: bool  # not isDelisted
    oi_cap_usd: float | None  # builder dex: perpDexs.assetToStreamingOiCap; native: not published as a number
    at_oi_cap: bool | None  # native: perpsAtOpenInterestCap; builder: None (not published by that endpoint)
    timestamp_utc: datetime
    pairing_eligible: bool = True
    pairing_note: str = "eligible"
    capital_bucket: str = ""  # fundarb.capital: "cross:<collateral>" or "isolated:<collateral>"


@dataclass
class SchemaCheck:
    status: str  # "ok" (matches the confirmed hash) | "new" (nothing confirmed yet) | "changed"
    hash: str
    confirmed_hash: str | None
    parts: dict[str, list[str]]
    added: dict[str, list[str]] = field(default_factory=dict)
    removed: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class UniverseSnapshot:
    fetched_at: datetime
    markets: list[Market]
    dexes: dict[str, dict[str, Any]]  # "" / "xyz" -> {full_name, collateral_token, markets, interval_hours, interval_source}
    failures: dict[str, str]  # call -> reason; a failed dex is named here, never silently missing
    schema: SchemaCheck
    calls: int
    elapsed_s: float

    @property
    def ranking_allowed(self) -> bool:
        return self.schema.status == "ok"


def _f(x: Any) -> float | None:
    try:
        return None if x is None else float(x)
    except (TypeError, ValueError):
        return None


def _keys(rows: Any) -> list[str]:
    return sorted({k for r in rows or [] if isinstance(r, dict) for k in r})


class SchemaGuard:
    """Hashes the field names each endpoint returned; compares with the hash a human confirmed last."""

    def __init__(self, path: Path) -> None:
        self.path = path

    @staticmethod
    def fingerprint(parts: dict[str, list[str]]) -> str:
        return hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()

    def _confirmed(self) -> dict[str, Any] | None:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def check(self, parts: dict[str, list[str]]) -> SchemaCheck:
        h, prev = self.fingerprint(parts), self._confirmed()
        if prev is None:
            log.warning("universe schema not confirmed yet (hash %s): ranking refused until `fundarb universe --accept-schema`", h[:12])
            return SchemaCheck("new", h, None, parts)
        if prev.get("hash") == h:
            return SchemaCheck("ok", h, h, parts)
        old: dict[str, list[str]] = prev.get("parts") or {}
        added = {k: sorted(set(v) - set(old.get(k, []))) for k, v in parts.items()}
        removed = {k: sorted(set(old.get(k, [])) - set(parts.get(k, []))) for k in set(old) | set(parts)}
        added, removed = {k: v for k, v in added.items() if v}, {k: v for k, v in removed.items() if v}
        log.warning("universe schema CHANGED (%s -> %s): added %s, removed %s; ranking refused until a human confirms",
                    str(prev.get("hash"))[:12], h[:12], added, removed)
        return SchemaCheck("changed", h, prev.get("hash"), parts, added, removed)

    def accept(self, check: SchemaCheck) -> None:
        self.path.write_text(json.dumps({"hash": check.hash, "parts": check.parts, "confirmed_at": datetime.now(timezone.utc).isoformat()},
                                        indent=2, sort_keys=True), encoding="utf-8")


class HyperliquidUniverse:
    """Fetches the classified universe in one batched pass; `run` repeats it every universe_refresh_seconds."""

    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.settings = settings
        self._transport = transport
        self.guard = SchemaGuard(settings.universe_schema_path)
        self.overrides = load_overrides(settings.universe_overrides_path)
        self._intervals: dict[str, tuple[float, float, str]] = {}  # dex -> (monotonic time, hours, source)
        self.calls = 0

    async def _post(self, client: httpx.AsyncClient, payload: dict[str, Any]) -> Any:
        async def go() -> Any:
            self.calls += 1
            r = await client.post("/info", json=payload)
            r.raise_for_status()
            return r.json()
        return await with_backoff(go)

    async def _interval(self, client: httpx.AsyncClient, dex: str, coin: str) -> tuple[float | None, str]:
        hit = self._intervals.get(dex)
        if hit and time.monotonic() - hit[0] < self.settings.interval_refresh_seconds:
            return hit[1], hit[2]
        start = int((time.time() - INTERVAL_LOOKBACK_HOURS * 3600) * 1000)
        rows = await self._post(client, {"type": "fundingHistory", "coin": coin, "startTime": start})
        hours = interval_from_timestamps([int(r["time"]) / 1000 for r in rows or []])
        src = f"derived: spacing of fundingHistory timestamps ({coin})"
        self._intervals[dex] = (time.monotonic(), hours, src)
        return hours, src

    async def fetch(self) -> UniverseSnapshot:
        t0, calls0, now = time.monotonic(), self.calls, datetime.now(timezone.utc)
        failures: dict[str, str] = {}
        async with httpx.AsyncClient(base_url=self.settings.hyperliquid_url, timeout=self.settings.http_timeout, transport=self._transport,
                                     headers={"User-Agent": "fundarb/0.1 (read-only)", "Accept": "application/json"}) as client:

            async def soft(name: str, payload: dict[str, Any], default: Any) -> Any:
                try:
                    return await self._post(client, payload)
                except Exception as exc:  # noqa: BLE001 - named in failures; the rest of the universe still loads
                    failures[name] = f"{type(exc).__name__}: {str(exc)[:200]}"
                    return default

            try:
                dexs = await self._post(client, {"type": "perpDexs"})
            except Exception as exc:  # noqa: BLE001 - without the dex list there is no universe to report
                raise VenueUnavailable(f"hyperliquid perpDexs failed: {type(exc).__name__}: {exc}") from exc
            cats_raw, spot, capped = await asyncio.gather(
                soft("perpCategories", {"type": "perpCategories"}, []), soft("spotMeta", {"type": "spotMeta"}, {"tokens": []}),
                soft("perpsAtOpenInterestCap", {"type": "perpsAtOpenInterestCap"}, None))
            categories = {str(r[0]): str(r[1]) for r in cats_raw or [] if isinstance(r, list) and len(r) == 2}
            tokens = {t.get("index"): t.get("name") for t in (spot or {}).get("tokens") or []}
            at_cap = set(capped) if isinstance(capped, list) else None
            dex_rows = [d for d in dexs if d is None or isinstance(d, dict)]
            sem = asyncio.Semaphore(DEX_CONCURRENCY)

            async def one_dex(d: dict[str, Any] | None) -> tuple[str, dict[str, Any] | None, Any]:
                name = "" if d is None else str(d.get("name", ""))
                async with sem:
                    try:
                        return name, d, await self._post(client, {"type": "metaAndAssetCtxs", "dex": name})
                    except Exception as exc:  # noqa: BLE001
                        failures[f"metaAndAssetCtxs[{name or 'native'}]"] = f"{type(exc).__name__}: {str(exc)[:200]}"
                        return name, d, None

            per_dex = await asyncio.gather(*(one_dex(d) for d in dex_rows))
            parts: dict[str, list[str]] = {"perpDexs": _keys(dex_rows), "spotMeta.tokens": _keys((spot or {}).get("tokens")),
                                           "perpCategories.row": ["coin:str", "category:str"] if categories else []}
            meta_keys: set[str] = set()
            uni_keys: set[str] = set()
            ctx_keys: set[str] = set()
            markets: list[Market] = []
            dex_info: dict[str, dict[str, Any]] = {}
            for name, d, got in per_dex:
                if not (isinstance(got, list) and len(got) == 2):
                    if got is not None:
                        failures[f"metaAndAssetCtxs[{name or 'native'}]"] = "unexpected response shape"
                    continue
                meta, ctxs = got
                universe = meta.get("universe") or []
                meta_keys |= set(meta)
                uni_keys |= set(_keys(universe))
                ctx_keys |= set(_keys(ctxs))
                if len(universe) != len(ctxs):
                    failures[f"metaAndAssetCtxs[{name or 'native'}]"] = f"{len(universe)} markets but {len(ctxs)} contexts"
                    continue
                coll = tokens.get(meta.get("collateralToken"), f"token {meta.get('collateralToken')}")
                live_first = next((u["name"] for u in universe if not u.get("isDelisted")), None)
                hours: float | None = None
                src = "unavailable" if live_first else "no live market to read it from"
                if live_first:
                    try:
                        hours, src = await self._interval(client, name, live_first)
                    except Exception as exc:  # noqa: BLE001 - its markets carry no interval and fail that gate later
                        failures[f"fundingHistory[{name or 'native'}]"] = f"{type(exc).__name__}: {str(exc)[:200]}"
                caps = {str(c): _f(v) for c, v in ((d or {}).get("assetToStreamingOiCap") or [])}
                builder = None if d is None else d.get("fullName")
                for u, c in zip(universe, ctxs):
                    markets.append(self._market(name, builder, coll, u, c, hours, src, caps, at_cap, categories, self.overrides, now))
                dex_info[name] = {"full_name": builder or "Hyperliquid (native)", "collateral_token": coll, "markets": len(universe),
                                  "interval_hours": hours, "interval_source": src}
            parts.update({"meta": sorted(meta_keys), "meta.universe": sorted(uni_keys), "assetCtx": sorted(ctx_keys)})
        check = self.guard.check(parts)
        snap = UniverseSnapshot(now, markets, dex_info, failures, check, self.calls - calls0, round(time.monotonic() - t0, 2))
        log.info("universe: %d markets on %d dexes in %.2fs (%d calls), schema %s", len(markets), len(dex_info), snap.elapsed_s, snap.calls,
                 check.status)
        return snap

    @staticmethod
    def _market(dex: str, builder: str | None, coll: str, u: dict[str, Any], c: dict[str, Any], hours: float | None, src: str,
                caps: dict[str, float | None], at_cap: set[str] | None, categories: dict[str, str], overrides: dict[str, Override],
                now: datetime) -> Market:
        name = str(u["name"])
        cls, rule = classify(dex, name, categories, overrides)
        pairing, pairing_note = pairing_rule(name, cls, overrides)
        only_iso, mode = bool(u.get("onlyIsolated", False)), str(u.get("marginMode") or "unset")
        # a missing field stays None (reported as such), never a silent zero
        rate, mark, oracle, oi = _f(c.get("funding")), _f(c.get("markPx")), _f(c.get("oraclePx")), _f(c.get("openInterest"))
        hourly = rate / hours if (rate is not None and hours) else None
        imp = c.get("impactPxs")
        spread = None
        if isinstance(imp, list) and len(imp) == 2 and _f(imp[0]) and _f(imp[1]):
            bid, ask = float(imp[0]), float(imp[1])
            spread = (ask - bid) / ((ask + bid) / 2) * 1e4
        return Market(
            raw_name=name, namespace=dex, builder=builder, asset_class=cls, class_rule=rule, collateral_token=coll,
            funding_rate_raw=rate, funding_interval_hours=hours, funding_interval_source=src, funding_rate_hourly=hourly,
            funding_apr=None if hourly is None else hourly * HOURS_PER_YEAR, premium=_f(c.get("premium")),
            oracle_price=oracle, mark_price=mark, mark_oracle_basis_bps=(mark - oracle) / oracle * 1e4 if (mark is not None and oracle) else None,
            open_interest_usd=oi * oracle if (oi is not None and oracle is not None) else None, volume_24h_usd=_f(c.get("dayNtlVlm")),
            max_leverage=float(u.get("maxLeverage") or 0), only_isolated=only_iso, margin_mode=mode, spread_bps=spread, spread_source="impactPxs" if spread is not None else "unavailable",
            depth_usd_1pct=None, listing_live=not u.get("isDelisted", False), oi_cap_usd=caps.get(name),
            at_oi_cap=(name in at_cap) if (dex == "" and at_cap is not None) else None, timestamp_utc=now,
            pairing_eligible=pairing, pairing_note=pairing_note, capital_bucket=market_bucket(only_iso, mode, coll, dex=dex))

    async def run(self, on_snapshot: Callable[[UniverseSnapshot], Awaitable[None] | None], stop: asyncio.Event | None = None) -> None:
        """Fetch now, then every universe_refresh_seconds, until `stop` is set. A failed pass is logged and retried next tick."""
        stop = stop or asyncio.Event()
        while not stop.is_set():
            try:
                res = on_snapshot(await self.fetch())
                if asyncio.iscoroutine(res):
                    await res
            except Exception:  # noqa: BLE001 - the loop survives one bad pass
                log.exception("universe refresh failed")
            try:
                await asyncio.wait_for(stop.wait(), self.settings.universe_refresh_seconds)
            except asyncio.TimeoutError:
                pass


def market_row(m: Market) -> dict[str, Any]:
    d = asdict(m)
    d["asset_class"] = m.asset_class.value
    d["timestamp_utc"] = m.timestamp_utc.isoformat()
    return d
