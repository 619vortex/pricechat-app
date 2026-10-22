"""Market structure (app/tradingos/structure.py): aggregated open interest, cross-venue CVD, order-book walls and imbalance,
reported liquidations, ESTIMATED liquidation zones, TWAP-like patterns, the recorder + heatmap, the /chart layers, the
Content Studio visuals (marketviz) and the bot channel footer. No network: fake exchanges answer canned public-API JSON;
one of them can be made to refuse (a region block) to prove a missing venue is reported, never filled in."""

import asyncio
import time
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.tradingos import TsBookSnapshot, TsLiquidation
from app.tradingos import bot, chartcmd, liqtape, market, structure as ms

from tests.test_trading_os import FakeMD, FakeTG, uptrend_pullback

H = 3_600_000
NOW = (int(time.time() * 1000) // H) * H
TS = [NOW - (47 - i) * H for i in range(48)]  # 48 hourly stamps, oldest first
PX = 100.0


class MD(FakeMD):
    def levels(self, coin: str, sig_figs: int = 5) -> dict[str, list[tuple[float, float]]]:
        return {"bids": [(PX - 0.05 * i, 10.0) for i in range(1, 21)], "asks": [(PX + 0.05 * i, 10.0) for i in range(1, 21)]}


def _book(step: float, n: int, qty: float, wall: tuple[int, float] | None = None) -> tuple[list[list[str]], list[list[str]]]:
    bids = [[f"{PX - step * i:.4f}", str(wall[1] if wall and i == wall[0] else qty)] for i in range(1, n + 1)]
    asks = [[f"{PX + step * i:.4f}", str(qty)] for i in range(1, n + 1)]
    return bids, asks


class FakeVenues(ms.Venues):
    """TEST FIXTURE: canned answers for the public endpoints structure.py reads."""

    def __init__(self, blocked: tuple[str, ...] = (), liq: list[dict[str, Any]] | None = None, trades: list[list[Any]] | None = None) -> None:
        super().__init__()
        self.blocked, self.liq, self.trades = blocked, liq, trades
        self.calls: list[str] = []

    def get(self, url: str, params: dict[str, Any], ttl: float = 20.0) -> Any:  # noqa: C901 - one canned answer per endpoint
        self.calls.append(url)
        host = next(v for v in ("binance", "bybit", "okx", "deribit") if v in url)
        if host in self.blocked:
            raise ms.StructureError("blocks requests from this server's region")
        path = url.split(".com", 1)[1]
        if path == "/fapi/v1/openInterest":
            return {"openInterest": "1000"}
        if path == "/fapi/v1/premiumIndex":
            return {"markPrice": str(PX)}
        if path == "/futures/data/openInterestHist":
            return [{"timestamp": t, "sumOpenInterest": str(1000 + i * (5 if i != 30 else -50)), "sumOpenInterestValue": str((1000 + i) * PX)}
                    for i, t in enumerate(TS)]
        if path == "/fapi/v1/klines":
            # [open time, o, h, l, c, vol, close time, quote vol, trades, taker buy base, taker buy quote, _]
            return [[t - H, "100", "101", "99" if i != 40 else "80", "100", "10", t - 1, "1000", 5, "6", "600", "0"] for i, t in enumerate(TS)]
        if path == "/fapi/v1/depth":
            b, a = _book(0.01, 1000, 1.0, wall=(50, 400.0))
            return {"bids": b, "asks": a}
        if path == "/fapi/v1/aggTrades":
            return [r for r in self.trades or [] if params["startTime"] <= r["T"] <= params["endTime"]][:1000]
        if path == "/v5/market/tickers":
            return {"retCode": 0, "result": {"list": [{"openInterestValue": "50000"}]}}
        if path == "/v5/market/open-interest":
            return {"retCode": 0, "result": {"list": [{"timestamp": str(t), "openInterest": "500"} for t in reversed(TS)]}}
        if path == "/v5/market/kline":
            return {"retCode": 0, "result": {"list": [[str(t), "100", "100", "100", "100", "1", "100"] for t in reversed(TS)]}}
        if path == "/v5/market/orderbook":
            b, a = _book(0.01, 500, 2.0)
            return {"retCode": 0, "result": {"b": b, "a": a}}
        if path == "/api/v5/public/open-interest":
            return {"code": "0", "data": [{"oiUsd": "30000"}]}
        if path == "/api/v5/rubik/stat/contracts/open-interest-history":
            if params.get("end"):
                return {"code": "0", "data": []}
            return {"code": "0", "data": [[str(t), "0", str(300 + 47 - i), "30000"] for i, t in enumerate(reversed(TS))]}
        if path == "/api/v5/rubik/stat/taker-volume-contract":
            if params.get("end"):
                return {"code": "0", "data": []}
            return {"code": "0", "data": [[str(t - H), "300", "200"] for t in reversed(TS)]}  # [ts, sell, buy]
        if path == "/api/v5/public/instruments":
            return {"code": "0", "data": [{"ctVal": "0.01"}]}
        if path == "/api/v5/market/books-full":
            b, a = _book(0.02, 2000, 100.0)
            return {"code": "0", "data": [{"bids": [x + ["0", "1"] for x in b], "asks": [x + ["0", "1"] for x in a]}]}
        if path == "/api/v5/public/liquidation-orders":
            return {"code": "0", "data": [{"details": self.liq if self.liq is not None else [
                {"bkPx": "99", "sz": "100", "posSide": "long", "ts": str(NOW - 600_000)},
                {"bkPx": "101", "sz": "50", "posSide": "short", "ts": str(NOW - 300_000)}]}]}
        if path == "/api/v5/market/candles":  # [open ts, o, h, l, c, ...], newest first
            return {"code": "0", "data": [[str(t - H), "100", "101", "99", "100", "1", "1", "100", "1"] for t in reversed(TS)]}
        if path == "/api/v2/public/get_book_summary_by_instrument":
            return {"result": [{"open_interest": 7000}]}
        raise AssertionError(f"unexpected call {url}")


@pytest.fixture
def md(monkeypatch: pytest.MonkeyPatch) -> MD:
    f = MD({"BTC": uptrend_pullback(400, PX), "ETH": uptrend_pullback(400, PX), "SOL": uptrend_pullback(400, PX)})
    monkeypatch.setattr(market, "_shared", f)
    return f


@pytest.fixture
def vs(monkeypatch: pytest.MonkeyPatch) -> FakeVenues:
    v = FakeVenues()
    monkeypatch.setattr(ms, "_venues", v)
    return v


def test_symbols_map_hyperliquid_names_to_each_venue() -> None:
    assert ms.symbols("BTC") == {"binance": "BTCUSDT", "bybit": "BTCUSDT", "okx": "BTC-USDT-SWAP", "hyperliquid": "BTC", "deribit": "BTC-PERPETUAL"}
    k = ms.symbols("kPEPE")
    assert k["binance"] == "1000PEPEUSDT" and k["okx"] == "PEPE-USDT-SWAP" and "deribit" not in k


def test_open_interest_is_summed_and_history_only_where_every_venue_reported(md: MD, vs: FakeVenues) -> None:
    o = ms.open_interest("BTC", "1h", 48, vs, md)
    hl = md.market("BTC").oi_usd
    assert o["basis"] == "aggregated" and o["venues"] == ["Binance", "Bybit", "OKX", "Deribit", "Hyperliquid"]
    assert o["total_usd"] == pytest.approx(1000 * PX + 50000 + 30000 + 7000 + hl)
    assert o["history_venues"] == ["Binance", "Bybit", "OKX"] and len(o["history"]) == 48
    assert o["history"][0] == (TS[0], 1000 * PX + 500 * PX + 30000)
    assert any("Hyperliquid publishes no OI history" in n for n in o["notes"])


def test_a_blocked_venue_is_named_not_filled_in(md: MD) -> None:
    v = FakeVenues(blocked=("binance",))
    o = ms.open_interest("BTC", "1h", 48, v, md)
    assert "Binance" not in o["venues"] and o["skipped"]["Binance"] == "blocks requests from this server's region"
    assert o["history_venues"] == ["Bybit", "OKX"]
    c = ms.cvd("BTC", "1h", 48, v)
    assert c["venues"] == ["OKX"] and c["basis"] == "observed" and "Binance" in c["skipped"]
    with pytest.raises(ms.StructureError, match="No taker-volume feed"):
        ms.cvd("BTC", "1h", 48, FakeVenues(blocked=("binance", "okx")))


def test_cvd_adds_taker_buys_minus_sells_across_venues(vs: FakeVenues) -> None:
    c = ms.cvd("BTC", "1h", 48, vs)
    # per hour: Binance buys 600 sells 400, OKX buys 200 sells 300 -> +100 an hour
    assert c["basis"] == "aggregated" and c["venues"] == ["Binance", "OKX"]
    assert c["points"][0][1] == 100 and c["points"][-1][1] == pytest.approx(100 * len(c["points"]))
    assert c["buy_usd"] == pytest.approx(800 * len(c["points"])) and c["delta_usd"] == pytest.approx(100 * len(c["points"]))
    one = ms.cvd("BTC", "1m", 48, vs)
    assert one["venues"] == ["Binance"] and "1m taker volume" in one["skipped"]["OKX"]


def test_book_walls_imbalance_and_coverage(md: MD, vs: FakeVenues) -> None:
    b = ms.books("BTC", vs, md)
    assert b["basis"] == "aggregated" and set(b["venues"]) == {"Binance", "Bybit", "OKX", "Hyperliquid"}
    wall = b["walls"][0]
    assert wall["venue"] == "Binance" and wall["side"] == "bid" and wall["px"] == pytest.approx(PX - 0.5) and wall["usd"] == pytest.approx((PX - 0.5) * 400)
    assert all(w["x_median"] >= 10 for w in b["walls"])
    # every venue covers at least ±1%? Hyperliquid's 20 levels reach ±1%; imbalance is measured only inside the common range
    assert b["imbalance"] and all(0 < float(k.rstrip("%")) <= b["common_pct"] + 1e-9 for k in b["imbalance"])
    assert any("Hyperliquid book covers" in n for n in b["notes"])


def test_reported_liquidations_are_okx_and_say_what_is_missing(vs: FakeVenues) -> None:
    lq = ms.reported_liquidations("BTC", NOW - H, None, vs)
    assert lq["basis"] == "observed" and lq["venues"] == ["OKX"] and lq["count"] == 2
    assert lq["long_usd"] == pytest.approx(99 * 100 * 0.01) and lq["short_usd"] == pytest.approx(101 * 50 * 0.01)
    assert "stream not connected" in lq["skipped"]["Binance"] and "Hyperliquid" in lq["skipped"]


def test_liquidation_zones_are_a_labelled_model_and_traded_through_zones_drop(vs: FakeVenues) -> None:
    z = ms.liquidation_zones("BTC", "1h", 48, vs)
    assert z["basis"] == "estimate" and z["basis_label"] == "Model estimate"
    assert any("not confirmed positions" in a for a in z["assumptions"]) and any("Leverage mix assumed" in a for a in z["assumptions"])
    # one candle traded down to 80: every long zone opened before it above 80 was dropped, so the remaining long zones
    # above 80 all come from intervals after that candle
    longs = [b for b in z["bins"] if b["side"] == "long"]
    assert longs and all(b["usd"] > 0 for b in longs)
    assert all(b["px"] < 100 for b in longs) and all(b["px"] > 100 for b in z["bins"] if b["side"] == "short")


def _trades(pattern: bool) -> list[tuple[int, float, float, str]]:
    import random

    rnd = random.Random(7)
    t0 = int(time.time() * 1000) - 20 * 60_000
    out = [(t0 + rnd.randint(0, 1_200_000), 100.0, round(rnd.uniform(0.01, 5), 3), rnd.choice(("buy", "sell"))) for _ in range(800)]
    if pattern:
        out += [(t0 + 60_000 + i * 30_000 + rnd.randint(-800, 800), 100.0, 500.0, "buy") for i in range(12)]
    return sorted(out)


def test_twap_like_slicing_is_found_only_when_it_is_there() -> None:
    found = ms.find_slicing(_trades(True))
    assert len(found) == 1
    p = found[0]
    assert p["side"] == "buy" and p["prints"] == 12 and 29 <= p["interval_s"] <= 31 and p["slice_qty"] == 500.0 and p["regularity_cv"] <= ms.TWAP_MAX_CV
    assert ms.find_slicing(_trades(False)) == []


def test_twap_patterns_are_labelled_inferred(vs: FakeVenues) -> None:
    vs.trades = [{"T": t, "p": str(px), "q": str(q), "m": side == "sell"} for t, px, q, side in _trades(True)]
    tw = ms.twap_patterns("BTC", 30, vs)
    assert tw["basis"] == "inferred" and tw["patterns"] and "can't tell who placed them" in tw["notes"][0]


def test_recorder_keeps_books_and_deduplicates_liquidations(db: Session, md: MD, vs: FakeVenues) -> None:
    now = utcnow()
    r1 = ms.record(db, ("BTC",), vs, md, now)
    r2 = ms.record(db, ("BTC",), vs, md, now + timedelta(minutes=2))
    assert r1 == {"books": 1, "liquidations": 2} and r2 == {"books": 1, "liquidations": 0}
    snap = db.scalars(select(TsBookSnapshot)).first()
    assert snap and set(snap.data) == {"Binance", "OKX", "Hyperliquid"} and all(x["cover"][0] < snap.mid < x["cover"][1] for x in snap.data.values())
    assert len(db.scalars(select(TsLiquidation)).all()) == 2
    hm = ms.heatmap(db, "BTC", now - timedelta(minutes=1))
    assert len(hm["snapshots"]) == 2 and hm["snapshots"][0][3]
    ms.record(db, ("BTC",), vs, md, now + ms.BOOK_KEEP + timedelta(minutes=5))  # retention
    assert len(db.scalars(select(TsBookSnapshot)).all()) == 1


def test_chart_structure_layers_say_what_each_one_is(db: Session, md: MD, vs: FakeVenues) -> None:
    assert chartcmd.parse(["BTC", "STRUCTURE"]).opts == chartcmd.STRUCTURE
    req = chartcmd.parse(["BTC", "1H", "CVD", "AGGOI", "LIQ", "ZONES", "WALLS", "HEATMAP"])
    png, cap = chartcmd.build(db, md, req)
    assert png[:4] == b"\x89PNG"
    assert "CVD: aggregated, Binance + OKX" in cap and "OI: aggregated history Binance + Bybit + OKX" in cap
    assert "Liquidations: reported by OKX; missing Binance, Bybit, Hyperliquid" in cap and "MODEL ESTIMATE" in cap and "Walls: observed" in cap
    assert "No liquidity heatmap yet" in cap  # nothing recorded yet: said, not drawn


def test_every_command_reply_links_the_bot_channel(db: Session, md: MD, vs: FakeVenues) -> None:
    tg = FakeTG()
    asyncio.run(bot.handle_command(db, tg, 1, 9, "/help"))
    asyncio.run(bot.handle_command(db, tg, 1, 9, "/price BTC"))
    asyncio.run(bot.handle_command(db, tg, -5, 9, "/chart BTC 1h", group=True, reply_to=3))
    asyncio.run(bot.handle_command(db, tg, 1, 9, "/nonsense"))
    assert len(tg.sent) == 4
    sign_up = "🎁 4% off Hyperliquid fees: https://api.pricechat.live/api/r/footer?src=tg"  # tracked: counts the click, 302s to the referral link
    for kind, _chat, text, *_ in tg.sent:  # the bot channel, then the Hyperliquid sign-up link below it (Earn settings)
        assert text.endswith(f"📢 Signals: @botpricechat\n{sign_up}"), (kind, text[-140:])
    assert tg.sent[2][0] == "photo" and len(tg.sent[2][2]) <= 1024
    assert tg.sent[0][2].startswith("🎁 <b>4% off Hyperliquid fees</b>")  # /help opens with the sign-up pitch


def test_signal_cards_and_announcements_carry_the_referral_link(db: Session) -> None:
    from app.models.tradingos import TsSignal
    from app.tradingbot.service import settings as earn_settings

    assert bot.with_referral(db, "card") == "card\n🎁 4% off Hyperliquid fees: https://api.pricechat.live/api/r/footer?src=tg"
    assert bot.with_referral(db, "x", "card").endswith("https://api.pricechat.live/api/r/card?src=tg")
    sig = TsSignal(id=5, number=5, coin="ETH", side="SHORT", timeframe="15m", strategy="x", entry_lo=1, entry_hi=2, stop=3, target=0.5, lev_lo=1,
                   lev_hi=2, rr=1.5, status="ACTIVE", expires_at=utcnow())
    rows = bot.buttons(sig, bot.tracked_url(db, "card"))
    assert [b["text"] for b in rows[0]] == ["ENTER", "REJECT", "AUTO"] and rows[1][0]["url"] == "https://api.pricechat.live/api/r/card?src=tg"
    sig.status = "CLOSED"
    assert bot.buttons(sig, bot.tracked_url(db, "card")) == [rows[1]]  # a closed card keeps only the sign-up button
    earn_settings(db).referral_url = "https://app.hyperliquid.xyz/join/KIWI"  # changed in Earn settings: used everywhere
    db.commit()
    assert bot.footer(db).endswith("4% off Hyperliquid fees: https://api.pricechat.live/api/r/footer?src=tg") and bot.referral_url(db).endswith("/KIWI")
    earn_settings(db).referral_url = ""
    db.commit()
    assert bot.with_referral(db, "card") == "card" and bot.footer(db) == bot.CHANNEL_FOOTER and bot.signup_block(db) == ""  # none set
    assert bot.buttons(sig) is None


def test_story_words_pick_the_visual_and_the_facts_carry_the_numbers(md: MD, vs: FakeVenues) -> None:
    from app.brand_manager import marketviz, safety

    assert marketviz.pick("Bitcoin longs wiped out as $400M liquidated") == "liq"
    assert marketviz.pick("Open interest hits a record as traders add leverage") == "oi"
    assert marketviz.pick("A $50M bid wall sits under ETH") == "depth"
    assert marketviz.pick("ETH rallies 8% on ETF news") == "cvd"
    assert marketviz.pick("A new NFT collection launches") is None
    brief: dict[str, Any] = {"headline": "Bitcoin rallies on aggressive buying", "facts": []}
    assert marketviz.attach(brief, md, None) == "cvd"
    ch = brief["chart"]
    assert ch["kind"] == "market" and ch["variant"] == "cvd" and ch["basis"] == "aggregated" and ch["venues"] == ["Binance", "OKX"]
    assert "UTC" in ch["source"] and len(ch["points"]) >= 12
    fact = brief["facts"][0]["text"]
    assert "Binance + OKX BTC perpetuals" in fact and "UTC" in fact
    said = fact.split(": ", 1)[1].split(" (")[0]
    ok = safety.check({"title": "BTC", "voiceover": f"Aggressive buyers added {said} over two days."}, brief, {})
    assert not [x for x in ok if "aren't in the research" in x]
    bad = safety.check({"title": "BTC", "voiceover": "Aggressive buyers added $987 million over two days."}, brief, {})
    assert [x for x in bad if "987" in x]


class FakeTape:
    """TEST FIXTURE: a recorded Hyperliquid trade tape."""

    def __init__(self, rows: list[tuple[int, float, float, str, str]], since: float | None) -> None:
        self.rows, self.since, self.error = rows, since, None

    def get(self, coin: str) -> tuple[list[tuple[int, float, float, str, str]], float | None]:
        return (self.rows if coin == "BTC" else []), self.since


def _tape_rows() -> list[tuple[int, float, float, str, str]]:
    import random

    rnd = random.Random(3)
    t0 = int(time.time() * 1000) - 15 * 60_000
    rows = [(t0 + rnd.randint(0, 800_000), 100.0, round(rnd.uniform(0.01, 3), 3), rnd.choice(("buy", "sell")), f"0x{rnd.randint(0, 40):040x}")
            for _ in range(600)]
    whale = "0xabcdef0000000000000000000000000000001234"
    rows += [(t0 + 30_000 * i + rnd.randint(-300, 300), 100.0, round(20 * rnd.uniform(0.96, 1.04), 3), "sell", whale) for i in range(14)]
    return sorted(rows)


def test_zone_model_falls_back_to_okx_when_binance_is_blocked() -> None:
    z = ms.liquidation_zones("BTC", "1h", 48, FakeVenues(blocked=("binance",)))
    assert z["venues"] == ["OKX"] and z["basis"] == "estimate" and "Binance" in z["skipped"]
    assert z["assumptions"][0].startswith("Model estimate from OKX open-interest changes") and z["bins"]


def test_twap_on_the_hyperliquid_tape_is_per_address_and_never_named() -> None:
    found = ms.find_slicing_by_address(_tape_rows())
    assert len(found) == 1 and found[0]["side"] == "sell" and found[0]["prints"] == 14 and found[0]["address"] == "0xabcd…1234"
    tw = ms.twap_patterns("BTC", 30, FakeVenues(blocked=("binance",)), FakeTape(_tape_rows(), time.time() - 1200))
    assert tw["venues"] == ["Hyperliquid"] and tw["patterns"][0]["venue"] == "Hyperliquid" and "Binance" in tw["skipped"]
    assert any("an address, not an identity" in n for n in tw["notes"])
    with pytest.raises(ms.StructureError, match="No trade feed"):
        ms.twap_patterns("BTC", 30, FakeVenues(blocked=("binance",)), FakeTape([], None))


def test_liquidation_streams_parse_each_exchange() -> None:
    assert [liqtape.base_of(x) for x in ("BTC-USDT-SWAP", "BTCUSDT", "1000PEPEUSDT", "BTC-USD-SWAP")] == ["BTC", "BTC", "PEPE", "BTC"]
    t = liqtape.LiqTape()
    t.okx_ct = {"BTC-USDT-SWAP": (0.01, "BTC"), "BTC-USD-SWAP": (100.0, "USD")}
    t.on_okx({"data": [{"instId": "BTC-USDT-SWAP", "details": [{"bkPx": "80000", "sz": "50", "posSide": "long", "ts": "1"}]},
                       {"instId": "BTC-USD-SWAP", "details": [{"bkPx": "80000", "sz": "20", "posSide": "short", "ts": "2"}]},
                       {"instId": "UNKNOWN-USDT-SWAP", "details": [{"bkPx": "1", "sz": "5", "posSide": "long", "ts": "3"}]}]})
    t.on_binance({"e": "forceOrder", "o": {"s": "1000PEPEUSDT", "S": "SELL", "q": "1000", "z": "1000", "ap": "0.01", "T": 4}})
    t.on_bybit({"topic": "allLiquidation.ETHUSDT", "data": [{"T": 5, "s": "ETHUSDT", "S": "Sell", "v": "2", "p": "2400"}]})
    rows = t.drain()
    assert [(r["venue"], r["coin"], r["side"], round(r["usd"], 2)) for r in rows] == [
        ("okx", "BTC", "long", 40000.0), ("okx", "BTC", "short", 2000.0), ("binance", "PEPE", "long", 10.0), ("bybit", "ETH", "short", 4800.0)]
    assert t.drain() == [] and t.status()["okx"]["received"] == 2


class FakeLiqTape:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def drain(self) -> list[dict[str, Any]]:
        out, self.rows = self.rows, []
        return out

    def status(self) -> dict[str, dict[str, Any]]:
        return {"okx": {"connected": True, "since": 1.0, "error": None, "received": 1},
                "binance": {"connected": True, "since": 1.0, "error": None, "received": 2},
                "bybit": {"connected": False, "since": None, "error": "InvalidStatus: HTTP 403", "received": 0}}


def test_streamed_liquidations_are_stored_once_and_named_by_venue(db: Session, md: MD, vs: FakeVenues) -> None:
    rows = [{"venue": "binance", "coin": "BTC", "t": NOW - 120_000, "side": "long", "px": 98.0, "qty": 10.0, "usd": 980.0},
            {"venue": "binance", "coin": "BTC", "t": NOW - 60_000, "side": "short", "px": 102.0, "qty": 5.0, "usd": 510.0}]
    tape = FakeLiqTape(list(rows))
    r = ms.record(db, ("BTC",), vs, md, utcnow(), liq_tape=tape)
    assert r["liquidations"] == 4 and r["streams"]["bybit"].startswith("down") and "stream bybit" in r["errors"]
    tape.rows = list(rows)  # the same fills again (a reconnect replay): not stored twice
    assert ms.record(db, ("BTC",), vs, md, utcnow(), liq_tape=tape)["liquidations"] == 0
    lq = ms.reported_liquidations("BTC", NOW - H, db, vs, streams=tape.status())
    assert lq["venues"] == ["OKX", "Binance"] and lq["basis"] == "aggregated" and lq["count"] == 4
    assert lq["by_venue"]["Binance"] == {"long_usd": 980.0, "short_usd": 510.0}
    assert "HTTP 403" in lq["skipped"]["Bybit"] and any("lower bound" in n for n in lq["notes"])


def test_the_same_okx_fill_from_rest_and_stream_is_stored_once(db: Session, md: MD, vs: FakeVenues) -> None:
    """Production bug (2026-10-20): OKX's REST list and its live stream report the same fills in one recorder run."""
    same = [{"venue": "okx", "coin": "BTC", "t": NOW - 600_000, "side": "long", "px": 99.0, "qty": 1.0, "usd": 99.0},
            {"venue": "okx", "coin": "BTC", "t": NOW - 300_000, "side": "short", "px": 101.0, "qty": 0.5, "usd": 50.5}]
    r = ms.record(db, ("BTC",), vs, md, utcnow(), liq_tape=FakeLiqTape(list(same)))
    assert r["liquidations"] == 2 and len(db.scalars(select(TsLiquidation)).all()) == 2
