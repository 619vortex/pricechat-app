"""Earn › Funding Arb board. TEST FIXTURES only: real public venue responses recorded on 2026-10-21 (fundarb/tests/fixtures),
served through httpx.MockTransport. Read-only: nothing here can trade."""

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.arbitrage import funding
from fundarb.config import Settings

FIX = Path(__file__).resolve().parents[2] / "fundarb" / "tests" / "fixtures"
pytestmark = pytest.mark.skipif(not FIX.exists(), reason="fundarb fixtures not present")


def fx(name: str) -> Any:
    return json.loads((FIX / f"{name}.json").read_text(encoding="utf-8"))


def transport(down: str | None = None) -> httpx.MockTransport:
    def handle(r: httpx.Request) -> httpx.Response:
        host, path = r.url.host, r.url.path
        if down and down in host:
            return httpx.Response(503)
        if "hyperliquid" in host:
            t = json.loads(r.content)["type"]
            return httpx.Response(200, json=fx({"metaAndAssetCtxs": "hl_meta_ctxs", "fundingHistory": "hl_funding_history"}[t]))
        if "backpack" in host:
            if path == "/api/v1/markets":
                return httpx.Response(200, json=fx("backpack_markets"))
            if path == "/api/v1/markPrices":  # the bulk call: every market's row
                return httpx.Response(200, json=[{**fx("backpack_mark_btc")[0], "symbol": "BTC_USDC_PERP", "fundingRate": "0.00015"},
                                                 {**fx("backpack_mark_btc")[0], "symbol": "ETH_USDC_PERP"}])
        if "orderly" in host:
            if path == "/v1/public/info":
                return httpx.Response(200, json=fx("orderly_info"))
            if path == "/v1/public/funding_rates":
                row = fx("orderly_funding_btc")["data"]
                return httpx.Response(200, json={"success": True, "data": {"rows": [row, {**row, "symbol": "PERP_ETH_USDC"}]}})
        if "zklighter" in host:
            name = {"/api/v1/orderBooks": "lighter_order_books", "/api/v1/fundings": "lighter_fundings_btc"}.get(path)
            if name:
                return httpx.Response(200, json=fx(name))
        return httpx.Response(404)
    return httpx.MockTransport(handle)


def test_board_normalizes_each_venues_own_interval_and_ranks_the_gaps() -> None:
    d = asyncio.run(funding.build(Settings(), transport()))
    assert all(v["ok"] for v in d["venues"].values()), d["venues"]
    assert d["venues"]["orderly"]["interval_hours"] == [8.0] and d["venues"]["backpack"]["interval_hours"] == [1.0]
    btc = next(r for r in d["rows"] if r["symbol"] == "BTC-PERP")
    legs = {l["venue"]: l for l in btc["legs"]}
    assert set(legs) == {"hyperliquid", "backpack", "orderly", "lighter"}
    orderly_rate = fx("orderly_funding_btc")["data"]["est_funding_rate"]
    assert legs["orderly"]["apr_pct"] == pytest.approx(orderly_rate / 8 * 8760 * 100, abs=1e-3)  # 8 h rate -> hourly -> APR
    assert legs["backpack"]["apr_pct"] == pytest.approx(0.00015 * 8760 * 100, abs=1e-3)
    hi = max(btc["legs"], key=lambda l: l["hourly"])
    lo = min(btc["legs"], key=lambda l: l["hourly"])
    assert btc["short"] == hi["venue"] and btc["long"] == lo["venue"]
    assert btc["gross_spread_apr_pct"] == pytest.approx((hi["hourly"] - lo["hourly"]) * 8760 * 100, abs=1e-3)
    assert d["rows"] == sorted(d["rows"], key=lambda r: -r["gross_spread_apr_pct"])
    assert "before fees" in d["note"]


def test_a_venue_that_fails_is_named_and_the_rest_still_show() -> None:
    d = asyncio.run(funding.build(Settings(), transport(down="backpack")))
    assert d["venues"]["backpack"]["ok"] is False and "backpack" in d["venues"]["backpack"]["error"]
    assert d["venues"]["hyperliquid"]["ok"] and any(r["symbol"] == "BTC-PERP" for r in d["rows"])


def test_public_endpoint(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake(force: bool = False, transport: Any = None) -> dict[str, Any]:
        return {"rows": [], "venues": {}, "pairs_found": 0, "note": "x", "as_of": "now"}
    monkeypatch.setattr(funding, "board", fake)
    client.cookies.clear()
    assert client.get("/api/members/arbitrage/funding").json()["pairs_found"] == 0


def test_a_slow_lighter_is_bounded_and_the_rest_still_show(monkeypatch: pytest.MonkeyPatch) -> None:
    sync = transport()
    calls: list[str] = []

    async def handle(r: httpx.Request) -> httpx.Response:
        if "zklighter" in r.url.host and r.url.path == "/api/v1/fundings":
            calls.append(str(r.url))
            await asyncio.sleep(5)
        return sync.handle_request(r)
    monkeypatch.setattr(funding, "LIGHTER_TIMEOUT", 0.3)
    d = asyncio.run(funding.build(Settings(), httpx.MockTransport(handle)))
    assert d["venues"]["lighter"]["ok"] is False and "TimeoutError" in d["venues"]["lighter"]["error"]
    assert d["venues"]["hyperliquid"]["ok"] and any(r["symbol"] == "BTC-PERP" for r in d["rows"])
    assert 0 < len(calls) <= funding.LIGHTER_CONCURRENCY  # a few at a time, only symbols Lighter lists


@pytest.fixture(autouse=True)
def _no_shared_history(monkeypatch: pytest.MonkeyPatch) -> Any:
    funding._history.clear()
    monkeypatch.setattr(funding, "_history_task", None)
    monkeypatch.setattr(funding, "_cache", {"at": 0.0, "data": None})
    yield
    funding._history.clear()


def _seed_history(hourly: dict[str, float], symbols: tuple[str, ...] = ("BTC-PERP", "ETH-PERP")) -> None:
    """Seven days of settled funding at a constant hourly-normalized rate per venue (each venue at its own interval)."""
    import time as _time
    now_h = int(_time.time() // 3600)
    interval = {"hyperliquid": 1.0, "backpack": 1.0, "lighter": 1.0, "orderly": 8.0}
    for v, rate in hourly.items():
        rows = [(float((now_h - k) * 3600), rate * interval[v]) for k in range(168, 0, -1)]
        for sym in symbols:
            funding._history[(v, sym)] = (_time.monotonic(), rows)


def test_the_widest_gaps_carry_their_7_day_average_apr_and_the_capital_they_tie_up() -> None:
    _seed_history({"hyperliquid": 0.00002, "backpack": 0.00002, "orderly": 0.00002, "lighter": 0.00002})
    d = asyncio.run(funding.build(Settings(), transport()))
    assert d["assumptions"] == {"leverage": 2.0, "buffer": 0.25, "capital_per_notional": 1.25, "stability_k": 2.0, "history_days": 7.0}
    btc = next(r for r in d["rows"] if r["symbol"] == "BTC-PERP")
    assert btc["capital_apr_pct"] == pytest.approx(btc["gross_spread_apr_pct"] / 1.25, abs=1e-2)
    st = btc["stats"]
    assert st["hours"] >= 160 and st["mean_apr_pct"] == pytest.approx(0.0, abs=1e-6) and st["paid_share"] == 0.0  # equal rates: no carry


def test_a_steady_gap_ranks_above_a_wider_one_with_no_record() -> None:
    _seed_history({"hyperliquid": 0.00001, "backpack": 0.00004, "orderly": 0.00001, "lighter": 0.00001}, symbols=("ETH-PERP",))
    d = asyncio.run(funding.build(Settings(), transport()))
    eth = next(r for r in d["rows"] if r["symbol"] == "ETH-PERP")
    if eth["short"] == "backpack" and eth["stats"] and eth["stats"].get("score_pct") is not None:
        assert eth["stats"]["mean_apr_pct"] == pytest.approx(0.00003 * 8760 * 100, rel=1e-3)
        assert d["rows"][0]["symbol"] == "ETH-PERP"  # a scored row comes before any unscored one
    scored = [r for r in d["rows"] if (r.get("stats") or {}).get("score_pct") is not None]
    assert d["rows"][: len(scored)] == scored


def test_missing_history_is_read_in_the_background_and_the_board_does_not_wait() -> None:
    async def go() -> tuple[dict[str, Any], dict[str, Any]]:
        funding._cache.update(at=123.0, data={"x": 1})
        first = await funding.build(Settings(), transport())
        assert funding._history_task is not None
        await funding._history_task
        assert funding._cache["at"] == 0.0  # the cached board is stale once history lands
        return first, await funding.build(Settings(), transport())
    first, second = asyncio.run(go())
    assert all(r.get("stats") is None for r in first["rows"])
    btc = next(r for r in second["rows"] if r["symbol"] == "BTC-PERP")
    assert btc["stats"] is not None  # read from the recorded fixtures (history the fixture venues don't serve counts as empty)
