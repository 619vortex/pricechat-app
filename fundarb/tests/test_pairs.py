"""Phase C: crypto pair selection. TEST FIXTURES only: made-up legs and price series; candle responses in the shapes each
venue returned on 2026-10-24."""

import asyncio
import math
from typing import Any

import httpx
import numpy as np
import pytest
from conftest import Router, body, fixture, settings

from fundarb.candles import _cache, hourly_closes
from fundarb.models import OrderBook
from fundarb.pairs import PairConfig, build_pairs, cluster, correlation_matrix, pair_metrics, rank
from fundarb.venues import BackpackVenue, HyperliquidVenue, LighterVenue, OrderlyVenue

CFG = PairConfig(min_overlap_hours=50, min_depth_usd=250_000)
H0 = 490_000  # an arbitrary unix hour


def leg(lid: str, venue: str, rate: float, *, dex: str = "native", kind: str = "perp", taker: float | None = 4.5, depth: float | None = 1e6,
        hourly: float | None = None, bucket: str | None = None, oracle: str | None = "ENFORCING") -> dict[str, Any]:
    h = rate if hourly is None else hourly
    return {"leg_id": lid, "venue": venue, "dex": dex, "market": lid, "symbol": "BTC-PERP", "kind": kind, "rate_raw": rate,
            "interval_hours": None if kind == "spot" else 1.0, "taker_bps": taker, "depth_usd_1pct": depth, "book_error": None,
            "max_leverage": 1.0 if kind == "spot" else 40.0, "oracle_state": oracle if venue == "hyperliquid" else None,
            "capital_bucket": bucket or f"cross:{venue}:{dex}:USDC", "funding_hourly": {} if kind == "spot" else {str(H0 + i): h for i in range(168)}}


def pair(short: dict[str, Any], long: dict[str, Any], **kw: Any) -> dict[str, Any]:
    p = {"pair_id": f"x:{short['leg_id']}>{long['leg_id']}", "kind": "perp-perp", "symbol": "BTC-PERP", "short": short, "long": long, "corr": 0.99,
         "overlap_hours": 700, "cluster_short": 0, "cluster_long": 0, "same_cluster": True, "spread_stdev_bps": 4.0, "mean_gap_bps": 1.0,
         "size_usd": 50_000, "slip_bps": {"short": 1.0, "long": 1.0}}
    return {**p, **kw}


def test_gross_net_and_capital_apr_with_every_term() -> None:
    p = pair(leg("hl", "hyperliquid", 0.0001), leg("lt", "lighter", -0.00002, taker=2.0))
    m = pair_metrics(p, CFG)
    assert m["gross_apr_pct"] == pytest.approx(0.00012 * 8760 * 100)
    assert m["costs_bps"] == pytest.approx(2 * (4.5 + 2.0 + 1.0 + 1.0))  # entry + exit, both legs, fees + slippage
    assert m["cost_drag_apr_pct"] == pytest.approx(17 / 1e4 * (8760 / 168) * 100)
    assert m["net_apr_pct"] == pytest.approx(m["gross_apr_pct"] - m["cost_drag_apr_pct"])
    assert m["capital_mode"] == "isolated" and m["capital_per_notional"] == pytest.approx(2 * 1.25 / 2)  # two venues: two accounts
    assert m["capital_apr_pct"] == pytest.approx(m["net_apr_pct"] / 1.25)
    assert m["breakeven_hours"] == pytest.approx(17 / 1e4 / 0.00012)
    assert m["score"] == pytest.approx(m["capital_apr_pct"] / 4.0)
    assert m["status"] == "ranked" and m["oracle"] == "enforced"


def test_one_account_pairs_get_the_cross_margin_rate() -> None:
    a = leg("hl1", "hyperliquid", 0.0001)
    b = leg("hl2", "hyperliquid", 0.0, dex="native")
    b["dex"] = "native2"  # a second dex would be a second account; force the same bucket to test the same-account path
    m = pair_metrics(pair(a, {**b, "capital_bucket": a["capital_bucket"]}), CFG)
    assert m["capital_mode"] == "cross" and m["capital_per_notional"] == pytest.approx(2.25 / 2)


def test_spot_perp_capital_and_direction() -> None:
    perp, spot = leg("hl", "hyperliquid", 0.00005), leg("sp", "hyperliquid", 0.0, dex="spot", kind="spot", taker=7.0, bucket="spot:hyperliquid:spot:USDC")
    m = pair_metrics(pair(perp, spot, kind="spot-perp"), CFG)
    assert m["capital_mode"] == "spot-perp" and m["capital_per_notional"] == pytest.approx(1 + 1.25 / 1)  # leverage capped by the spot leg
    assert m["gross_apr_pct"] == pytest.approx(0.00005 * 8760 * 100) and m["persistent"]
    neg = pair_metrics(pair(leg("hl", "hyperliquid", -0.00005), spot, kind="spot-perp"), CFG)
    assert neg["status"] == "rejected" and neg["reject_code"] == "spot_needs_short"


@pytest.mark.parametrize(("over", "code"), [
    ({"same_cluster": False, "corr": 0.2}, "cross_cluster"), ({"mean_gap_bps": 900.0}, "price_gap"), ({"spread_stdev_bps": None}, "no_price_history"),
])
def test_identity_checks_reject_with_a_code(over: dict[str, Any], code: str) -> None:
    m = pair_metrics(pair(leg("hl", "hyperliquid", 0.0001), leg("lt", "lighter", 0.0), **over), CFG)
    assert m["status"] == "rejected" and m["reject_code"] == code


def test_depth_unknown_fees_unknown_unprofitable_and_transient_are_never_ranked() -> None:
    hl = leg("hl", "hyperliquid", 0.0001)
    assert pair_metrics(pair(hl, leg("o", "orderly", 0.0, depth=None)), CFG)["reject_code"] == "depth_unknown:orderly"
    assert pair_metrics(pair(hl, leg("b", "backpack", 0.0, depth=100_000)), CFG)["reject_code"] == "depth_below_min:backpack"
    assert pair_metrics(pair(hl, leg("b", "backpack", 0.0, taker=None)), CFG)["status"] == "net_unknown"
    assert pair_metrics(pair(leg("hl", "hyperliquid", 0.000001), leg("lt", "lighter", 0.0)), CFG)["status"] == "unprofitable"
    flicker = leg("hl", "hyperliquid", 0.0001)
    flicker["funding_hourly"][str(H0 + 166)] = -0.001  # the spread did not pay two windows ago
    t = pair_metrics(pair(flicker, leg("lt", "lighter", 0.0)), CFG)
    assert t["status"] == "transient" and not t["persistent"]


def test_provisional_oracle_is_stamped_on_the_pair() -> None:
    m = pair_metrics(pair(leg("hl", "hyperliquid", 0.0001, oracle="WARMING"), leg("lt", "lighter", 0.0)), CFG)
    assert m["oracle"] == "provisional"


def test_rank_gives_both_lists_and_caps_them() -> None:
    ps = [pair(leg(f"hl{i}", "hyperliquid", 0.0001 * (i + 1)), leg(f"lt{i}", "lighter", 0.0), spread_stdev_bps=float(50 - i)) for i in range(30)]
    out = rank(ps, PairConfig(min_overlap_hours=50, top_n=20))
    assert len(out["capital_apr"]) == 20 and len(out["score"]) == 20 and len(out["all_ranked"]) == 30
    caps = [r["metrics"]["capital_apr_pct"] for r in out["capital_apr"]]
    assert caps == sorted(caps, reverse=True)
    scores = [r["metrics"]["score"] for r in out["score"]]
    assert scores == sorted(scores, reverse=True)


def _walk(seed: int, n: int = 400, base: np.ndarray | None = None, noise: float = 0.0) -> dict[int, float]:
    rng = np.random.default_rng(seed)
    steps = rng.normal(0, 0.01, n) if base is None else base + rng.normal(0, noise, n)
    return {H0 + i: float(100 * math.exp(x)) for i, x in enumerate(np.cumsum(steps))}


def test_clustering_puts_the_same_asset_together_and_unrelated_ones_apart() -> None:
    rng = np.random.default_rng(1)
    common = rng.normal(0, 0.01, 400)
    closes = {"hl:BTC": _walk(2, base=common, noise=0.0005), "lt:BTC": _walk(3, base=common, noise=0.0005), "hl:ODD": _walk(4)}
    ids, corr, overlap = correlation_matrix(closes, 50)
    i, j = ids.index("hl:BTC"), ids.index("lt:BTC")
    assert corr[i, j] > 0.95 and overlap[i, j] == 399
    cl = cluster(ids, corr, 0.75)
    assert cl["hl:BTC"] == cl["lt:BTC"] != cl["hl:ODD"]


def test_build_pairs_needs_a_hyperliquid_leg_and_rejects_a_namesake() -> None:
    rng = np.random.default_rng(5)
    common = rng.normal(0, 0.01, 400)
    legs = {"hyperliquid:native:BTC": leg("hyperliquid:native:BTC", "hyperliquid", 0.0001),
            "lighter:BTC-PERP": leg("lighter:BTC-PERP", "lighter", 0.0, dex=""),
            "backpack:BTC-PERP": leg("backpack:BTC-PERP", "backpack", -0.0001, dex=""),
            "hyperliquid:spot:@1": leg("hyperliquid:spot:@1", "hyperliquid", 0.0, dex="spot", kind="spot")}
    closes = {"hyperliquid:native:BTC": _walk(6, base=common, noise=0.0003), "lighter:BTC-PERP": _walk(7, base=common, noise=0.0003),
              "backpack:BTC-PERP": _walk(8, base=common, noise=0.0003), "hyperliquid:spot:@1": _walk(9)}  # the spot token is a namesake
    out = build_pairs(legs, {k: None for k in legs}, closes, PairConfig(min_overlap_hours=50))
    ids = {p["pair_id"] for p in out["pairs"]}
    assert not any("lighter" in i and "backpack" in i for i in ids)  # no pair without a Hyperliquid leg
    spot = next(p for p in out["pairs"] if p["kind"] == "spot-perp")
    assert spot["same_cluster"] is False and pair_metrics(spot, PairConfig(min_overlap_hours=50))["reject_code"] == "cross_cluster"
    good = next(p for p in out["pairs"] if p["kind"] == "perp-perp" and "lighter" in p["pair_id"])
    assert good["same_cluster"] and good["corr"] > 0.95 and good["short"]["venue"] == "hyperliquid"  # higher funding is the short
    assert {m["leg_id"] for m in out["clusters"]["members"]} == set(closes)


# --- candle readers (response shapes as verified live) ---------------------------------------------------------------

def test_candle_readers_per_venue() -> None:
    _cache.clear()
    t_ms = H0 * 3600 * 1000

    def handle(r: httpx.Request) -> httpx.Response:
        host, path = r.url.host, r.url.path
        if "hyperliquid" in host:
            b = body(r)
            if b.get("type") == "candleSnapshot":
                return httpx.Response(200, json=[{"t": t_ms, "c": "100.5"}, {"t": t_ms + 3_600_000, "c": "101"}])
            return httpx.Response(200, json=[{"universe": []}, []])
        if "zklighter" in host:
            if path.endswith("/orderBooks"):
                return httpx.Response(200, json=fixture("lighter_order_books"))
            if path.endswith("/candles"):
                return httpx.Response(200, json={"code": 200, "r": "1h", "c": [{"t": t_ms, "c": 99.0}]})
        if "backpack" in host:
            if path == "/api/v1/markets":
                return httpx.Response(200, json=fixture("backpack_markets"))
            if path == "/api/v1/klines":
                return httpx.Response(200, json=[{"start": "2025-11-24 08:00:00", "close": "98.5"}])
        if "orderly" in host:
            if path == "/v1/public/info":
                return httpx.Response(200, json=fixture("orderly_info"))
            if path == "/tv/history":
                return httpx.Response(200, json={"s": "ok", "t": [H0 * 3600], "c": [97.0]})
        return httpx.Response(404)

    async def go() -> list[dict[int, float]]:
        out = []
        for cls, market in ((HyperliquidVenue, "BTC"), (LighterVenue, "BTC-PERP"), (BackpackVenue, "BTC-PERP"), (OrderlyVenue, "BTC-PERP")):
            v = cls(settings(), transport=Router(handle).transport())
            v._client = httpx.AsyncClient(base_url=v.base_url, transport=Router(handle).transport())
            try:
                out.append(await hourly_closes(v, market, 30, symbol=market))
            finally:
                await v.disconnect()
        return out

    hl, lt, bp, oy = asyncio.run(go())
    assert hl == {H0: 100.5, H0 + 1: 101.0} and lt == {H0: 99.0} and oy == {H0: 97.0}
    assert list(bp.values()) == [98.5]


def test_orderbook_depth_helper_used_for_legs() -> None:
    b = OrderBook.build("x", "BTC-PERP", [(99.9, 10), (98.0, 10)], [(100.1, 10), (102.0, 10)])
    bd, ad, complete = b.depth_within(1.0)
    assert bd == pytest.approx(999) and ad == pytest.approx(1001) and complete
