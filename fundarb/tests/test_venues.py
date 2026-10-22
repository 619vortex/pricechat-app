import asyncio
from typing import Any

import httpx
import pytest

from conftest import Router, body, fixture, settings
from fundarb.venues import BackpackVenue, HyperliquidVenue, LighterVenue, OrderlyVenue, TradingDisabled, VenueError, VenueUnavailable
from fundarb.venues import base as vbase

ADDR = "0x" + "11" * 20


def run(coro: Any) -> Any:
    return asyncio.run(coro)


async def connected(venue: Any) -> Any:
    await venue.connect()
    return venue


# --- Hyperliquid -----------------------------------------------------------------------------------------------------------


def hl_router(fail_first: int = 0) -> Router:
    state = {"fails": fail_first}

    def handle(r: httpx.Request) -> httpx.Response:
        if state["fails"]:
            state["fails"] -= 1
            return httpx.Response(503)
        t = body(r)["type"]
        if t == "metaAndAssetCtxs":
            return httpx.Response(200, json=fixture("hl_meta_ctxs"))
        if t == "fundingHistory":
            return httpx.Response(200, json=fixture("hl_funding_history"))
        if t == "l2Book":
            return httpx.Response(200, json=fixture("hl_l2book"))
        if t == "userFees":
            return httpx.Response(200, json=fixture("hl_user_fees"))
        if t == "clearinghouseState":
            return httpx.Response(200, json={"marginSummary": {"accountValue": "1250.5"}, "withdrawable": "900.25", "assetPositions": [
                {"position": {"coin": "BTC", "szi": "-0.01", "entryPx": "83000.0", "unrealizedPnl": "-5.4"}},
                {"position": {"coin": "ETH", "szi": "0.0", "entryPx": None, "unrealizedPnl": "0"}}]})
        if t == "openOrders":
            return httpx.Response(200, json=[{"coin": "BTC", "side": "B", "limitPx": "80000.0", "sz": "0.002", "oid": 77, "timestamp": 1}])
        return httpx.Response(400, json={"error": "unknown"})
    return Router(handle)


def test_hyperliquid_quote_from_real_responses() -> None:
    r = hl_router()
    meta, ctxs = fixture("hl_meta_ctxs")
    btc = ctxs[[u["name"] for u in meta["universe"]].index("BTC")]

    async def go() -> Any:
        v = await connected(HyperliquidVenue(settings(hl_account_address=ADDR), transport=r.transport()))
        return await v.quote("BTC-PERP"), await v.symbols()
    q, syms = run(go())
    assert q.funding_rate_raw == float(btc["funding"]) and q.funding_interval_hours == 1.0 and q.funding_kind == "predicted"
    assert q.funding_interval_source == "derived: spacing of fundingHistory timestamps"
    assert q.funding_apr == pytest.approx(float(btc["funding"]) * 8760)
    assert q.mark_price == float(btc["markPx"]) and q.index_price == float(btc["oraclePx"])
    levels = fixture("hl_l2book")["levels"]
    assert q.bid == float(levels[0][0]["px"]) and q.ask == float(levels[1][0]["px"]) and q.bid < q.ask
    assert (q.maker_bps, q.taker_bps) == (pytest.approx(1.5), pytest.approx(4.5)) and q.fee_source.startswith("api: userFees")
    assert "1000PEPE-PERP" in syms and "BTC-PERP" in syms


def test_hyperliquid_fees_without_an_address_are_the_published_base_tier_and_account_reads_parse() -> None:
    r = hl_router()
    base = fixture("hl_user_fees")["feeSchedule"]

    async def go() -> Any:
        anon = await connected(HyperliquidVenue(settings(), transport=r.transport()))
        fees = await anon.get_fee_schedule()
        q = await anon.quote("BTC-PERP")
        with pytest.raises(VenueUnavailable, match="FUNDARB_HL_ACCOUNT_ADDRESS"):
            await anon.get_account_balance()  # account data still needs the address
        me = await connected(HyperliquidVenue(settings(hl_account_address=ADDR), transport=r.transport()))
        return fees, q, await me.get_position("BTC-PERP"), await me.get_position("ETH-PERP"), await me.get_account_balance(), await me.get_open_orders()
    fees, q, btc, eth, bal, orders = run(go())
    assert (fees.maker_bps, fees.taker_bps) == (pytest.approx(float(base["add"]) * 1e4), pytest.approx(float(base["cross"]) * 1e4))
    assert "published base tier" in fees.source and q.taker_bps == pytest.approx(4.5)
    assert btc.size == -0.01 and btc.entry_price == 83000.0 and btc.unrealized_pnl == -5.4 and eth is None
    assert bal.equity_usd == 1250.5 and bal.available_usd == 900.25
    assert orders[0].side == "buy" and orders[0].order_id == "77" and orders[0].symbol == "BTC-PERP"


def test_retries_5xx_with_backoff_but_not_4xx(monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_sleep(_: float) -> None:
        return None
    monkeypatch.setattr(vbase.asyncio, "sleep", no_sleep)
    r = hl_router(fail_first=2)
    run(connected(HyperliquidVenue(settings(), transport=r.transport())))  # two 503s, then fine
    assert len(r.calls) == 3
    bad = Router(lambda req: httpx.Response(422, text="bad request"))
    with pytest.raises(VenueError, match="HTTP 422"):
        run(connected(HyperliquidVenue(settings(), transport=bad.transport())))
    assert len(bad.calls) == 1


# --- Lighter ---------------------------------------------------------------------------------------------------------------


def lighter_router() -> Router:
    def handle(r: httpx.Request) -> httpx.Response:
        path = r.url.path
        name = {"/api/v1/orderBooks": "lighter_order_books", "/api/v1/orderBookDetails": "lighter_details_btc",
                "/api/v1/fundings": "lighter_fundings_btc", "/api/v1/orderBookOrders": "lighter_book_btc"}.get(path)
        return httpx.Response(200, json=fixture(name)) if name else httpx.Response(404)
    return Router(handle)


def test_lighter_reads_percent_rates_signed_by_direction_and_fee_percentages() -> None:
    r = lighter_router()
    fundings = sorted(fixture("lighter_fundings_btc")["fundings"], key=lambda f: f["timestamp"])
    last = fundings[-1]
    sign = 1 if last["direction"] == "long" else -1
    details = fixture("lighter_details_btc")["order_book_details"][0]

    async def go() -> Any:
        v = await connected(LighterVenue(settings(), transport=r.transport()))
        return await v.quote("BTC-PERP"), await v.get_fee_schedule()
    q, fees = run(go())
    assert q.funding_rate_raw == pytest.approx(sign * float(last["rate"]) / 100) and q.funding_kind == "last_settled"
    assert q.funding_interval_hours == 1.0 and "spacing of /fundings" in q.funding_interval_source
    assert q.mark_price == float(details["mark_price"]) and q.index_price == float(details["index_price"])
    assert q.bid < q.ask and q.depth_usd_1pct > 0
    btc = next(b for b in fixture("lighter_order_books")["order_books"] if b["symbol"] == "BTC")
    assert fees.for_symbol("BTC-PERP") == (float(btc["maker_fee"]) * 100, float(btc["taker_fee"]) * 100)
    assert q.fee_source == "api: /orderBooks maker_fee/taker_fee (percent)"
    limit = next(c for c in r.calls if c.url.path.endswith("orderBookOrders")).url.params["limit"]
    assert int(limit) <= 250  # the venue rejects more (verified: limit=500 -> "invalid param")


def test_lighter_short_direction_is_negative() -> None:
    def handle(r: httpx.Request) -> httpx.Response:
        if r.url.path.endswith("/fundings"):
            return httpx.Response(200, json={"code": 200, "fundings": [{"timestamp": 1792566000 + h * 3600, "rate": "0.0030",
                                                                         "direction": "short"} for h in range(5)]})
        return lighter_router()._handler(r)
    f = run(_lighter_rate(Router(handle)))
    assert f.rate_raw == pytest.approx(-0.00003)


async def _lighter_rate(r: Router) -> Any:
    v = await connected(LighterVenue(settings(), transport=r.transport()))
    return await v.get_funding_rate("BTC-PERP")


# --- Orderly ----------------------------------------------------------------------------------------------------------------


def orderly_router() -> Router:
    def handle(r: httpx.Request) -> httpx.Response:
        p = r.url.path
        if p == "/v1/public/info":
            return httpx.Response(200, json=fixture("orderly_info"))
        if p.startswith("/v1/public/funding_rate/"):
            return httpx.Response(200, json=fixture("orderly_funding_btc"))
        if p.startswith("/v1/public/futures/"):
            return httpx.Response(200, json=fixture("orderly_futures_btc"))
        if p.startswith("/v1/orderbook/"):
            return httpx.Response(200, json=fixture("orderly_book_noauth"))
        return httpx.Response(404)
    return Router(handle)


def test_orderly_interval_comes_from_funding_period_and_missing_pieces_say_why() -> None:
    r = orderly_router()
    info = next(x for x in fixture("orderly_info")["data"]["rows"] if x["symbol"] == "PERP_BTC_USDC")

    async def go() -> Any:
        v = await connected(OrderlyVenue(settings(), transport=r.transport()))
        f = await v.get_funding_rate("BTC-PERP")
        with pytest.raises(VenueUnavailable, match="authenticated Orderly account"):
            await v.quote("BTC-PERP")
        with pytest.raises(VenueUnavailable, match="authenticated"):
            await v.get_fee_schedule()
        return f, await v.get_mark_price("BTC-PERP"), await v.get_index_price("BTC-PERP")
    f, mark, index = run(go())
    assert f.interval_hours == float(info["funding_period"]) == 8.0 and f.interval_source == "api: /v1/public/info funding_period"
    assert f.rate_raw == fixture("orderly_funding_btc")["data"]["est_funding_rate"] and f.kind == "predicted"
    fut = fixture("orderly_futures_btc")["data"]
    assert mark == fut["mark_price"] and index == fut["index_price"]


# --- Backpack ---------------------------------------------------------------------------------------------------------------


def backpack_router() -> Router:
    def handle(r: httpx.Request) -> httpx.Response:
        p = r.url.path
        if p == "/api/v1/markets":
            return httpx.Response(200, json=fixture("backpack_markets"))
        if p == "/api/v1/markPrices":
            return httpx.Response(200, json=fixture("backpack_mark_btc"))
        if p == "/api/v1/depth":
            return httpx.Response(200, json=fixture("backpack_depth_btc"))
        return httpx.Response(404)
    return Router(handle)


def test_backpack_interval_is_read_from_the_market_and_logged(caplog: pytest.LogCaptureFixture) -> None:
    r = backpack_router()
    mk = next(m for m in fixture("backpack_markets") if m["symbol"] == "BTC_USDC_PERP")
    mark = fixture("backpack_mark_btc")[0]

    async def go() -> Any:
        v = await connected(BackpackVenue(settings(), transport=r.transport()))
        return await v.quote("BTC-PERP"), await v.symbols()
    with caplog.at_level("WARNING", logger="fundarb.venues.backpack"):
        q, syms = run(go())
    assert q.funding_interval_hours == mk["fundingInterval"] / 3_600_000 and q.funding_interval_source == "api: /api/v1/markets fundingInterval"
    assert "fundingInterval" in caplog.text and "ms" in caplog.text
    assert q.funding_rate_raw == float(mark["fundingRate"]) and q.mark_price == float(mark["markPrice"])
    depth = fixture("backpack_depth_btc")
    assert q.bid == max(float(p) for p, _ in depth["bids"]) and q.ask == min(float(p) for p, _ in depth["asks"])  # bids came worst-first
    assert q.maker_bps is None and "authenticated" in q.fee_source
    assert "SOL-PERP" in syms and all(s.endswith("-PERP") for s in syms) and len(syms) == 3  # the SOL_USDC spot market isn't a perp


def test_backpack_uses_the_markets_own_name() -> None:
    def handle(r: httpx.Request) -> httpx.Response:
        if r.url.path == "/api/v1/markets":
            m = dict(next(x for x in fixture("backpack_markets") if x["symbol"] == "BTC_USDC_PERP"), symbol="kPEPE_USDC_PERP")
            return httpx.Response(200, json=[m])
        if r.url.path == "/api/v1/markPrices":
            assert r.url.params["symbol"] == "kPEPE_USDC_PERP"
            return httpx.Response(200, json=[{"symbol": "kPEPE_USDC_PERP", "fundingRate": "0.0001", "markPrice": "0.0046", "indexPrice": "0.0046"}])
        return httpx.Response(404)

    async def go() -> Any:
        v = await connected(BackpackVenue(settings(), transport=Router(handle).transport()))
        return await v.get_funding_rate("1000PEPE-PERP")
    assert run(go()).rate_raw == 0.0001


# --- orders are gated ---------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("cls,router", [(HyperliquidVenue, hl_router), (LighterVenue, lighter_router), (OrderlyVenue, orderly_router),
                                        (BackpackVenue, backpack_router)])
def test_no_order_can_be_placed(cls: Any, router: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    r = router()

    async def go(v: Any) -> None:
        await v.connect()
        sent = len(r.calls)
        for call in (v.place_order("BTC-PERP", "buy", 0.001, 80000.0), v.cancel_order("BTC-PERP", "1"), v.cancel_all()):
            with pytest.raises(TradingDisabled):
                await call
        assert len(r.calls) == sent  # nothing went to the venue
    run(go(cls(settings(), transport=r.transport())))
    monkeypatch.setenv("LIVE_TRADING", "true")
    monkeypatch.setenv("LIVE_CONFIRM_TOKEN", "armed")

    async def armed(v: Any) -> None:
        await v.connect()
        with pytest.raises(NotImplementedError, match="Phase 4"):
            await v.place_order("BTC-PERP", "buy", 0.001, 80000.0)
    run(armed(cls(settings(), transport=r.transport())))
