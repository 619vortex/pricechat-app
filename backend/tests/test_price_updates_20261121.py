"""The Price Updates defects of 2026-11-19..21, replayed from production evidence (owner, 2026-11-21).

- Telegram 204 (2026-11-21 04:04 UTC): "BTCUSD_PERP longs liquidated: $10.1B on Binance in an hour". Binance COIN-M quantities are $100
  contracts (BTC) or $10 (others), and were priced as coins; the whole hour's BTCUSD_PERP trading was $286M (dapi klines). Evidence:
  docs/incidents/2026-11-21-telegram-204.
- Telegram 203 (2026-11-20 20:04 UTC): "US stocks tumble: Nasdaq -4.6%, S&P 500 -3.1% in 24 hours" / "Price from Yahoo Finance as of
  20:00 UTC": no index level, a rolling 24 hours where a session is measured from the previous close, a footer about a price it never
  printed, and no picture ("visual editor: text only: no verified series").
- Telegram 199 (2026-11-19 17:06 UTC): "Nasdaq -1.9%, S&P 500 -1.4%, DOW Jones -1.1% in 15 minutes": no levels, no picture.
- X 145 (2026-11-21 00:11 UTC): "Nvidia's biggest one-day drop since January 2025, so far today: -12.0%" from a 20:47 UTC price, which
  Telegram's final check had refused as 201 minutes old (Nvidia closed -14.0%).

The Yahoo answers are real (tests/price_updates_yahoo_20261121.json, read 2026-11-21); a replay at time T keeps the 5-minute bars that had
closed by T, as Yahoo served them then. No test reaches a real service.
"""

import copy
import io
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.brand_manager import sources as bm_sources
from app.config.settings import get_settings
from app.models.newsdesk import NdEvent, NdPost
from app.models.tradingos import TsLiquidation
from app.newsdesk import clean, engine, events, headline, livedata, moves, photography, photos, sources, visual
from app.tradingos import liqtape
from tests.test_newsdesk_telegram import desk_running

FIX = json.loads(Path(__file__).with_name("price_updates_yahoo_20261121.json").read_text())
EVIDENCE = Path(__file__).resolve().parents[2] / "docs" / "incidents" / "2026-11-21-telegram-204" / "raw"
INDICES = [w for w in sources.DEFAULT_WATCHLIST if w["group"] == "index"]
T203 = datetime(2026, 11, 20, 20, 4, tzinfo=timezone.utc)
T199 = datetime(2026, 11, 19, 17, 6, tzinfo=timezone.utc)
CLOSE_1120 = datetime(2026, 11, 20, 21, 5, tzinfo=timezone.utc)


def payload_at(sym: str, t: datetime) -> dict[str, Any]:
    """Yahoo's chart answer for `sym` as it stood at t: the bars closed by then, the price and time of the newest, that session's
    regular hours, and the official close of the session before (from Yahoo's own daily closes)."""
    p = copy.deepcopy(FIX["charts"][sym])
    r = p["chart"]["result"][0]
    m = r["meta"]
    ts = int(t.timestamp())
    keep = [i for i, x in enumerate(r["timestamp"]) if x + 300 <= ts]
    q = r["indicators"]["quote"][0]
    r["timestamp"] = [r["timestamp"][i] for i in keep]
    r["indicators"]["quote"][0] = {k: [q[k][i] for i in keep] for k in q}
    m["regularMarketPrice"], m["regularMarketTime"] = q["close"][keep[-1]], r["timestamp"][-1] + 300
    periods = [d[0] for d in m["tradingPeriods"]]
    current = max((s for s in periods if s["start"] <= ts), key=lambda s: s["start"])
    m["currentTradingPeriod"]["regular"] = current
    m["tradingPeriods"] = [[s] for s in periods if s["start"] <= ts]
    m["previousClose"] = [c for d, c in FIX["daily"][sym] if d < current["start"]][-1]
    return p


def quotes_at(t: datetime, syms: tuple[str, ...] = ("^IXIC", "^GSPC", "^DJI")) -> dict[str, sources.Quote]:
    out = {s: sources.parse_chart(s, payload_at(s, t)) for s in syms}
    assert all(out.values())
    return out  # type: ignore[return-value]


def wide_at(t: datetime) -> moves.Move:
    [m] = [x for x in moves.detect(quotes_at(t), INDICES, t) if x.kind == "wide"]
    return m


# --- Telegram 204: Binance COIN-M liquidations ---------------------------------------------------------------------------------

def forced(symbol: str, side: str, px: float, qty: float, t: int = 1_795_230_000_000) -> dict[str, Any]:
    return {"e": "forceOrder", "o": {"s": symbol, "S": side, "ap": str(px), "q": str(qty), "z": str(qty), "T": t}}


def test_a_binance_coin_margined_liquidation_is_valued_in_contracts_not_coins() -> None:
    tape = liqtape.LiqTape()
    tape.on_binance(forced("BTCUSD_PERP", "SELL", 57_000, 1_000))  # 1,000 contracts of $100: $100,000, not 57 million
    tape.on_binance(forced("ETHUSD_PERP", "BUY", 1_650, 500))  # 500 contracts of $10: $5,000
    tape.on_binance(forced("BTCUSDT", "SELL", 57_000, 2))  # USDS-M: 2 BTC
    btc_m, eth_m, btc_usdt = tape.drain()
    assert (btc_m["coin"], btc_m["side"], btc_m["usd"]) == ("BTC", "long", 100_000.0) and btc_m["qty"] == pytest.approx(100_000 / 57_000)
    assert (eth_m["coin"], eth_m["side"], eth_m["usd"]) == ("ETH", "short", 5_000.0)
    assert (btc_usdt["coin"], btc_usdt["usd"]) == ("BTC", 114_000.0)


def test_contract_names_never_become_coins() -> None:
    assert [liqtape.base_of(s) for s in ("BTCUSD_PERP", "BTCUSD_261225", "BTCUSDT_261225", "ETHUSD_PERP", "BTC-USD-SWAP", "1000PEPEUSDT")] == \
        ["BTC", "BTC", "BTC", "ETH", "BTC", "PEPE"]
    assert liqtape.coin_m_contract_usd("BTCUSD_PERP") == 100 and liqtape.coin_m_contract_usd("SOLUSD_PERP") == 10
    assert liqtape.coin_m_contract_usd("BTCUSDT") is None and liqtape.coin_m_contract_usd("BTCUSDT_261225") is None


def test_the_contract_sizes_are_binances_own() -> None:
    """The sizes the parser uses, against Binance's exchangeInfo saved with the incident (every COIN-M contract it lists)."""
    info = json.loads((EVIDENCE / "dapi_exchangeInfo.json").read_text())
    for s in info["symbols"]:
        assert liqtape.coin_m_contract_usd(s["symbol"]) == float(s["contractSize"]), s["symbol"]


def post_204_rows(db: Session, coin: str, usd_of: Any) -> None:
    """The hour behind Telegram 204: forced sells of BTCUSD_PERP between 56,300 and 58,900 (inside the hour's range), 175,320 contracts."""
    now_ms = int(datetime(2026, 11, 21, 3, 48, tzinfo=timezone.utc).timestamp() * 1000)
    for i in range(60):
        px, qty = 58_900 - i * 44, 2_922.0
        db.add(TsLiquidation(venue="binance", coin=coin, t=now_ms - (60 - i) * 59_000, side="long", px=px, qty=qty, usd=usd_of(px, qty)))
    db.commit()


def test_post_204_replayed_the_mis_keyed_records_never_publish(db: Session) -> None:
    now = datetime(2026, 11, 21, 3, 48, tzinfo=timezone.utc)
    post_204_rows(db, "BTCUSD_PERP", lambda px, q: px * q)  # what production stored: contracts priced as coins
    total = sum(r.usd for r in db.scalars(select(TsLiquidation)))
    assert 10.05e9 <= total <= 10.15e9  # the post's "$10.1B"
    assert bm_sources.liquidation_topics(db, now) == []  # "BTCUSD_PERP" is a contract, not a coin: refused
    assert events._offer_liquidations(db, [], now, {}, events.POLICY) == 0


def test_post_204_replayed_the_real_figure_and_the_review_ceiling(db: Session) -> None:
    now = datetime(2026, 11, 21, 3, 48, tzinfo=timezone.utc)
    post_204_rows(db, "BTC", lambda px, q: q * 100)  # what the fixed parser stores
    [topic] = bm_sources.liquidation_topics(db, now)
    assert topic.brief["headline"] == "BTC longs liquidated: $18 million on Binance in an hour"  # 175,320 contracts x $100 = $17.5M
    assert topic.brief["numbers"]["liquidations_1h_usd"] == 17_532_000
    # never more than the hour's whole BTCUSD_PERP trading (Binance's own klines, saved with the incident): 02:00-04:00 UTC
    klines = json.loads((EVIDENCE / "dapi_klines_1h.json").read_text())
    traded = sum(float(k[5]) * 100 for k in klines[:2])
    assert topic.brief["numbers"]["liquidations_1h_usd"] < traded == 321_451_200


def test_an_implausible_hour_is_held_for_a_person(db: Session, caplog: pytest.LogCaptureFixture) -> None:
    now = datetime(2026, 11, 21, 3, 48, tzinfo=timezone.utc)
    post_204_rows(db, "BTC", lambda px, q: px * q)  # any other unit error that keeps a coin's name
    assert bm_sources.liquidation_topics(db, now) == []
    assert "held for review" in caplog.text


# --- Telegram 203 and 199: combined index posts ------------------------------------------------------------------------------

def test_post_203_replayed_prints_each_level_against_the_previous_close() -> None:
    m = wide_at(T203)
    head = headline.market(m)
    summary = livedata.market_summary(m, "one price source (Yahoo Finance)")
    assert head == "US STOCKS TUMBLE: NASDAQ -4.4% TO 22,824.39, S&P 500 -2.9% TO 6,966.75 TODAY"
    assert summary == ("Change from Thursday's close: Nasdaq 23,879.20, S&P 500 7,175.83. "
                       "Market data: Yahoo Finance, 20:00 UTC, intraday (US market open).")
    assert "24 HOURS" not in head and "Price from" not in summary
    assert (m.window, m.session) == ("day", "intraday")
    # each figure is Yahoo's: the 19:55-20:00 bar's close, the official previous close, and the percent those two give
    nasdaq = m.figures[0]
    assert (nasdaq["price"], nasdaq["ref"]) == (pytest.approx(22824.39, abs=0.01), pytest.approx(23879.20, abs=0.005))
    assert nasdaq["change"] == pytest.approx((22824.392578125 - 23879.2) / 23879.2 * 100, abs=1e-4)


def test_at_the_close_the_post_says_so_and_matches_yahoos_day_change() -> None:
    m = wide_at(CLOSE_1120)
    assert headline.market(m) == "US STOCKS PLUNGE: NASDAQ -5.3% TO 22,617.73, S&P 500 -3.7% TO 6,910.39 TODAY"
    assert livedata.market_summary(m, "").endswith(", at the close.")
    official = FIX["charts"]["^IXIC"]["chart"]["result"][0]["meta"]["regularMarketChangePercent"]  # Yahoo's own: -5.283
    assert m.figures[0]["change"] == pytest.approx(official, abs=0.001)


def test_post_199_replayed_a_15_minute_burst_measured_between_real_prices() -> None:
    m = wide_at(T199)
    assert headline.market(m) == ("US STOCKS TUMBLE: NASDAQ -1.7% TO 24,105.03, S&P 500 -1.3% TO 7,219.57, DOW JONES -1.0% TO 49,245.34 "
                                  "IN 15 MINUTES")
    assert livedata.reference_line(m) == "Change from 16:50 UTC: Nasdaq 24,528.58, S&P 500 7,314.95, Dow Jones 49,745.52."
    # the two prices are 15 minutes apart: the 16:45-16:50 bar's close and the 17:00-17:05 bar's (Yahoo's 1-minute closes at 16:49 and
    # 17:04 were 24,528.58 and 24,105.03)
    assert m.as_of == datetime(2026, 11, 19, 17, 5, tzinfo=timezone.utc) and m.ref_at == datetime(2026, 11, 19, 16, 50, tzinfo=timezone.utc)


def test_a_us_index_is_never_measured_over_a_rolling_24_hours() -> None:
    from tests.test_newsdesk_telegram import quote

    now = T203
    q = {"^IXIC": quote("^IXIC", 25_000, 23_800, now), "^GSPC": quote("^GSPC", 7_400, 7_100, now)}  # -4.8% / -4.1% over 24 h, no session data
    found = moves.detect(q, INDICES, now)
    assert all(m.window not in ("24h", "4h", "day") for m in found)  # without Yahoo's previous close there is no day figure at all


async def test_a_combined_post_without_levels_is_never_offered(db: Session) -> None:
    bare = moves.Move("wide", {"symbol": "US-STOCKS", "name": "US stocks", "group": "index", "category": "stocks"}, 1, T203, "day", -4.6, 1.8,
                      members=[("Nasdaq", -4.6), ("S&P 500", -3.1)])
    assert await events._offer_move(db, bare, [], T203, {}, events.POLICY, None, None, {}) == 0


async def test_post_203_through_the_desk_and_the_telegram_writer(db: Session) -> None:
    desk_running(db, T203, {})
    quotes = quotes_at(T203)
    await events.decide(db, T203, quotes=quotes, watchlist=INDICES, categories={}, desk_state=engine.settings_row(db).state, liquidations=False,
                        second_opinion=None, daily={})
    [ev] = list(db.scalars(select(NdEvent).where(NdEvent.kind == "market")))
    assert ev.headline.startswith("US STOCKS TUMBLE: NASDAQ -4.4% TO 22,824.39")
    assert "Change from Thursday's close" in ev.summary and "Market data: Yahoo Finance" in ev.summary
    assert "22,824.39" in ev.facts["told"] and "23,879.20" in ev.facts["told"]  # every printed figure is a measured one
    assert [f["name"] for f in ev.facts["figures"]] == ["Nasdaq", "S&P 500"] and ev.facts["session"] == "intraday"


async def test_post_203_as_telegram_and_x_send_it_now(db: Session, wall_street: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    """The whole path production took for 203: the desk's pass, Telegram's re-read and post, X following with the same figures."""
    from tests.test_newsdesk_telegram import Tg
    from app.newsdesk import network
    from app.newsdesk import telegram as lane

    monkeypatch.setattr(network, "DEFAULT_STYLE", "clean")  # production's style (tests default to classic: conftest._classic_post_style)

    desk_running(db, T203, {})
    quotes = quotes_at(T203)
    await events.decide(db, T203, quotes=quotes, watchlist=INDICES, categories={}, desk_state=engine.settings_row(db).state, liquidations=False,
                        second_opinion=None, daily={})
    [ev] = list(db.scalars(select(NdEvent).where(NdEvent.kind == "market")))
    if ev.status != "released":  # the slot plan is not what is under test: release it as the 00:00 Dubai slot did
        ev.status, ev.released_at, ev.slot = "released", T203, "slot"
        db.commit()

    class PhotoTg(Tg):  # a licensed photograph is a JPEG (the shared fake expects the old PNG cards)
        async def send_photo(self, chat: Any, image: bytes, caption: str, **kw: Any) -> dict[str, Any]:
            assert image[:2] == bytes([0xFF, 0xD8]) or image[:4] == bytes([0x89, 0x50, 0x4E, 0x47])  # JPEG or PNG
            self.sent.append({"chat": chat, "text": caption, "card": True, "image": image, **kw})
            return self._answer()

    tg = PhotoTg()
    out = await lane.tick(db, get_settings(), tg, T203 + timedelta(seconds=30))
    db.refresh(ev)
    assert out["sent"], out
    assert tg.sent[0]["card"] and Image.open(io.BytesIO(tg.sent[0]["image"])).size  # the picture went with the post
    text = tg.sent[0]["text"]
    for figure in ("22,824.39", "6,966.75", "23,879.20", "7,175.83", "4.4%", "2.9%", "Market data: Yahoo Finance"):
        assert figure in text, (figure, text)
    assert "24 hours" not in text.lower() and "Price from Yahoo Finance" not in text
    assert ev.facts["visual"]["kind"] == "photo" and ev.facts["visual"]["library"] == "stocks" and wall_street
    await engine.deliver_x(db, engine.settings_row(db), get_settings(), None, 0, T203 + timedelta(seconds=60))
    db.refresh(ev)
    x_text = db.get(NdPost, ev.x_post_id).text
    assert ev.x_status == "dry_run" and "22,824.39" in x_text and "4.4%" in x_text  # X prints Telegram's figures, read a moment before


@pytest.mark.parametrize("style", ["clean", "wire", "classic"])
async def test_every_post_style_prints_the_levels_the_reference_and_the_market_data_line(db: Session, wall_street: list[str],
                                                                                         monkeypatch: pytest.MonkeyPatch, style: str) -> None:
    from app.newsdesk import network
    from app.newsdesk import telegram as lane

    monkeypatch.setattr(network, "DEFAULT_STYLE", style)  # the owner's Admin switch
    desk_running(db, T203, {})
    livedata.remember(quotes_at(T203))
    await events.decide(db, T203, quotes=quotes_at(T203), watchlist=INDICES, categories={}, desk_state=engine.settings_row(db).state, liquidations=False,
                        second_opinion=None, daily={})
    [ev] = list(db.scalars(select(NdEvent)))
    assert (await livedata.refresh(db, ev, T203))[0] == livedata.OK
    msg, why = await lane.compose(get_settings(), ev, None, db=db, s=None, now=T203)
    assert msg is not None, why
    x = ev.facts["package"]["x_text"]
    for text in (msg["html"].replace("&amp;", "&"), x):
        for figure in ("22,824.39", "6,966.75", "4.4%", "2.9%", "Market data: Yahoo Finance, 20:00 UTC, intraday (US market open)."):
            assert figure in text, (style, figure, text)
    assert "Change from Thursday's close: Nasdaq 23,879.20, S&P 500 7,175.83." in msg["html"].replace("&amp;", "&")


# --- pictures for Yahoo-measured stock and index posts --------------------------------------------------------------------------

STOCKS = [photos.LicensedPhoto(title="File:Sign of the New York Stock Exchange, Broad Street.jpg",
                               url="https://upload.wikimedia.org/thumb/Sign_of_the_NYSE/2560px-Sign_of_the_NYSE.jpg",
                               page="https://commons.wikimedia.org/wiki/File:Sign_of_the_New_York_Stock_Exchange,_Broad_Street.jpg",
                               creator="Billie Grace Ward", provider="wikimedia", source="wikimedia", licence="CC0",
                               licence_url="http://creativecommons.org/publicdomain/zero/1.0/deed.en", width=4240, height=2832)]


@pytest.fixture
def wall_street(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    from tests.test_newsdesk_photography_20261119 import pic

    fetched: list[str] = []

    async def records(client: Any, subject: Any) -> list[Any]:
        return list(STOCKS) if subject.key == "stocks" else []

    async def fetch(client: Any, p: Any, min_width: int = 0, colour: bool = True) -> Image.Image:
        fetched.append(p.title)
        return pic()

    monkeypatch.setattr(photography, "_records", records)
    monkeypatch.setattr(photos, "fetch", fetch)
    photography._cache.clear()
    return fetched


def event_203() -> NdEvent:
    m = wide_at(T203)
    head = headline.market(m)
    ev = NdEvent(key="move:US-STOCKS", kind="market", title=head, headline=head, summary=livedata.market_summary(m, ""), topic="move", label="",
                 evidence="price data", category="stocks", sources=[{"name": "Yahoo Finance", "url": "https://finance.yahoo.com/markets/"}],
                 facts={"symbol": "US-STOCKS", "kind": "wide", "window": m.window, "change_pct": m.change, "price": m.price, "as_of": m.as_of.isoformat(),
                        "members": m.members, "tickers": [], "told": livedata.market_told(m, head), **livedata.move_facts(m)}, released_at=T203)
    ev.id = 932
    return ev


async def test_post_203_gets_the_wall_street_photograph_after_its_figures_check_out(wall_street: list[str]) -> None:
    livedata.remember(quotes_at(T203))  # the re-read Telegram makes just before writing: the picture checks the same answer
    ev = event_203()
    data, meta = await clean.picture(ev, now=T203, platform="telegram")
    assert meta["kind"] == "photo" and meta["library"] == "stocks" and wall_street == [STOCKS[0].title] and data
    assert ev.facts["number_check"]["ok"] and "Nasdaq 22,824.39 vs 23,879.20" in ev.facts["number_check"]["detail"]
    assert Image.open(io.BytesIO(data)).size  # a real picture, credited (CC0, Billie Grace Ward)


async def test_a_figure_that_disagrees_with_yahoo_stops_the_post(wall_street: list[str]) -> None:
    livedata.remember(quotes_at(T203))
    ev = event_203()
    figs = [dict(f) for f in ev.facts["figures"]]
    figs[1]["ref"] = 7_200.0  # a wrong previous close for the S&P 500
    figs[1]["change"] = (figs[1]["price"] - 7_200.0) / 7_200.0 * 100
    ev.facts = {**ev.facts, "figures": figs}
    with pytest.raises(visual.NumbersRefused, match="previous close"):
        await clean.picture(ev, now=T203, platform="telegram")
    assert wall_street == []


async def test_no_yahoo_quote_means_text_only_never_a_guess(wall_street: list[str], caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("INFO")
    livedata.forget()  # and the test's fetchers return nothing
    data, meta = await clean.picture(event_203(), now=T203, platform="telegram")
    assert (data, meta["kind"]) == (b"", "text") and "no Yahoo quote" in caplog.text and wall_street == []


# --- X 145: the same freshness on X as on Telegram ------------------------------------------------------------------------------

def nvidia_1008(released_at: datetime) -> NdEvent:
    at = datetime(2026, 11, 20, 20, 47, tzinfo=timezone.utc)
    head = "Nvidia's biggest one-day drop since January 2025, so far today: −12.0%"
    return NdEvent(key="data:2026-11-20:anomaly:NVDA", kind="data", title=head, headline=head, summary="", topic="data", evidence="price data",
                   category="markets", facts={"assets": ["NVDA"], "data": {"kind": "anomaly", "symbols": ["NVDA"], "headline": head,
                                                                         "last": {"kind": "intraday", "at": at.isoformat()}}},
                   virality=70.0, status="released", released_at=released_at, first_seen=datetime(2026, 11, 20, 21, 5, tzinfo=timezone.utc),
                   expires_at=released_at + timedelta(hours=6), slot="slot")


async def test_x_145_replayed_a_stale_price_is_held_on_x_exactly_as_on_telegram(db: Session) -> None:
    now = datetime(2026, 11, 21, 0, 11, tzinfo=timezone.utc)
    desk_running(db, now, {})
    ev = nvidia_1008(now - timedelta(minutes=5))
    db.add(ev)
    db.commit()
    assert livedata.needs_live(ev) and "not re-read" in livedata.problem(ev, now)  # Telegram's final check and X's publish gate: the same rule
    await engine.deliver_x(db, engine.settings_row(db), get_settings(), None, 0, now)  # Yahoo can't be read in the test: nothing fresh
    db.refresh(ev)
    assert ev.x_status == "pending" and ev.x_post_id is None and "fresh live price" in (ev.x_note or "")
    assert not db.scalars(select(NdPost)).first()  # nothing queued, nothing published


async def test_x_never_publishes_a_queued_post_built_from_the_2047_price(db: Session) -> None:
    now = datetime(2026, 11, 21, 0, 11, tzinfo=timezone.utc)
    desk_running(db, now, {})
    ev = nvidia_1008(now - timedelta(minutes=5))
    at = datetime(2026, 11, 20, 20, 47, tzinfo=timezone.utc)
    ev.facts = {**ev.facts, "live": {"checked_at": (now - timedelta(minutes=1)).isoformat(), "at": at.isoformat(), "price": 128.4, "prev": 145.98,
                                     "change_pct": -12.0, "source": "Yahoo Finance daily", "kind": "intraday"}}
    db.add(ev)
    db.commit()
    assert livedata.problem(ev, now) == "its live price is from 20:47 UTC, 204 min ago"
    post = engine.enqueue(db, [], kind="market", category="stocks", priority="scheduled", text=ev.headline + "\n\nSource: Yahoo Finance", key=f"event:{ev.id}",
                          score=7.0, facts={"event_id": ev.id}, src=[{"name": "Yahoo Finance", "url": "https://finance.yahoo.com/quote/NVDA"}],
                          source_text=ev.headline, names=["Yahoo Finance"], expires=None, now=now, dedupe=False)
    assert post is not None
    events.mark(ev, "x", "queued", post_id=post.id)
    db.commit()
    result = await engine.publish(db, engine.settings_row(db), post, get_settings(), None, 0, now)
    db.refresh(post)
    assert result == engine.STALE and post.status == "expired" and "20:47 UTC" in post.error


async def test_the_urgency_line_survives_beside_the_market_data_line(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """2026-11-20's silver crash (tests/test_market_urgency_20261120.py): the clean caption keeps "no one-day drop this large ... since
    June 2026" and now also says what the change is measured from and where the price came from."""
    import time

    from app.newsdesk import datapost, network
    from tests.test_market_urgency_20261120 import NOW, daily_at, desk_pass, released, tick
    from tests.test_newsdesk_telegram import Tg

    monkeypatch.setattr(network, "DEFAULT_STYLE", "clean")
    # production's desk pass reads the daily closes through datapost.fetch_all, which caches them: the re-read before sending finds them there
    monkeypatch.setitem(datapost._cache, "SI=F:2y", (time.time(), daily_at(NOW, 41.95)["SI=F"]))
    await desk_pass(db, NOW)
    tg = Tg()
    out = await tick(db, tg, NOW + timedelta(minutes=1))
    [ev] = released(db)
    text = tg.sent[0]["text"]
    assert out["sent"] and "6.4%" in text and "since June 2026" in text, text
    assert "Change from 24 hours earlier, $44.82." in text and "Market data: Yahoo Finance, matched against Hyperliquid, 01:45 UTC." in text, text  # SI=F has a second market since Phase 1
    assert ev.facts["data_note"].endswith("UTC.")  # the "Market data:" line closes the note (X keeps it when space is short)
