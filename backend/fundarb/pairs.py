"""Module 3: crypto pair selection. Read-only: nothing here can place an order.

Funding arb needs the SAME asset on two legs whose funding differs. Candidate pairs are therefore built only from:
  - one crypto perp on two venues (Hyperliquid vs Lighter / Orderly / Backpack), or on two Hyperliquid dexes;
  - Hyperliquid spot vs Hyperliquid perp (no funding on spot; long spot / short perp, so only when perp funding is > 0).
Different assets are never paired: that is a directional spread bet, not funding arb.

Pipeline (in this order):
  1 Cluster    30-day hourly log-return correlation over every leg; average-linkage agglomerative clustering, cut at
               corr_threshold (0.75). Both legs of a pair must sit in one cluster: a same-ticker pair across clusters
               (a different token under the same name, a broken feed) is rejected explicitly. Clusters are stored.
  2 Venues     a Hyperliquid market with no second leg anywhere is dropped with the reason (the venues checked, and the
               venues whose adapters are not built yet).
  3 Spot-perp  Hyperliquid spot legs, matched by EXACT token name (or an operator alias in classification_overrides.json).
  4 Rank       gross APR = (r_short / h_short - r_long / h_long) x 8760; net = gross - annual cost drag (round-trip taker
               fees from each venue + book-walk slippage at the intended size, spread over hold_hours); capital APR = net /
               capital per $1 of notional (fundarb.capital: cross in one account, isolated, or spot-perp).
  5 Variance   score = capital APR / max(stdev of the two legs' hourly price spread in bps, 1).
  6 Persist    the funding spread must have paid in each of the last persistence_windows settled hours; others are
               "transient" (stored, never ranked). A pair whose net APR is <= 0 after costs is "unprofitable" (stored).
`pair_metrics` is a pure function of the stored inputs, so a stored scan replays to the same ranking."""

from __future__ import annotations

import asyncio
import math
import statistics
import time
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np

from fundarb.candles import hourly_closes
from fundarb.capital import market_bucket, pair_capital, spot_perp_capital
from fundarb.config import Settings
from fundarb.models import HOURS_PER_YEAR, OrderBook
from fundarb.signals import _slippage_bps, hourly_series
from fundarb.symbols import canonical
from fundarb.universe import AssetClass, Market
from fundarb.venues import BackpackVenue, HyperliquidVenue, LighterVenue, OrderlyVenue, PerpVenue

EXTERNAL: dict[str, type[PerpVenue]] = {"lighter": LighterVenue, "orderly": OrderlyVenue, "backpack": BackpackVenue}
# requests in flight per venue: Lighter answered HTTP 429 at 4 (2026-10-24), so it is read one at a time
VENUE_CONCURRENCY = {"hyperliquid": 4, "lighter": 1, "orderly": 4, "backpack": 4}
SLIP_SIG_FIGS = (4, 3)  # coarser Hyperliquid books, finest first, when 20 full-precision levels can't fill the intended size
# public APIs answered on 2026-10-24 (Aster /fapi/v1/premiumIndex, Paradex /v1/markets/summary, Pacifica /api/v1/info);
# no adapter is built yet, so their legs are not read
NOT_BUILT = ["aster", "pacifica", "paradex"]


@dataclass(frozen=True)
class PairConfig:
    leverage: float = 2.0  # per leg, for capital only (capped by each leg's live max leverage); never for sizing
    buffer: float = 0.25
    hold_hours: float = 168.0  # round-trip costs are spread over this expected hold
    corr_days: float = 30.0
    corr_threshold: float = 0.75
    min_overlap_hours: int = 240  # shared hourly returns before a correlation counts
    max_price_gap_bps: float = 200.0  # mean |price spread| above this = a different contract or asset under one name
    persistence_windows: int = 3
    funding_days: float = 7.0
    depth_fraction: float = 0.05  # intended size: 5% of the thinner leg's depth within 1%
    min_depth_usd: float = 250_000
    top_n: int = 20

    @classmethod
    def from_settings(cls, s: Settings, **kw: Any) -> PairConfig:
        return cls(min_depth_usd=s.min_depth_usd, buffer=s.capital_buffer, **kw)


# --- clustering ---------------------------------------------------------------------------------------------------------

def correlation_matrix(closes: dict[str, dict[int, float]], min_overlap: int) -> tuple[list[str], np.ndarray, np.ndarray]:
    """Pairwise Pearson correlation of hourly log returns over the hours both legs have; NaN below min_overlap."""
    ids = sorted(closes)
    hours = sorted({h for c in closes.values() for h in c})
    idx = {h: i for i, h in enumerate(hours)}
    px = np.full((len(ids), len(hours)), np.nan)
    for i, k in enumerate(ids):
        for h, p in closes[k].items():
            if p > 0:
                px[i, idx[h]] = p
    with np.errstate(invalid="ignore", divide="ignore"):
        ret = np.diff(np.log(px), axis=1)  # NaN where either hour is missing
    n = len(ids)
    corr, overlap = np.full((n, n), np.nan), np.zeros((n, n), dtype=int)
    ok = np.isfinite(ret)
    for i in range(n):
        corr[i, i], overlap[i, i] = 1.0, int(ok[i].sum())
        for j in range(i + 1, n):
            m = ok[i] & ok[j]
            k = int(m.sum())
            overlap[i, j] = overlap[j, i] = k
            if k >= min_overlap:
                a, b = ret[i, m], ret[j, m]
                if a.std() > 0 and b.std() > 0:
                    corr[i, j] = corr[j, i] = float(np.corrcoef(a, b)[0, 1])
    return ids, corr, overlap


def cluster(ids: list[str], corr: np.ndarray, threshold: float) -> dict[str, int]:
    """Average-linkage agglomerative clustering on correlation: keep merging the two clusters with the highest average
    pairwise correlation while it is >= threshold (an unknown correlation counts as 0, so it never helps a merge)."""
    c = np.where(np.isfinite(corr), corr, 0.0)
    groups: list[list[int]] = [[i] for i in range(len(ids))]
    while len(groups) > 1:
        best, pair = -math.inf, None
        for a in range(len(groups)):
            for b in range(a + 1, len(groups)):
                s = float(c[np.ix_(groups[a], groups[b])].mean())
                if s > best:
                    best, pair = s, (a, b)
        if pair is None or best < threshold:
            break
        a, b = pair
        groups[a] += groups.pop(b)
    groups.sort(key=lambda g: (-len(g), min(ids[i] for i in g)))
    return {ids[i]: k for k, g in enumerate(groups) for i in g}


# --- the pure part --------------------------------------------------------------------------------------------------

def _hourly(leg: dict[str, Any]) -> float:
    return 0.0 if leg["kind"] == "spot" else leg["rate_raw"] / leg["interval_hours"]


def pair_metrics(p: dict[str, Any], cfg: PairConfig) -> dict[str, Any]:
    """Every number the ranking uses, from one pair's stored inputs. Pure."""
    s, l = p["short"], p["long"]
    spread_h = _hourly(s) - _hourly(l)
    gross = spread_h * HOURS_PER_YEAR * 100
    fees = [s["taker_bps"], l["taker_bps"]]
    slips = [p["slip_bps"]["short"], p["slip_bps"]["long"]]
    costs = 2 * (sum(fees) + sum(slips)) if None not in fees and None not in slips else None  # entry + exit, both legs
    drag = None if costs is None else costs / 1e4 * (HOURS_PER_YEAR / cfg.hold_hours) * 100
    net = None if drag is None else gross - drag
    levs = [x for x in (s.get("max_leverage"), l.get("max_leverage")) if x]
    lev = min([cfg.leverage, *levs])
    cap = spot_perp_capital(lev, cfg.buffer) if p["kind"] == "spot-perp" else pair_capital(s["capital_bucket"], l["capital_bucket"], lev, cfg.buffer)
    cap_apr = None if net is None else net / cap.capital_per_notional
    breakeven = None if costs is None else (costs / 1e4 / spread_h if spread_h > 0 else math.inf)
    hs = {int(h): v for h, v in s["funding_hourly"].items()}
    hl = {int(h): v for h, v in l["funding_hourly"].items()}
    if s["kind"] == "spot":
        hs = {h: 0.0 for h in hl}
    if l["kind"] == "spot":
        hl = {h: 0.0 for h in hs}
    common = sorted(set(hs) & set(hl))
    series = [hs[h] - hl[h] for h in common]
    mean7 = std7 = paid = None
    if series:
        mean7, std7 = statistics.fmean(series) * HOURS_PER_YEAR * 100, statistics.pstdev(series) * HOURS_PER_YEAR * 100
        paid = sum(1 for x in series if x > 0) / len(series)
    persistent = len(series) >= cfg.persistence_windows and all(x > 0 for x in series[-cfg.persistence_windows:])
    sd = p["spread_stdev_bps"]
    score = None if cap_apr is None or sd is None else cap_apr / max(sd, 1.0)
    reject = code = None
    if not p["same_cluster"]:
        code = "cross_cluster"
        reject = (f"cross-cluster (corr {p['corr']:.2f}): a directional spread bet, not funding arb" if p["corr"] is not None
                  else "cross-cluster (correlation unknown: too little shared history)")
    elif p["mean_gap_bps"] is not None and abs(p["mean_gap_bps"]) > cfg.max_price_gap_bps:
        code, reject = "price_gap", f"price gap {p['mean_gap_bps']:+.0f} bps on average: a different contract or asset under one name"
    elif sd is None:
        code, reject = "no_price_history", "no shared price history for the spread"
    elif p["kind"] == "spot-perp" and spread_h <= 0:
        code, reject = "spot_needs_short", "perp funding is not positive: the trade would need a short spot leg"
    elif spread_h <= 0:
        code, reject = "no_spread", "no funding spread right now"
    else:
        for side, leg in (("short", s), ("long", l)):
            if leg["depth_usd_1pct"] is None:
                code, reject = f"depth_unknown:{leg['venue']}", f"{side} leg depth unknown ({leg['venue']}: {leg.get('book_error') or 'no book'})"
                break
            if leg["depth_usd_1pct"] < cfg.min_depth_usd:
                code = f"depth_below_min:{leg['venue']}"
                reject = f"{side} leg depth ${leg['depth_usd_1pct']:,.0f} < ${cfg.min_depth_usd:,.0f} within 1% ({leg['venue']})"
                break
    status = ("rejected" if reject else "net_unknown" if net is None else "unprofitable" if net <= 0
              else "transient" if not persistent else "ranked")
    return {"status": status, "reject": reject, "reject_code": code, "gross_apr_pct": gross, "costs_bps": costs, "cost_drag_apr_pct": drag, "net_apr_pct": net,
            "capital_mode": cap.mode, "capital_reason": cap.reason, "capital_per_notional": cap.capital_per_notional, "leverage_used": lev,
            "capital_apr_pct": cap_apr, "breakeven_hours": breakeven, "spread_stdev_bps": sd, "score": score, "mean_7d_apr_pct": mean7,
            "std_7d_apr_pct": std7, "paid_share": paid, "persistent": persistent, "funding_hours": len(series),
            "oracle": "provisional" if any(x.get("oracle_state") == "WARMING" for x in (s, l) if x["venue"] == "hyperliquid") else "enforced"}


def rank(pairs: list[dict[str, Any]], cfg: PairConfig) -> dict[str, list[dict[str, Any]]]:
    """Both ranked lists (top_n each) plus everything that wasn't ranked, each with its reason."""
    rows = [{"pair_id": p["pair_id"], "inputs": p, "metrics": pair_metrics(p, cfg)} for p in pairs]
    ranked = [r for r in rows if r["metrics"]["status"] == "ranked"]
    by = lambda k: sorted(ranked, key=lambda r: (-r["metrics"][k], r["pair_id"]))  # noqa: E731
    return {"capital_apr": by("capital_apr_pct")[:cfg.top_n], "score": by("score")[:cfg.top_n],
            "transient": [r for r in rows if r["metrics"]["status"] == "transient"],
            "unprofitable": sorted((r for r in rows if r["metrics"]["status"] == "unprofitable"), key=lambda r: -r["metrics"]["net_apr_pct"]),
            "net_unknown": [r for r in rows if r["metrics"]["status"] == "net_unknown"],
            "rejected": [r for r in rows if r["metrics"]["status"] == "rejected"], "all_ranked": ranked}


# --- gathering the inputs (network) -----------------------------------------------------------------------------------

def _depth(book: OrderBook | None) -> tuple[float | None, bool]:
    if book is None:
        return None, False
    bd, ad, complete = book.depth_within(1.0)
    return min(bd, ad), complete


def _hl_book(raw: dict[str, Any], name: str) -> OrderBook:
    bids, asks = raw["levels"]
    return OrderBook.build("hyperliquid", name, [(float(x["px"]), float(x["sz"])) for x in bids], [(float(x["px"]), float(x["sz"])) for x in asks])


async def gather_inputs(settings: Settings, markets: list[Market], gate_results: dict[str, dict[str, Any]], cfg: PairConfig,
                        spot_aliases: dict[str, str] | None = None) -> dict[str, Any]:
    """Every leg's data for the crypto markets that passed all gates, then the candidate pairs with their full inputs."""
    hl = HyperliquidVenue(settings)
    await hl.connect()
    externals: dict[str, PerpVenue] = {}
    notes: dict[str, str] = {}
    for name, cls in EXTERNAL.items():
        v = cls(settings)
        try:
            await v.connect()
            externals[name] = v
        except Exception as exc:  # noqa: BLE001 - that venue's legs are missing; named in the report
            notes[name] = f"connect failed: {type(exc).__name__}: {exc}"
    sems = {k: asyncio.Semaphore(n) for k, n in VENUE_CONCURRENCY.items()}
    try:
        crypto = [m for m in markets if m.asset_class is AssetClass.CRYPTO and gate_results.get(m.raw_name, {}).get("passed")]
        # fees from the venue: your tier with FUNDARB_HL_ACCOUNT_ADDRESS, else the published base tier (userFees.feeSchedule)
        user = settings.hl_account_address
        hl_fees = await hl._info({"type": "userFees", "user": user or "0x" + "0" * 40})
        if user:
            perp_taker, spot_taker = float(hl_fees["userCrossRate"]) * 1e4, float(hl_fees["userSpotCrossRate"]) * 1e4
        else:
            perp_taker, spot_taker = float(hl_fees["feeSchedule"]["cross"]) * 1e4, float(hl_fees["feeSchedule"]["spotCross"]) * 1e4
        fee_src = "api: userFees " + ("(your tier)" if user else "feeSchedule (published base tier)")
        legs: dict[str, dict[str, Any]] = {}
        books: dict[str, OrderBook | None] = {}
        closes: dict[str, dict[int, float]] = {}

        async def hl_leg(m: Market) -> None:
            async with sems["hyperliquid"]:
                lid = f"hyperliquid:{m.namespace or 'native'}:{m.raw_name}"
                err = None
                try:
                    book = _hl_book(await hl._info({"type": "l2Book", "coin": m.raw_name}), m.raw_name)
                except Exception as exc:  # noqa: BLE001
                    book, err = None, f"{type(exc).__name__}: {exc}"
                start = int((time.time() - cfg.funding_days * 86400) * 1000)
                hist = await hl._info({"type": "fundingHistory", "coin": m.raw_name, "startTime": start})
                series = hourly_series(sorted((int(r["time"]) / 1000, float(r["fundingRate"])) for r in hist or []), m.funding_interval_hours or 1.0)
                closes[lid] = await hourly_closes(hl, m.raw_name, cfg.corr_days)
                # depth: the gate's own measurement (it falls back to the aggregated book when 20 levels stop short of 1%);
                # the full-precision book read here is only walked for slippage at the intended size
                g = gate_results[m.raw_name]
                depth, complete = (g["depth_usd_1pct"], "lower bound" not in g["depth_source"]) if g.get("depth_usd_1pct") is not None else _depth(book)
                slip_src = "l2Book full precision"
                if book is not None and depth:
                    most = cfg.depth_fraction * depth  # the largest size this leg can be asked to fill
                    for sig in SLIP_SIG_FIGS:
                        if _slippage_bps(book, most) is not None:
                            break
                        try:
                            book = _hl_book(await hl._info({"type": "l2Book", "coin": m.raw_name, "nSigFigs": sig}), m.raw_name)
                            slip_src = f"l2Book nSigFigs={sig} (aggregated levels: slippage approximate)"
                        except Exception:  # noqa: BLE001 - keep the finer book; slippage may stay unknown
                            break
                books[lid] = book
                st = g["oracle_state"]
                # HIP-3 dexes scale fees by deployerFeeScale, not modelled yet: a builder leg's fee is unknown, never guessed
                legs[lid] = {"leg_id": lid, "venue": "hyperliquid", "dex": m.namespace or "native", "market": m.raw_name,
                             "symbol": canonical(m.raw_name.split(":")[-1]), "kind": "perp", "rate_raw": m.funding_rate_raw,
                             "interval_hours": m.funding_interval_hours, "interval_source": m.funding_interval_source,
                             "taker_bps": perp_taker if not m.namespace else None,
                             "fee_source": fee_src if not m.namespace else "unknown: HIP-3 deployer fee scale not modelled",
                             "depth_usd_1pct": depth, "depth_complete": complete, "depth_source": g.get("depth_source", "l2Book"),
                             "slippage_source": slip_src,
                             "book_error": err, "max_leverage": m.max_leverage, "capital_bucket": m.capital_bucket, "oracle_state": st, "volume_24h_usd": m.volume_24h_usd,
                             "funding_hourly": {str(h): v for h, v in series.items()}}

        await asyncio.gather(*(hl_leg(m) for m in crypto))
        wanted = {leg["symbol"] for leg in legs.values()}

        async def ext_venue(name: str, v: PerpVenue) -> None:
            try:
                listed = set(await v.symbols()) & wanted
                rates = await v.funding_board(sorted(listed))
            except Exception as exc:  # noqa: BLE001
                notes[name] = f"listing/funding read failed: {type(exc).__name__}: {exc}"
                return
            try:
                fs = await v.get_fee_schedule()
                taker, fsrc = fs.taker_bps, fs.source
            except Exception as exc:  # noqa: BLE001 - fee tiers are never assumed
                assumed = settings.assumed_taker_bps.get(name)
                taker, fsrc = (assumed, "operator assumption (FUNDARB_ASSUMED_TAKER_BPS)") if assumed is not None else (None, f"unknown: {exc}")

            async def one(sym: str) -> None:
                async with sems[name]:
                    lid, err = f"{name}:{sym}", None
                    try:
                        book = await v.get_orderbook(sym, settings.book_depth)
                    except Exception as exc:  # noqa: BLE001 - e.g. Orderly serves its book only to a signed-in account
                        book, err = None, str(exc)[:160]
                    try:
                        hist = await v.funding_history(sym, cfg.funding_days)
                    except Exception:  # noqa: BLE001
                        hist = []
                    try:
                        closes[lid] = await hourly_closes(v, sym, cfg.corr_days, symbol=sym)
                    except Exception:  # noqa: BLE001 - no price history: the pair is rejected for it, by name
                        closes[lid] = {}
                    f = rates[sym]
                    depth, complete = _depth(book)
                    books[lid] = book
                    legs[lid] = {"leg_id": lid, "venue": name, "dex": "", "market": sym, "symbol": sym, "kind": "perp", "rate_raw": f.rate_raw,
                                 "interval_hours": f.interval_hours, "interval_source": f.interval_source, "taker_bps": taker, "fee_source": fsrc,
                                 "depth_usd_1pct": depth, "depth_complete": complete, "book_error": err, "max_leverage": None,
                                 "capital_bucket": market_bucket(False, "unset", "unknown", venue=name), "oracle_state": None,
                                 "volume_24h_usd": None, "funding_hourly": {str(h): r for h, r in hourly_series(hist, f.interval_hours).items()}}
            await asyncio.gather(*(one(s) for s in sorted(rates)))

        await asyncio.gather(*(ext_venue(n, v) for n, v in externals.items()))
        # Hyperliquid spot legs: exact token name (or the operator's alias), quoted in USDC
        sm, sctx = await hl._info({"type": "spotMetaAndAssetCtxs"})
        tok = {t["index"]: t["name"] for t in sm["tokens"]}
        aliases = spot_aliases or {}
        for m in [m for m in crypto if not m.namespace]:
            want = aliases.get(m.raw_name, m.raw_name)
            for u, c in zip(sm["universe"], sctx):
                base, quote = tok.get(u["tokens"][0]), tok.get(u["tokens"][1])
                if base != want or quote != "USDC":
                    continue
                lid = f"hyperliquid:spot:{u['name']}"
                try:
                    book = _hl_book(await hl._info({"type": "l2Book", "coin": u["name"]}), u["name"])
                    err = None
                except Exception as exc:  # noqa: BLE001
                    book, err = None, str(exc)[:160]
                closes[lid] = await hourly_closes(hl, u["name"], cfg.corr_days)
                depth, complete = _depth(book)
                books[lid] = book
                legs[lid] = {"leg_id": lid, "venue": "hyperliquid", "dex": "spot", "market": u["name"], "symbol": canonical(m.raw_name),
                             "spot_token": base, "kind": "spot", "rate_raw": 0.0, "interval_hours": None, "interval_source": "spot: no funding",
                             "taker_bps": spot_taker, "fee_source": fee_src + " spotCross", "depth_usd_1pct": depth, "depth_complete": complete,
                             "book_error": err, "max_leverage": 1.0, "capital_bucket": f"spot:hyperliquid:spot:{quote}", "oracle_state": None,
                             "volume_24h_usd": float(c.get("dayNtlVlm") or 0), "funding_hourly": {}}
    finally:
        await hl.disconnect()
        for v in externals.values():
            await v.disconnect()
    calls = hl.calls + sum(v.calls for v in externals.values())
    return build_pairs(legs, books, closes, cfg) | {"notes": notes, "calls": calls}


def build_pairs(legs: dict[str, dict[str, Any]], books: dict[str, OrderBook | None], closes: dict[str, dict[int, float]],
                cfg: PairConfig) -> dict[str, Any]:
    ids, corr, overlap = correlation_matrix({k: v for k, v in closes.items() if v}, cfg.min_overlap_hours)
    pos = {k: i for i, k in enumerate(ids)}
    clusters = cluster(ids, corr, cfg.corr_threshold)
    by_sym: dict[str, list[str]] = {}
    for lid, leg in legs.items():
        by_sym.setdefault(leg["symbol"], []).append(lid)
    pairs: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for sym, lids in sorted(by_sym.items()):
        perps = [x for x in lids if legs[x]["kind"] == "perp"]
        found = False
        for i, a in enumerate(lids):
            for b in lids[i + 1:]:
                la, lb = legs[a], legs[b]
                if la["kind"] == "spot" and lb["kind"] == "spot":
                    continue
                if (la["venue"], la["dex"]) == (lb["venue"], lb["dex"]):
                    continue
                if "hyperliquid" not in (la["venue"], lb["venue"]):
                    continue  # the pool is Hyperliquid markets: one leg is always on Hyperliquid
                if la["kind"] == "spot" or lb["kind"] == "spot":
                    spot, perp = (la, lb) if la["kind"] == "spot" else (lb, la)
                    if perp["venue"] != "hyperliquid" or perp["dex"] != "native":
                        continue  # spot-perp only on one venue (no cross-venue leg risk)
                    short, long_, kind = perp, spot, "spot-perp"
                else:
                    short, long_ = (la, lb) if _hourly(la) >= _hourly(lb) else (lb, la)
                    kind = "perp-perp"
                found = True
                ia, ib = pos.get(short["leg_id"]), pos.get(long_["leg_id"])
                c = None if ia is None or ib is None or not np.isfinite(corr[ia, ib]) else float(corr[ia, ib])
                same = ia is not None and ib is not None and clusters.get(short["leg_id"]) == clusters.get(long_["leg_id"]) and c is not None
                ps, pl = closes.get(short["leg_id"], {}), closes.get(long_["leg_id"], {})
                common = sorted(set(ps) & set(pl))
                gaps = [(ps[h] - pl[h]) / ((ps[h] + pl[h]) / 2) * 1e4 for h in common]
                sd = statistics.pstdev(gaps) if len(gaps) >= cfg.min_overlap_hours else None
                mg = statistics.fmean(gaps) if gaps else None
                bs, bl = books.get(short["leg_id"]), books.get(long_["leg_id"])
                ds, dl = short["depth_usd_1pct"], long_["depth_usd_1pct"]
                size = cfg.depth_fraction * min(ds, dl) if ds and dl else None
                slip = {"short": _slippage_bps(bs, size) if size else None, "long": _slippage_bps(bl, size) if size else None}
                pairs.append({"pair_id": f"{kind}:{short['leg_id']}>{long_['leg_id']}", "kind": kind, "symbol": sym, "short": short, "long": long_,
                              "corr": c, "overlap_hours": int(overlap[ia, ib]) if ia is not None and ib is not None else 0,
                              "cluster_short": clusters.get(short["leg_id"]), "cluster_long": clusters.get(long_["leg_id"]), "same_cluster": same,
                              "spread_stdev_bps": sd, "mean_gap_bps": mg, "size_usd": size, "slip_bps": slip})
        if not found and any(legs[x]["venue"] == "hyperliquid" for x in perps):
            dropped.append({"symbol": sym, "legs": lids, "reason": f"no second leg: not listed on {', '.join(EXTERNAL)}, no Hyperliquid spot or "
                                                                  f"second dex; adapters not built for {', '.join(NOT_BUILT)}"})
    cl = {"threshold": cfg.corr_threshold, "window_days": cfg.corr_days, "legs": len(ids),
          "members": [{"leg_id": k, "cluster": clusters[k], "hours": len(closes[k])} for k in ids]}
    return {"legs": legs, "pairs": pairs, "dropped": dropped, "clusters": cl}


def config_dict(cfg: PairConfig) -> dict[str, Any]:
    return asdict(cfg)
