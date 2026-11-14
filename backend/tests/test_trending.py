"""Trending Topics (app/trending). TEST FIXTURES only (tests/trending_fixtures.py): made-up headlines and trends, mocked HTTP. Nothing real is
contacted and nothing is posted: the one live-mode test talks to a mocked X API."""

from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.settings import get_settings
from app.db.base import utcnow
from app.models.newsdesk import NdEvent, NdPost
from app.models.trending import TtPost, TtSignal, TtTopic
from app.services.agent_service import get_agent_config
from app.services.safety import get_or_create_policy
from app.trending import coordinate, engine, gates, learning, rank, sources, writer
from tests.conftest import connect_x_user
from tests.trending_fixtures import Web, google_rss, story

NOW = utcnow().replace(hour=10, minute=10, second=0, microsecond=0)  # two hours from the nearest News Desk slot (UTC 8:00 / 12:00)
HACK = "Exchange Bybaz halts withdrawals after $40 million hack"


@pytest.fixture
def web() -> Web:
    return Web(NOW)


@pytest.fixture(autouse=True)
def alerts_log(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    sent: list[tuple[str, str]] = []
    from app import alerts

    async def notify(db: Any, key: str, text: str, **_: Any) -> bool:
        sent.append((key, text))
        return True

    monkeypatch.setattr(alerts, "notify_owner", notify)
    return sent


async def tick(db: Session, web: Web, now: Any = None, **kw: Any) -> dict[str, Any]:
    return await engine.tick(db, get_settings(), web.runtime, get_agent_config(db).id, now or NOW, transport=web.transport, **kw)


def hack_story(db: Session, web: Web) -> None:
    story(db, HACK, "CoinDesk", 1, NOW, 20)
    story(db, "Bybaz halts withdrawals after a $40 million hack, the exchange says", "The Block", 2, NOW, 15)
    story(db, "Crypto exchange Bybaz pauses withdrawals following $40 million hack", "Reuters", 3, NOW, 10)
    web.google = [{"query": "bybaz", "traffic": "200K+", "news": [{"title": HACK, "url": "https://coindesk.test/1", "source": "CoinDesk"}]}]


def approval_mode(db: Session) -> None:
    s = engine.settings_row(db)
    s.mode, s.auto_confirmed_at = "approval", None
    db.commit()


def newsdesk_live(db: Session, web: Web) -> Any:
    """The News Desk's X lane live, X connected with posting access, outbound on: what lets a trend post reach the (mocked) X."""
    from app.newsdesk import engine as desk

    connect_x_user(db)
    policy = get_or_create_policy(db, get_agent_config(db).id)
    policy.outbound_enabled, policy.kill_switch = True, False
    nd = desk.settings_row(db)
    nd.mode = "live"
    db.commit()
    return web.runtime


def posts(db: Session) -> list[TtPost]:
    db.expire_all()
    return list(db.scalars(select(TtPost).order_by(TtPost.id)))


# --- defaults --------------------------------------------------------------------------------------------------------------


def test_shipped_defaults_automatic_3_a_day_3_hours_apart_following_the_news_desk_x_mode(db: Session) -> None:
    s = engine.settings_row(db)
    assert s.mode == "automatic" and engine.automatic(s) and s.live is True and s.kill_switch is False
    assert s.max_per_day == 3 and s.gap_minutes == 180 and s.hot_score == 75 and s.shared_daily_cap == 20
    assert s.sources["reddit"]["public_json"] is False and s.sources["x_accounts"]["on"] is False and s.sources["x_trends"]["on"] is False  # no new paid reads
    assert coordinate.x_live(db, s) is False  # the News Desk is in dry run (its default): so is Trending


async def test_with_the_news_desk_in_dry_run_an_automatic_hot_topic_is_only_recorded(db: Session, web: Web) -> None:
    s = engine.settings_row(db)
    s.hot_score = 50
    db.commit()
    hack_story(db, web)
    connect_x_user(db)  # X connected and Trending live: the News Desk's dry run still wins
    await tick(db, web)
    [post] = [p for p in posts(db) if "Bybaz" in p.text]
    assert post.status == "dry_run" and "the News Desk's X lane is in dry run" in post.note
    assert web.posted == []


async def test_in_approval_mode_a_hot_topic_gets_a_pending_suggestion_and_nothing_is_posted(db: Session, web: Web) -> None:
    approval_mode(db)
    hack_story(db, web)
    out = await tick(db, web)
    assert out["suggested"] >= 1 and out["published"] is None
    [post] = [p for p in posts(db) if "Bybaz" in p.text]
    assert post.status == "pending" and post.hold_reason is None
    assert "(via CoinDesk)" in post.text or "(via Reuters)" in post.text or "(via The Block)" in post.text
    topic = db.get(TtTopic, post.topic_id)
    assert topic is not None and topic.category == "crypto" and {"news", "google"} <= set(topic.parts["sources"])
    assert not [c for c in web.calls if "api.x.com" in c]  # X never called: not connected, and nothing approved


# --- sources -----------------------------------------------------------------------------------------------------------------


def test_google_trends_rss_is_parsed_with_traffic_and_its_reliable_articles() -> None:
    xml = google_rss([{"query": "nvidia earnings", "traffic": "500K+", "news": [{"title": "Nvidia beats estimates", "url": "https://cnbc.test/n", "source": "CNBC"},
                                                                                {"title": "Nvidia!!", "url": "https://blog.test/n", "source": "Some Blog"}]}])
    items = sources.parse_google(xml, "US")
    trend, a, b = items
    assert trend["engagement"] == 500_000 and trend["reliable"] is False and trend["extra"]["query"] == "nvidia earnings"
    assert a["reliable"] is True and b["reliable"] is False and a["extra"]["group"] == trend["extra"]["group"]
    assert sources.parse_google("<!DOCTYPE x [<!ENTITY a 'b'>]><rss/>", "US") == []  # no entity tricks


async def test_reddit_stays_off_without_oauth_unless_the_owner_allows_public_json(db: Session, web: Web) -> None:
    import httpx

    web.reddit = {"CryptoCurrency": [{"title": "Bybaz withdrawals halted?", "score": 5000, "comments": 900}]}
    async with httpx.AsyncClient(transport=web.transport) as client:
        items, problem, meta = await sources.reddit(client, {"public_json": False, "subreddits": ["CryptoCurrency"]}, get_settings())
        assert items == [] and "needs setup" in (problem or "") and meta["unavailable"] and web.calls == []
        items, problem, meta = await sources.reddit(client, {"public_json": True, "subreddits": ["CryptoCurrency"]}, get_settings())
    assert problem is None and meta["auth"] == "public_json" and items[0]["engagement"] == 5000 + 2 * 900 and items[0]["reliable"] is False
    assert web.calls == ["GET www.reddit.com/r/CryptoCurrency/hot.json"]


async def test_x_trends_not_on_the_plan_degrades_and_is_not_asked_again_for_a_day(db: Session, web: Web, alerts_log: list[Any]) -> None:
    connect_x_user(db)
    s = engine.settings_row(db)
    s.sources = {**s.sources, "x_trends": {"on": True, "woeid": 1}}
    db.commit()
    web.status["api.x.com"] = 403
    await tick(db, web, only={"x_trends"})
    h = engine.settings_row(db).state["health"]["x_trends"]
    assert h["status"] == "unavailable" and h["problem"] == "not available on your X plan (X answered 403)" and h["plan"] is True
    calls = len(web.calls)
    await tick(db, web, NOW + timedelta(hours=2))
    assert len([c for c in web.calls[calls:] if "trends/by/woeid" in c]) == 0  # not due again until tomorrow
    assert not [k for k, _ in alerts_log if "x_trends" in k]  # a plan limit is not a failure: no alert


async def test_x_trends_on_a_plan_that_has_them(db: Session, web: Web) -> None:
    connect_x_user(db)
    s = engine.settings_row(db)
    s.sources = {**s.sources, "x_trends": {"on": True, "woeid": 1}}
    db.commit()
    web.x_trends = [{"trend_name": "#Bybaz", "tweet_count": 120000}]
    await tick(db, web, only={"x_trends"})
    h = engine.settings_row(db).state["health"]["x_trends"]
    assert h["status"] == "ok" and engine.settings_row(db).state["usage"]["cost_usd"]["x_trends"] == 0.01
    assert db.scalar(select(TtSignal).where(TtSignal.source == "x_trends")).title == "Bybaz"


async def test_a_source_failing_three_times_alerts_the_owner_once_then_recovery(db: Session, web: Web, alerts_log: list[Any], monkeypatch: pytest.MonkeyPatch) -> None:
    web.status["trends.google.com"] = 500
    for i in range(3):
        await tick(db, web, NOW + timedelta(minutes=31 * i))
    assert [k for k, _ in alerts_log] == ["trending:source:google"]
    assert engine.settings_row(db).state["health"]["google"]["fails"] == 3
    await tick(db, web, NOW + timedelta(minutes=31 * 4))  # a fourth failure inside 6 h: notify_owner's dedupe (cooldown) is the real one
    del web.status["trends.google.com"]
    web.google = [{"query": "fed rate cut", "news": [{"title": "Fed signals a rate cut", "url": "https://cnbc.test/f", "source": "CNBC"}]}]
    from app import alerts

    monkeypatch.setattr(alerts, "_is_open", lambda db, key: key == "trending:source:google")  # what notify_owner(open_=True) recorded
    await tick(db, web, NOW + timedelta(minutes=31 * 5))
    assert engine.settings_row(db).state["health"]["google"]["status"] == "ok"
    assert [k for k, _ in alerts_log][-1] == "trending:source:google:recovered"


# --- ranking ------------------------------------------------------------------------------------------------------------------


def _sig(i: int, title: str, source: str = "news", outlet: str = "BBC", reliable: bool = True, eng: float = 2.0, minutes: int = 30, **extra: Any) -> rank.Sig:
    at = NOW - timedelta(minutes=minutes)
    return rank.Sig(id=i, source=source, title=title, outlet=outlet, reliable=reliable, engagement=eng, first_seen=at, last_seen=NOW, extra=extra)


def test_ranking_rewards_velocity_breadth_and_relevance_and_drops_what_the_audience_does_not_follow() -> None:
    hot = [_sig(1, HACK, outlet="CoinDesk"), _sig(2, "Bybaz halts withdrawals after a $40 million hack", outlet="Reuters", minutes=20),
           _sig(3, "Crypto exchange Bybaz pauses withdrawals after $40 million hack", outlet="The Block", minutes=10),
           _sig(4, "bybaz", source="google", outlet="Google Trends", reliable=False, eng=200_000, query="bybaz", group="g:bybaz"),
           _sig(5, "Bybaz just froze my withdrawals", source="reddit", outlet="r/CryptoCurrency", reliable=False, eng=6000, minutes=40, comments=800)]
    slow = [_sig(10, "Oil prices edge higher as OPEC meeting nears", outlet="BBC", minutes=60 * 9)]
    sport = [_sig(20, "Lakers beat Celtics in overtime thriller", outlet="BBC")]
    clusters = rank.cluster(hot + slow + sport)
    by = {c.lead.id: c for c in clusters}
    assert 20 not in by  # sport: not for this audience
    hot_c = next(c for c in clusters if any(s.id == 1 for s in c.signals))
    assert {s.id for s in hot_c.signals} == {1, 2, 3, 4, 5}  # the trend, the headlines and the thread are one topic
    hot_score, parts = rank.score(hot_c, NOW)
    slow_score, _ = rank.score(next(c for c in clusters if any(s.id == 10 for s in c.signals)), NOW)
    assert hot_score > slow_score + 20 and hot_c.category == "crypto"
    assert set(parts) >= {"velocity", "engagement", "freshness", "relevance", "discussion", "learned"} and parts["learned"] == 1.0


def test_learned_weights_are_bounded_and_need_enough_posts(db: Session) -> None:
    t = TtTopic(key="t:1", title="x", category="crypto", status="posted", score=80, peak_score=80, parts={"sources": ["news", "reddit"]}, signal_ids=[], sources=[],
                metrics={}, first_seen=NOW, last_seen=NOW)
    db.add(t)
    db.flush()
    for i in range(4):
        db.add(TtPost(topic_id=t.id, category="crypto", status="published", text="t", published_at=NOW - timedelta(days=1), sources=[], checks=[],
                      metrics={"impression_count": 1000, "like_count": 900}))
    db.commit()
    assert learning.weights(db, NOW) == {}  # four posts: not enough to learn from
    for i in range(30):
        db.add(TtPost(topic_id=t.id, category="stocks" if i % 2 else "crypto", status="published", text="t", published_at=NOW - timedelta(days=1), sources=[], checks=[],
                      metrics={"impression_count": 1000, "like_count": 1000 if i % 2 == 0 else 0}))
    db.commit()
    w = learning.weights(db, NOW)
    assert w and all(0.8 <= v <= 1.2 for v in w.values())
    assert w["cat:crypto"] == 1.2 and w["cat:stocks"] == 0.8
    c = rank.Cluster([_sig(1, HACK)], "crypto", 3.0)
    _, parts = rank.score(c, NOW, {"cat:crypto": 5.0, "src:news": 5.0})  # even a corrupted weight can't run away
    assert parts["learned"] == 1.2


# --- the gate --------------------------------------------------------------------------------------------------------------


SRC = f"{HACK} Bybaz halts withdrawals after a $40 million hack, the exchange says"


def gate(text: str, **kw: Any) -> dict[str, Any]:
    return gates.gate(text, source_text=kw.pop("source_text", SRC), source_names=["CoinDesk"], reliable_count=kw.pop("reliable", 1), **kw)


def test_the_gate_passes_a_sourced_tweet_and_holds_invented_claims() -> None:
    ok = gate(f"👀 Crypto's story of the moment:\n\n{HACK} (via CoinDesk)\n\nOverreaction or the real deal?")
    assert ok["ok"], ok["reason"]
    assert "Figures no source gave: 25" in gate(f"{HACK}, with 25% of users hit (via CoinDesk)")["reason"]
    assert "people no source names" in gate(f"{HACK}. Changpeng Zhao is furious (via CoinDesk)")["reason"] or not gate(f"{HACK}. Changpeng Zhao is furious (via CoinDesk)")["ok"]
    assert "accusations" in gate(f"{HACK}. Fraud, plain and simple (via CoinDesk)")["reason"]
    assert "quotations" in gate(f'{HACK} (via CoinDesk): "we will make every user whole"')["reason"]
    assert "Controversy" in gate(f"{HACK}. The backlash is already here (via CoinDesk)")["reason"]
    assert not gate(f"{HACK} (via CoinDesk)", reliable=0)["ok"]  # social signals alone never back a tweet
    assert "trend source" in gate(f"{HACK} is trending everywhere (via CoinDesk)")["reason"]
    assert gate(f"{HACK} is trending (via CoinDesk)", buzz_ok=True)["ok"]
    assert "BREAKING" in gate(f"BREAKING: {HACK} (via CoinDesk)")["reason"]


def test_at_most_one_cashtag() -> None:
    src = "Bitcoin and Ethereum fall as Bybaz halts withdrawals"
    two = gates.gate("$BTC and $ETH slide as Bybaz halts withdrawals (via CoinDesk)", source_text=src + " $BTC $ETH", source_names=["CoinDesk"], reliable_count=1)
    assert not two["ok"] and "2 cashtags" in two["reason"]
    one = writer.with_one_cashtag("Bitcoin and Ethereum fall as Bybaz halts withdrawals", src)
    assert len(gates.cashtags(one)) == 1 and "Bitcoin ($BTC)" in one
    assert gates.gate(f"{one} (via CoinDesk)", source_text=src, source_names=["CoinDesk"], reliable_count=1)["ok"]


def test_the_rules_writer_has_personality_in_the_framing_only_and_passes_the_gate() -> None:
    b = writer.Brief(key="t:7", category="crypto", headline=HACK, outlet="CoinDesk", google=True, reddit_sub="CryptoCurrency", outlets=["CoinDesk", "Reuters", "The Block"],
                     source_text=SRC + " on Google's trending searches a top thread on r/CryptoCurrency 3 outlets")
    seen = set()
    for angle in ("hook", "question", "signal"):
        text, used = writer.rules(b, angle)
        seen.add(text)
        assert len(text) <= 280 and "(via CoinDesk)" in text and HACK in text
        res = gates.gate(text, source_text=b.source_text, source_names=["CoinDesk"], reliable_count=3, buzz_ok=True, discussion_ok=True)
        assert res["ok"], (angle, res["reason"])
    assert len(seen) == 3
    assert writer.rules(b, "question")[0].rstrip().endswith("?")


async def test_a_social_only_topic_is_held_for_lack_of_a_reliable_source(db: Session, web: Web) -> None:
    s = engine.settings_row(db)
    s.sources = {**s.sources, "news": {"on": False}, "reddit": {**s.sources["reddit"], "public_json": True, "subreddits": ["CryptoCurrency"]}}
    s.suggest_score = 10
    db.commit()
    web.reddit = {"CryptoCurrency": [{"title": "Rumour: Bybaz insolvent, Bitcoin to crash", "score": 30000, "comments": 5000}]}
    await tick(db, web)
    [post] = posts(db)
    assert post.status == "held" and post.hold_reason.startswith("No reliable source")
    with pytest.raises(ValueError):
        await engine.approve(db, get_settings(), web.runtime, get_agent_config(db).id, post, NOW)


# --- News Desk coordination -----------------------------------------------------------------------------------------------


async def test_a_story_the_news_desk_already_posted_is_held_as_covered(db: Session, web: Web) -> None:
    db.add(NdPost(kind="news", category="crypto", priority="scheduled", status="dry_run", text=f"{HACK}\n\nSource: CoinDesk", key="event:9", fingerprint="",
                  facts={"title": HACK, "headline": HACK.upper()}, sources=[], checks=[], metrics={}, published_at=NOW - timedelta(hours=3)))
    db.commit()
    hack_story(db, web)
    await tick(db, web)
    [post] = [p for p in posts(db) if "Bybaz" in p.text or "Bybaz" in (p.hold_reason or "")]
    assert post.status == "held" and post.hold_reason.startswith("covered by the News Desk")


async def test_the_news_desk_skips_an_event_trending_already_posted(db: Session) -> None:
    from app.newsdesk import engine as desk

    db.add(TtPost(topic_id=1, category="crypto", status="dry_run", text=f"{HACK} (via CoinDesk)", sources=[{"name": "CoinDesk", "title": HACK, "url": "https://c.test/1"}],
                  checks=[], metrics={}, published_at=utcnow() - timedelta(hours=1)))
    db.commit()
    ev = NdEvent(key="story:x", kind="news", title=HACK, headline=HACK.upper(), category="crypto", sources=[], facts={}, status="released")
    assert "Trending Topics already posted this story" in desk._trend_covered(db, ev, utcnow())
    other = NdEvent(key="story:y", kind="news", title="Fed holds rates steady at 4.5%", headline="FED HOLDS", category="economics", sources=[], facts={}, status="released")
    assert desk._trend_covered(db, other, utcnow()) == ""


def test_spacing_daily_limits_and_the_shared_cap(db: Session) -> None:
    s = engine.settings_row(db)
    db.commit()
    assert coordinate.spacing_problem(db, s, NOW) is None and coordinate.cap_problem(db, s, NOW) is None
    db.add(NdPost(kind="news", category="crypto", priority="scheduled", status="dry_run", text="x", key="k", fingerprint="", facts={}, sources=[], checks=[], metrics={},
                  published_at=NOW - timedelta(minutes=10)))
    db.commit()
    assert "the News Desk posted 10 min ago" in coordinate.spacing_problem(db, s, NOW)
    assert "News Desk slot" in coordinate.spacing_problem(db, s, NOW.replace(hour=11, minute=50) + timedelta(hours=0))
    db.add(TtPost(topic_id=1, category="crypto", status="dry_run", text="t", sources=[], checks=[], metrics={}, published_at=NOW - timedelta(minutes=60)))
    db.commit()
    assert "last trend post was 60 min ago" in coordinate.spacing_problem(db, s, NOW)
    for i in range(3):
        db.add(TtPost(topic_id=1, category="crypto", status="dry_run", text="t", sources=[], checks=[], metrics={}, published_at=NOW - timedelta(hours=3 + i)))
    db.commit()
    assert "daily limit reached (4/3" in coordinate.cap_problem(db, s, NOW)
    s.max_per_day, s.shared_daily_cap = 10, 5
    db.commit()
    assert "shared X cap is reached (5/5" in coordinate.cap_problem(db, s, NOW)
    from app.newsdesk import engine as desk

    desk.settings_row(db).mode = "live"  # live counts only what really went to X
    db.commit()
    assert coordinate.cap_problem(db, s, NOW) is None


async def test_the_news_desk_emergency_stop_stops_trend_posts(db: Session, web: Web) -> None:
    from app.newsdesk import engine as desk
    from app.newsdesk import network

    runtime = newsdesk_live(db, web)
    network.configure(db, emergency=True, now=NOW)
    s = engine.settings_row(db)
    s.hot_score = 50
    db.commit()
    hack_story(db, web)
    await engine.tick(db, get_settings(), runtime, get_agent_config(db).id, NOW, transport=web.transport)
    [post] = [p for p in posts(db) if "Bybaz" in p.text]
    assert post.status == "queued" and "paused" in post.note and web.posted == []  # automatic, live, hot: and still nothing goes out
    desk.settings_row(db).kill_switch = True  # the News Desk's own emergency stop, after the pause is lifted
    network.configure(db, emergency=False, now=NOW)
    await engine.tick(db, get_settings(), runtime, get_agent_config(db).id, NOW + timedelta(minutes=10), transport=web.transport)
    db.refresh(post)
    assert post.status == "queued" and "emergency stop" in post.note and web.posted == []
    desk.settings_row(db).kill_switch = False
    db.commit()
    await engine.tick(db, get_settings(), runtime, get_agent_config(db).id, NOW + timedelta(minutes=20), transport=web.transport)
    db.refresh(post)
    assert post.status == "published" and len(web.posted) == 1


async def test_trendings_own_stop_button_stops_its_posts(client: TestClient, db: Session, web: Web) -> None:
    runtime = newsdesk_live(db, web)
    out = client.put("/api/x/trending/settings", json={"kill_switch": True, "hot_score": 50}).json()
    assert out["settings"]["kill_switch"] is True and "stop is on" in out["coordination"]["desk_blocked"]
    hack_story(db, web)
    await engine.tick(db, get_settings(), runtime, get_agent_config(db).id, NOW, transport=web.transport)
    [post] = [p for p in posts(db) if "Bybaz" in p.text]
    assert post.status == "queued" and post.note == "Trending Topics' stop is on" and web.posted == []
    client.put("/api/x/trending/settings", json={"kill_switch": False})
    await engine.tick(db, get_settings(), runtime, get_agent_config(db).id, NOW + timedelta(minutes=10), transport=web.transport)
    db.refresh(post)
    assert post.status == "published" and len(web.posted) == 1


# --- approval and automatic -----------------------------------------------------------------------------------------------


async def test_approving_in_dry_run_records_and_never_calls_x(db: Session, web: Web) -> None:
    approval_mode(db)
    hack_story(db, web)
    await tick(db, web)
    [post] = [p for p in posts(db) if p.status == "pending"]
    assert await engine.approve(db, get_settings(), web.runtime, get_agent_config(db).id, post, NOW) == "dry_run"
    db.refresh(post)
    assert post.status == "dry_run" and post.published_at is not None and web.posted == []
    assert db.get(TtTopic, post.topic_id).status == "posted"
    await tick(db, web, NOW + timedelta(minutes=20))  # the same topic is never suggested twice in its cooldown
    assert len([p for p in posts(db) if "Bybaz" in p.text]) == 1


async def test_approving_in_live_mode_posts_through_the_guard_to_a_fake_x(db: Session, web: Web) -> None:
    newsdesk_live(db, web)
    approval_mode(db)
    hack_story(db, web)
    await tick(db, web)
    [post] = [p for p in posts(db) if p.status == "pending"]
    assert web.posted == []  # approval mode: nothing until the owner approves
    assert await engine.approve(db, get_settings(), web.runtime, get_agent_config(db).id, post, NOW) == "published"
    db.refresh(post)
    assert post.status == "published" and post.x_id == "1990000000000000777" and len(web.posted) == 1
    assert b"Bybaz" in web.posted[0]


async def test_automatic_mode_needs_explicit_confirmation_and_then_publishes_hot_topics(client: TestClient, db: Session, web: Web) -> None:
    assert client.put("/api/x/trending/settings", json={"mode": "approval"}).json()["settings"]["automatic"] is False
    assert client.put("/api/x/trending/settings", json={"mode": "automatic"}).status_code == 400
    assert client.get("/api/x/trending").json()["settings"]["automatic"] is False
    assert client.put("/api/x/trending/settings", json={"live": False}).json()["settings"]["live"] is False
    assert client.put("/api/x/trending/settings", json={"live": True}).status_code == 400  # back on needs a confirmation
    out = client.put("/api/x/trending/settings", json={"mode": "automatic", "confirm_automatic": True, "hot_score": 50}).json()
    assert out["settings"]["automatic"] is True and out["settings"]["live"] is False
    hack_story(db, web)
    await tick(db, web)
    [post] = [p for p in posts(db) if "Bybaz" in p.text]
    assert post.status == "dry_run" and web.posted == [] and "own dry-run switch" in post.note  # automatic, but Trending's dry run
    assert client.put("/api/x/trending/settings", json={"mode": "approval"}).json()["settings"]["automatic"] is False


async def test_a_topic_below_the_hot_line_waits_for_approval_even_in_automatic(db: Session, web: Web) -> None:
    s = engine.settings_row(db)
    s.mode, s.auto_confirmed_at, s.hot_score = "automatic", NOW, 99
    db.commit()
    hack_story(db, web)
    await tick(db, web)
    assert [p.status for p in posts(db) if "Bybaz" in p.text] == ["pending"]


def test_admin_overview_and_editing_through_the_gate(client: TestClient, db: Session) -> None:
    t = TtTopic(key="t:1", title=HACK, category="crypto", status="active", score=60, peak_score=60, parts={}, signal_ids=[], sources=[], metrics={},
                first_seen=NOW, last_seen=NOW)
    db.add(t)
    db.flush()
    approval_mode(db)
    p = TtPost(topic_id=t.id, category="crypto", status="pending", text=f"{HACK} (via CoinDesk)", sources=[{"name": "CoinDesk", "title": HACK, "url": "https://c.test"}],
               source_text=SRC, checks=[], metrics={}, expires_at=utcnow() + timedelta(hours=1))
    db.add(p)
    db.commit()
    out = client.get("/api/x/trending").json()
    assert out["topics"][0]["post"]["id"] == p.id and out["queue"][0]["id"] == p.id and out["writer"] == "rules"
    assert set(out["health"]) == set() and "coordination" in out and out["settings"]["mode"] == "approval"
    bad = client.put(f"/api/x/trending/posts/{p.id}", json={"text": f"{HACK}, 90% of users wiped out (via CoinDesk)"})
    assert bad.status_code == 400 and "Figures no source gave" in bad.json()["detail"]
    good = client.put(f"/api/x/trending/posts/{p.id}", json={"text": f"Not great: {HACK} (via CoinDesk)"})
    assert good.status_code == 200 and good.json()["edited"] and good.json()["writer"] == "owner"
    assert client.post(f"/api/x/trending/posts/{p.id}/reject").json()["status"] == "rejected"
    assert client.put("/api/x/trending/settings", json={"sources": {"reddit": {"evil": 1}}}).status_code == 400


# --- the real scheduler path and monitoring ------------------------------------------------------------------------------


async def test_the_scheduler_job_runs_the_tick_and_posts_through_a_fake_x(client: TestClient, db: Session, web: Web, monkeypatch: pytest.MonkeyPatch) -> None:
    """newsdesk.trending as the scheduler runs it (build_jobs -> Scheduler.run_job): sources and X are mocked, nothing real is called."""
    from app.db.session import SessionLocal
    from app.jobs import build_jobs
    from app.models.routing import ScheduledJob
    from app.scheduler.scheduler import Scheduler

    newsdesk_live(db, web)
    s = engine.settings_row(db)
    s.hot_score = 50
    db.commit()
    hack_story(db, web)
    runtime = client.app.state.runtime
    monkeypatch.setattr(runtime.x, "transport", web.transport, raising=False)
    monkeypatch.setattr(engine, "HTTP_TRANSPORT", web.transport)
    monkeypatch.setattr(engine, "utcnow", lambda: NOW)
    spec = next(j for j in build_jobs(runtime) if j.name == "newsdesk.trending")
    assert spec.name.startswith("newsdesk.")  # production's SCHEDULER_ONLY_JOBS lists "newsdesk.": it runs there with no env change
    sch = Scheduler(SessionLocal, [spec])
    sch.sync_jobs()
    out = await sch.run_job("newsdesk.trending")
    assert out.status == "success", out.error
    assert out.result["published"] == "published" and len(web.posted) == 1
    row = db.get(ScheduledJob, "newsdesk.trending")
    db.refresh(row)
    assert row.last_status == "success"
    out2 = await sch.run_job("newsdesk.trending")  # the next run: one story, one tweet; and 3 hours between trend posts
    assert out2.status == "success" and len(web.posted) == 1


def test_health_check_reports_trending(db: Session) -> None:
    from app import observability

    class Sched:
        _loop_task = object()
        specs = {"newsdesk.trending": object()}
        disabled: set[str] = set()

    s = engine.settings_row(db)
    s.state = {"last_run": {"at": utcnow().isoformat()}, "health": {"google": {"name": "Google Trends", "status": "error", "fails": 4}}}
    db.commit()
    c = observability.trending_check(db, Sched(), utcnow())
    assert c["status"] == "warn" and "Google Trends" in c["detail"]
    s.state = {"last_run": {"at": utcnow().isoformat()}, "health": {}}
    db.commit()
    assert observability.trending_check(db, Sched(), utcnow())["status"] == "ok"
    assert observability.trending_check(db, None, utcnow())["status"] == "off"


def test_performance_report_per_category(db: Session) -> None:
    for i, cat in enumerate(["crypto", "crypto", "stocks"]):
        db.add(TtPost(topic_id=1, category=cat, status="published", text="t", published_at=NOW - timedelta(hours=5), sources=[], checks=[],
                      metrics={"impression_count": 1000, "like_count": 10 * (i + 1), "retweet_count": 2, "reply_count": 1}))
    db.commit()
    r = learning.report(db, NOW)
    assert r["per_category"]["crypto"] == {"posts": 2, "impressions": 2000, "likes": 30, "reposts": 4, "replies": 2, "rate": r["per_category"]["crypto"]["rate"]}
    assert r["totals"]["like_count"] == 60 and not r["learning"]


def test_no_jokes_or_questions_on_deaths() -> None:
    b = writer.Brief(key="t:9", category="geopolitics", headline="Airstrike on a camp kills 12, officials say", outlet="BBC", source_text="Airstrike on a camp kills 12, officials say")
    for angle in ("hook", "question", "signal"):
        text, used = writer.rules(b, angle)
        assert used == "somber" and "?" not in text and text.startswith("🌍 Worth knowing today:")
