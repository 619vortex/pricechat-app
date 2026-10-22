"""Cashtags and multi-venue markets in the Telegram bot: every input form ($BTC, BTCUSDT, $BTCUSDT, BTC/USDT, .P, names,
contract addresses), exchange / market-type words, resolution across Hyperliquid, Binance, Bybit, OKX and DEX pools
(ambiguity is never guessed), the compact chart caption, /scan, /signal for coins Hyperliquid doesn't list, "fc" in plain
text, HTML escaping and caption length. TEST FIXTURES only: a fake Hyperliquid (FakeMD) and fake exchange / GeckoTerminal
APIs (httpx.MockTransport); no network."""

import asyncio
import json
import re
from typing import Any

import httpx
import pytest
from sqlalchemy.orm import Session

from app.tradingos import bot, chartcmd, market, readout, tickers, venues
from app.tradingos.structure import Venues
from app.tradingos.tickers import Symbol, cashtag, parse_symbol

from tests.test_bot_commands import StudyMD
from tests.test_trading_os import FakeMD, FakeTG, flat, uptrend_pullback

PEPE_A = "0x6982508145454ce325ddbe47a25d4ec3d2311933"
PEPE_B = "0x1111111111111111111111111111111111111111"
NEWT = "0x3333333333333333333333333333333333333333"  # a token only DexScreener returns in these fixtures


def _rows(cs: list[Any], newest_first: bool = True) -> list[list[str]]:
    rows = [[str(c.t), str(c.o), str(c.h), str(c.l), str(c.c), str(c.v)] for c in cs]
    return rows[::-1] if newest_first else rows


class FakeExchanges:
    """TEST FIXTURE: Binance blocked by region (as from production), Bybit and OKX answering for a few markets, and
    GeckoTerminal with two different tokens both called PEPE."""

    def __init__(self) -> None:
        self.btc = uptrend_pullback(300)
        self.kas = flat(300, 0.0374)
        self.calls: list[str] = []

    def transport(self) -> httpx.MockTransport:
        def handle(r: httpx.Request) -> httpx.Response:
            host, path, q = r.url.host, r.url.path, dict(r.url.params)
            self.calls.append(f"{host}{path}?{r.url.query.decode()}")
            if "binance" in host:
                return httpx.Response(451, json={"msg": "Service unavailable from a restricted location"})
            if host == "api.bybit.com":
                sym, cat = q.get("symbol"), q.get("category")
                known = {("spot", "BTCUSDT"): (self.btc, 5e8), ("linear", "BTCUSDT"): (self.btc, 9e9), ("spot", "KASUSDT"): (self.kas, 1.8e6)}
                if (cat, sym) not in known:
                    return httpx.Response(200, json={"retCode": 10001, "retMsg": "Not supported symbols", "result": {}})
                cs, vol = known[(cat, sym)]
                if path.endswith("/tickers"):
                    return httpx.Response(200, json={"retCode": 0, "result": {"list": [{"symbol": sym, "lastPrice": str(cs[-1].c), "price24hPcnt": "0.012",
                                                                                         "turnover24h": str(vol)}]}})
                return httpx.Response(200, json={"retCode": 0, "result": {"list": _rows(cs)}})
            if host == "www.okx.com":
                inst = q.get("instId")
                if inst != "BTC-USDT":
                    return httpx.Response(200, json={"code": "51001", "data": [], "msg": "Instrument ID doesn't exist."})
                if path.endswith("/ticker"):
                    return httpx.Response(200, json={"code": "0", "data": [{"last": str(self.btc[-1].c), "open24h": str(self.btc[-24].c), "volCcy24h": "2e8"}]})
                return httpx.Response(200, json={"code": "0", "data": _rows(self.btc)})
            if host == "api.geckoterminal.com":
                if path.endswith("/search/pools"):
                    def pool(addr: str, token: str, liq: float, dex: str) -> dict[str, Any]:
                        return {"id": f"eth_{addr}", "type": "pool", "attributes": {"address": addr, "name": "PEPE / WETH", "base_token_price_usd": "0.0000045",
                                "reserve_in_usd": str(liq), "volume_usd": {"h24": "2600000"}, "price_change_percentage": {"h24": "10.6"}},
                                "relationships": {"base_token": {"data": {"id": f"eth_{token}"}}, "dex": {"data": {"id": dex}}}}
                    pools = [pool("0xaaaa000000000000000000000000000000000001", PEPE_A, 31e6, "uniswap_v2"),
                             pool("0xaaaa000000000000000000000000000000000002", PEPE_A, 5e6, "uniswap_v3"),
                             pool("0xbbbb000000000000000000000000000000000003", PEPE_B, 80e3, "uniswap_v3"),
                             pool("0xcccc000000000000000000000000000000000004", "0x2222222222222222222222222222222222222222", 900, "uniswap_v3")]  # dust
                    return httpx.Response(200, json={"data": pools})
                if "/ohlcv/" in path:
                    rows = [[c.t // 1000, c.o / 1e7, c.h / 1e7, c.l / 1e7, c.c / 1e7, c.v] for c in reversed(self.btc)]
                    return httpx.Response(200, json={"data": {"attributes": {"ohlcv_list": rows}}})
            if host == "api.dexscreener.com":  # a newer Base token GeckoTerminal's search doesn't return
                def pair(chain: str, addr: str, liq: float) -> dict[str, Any]:
                    return {"chainId": chain, "dexId": "aerodrome", "labels": ["v2"], "pairAddress": addr, "priceUsd": "0.0000052345",
                            "priceChange": {"h24": 12.5}, "volume": {"h24": 420000}, "liquidity": {"usd": liq},
                            "baseToken": {"address": NEWT, "symbol": "NEWT"}, "quoteToken": {"symbol": "WETH"}}
                pairs = [pair("base", "0xdddd000000000000000000000000000000000005", 250e3), pair("base", "0xdddd000000000000000000000000000000000006", 400),
                         pair("pulsechain", "0xdddd000000000000000000000000000000000007", 9e6)]  # dust, and a chain we can't chart
                if path.endswith(f"/tokens/{NEWT}") or (path.endswith("/search") and q.get("q", "").upper() == "NEWT"):
                    return httpx.Response(200, json={"pairs": pairs})
                return httpx.Response(200, json={"pairs": []})
            return httpx.Response(404, json={})
        return httpx.MockTransport(handle)


@pytest.fixture
def md(monkeypatch: pytest.MonkeyPatch) -> FakeMD:
    f = StudyMD({"BTC": uptrend_pullback(400), "SOL": uptrend_pullback(400, 20.0), "DOGE": uptrend_pullback(400, 0.2), "kPEPE": flat(400, 0.01),
                "FLAT": flat(400)})
    monkeypatch.setattr(market, "_shared", f)
    return f


@pytest.fixture
def ex(monkeypatch: pytest.MonkeyPatch) -> FakeExchanges:
    fake = FakeExchanges()
    monkeypatch.setattr(venues, "_shared", Venues(transport=fake.transport()))
    monkeypatch.setattr(venues, "_blocked", {})
    return fake


# ---------------------------------------------------------------- input forms
@pytest.mark.parametrize("word,base,quote,kind", [
    ("BTC", "BTC", None, None), ("$BTC", "BTC", None, None), ("btc", "BTC", None, None), ("BTCUSDT", "BTC", "USDT", None),
    ("$BTCUSDT", "BTC", "USDT", None), ("$btcusdt", "BTC", "USDT", None), ("BTC/USDT", "BTC", "USDT", None), ("btc-usdt", "BTC", "USDT", None),
    ("BTCUSDT.P", "BTC", "USDT", "perp"), ("BTC-PERP", "BTC", None, "perp"), ("ETHUSDC", "ETH", "USDC", None), ("BTCUSD", "BTC", "USD", None),
    ("bitcoin", "BTC", None, None), ("$KAS", "KAS", None, None), ("1000PEPEUSDT", "1000PEPE", "USDT", None), ("gold", "PAXG", None, None),
])
def test_every_input_form_normalises_to_one_symbol(word: str, base: str, quote: str | None, kind: str | None) -> None:
    s = parse_symbol(word)
    assert s is not None and (s.base, s.quote, s.kind) == (base, quote, kind)


def test_addresses_and_non_markets() -> None:
    assert parse_symbol(PEPE_A) == Symbol(base="", address=PEPE_A, venue="dex")
    sol = "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm"
    assert parse_symbol(sol).address == sol  # case kept: Solana addresses are case-sensitive
    assert parse_symbol("BTC/EUR/USD") is None and parse_symbol("BTC/XYZ") is None and parse_symbol("!!") is None


def test_display_is_one_searchable_spelling() -> None:
    assert cashtag("btc", "usdt") == "$BTCUSDT" and cashtag("BTC") == "$BTC" and cashtag("BT C", "US-DT") == "$BTCUSDT"  # never a space inside
    assert parse_symbol("BTC/USDT").display == parse_symbol("$btcusdt").display == "$BTCUSDT"


def test_qualifier_words_and_contradictions() -> None:
    s, rest = tickers.symbol_args(["$KAS", "bybit", "spot"])
    assert (s.base, s.venue, s.kind) == ("KAS", "bybit", "spot") and rest == []
    s, _ = tickers.symbol_args(["eth"])  # "eth" is the coin, not the Ethereum chain
    assert s.base == "ETH" and s.chain is None
    s, _ = tickers.symbol_args(["pepe", "dex", "eth"])
    assert (s.base, s.venue, s.chain) == ("PEPE", "dex", "eth")
    with pytest.raises(chartcmd.ChartUsageError, match="perpetual"):
        chartcmd.parse(["BTCUSDT.P", "spot"])
    r = chartcmd.parse(["$BTCUSDT", "15m", "rsi", "binance"])
    assert (r.symbol.base, r.symbol.quote, r.symbol.venue, r.tf, r.opts) == ("BTC", "USDT", "binance", "15m", {"rsi"})


# ---------------------------------------------------------------- resolution
def test_plain_ticker_is_the_hyperliquid_perp_and_a_pair_is_the_exchange_market(md: FakeMD, ex: FakeExchanges) -> None:
    vs = venues.client()
    hl = venues.resolve(parse_symbol("BTC"), md, vs)
    assert hl.headline == "$BTCUSD | Hyperliquid Perpetual" and not ex.calls  # no exchange asked when Hyperliquid has it
    pair = venues.resolve(parse_symbol("$BTCUSDT"), md, vs)
    assert pair.headline == "$BTCUSDT | OKX Spot"  # spot (a plain pair); no exchange named, so OKX when OKX lists it
    assert pair.alternatives[:2] == ["Bybit Perpetual", "Bybit Spot"]  # the other places it trades are named
    perp = venues.resolve(tickers.with_qualifiers(parse_symbol("BTCUSDT"), {"kind": "perp"}), md, vs)
    assert perp.headline.endswith("Perpetual")  # never a spot market shown as a perpetual (or the reverse)
    assert venues.resolve(parse_symbol("PEPE"), md, vs).headline == "$KPEPEUSD | Hyperliquid Perpetual"  # kPEPE = 1000 PEPE, said as such


def test_tokens_hyperliquid_doesnt_list_and_where_they_are_missing(md: FakeMD, ex: FakeExchanges) -> None:
    kas = venues.resolve(parse_symbol("$KAS"), md, venues.client())
    assert kas.headline == "$KASUSDT | Bybit Spot" and kas.alternatives == []
    with pytest.raises(venues.NotFound) as e:
        venues.resolve(parse_symbol("$NOPE"), md, venues.client())
    assert "Binance: blocks requests from this server's region" in str(e.value) and "No DEX pool either" in str(e.value)  # DEXes tried too
    with pytest.raises(venues.NotFound, match="isn't a Hyperliquid perpetual"):
        venues.resolve(tickers.with_qualifiers(parse_symbol("KAS"), {"venue": "hyperliquid"}), md, venues.client())


def test_dex_tokens_sharing_a_ticker_are_never_guessed(md: FakeMD, ex: FakeExchanges) -> None:
    with pytest.raises(venues.Ambiguous) as e:
        venues.resolve(Symbol(base="PEPE", venue="dex"), md, venues.client())
    assert [o.token_address for o in e.value.options] == [PEPE_A, PEPE_B]  # the dust pool's token isn't offered
    text = venues.ambiguity_text(e.value)
    assert f"<code>{PEPE_A}</code>" in text and "/chart " + PEPE_A in text
    one = venues.resolve(parse_symbol(PEPE_A), md, venues.client())
    assert one.headline == "$PEPE | Uniswap V2 · Ethereum" and one.liquidity == 31e6  # its most liquid pool


# ---------------------------------------------------------------- captions
LINES = ("Price: ", "Trend: ", "RSI(14) ", "MACD ", "EMA 20/50 ", "Support: ", "Resistance: ", "Signal: ", "Kiwi Take: ", "Updated: ", "Entry ")


def _caption_ok(cap: str, head: str) -> None:
    assert cap.startswith(f"📊 <b>{head.split(' | ')[0]}</b> | {head.split(' | ', 1)[1]}")
    for line in LINES:
        assert line in cap, line
    assert not re.search(r"(^|\s)#[A-Za-z]", cap)  # no hashtags
    assert re.search(r"Signal: (LONG|SHORT|WAIT)", cap)


def test_chart_captions_for_each_kind_of_market(db: Session, md: FakeMD, ex: FakeExchanges) -> None:
    tg = FakeTG()
    for cmd, head in (("/chart $BTCUSDT", "$BTCUSDT | 1H | OKX Spot"), ("/chart BTC 4h", "$BTCUSD | 4H | Hyperliquid Perpetual"),
                      ("/chart $KAS bybit 15m", "$KASUSDT | 15m | Bybit Spot"), (f"/chart {PEPE_A} 1h", "$PEPE | 1H | Uniswap V2 · Ethereum")):
        asyncio.run(bot.handle_command(db, tg, -5, 9, cmd, group=True, reply_to=1))
        photo = next(s for s in reversed(tg.sent) if s[0] == "photo")
        _caption_ok(photo[2], head)
        assert len(photo[2]) <= 1024
    assert "(signal rules)" in next(s for s in tg.sent if s[0] == "photo" and "Hyperliquid Perpetual" in s[2])[2]
    assert "(chart rules)" in tg.sent[-1][2] and "Pool PEPE / WETH" in tg.sent[-1][2]


def test_existing_commands_without_dollar_still_work(db: Session, md: FakeMD, ex: FakeExchanges) -> None:
    tg = FakeTG()
    for cmd in ("/chart BTC 15m", "/chart BTC 15m bb oi funding", "/chart SOL 1h macd volume", "/chart btc 5m"):
        asyncio.run(bot.handle_command(db, tg, 1, 9, cmd))
        assert tg.sent[-1][0] == "photo", cmd
    asyncio.run(bot.handle_command(db, tg, 1, 9, "/price BTC"))
    assert tg.sent[-1][2].startswith("<b>$BTC</b>  ") and "24h" in tg.sent[-1][2] and "Funding" not in tg.sent[-1][2]  # price + 24h only


def test_hyperliquid_panels_on_an_exchange_market_say_why_they_are_missing(db: Session, md: FakeMD, ex: FakeExchanges) -> None:
    _, cap, details = chartcmd.build_full(db, md, chartcmd.parse(["$KAS", "1h", "oi", "funding", "depth"]), venues.client())
    text = cap + (details or "")
    assert "OI history: not available for Bybit Spot" in text and "Funding: not available" in text and "Hyperliquid" not in text


def test_long_reports_go_to_a_second_message_instead_of_being_cut(db: Session, md: FakeMD, ex: FakeExchanges, monkeypatch: pytest.MonkeyPatch) -> None:
    def many(db: Any, coin: str, req: Any, vis: Any, start: Any, panels: Any, notes: list[str]) -> tuple[dict[str, Any], list[str]]:
        notes += [f"note {i}: " + "x" * 90 for i in range(12)]
        return {}, ["📊 CVD: aggregated, Binance + OKX perp taker volume <&>"]
    monkeypatch.setattr(chartcmd, "_structure", many)
    tg = FakeTG()
    asyncio.run(bot.handle_command(db, tg, -5, 9, "/chart BTC 1h cvd", group=True, reply_to=1))
    photo, msg = tg.sent[-2], tg.sent[-1]
    assert photo[0] == "photo" and msg[0] == "msg"
    assert len(photo[2]) <= 1024 and photo[2].endswith("/api/r/footer?src=tg") and "note 0" not in photo[2]
    assert "<b>$BTCUSD</b> 1H · chart details" in msg[2] and "note 11" in msg[2]  # everything, nothing truncated
    assert "&lt;&amp;&gt;" in msg[2]  # HTML-escaped


# ---------------------------------------------------------------- /signal, /scan, fc
def test_signal_with_cashtags_and_for_coins_hyperliquid_doesnt_list(db: Session, md: FakeMD, ex: FakeExchanges) -> None:
    tg = FakeTG()
    asyncio.run(bot.handle_command(db, tg, -5, 9, "/signal $DOGE", group=True, reply_to=1))
    out = tg.sent[-1][2]
    assert "$DOGEUSD" in out and "Hyperliquid Perpetual" in out and ("<b>WAIT</b>" in out or "setup</b>" in out) and "RSI(14)" in out
    asyncio.run(bot.handle_command(db, tg, -5, 9, "/signal $KAS", group=True, reply_to=2))
    photo = next(s for s in reversed(tg.sent) if s[0] == "photo")
    assert "Hyperliquid" not in photo[2].split("\n\n📢")[0] and "$KASUSDT</b> | 1H | Bybit Spot" in photo[2] and "WATCH" in photo[2] and "chart-based read only" in photo[2]  # said without fuss


def test_scan_lists_every_market_and_what_couldnt_be_read(db: Session, md: FakeMD, ex: FakeExchanges) -> None:
    tg = FakeTG()
    asyncio.run(bot.handle_command(db, tg, -5, 9, "/scan $KAS", group=True, reply_to=1))
    out = tg.sent[-1][2]
    assert "🔎 <b>$KAS</b>" in out and "$KASUSDT | Bybit Spot" in out and "Not a Hyperliquid perpetual" not in out and "MACD" in out
    assert "Binance: blocks requests from this server's region" in out and "Kiwi Take:" in out and "/chart $KASUSDT bybit 4h" in out
    asyncio.run(bot.handle_command(db, tg, -5, 9, "/scan BTC", group=True, reply_to=2))
    out = tg.sent[-1][2]
    assert out.index("$BTCUSDT | Bybit Perpetual") < out.index("$BTCUSD | Hyperliquid Perpetual")  # most liquid first
    asyncio.run(bot.handle_command(db, tg, -5, 9, "/scan pepe dex", group=True, reply_to=3))
    assert "different tokens use the ticker $PEPE" in tg.sent[-1][2]


def test_fc_accepts_cashtags_timeframes_and_options_but_not_chat(db: Session, md: FakeMD, ex: FakeExchanges) -> None:
    assert bot.fc_command("Fc $SOL 4h") == "/fc SOL 4h" and bot.fc_command("fc $BTCUSDT 15m rsi") == "/fc BTCUSDT 15m rsi"
    assert bot.fc_command("fc gold now please") is None and bot.fc_command("fc barcelona won 3-0") is None
    tg = FakeTG()
    asyncio.run(bot.handle_command(db, tg, -5, 9, bot.fc_command("Fc $SOL 4h") or "", group=True, reply_to=1))
    assert "$SOLUSD</b> | 4H | Hyperliquid Perpetual" in tg.sent[-1][2]
    asyncio.run(bot.handle_command(db, tg, -5, 9, "/fc $BTCUSDT 15m rsi", group=True, reply_to=2))
    assert "$BTCUSDT</b> | 15m | OKX Spot" in tg.sent[-1][2] and "Layers: rsi" in tg.sent[-1][2]


def test_outgoing_market_messages_use_cashtags(db: Session, md: FakeMD) -> None:
    digest = bot.markets_digest(md)
    assert "<b>$BTC</b>" in digest and "#" not in digest


def test_kiwi_take_is_an_observation_from_the_numbers() -> None:
    assert readout.kiwi_take(99.9, "Bullish", 72, 90, 100, 1.0, 98, 1.0).startswith("Pressing resistance at 100")
    assert readout.kiwi_take(50, "Neutral", 25, 40, 60, 1.0, 50, -2).startswith("RSI 25: oversold")
    assert readout.kiwi_take(50, "Neutral", 50, 40, 60, 1.0, 50, 0.5) == f"Ranging between {market.fmt_price(40)} and {market.fmt_price(60)}."


def test_a_region_blocked_exchange_is_not_asked_again_for_a_while(md: FakeMD, ex: FakeExchanges) -> None:
    venues.resolve(parse_symbol("$BTCUSDT"), md, venues.client())
    asked = sum("binance" in c for c in ex.calls)
    with pytest.raises(venues.NotFound, match="Binance: blocks requests"):  # still named as the reason it's missing
        venues.resolve(parse_symbol("$NOPE"), md, venues.client())
    assert sum("binance" in c for c in ex.calls) == asked == 2  # spot + perp once, then remembered


def test_a_huge_details_message_drops_whole_lines(db: Session, md: FakeMD, ex: FakeExchanges, monkeypatch: pytest.MonkeyPatch) -> None:
    def flood(db: Any, coin: str, req: Any, vis: Any, start: Any, panels: Any, notes: list[str]) -> tuple[dict[str, Any], list[str]]:
        notes += [f"note {i} <b>" + "y" * 200 for i in range(40)]
        return {}, []
    monkeypatch.setattr(chartcmd, "_structure", flood)
    _, _, details = chartcmd.build_full(db, md, chartcmd.parse(["BTC", "1h", "cvd"]), venues.client())
    assert details is not None and len(details) <= chartcmd.DETAILS_LIMIT + 200 and details.rstrip().endswith("more notes")
    assert "&lt;b&gt;" in details and "<b>y" not in details  # escaped and never cut mid-line


def test_venue_ids_stay_internal(md: FakeMD, ex: FakeExchanges) -> None:
    pair = venues.resolve(tickers.with_qualifiers(parse_symbol("BTCUSDT"), {"venue": "okx"}), md, venues.client())
    assert pair.symbol == "BTC-USDT" and pair.tag == "$BTCUSDT" and pair.label == "OKX Spot"  # internal id vs display
    assert json.dumps(pair.alternatives) == "[]"


# --- the three main indicators, "fc X indicators", and OKX first --------------------------------------------------------------


def test_three_main_indicators_in_plain_words() -> None:
    from app.tradingos import indicators as ind

    up = [100 + i * 0.5 + (i % 3) * 0.2 for i in range(90)]  # a steady climb
    lines = ind.main_three_lines(ind.main_three(up))
    assert lines[0].startswith("RSI(14) ") and lines[1].startswith("MACD ") and lines[2] == "EMA 20/50 price above both · 20 over 50"
    down = list(reversed(up))
    assert ind.main_three_lines(ind.main_three(down))[2] == "EMA 20/50 price below both · 20 under 50"
    assert ind.main_three(up[:40]) is None and ind.main_three_lines(None) == []
    d = {"price": 10, "rsi": 75.0, "macd": 1, "macd_signal": 0.5, "macd_hist": 0.2, "macd_hist_prev": -0.1, "ema20": 9, "ema50": 8}
    assert ind.main_three_lines(d)[:2] == ["RSI(14) 75.0 · overbought", "MACD bullish cross · momentum rising"]


def test_fc_indicators_draws_the_three(md: FakeMD) -> None:
    from app.tradingos import chartcmd

    assert bot.fc_command("fc btc indicators") == "/fc btc indicators" and bot.fc_command("FC $SOL 4h indicator") == "/fc SOL 4h indicator"
    req = chartcmd.parse(["BTC", "indicators"], {"BTC"})
    assert {"rsi", "macd", "ma"} <= req.opts


def test_signal_cards_show_the_indicators_the_signal_was_made_with() -> None:
    from app.models.tradingos import TsSignal
    from app.tradingos import signals
    from app.db.base import utcnow

    main3 = {"price": 101, "rsi": 58.2, "macd": 0.4, "macd_signal": 0.3, "macd_hist": 0.1, "macd_hist_prev": 0.05, "ema20": 100, "ema50": 98}
    s = TsSignal(number=7, coin="SOL", side="LONG", timeframe="1h", strategy="trend_pullback_v1", entry_lo=99, entry_hi=101, stop=95, target=110,
                 lev_lo=2, lev_hi=4, rr=2.0, status="ACTIVE", reason="pullback to EMA20", analysis={"main3": main3}, expires_at=utcnow())
    card = signals.card(s)
    assert "📈 Indicators" in card and "· RSI(14) 58.2 · neutral" in card and "· MACD bullish · momentum rising" in card
    assert "· EMA 20/50 price above both · 20 over 50" in card
    s.analysis = {}  # signals made before the indicators were recorded: no empty section
    assert "Indicators" not in signals.card(s)


def test_a_coin_hyperliquid_doesnt_list_comes_from_okx_when_okx_lists_it(md: FakeMD, ex: FakeExchanges) -> None:
    pick = venues.resolve(parse_symbol("BTCUSDT"), md, venues.client())
    assert pick.venue == "okx" and pick.headline == "$BTCUSDT | OKX Spot"
    assert venues.resolve(tickers.with_qualifiers(parse_symbol("BTCUSDT"), {"venue": "bybit"}), md, venues.client()).venue == "bybit"  # named: obeyed
    assert venues.resolve(parse_symbol("$KAS"), md, venues.client()).venue == "bybit"  # OKX doesn't list it: the next market


# --- DEX coverage: DexScreener after GeckoTerminal, and the DEXes when no exchange lists a coin -----------------------------


def test_a_contract_geckoterminal_doesnt_know_is_found_on_dexscreener(md: FakeMD, ex: FakeExchanges) -> None:
    x = venues.resolve(parse_symbol(NEWT), md, venues.client())
    assert x.venue == "dex" and x.base == "NEWT" and x.network == "base" and x.symbol.endswith("0005")  # the liquid pool, not the dust one
    assert x.token_address == NEWT and x.price == pytest.approx(0.0000052345) and x.change_24h == 12.5
    assert any("dexscreener" in c for c in ex.calls) and any("geckoterminal" in c for c in ex.calls)  # GeckoTerminal asked first


def test_a_ticker_no_exchange_lists_falls_through_to_the_dexes(md: FakeMD, ex: FakeExchanges) -> None:
    x = venues.resolve(parse_symbol("$NEWT"), md, venues.client())
    assert x.venue == "dex" and x.base == "NEWT" and x.network == "base"
    with pytest.raises(venues.Ambiguous):  # two different tokens called PEPE on DEXes: the member gets the contracts, never a guess
        venues.resolve(parse_symbol("PEPEUSDT"), md, venues.client())
    with pytest.raises(venues.NotFound, match="or a DEX"):
        venues.resolve(parse_symbol("$ZZQQX"), md, venues.client())
    assert venues.resolve(parse_symbol("$KAS"), md, venues.client()).venue == "bybit"  # an exchange lists it: never the DEX


def test_price_of_a_contract_keeps_its_small_digits(db: Session, md: FakeMD, ex: FakeExchanges) -> None:
    tg = FakeTG()
    asyncio.run(bot.handle_command(db, tg, 1, 9, f"/price {NEWT}"))
    assert tg.sent[-1][2].startswith("<b>$NEWT</b>  0.000005234  ▲ 12.5% 24h")


def test_price_takes_several_coins_one_line_each(db: Session, md: FakeMD, ex: FakeExchanges) -> None:
    tg = FakeTG()
    asyncio.run(bot.handle_command(db, tg, 1, 9, "/price btc $SOL zzqqxnope"))
    lines = tg.sent[-1][2].split("\n\n📢")[0].split("\n")
    assert [l.split("  ")[0] for l in lines] == ["<b>$BTC</b>", "<b>$SOL</b>", "<b>ZZQQXNOPE</b>"] and lines[2].endswith("not found")
    asyncio.run(bot.handle_command(db, tg, 1, 9, "/price kas bybit"))  # an exchange word: still one coin
    assert tg.sent[-1][2].startswith("<b>$KAS</b>  ") and "\n<b>" not in tg.sent[-1][2].split("\n\n📢")[0]
