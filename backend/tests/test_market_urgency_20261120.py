"""The 2026-11-20 silver crash, replayed (owner, 2026-11-20: "fix the root causes ... using real production evidence").

Production that night: silver fell 6.4% in 24 hours (event #870) and 6.75% on the day (#880). #870 scored 56 against a fixed "a move needs 70"
bar and the 04:00 Dubai slot was skipped; #880 waited for the 08:00 Dubai slot with a 01:40 UTC price, Telegram's final check refused it as
140 minutes old, and X's own-text path published the same 2h15m-old figure under the one silver photo that passed the pixel rules: a
collector-coin picture. Owner alerts were "logged only": nobody was told.

The daily closes here are Yahoo Finance's own (tests/newsdesk_silver_daily_20261120.json, read 2026-11-20 04:35 UTC). No test reaches a
real service: prices come from the test's desk pass, Telegram and X are fakes.
"""

import io
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import alerts
from app.config.settings import Settings, get_settings
from app.models.alerts import OwnerAlert
from app.models.newsdesk import NdEvent, NdPost
from app.newsdesk import datapost, engine, events, livedata, photography, photos, sources, urgency
from app.newsdesk import telegram as lane
from tests.test_newsdesk_telegram import Tg, agrees, desk_running

FIX = json.loads(Path(__file__).with_name("newsdesk_silver_daily_20261120.json").read_text())
NOW = datetime(2026, 11, 20, 1, 45, tzinfo=timezone.utc)  # 2 h 15 min before the next slot (04:00 UTC = 08:00 Dubai)
START = 44.82  # silver 24 hours earlier
SOURCE_LINE = "\n\nSource: Yahoo Finance"


def closes(sym: str = "SI=F") -> list[tuple[datetime, float]]:
    return [(datetime.fromtimestamp(t, tz=timezone.utc), c) for t, c in FIX[sym]]


def daily_at(now: datetime, price: float) -> dict[str, list[tuple[datetime, float]]]:
    """The real daily history, with the live session's bar at `price` (as Yahoo serves it while silver trades)."""
    hist = closes()[:-1]
    at = now - timedelta(minutes=5)
    datapost._last["SI=F"] = datapost.Last("intraday", price, hist[-1][1], at, "FUTURE")
    return {"SI=F": [*hist, (at, price)]}


def silver(now: datetime, end: float = 41.95, at: datetime | None = None) -> sources.Quote:
    """Five-minute bars: flat at START until 24 h ago, then down to `end` (-6.4% for 41.95)."""
    t_now = now.timestamp()
    bars = [(int(t_now - 49 * 3600 + i * 300), START, 1.0) for i in range(25 * 12)]
    bars += [(int(t_now - 24 * 3600 + i * 300), START + (end - START) * i / 288, 1.0) for i in range(289)]
    return sources.Quote("SI=F", end, at or now, None, None, bars)


async def desk_pass(db: Session, now: datetime, end: float = 41.95, plan: dict[str, Any] | None = None) -> dict[str, Any]:
    desk_running(db, now, {} if plan is None else plan)  # {}: the production plan (six slots, the 70 bar for routine moves)
    return await events.decide(db, now, quotes={"SI=F": silver(now, end)}, watchlist=sources.DEFAULT_WATCHLIST, categories={},
                               desk_state=engine.settings_row(db).state, liquidations=False, second_opinion=agrees, daily=daily_at(now, end))


def released(db: Session) -> list[NdEvent]:
    return list(db.scalars(select(NdEvent).where(NdEvent.status == "released")))


# --- urgency from the asset's own history (no keyword, no fixed line alone) ------------------------------------------------------


def test_silver_on_the_night_measured_against_its_own_last_90_days() -> None:
    at = datetime(2026, 11, 20, 4, 35, tzinfo=timezone.utc)
    hist = closes()  # the last bar is the live session
    day = urgency.assess(-6.75, 6.75 / 5, hist, at, last_is_live=True)  # #880's figure: the day's change
    assert (day.level, day.since_days, day.since, day.z, day.sd) == (urgency.MAJOR, 189, "May 2026", 2.83, 2.38)
    move = urgency.assess(-6.4, 6.4 / 5, hist, at, last_is_live=True)  # #870's figure: 24 hours
    assert (move.level, move.since, move.z) == (urgency.SIGNIFICANT, "June 2026", 2.68)
    assert urgency.assess(-4.84, 0.97, hist, at, last_is_live=True).level == urgency.ROUTINE  # 18 Nov: under its line
    assert urgency.assess(-1.5, 0.3, hist, at, last_is_live=True).level == urgency.ROUTINE
    # no history (a feed down) never makes a move MORE urgent than its line says
    assert urgency.assess(-6.4, 1.28, [], at).level == urgency.ROUTINE
    assert urgency.assess(-12.0, 2.4, [], at).level == urgency.MAJOR


def test_the_same_size_is_routine_in_a_wild_year_and_major_in_a_calm_one() -> None:
    at = datetime(2026, 11, 20, 12, tzinfo=timezone.utc)
    t0 = at - timedelta(days=400)
    calm = [(t0 + timedelta(days=i), 100.5 if i % 2 else 100.0) for i in range(400)]  # half a percent a day
    wild = [(t0 + timedelta(days=i), 106.0 if i % 2 else 100.0) for i in range(400)]  # six percent a day, every day
    assert urgency.assess(-5.5, 1.1, calm, at, last_is_live=False).level == urgency.MAJOR
    assert urgency.assess(-5.5, 1.1, wild, at, last_is_live=False).level == urgency.ROUTINE


# --- the replay: prompt, fresh, once ---------------------------------------------------------------------------------------------


async def test_the_silver_crash_goes_out_now_not_at_the_next_slot(db: Session) -> None:
    await desk_pass(db, NOW)
    [ev] = released(db)
    u = ev.facts["urgency"]
    assert ev.kind == "market" and ev.slot == "breaking" and ev.facts["market_major"]  # between slots: not held for 08:00 Dubai
    assert u["level"] == urgency.SIGNIFICANT and u["since"] == "June 2026" and ev.parts["context"] == u["points"]
    assert ev.headline == "SILVER FALLS 6.4% IN 24 HOURS TO $41.95"
    assert "Daily closes show no one-day drop this large for Silver since June 2026." in ev.summary
    assert "June 2026" in ev.facts["told"]  # the context line is verified like every other figure
    assert not db.scalars(select(NdEvent).where(NdEvent.kind == "data")).first()  # never a second crash story for the same move
    assert "MARKET MOVE" in ev.why or "market move" in ev.why


async def tick(db: Session, tg: Tg, at: datetime) -> dict[str, Any]:
    """The Telegram lane's run; the desk ran moments ago, so the lane reads no prices of its own (no test reaches Yahoo)."""
    desk_running(db, at, {})
    return await lane.tick(db, get_settings(), tg, at)


async def test_both_platforms_reread_the_price_and_print_the_same_fresh_figure(db: Session) -> None:
    await desk_pass(db, NOW)
    tg = Tg()
    later = NOW + timedelta(minutes=1)
    out = await tick(db, tg, later)
    [ev] = released(db)
    assert out["sent"] and "6.4%" in tg.sent[0]["text"]
    live = ev.facts["live"]
    assert live["checked_at"] == later.isoformat() and live["source"] == "Yahoo Finance 5-minute" and livedata.problem(ev, later) == ""
    await engine.deliver_x(db, engine.settings_row(db), get_settings(), None, 0, later + timedelta(seconds=30))
    db.refresh(ev)
    assert ev.x_status == "dry_run" and "6.4%" in db.get(NdPost, ev.x_post_id).text  # X printed what Telegram printed, read seconds before


async def test_no_fresh_price_holds_the_post_on_both_platforms_and_a_new_one_rewrites_it(db: Session) -> None:
    await desk_pass(db, NOW)
    livedata.forget()  # 20 minutes later the desk's reading is old, and Yahoo doesn't answer (the test's fetchers return nothing)
    later = NOW + timedelta(minutes=20)
    tg = Tg()
    out = await tick(db, tg, later)
    [ev] = released(db)
    assert tg.sent == [] and "waiting for a fresh live price" in out["skipped"] and ev.tg_status == "pending"
    await engine.deliver_x(db, engine.settings_row(db), get_settings(), None, 0, later)
    db.refresh(ev)
    assert ev.x_status == "pending" and ev.x_post_id is None and "fresh live price" in (ev.x_note or "")
    # the price comes back, lower: the post says what silver is doing NOW, never the old figure
    livedata.remember({"SI=F": silver(later, 41.70)})
    out = await tick(db, tg, later + timedelta(minutes=1))
    db.refresh(ev)
    assert out["sent"] and "7.0%" in tg.sent[0]["text"] and "6.4%" not in tg.sent[0]["text"]
    assert ev.headline == "SILVER FALLS 7.0% IN 24 HOURS TO $41.70" and "41.7" in ev.facts["told"]


async def test_a_move_that_has_faded_is_not_published_anywhere(db: Session) -> None:
    await desk_pass(db, NOW)
    livedata.forget()
    later = NOW + timedelta(minutes=10)
    livedata.remember({"SI=F": silver(later, 44.10)})  # back to -1.6%: inside its line
    tg = Tg()
    await tick(db, tg, later)
    await engine.deliver_x(db, engine.settings_row(db), get_settings(), None, 0, later)
    [ev] = released(db)
    assert tg.sent == [] and (ev.tg_status, ev.x_status) == ("skipped", "skipped")
    assert "no longer stands" in ev.tg_note and "no longer stands" in ev.x_note


async def test_x_never_sends_a_queued_post_whose_figures_went_stale(db: Session) -> None:
    await desk_pass(db, NOW)
    [ev] = released(db)
    written = NOW + timedelta(minutes=1)
    assert (await livedata.refresh(db, ev, written))[0] == livedata.OK  # X read the price and wrote its post ...
    s = engine.settings_row(db)
    post = engine.enqueue(db, [], kind="market", category="commodities", priority="breaking", text=ev.headline + SOURCE_LINE, key=f"event:{ev.id}",
                          score=6.0, facts={"event_id": ev.id}, src=ev.sources, source_text=ev.facts["told"], names=["Yahoo Finance"], expires=None, now=written,
                          dedupe=False)
    assert post is not None
    events.mark(ev, "x", "queued", post_id=post.id)
    db.commit()
    result = await engine.publish(db, s, post, get_settings(), None, 0, NOW + timedelta(minutes=16))
    db.refresh(ev)
    db.refresh(post)
    assert result == engine.STALE and post.status == "expired" and "stale market data" in post.error
    assert ev.x_status == "pending" and ev.x_post_id is None  # written again from a fresh price on the next run


async def test_the_old_two_post_shape_cannot_happen_again(db: Session) -> None:
    """#880 (the data post) and #870 (the move) were one story: a data post on silver already out keeps the move from becoming a second."""
    desk_running(db, NOW, {})
    data = NdEvent(key="data:2026-11-20:anomaly:SI=F", kind="data", title="Silver's biggest one-day drop since May 2026, so far today: −6.7%",
                   headline="Silver's biggest one-day drop since May 2026, so far today: −6.7%", summary="", topic="data", evidence="price data",
                   category="markets", facts={"assets": ["SI=F"], "data": {"kind": "anomaly", "symbols": ["SI=F"]}}, virality=57.0, status="released",
                   released_at=NOW - timedelta(minutes=30), first_seen=NOW - timedelta(hours=2), expires_at=NOW + timedelta(hours=6), slot="slot")
    db.add(data)
    db.commit()
    await events.decide(db, NOW, quotes={"SI=F": silver(NOW)}, watchlist=sources.DEFAULT_WATCHLIST, categories={},
                        desk_state=engine.settings_row(db).state, liquidations=False, second_opinion=agrees, daily=daily_at(NOW, 41.95))
    assert [e.id for e in released(db)] == [data.id] and not db.scalars(select(NdEvent).where(NdEvent.kind == "market")).first()
    assert events.canon_assets(data) == {"SILVER"}


async def test_a_busy_market_day_has_no_daily_count_only_a_burst_guard(db: Session) -> None:
    # owner, 2026-11-24: "Do not impose an arbitrary daily limit on genuinely major breaking news" (this test held the 4th major move of a
    # day until then). Three majors spread over the morning: a fourth asset's major move still goes out.
    desk_running(db, NOW, {})
    for i, asset in enumerate(("GOLD", "OIL", "BTC")):
        db.add(NdEvent(key=f"move:{asset}:x", kind="market", title=f"{asset} move", headline=f"{asset} MOVE", summary="", topic="move", evidence="price data",
                       category="markets", facts={"assets": [asset], "market_major": "major", "urgency": {"level": "major"}}, virality=70.0, status="released",
                       released_at=NOW - timedelta(hours=3 + i), first_seen=NOW - timedelta(hours=3 + i), expires_at=NOW, slot="breaking"))
    db.commit()
    await events.decide(db, NOW, quotes={"SI=F": silver(NOW)}, watchlist=sources.DEFAULT_WATCHLIST, categories={},
                        desk_state=engine.settings_row(db).state, liquidations=False, second_opinion=agrees, daily=daily_at(NOW, 41.95))
    ev = db.scalars(select(NdEvent).where(NdEvent.key.like("move:SILVER%"))).one()
    assert ev.status == "released" and "major market moves went out in 24 hours" not in str((ev.facts or {}).get("breaking_miss") or "")


async def test_four_majors_inside_an_hour_hold_the_next_for_a_person(db: Session) -> None:
    desk_running(db, NOW, {})
    for i, asset in enumerate(("GOLD", "OIL", "BTC", "ETH")):
        db.add(NdEvent(key=f"move:{asset}:x", kind="market", title=f"{asset} move", headline=f"{asset} MOVE", summary="", topic="move", evidence="price data",
                       category="markets", facts={"assets": [asset], "market_major": "major", "urgency": {"level": "major"}}, virality=70.0, status="released",
                       released_at=NOW - timedelta(minutes=58 - 12 * i), first_seen=NOW - timedelta(minutes=59), expires_at=NOW, slot="breaking"))
    db.commit()
    await events.decide(db, NOW, quotes={"SI=F": silver(NOW)}, watchlist=sources.DEFAULT_WATCHLIST, categories={},
                        desk_state=engine.settings_row(db).state, liquidations=False, second_opinion=agrees, daily=daily_at(NOW, 41.95))
    ev = db.scalars(select(NdEvent).where(NdEvent.key.like("move:SILVER%"))).one()
    assert ev.status == "candidate" and "unusual burst" in ev.facts["breaking_miss"]


# --- the silver photograph ---------------------------------------------------------------------------------------------------------


def test_the_silver_library_is_bullion_only() -> None:
    lib = photography.BY_KEY["silver"].files
    assert "File:Silver coins, Rostov-on-Don, Russia.jpg" not in lib and len(lib) >= 6
    assert all(not photography.CLUTTERED.search(f) for f in lib)
    assert photography.refused("silver", "File:Silver coins, Rostov-on-Don, Russia.jpg")
    assert photography.refused("gold", "Commemorative gold medals collection")
    assert not photography.refused("silver", "File:Scottsdale Mint 100 Troy Ounce Silver Bar.jpg")
    assert not photography.refused("bitcoin", "Physical bitcoin coin")  # a Bitcoin "coin" is the subject's usual photograph


async def test_a_grey_silver_bar_is_not_refused_as_black_and_white_in_the_library() -> None:
    import numpy as np

    rng = np.random.default_rng(7)  # brushed metal: grey everywhere, light and shadow, fine grain (no colour at all)
    shade = np.linspace(60, 210, 2400)[None, :] + rng.normal(0, 18, (1600, 2400))
    grey = np.clip(shade, 0, 255).astype(np.uint8)
    img = Image.fromarray(np.stack([grey, grey, grey], axis=-1))
    buf = io.BytesIO()
    img.save(buf, "JPEG")
    body = buf.getvalue()
    transport = httpx.MockTransport(lambda r: httpx.Response(200, content=body))
    p = photos.LicensedPhoto(title="Silver Bar 01", url="https://upload.wikimedia.test/silver.jpg", page="https://commons.wikimedia.org/wiki/File:Silver_Bar_01.jpg",
                             creator="Kuebi", provider="wikimedia", licence="CC BY-SA 3.0", width=2400, height=1600)
    async with httpx.AsyncClient(transport=transport) as c:
        assert await photos.fetch(c, p, min_width=1600) is None  # search results: a grey picture reads as a black-and-white archive print
        assert await photos.fetch(c, p, min_width=1600, colour=False) is not None  # the editor's own silver bar


# --- owner alerts reach the owner -----------------------------------------------------------------------------------------------


class Bot:
    def __init__(self, creators: dict[str, int], dm: bool = True) -> None:
        self.creators, self.dm = creators, dm
        self.sent: list[dict[str, Any]] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        if request.url.path.endswith("/getChatAdministrators"):
            uid = self.creators.get(str(body["chat_id"]).lower())
            admins = [{"status": "administrator", "user": {"id": 9, "is_bot": True}}]
            if uid:
                admins.append({"status": "creator", "user": {"id": uid, "is_bot": False, "username": "pcowner", "first_name": "Owner"}})
            return httpx.Response(200, json={"ok": True, "result": admins})
        if request.url.path.endswith("/sendMessage"):
            if not self.dm:
                return httpx.Response(403, json={"ok": False, "error_code": 403, "description": "Forbidden: bot can't initiate conversation with a user"})
            self.sent.append(body)
            return httpx.Response(200, json={"ok": True, "result": {"message_id": len(self.sent)}})
        return httpx.Response(404, json={"ok": False, "description": "not here"})


SETTINGS = Settings(telegram_bot_token="123:abc", telegram_api_base="https://api.telegram.test")


async def test_the_owner_is_found_and_the_missed_post_alert_reaches_their_dm(db: Session) -> None:
    bot = Bot({"@thepricechat": 777, "@botpricechat": 777, "@pricechatupdates": 777})
    t = httpx.MockTransport(bot.handle)
    assert alerts.owner_chat(db) is None
    text = "News Desk: the 08:00 Dubai post (event #880) was not delivered to Telegram: skipped."
    assert await alerts.notify_owner(db, "newsdesk:delivery:880:Telegram", text, settings=SETTINGS, transport=t, now=NOW)
    assert alerts.owner_chat(db) == "777"
    assert [m["chat_id"] for m in bot.sent] == [777, 777]
    assert "owner alerts are connected" in bot.sent[0]["text"] and bot.sent[1]["text"] == text
    assert db.get(OwnerAlert, "newsdesk:delivery:880:Telegram").sent_count == 1


async def test_the_owner_is_never_guessed(db: Session) -> None:
    two = Bot({"@thepricechat": 777, "@botpricechat": 888})  # two different creators: no guess
    assert await alerts.discover_owner_chat(db, settings=SETTINGS, transport=httpx.MockTransport(two.handle), now=NOW) is None
    assert two.sent == [] and "can't tell who the owner is" in db.get(OwnerAlert, alerts.DISCOVER_KEY).last_text
    blocked = Bot({"@thepricechat": 777}, dm=False)  # the owner never pressed Start: told what to do, nothing saved
    assert await alerts.discover_owner_chat(db, settings=SETTINGS, transport=httpx.MockTransport(blocked.handle), now=NOW, force=True) is None
    assert alerts.owner_chat(db) is None and "press Start" in db.get(OwnerAlert, alerts.DISCOVER_KEY).last_text
    alerts.settings_row(db).enabled = False  # the owner turned alerts off: never turned back on
    db.commit()
    one = Bot({"@thepricechat": 777})
    assert await alerts.discover_owner_chat(db, settings=SETTINGS, transport=httpx.MockTransport(one.handle), now=NOW, force=True) is None
    assert one.sent == []


async def test_a_prompt_market_post_that_was_not_delivered_is_told_with_the_reason(db: Session, monkeypatch: Any) -> None:
    alerts.settings_row(db).telegram_chat = "777"
    db.commit()
    sent: list[str] = []

    async def capture(settings: Any, chat: str, text: str, transport: Any = None) -> None:
        sent.append(text)

    monkeypatch.setattr(alerts, "send_text", capture)
    events._alerted.clear()
    db.add(NdEvent(key="move:SILVER:t", kind="market", title="SILVER FALLS 6.4% IN 24 HOURS TO $41.95", headline="SILVER FALLS 6.4% IN 24 HOURS TO $41.95",
                   summary="", topic="move", evidence="price data", category="commodities", facts={"assets": ["SILVER"], "market_major": "significant"},
                   virality=64.0, status="released", released_at=NOW - timedelta(minutes=50), first_seen=NOW - timedelta(hours=1), expires_at=NOW,
                   slot="breaking", tg_status="missed", tg_note="waiting for a fresh live price: no live SI=F price could be read", x_status="dry_run"))
    db.commit()
    await events.alert_slots(db, NOW, events.policy({}))
    [text] = [t for t in sent if "event #" in t and "SILVER" in t]
    assert "not delivered to Telegram: missed" in text and "no live SI=F price" in text and "SILVER FALLS 6.4%" in text
