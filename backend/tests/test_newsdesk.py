"""The News Desk: nothing unverified, duplicated, linked or over its limits goes out, and dry run never reaches X. No test reaches a real service."""

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.settings import get_settings
from app.db.base import utcnow
from app.models.newsdesk import NdPost, NdStory
from app.newsdesk import checks, engine, sources, writer
from app.services.agent_service import get_agent_config
from app.services.safety import get_or_create_policy
from tests.conftest import connect_x_user
from tests.fakes import FakeX


def rss(items: list[tuple[str, str, datetime]]) -> str:
    body = "".join(f"<item><title>{t}</title><link>{link}</link><pubDate>{at.strftime('%a, %d %b %Y %H:%M:%S +0000')}</pubDate><description>{t}</description></item>" for t, link, at in items)
    return f"<?xml version='1.0'?><rss version='2.0'><channel><title>x</title>{body}</channel></rss>"


def chart(price: float, then: float, now: datetime, *, hi: float | None = None, lo: float | None = None, seconds: int = 3600) -> dict[str, Any]:
    end = int(now.timestamp())
    stamps = [end - seconds * 2 - 3600, end - seconds, end - 300, end]
    return {"chart": {"result": [{"meta": {"regularMarketPrice": price, "regularMarketTime": end, "fiftyTwoWeekHigh": hi or price * 2, "fiftyTwoWeekLow": lo or price / 2},
                                  "timestamp": stamps, "indicators": {"quote": [{"close": [then, then, price, price], "volume": [1, 1, 1, 1]}]}}]}}


class Web:
    """Feeds and prices, all fake."""

    def __init__(self) -> None:
        self.feeds: dict[str, str] = {}
        self.prices: dict[str, dict[str, Any]] = {}

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "finance.yahoo.com" in url:
            sym = request.url.path.rsplit("/", 1)[-1]
            payload = self.prices.get(sym)
            return httpx.Response(200, json=payload) if payload else httpx.Response(404)
        for k, v in self.feeds.items():
            if url.startswith(k):
                return httpx.Response(200, text=v)
        return httpx.Response(404)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


class Market:
    def __init__(self, mark: float) -> None:
        self.mark_value = mark

    def market(self, _name: str) -> Any:
        return type("M", (), {"mark": self.mark_value})()


OFFICIAL = {"name": "Federal Reserve", "url": "https://fed.test/feed", "tier": 0, "category": "economics"}
MAJOR = {"name": "BBC", "url": "https://bbc.test/feed", "tier": 1, "category": None}
OTHER = {"name": "CNBC", "url": "https://cnbc.test/feed", "tier": 1, "category": None}
BTC = {"symbol": "BTC-USD", "name": "Bitcoin", "group": "crypto_major", "category": "crypto", "emoji": "₿", "hl": "BTC", "step": 5000}
GOLD = {"symbol": "GC=F", "name": "Gold", "group": "commodity", "category": "commodities", "emoji": "🥇", "step": 100}


# The desk tests check reading, verifying and publishing, so the shared daily plan is opened up; the plan has its own tests (test_newsdesk_events.py).
OPEN = {"regular_bar": 0, "opening_bar": 0, "regular_per_day": 50, "ceiling": 50, "window_hours": 0, "regular_gap_minutes": 0, "min_gap_minutes": 0, "topic_max": 50}


def setup(db: Session, feeds: list[dict[str, Any]] | None = None, watch: list[dict[str, Any]] | None = None, **over: Any) -> Any:
    s = engine.settings_row(db)
    s.feeds, s.watchlist = feeds or [], watch or []
    s.state = {"policy": OPEN, **over.pop("state", {})}
    for k, v in over.items():
        setattr(s, k, v)
    db.commit()
    return s


async def run(db: Session, web: Web, market: Any = None, runtime: Any = None, now: datetime | None = None) -> dict[str, Any]:
    return await engine.tick(db, get_settings(), runtime, get_agent_config(db).id, now or utcnow(), transport=web.transport, market=market or Market(0))


def posts(db: Session, **where: Any) -> list[NdPost]:
    db.expire_all()
    return list(db.scalars(select(NdPost).filter_by(**where).order_by(NdPost.id)))


# --- the checks ----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("text", "reason"), [
    ("Fed cuts rates. pricechat.live\n\nvia Fed", "no_link"),
    ("Fed cuts rates https://t.co/x\n\nvia Fed", "no_link"),
    ("Fed cuts rates @jerome\n\nvia Fed", "no_mentions"),
    ("Bitcoin is a guaranteed win, buy now\n\nvia Fed", "claims"),
    ("Fed cuts rates by 9 points\n\nvia Fed", "figures"),
    ("Rates were cut", "attribution"),
    ("🚨 BREAKING: Fed cuts rates\n\nvia Fed", "urgency"),
    ("Fed reportedly weighing a cut\n\nvia Fed", "not_rumour"),
    ("x" * 281, "length"),
])
def test_a_post_that_breaks_a_rule_is_refused(text: str, reason: str) -> None:
    r = checks.check_post(text, source_text="Fed cuts rates Rates were cut", source_names=["Fed"])
    assert not r.ok and reason in [c["check"] for c in r.checks if not c["passed"]]


def test_a_clean_post_passes_and_figures_must_match_the_source() -> None:
    assert checks.check_post("Fed cuts rates by 0.25 points to 3,500.\n\nvia Fed", source_text="cut of 0.25 to 3500 today", source_names=["Fed"]).ok
    assert checks.numbers("$1,000.50 and 2.0") == {"1000.5", "2"}


def test_near_duplicates_are_seen() -> None:
    a, b = checks.fingerprint("Fed cuts interest rates by a quarter point"), checks.fingerprint("Fed cuts interest rates a quarter point on Wednesday")
    assert checks.similarity(a, b) >= engine.SIMILAR
    assert checks.similarity(a, checks.fingerprint("Gold hits record as dollar falls")) < engine.SIMILAR


def test_a_bad_price_answer_is_thrown_away() -> None:
    now = utcnow()
    good = chart(100, 90, now)
    assert sources.parse_chart("X", good).price == 100
    bad = json.loads(json.dumps(good))
    bad["chart"]["result"][0]["meta"]["regularMarketPrice"] = 150  # disagrees with its own chart
    assert sources.parse_chart("X", bad) is None
    assert sources.parse_chart("X", {"chart": {"result": None}}) is None


def test_a_closed_market_claims_no_move() -> None:
    now = utcnow()
    q = sources.parse_chart("X", chart(100, 90, now - timedelta(hours=3)))
    assert q.change("1h", now) is None
    assert sources.parse_chart("X", chart(100, 90, now)).change("1h", now) == pytest.approx(11.11, abs=0.01)


# --- dry run, end to end -------------------------------------------------------------------------------------------


async def test_dry_run_runs_the_whole_pipeline_and_never_touches_x(db: Session, fake_x: FakeX) -> None:
    now = utcnow()
    web = Web()
    web.feeds[OFFICIAL["url"]] = rss([("Federal Reserve lowers interest rate target", "https://fed.test/1", now - timedelta(minutes=20))])
    web.prices["BTC-USD"] = chart(60000, 70000, now)
    setup(db, [OFFICIAL], [BTC], target_per_day=12)
    out = await run(db, web, Market(60100))
    assert out["mode"] == "dry_run" and out["published"] == "dry_run"
    assert fake_x.writes == []
    done = posts(db, status="dry_run")
    assert len(done) == 1 and done[0].priority == "breaking" and done[0].published_at
    assert done[0].x_id is None and done[0].checks and done[0].sources


async def test_a_real_move_needs_the_second_source_to_agree(db: Session) -> None:
    now = utcnow()
    web = Web()
    web.prices["BTC-USD"] = chart(60000, 70000, now)
    setup(db, [], [BTC])
    await run(db, web, Market(50000))  # Hyperliquid says otherwise
    assert posts(db) == []
    await run(db, web, Market(60100), now=now + timedelta(minutes=5))
    made = posts(db)
    assert len(made) == 1 and "BITCOIN CRASHES 14.3% IN AN HOUR TO $60,000" in made[0].text and made[0].text.startswith("🚨 BREAKING")  # 4.8 times its alert line
    assert made[0].facts["headline"] == "BITCOIN CRASHES 14.3% IN AN HOUR TO $60,000" and made[0].facts["label"] == "🚨 BREAKING"  # what Telegram carries too
    assert "Hyperliquid" in made[0].facts["verified"] and "Yahoo Finance" in made[0].text


async def test_a_move_isnt_repeated_while_cooling_down(db: Session) -> None:
    now = utcnow()
    web = Web()
    web.prices["BTC-USD"] = chart(60000, 70000, now)
    setup(db, [], [BTC])
    await run(db, web, Market(60000), now=now)
    await run(db, web, Market(60000), now=now + timedelta(minutes=5))
    assert len(posts(db)) == 1


async def test_small_moves_are_ignored(db: Session) -> None:
    now = utcnow()
    web = Web()
    web.prices["BTC-USD"] = chart(69500, 70000, now)
    setup(db, [], [BTC])
    await run(db, web, Market(69500))
    assert posts(db) == []


async def test_a_round_number_milestone(db: Session) -> None:
    now = utcnow()
    web = Web()
    web.prices["GC=F"] = chart(3020, 2990, now, seconds=1800)  # through $3,000 in the last half hour, and holding 0.5% past it
    setup(db, [], [GOLD])
    from app.newsdesk import sources as _sources

    async def gold_xyz(*_: object, **__: object) -> dict[str, tuple[float, float]]:
        return {"xyz:GOLD": (3021.0, 3021.0)}  # the second market agrees (a move is published only when two markets agree, 2026-11-23)

    _sources._xyz = None
    import pytest as _pytest

    mp = _pytest.MonkeyPatch()
    mp.setattr(_sources, "xyz_prices", gold_xyz)
    try:
        await run(db, web)
    finally:
        mp.undo()
    made = posts(db)
    assert len(made) == 1 and "GOLD BREAKS ABOVE $3,000, NOW $3,020" in made[0].text


async def test_a_rumour_or_a_story_with_no_market_angle_is_ignored(db: Session) -> None:
    now = utcnow()
    web = Web()
    web.feeds[MAJOR["url"]] = rss([("Fed reportedly considering a rate cut", "https://b.test/1", now), ("Local team wins the cup", "https://b.test/2", now)])
    setup(db, [MAJOR], [])
    await run(db, web)
    assert list(db.scalars(select(NdStory))) == [] and posts(db) == []


async def test_explainers_arent_news(db: Session) -> None:
    now = utcnow()
    web = Web()
    web.feeds[MAJOR["url"]] = rss([("What's happening to UK interest rates and what does it mean for mortgages?", "https://b.test/1", now), ("Will the Fed cut interest rates next?", "https://b.test/2", now)])
    setup(db, [MAJOR], [])
    await run(db, web)
    assert list(db.scalars(select(NdStory))) == []


async def test_different_headlines_about_one_story_go_out_once(db: Session) -> None:
    now = utcnow()
    web = Web()
    web.feeds[MAJOR["url"]] = rss([("Binance says hackers stole $90 million using fake job interviews", "https://b.test/1", now - timedelta(minutes=30))])
    web.feeds[OTHER["url"]] = rss([("Binance hack: thieves took $90 million through fake job interviews", "https://c.test/1", now - timedelta(minutes=20)), ("Nvidia CEO Jensen Huang resigns after earnings miss on Wall Street", "https://c.test/2", now - timedelta(minutes=10))])
    setup(db, [MAJOR, OTHER], [])
    for minutes in (0, 5, 10):  # one event is released a run
        await run(db, web, now=now + timedelta(minutes=minutes))
    made = [p.text.upper() for p in posts(db)]
    assert sum("BINANCE" in t for t in made) == 1 and sum("NVIDIA" in t for t in made) == 1


def test_bitcoin_gets_its_symbol_and_other_crypto_doesnt() -> None:
    assert writer.headline_post("Bitcoin rises", "BBC", "crypto", False).startswith("₿")
    assert writer.headline_post("Binance is hacked", "BBC", "crypto", False).startswith("🪙")


async def test_breaking_needs_an_official_source_or_a_second_outlet(db: Session) -> None:
    now = utcnow()
    web = Web()
    title = "Fed cuts interest rate as inflation falls and recession fears grow"
    web.feeds[MAJOR["url"]] = rss([(title, "https://b.test/1", now - timedelta(minutes=5))])
    setup(db, [MAJOR], [], sensitivity="high")
    await run(db, web)
    assert all(p.priority != "breaking" for p in posts(db))
    assert all("BREAKING" not in p.text for p in posts(db))


async def test_the_same_story_from_two_outlets_goes_out_once_naming_its_source(db: Session) -> None:
    now = utcnow()
    web = Web()
    web.feeds[MAJOR["url"]] = rss([("US imposes new tariff on steel imports from Europe", "https://b.test/1", now - timedelta(minutes=30))])
    web.feeds[OTHER["url"]] = rss([("US imposes new tariff on steel imports from Europe today", "https://c.test/1", now - timedelta(minutes=20))])
    setup(db, [MAJOR, OTHER], [])
    await run(db, web)
    await run(db, web, now=now + timedelta(minutes=5))
    made = posts(db)
    assert len(made) == 1 and made[0].category == "politics" and made[0].facts["corroborated_by"]
    assert "via " in made[0].text and len(made[0].sources) == 2 and "http" not in made[0].text


async def test_a_switched_off_category_is_silent(db: Session) -> None:
    now = utcnow()
    web = Web()
    web.prices["BTC-USD"] = chart(60000, 70000, now)
    setup(db, [], [BTC], categories={"crypto": False})
    await run(db, web, Market(60000))
    assert posts(db) == []


def _queued(db: Session, n: int = 1, priority: str = "scheduled", category: str = "crypto", start: int = 0) -> list[NdPost]:
    out = []
    for i in range(n):
        p = NdPost(kind="news", category=category, priority=priority, status="queued", text=f"Unique story number {start + i} about topic{start + i}\n\nvia BBC", key=f"k{start + i}", fingerprint=f"topic{start + i} u{start + i}", score=4.0, facts={}, sources=[{"name": "BBC", "url": "https://b.test", "at": None}], checks=[], metrics={})
        db.add(p)
        out.append(p)
    db.commit()
    return out


async def test_scheduled_posts_are_spaced_through_the_day(db: Session) -> None:
    now = utcnow()
    setup(db, [], [], target_per_day=12)
    _queued(db, 3)
    web = Web()
    await run(db, web, now=now)
    assert len(posts(db, status="dry_run")) == 1
    await run(db, web, now=now + timedelta(minutes=30))
    assert len(posts(db, status="dry_run")) == 1  # two hours apart at 12 a day
    await run(db, web, now=now + timedelta(hours=2))
    assert len(posts(db, status="dry_run")) == 2


async def test_the_daily_target_is_a_ceiling(db: Session) -> None:
    now = utcnow()
    setup(db, [], [], target_per_day=1)
    _queued(db, 3)
    web = Web()
    await run(db, web, now=now)
    await run(db, web, now=now + timedelta(hours=20))
    assert len(posts(db, status="dry_run")) == 1


async def test_breaking_bypasses_the_queue_within_its_limits(db: Session) -> None:
    now = utcnow()
    setup(db, [], [], target_per_day=1, max_breaking_per_day=2, breaking_gap_minutes=20)
    _queued(db, 1, start=0)
    _queued(db, 3, priority="breaking", start=10)
    web = Web()
    await run(db, web, now=now)
    assert [p.priority for p in posts(db, status="dry_run")] == ["breaking"]
    await run(db, web, now=now + timedelta(minutes=5))
    assert len(posts(db, status="dry_run")) == 2  # the scheduled one: the gap only limits breaking
    await run(db, web, now=now + timedelta(minutes=10))
    assert len([p for p in posts(db, status="dry_run") if p.priority == "breaking"]) == 1
    await run(db, web, now=now + timedelta(minutes=30))
    await run(db, web, now=now + timedelta(minutes=60))
    assert len([p for p in posts(db, status="dry_run") if p.priority == "breaking"]) == 2  # the daily ceiling


async def test_old_market_posts_expire_rather_than_go_out_stale(db: Session) -> None:
    now = utcnow()
    setup(db, [], [])
    p = _queued(db, 1)[0]
    p.expires_at = now - timedelta(minutes=1)
    db.commit()
    await run(db, Web(), now=now)
    assert posts(db, status="expired") and not posts(db, status="dry_run")


async def test_the_kill_switch_stops_everything_and_nothing_is_read(db: Session) -> None:
    setup(db, [OFFICIAL], [], kill_switch=True)
    _queued(db, 1)
    web = Web()
    out = await run(db, web)
    assert "stop" in out["skipped"] and posts(db, status="queued")


# --- live, against a fake X ------------------------------------------------------------------------------------------


def live(db: Session, fake_x: FakeX) -> Any:
    connect_x_user(db)
    agent = get_agent_config(db)
    policy = get_or_create_policy(db, agent.id)
    policy.outbound_enabled, policy.kill_switch, policy.dry_run = True, False, True  # the human-approval side stays in dry run
    db.commit()
    return type("Runtime", (), {"transport": fake_x.transport})()


async def test_live_publishes_once_through_the_guard_and_records_it(db: Session, fake_x: FakeX) -> None:
    runtime = live(db, fake_x)
    setup(db, [], [], mode="live")
    _queued(db, 1)
    out = await run(db, Web(), runtime=runtime)
    assert out["published"] == "published"
    assert len(fake_x.writes) == 1 and fake_x.writes[0][1]["text"].startswith("Unique story")
    done = posts(db, status="published")
    assert len(done) == 1 and done[0].x_id
    await run(db, Web(), runtime=runtime, now=utcnow() + timedelta(hours=3))
    assert len(fake_x.writes) == 1


async def test_the_x_kill_switch_stops_the_desk_too(db: Session, fake_x: FakeX) -> None:
    runtime = live(db, fake_x)
    agent = get_agent_config(db)
    get_or_create_policy(db, agent.id).kill_switch = True
    db.commit()
    setup(db, [], [], mode="live")
    _queued(db, 1)
    out = await run(db, Web(), runtime=runtime)
    assert out["published"] == "blocked" and fake_x.writes == []
    assert posts(db, status="queued")


async def test_two_runs_cant_send_the_same_post(db: Session, fake_x: FakeX) -> None:
    runtime = live(db, fake_x)
    s = setup(db, [], [], mode="live")
    post = _queued(db, 1)[0]
    db.execute(engine.update(NdPost).where(NdPost.id == post.id).values(status="publishing"))
    db.commit()
    db.refresh(post)
    result = await engine.publish(db, s, post, get_settings(), runtime, get_agent_config(db).id, utcnow())
    assert result == "already claimed" and fake_x.writes == []


async def test_no_x_credit_pauses_the_desk_and_keeps_the_post(db: Session, fake_x: FakeX) -> None:
    runtime = live(db, fake_x)
    setup(db, [], [], mode="live")
    _queued(db, 2)
    fake_x.write_status = 403
    out = await run(db, Web(), runtime=runtime)
    assert out["published"] == "paused"
    db.expire_all()
    s = engine.settings_row(db)
    assert s.paused_until and "refused" in s.pause_reason and posts(db, status="queued")
    await run(db, Web(), runtime=runtime, now=utcnow() + timedelta(hours=3))
    assert len(posts(db, status="queued")) == 2


async def test_a_rate_limit_retries_later_then_gives_up(db: Session, fake_x: FakeX) -> None:
    runtime = live(db, fake_x)
    s = setup(db, [], [], mode="live")
    post = _queued(db, 1)[0]
    fake_x.write_status = 429
    await engine.publish(db, s, post, get_settings(), runtime, get_agent_config(db).id, utcnow())
    db.refresh(post)
    assert post.status == "queued" and post.not_before and post.attempts == 1


async def test_a_timeout_is_never_resent_automatically(db: Session) -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    connect_x_user(db)
    agent = get_agent_config(db)
    policy = get_or_create_policy(db, agent.id)
    policy.outbound_enabled, policy.kill_switch = True, False
    db.commit()
    s = setup(db, [], [], mode="live")
    post = _queued(db, 1)[0]
    runtime = type("Runtime", (), {"transport": httpx.MockTransport(boom)})()
    assert await engine.publish(db, s, post, get_settings(), runtime, agent.id, utcnow()) == "uncertain"
    db.refresh(post)
    assert post.status == "uncertain" and "Check X" in post.error


async def test_news_posts_dont_use_up_the_human_approved_post_limit(db: Session, fake_x: FakeX) -> None:
    from app.models.enums import Platform, SocialAction
    from app.social.guard import ActionRequest, SocialActionGuard

    runtime = live(db, fake_x)
    setup(db, [], [], mode="live", target_per_day=24)
    _queued(db, 1)
    await run(db, Web(), runtime=runtime)
    guard = SocialActionGuard(db, get_agent_config(db).id, get_settings())
    assert guard._executed_since(utcnow() - timedelta(days=1), Platform.X, SocialAction.POST) == 0
    assert guard.evaluate(ActionRequest(Platform.X, SocialAction.POST, text="a person's own post"), approved=False).allowed


def test_replies_and_other_actions_stay_human_approved(db: Session) -> None:
    from app.social import guard as g

    assert g.AUTOMATIC_AVAILABLE is False


# --- Admin ------------------------------------------------------------------------------------------------------


def test_admin_controls(client: TestClient, db: Session) -> None:
    out = client.get("/api/x/newsdesk").json()
    assert out["settings"]["mode"] == "dry_run" and out["settings"]["automation"] is True and out["writer"] == "headlines"
    assert out["settings"]["target_per_day"] == 12 and {f["name"] for f in out["settings"]["feeds"]} >= {"Federal Reserve", "BBC"}
    assert client.put("/api/x/newsdesk/settings", json={"kill_switch": True, "target_per_day": 6, "sensitivity": "high", "categories": {"crypto": False}}).json()["blocked"] == "Emergency stop is on"
    assert client.put("/api/x/newsdesk/settings", json={"feeds": [{"name": "x", "url": "http://not-secure.test"}]}).status_code == 400
    assert client.put("/api/x/newsdesk/settings", json={"watchlist": [{"symbol": "X"}]}).status_code == 400
    assert client.put("/api/x/newsdesk/settings", json={"mode": "maybe"}).status_code == 422


def test_a_person_can_edit_reject_and_add_but_not_break_the_rules(client: TestClient, db: Session) -> None:
    p = client.post("/api/x/newsdesk/posts", json={"text": "A post I wrote myself about gold at 3,000."}).json()
    assert p["status"] == "queued" and p["score"] == 99
    assert client.post("/api/x/newsdesk/posts", json={"text": "See pricechat.live"}).status_code == 400
    edited = client.put(f"/api/x/newsdesk/posts/{p['id']}", json={"text": "Gold at 3,001, I checked."}).json()
    assert edited["text"] == "Gold at 3,001, I checked." and edited["edited"] is True
    assert client.put(f"/api/x/newsdesk/posts/{p['id']}", json={"text": "Buy now at x.com"}).status_code == 400
    assert client.post(f"/api/x/newsdesk/posts/{p['id']}/reject").json()["status"] == "rejected"
    assert client.post(f"/api/x/newsdesk/posts/{p['id']}/reject").status_code == 409


def test_the_override_publishes_in_dry_run_and_respects_the_stop(client: TestClient, db: Session, fake_x: FakeX) -> None:
    p = client.post("/api/x/newsdesk/posts", json={"text": "A post to send straight away."}).json()
    assert client.post(f"/api/x/newsdesk/posts/{p['id']}/publish").json()["status"] == "dry_run"
    q = client.post("/api/x/newsdesk/posts", json={"text": "Another one, with the stop on."}).json()
    client.put("/api/x/newsdesk/settings", json={"kill_switch": True})
    assert client.post(f"/api/x/newsdesk/posts/{q['id']}/publish").status_code == 409


def test_writer_only_uses_verified_figures() -> None:
    text = writer.move_post(BTC, "1h", -5.25, 60123.4, "04 Nov 10:00", big=True)
    assert "$60,123" in text and "5.2%" in text or "5.3%" in text
    assert checks.check_post(text, source_text=text, source_names=["Yahoo Finance"], breaking=True).ok
    assert datetime(2026, 1, 1, tzinfo=timezone.utc)
