"""Trending Topics (app/trending). TEST FIXTURES only (tests/trending_fixtures.py): made-up headlines and trends, mocked HTTP. Nothing real is
contacted and nothing is posted: the one live-mode test talks to a mocked X API."""

import re
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
from app.trending import coordinate, engine, gates, learning, rank, sensitive, sources, writer
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
    assert any(o in post.text for o in ("CoinDesk", "Reuters", "The Block")) and "?" not in post.text  # credited; no canned question
    assert "Searches for Bybaz have topped 200K on Google in the US." in post.text  # the measured interest, plainly
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
           _sig(6, "Bybaz withdrawals frozen after $40 million hack", source="google", outlet="Some Blog", reliable=False, eng=0, group="g:bybaz", kind="article",
                query="bybaz"),
           _sig(5, "Bybaz just froze my withdrawals", source="reddit", outlet="r/CryptoCurrency", reliable=False, eng=6000, minutes=40, comments=800)]
    slow = [_sig(10, "Oil prices edge higher as OPEC meeting nears", outlet="BBC", minutes=60 * 9)]
    sport = [_sig(20, "Lakers beat Celtics in overtime thriller", outlet="BBC")]
    clusters = rank.cluster(hot + slow + sport)
    by = {c.lead.id: c for c in clusters}
    assert 20 not in by  # sport: not for this audience
    hot_c = next(c for c in clusters if any(s.id == 1 for s in c.signals))
    assert {s.id for s in hot_c.signals} == {1, 2, 3, 4, 5, 6}  # the trend (through the article Google links it to), the headlines, the thread
    hot_score, parts = rank.score(hot_c, NOW)
    slow_score, _ = rank.score(next(c for c in clusters if any(s.id == 10 for s in c.signals)), NOW)
    assert hot_score > slow_score + 20 and hot_c.category == "crypto"
    assert set(parts) >= {"velocity", "engagement", "freshness", "relevance", "discussion", "learned", "growth"} and parts["learned"] == 1.0
    assert parts["growth"]["measured"] and "google" in parts["growth"]["evidence"]


def _seen_sig(i: int, title: str, outlet: str, first_seen_min: int, published_min: int | None = None, **kw: Any) -> rank.Sig:
    """A signal first seen by our scan first_seen_min minutes before NOW, published published_min before NOW."""
    seen = NOW - timedelta(minutes=first_seen_min)
    pub = NOW - timedelta(minutes=published_min if published_min is not None else first_seen_min)
    return rank.Sig(id=i, source=kw.pop("source", "news"), title=title, outlet=outlet, reliable=kw.pop("reliable", True), engagement=kw.pop("eng", 2.0),
                    first_seen=seen, last_seen=NOW, published_at=pub, history=kw.pop("history", []), extra=kw)


ZEN = ["Zentrix agrees $12 billion takeover of crypto exchange Lumora", "Zentrix to buy crypto exchange Lumora in $12 billion takeover",
       "Crypto exchange Lumora accepts $12 billion Zentrix takeover", "Zentrix wins Lumora takeover, a $12 billion crypto exchange deal",
       "Lumora board backs $12 billion Zentrix takeover of the crypto exchange"]
OUTLETS = ["Reuters", "Bloomberg", "CNBC", "Financial Times", "CoinDesk", "The Block", "BBC", "Axios", "Decrypt", "Fortune"]


def test_many_articles_but_flat_interest_stays_below_the_automatic_line() -> None:
    """Ten outlets on one story, spread over twelve hours, nothing new since our scans first saw them, no search interest: coverage, not growth."""
    sigs = [_seen_sig(i, ZEN[i % 5], OUTLETS[i], first_seen_min=200, published_min=60 + 70 * i) for i in range(10)]
    [c] = rank.cluster(sigs)
    assert len(c.signals) == 10  # same-event coverage merges
    sc, parts = rank.score(c, NOW)
    assert sc < 75 and sc <= rank.NO_GROWTH_CAP and parts["growth"]["measured"] is False and parts["growth"]["evidence"] == []
    # even at its freshest (all ten published in the last two hours, a burst), coverage alone can't reach the line
    burst = [_seen_sig(i, ZEN[i % 5], OUTLETS[i], first_seen_min=0, published_min=5 + 10 * i) for i in range(10)]
    [b] = rank.cluster(burst)
    sc2, p2 = rank.score(b, NOW)
    assert sc2 <= rank.NO_GROWTH_CAP < 75 and p2["growth"]["measured"] is False


def test_interest_growing_across_scans_raises_the_score_past_the_line() -> None:
    q = {"query": "zentrix lumora", "group": "g:zl", "geo": "US"}
    # scan 1, an hour ago: two outlets, Google at 20K+
    early = [_seen_sig(1, ZEN[0], "Reuters", 60), _seen_sig(2, ZEN[1], "Bloomberg", 60),
             _seen_sig(3, "zentrix lumora", "Google Trends", 60, source="google", reliable=False, eng=20_000, history=[[(NOW - timedelta(minutes=60)).isoformat(), 20_000]], **q)]
    then = NOW - timedelta(minutes=60)
    [c1] = rank.cluster(early)
    s1, p1 = rank.score(c1, then)
    # scan 2, now: four more outlets, Google at 200K+ (the same trend's history across the two scans)
    later = early[:2] + [_seen_sig(3, "zentrix lumora", "Google Trends", 60, source="google", reliable=False, eng=200_000,
                                   history=[[then.isoformat(), 20_000], [NOW.isoformat(), 200_000]], **q)]
    later += [_seen_sig(10 + i, ZEN[(i + 2) % 5], OUTLETS[2 + i], 0, published_min=10 + 5 * i) for i in range(4)]
    [c2] = rank.cluster(later)
    s2, p2 = rank.score(c2, NOW)
    g = p2["growth"]
    assert s2 > s1 + 10 and s2 >= 75, (s1, s2, p2)
    assert {"scans", "google"} <= set(g["evidence"]) and g["outlets_before"] == 2 and g["outlets_now"] == 6 and g["google_up"]
    assert g["google_from"] == 20_000 and g["google_to"] == 200_000


NVIDIA = ["Nvidia unveils Rubin chips at its GTC developer conference", "Nvidia shares slide as China opens antitrust probe",
          "Nvidia CEO Jensen Huang meets Trump at the White House", "Nvidia CEO Jensen Huang says AI demand is still rising",
          "Nvidia to invest $5 billion in Intel", "Singapore files 41 charges in Nvidia-linked server case"]


def test_unrelated_stories_sharing_a_big_name_do_not_merge() -> None:
    sigs = [_seen_sig(i, t, OUTLETS[i], 30) for i, t in enumerate(NVIDIA)]
    sigs.append(_seen_sig(20, "nvidia", "Google Trends", 30, source="google", reliable=False, eng=500_000, query="nvidia", group="g:nv"))
    sigs.append(_seen_sig(21, "Nvidia to invest $5 billion in chipmaker Intel", "Some Blog", 30, source="google", reliable=False, eng=0,
                          query="nvidia", group="g:nv", kind="article"))
    clusters = rank.cluster(sigs)
    sizes = sorted(len(c.signals) for c in clusters)
    assert len(clusters) == 6, [[s.title for s in c.signals] for c in clusters]
    invest = next(c for c in clusters if any(s.id == 4 for s in c.signals))
    assert {s.id for s in invest.signals} == {4, 20, 21}  # the one-word trend joins only through the article Google linked to it
    assert sizes == [1, 1, 1, 1, 1, 3]
    # a cluster's seed decides, not any member: a story that matches a later member but not the seed stays out
    chain = [_seen_sig(1, "Bybaz halts withdrawals after $40 million hack", "Reuters", 30),
             _seen_sig(2, "Bybaz hack: $40 million drained, withdrawals halted as Qorvex audit questioned", "CoinDesk", 30, eng=1.0),
             _seen_sig(3, "Qorvex audit of crypto exchange Lumora questioned by regulators", "BBC", 30, eng=0.5)]
    assert [len(c.signals) for c in rank.cluster(chain)] == [2, 1]
    # and a time window: the same words twenty hours apart are two stories
    apart = [_seen_sig(1, ZEN[0], "Reuters", 30), _seen_sig(2, ZEN[1], "Bloomberg", 30, published_min=20 * 60)]
    assert len(rank.cluster(apart)) == 2


def test_same_event_coverage_still_merges() -> None:
    donbas = ["Zelenskyy Says US Floats NATO Membership in Deal for Donbas", "US floats Nato membership for Donbas withdrawal deal, Zelenskyy says",
              "U.S. floated idea of Ukraine ceding Donbas for NATO membership, Zelenskyy says"]
    hack = [HACK, "Bybaz halts withdrawals after a $40 million hack, the exchange says", "Crypto exchange Bybaz pauses withdrawals following $40 million hack"]
    sigs = [_seen_sig(i, t, OUTLETS[i], 30) for i, t in enumerate(donbas)] + [_seen_sig(10 + i, t, OUTLETS[3 + i], 30) for i, t in enumerate(hack)]
    assert sorted(len(c.signals) for c in rank.cluster(sigs)) == [3, 3]
    assert rank.related("Bitcoin breaks below $69,000", "Bitcoin tumbles below $69,000 as ETF outflows grow")
    assert not rank.related("Nvidia CEO Jensen Huang meets Trump", "Nvidia CEO Jensen Huang says AI demand is still rising")


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
    ok = gate(f"{HACK}, CoinDesk reports.\n\nThe exchange says it halted withdrawals after a $40 million hack.")
    assert ok["ok"], ok["reason"]
    assert "Figures no source gave: 25" in gate(f"{HACK}, with 25% of users hit (via CoinDesk)")["reason"]
    assert "people no source names" in gate(f"{HACK}. Changpeng Zhao is furious (via CoinDesk)")["reason"] or not gate(f"{HACK}. Changpeng Zhao is furious (via CoinDesk)")["ok"]
    assert any("accusations" in c["detail"] for c in gate(f"{HACK}. Fraud, plain and simple (via CoinDesk)")["checks"] if not c["passed"])
    assert "quotations" in gate(f'{HACK} (via CoinDesk): "we will make every user whole"')["reason"]
    assert "Controversy" in gate(f"{HACK}. The backlash is already here (via CoinDesk)")["reason"]
    assert not gate(f"{HACK} (via CoinDesk)", reliable=0)["ok"]  # social signals alone never back a tweet
    assert "trend source" in gate(f"{HACK} is trending everywhere (via CoinDesk)")["reason"]
    assert gate(f"{HACK} is trending (via CoinDesk)", buzz_ok=True)["ok"]
    assert "BREAKING" in gate(f"BREAKING: {HACK} (via CoinDesk)")["reason"]


RETIRED = ["👀 Crypto's story of the moment:", "🪙 Worth a minute of your crypto scroll:", "⚡ On the crypto radar right now:", "🌍 The world story with market stakes:",
           "🧠 One for the crypto crowd:", "📈 On the stock market radar:", "🌐 Today's big picture:"]
BAIT = ["Does this change your view?", "Who comes out ahead here?", "What happens next?", "Overreaction or the real deal?", "Game changer or hype?",
        "Thoughts?", "What's the market missing?", "Retweet if you agree."]


def test_the_gate_holds_template_lines_engagement_bait_and_generic_questions() -> None:
    for hook in RETIRED:
        r = gate(f"{hook}\n\n{HACK} (via CoinDesk)")
        assert not r["ok"] and "template line or engagement bait" in r["reason"], hook
    for q in BAIT:
        assert not gate(f"{HACK} (via CoinDesk)\n\n{q}")["ok"], q
    assert not gate(f"{HACK} (via CoinDesk)\n\nIs this good?")["ok"]  # generic, not tied to a stated fact
    assert not gate(f"{HACK} (via CoinDesk). Will Bybaz reopen withdrawals? Will the hack spread?")["ok"]  # one question at most
    specific = gate(f"{HACK} (via CoinDesk)\n\nWill Bybaz reopen withdrawals this week?")
    assert specific["ok"], specific["reason"]  # rare, specific, neutral, tied to the stated fact: allowed


def _brief(**kw: Any) -> writer.Brief:
    b = writer.Brief(key=kw.pop("key", "t:7"), category=kw.pop("category", "crypto"), headline=kw.pop("headline", HACK), outlet=kw.pop("outlet", "CoinDesk"), **kw)
    b.source_text = " ".join([b.headline] + [h for _, h in b.others]) + " " + " ".join(writer.measured_lines(b))
    return b


def test_the_rules_writer_is_natural_varied_sourced_and_asks_nothing() -> None:
    others = [("Reuters", "Bybaz says 12,000 accounts were affected by the $40 million hack")]
    seen = set()
    for i in range(12):
        b = _brief(key=f"t:{i}", others=others, outlets=["CoinDesk", "Reuters"])
        text, angle = writer.rules(b)
        seen.add(text.split("\n\n")[0].replace(HACK, "H"))
        assert len(text) <= 280 and "CoinDesk" in text and HACK in text and "?" not in text and angle == "detail"
        assert "12,000 accounts" in text and "Reuters" in text  # the second sentence: a concrete figure from another reliable outlet
        assert not gates.EMOJI.search(text) and not gates.BAIT.search(text)
        res = gates.gate(text, source_text=b.source_text, source_names=["CoinDesk"], reliable_count=2)
        assert res["ok"], (text, res["reason"])
    assert len(seen) >= 2  # the opening varies by topic ("(via CoinDesk)", "CoinDesk reports.", "CoinDesk:")
    # the measured interest, only when Google gave a traffic figure
    g = _brief(google=True, google_query="bybaz", google_geo="US", google_traffic="500K+", google_to=500_000)
    text, angle = writer.rules(g)
    assert angle == "interest" and text.endswith("Searches for Bybaz have topped 500K on Google in the US.")
    assert gates.gate(text, source_text=g.source_text, source_names=["CoinDesk"], reliable_count=1, buzz_ok=True)["ok"]
    rising = _brief(google=True, google_query="bybaz", google_geo="US", google_from=20_000, google_to=200_000, google_minutes=130)
    assert writer.rules(rising)[0].endswith("Google searches for Bybaz in the US went from 20K+ to 200K+ in about 2 hours.")
    none = _brief()
    assert writer.rules(none) == (writer.opener(none), "plain") and "Searches" not in writer.rules(none)[0]
    cov = _brief(outlets=["CoinDesk", "Reuters", "The Block", "AP"])
    assert writer.rules(cov)[0].endswith("Reuters, The Block and AP have it too.")


def test_the_model_writer_gets_the_same_constraints() -> None:
    b = _brief(others=[("Reuters", "Bybaz says 12,000 accounts were affected")], google=True, google_query="bybaz", google_geo="US", google_to=200_000)
    system, user = writer.prompt(b)
    for must in ("no engagement bait", "By default ask no question", "No hook or teaser lines", "must come from the FACTS", "untrusted text"):
        assert must in system
    assert "Headline (Reuters): Bybaz says 12,000 accounts were affected" in user and "Measured by Price Chat: Searches for Bybaz have topped 200K" in user
    s = _brief(headline="Airstrike on a camp kills 12, officials say", outlet="BBC", category="geopolitics")
    s.sensitive = sensitive.classify(s.headline)
    system, _ = writer.prompt(s)
    assert "SENSITIVE" in system and "No question, no emoji" in system


def test_at_most_one_cashtag() -> None:
    src = "Bitcoin and Ethereum fall as Bybaz halts withdrawals"
    two = gates.gate("$BTC and $ETH slide as Bybaz halts withdrawals (via CoinDesk)", source_text=src + " $BTC $ETH", source_names=["CoinDesk"], reliable_count=1)
    assert not two["ok"] and "2 cashtags" in two["reason"]
    one = writer.with_one_cashtag("Bitcoin and Ethereum fall as Bybaz halts withdrawals", src)
    assert len(gates.cashtags(one)) == 1 and "Bitcoin ($BTC)" in one
    assert gates.gate(f"{one} (via CoinDesk)", source_text=src, source_names=["CoinDesk"], reliable_count=1)["ok"]


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
    import app.scheduler.scheduler as scheduler_module

    monkeypatch.setattr(scheduler_module, "utcnow", lambda: NOW)  # the job's ctx.now: fixed, so News Desk slot spacing never depends on the wall clock
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


# --- sensitive subjects (owner, 2026-11-14) ---------------------------------------------------------------------------------------

SENSITIVE_CASES = [
    ("allegation", "approval", "Senator John Smith accused of taking bribes, prosecutors say"),
    ("crime", "approval", "Gunman opens fire in a shopping centre shooting, police say"),
    ("terrorism", "approval", "Islamic State claims responsibility for checkpoint bombing"),
    ("sexual", "approval", "Film studio faces sexual misconduct claims from former staff"),
    ("suicide", "approval", "Rise in suicide rates among farmers, study says"),
    ("minors", "approval", "School bus crash injures 12 children, officials say"),
    ("conflict", "strict", "Airstrikes hit oil depot as war enters its third year"),
    ("deaths", "strict", "Factory fire kills 12, officials say"),
    ("disaster", "strict", "Magnitude 7.1 earthquake strikes off the coast of Japan"),
    ("health", "strict", "Bird flu outbreak spreads to dairy herds, USDA says"),
    ("legal", "strict", "SEC sues Bybaz over unregistered token sales"),
]


@pytest.mark.parametrize("kind,tier,title", SENSITIVE_CASES)
def test_each_sensitive_category_is_recognised_and_written_plainly(kind: str, tier: str, title: str) -> None:
    c = sensitive.classify(title)
    assert c is not None and c["kind"] == kind and c["tier"] == tier, c
    b = _brief(headline=title, outlet="BBC", category="world", outlets=["BBC", "Reuters"], google=True, google_query="x", google_to=500_000)
    b.sensitive = c
    b.source_text = title + " " + " ".join(writer.measured_lines(b))
    text, angle = writer.rules(b)
    assert angle == f"sensitive:{kind}" and text.startswith(f"BBC reports: {title}") and text.endswith("Also reported by Reuters.")
    assert "?" not in text and not gates.EMOJI.search(text) and "Searches" not in text  # no hook, question, emoji or interest statistics
    res = gates.gate(text, source_text=b.source_text, source_names=["BBC"], reliable_count=2)
    assert res["ok"], res["reason"]
    for bad in (f"{text} What happens now?", f"\U0001F622 {text}", f"Wow. {text}", f"{text}\n\nSearches for it have topped 500K on Google."):
        assert not gates.gate(bad, source_text=b.source_text + " Searches for it have topped 500K on Google.", source_names=["BBC"], reliable_count=2)["ok"], bad
    # automatic: never for the approval-only subjects; the strict ones only with 2+ independent outlets, attributed, nobody named
    two = sensitive.auto_problem(c, text=text, outlets=["BBC", "Reuters"])
    one = sensitive.auto_problem(c, text=text, outlets=["BBC", "BBC News"])  # one outlet under two names
    if tier == "approval":
        assert two and "approval only" in two
    else:
        assert two is None and one and "at least 2 independent" in one
        if not sensitive.ATTRIBUTION.search(title):  # a headline that already says who says it is attributed
            assert "who reports it" in (sensitive.auto_problem(c, text=title, outlets=["BBC", "Reuters"]) or "")


def test_ordinary_market_stories_are_not_sensitive() -> None:
    for t in (HACK, "Trade war fears lift gold to $2,400", "Nvidia shares slide 4% after earnings", "Bitcoin price war chest grows as ETFs add $1 billion",
              "Clinical trial results lift Moderna shares"):
        assert sensitive.classify(t) is None, t


def test_allegation_wording_keeps_the_sources_hedges() -> None:
    src = "Senator John Smith accused of taking bribes, prosecutors say"
    ok = gates.gate(f"BBC reports: {src}.", source_text=src, source_names=["BBC"], reliable_count=2)
    assert ok["ok"], ok["reason"]
    dropped = gates.gate("BBC reports: Senator John Smith took bribes.", source_text=src + " took", source_names=["BBC"], reliable_count=2)
    assert not dropped["ok"] and any("hedge words" in c["detail"] for c in dropped["checks"] if not c["passed"])


def test_a_strict_topic_names_no_person_when_automatic() -> None:
    c = sensitive.classify("Factory fire kills 12, officials say")
    assert "names a person" in (sensitive.auto_problem(c, text="BBC reports: Factory fire kills 12 as John Smith escapes, officials say.", outlets=["BBC", "AP"]) or "")


def _hot_story(db: Session, web: Web, titles: list[tuple[str, str]], query: str) -> None:
    for i, (outlet, title) in enumerate(titles):
        story(db, title, outlet, 100 + i, NOW, 20 - i, category="politics")
    web.google = [{"query": query, "traffic": "500K+", "news": [{"title": titles[0][1], "url": "https://bbc.test/s", "source": titles[0][0]}]}]


async def test_automatic_mode_never_publishes_an_allegation_about_a_named_person(db: Session, web: Web) -> None:
    s = engine.settings_row(db)
    s.hot_score = 40
    db.commit()
    _hot_story(db, web, [("BBC", "Senator John Smith accused of taking bribes from Bybaz, prosecutors say"),
                         ("Reuters", "Prosecutors say Senator John Smith accused of taking Bybaz bribes"),
                         ("AP", "Senator John Smith accused of taking bribes from crypto firm Bybaz")], "john smith")
    await tick(db, web)
    [post] = [p for p in posts(db) if "Smith" in p.text]
    assert post.status == "pending" and "approval only" in (post.note or "") and post.angle == "sensitive:allegation"
    assert "?" not in post.text and post.text.startswith(("BBC reports:", "Reuters reports:", "AP reports:"))


async def test_automatic_mode_may_publish_a_disaster_only_with_two_independent_outlets(db: Session, web: Web) -> None:
    s = engine.settings_row(db)
    s.hot_score = 40
    db.commit()
    _hot_story(db, web, [("BBC", "Magnitude 7.1 earthquake strikes off the coast of Japan, closing Bybaz offices"),
                         ("Reuters", "Magnitude 7.1 earthquake strikes off the coast of Japan; Bybaz offices closed")], "japan earthquake")
    await tick(db, web)
    [post] = [p for p in posts(db) if "earthquake" in p.text]
    assert post.angle == "sensitive:disaster" and post.status == "dry_run", (post.status, post.note, post.text)  # two outlets, attributed: it may go
    assert "Also reported by" in post.text and "Searches" not in post.text


async def test_a_disaster_with_one_outlet_waits_for_the_owner(db: Session, web: Web) -> None:
    s = engine.settings_row(db)
    s.hot_score = 40
    db.commit()
    _hot_story(db, web, [("BBC", "Magnitude 7.1 earthquake strikes off the coast of Japan, closing Bybaz offices")], "japan earthquake")
    await tick(db, web)
    [post] = [p for p in posts(db) if "earthquake" in p.text]
    assert post.status == "pending" and "at least 2 independent" in (post.note or "")


# --- growth evidence in the engine ----------------------------------------------------------------------------------------------


async def test_a_hot_score_without_measured_growth_waits_for_the_owner(db: Session, web: Web) -> None:
    s = engine.settings_row(db)
    s.hot_score, s.suggest_score = 30, 20  # even with the line lowered by hand, coverage alone never publishes by itself
    db.commit()
    for i, o in enumerate(OUTLETS[:6]):
        story(db, ZEN[i % 5], o, 200 + i, NOW, 30 + 40 * i)
    await tick(db, web)
    [post] = [p for p in posts(db) if "Zentrix" in p.text]
    topic = db.get(TtTopic, post.topic_id)
    assert topic.score >= 30 and topic.parts["growth"]["measured"] is False
    assert post.status == "pending" and "no measured growth" in (post.note or "")


# --- limits (unchanged: 3 a day, 180 minutes apart, the shared cap) ----------------------------------------------------------


async def test_never_more_than_three_trend_posts_a_day_and_always_three_hours_apart(db: Session, web: Web) -> None:
    s = engine.settings_row(db)
    db.commit()
    for i in range(8):
        db.add(TtPost(topic_id=1000 + i, category="crypto", status="queued", text=f"{HACK} (via CoinDesk)", score=90 - i, source_text=SRC,
                      sources=[{"name": "CoinDesk", "title": f"story {i}", "url": "https://c.test"}], checks=[], metrics={}, expires_at=NOW + timedelta(days=3)))
    db.commit()
    at = NOW.replace(hour=0, minute=5)
    for step in range(6 * 36):  # every 10 minutes for 36 hours
        await engine.publish_due(db, s, get_settings(), web.runtime, get_agent_config(db).id, at + timedelta(minutes=10 * step))
    sent = sorted(p.published_at for p in posts(db) if p.status == "dry_run")
    assert len(sent) >= 4  # the limits pace posts, they don't stop them
    assert all((b - a) >= timedelta(minutes=180) for a, b in zip(sent, sent[1:]))
    assert all(sum(1 for x in sent if t - timedelta(hours=24) < x <= t) <= 3 for t in sent)
    assert web.posted == []
    s.max_per_day, s.shared_daily_cap = 10, 1  # the shared News Desk + Trending cap stops it too
    db.commit()
    assert "shared X cap" in (coordinate.cap_problem(db, s, sent[-1] + timedelta(minutes=1)) or "")


# --- production evidence: the scan log ------------------------------------------------------------------------------------------

SECRETISH = re.compile(r"(?i)bearer\s|sk-[a-z0-9]|api[_-]?key\s*[=:]|token\s*[=:]|secret\s*[=:]|password|[A-Za-z0-9_\-]{32,}|[\w.+-]+@[\w-]+\.\w+")


async def test_each_scan_logs_sources_topics_top_five_and_the_decision_with_no_secrets(db: Session, web: Web, caplog: pytest.LogCaptureFixture,
                                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    import logging

    monkeypatch.setattr(engine, "_STARTUP_LOGGED", False)
    hack_story(db, web)
    with caplog.at_level(logging.INFO, logger="app.trending.engine"):
        await tick(db, web)
        await tick(db, web, NOW + timedelta(minutes=10))
    lines = [r.getMessage() for r in caplog.records if r.name == "app.trending.engine"]
    startup = [x for x in lines if x.startswith("trending startup:")]
    assert len(startup) == 1  # once per process (per deploy)
    for want in ("mode=automatic", "auto=on", "hot_threshold=75", "daily_max=3", "min_gap=180min", "newsdesk_x=dry_run", "x_live=False", "live_switch=on"):
        assert want in startup[0], want
    scans = [x for x in lines if x.startswith("trending scan ")]
    assert len(scans) == 2 and "news=ok" in scans[0] and "google=ok" in scans[0] and "reddit=unavailable" in scans[0] and "x_trends=off" in scans[0]
    assert "google=ok (not due)" in scans[1]
    tops = [x for x in lines if x.startswith("trending top")]
    assert tops and "Bybaz" in tops[0] and "growth=google" in tops[0] and " v" in tops[0] and "tweet " in tops[0]
    pubs = [x for x in lines if x.startswith("trending publish:")]
    assert len(pubs) == 2 and pubs[0].startswith("trending publish: dry_run") and "dry run" in pubs[0].lower()
    assert len(lines) <= 1 + 2 * 7  # a few lines per scan
    for x in lines:
        assert not SECRETISH.search(x), x
    assert engine._safe("Authorization: Bearer abc.def.ghi token=xyz sk-ant-12345678 owner@example.com").count("[redacted]") >= 3
