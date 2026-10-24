"""Module 2: liquidity, quality and oracle-health gates. A market enters the candidate pool only if it passes ALL of them.

Order (cheap gates first; the order book is read only for markets that pass them, one l2Book call each):
  pairing_eligible   not excluded by an operator override, not UNCLASSIFIED
  listing_live       not delisted
  funding_interval   the dex's interval was read and is > 0
  volume_24h         volume_24h_usd >= min_volume_24h_usd
  open_interest      open_interest_usd >= min_oi_usd
  oi_cap             if the market has an OI cap: open_interest_usd <= max_oi_cap_share x cap
  spread             top-of-book spread (l2Book) <= max_spread_bps
  depth              min(bid, ask) notional within 1% of mid >= min_depth_usd
  oracle             |basis| now <= the market's own 99th percentile of |basis| (needs oracle_min_samples of history;
                     until then it passes, flagged "warming up")
Flag (not a reject): |basis| > oracle_persistent_bps on every one of the last oracle_recent_samples samples and steady
(std < oracle_stable_std_bps): usually a thin or lagging oracle, not an opportunity.

Nothing is dropped silently: every market gets a result naming the first gate it failed, every check's value and limit,
and gates that were not measured (because an earlier, cheaper gate failed) say so. `decide` is a pure function of the
stored inputs, so a stored run replays to the same answer."""

from __future__ import annotations

import asyncio
import math
import statistics
import time
from dataclasses import asdict, dataclass
from typing import Any

import httpx

from fundarb.config import Settings
from fundarb.universe import Market
from fundarb.venues.base import with_backoff

GATES = ["pairing_eligible", "listing_live", "funding_interval", "volume_24h", "open_interest", "oi_cap", "spread", "depth", "oracle"]
CHEAP = GATES[:6]
COARSE_SIG_FIGS = 3  # l2Book aggregation used when 20 full-precision levels don't reach 1% from mid


@dataclass(frozen=True)
class GateConfig:
    min_volume_24h_usd: float
    min_oi_usd: float
    max_spread_bps: float
    min_depth_usd: float
    max_oi_cap_share: float
    oracle_min_samples: int
    oracle_persistent_bps: float
    oracle_stable_std_bps: float
    oracle_recent_samples: int

    @classmethod
    def from_settings(cls, s: Settings) -> GateConfig:
        return cls(**{f: getattr(s, f) for f in cls.__dataclass_fields__})


@dataclass(frozen=True)
class Book:
    spread_bps: float
    depth_usd_1pct: float  # the thinner side
    complete: bool  # the levels read reach 1% from mid on both sides (else depth is a lower bound)
    source: str


def _side_depth(levels: list[dict[str, Any]], mid: float, bid_side: bool) -> tuple[float, bool]:
    lim = mid * (0.99 if bid_side else 1.01)
    total, reached = 0.0, False
    for lv in levels:
        px, sz = float(lv["px"]), float(lv["sz"])
        if (px < lim) if bid_side else (px > lim):
            reached = True
            break
        total += px * sz
    return total, reached


def parse_book(raw: dict[str, Any], source: str) -> Book | None:
    """l2Book {"levels": [bids best-first, asks best-first]} -> top-of-book spread and depth within 1%."""
    try:
        bids, asks = raw["levels"]
        if not bids or not asks:
            return None
        bid, ask = float(bids[0]["px"]), float(asks[0]["px"])
    except (KeyError, TypeError, ValueError, IndexError):
        return None
    mid = (bid + ask) / 2
    bd, br = _side_depth(bids, mid, True)
    ad, ar = _side_depth(asks, mid, False)
    return Book((ask - bid) / mid * 1e4, min(bd, ad), br and ar, source)


async def read_books(settings: Settings, names: list[str], transport: httpx.AsyncBaseTransport | None = None,
                     min_depth: float = 0.0) -> tuple[dict[str, Book], dict[str, str], int]:
    """One full-precision l2Book per market (exact spread); when its 20 levels stop short of 1% and the partial depth is
    below `min_depth`, one more read aggregated to COARSE_SIG_FIGS for the depth (labelled so)."""
    books: dict[str, Book] = {}
    failures: dict[str, str] = {}
    calls = 0
    sem = asyncio.Semaphore(settings.book_concurrency)
    async with httpx.AsyncClient(base_url=settings.hyperliquid_url, timeout=settings.http_timeout, transport=transport,
                                 headers={"User-Agent": "fundarb/0.1 (read-only)"}) as client:
        async def post(payload: dict[str, Any]) -> Any:
            nonlocal calls

            async def go() -> Any:
                nonlocal calls
                calls += 1
                r = await client.post("/info", json=payload)
                r.raise_for_status()
                return r.json()
            return await with_backoff(go)

        async def one(name: str) -> None:
            async with sem:
                try:
                    fine = parse_book(await post({"type": "l2Book", "coin": name}), "l2Book full precision")
                    if fine is None:
                        failures[name] = "empty or unreadable book"
                        return
                    if not fine.complete and fine.depth_usd_1pct < min_depth:
                        coarse = parse_book(await post({"type": "l2Book", "coin": name, "nSigFigs": COARSE_SIG_FIGS}),
                                            f"l2Book nSigFigs={COARSE_SIG_FIGS} (aggregated)")
                        if coarse is not None:
                            fine = Book(fine.spread_bps, coarse.depth_usd_1pct, coarse.complete, f"spread: full precision; depth: {coarse.source}")
                    books[name] = fine
                except Exception as exc:  # noqa: BLE001 - that market's depth/spread gates say why
                    failures[name] = f"{type(exc).__name__}: {str(exc)[:160]}"

        await asyncio.gather(*(one(n) for n in names))
    return books, failures, calls


def _p99_abs(xs: list[float]) -> float:
    """Nearest-rank 99th percentile of |x| (deterministic, no interpolation)."""
    a = sorted(abs(x) for x in xs)
    return a[max(0, math.ceil(0.99 * len(a)) - 1)]


def oracle_stats(history: list[float], cfg: GateConfig) -> dict[str, Any]:
    """What the oracle gate needs from a market's stored basis history (stored with the result, so replays don't need it)."""
    return {"n": len(history), "p99_abs_bps": _p99_abs(history) if history else None,
            "recent_bps": [round(x, 4) for x in history[-(cfg.oracle_recent_samples - 1):]] if cfg.oracle_recent_samples > 1 else []}


def market_inputs(m: Market, book: Book | None, book_error: str | None, history: list[float], cfg: GateConfig) -> dict[str, Any]:
    return {"raw_name": m.raw_name, "pairing_eligible": m.pairing_eligible, "pairing_note": m.pairing_note, "listing_live": m.listing_live,
            "funding_interval_hours": m.funding_interval_hours, "volume_24h_usd": m.volume_24h_usd, "open_interest_usd": m.open_interest_usd,
            "oi_cap_usd": m.oi_cap_usd, "impact_spread_bps": m.spread_bps, "basis_bps": m.mark_oracle_basis_bps,
            "book": None if book is None else asdict(book), "book_error": book_error, "oracle": oracle_stats(history, cfg)}


def _chk(gate: str, ok: bool | None, value: Any, limit: Any, note: str = "") -> dict[str, Any]:
    return {"gate": gate, "ok": ok, "value": value, "limit": limit, "note": note}


def decide(x: dict[str, Any], cfg: GateConfig) -> dict[str, Any]:
    """Every gate for one market from its stored inputs -> {passed, failed_gate, checks, flags}. Pure: replays exactly."""
    checks: list[dict[str, Any]] = []
    flags: list[str] = []
    checks.append(_chk("pairing_eligible", bool(x["pairing_eligible"]), x["pairing_note"], "eligible"))
    checks.append(_chk("listing_live", bool(x["listing_live"]), x["listing_live"], True))
    h = x["funding_interval_hours"]
    checks.append(_chk("funding_interval", h is not None and h > 0, h, "> 0"))
    v, oi, cap = x["volume_24h_usd"], x["open_interest_usd"], x["oi_cap_usd"]
    checks.append(_chk("volume_24h", v is not None and v >= cfg.min_volume_24h_usd, v, cfg.min_volume_24h_usd))
    checks.append(_chk("open_interest", oi is not None and oi >= cfg.min_oi_usd, oi, cfg.min_oi_usd))
    if cap is None:
        checks.append(_chk("oi_cap", True, None, None, "no published cap"))
    else:
        checks.append(_chk("oi_cap", oi is not None and oi <= cfg.max_oi_cap_share * cap, oi, cfg.max_oi_cap_share * cap,
                           f"{(oi or 0) / cap:.0%} of a {cap:,.0f} cap"))
    cheap_ok = all(c["ok"] for c in checks)
    book = x["book"]
    if book is None:
        why = x["book_error"] or ("not measured: an earlier gate failed" if not cheap_ok else "no book")
        checks.append(_chk("spread", None if not cheap_ok else False, x["impact_spread_bps"], cfg.max_spread_bps,
                           f"{why} (impact spread shown)"))
        checks.append(_chk("depth", None if not cheap_ok else False, None, cfg.min_depth_usd, why))
    else:
        checks.append(_chk("spread", book["spread_bps"] <= cfg.max_spread_bps, round(book["spread_bps"], 3), cfg.max_spread_bps, "top of book"))
        lower = "" if book["complete"] else " (lower bound: the levels read stop short of 1%)"
        checks.append(_chk("depth", book["depth_usd_1pct"] >= cfg.min_depth_usd, round(book["depth_usd_1pct"], 2), cfg.min_depth_usd,
                           book["source"] + lower))
    o, b = x["oracle"], x["basis_bps"]
    if b is None:
        checks.append(_chk("oracle", False, None, None, "no basis (mark or oracle missing)"))
    elif o["n"] < cfg.oracle_min_samples or o["p99_abs_bps"] is None:
        checks.append(_chk("oracle", True, round(b, 3), None, f"warming up: {o['n']}/{cfg.oracle_min_samples} samples"))
        if x["listing_live"]:
            flags.append(f"oracle history warming up ({o['n']}/{cfg.oracle_min_samples})")
    else:
        checks.append(_chk("oracle", abs(b) <= o["p99_abs_bps"], round(b, 3), round(o["p99_abs_bps"], 3), "|basis| vs own p99"))
    recent = list(o["recent_bps"]) + ([b] if b is not None else [])
    if len(recent) >= cfg.oracle_recent_samples and all(abs(r) > cfg.oracle_persistent_bps for r in recent) \
            and statistics.pstdev(recent) < cfg.oracle_stable_std_bps:
        flags.append(f"persistent stable basis ~{statistics.fmean(recent):+.0f} bps over {len(recent)} samples: likely a thin or lagging oracle")
    failed = next((c["gate"] for c in checks if c["ok"] is False), None)
    return {"passed": failed is None, "failed_gate": failed, "checks": checks, "flags": flags}


@dataclass
class GateReport:
    run_id: str
    ts: str
    total: int
    counts: dict[str, dict[str, int]]  # gate -> {"pass", "fail", "not_measured"}
    passed: list[str]
    results: list[dict[str, Any]]  # {raw_name, passed, failed_gate, checks, flags, inputs}
    top_rejects: list[dict[str, Any]]


def run_gates(inputs: list[dict[str, Any]], cfg: GateConfig, run_id: str, ts: str, volume: dict[str, float] | None = None) -> GateReport:
    results = [{"raw_name": x["raw_name"], **decide(x, cfg), "inputs": x} for x in inputs]
    counts = {g: {"pass": 0, "fail": 0, "not_measured": 0} for g in GATES}
    for r in results:
        for c in r["checks"]:
            counts[c["gate"]]["pass" if c["ok"] else "not_measured" if c["ok"] is None else "fail"] += 1
    rejects = sorted((r for r in results if not r["passed"]), key=lambda r: -((volume or {}).get(r["raw_name"]) or 0))
    top = [{"raw_name": r["raw_name"], "gate": r["failed_gate"], **{k: c[k] for c in r["checks"] if c["gate"] == r["failed_gate"]
                                                                        for k in ("value", "limit", "note")}} for r in rejects[:20]]
    return GateReport(run_id, ts, len(results), counts, sorted(r["raw_name"] for r in results if r["passed"]), results, top)


def cheap_pass(x: dict[str, Any], cfg: GateConfig) -> bool:
    """The six cheap gates only (decides whether the order book is worth reading)."""
    d = decide({**x, "book": {"spread_bps": 0.0, "depth_usd_1pct": math.inf, "complete": True, "source": "probe"},
                "oracle": {"n": 0, "p99_abs_bps": None, "recent_bps": []}}, cfg)
    return all(c["ok"] for c in d["checks"] if c["gate"] in CHEAP)


def new_run_id() -> str:
    return time.strftime("g%Y%m%dT%H%M%S", time.gmtime()) + f"{int(time.time() * 1000) % 1000:03d}"
