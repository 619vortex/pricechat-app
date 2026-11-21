"""Phase 0 of the engine roadmap (owner, 2026-11-21): the emergency stop, the accuracy firewall, crypto windows, X levels, owner alerts.

Each test is a production failure or a gap found in the 2026-11-21 audit:
- the Terminal / Admin emergency stop held X back while Telegram kept posting (and took over the desk's decisions);
- the figures check compared bare digits ("$17B" passed for "$17M", "+6.7%" for "-6.7%") and every caption's source text was handed the
  words "24 hours";
- a verified crypto move over 15 minutes, an hour or 4 hours was refused ("the chart covers 24.0 h, not the 1 h window") and never posted;
- X's own text after a live re-read printed the headline and "Source" only: Telegram 203's missing levels came back on X;
- every owner alert of 2026-11-20/21 was a log line ("no owner Telegram chat set, logged only"), lost for good.
No test reaches a real service.
"""

import math
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import alerts
from app.config.settings import get_settings
from app.models.newsdesk import NdEvent, NdPost
from app.newsdesk import checks, engine, events, livedata, network, photography, photos, sources, visual
from app.newsdesk import telegram as lane
from app.observability import dead_feeds, newsdesk_delivery_alerts
from tests.test_newsdesk_telegram import OPEN, Tg, agrees, desk_running, verified

NOW = datetime(2026, 11, 21, 6, 5, tzinfo=timezone.utc)


# --- 1. the emergency stop holds every automated post, on both platforms -----------------------------------------------------------

async def test_the_emergency_stop_holds_telegram_and_the_lane_never_takes_over_the_desk(db: Session) -> None:
    verified(db)
    desk = engine.settings_row(db)
    desk.kill_switch, desk.state = True, {"policy": OPEN}
    db.commit()
    asked: list[str] = []

    import httpx

    def web(request: httpx.Request) -> httpx.Response:
        asked.append(str(request.url))
        return httpx.Response(200, text="<rss><channel></channel></rss>")

    tg = Tg()
    out = await lane.tick(db, get_settings(), tg, NOW, transport=httpx.MockTransport(web))
    assert out["skipped"] == "Emergency stop is on" and tg.sent == [] and asked == []  # nothing sent, no feed read, no decision taken


async def test_a_stop_switched_on_during_a_run_still_holds_the_telegram_post(db: Session) -> None:
    desk = engine.settings_row(db)
    desk.kill_switch = True
    db.commit()
    s = lane.settings_row(db)
    message = {"link": "event:1", "title": "t", "headline": "h", "plain": "p", "source": "s", "sources": [], "fingerprint": "f", "writer": "w",
               "html": "p", "card": None}
    tg = Tg()
    assert await lane.send(db, s, tg, message, "@Pricechatupdates", {"chat_id": -100, "bot": "@b"}, NOW) is None
    assert tg.sent == []


async def test_the_emergency_stop_holds_x_queued_posts_and_the_owners_test_post(db: Session) -> None:
    s = engine.settings_row(db)
    s.kill_switch, s.mode = True, "live"
    db.commit()
    post = engine.enqueue(db, [], kind="news", category="crypto", priority="scheduled", text="A verified story\n\nSource: CoinDesk", key="k1", score=5.0,
                          facts={}, src=[{"name": "CoinDesk", "url": "https://coindesk.com/x"}], source_text="A verified story", names=["CoinDesk"],
                          expires=None, now=NOW, dedupe=False)
    assert post is not None
    assert await engine.publish(db, s, post, get_settings(), None, 0, NOW) == engine.STOPPED
    assert await engine.publish(db, s, post, get_settings(), None, 0, NOW, force_live=True) == engine.STOPPED
    db.refresh(post)
    assert post.status == "queued" and post.published_at is None
    assert await engine.deliver_x(db, s, get_settings(), None, 0, NOW) is None


async def test_the_network_emergency_pause_holds_x_publish_too(db: Session) -> None:
    network.configure(db, emergency=True)
    s = engine.settings_row(db)
    assert engine.emergency_stop(db) == network.EMERGENCY
    post = engine.enqueue(db, [], kind="news", category="crypto", priority="scheduled", text="A verified story\n\nSource: CoinDesk", key="k2", score=5.0,
                          facts={}, src=[{"name": "CoinDesk", "url": "https://coindesk.com/x"}], source_text="A verified story", names=["CoinDesk"],
                          expires=None, now=NOW, dedupe=False)
    assert post is not None and await engine.publish(db, s, post, get_settings(), None, 0, NOW) == engine.STOPPED


def market_event(db: Session, key: str, kind: str = "market", status: str = "released", **facts: Any) -> NdEvent:
    ev = NdEvent(key=key, kind=kind, title=key, headline=key.upper(), summary="", topic="move" if kind == "market" else "hack", label="",
                 evidence="price data" if kind == "market" else "two outlets", category="crypto", virality=80.0, sources=[], facts=facts, status=status,
                 released_at=NOW if status == "released" else None, first_seen=NOW, expires_at=NOW + timedelta(hours=2), slot="slot")
    db.add(ev)
    db.commit()
    return ev


async def test_the_owners_market_pause_holds_measured_market_posts_and_news_goes_on(db: Session) -> None:
    desk = engine.settings_row(db)
    desk.state = {"market_pause": True}
    db.commit()
    move = market_event(db, "bitcoin falls 4.0% in an hour", symbol="BTC-USD", kind_="move")
    news = market_event(db, "exchange hacked", kind="news")
    owed = events.pending(db, "telegram", NOW)
    db.refresh(move)
    db.refresh(news)
    assert move.id not in [e.id for e in owed] and move.tg_status == "skipped" and "paused" in (move.tg_note or "")
    assert "paused" not in (news.tg_note or "")  # news is never held by the market pause
    waiting = market_event(db, "gold falls 3% in an hour", status="candidate", symbol="GC=F")
    out = events.release(db, NOW)
    assert events.market_post(waiting) and (out is None or out.id != waiting.id)
    assert engine.settings_out(desk)["market_pause"] is True


# --- 2. the accuracy firewall: units, scale, sign, direction; nothing certifies itself ---------------------------------------------

@pytest.mark.parametrize("story, source, problem", [
    ("$17B of longs liquidated", "liquidations of $17M", "17b"),        # Telegram 204's class of error, made by a writer
    ("Bitcoin +6.7% in 24 hours", "BTC -6.7% in 24h", "+6.7%"),          # the opposite sign
    ("Shares at $6.7", "shares up 6.7%", "$6.7"),                       # a percent printed as a price
    ("Bitcoin -6.7% today", "bitcoin falls 6.7%", None),                # "falls 6.7%" is -6.7%
    ("$17 billion raised", "raised about $17B", None),                  # one figure, two spellings
    ("Nasdaq −4.4% to 22,824.39", "NASDAQ -4.4% TO 22,824.39", None),   # the typographic minus is a minus
])
def test_figures_are_checked_with_their_unit_scale_and_sign(story: str, source: str, problem: str | None) -> None:
    found = checks.unit_problems(story, source)
    assert (problem is None and found == []) or (problem is not None and any(problem in x for x in found)), found
    result = checks.check_post(f"{story}\n\nSource: CoinDesk", source_text=source, source_names=["CoinDesk"])
    assert result.ok == (problem is None), result.reason


def test_no_caption_is_handed_figures_it_does_not_have() -> None:
    ev = NdEvent(key="k", kind="news", title="Exchange hacked for $40 million", headline="EXCHANGE HACKED FOR $40 MILLION", summary="", topic="hack",
                 label="", evidence="two outlets", category="crypto", sources=[], facts={})
    assert checks.numbers(network.source_text_of(ev)) == {"40"}  # "24 hours" was in every source text: any "24" passed


def test_a_headline_never_says_the_opposite_of_the_measured_move() -> None:
    assert visual.direction_problem("BITCOIN RISES 4.0% IN AN HOUR TO $57,600", -4.0)
    assert visual.direction_problem("GOLD FALLS 2.1% TODAY", +2.1)
    assert visual.direction_problem("BITCOIN FALLS 4.0% IN AN HOUR TO $57,600", -4.0) == ""
    assert visual.direction_problem("BITCOIN RISES AS GOLD FALLS", -1.0) == ""  # both named: not the desk's own one-way headline


def test_a_percent_is_never_found_inside_a_bigger_one() -> None:
    from tests.test_price_updates_20261121 import T203, quotes_at, wide_at
    from app.newsdesk import headline

    m = wide_at(T203)
    ev = NdEvent(key="k", kind="market", title="", headline="", summary="", topic="move", label="", evidence="price data", category="stocks", sources=[],
                 facts={"symbol": "US-STOCKS", "kind": "wide", "window": "day", "as_of": m.as_of.isoformat(), **livedata.move_facts(m)})
    ev.headline = headline.market(m).replace("-4.4%", "-14.4%")  # "4.4%" sits inside "14.4%"
    check = visual.verify_quote_move(ev, quotes_at(T203), T203)
    assert not check.ok and any("doesn't print 4.4%" in r for r in check.reasons)


# --- 3. crypto moves over 15 minutes, an hour and 4 hours are checked against their own window -----------------------------------

class Candle:
    def __init__(self, t: int, o: float, c: float) -> None:
        self.t, self.o, self.c = t, o, c


class WindowHL:
    """Hyperliquid's candles for any interval: BTC flat near 60,000, then down to 57,600 over the last hour, with real-looking wiggles."""
    STEP = {"1m": 60, "5m": 300, "15m": 900}

    def __init__(self, now: datetime) -> None:
        self.now = int(now.timestamp())
        self.asked: list[tuple[str, int]] = []

    def price(self, t: int) -> float:
        start = self.now - 3600
        base = 60000.0 if t <= start else 60000.0 - 2400.0 * (t - start) / 3600
        return base + 9.0 * math.sin(t / 377.0) + 4.0 * math.sin(t / 91.0)

    def candles(self, coin: str, interval: str, count: int) -> list[Candle]:
        self.asked.append((interval, count))
        step = self.STEP[interval]
        first = (self.now // step) * step - (count - 1) * step
        return [Candle((first + i * step) * 1000, self.price(first + i * step), self.price(min(first + (i + 1) * step, self.now))) for i in range(count)]


@pytest.mark.parametrize("window, interval, span", [("15M", "1m", 900), ("1H", "5m", 3600), ("4H", "15m", 14400)])
async def test_each_window_gets_candles_that_cover_exactly_that_window(window: str, interval: str, span: int) -> None:
    hl = WindowHL(NOW)
    pts = await visual.candles_for("BTC", hl, window)
    assert hl.asked[0][0] == interval and len(pts) >= 10 and pts[-1][0] - pts[0][0] == span


def btc_quote(now: datetime) -> sources.Quote:
    t_now = now.timestamp()
    bars = [(int(t_now - 49 * 3600 + i * 300), 60000.0, 1.0) for i in range(48 * 12)]
    bars += [(int(t_now - 3600 + i * 300), 60000.0 - 2400.0 * i / 12, 1.0) for i in range(13)]
    return sources.Quote("BTC-USD", 57600.0, now, None, None, bars)


async def test_a_bitcoin_move_in_an_hour_is_published_with_its_photograph(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """The audit's replay: "BITCOIN FALLS 4.0% IN AN HOUR TO $57,600" was refused, "the chart covers 24.0 h, not the 1 h window"."""
    from tests.test_newsdesk_photography_20261119 import REAL, pic

    monkeypatch.setattr(network, "DEFAULT_STYLE", "clean")

    async def records(client: Any, subject: Any) -> list[Any]:
        return list(REAL.get(subject.key, []))

    async def fetch(client: Any, p: Any, min_width: int = 0, colour: bool = True) -> Image.Image:
        return pic()

    monkeypatch.setattr(photography, "_records", records)
    monkeypatch.setattr(photos, "fetch", fetch)
    photography._cache.clear()
    hl = WindowHL(NOW)
    monkeypatch.setattr(visual, "candles_for", lambda coin, market=None, window="24H": _candles(coin, hl, window))
    desk_running(db, NOW, OPEN)
    q = {"BTC-USD": btc_quote(NOW)}
    await events.decide(db, NOW, quotes=q, watchlist=sources.DEFAULT_WATCHLIST, categories={}, desk_state=engine.settings_row(db).state,
                        liquidations=False, second_opinion=agrees, daily={})
    [ev] = list(db.scalars(select(NdEvent).where(NdEvent.kind == "market")))
    assert ev.headline == "BITCOIN FALLS 4.0% IN AN HOUR TO $57,600" and ev.facts["window"] == "1h"
    if ev.status != "released":
        ev.status, ev.released_at, ev.slot = "released", NOW, "slot"
        db.commit()
    livedata.remember(q)

    class PhotoTg(Tg):
        async def send_photo(self, chat: Any, image: bytes, caption: str, **kw: Any) -> dict[str, Any]:
            self.sent.append({"chat": chat, "text": caption, "card": True, "image": image, **kw})
            return self._answer()

    tg = PhotoTg()
    out = await lane.tick(db, get_settings(), tg, NOW + timedelta(seconds=20))
    db.refresh(ev)
    assert out["sent"], out
    assert ev.facts["number_check"]["ok"] and ev.facts["visual"]["kind"] == "photo" and ev.facts["visual"]["library"] == "bitcoin" and tg.sent[0]["card"]
    assert "4.0%" in tg.sent[0]["text"] and "Market data: Yahoo Finance, matched against Hyperliquid" in tg.sent[0]["text"]


async def _candles(coin: str, hl: WindowHL, window: str) -> list[tuple[int, float]]:
    from app.newsdesk.visual import CHART_CANDLES
    from app.tradingos.market import INTERVALS

    interval, count = CHART_CANDLES.get(window, ("15m", 97))
    rows = hl.candles(coin, interval, count)
    step = INTERVALS[interval]
    return [(rows[0].t // 1000, rows[0].o)] + [(r.t // 1000 + step, r.c) for r in rows]


# --- 4. X keeps the levels when it writes its own text ------------------------------------------------------------------------

def test_x_own_text_after_a_re_read_keeps_the_reference_and_market_data_line(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_price_updates_20261121 import T203, event_203

    monkeypatch.setattr(engine, "X_FORMAT", "visual")
    ev = event_203()
    ev.virality = 74.0
    b = events.brief_of(ev, T203)
    text, _, told, _ = engine.x_words(ev, b, [], ["Yahoo Finance"], package=False)  # the package was dropped: the figures changed
    assert "22,824.39" in text and "Market data: Yahoo Finance, 20:00 UTC, intraday (US market open)." in text
    assert network.x_length(text) <= network.X_LIMIT
    assert checks.check_post(text, source_text=told, source_names=["Yahoo Finance"]).ok


async def test_a_silver_move_gets_its_bullion_photograph_after_the_yahoo_check(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """Commodities had no checked series (Hyperliquid has no GOLD/SILVER candles): text only, figures unchecked."""
    import time

    from app.newsdesk import clean, datapost
    from tests.test_market_urgency_20261120 import NOW as SILVER_NOW, daily_at, desk_pass, released
    from tests.test_newsdesk_photography_20261119 import pic, rec

    bar = rec("File:Scottsdale Mint 100 Troy Ounce Silver Bar.jpg", "Scottsdale Mint", "CC BY 2.0", 4000, 2600)

    async def records(client: Any, subject: Any) -> list[Any]:
        return [bar] if subject.key == "silver" else []

    async def fetch(client: Any, p: Any, min_width: int = 0, colour: bool = True) -> Image.Image:
        return pic()

    monkeypatch.setattr(photography, "_records", records)
    monkeypatch.setattr(photos, "fetch", fetch)
    photography._cache.clear()
    monkeypatch.setitem(datapost._cache, "SI=F:2y", (time.time(), daily_at(SILVER_NOW, 41.95)["SI=F"]))
    await desk_pass(db, SILVER_NOW)
    [ev] = released(db)
    data, meta = await clean.picture(ev, now=SILVER_NOW + timedelta(seconds=30), platform="telegram")
    assert meta["kind"] == "photo" and meta["library"] == "silver" and data
    assert ev.facts["number_check"]["ok"]


# --- 5. owner alerts are kept until they can be delivered; the watchdog runs the desk's monitors ------------------------------------

class Bot:
    def __init__(self) -> None:
        self.sent: list[str] = []

    @property
    def transport(self) -> Any:
        import json

        import httpx

        def handle(request: httpx.Request) -> httpx.Response:
            self.sent.append(json.loads(request.content).get("text", ""))
            return httpx.Response(200, json={"ok": True, "result": {"message_id": len(self.sent)}})

        return httpx.MockTransport(handle)


async def test_an_alert_with_no_chat_linked_is_kept_and_delivered_once_a_chat_exists(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config.settings import Settings

    async def nobody(*a: Any, **k: Any) -> None:
        return None

    monkeypatch.setattr(alerts, "discover_owner_chat", nobody)
    bot = Bot()
    st = Settings(telegram_bot_token="123:abc")
    for _ in range(3):  # the silence alert, raised again and again (46 times in 13 h on 2026-11-20/21)
        assert not await alerts.notify_owner(db, "ops:silence:newsdesk_telegram", "No Telegram post for 15 h", settings=st, transport=bot.transport, now=NOW)
    assert not await alerts.notify_owner(db, "newsdesk:delivery:1045:X", "Event #1045 not delivered to X", settings=st, transport=bot.transport, now=NOW)
    kept = alerts.pending_alerts(db)
    assert bot.sent == [] and len(kept) == 2 and kept[0].sent_count in (1, 3)
    alerts.settings_row(db).telegram_chat = "424242"
    db.commit()
    assert await alerts.flush_pending(db, settings=st, transport=bot.transport, now=NOW + timedelta(minutes=5)) == 2
    assert len(bot.sent) == 1 and "2 alert(s)" in bot.sent[0] and "(x3)" in bot.sent[0] and "#1045" in bot.sent[0]
    assert alerts.pending_alerts(db) == []


def test_the_watchdog_names_feeds_dead_for_a_day(db: Session) -> None:
    s = engine.settings_row(db)
    s.state = {"feed_health": {"at": NOW.isoformat(), "feeds": {
        "https://www.federalreserve.gov/feeds/press_all.xml": {"name": "Federal Reserve", "tier": 0, "problem": "HTTP 404", "fails": 400,
                                                                "last_ok": (NOW - timedelta(days=3)).isoformat()},
        "https://www.mining.com/feed/": {"name": "Mining.com", "tier": 2, "problem": "ReadTimeout", "fails": 2, "last_ok": (NOW - timedelta(minutes=20)).isoformat()},
    }}}
    db.commit()
    dead = dead_feeds(db, NOW)
    assert len(dead) == 1 and dead[0].startswith("Federal Reserve (official source): HTTP 404")  # a timeout 20 minutes ago is not dead


async def test_the_watchdog_runs_the_desks_delivery_alerts_even_while_telegram_is_off(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[datetime] = []

    async def alert_slots(db_: Session, now: datetime, p: Any) -> int:
        called.append(now)
        return 1

    monkeypatch.setattr(events, "alert_slots", alert_slots)
    engine.settings_row(db)
    lane.settings_row(db).enabled = False  # the lane that used to be the only caller
    db.commit()
    assert await newsdesk_delivery_alerts(db, NOW) == 1 and called == [NOW]
