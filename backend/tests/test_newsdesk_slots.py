"""The six-slot schedule (owner, 2026-11-09): one post at 00, 04, 08, 12, 16 and 20 Dubai time; the best verified story that clears the bar,
else a market check made only of measured prices; nothing between slots; missed slots and failed deliveries alert the owner. Also the
tiered feeds and feed health. No test reaches a real service."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.settings import get_settings
from app.members.referrals import program
from app.models.newsdesk import NdEvent, NdTelegramPost
from app.newsdesk import engine, events, recap, sources, telegram as lane

from tests.test_newsdesk_telegram import Tg, quote, verified

SLOT = datetime(2026, 11, 9, 4, 0, tzinfo=timezone.utc)  # 08:00 Dubai


@dataclass
class Mkt:
    coin: str
    mark: float
    prev_day: float


class FakeHL:
    def __init__(self, coins: dict[str, tuple[float, float]]) -> None:
        self.coins = coins

    def market(self, coin: str) -> Mkt:
        if coin not in self.coins:
            raise KeyError(coin)
        mark, prev = self.coins[coin]
        return Mkt(coin, mark, prev)


HL = FakeHL({"BTC": (95120.0, 94000.0), "ETH": (3410.0, 3440.0), "HYPE": (38.2, 36.7), "SOL": (182.4, 178.3)})


@pytest.fixture(autouse=True)
def _slots(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(events, "DEFAULT_SCHEDULE", "slots")
    monkeypatch.setattr(sources, "EXTRA_FEEDS", [])
    monkeypatch.setattr(sources, "WORLDMONITOR_FEEDS", [])
    events._alerted.clear()
    program(db).updates_channel = "@Pricechatupdates"
    db.commit()


async def agrees(item: dict[str, Any], price: float, market: Any = None) -> tuple[bool, str]:
    return True, "agrees"


async def decide(db: Session, now: datetime, quotes: dict[str, Any] | None = None, market: Any = HL, plan: dict[str, Any] | None = None) -> dict[str, Any]:
    desk = engine.settings_row(db)
    desk.state = {"last_run": {"at": now.isoformat()}, **({"policy": plan} if plan else {})}
    db.commit()
    return await events.decide(db, now, quotes=quotes or {}, watchlist=[], categories=desk.categories or {}, desk_state=desk.state, second_opinion=agrees, liquidations=False,
                               market=market)


def released(db: Session) -> list[NdEvent]:
    db.expire_all()
    return list(db.scalars(select(NdEvent).where(NdEvent.status == "released").order_by(NdEvent.released_at, NdEvent.id)))


# --- the clock -------------------------------------------------------------------------------------------------------


def test_the_six_dubai_slots_are_every_four_hours_and_the_next_one_is_known() -> None:
    p = events.policy({})
    assert events.sched(p) == "slots" and p.slot_hours == (0, 4, 8, 12, 16, 20)
    now = datetime(2026, 11, 9, 5, 30, tzinfo=timezone.utc)  # 09:30 Dubai
    times = [t for t in events.slot_times(now, p) if t <= now]
    assert [events.slot_label(t) for t in times[-6:]] == ["12:00", "16:00", "20:00", "00:00", "04:00", "08:00"]
    assert [t.hour for t in times[-6:]] == [8, 12, 16, 20, 0, 4]  # the same instants in UTC
    assert events.next_slot(now, p) == datetime(2026, 11, 9, 8, 0, tzinfo=timezone.utc) and events.slot_label(events.next_slot(now, p)) == "12:00"
    assert events.current_slot(SLOT + timedelta(minutes=30), p) == SLOT
    assert events.current_slot(SLOT + timedelta(minutes=91), p) is None  # past the catch-up: never posted late


# --- what fills a slot -----------------------------------------------------------------------------------------------


async def test_a_story_that_clears_the_bar_fills_the_slot_and_nothing_else_goes_until_the_next(db: Session) -> None:
    verified(db, "Bybaz exchange says hackers drained $400 million from its hot wallet", now=SLOT, hours=0.2)
    await decide(db, SLOT + timedelta(minutes=2))
    out = released(db)
    assert len(out) == 1 and out[0].kind == "news" and out[0].slot == "slot" and "08:00 Dubai slot" in out[0].why
    verified(db, "Kryptex exchange says hackers drained $40 million from its hot wallet", now=SLOT + timedelta(minutes=30), hours=0)  # under the major line
    await decide(db, SLOT + timedelta(minutes=40))
    assert len(released(db)) == 1  # the slot has its post
    waiting = db.scalar(select(NdEvent).where(NdEvent.status == "candidate"))
    assert waiting is not None and "waiting for the next slot (12:00 Dubai)" in waiting.why
    verified(db, "Zentra exchange says hackers drained $700 million from its hot wallet", now=SLOT + timedelta(hours=3, minutes=55), hours=0)
    await decide(db, SLOT + timedelta(hours=4, minutes=1))
    nxt = released(db)
    assert len(nxt) == 2 and nxt[1].kind == "news" and "12:00 Dubai slot" in nxt[1].why  # a fresh story, not the market check


async def test_without_a_qualifying_story_the_slot_gets_a_verified_market_check_once(db: Session) -> None:
    await decide(db, SLOT + timedelta(minutes=1), quotes={"BTC-USD": quote("BTC-USD", 94000, 95100, SLOT)})
    out = released(db)
    assert len(out) == 1 and out[0].kind == "recap" and out[0].key == "recap:2026-11-09T04:00"
    f = out[0].facts
    assert out[0].headline == "Crypto holds steady, HYPE up 4.1% in 24 hours"  # a news line measured from the data, not an alert
    assert f["telegram_html"].startswith("<b>Crypto holds steady, HYPE up 4.1% in 24 hours</b>")
    assert "Bitcoin $95,120 (+1.2%) · Ethereum $3,410 (-0.9%) · HYPE $38.20 (+4.1%)" in f["telegram_html"]
    assert "ALERT" not in f["telegram_html"].upper() and "🚨" not in f["telegram_html"] and "📊" not in f["telegram_html"]
    assert f["x_text"].startswith("Crypto holds steady, HYPE up 4.1% in 24 hours.") and "$BTC +1.2%" in f["x_text"] and len(f["x_text"]) <= 280 and "http" not in f["x_text"]
    assert "assets" not in f  # a market check never puts an asset on its six-hour cooldown
    await decide(db, SLOT + timedelta(minutes=10))
    assert len(released(db)) == 1  # one per slot, however many runs


async def test_a_coin_the_two_price_sources_disagree_on_is_left_out_and_thin_data_means_no_recap(db: Session) -> None:
    lines = recap.rows(HL.__dict__ and {c: HL.market(c) for c in ("BTC", "ETH", "HYPE", "SOL")}, {"BTC-USD": quote("BTC-USD", 90000, 90000, SLOT)}, SLOT)
    assert "$BTC" not in {r["tag"] for r in lines}  # Hyperliquid 95,120 vs Yahoo 90,000
    thin = FakeHL({"BTC": (95120.0, 94000.0)})
    await decide(db, SLOT + timedelta(minutes=1), market=thin)
    assert released(db) == []  # nothing made up to fill the slot


async def test_a_verified_important_story_held_five_hours_still_takes_the_slot_instead_of_a_market_check(db: Session) -> None:
    # owner, 2026-11-10: important stories were fading below the bar while they waited. A hack confirmed by two outlets, five hours old,
    # keeps its best score (less a point an hour past the first slot interval) and goes, where it used to lose the slot to a market check.
    verified(db, "Bybaz exchange says hackers drained $40 million from its hot wallet", now=SLOT, hours=5)
    await decide(db, SLOT + timedelta(minutes=1))
    out = released(db)
    assert [e.kind for e in out] == ["news"] and "Bybaz" in out[0].title


def test_the_brief_says_what_the_market_did_and_explains_negative_funding() -> None:
    lines = [{"tag": "$BTC", "name": "Bitcoin", "emoji": "", "price": 74940.0, "pct": -1.2, "source": "Hyperliquid", "funding": 0.0000125},
             {"tag": "$ETH", "name": "Ethereum", "emoji": "", "price": 2140.0, "pct": -4.3, "source": "Hyperliquid", "funding": -0.00004},
             {"tag": "$SOL", "name": "Solana", "emoji": "", "price": 87.5, "pct": -1.7, "source": "Hyperliquid", "funding": None},
             {"tag": "Gold", "name": "Gold", "emoji": "", "price": 2650.1, "pct": 0.4, "source": "Yahoo Finance"}]
    made = recap.compose(lines, [], "08:00", SLOT)
    assert made is not None and made["headline"] == "Crypto slides, Ethereum down 4.3% in 24 hours"
    assert "Gold 2,650 (+0.4%)" in made["telegram_html"] and "Data: Hyperliquid, Yahoo Finance, 04:00 UTC" in made["telegram_html"]
    assert "Funding on Hyperliquid: Bitcoin +0.0013% an hour, Ethereum -0.0040% an hour." in made["telegram_html"]
    assert "traders betting on a fall are paying those betting on a rise" in made["telegram_html"]
    assert recap.headline(lines[:2] + [{**lines[2], "pct": 0.3}]) == "Crypto slides, Ethereum down 4.3% in 24 hours"
    flat = [{**r, "pct": 0.2} for r in lines]
    assert recap.headline(flat) == "Crypto holds steady over the last 24 hours"


async def test_a_small_price_move_never_takes_a_slot(db: Session) -> None:
    ev = NdEvent(key="liq:BTC:1", kind="market", title="BTC longs liquidated", headline="BTC LONGS LIQUIDATED: $8 MILLION IN AN HOUR", topic="liquidations",
                 evidence="exchange data", category="crypto", virality=58, status="candidate", first_seen=SLOT, facts={"assets": ["LIQ:BTC"]})
    db.add(ev)
    db.commit()
    await decide(db, SLOT + timedelta(minutes=1))
    db.refresh(ev)
    assert ev.status == "candidate" and "a move on its own needs 70" in ev.why
    assert [e.kind for e in released(db)] == ["recap"]


async def test_priority_subjects_rank_first_but_never_lower_the_bar(db: Session) -> None:
    p = events.policy({})
    btc = NdEvent(key="a", kind="news", title="t", headline="Bitcoin miners sell", topic="other", category="crypto", virality=58)
    other = NdEvent(key="b", kind="news", title="t", headline="Some exchange sells", topic="other", category="viral", virality=62)
    assert events.priority(btc, p) and not events.priority(other, p)
    assert sorted([other, btc], key=lambda e: -(e.virality + (p.priority_bonus if events.priority(e, p) else 0)))[0] is btc


# --- delivery --------------------------------------------------------------------------------------------------------


async def test_telegram_and_x_each_deliver_the_market_check_in_their_own_format(db: Session) -> None:
    now = SLOT + timedelta(minutes=1)
    await decide(db, now)
    tg = Tg()
    out = await lane.tick(db, get_settings(), tg, now, quotes={})
    assert out["sent"] == 144 and tg.sent[0]["card"] is False and tg.sent[0]["text"].startswith("<b>Crypto holds steady")
    desk = engine.settings_row(db)
    await engine.deliver_x(db, desk, get_settings(), None, 0, now)
    ev = released(db)[0]
    assert ev.tg_status == "delivered" and ev.tg_message_id == 144 and ev.x_status == "dry_run"
    sch = events.schedule(db, now, events.policy({}))
    assert sch["last_telegram"]["message_id"] == 144 and sch["next_slot_dubai"] == "12:00" and sch["slots"][-1]["state"] == "filled"


async def test_the_old_four_a_day_telegram_limit_doesnt_drop_scheduled_slots(db: Session) -> None:
    s = lane.settings_row(db)
    s.max_per_day = 4
    now = SLOT
    for i in range(4):
        db.add(NdTelegramPost(story_link=f"event:old{i}", title="t", headline="h", text="x", source="s", sources=[], fingerprint=f"f{i}", status="delivered", chat="@c",
                              created_at=now - timedelta(hours=4 * (i + 1)), updated_at=now))
    db.commit()
    assert lane.over_limit(db, s, now, "regular") and lane.over_limit(db, s, now, "slot") is None


# --- never silent ----------------------------------------------------------------------------------------------------


async def test_a_missed_slot_and_a_failed_delivery_alert_the_owner_once(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    told: list[str] = []

    async def notify(db: Session, key: str, text: str, **kw: Any) -> bool:
        if key in told:
            return False
        told.append(key)
        return True

    monkeypatch.setattr("app.alerts.notify_owner", notify)
    p = events.policy({})
    later = SLOT + timedelta(hours=2)  # the 08:00 slot was never filled
    assert await events.alert_slots(db, later, p) == 2 and told == ["newsdesk:slot:2026-11-09T00:00:00+00:00", "newsdesk:slot:2026-11-09T04:00:00+00:00"]
    assert await events.alert_slots(db, later + timedelta(minutes=5), p) == 0
    await decide(db, SLOT + timedelta(hours=4, minutes=1))
    ev = released(db)[0]
    ev.tg_status = "missed"
    db.commit()
    assert await events.alert_slots(db, SLOT + timedelta(hours=5), p) == 1 and told[-1] == f"newsdesk:delivery:{ev.id}:Telegram"
    assert events.schedule(db, SLOT + timedelta(hours=5), p)["failures"][0]["telegram"] == "missed"


async def test_slots_before_the_schedule_started_are_neither_missed_nor_alerted(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    told: list[str] = []

    async def notify(db: Session, key: str, text: str, **kw: Any) -> bool:
        told.append(key)
        return True

    monkeypatch.setattr("app.alerts.notify_owner", notify)
    engine.settings_row(db)
    db.commit()
    first = SLOT + timedelta(hours=2)  # the deploy: the 04:00 and 08:00 Dubai slots were never owed
    assert await events.alert_slots(db, first, events.policy({})) == 0 and told == []
    states = {x["dubai"]: x["state"] for x in events.schedule(db, first, events.policy({}))["slots"]}
    assert states["08:00"] == "before" and states["04:00"] == "before"
    assert await events.alert_slots(db, SLOT + timedelta(hours=6), events.policy({})) == 1 and told == ["newsdesk:slot:2026-11-09T08:00:00+00:00"]  # 12:00 Dubai was owed


async def test_a_recap_that_crashes_never_stops_the_desk(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(*a: Any, **k: Any) -> None:
        raise RuntimeError("Hyperliquid down")

    monkeypatch.setattr(recap, "build", boom)
    out = await decide(db, SLOT + timedelta(minutes=1))
    assert out["released"] is None


# --- feeds -----------------------------------------------------------------------------------------------------------


def test_the_tiered_feeds_are_added_to_the_stored_ones_unless_turned_off(monkeypatch: pytest.MonkeyPatch) -> None:
    extra = [{"name": "CoinDesk", "url": "https://coindesk.test/rss", "tier": 2, "category": "crypto"}]
    monkeypatch.setattr(sources, "EXTRA_FEEDS", extra)
    monkeypatch.setattr(sources, "WORLDMONITOR_FEEDS", [])
    stored = [{"name": "Decrypt", "url": "https://decrypt.test/feed", "tier": 2, "category": "crypto"}]
    assert [f["name"] for f in sources.feeds_for(stored, {})] == ["Decrypt", "CoinDesk"]
    assert [f["name"] for f in sources.feeds_for(stored + extra, {})] == ["Decrypt", "CoinDesk"]
    assert [f["name"] for f in sources.feeds_for(stored, {"extra_feeds": False})] == ["Decrypt"]


async def test_a_stale_or_broken_feed_is_flagged_and_never_stops_the_others(db: Session) -> None:
    now = SLOT
    desk = engine.settings_row(db)
    desk.feeds = [{"name": "Old", "url": "https://old.test/rss", "tier": 1, "category": None}, {"name": "Down", "url": "https://down.test/rss", "tier": 1, "category": None},
                  {"name": "Good", "url": "https://good.test/rss", "tier": 2, "category": "crypto"}]
    db.commit()
    fresh = (now - timedelta(minutes=20)).strftime("%a, %d %b %Y %H:%M:%S +0000")

    def web(request: httpx.Request) -> httpx.Response:
        if "old" in request.url.host:
            return httpx.Response(200, text="<rss><channel><item><title>Fed holds rates</title><link>https://old.test/1</link><pubDate>Thu, 03 Jul 2025 12:36:00 GMT</pubDate></item></channel></rss>")
        if "down" in request.url.host:
            return httpx.Response(503)
        return httpx.Response(200, text=f"<rss><channel><item><title>Bybaz exchange hacked for $40 million</title><link>https://good.test/1</link><pubDate>{fresh}</pubDate></item></channel></rss>")

    async with httpx.AsyncClient(transport=httpx.MockTransport(web)) as client:
        out = await engine.collect(db, desk, client, now)
    assert out["feed_problems"]["https://old.test/rss"].startswith("stale") and out["feed_problems"]["https://down.test/rss"] == "HTTP 503"
    assert out["new_stories"] == 1 and out["feed_health"]["https://good.test/rss"]["new"] == 1


@pytest.mark.asyncio
async def test_an_outage_reported_five_hours_ago_does_not_take_a_slot(db: Session) -> None:
    """Production 2026-11-17 08:00 UTC (12:00 Dubai): "United Airlines says 'widespread' system outage resolved" took the slot 304 min after
    its first report, hours after the outage had ended. An outage is news while it happens, not at a later slot."""
    verified(db, "United Airlines says widespread system outage resolved", now=SLOT, hours=5)
    await decide(db, SLOT + timedelta(minutes=1))
    out = released(db)
    assert not any("United" in (e.title or "") for e in out), [(e.title, e.topic, e.slot, (e.facts or {}).get("importance"), int(events._age(e, SLOT + timedelta(minutes=1))), e.why) for e in out]


@pytest.mark.asyncio
async def test_a_fresh_outage_still_takes_the_slot(db: Session) -> None:
    verified(db, "United Airlines says widespread system outage grounds flights nationwide", now=SLOT, hours=1)
    await decide(db, SLOT + timedelta(minutes=1))
    assert any("United" in (e.title or "") for e in released(db))
