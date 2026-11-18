"""Price Chat Terminal (owner, 2026-11-18): viral potential kept apart from evidence, rumours never published automatically, the fast lane
for verified high-impact breaking events, hooks through the gate, the discovery sources, and the production faults found in the 72-hour
audit (round-up false positives, one story becoming a new topic row every scan, a 19-dead crash with no fast path).

Headlines marked "production" are real ones from the 2026-11-15..18 Railway logs; the rest are fixtures. Nothing real is contacted."""

from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.settings import get_settings
from app.db.base import utcnow
from app.models.trending import TtPost, TtTopic
from app.newsdesk import quality
from app.services.agent_service import get_agent_config
from app.terminal import discovery, feed, intel
from app.trending import coordinate, engine, gates
from tests.trending_fixtures import Web, story

NOW = utcnow().replace(hour=10, minute=10, second=0, microsecond=0)


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


async def tick(db: Session, web: Web, now: Any = None) -> dict[str, Any]:
    return await engine.tick(db, get_settings(), web.runtime, get_agent_config(db).id, now or NOW, transport=web.transport)


def src(outlet: str, title: str, *, reliable: bool = True, source: str = "news", **extra: Any) -> dict[str, Any]:
    return {"source": source, "outlet": outlet, "title": title, "url": f"https://{outlet.lower().replace(' ', '')}.test/a", "reliable": reliable,
            "engagement": 1.0, "summary": "", "extra": extra}


FRESH = {"age_hours": 0.3, "stale": None, "impact": 12.0, "relevance": 7.0, "timeline": {"outlets": 3, "outlets_1h": 3, "items_per_hour": 4}, "growth": {}}


# --- evidence and viral potential are separate ---------------------------------------------------------------------------------------


def test_social_only_story_is_a_rumour_with_low_evidence_whatever_its_reach() -> None:
    srcs = [src("Google Trends", "exchange down", reliable=False, source="google", approx_traffic="500K+"),
            src("Hacker News", "Major exchange appears to be down for everyone", reliable=False, source="hn", points=900)]
    parts = {**FRESH, "growth": {"measured": True, "google_up": True}}
    ev = intel.evidence("exchange down", srcs, parts)
    vi = intel.viral("Major exchange appears to be down for everyone", "crypto", srcs, parts)
    assert ev["rumor"] and ev["score"] <= 20 and ev["level"] == "unverified"
    assert vi["parts"]["social"] >= 4  # measured: Google 500K+ and rising, Hacker News 900 points
    assert any("Google Trends 500K+" in m for m in vi["measured"]) and any("Hacker News 900" in m for m in vi["measured"])
    assert intel.ahead_of_mainstream(srcs, parts, ev)


def test_hedged_headline_from_a_reliable_outlet_is_still_a_rumour() -> None:
    ev = intel.evidence("x", [src("Reuters", "Binance reportedly weighs halting withdrawals, sources say")], FRESH)
    assert ev["rumor"] and any("hedged" in r for r in ev["reasons"])


def test_three_independent_outlets_with_mainstream_confirm() -> None:
    t = "Russia launches missile strikes on Kyiv, killing 25"
    ev = intel.evidence(t, [src("Reuters", t), src("BBC", t), src("Al Jazeera", t)], FRESH)
    assert not ev["rumor"] and ev["level"] == "confirmed" and ev["mainstream"] and ev["score"] >= 75


def test_an_official_source_alone_is_corroborated() -> None:
    ev = intel.evidence("q", [src("USGS", "Magnitude 7.1 earthquake strikes near Tokyo", source="usgs", official=True, magnitude=7.1)], FRESH)
    assert ev["official"] and ev["score"] >= 50 and not ev["rumor"]


def test_no_engagement_figure_is_invented() -> None:
    vi = intel.viral("Fed holds rates steady", "markets", [src("Reuters", "Fed holds rates steady")], {"age_hours": 1, "timeline": {}, "growth": {}})
    assert vi["parts"]["social"] == 0 and vi["measured"] == []


# --- the fast lane -------------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("title", [
    "Russia launches missile strikes on Kyiv, killing 25",
    "Military aircraft crashes in Siberia, killing all 19 onboard: Russian ministry",  # production 2026-11-17, verified +153 min, never posted
    "Hackers drain $120 million from crypto exchange Bybaz",
    # production 2026-11-17: seen 1 min after the first report, refused "under 90 minutes since the last post", out 137 min later in a combined story
    "US jets strike IRGC missile launch site on Iran's Qeshm Island in 'self-defence'",
    "Ukrainian drone attack damages Crimea bridge, shutting down road traffic for hours",  # production 2026-11-16: "scores 65, under the breaking bar of 70"
    "Iranian mines hit five ships in Strait of Hormuz",  # ground truth 2026-11-17
    "Wall Street sells off as Nasdaq enters correction",  # ground truth 2026-11-16
    "Nvidia revenue jumps 103% to $115.8 billion, beats estimates",  # ground truth 2026-11-17
])
def test_verified_fresh_high_impact_events_take_the_fast_lane(title: str) -> None:
    srcs = [src("Reuters", title), src("Associated Press", title), src("BBC", title)]
    ev = intel.evidence(title, srcs, FRESH)
    ok, why = intel.fast_lane(title, "", srcs, FRESH, ev)
    assert ok, why


def test_fast_lane_refuses_single_source_old_or_hedged_or_soft_stories() -> None:
    t = "Russia launches missile strikes on Kyiv, killing 25"
    one = [src("Reuters", t)]
    assert not intel.fast_lane(t, "", one, FRESH, intel.evidence(t, one, FRESH))[0]  # one outlet: evidence 40 < 55
    three = [src("Reuters", t), src("BBC", t), src("CNN", t)]
    old = {**FRESH, "age_hours": 3.0}
    assert "fast lane only within" in intel.fast_lane(t, "", three, old, intel.evidence(t, three, old))[1]
    h = "Russia reportedly launches missile strikes on Kyiv, sources say"
    hs = [src("Reuters", h), src("BBC", h), src("CNN", h)]
    assert "rumour" in intel.fast_lane(h, "", hs, FRESH, intel.evidence(h, hs, FRESH))[1]
    for routine in ("Bitcoin price holds near $65,000 as Nvidia turmoil drags Nasdaq down 2.5%", "Nvidia set to report quarterly earnings for first time since Jensen Huang resigned"):
        rs = [src("Reuters", routine), src("BBC", routine), src("CNBC", routine)]
        assert not intel.fast_lane(routine, "", rs, FRESH, intel.evidence(routine, rs, FRESH))[0], routine
    soft = "Trump says strikes on Iran will continue"  # talk, not the event
    ss = [src("Reuters", soft), src("BBC", soft), src("CNN", soft)]
    assert not intel.fast_lane(soft, "", ss, FRESH, intel.evidence(soft, ss, FRESH))[0]


def test_usgs_magnitude_and_gdacs_red_are_high_impact() -> None:
    assert intel.impact_class("Magnitude 7.2 earthquake strikes off Japan", "", [src("USGS", "q", source="usgs", magnitude=7.2)], {})[0] == "disaster"
    assert intel.impact_class("Red alert for tropical cyclone", "", [src("GDACS", "q", source="gdacs", alert="red", event="tropical cyclone")], {})[0] == "disaster"
    assert intel.impact_class("Magnitude 6.1 earthquake strikes Chile", "", [src("USGS", "q", source="usgs", magnitude=6.1)], {})[0] is None


class _T:
    def __init__(self, score: float, terminal: dict[str, Any], growth: bool = False, title: str = "Iranian mines hit five ships in Strait of Hormuz") -> None:
        self.score, self.parts, self.title = score, {"terminal": terminal, "growth": {"measured": growth}}, title


def test_auto_ok_fast_lane_skips_the_momentum_line_but_rumours_never_publish(db: Session) -> None:
    s = engine.settings_row(db)
    assert engine.auto_ok(s, _T(52, {"fast": True}), "t", None) == (True, None)  # below 75, no measured growth: fast lane
    assert not engine.auto_ok(s, _T(52, {"fast": False}), "t", None)[0]
    ok, why = engine.auto_ok(s, _T(95, {"rumor": True}, growth=True), "t", None)  # hot, growing, but a rumour
    assert not ok and "rumour" in why


def test_fast_posts_have_their_own_allowance_and_short_gaps(db: Session) -> None:
    s = engine.settings_row(db)
    now = NOW
    for i in range(s.max_per_day):
        db.add(TtPost(topic_id=0, category="world", status="dry_run", text=f"t{i}", published_at=now - timedelta(minutes=60 + i)))
    db.commit()
    assert "daily limit" in (coordinate.cap_problem(db, s, now) or "")
    assert coordinate.cap_problem(db, s, now, fast=True) is None
    db.add(TtPost(topic_id=0, category="world", status="dry_run", text="recent", published_at=now - timedelta(minutes=25)))
    db.commit()
    assert "spacing" in (coordinate.spacing_problem(db, s, now) or "")
    assert coordinate.spacing_problem(db, s, now, fast=True) is None  # 25 min > the fast gap of 20


async def test_a_verified_breaking_event_publishes_on_its_first_scan(db: Session, web: Web, alerts_log: list[Any]) -> None:
    t = "Russia launches missile strikes on Kyiv, killing 25"
    story(db, t, "Reuters", 1, NOW, 12, category="politics")
    story(db, "Russian missile strikes on Kyiv kill 25, officials say", "Associated Press", 2, NOW, 9, category="politics")
    story(db, "Russia launches missile strikes on Kyiv, killing at least 25", "BBC", 3, NOW, 6, category="politics")
    await tick(db, web)
    topic = max(db.scalars(select(TtTopic)), key=lambda x: x.score)
    term = topic.parts["terminal"]
    assert term["fast"], term["fast_why"]
    post = db.scalars(select(TtPost).where(TtPost.topic_id == topic.id)).first()
    assert post is not None and post.angle.startswith("fast"), (post and (post.status, post.hold_reason, post.note))
    assert post.status in ("dry_run", "published", "queued"), (post.status, post.hold_reason, post.note)
    assert any(k.startswith("terminal:fast:") for k, _ in alerts_log)


async def test_a_social_only_story_never_publishes(db: Session, web: Web) -> None:
    web.google = [{"query": "bybaz down", "traffic": "500K+", "news": []}]
    await tick(db, web)
    assert not list(db.scalars(select(TtPost).where(TtPost.status.in_(("queued", "dry_run", "published")))))


# --- hooks ----------------------------------------------------------------------------------------------------------------------------


def test_rule_hooks_for_a_verified_story_pass_the_gate_and_rumour_hooks_say_unconfirmed() -> None:
    t = "Russia launches missile strikes on Kyiv, killing 25"
    srcs = [src("Reuters", t), src("BBC", t), src("CNN", t)]
    ev = intel.evidence(t, srcs, FRESH)
    hooks = intel.rule_hooks(t, "Reuters", "geopolitics", ev, 80, None)
    assert [h["kind"] for h in hooks] == ["breaking", "viral", "curiosity"]
    assert hooks[0]["text"].startswith("BREAKING:")
    for h in hooks:
        chk = intel.check_hook(h["text"], source_text=t, outlet="Reuters", reliable_count=3, breaking_ok=intel.breaking_allowed(ev))
        assert chk["ok"], (h, chk)
    r = intel.rule_hooks("Binance reportedly halts withdrawals", "CoinDesk", "crypto", {"rumor": True, "level": "unverified"}, 80, None)
    assert all(any(w in h["text"].lower() for w in ("unconfirmed", "unverified", "circulating")) for h in r)


def test_breaking_needs_a_verified_story_at_the_gate() -> None:
    text = "BREAKING: Russia launches missile strikes on Kyiv\n\nVia Reuters"
    src_text = "Russia launches missile strikes on Kyiv"
    assert not gates.gate(text, source_text=src_text, source_names=["Reuters"], reliable_count=1)["ok"]
    assert gates.gate(text, source_text=src_text, source_names=["Reuters"], reliable_count=3, breaking_ok=True)["ok"]


def test_hooks_never_add_figures_the_headline_lacks() -> None:
    hooks = intel.rule_hooks("Oil jumps after attack on Saudi facility", "Reuters", "markets", {"rumor": False, "level": "corroborated"}, 60, None)
    for h in hooks:
        chk = intel.check_hook(h["text"], source_text="Oil jumps after attack on Saudi facility", outlet="Reuters", reliable_count=2, breaking_ok=True)
        assert chk["ok"], (h, chk)
    bad = intel.check_hook("BREAKING: Oil jumps 12% after attack on Saudi facility\n\nVia Reuters", source_text="Oil jumps after attack on Saudi facility", outlet="Reuters",
                           reliable_count=2, breaking_ok=True)
    assert not bad["ok"]


# --- production faults from the 72-hour audit ------------------------------------------------------------------------------------------


@pytest.mark.parametrize("title", [
    "Apple outage impacts iCloud, support and App Store for users",  # production 2026-11-17, held as a round-up at 86.1
    "Trump Downplays Microsoft, JPMorgan Outages, Saying ‘It’s Life’",  # production 2026-11-17
    "Cyberattack targets BBC, Zoom and Ikea as 178 websites hacked",  # production 2026-11-17
])
def test_one_event_hitting_several_services_is_not_a_round_up(title: str) -> None:
    assert not quality.is_roundup(title)


@pytest.mark.parametrize("title", ["Nationwide ground stop lifted, Nvidia spy scandal, starship's longest test flight", "Hot Inflation, AI Selloff & Anthropic IPO"])
def test_real_round_ups_still_are(title: str) -> None:
    assert quality.is_roundup(title)


async def test_one_story_keeps_its_topic_rows_across_scans(db: Session, web: Web) -> None:
    """Production 2026-11-17: the iCloud outage made a new topic row every scan (40 rows in two hours)."""
    story(db, "iCloud Mail, Calendar, Contacts and More Experiencing Outage", "MacRumors", 1, NOW, 20, category="viral")
    story(db, "iCloud, App Store, and other Apple services hit by ongoing outage", "9to5Mac", 2, NOW, 15, category="viral")
    web.google = [{"query": "apple down", "traffic": "100K+", "news": [{"title": "iCloud, App Store, and other Apple services hit by ongoing outage",
                                                                        "url": "https://9to5mac.test/2", "source": "9to5Mac"}]}]
    await tick(db, web)
    first = {t.id for t in db.scalars(select(TtTopic))}
    for i in range(1, 4):
        await tick(db, web, NOW + timedelta(minutes=5 * i))
    assert {t.id for t in db.scalars(select(TtTopic))} == first


# --- discovery sources ----------------------------------------------------------------------------------------------------------------


def test_usgs_keeps_only_big_quakes_as_official_items() -> None:
    body = {"features": [{"id": "us1", "properties": {"mag": 7.0, "place": "50 km S of Hualien, Taiwan", "time": 1794978902000, "url": "https://usgs.test/1", "alert": "orange", "tsunami": 1}},
                         {"id": "us2", "properties": {"mag": 4.8, "place": "Alaska", "time": 1794978902000}}]}
    items = discovery.parse_usgs(body)
    assert len(items) == 1 and items[0]["reliable"] and items[0]["extra"]["official"] and "Magnitude 7.0 earthquake strikes" in items[0]["title"]


def test_gdacs_orange_and_red_only() -> None:
    xml = """<?xml version="1.0"?><rss xmlns:gdacs="http://www.gdacs.org"><channel>
    <item><title>Red alert for tropical cyclone FUNG-WONG</title><link>https://gdacs.test/1</link><pubDate>{d}</pubDate><gdacs:alertlevel>Red</gdacs:alertlevel><gdacs:eventtype>TC</gdacs:eventtype><gdacs:eventid>1</gdacs:eventid></item>
    <item><title>Green alert for flood</title><pubDate>{d}</pubDate><gdacs:alertlevel>Green</gdacs:alertlevel><gdacs:eventtype>FL</gdacs:eventtype></item>
    </channel></rss>""".replace("{d}", NOW.strftime("%a, %d %b %Y %H:%M:%S GMT"))
    items = discovery.parse_gdacs(xml, NOW)
    assert [i["extra"]["alert"] for i in items] == ["red"]


def test_gdelt_items_are_momentum_never_evidence() -> None:
    items = discovery.parse_gdelt({"articles": [{"title": "Explosion reported at port in Odesa", "url": "https://local.test/1", "domain": "local.test", "seendate": "20261118T091500Z"},
                                                {"title": "Celebrity launches perfume", "url": "https://x.test/2", "domain": "x.test"}]})
    assert len(items) == 1 and not items[0]["reliable"]


# --- the API ---------------------------------------------------------------------------------------------------------------------------


async def test_terminal_api_lists_stories_with_both_scores_and_actions(db: Session, web: Web, client: TestClient) -> None:
    t = "Russia launches missile strikes on Kyiv, killing 25"
    story(db, t, "Reuters", 1, NOW, 12, category="politics")
    story(db, "Russian missile strikes on Kyiv kill 25, officials say", "Associated Press", 2, NOW, 9, category="politics")
    await tick(db, web)
    r = client.get("/api/terminal")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["stories"] and {"viral", "evidence", "lane", "decision", "sources"} <= set(body["stories"][0])
    sid = body["stories"][0]["id"]
    d = client.get(f"/api/terminal/stories/{sid}").json()
    assert len(d["hooks"]) >= 3 and d["timeline"]
    r = client.post(f"/api/terminal/stories/{sid}/reject", json={"reason": "test"})
    assert r.status_code == 200 and r.json()["lane"] == "rejected"


# --- News Desk: an off-story corroborating row no longer kills the whole post (audit 2026-11-18) --------------------------------------


def test_off_story_sources_are_left_out_instead_of_refusing_the_post() -> None:
    """Production 2026-11-17 23:47: event #662 "Kuwait reports drone attacks as Iran, US trade attacks on Qeshm and Bahrain" was released as
    BREAKING, then skipped on X and Telegram: one of its rows was "Iran attacks US air base in Bahrain". Four of five breaking releases died
    this way (#649, #658, #662, #667)."""
    from types import SimpleNamespace

    ev = SimpleNamespace(kind="news", title="Kuwait reports drone attacks as Iran, US trade attacks on Qeshm and Bahrain", summary="")
    lead = SimpleNamespace(title=ev.title)
    same = SimpleNamespace(title="Kuwait says it intercepted drones as Iran and US trade strikes on Qeshm")
    other = SimpleNamespace(title="Iran attacks US air base in Bahrain")
    assert quality.event_problems(ev, [lead, same, other])  # the old behaviour: the whole post refused
    dropped, kept = quality.split_off_story(ev, [lead, same, other])
    assert lead in kept and other in dropped
    assert not quality.event_problems(ev, kept)


# --- owner alerts: linking the owner's chat from Admin (production: alerts were "logged only", no chat set) ------------------------------


def test_owner_link_code_works_once_and_expires(db: Session) -> None:
    from app import alerts

    code, _ = alerts.start_owner_link(db, NOW)
    assert not alerts.claim_owner_link(db, "wrong", 4242, NOW)
    assert not alerts.claim_owner_link(db, code, 4242, NOW + timedelta(minutes=31))  # expired
    code, _ = alerts.start_owner_link(db, NOW)
    assert alerts.claim_owner_link(db, code, 4242, NOW + timedelta(minutes=1))
    assert alerts.owner_chat(db) == "4242"
    assert not alerts.claim_owner_link(db, code, 999, NOW + timedelta(minutes=2))  # once
    assert alerts.owner_chat(db) == "4242"


def test_owner_link_route_needs_the_bot_and_returns_a_deep_link(db: Session, client: TestClient) -> None:
    from app.telegram.ingestion import get_bot_state

    get_bot_state(db).bot_username = "Cryptopricechatbot"
    db.commit()
    r = client.post("/api/system/alerts/link")
    assert r.status_code == 200 and r.json()["url"].startswith("https://t.me/Cryptopricechatbot?start=ow_")
