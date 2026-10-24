"""Module 1 universe discovery, from real Hyperliquid responses recorded 2026-10-24 (trimmed: native, xyz, flx and hyna dexes)."""

import asyncio
from pathlib import Path
from typing import Any

import httpx
import pytest
from conftest import Router, body, fixture, settings

from fundarb.universe import AssetClass, HyperliquidUniverse, SchemaGuard, classify

DEXES = {"": "native", "xyz": "xyz", "flx": "flx", "hyna": "hyna"}


def router(down: str | None = None, categories: Any = None, drop_field: str | None = None) -> Router:
    def handle(r: httpx.Request) -> httpx.Response:
        b = body(r)
        t = b.get("type")
        if t == "perpDexs":
            return httpx.Response(200, json=fixture("hl_perp_dexs"))
        if t == "perpCategories":
            return httpx.Response(200, json=fixture("hl_perp_categories") if categories is None else categories)
        if t == "spotMeta":
            return httpx.Response(200, json=fixture("hl_spot_meta_tokens"))
        if t == "perpsAtOpenInterestCap":
            return httpx.Response(200, json=fixture("hl_perps_at_oi_cap"))
        if t == "metaAndAssetCtxs":
            dex = b.get("dex", "")
            if down is not None and dex == down:
                return httpx.Response(422, text="bad dex")
            meta, ctxs = fixture(f"hl_meta_ctxs_{DEXES[dex]}")
            if drop_field:
                ctxs = [{k: v for k, v in c.items() if k != drop_field} for c in ctxs]
            return httpx.Response(200, json=[meta, ctxs])
        if t == "fundingHistory":
            dex = b["coin"].split(":")[0] if ":" in b["coin"] else ""
            return httpx.Response(200, json=fixture(f"hl_funding_history_{DEXES[dex]}"))
        return httpx.Response(404)
    return Router(handle)


def fetch(tmp_path: Path, r: Router | None = None) -> Any:
    uni = HyperliquidUniverse(settings(universe_schema_path=tmp_path / "schema.json"), transport=(r or router()).transport())
    return uni, asyncio.run(uni.fetch())


def test_classification_rules_one_class_each() -> None:
    cats = {"xyz:TSLA": "stocks", "xyz:AAPL": "stock", "xyz:EUR": "FX", "xyz:SP500": "indices", "para:10Y": "rates", "x:NEW": "weather"}
    assert classify("", "BTC", cats) == (AssetClass.CRYPTO, "native dex")
    assert classify("xyz", "xyz:TSLA", cats)[0] is AssetClass.EQUITY
    assert classify("xyz", "xyz:AAPL", cats)[0] is AssetClass.EQUITY  # the registry's singular spelling
    assert classify("xyz", "xyz:EUR", cats)[0] is AssetClass.FX  # upper-case variant
    assert classify("xyz", "xyz:SP500", cats)[0] is AssetClass.INDEX
    assert classify("para", "para:10Y", cats)[0] is AssetClass.RATES
    assert classify("xyz", "xyz:MYSTERY", cats) == (AssetClass.UNCLASSIFIED, "not in perpCategories")  # never guessed from the name
    assert classify("x", "x:NEW", cats) == (AssetClass.UNCLASSIFIED, "unknown registry category 'weather'")


def test_universe_from_recorded_responses(tmp_path: Path) -> None:
    _, snap = fetch(tmp_path)
    assert set(snap.dexes) == {"", "xyz", "flx", "hyna"} and not snap.failures
    by = {m.raw_name: m for m in snap.markets}
    btc, idx = by["BTC"], by["xyz:XYZ100"]
    meta, ctxs = fixture("hl_meta_ctxs_native")
    ctx = ctxs[[u["name"] for u in meta["universe"]].index("BTC")]
    assert btc.asset_class is AssetClass.CRYPTO and btc.builder is None and btc.collateral_token == "USDC"
    assert btc.funding_interval_hours == 1.0 and "fundingHistory" in btc.funding_interval_source
    assert btc.funding_apr == pytest.approx(float(ctx["funding"]) * 8760)
    assert btc.open_interest_usd == pytest.approx(float(ctx["openInterest"]) * float(ctx["oraclePx"]))
    assert btc.mark_oracle_basis_bps == pytest.approx((float(ctx["markPx"]) - float(ctx["oraclePx"])) / float(ctx["oraclePx"]) * 1e4)
    assert btc.max_leverage == float(meta["universe"][0]["maxLeverage"])  # read live, not assumed
    assert idx.asset_class is AssetClass.INDEX and idx.namespace == "xyz" and idx.builder == "XYZ" and idx.oi_cap_usd and idx.oi_cap_usd > 0
    assert all(m.collateral_token == "USDH" for m in snap.markets if m.namespace == "flx")  # not every dex margins in USDC
    assert all(m.collateral_token == "USDE" for m in snap.markets if m.namespace == "hyna")
    assert all(len({m.asset_class}) == 1 for m in snap.markets)
    assert any(not m.listing_live for m in snap.markets)  # delisted markets are reported, not dropped


def test_a_failed_dex_is_named_and_the_rest_still_load(tmp_path: Path) -> None:
    _, snap = fetch(tmp_path, router(down="xyz"))
    assert "metaAndAssetCtxs[xyz]" in snap.failures
    assert "xyz" not in snap.dexes and any(m.namespace == "" for m in snap.markets)


def test_without_the_registry_builder_markets_stay_unclassified(tmp_path: Path) -> None:
    _, snap = fetch(tmp_path, router(categories=[]))
    builder = [m for m in snap.markets if m.namespace]
    assert builder and all(m.asset_class is AssetClass.UNCLASSIFIED for m in builder)
    assert all(m.asset_class is AssetClass.CRYPTO for m in snap.markets if not m.namespace)


def test_a_missing_field_is_none_never_zero(tmp_path: Path) -> None:
    _, snap = fetch(tmp_path, router(drop_field="funding"))
    assert all(m.funding_rate_raw is None and m.funding_apr is None for m in snap.markets)


def test_schema_guard_refuses_ranking_until_confirmed_and_on_drift(tmp_path: Path) -> None:
    uni, first = fetch(tmp_path)
    assert first.schema.status == "new" and not first.ranking_allowed
    uni.guard.accept(first.schema)
    _, again = fetch(tmp_path)
    assert again.schema.status == "ok" and again.ranking_allowed
    _, drifted = fetch(tmp_path, router(drop_field="oraclePx"))  # a silent rename/removal upstream
    assert drifted.schema.status == "changed" and not drifted.ranking_allowed
    assert drifted.schema.removed == {"assetCtx": ["oraclePx"]}


def test_schema_fingerprint_is_order_independent() -> None:
    assert SchemaGuard.fingerprint({"a": ["x", "y"], "b": []}) == SchemaGuard.fingerprint({"b": [], "a": ["x", "y"]})


def test_batched_call_count_and_cached_intervals(tmp_path: Path) -> None:
    r = router()
    uni = HyperliquidUniverse(settings(universe_schema_path=tmp_path / "s.json"), transport=r.transport())
    asyncio.run(uni.fetch())
    types = [body(c).get("type") for c in r.calls]
    first, n_dex = len(types), len(DEXES)
    assert types.count("metaAndAssetCtxs") == n_dex  # one batched call per dex, never one per market
    assert 1 <= types.count("fundingHistory") <= n_dex  # the interval: once per dex that has a live market
    asyncio.run(uni.fetch())
    again = [body(c).get("type") for c in r.calls[first:]]
    assert "fundingHistory" not in again and len(again) == 4 + n_dex  # intervals cached; 4 registry calls + one per dex
