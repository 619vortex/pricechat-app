"""X Intelligence (owner, 2026-11-18). SIMULATED X: the X API is mocked (recent search answers with fixture posts); nothing real is read or
paid for. The rest is the real pipeline: Trending's scan -> signals -> topics -> Terminal scores -> gates -> (dry-run) publication."""

from datetime import timedelta
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.settings import get_settings
from app.db.base import utcnow
from app.models.trending import TtPost, TtSignal, TtTopic
from app.services.agent_service import get_agent_config
from app.terminal import feed, intel, xintel
from app.trending import engine
from tests.conftest import connect_x_user
from tests.trending_fixtures import Web, story

NOW = utcnow().replace(hour=10, minute=10, second=0, microsecond=0)
WATCH = [{"handle": "Reuters", "group": "breaking", "tier": "A"}, {"handle": "zachxbt", "group": "onchain", "tier": "B"},
         {"handle": "PeckShieldAlert", "group": "onchain", "tier": "B"}, {"handle": "lookonchain", "group": "onchain", "tier": "C"},
         {"handle": "WatcherGuru", "group": "onchain", "tier": "C"}, {"handle": "tier10k", "group": "onchain", "tier": "C"}]
USERS = {h["handle"]: str(1000 + i) for i, h in enumerate(WATCH)}


def post(pid: int, handle: str, text: str, minutes_ago: int, likes: int = 50, rts: int = 10, quoted: str | None = None) -> dict[str, Any]:
    p = {"id": str(pid), "text": text, "author_id": USERS[handle], "created_at": (NOW - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
         "conversation_id": str(pid), "public_metrics": {"like_count": likes, "retweet_count": rts, "reply_count": 3, "quote_count": 1}}
    if quoted:
        p["referenced_tweets"] = [{"type": "quoted", "id": quoted}]
    return p


class XWeb(Web):
    """The trending fixtures' web plus X's recent search."""

    def __init__(self, now: Any) -> None:
        super().__init__(now)
        self.x_posts: list[dict[str, Any]] = []
        self.queries: list[dict[str, str]] = []
        self.lookups: list[list[str]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.x.com" and request.url.path == "/2/users/by":
            names = dict(request.url.params)["usernames"].split(",")
            self.lookups.append(names)
            return httpx.Response(200, json={"data": [{"id": USERS[n], "username": n} for n in names if n in USERS]})
        if request.url.host == "api.x.com" and request.url.path == "/2/tweets/search/recent":
            q = dict(request.url.params)
            self.queries.append(q)
            since = int(q.get("since_id") or 0)
            handles = {h.split(":", 1)[1].lower() for h in q["query"].strip("()").split(") ")[0].replace("(", "").split(" OR ")}
            data = [p for p in self.x_posts if int(p["id"]) > since and next(h for h, u in USERS.items() if u == p["author_id"]).lower() in handles]
            users = [{"id": USERS[h], "username": h, "name": h} for h in USERS if USERS[h] in {p["author_id"] for p in data}] if "expansions" in q else []
            meta = {"result_count": len(data), **({"newest_id": max((p["id"] for p in data), key=int)} if data else {})}
            return httpx.Response(200, json={"data": data, "includes": {"users": users}, "meta": meta})
        return super().handler(request)


@pytest.fixture
def xweb(db: Session, monkeypatch: pytest.MonkeyPatch) -> XWeb:
    connect_x_user(db)
    s = get_settings()
    monkeypatch.setattr(s, "x_daily_budget_usd", 5.0)  # production's account guard (the owner's caps)
    monkeypatch.setattr(s, "x_monthly_budget_usd", 30.0)
    pilot_handled(db)
    return XWeb(NOW)


def pilot_handled(db: Session) -> None:
    """The one-time pilot activation already happened (these tests switch X Intelligence on and off themselves)."""
    t = engine.settings_row(db)
    t.state = {**(t.state or {}), "x_intel": {"pilot": {"approved": xintel.PILOT_APPROVED}}}
    db.commit()


@pytest.fixture(autouse=True)
def alerts_log(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    sent: list[tuple[str, str]] = []
    from app import alerts

    async def notify(db: Any, key: str, text: str, **_: Any) -> bool:
        sent.append((key, text))
        return True

    monkeypatch.setattr(alerts, "notify_owner", notify)
    return sent


def turn_on(db: Session, accounts: list[dict[str, str]] = WATCH, daily_usd: float = 2.0) -> None:
    feed.x_settings(db, accounts=accounts)
    feed.x_settings(db, on=True, confirm_paid=True, daily_usd=daily_usd)


async def tick(db: Session, web: XWeb, now: Any = None) -> dict[str, Any]:
    return await engine.tick(db, get_settings(), web.runtime, get_agent_config(db).id, now or NOW, transport=web.transport)


HACK = "Bybaz exchange drained of $120 million in hot wallet exploit"


# --- access and the paid flag ----------------------------------------------------------------------------------------------------------


def test_off_by_default_and_turning_on_needs_a_confirmation(db: Session, client: TestClient) -> None:
    s = engine.settings_row(db)
    from app.trending import sources

    assert sources.merged(s.sources)["x_accounts"]["on"] is False
    with pytest.raises(ValueError, match="paid"):
        feed.x_settings(db, on=True)
    r = client.put("/api/x/trending/settings", json={"sources": {"x_accounts": {"on": True}}})
    assert r.status_code == 400 and "X Intelligence" in r.text  # not around the price screen either
    r = client.post("/api/terminal/x/read")
    assert r.status_code == 409


async def test_nothing_is_read_from_x_while_it_is_off(db: Session, xweb: XWeb) -> None:
    xweb.x_posts = [post(10, "Reuters", HACK, 5)]
    await tick(db, xweb)
    assert xweb.queries == []


def test_queries_fit_xs_length_limit_and_exclude_reposts() -> None:
    many = [{"handle": f"acct{i:02d}xyzabc", "group": "breaking", "tier": "C"} for i in range(60)]
    qs = xintel.queries(many)
    assert len(qs) > 1 and all(len(q) <= xintel.QUERY_MAX and q.endswith("-is:retweet -is:reply") for q, _ in qs)
    assert sum(len(h) for _, h in qs) == 60


def test_watchlist_validation_drops_bad_entries() -> None:
    rows = xintel.clean_watchlist([{"handle": "@Reuters", "group": "breaking", "tier": "a"}, {"handle": "bad handle!", "group": "breaking", "tier": "A"},
                                   {"handle": "reuters", "group": "breaking", "tier": "A"}, {"handle": "x", "group": "nope", "tier": "A"}])
    assert rows == [{"handle": "Reuters", "group": "breaking", "tier": "A", "note": "", "on": True}]


# --- ingestion, incremental reads, cost --------------------------------------------------------------------------------------------------


async def test_reads_are_incremental_and_the_cost_is_measured(db: Session, xweb: XWeb) -> None:
    turn_on(db)
    xweb.x_posts = [post(10, "zachxbt", HACK, 20), post(11, "lookonchain", "Bybaz hot wallet drained, $120M gone, exploit ongoing", 15)]
    await tick(db, xweb)
    assert len(xweb.queries) == 1 and "since_id" not in xweb.queries[0]
    sigs = list(db.scalars(select(TtSignal).where(TtSignal.source == "x_accounts")))
    assert {s.extra["handle"] for s in sigs} == {"zachxbt", "lookonchain"} and sigs[0].url.startswith("https://x.com/")
    st = engine.settings_row(db).state["x_intel"]
    assert xweb.lookups == [[w["handle"] for w in WATCH]]  # the watched accounts, looked up once in one request
    assert st["last_read"]["usd"] == pytest.approx(2 * 0.005 + len(WATCH) * 0.010)
    assert "expansions" not in xweb.queries[0]  # searches never pay for author objects
    xweb.x_posts.append(post(12, "PeckShieldAlert", "Bybaz exploit: attacker drained ~$120M, funds moving to Tornado Cash", 2))
    await tick(db, xweb, NOW + timedelta(minutes=6))
    assert xweb.queries[-1]["since_id"] == "11"  # only what's new is read (and paid for)
    # every watched author is known by now: no user objects asked for, so only the post is paid
    assert "expansions" not in xweb.queries[-1]
    assert engine.settings_row(db).state["x_intel"]["last_read"]["usd"] == pytest.approx(0.005)
    sig = db.scalars(select(TtSignal).where(TtSignal.external_id == "xpost:12")).one()
    assert sig.extra["handle"] == "PeckShieldAlert"  # named from the cache


async def test_the_daily_cap_stops_reads_before_spending(db: Session, xweb: XWeb) -> None:
    turn_on(db, daily_usd=0.1)
    xweb.x_posts = [post(10, "zachxbt", HACK, 20)]
    await tick(db, xweb)
    assert xweb.queries == []  # one batch could cost up to 50 x $0.005 + 6 x $0.010 = $0.31 > $0.10
    h = engine.settings_row(db).state["health"]["x_accounts"]
    assert "daily cap" in (h["problem"] or "")


# --- narratives, independence, evidence, publication ---------------------------------------------------------------------------------


async def test_an_x_only_narrative_is_a_rumour_and_never_publishes(db: Session, xweb: XWeb, alerts_log: list[Any]) -> None:
    turn_on(db)
    xweb.x_posts = [post(10, "zachxbt", HACK, 20, likes=900, rts=400), post(11, "lookonchain", "Bybaz exchange drained of $120 million in exploit", 15),
                    post(12, "WatcherGuru", "JUST IN: Bybaz exchange drained of $120 million in hot wallet exploit", 10),
                    post(13, "tier10k", "Bybaz exchange drained of $120 million in hot wallet exploit", 9)]
    await tick(db, xweb)
    t = max(db.scalars(select(TtTopic)), key=lambda t: len(t.signal_ids or []))
    term = t.parts["terminal"]
    assert term["rumor"] and term["evidence"]["score"] < intel.FAST_EVIDENCE and not term["fast"]
    assert term["claim"]["status"] == "unsupported" and "financial scandal" in term["claim"]["kinds"] or term["claim"]["status"] == "unsupported"
    assert not list(db.scalars(select(TtPost).where(TtPost.status.in_(("queued", "dry_run", "published")))))
    out = feed.x_overview(db, get_settings())
    n = next(x for x in out["narratives"] if x["id"] == t.id)
    assert n["x"]["posts"] == 4 and n["x"]["copies"] >= 1 and n["x"]["independent"] < 4  # near-identical copies aren't independent
    assert n["x"]["velocity"]["basis"].startswith("X public counts")
    assert any(k.startswith("terminal:x:") or k.startswith("terminal:rumor:") for k, _ in alerts_log)


async def test_a_tier_a_post_plus_news_reporting_is_verified_and_can_publish(db: Session, xweb: XWeb) -> None:
    turn_on(db)
    xweb.x_posts = [post(10, "Reuters", "Crypto exchange Bybaz says hackers drained $120 million from hot wallet", 12),
                    post(11, "zachxbt", HACK, 20)]
    story(db, "Crypto exchange Bybaz says hackers drained $120 million from hot wallet", "CoinDesk", 1, NOW, 10)
    story(db, "Bybaz exchange hacked for $120 million, company says", "The Block", 2, NOW, 8)
    await tick(db, xweb)
    t = max(db.scalars(select(TtTopic)), key=lambda t: len(t.signal_ids or []))
    ev = t.parts["terminal"]["evidence"]
    assert not ev["rumor"] and ev["level"] in ("confirmed", "corroborated")
    outlets = intel.evidence(t.title, t.sources, t.parts)["outlets"]
    assert outlets == len({intel.outlet_of(x) for x in t.sources if x.get("reliable")})  # @Reuters counted once, as Reuters
    post_row = db.scalars(select(TtPost).where(TtPost.topic_id == t.id)).first()
    assert post_row is not None and post_row.status in ("queued", "dry_run", "published", "pending"), (post_row.status, post_row.hold_reason)


def test_reuters_on_x_and_the_reuters_feed_are_one_outlet() -> None:
    srcs = [{"source": "x_accounts", "outlet": "@Reuters", "title": "t", "reliable": True, "extra": {"tier": "A"}},
            {"source": "news", "outlet": "Reuters", "title": "t", "reliable": True, "extra": {}}]
    assert intel.evidence("t", srcs, {})["outlets"] == 1


def test_tier_b_alone_never_verifies() -> None:
    srcs = [{"source": "x_accounts", "outlet": f"@{h}", "title": HACK, "reliable": False, "extra": {"tier": "B"}} for h in ("zachxbt", "PeckShieldAlert", "CertiKAlert")]
    ev = intel.evidence(HACK, srcs, {})
    assert ev["rumor"] and ev["score"] < intel.FAST_EVIDENCE and any("tier-B" in r for r in ev["reasons"])


def test_coordinated_amplification_is_flagged() -> None:
    from app.newsdesk import checks

    text = "BREAKING: Major bank collapses, depositors locked out"
    posts = [{"handle": h, "tier": "C", "at": (NOW - timedelta(minutes=m)).isoformat(), "fp": checks.fingerprint(text), "post_id": str(i)}
             for i, (h, m) in enumerate((("a1", 9), ("a2", 7), ("a3", 5), ("a4", 4)))]
    r = xintel.independence(posts)
    assert r["coordinated"] and r["independent"] == 1 and r["copies"] == 3


def test_disputed_claims_are_marked() -> None:
    srcs = [{"source": "x_accounts", "outlet": "@a", "title": "Pentagon covered up a drone incident over Nevada", "reliable": False, "extra": {"tier": "C"}},
            {"source": "news", "outlet": "Reuters", "title": "Pentagon denies drone incident cover-up report", "reliable": True, "extra": {}}]
    ev = intel.evidence(srcs[0]["title"], srcs, {})
    c = intel.claim_status(srcs[0]["title"], srcs, ev)
    assert c["status"] == "disputed" and "cover-up" in c["kinds"] and c["disputes"][0]["outlet"] == "Reuters"


def test_emerging_terms_need_several_accounts_and_a_jump() -> None:
    posts = [{"handle": h, "at": (NOW - timedelta(minutes=5)).isoformat(), "text": "Bybaz withdrawals frozen #Bybaz $BYZ", "tags": xintel.tags("$BYZ #Bybaz")}
             for h in ("a", "b", "c", "d")]
    posts += [{"handle": "a", "at": (NOW - timedelta(minutes=m)).isoformat(), "text": "$BYZ", "tags": xintel.tags("$BYZ")} for m in (5, 6, 7, 8, 9)]  # one account repeating
    terms = {e["term"]: e for e in xintel.emerging(posts, NOW)}
    assert "$BYZ" in terms and terms["$BYZ"]["accounts"] == 4 and "#bybaz" in terms
    solo = [{"handle": "a", "at": (NOW - timedelta(minutes=m)).isoformat(), "text": "$ZZZ", "tags": xintel.tags("$ZZZ")} for m in range(1, 10)]
    assert not xintel.emerging(solo, NOW)


def test_hooks_are_breaking_viral_curiosity_and_gated() -> None:
    t = "Crypto exchange Bybaz says hackers drained $120 million from hot wallet"
    ev = {"rumor": False, "level": "corroborated"}
    hooks = intel.rule_hooks(t, "Reuters", "crypto", ev, 80, None)
    assert [h["kind"] for h in hooks] == ["breaking", "viral", "curiosity"]
    for h in hooks:
        assert intel.check_hook(h["text"], source_text=t, outlet="Reuters", reliable_count=2, breaking_ok=True)["ok"], h


def test_velocity_uses_only_xs_counts() -> None:
    p = [{"handle": "a", "at": (NOW - timedelta(minutes=10)).isoformat(), "engagement": 100.0, "history": []},
         {"handle": "b", "at": (NOW - timedelta(minutes=30)).isoformat(), "engagement": 50.0,
          "history": [[(NOW - timedelta(minutes=10)).isoformat(), 20.0], [NOW.isoformat(), 50.0]]}]
    v = xintel.velocity(p, NOW)
    assert v["per_min"] == pytest.approx(100 / 10 + 30 / 10) and v["between_reads"] == 1


async def test_the_x_api_route_shows_access_watchlist_and_estimate(db: Session, client: TestClient) -> None:
    connect_x_user(db)
    r = client.get("/api/terminal/x")
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["enabled"] is False and b["access"]["plan"].startswith("pay-per-use") and b["access"]["prices"]["post_read"] == 0.005
    assert len(b["watchlist"]) == len(xintel.DEFAULT_WATCHLIST) and b["estimate"]["usd_per_day"] > 0
    r = client.put("/api/terminal/x/watchlist", json={"accounts": [{"handle": "Reuters", "group": "breaking", "tier": "A", "note": ""}]})
    assert r.status_code == 200 and [a["handle"] for a in r.json()["watchlist"]] == ["Reuters"]
    r = client.put("/api/terminal/x/settings", json={"on": True})
    assert r.status_code == 400
    r = client.put("/api/terminal/x/settings", json={"on": True, "confirm_paid": True, "daily_usd": 1.5})
    assert r.status_code == 200 and r.json()["enabled"] is True and r.json()["daily_usd"] == 1.5



# --- the pilot (owner, 2026-11-18: $30 a month and $5 a day across all X activity; X Intelligence $1 a day) -----------------------------


def _budget(monkeypatch: pytest.MonkeyPatch) -> None:
    s = get_settings()
    monkeypatch.setattr(s, "x_daily_budget_usd", 5.0)
    monkeypatch.setattr(s, "x_monthly_budget_usd", 30.0)


def _spend(db: Session, day_cost: float, month_cost: float, writes: float = 0.15) -> None:
    from app.connectors.state import get_connector_state
    from app.models.enums import Platform
    from app.social.budget import _day

    st = get_connector_state(db, Platform.X)
    today = _day()
    earlier = (utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")
    usage = {today: {"requests": 10, "posts": 10, "users": 0, "cost": day_cost, "writes": 5, "write_cost": writes}}
    if month_cost > day_cost and earlier[:7] == today[:7]:
        usage[earlier] = {"requests": 10, "posts": 10, "users": 0, "cost": round(month_cost - day_cost, 4), "writes": 5, "write_cost": writes}
    st.usage = usage
    db.commit()


async def test_the_pilot_starts_once_when_the_budget_supports_it(db: Session, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    import logging

    connect_x_user(db)
    _budget(monkeypatch)
    _spend(db, 0.1, 1.47)
    web = XWeb(NOW)
    with caplog.at_level(logging.INFO, logger="app.trending.engine"):
        await tick(db, web)
    from app.trending import sources

    o = sources.merged(engine.settings_row(db).sources)["x_accounts"]
    assert o["on"] is True and o["daily_usd"] == 1.0
    on = {a["handle"].lower() for a in o["accounts"] if a["on"]}
    assert on == xintel.PILOT and "reuters" not in on and "deitaone" not in on  # high-signal only: no wires (RSS has them), no firehoses
    assert any(r.getMessage().startswith("x pilot: STARTED") for r in caplog.records)
    feed.x_settings(db, on=False)  # the owner turns it off: it stays off
    await tick(db, web, NOW + timedelta(minutes=6))
    assert sources.merged(engine.settings_row(db).sources)["x_accounts"]["on"] is False


async def test_the_pilot_does_not_start_without_room_in_the_month(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    connect_x_user(db)
    _budget(monkeypatch)
    _spend(db, 0.1, 25.0)
    await tick(db, XWeb(NOW))
    from app.trending import sources

    assert sources.merged(engine.settings_row(db).sources)["x_accounts"]["on"] is False
    assert "left this month" in engine.settings_row(db).state["x_intel"]["pilot"]["budget"]


async def test_paid_reads_stop_before_they_could_touch_the_posting_reserve(db: Session, xweb: XWeb) -> None:
    turn_on(db)
    _spend(db, 3.45, 3.45)  # $5 a day - $1.50 reserve = $3.50: one more search ($0.10 worst case) would cut into posting
    xweb.x_posts = [post(10, "zachxbt", HACK, 20)]
    await tick(db, xweb)
    assert xweb.queries == []
    xi = engine.settings_row(db).state["x_intel"]
    assert "protect X posting" in (xi["last_read"]["problem"] or "") and xi["paused"]["why"]
    from app.social.budget import PlatformBudget

    PlatformBudget(db, get_settings()).check(0.2)  # a post still fits under the account guard


def test_pilot_metrics_measure_lead_verification_and_false_positives() -> None:
    xi: dict[str, Any] = {"pilot": {"started": (NOW - timedelta(hours=10)).isoformat()}}
    xintel.pilot_day(xi, NOW, usd=0.4, posts=40, new=38, dupes=2)

    class T:
        def __init__(self, i: int, title: str, srcs: list[dict[str, Any]], rumor: bool, level: str) -> None:
            self.id, self.title, self.sources, self.status = i, title, srcs, "active"
            self.parts = {"terminal": {"evidence": {"rumor": rumor, "level": level}, "claim": {"status": "confirmed" if not rumor else "unsupported"}}}

    at = lambda m: (NOW - timedelta(minutes=m)).isoformat()  # noqa: E731
    lead = T(1, "Bybaz drained", [{"source": "x_accounts", "outlet": "@zachxbt", "at": at(60)}, {"source": "news", "outlet": "CoinDesk", "at": at(38)}], False, "corroborated")
    flop = T(2, "Bank collapse rumour", [{"source": "x_accounts", "outlet": "@a", "at": at(500)}], True, "unverified")
    late = T(3, "Fed holds", [{"source": "news", "outlet": "Reuters", "at": at(90)}, {"source": "x_accounts", "outlet": "@federalreserve", "at": at(80)}], False, "confirmed")
    xintel.pilot_ledger(xi, [lead, flop, late], NOW)
    m = xintel.pilot_metrics(xi, NOW, {1})
    assert m["spend_usd"] == 0.4 and m["unique_posts"] == 38 and m["duplicates_returned"] == 2
    assert m["x_led"] == 2 and m["before_other_sources"] == 1 and m["lead_min"]["median"] == 22
    assert m["useful_discoveries"] == 1 and m["verified_published"] == 1 and m["false_positives"] == 1 and m["cost_per_useful"] == 0.4


def test_posts_from_before_the_pilot_never_count_as_discoveries() -> None:
    """Production 2026-11-18 09:10: the first read returned a post from the day before, and the ledger credited X with a 408-minute lead."""
    xi: dict[str, Any] = {"pilot": {"started": NOW.isoformat()}}

    class T:
        id, title, status, parts = 7, "Quad9 withdraws from France", "active", {"terminal": {"evidence": {"rumor": False, "level": "corroborated"}}}
        sources = [{"source": "x_accounts", "outlet": "@a", "at": (NOW - timedelta(hours=18)).isoformat()},
                   {"source": "news", "outlet": "The Register", "at": (NOW - timedelta(hours=11)).isoformat()}]

    xintel.pilot_ledger(xi, [T()], NOW)
    m = xintel.pilot_metrics(xi, NOW, set())
    assert m["stories"] == 0 and m["x_led"] == 0 and m["useful_discoveries"] == 0


class RenamedWeb(XWeb):
    """X's answer when none of the looked-up accounts exists: 200 with errors only (no "data"), which the client raises as not found."""

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.x.com" and request.url.path == "/2/users/by":
            names = dict(request.url.params)["usernames"].split(",")
            self.lookups.append(names)
            data = [{"id": USERS[n], "username": n} for n in names if n in USERS]
            errors = [{"value": n, "detail": f"Could not find user with usernames: [{n}].", "title": "Not Found Error",
                       "type": "https://api.twitter.com/2/problems/resource-not-found"} for n in names if n not in USERS]
            return httpx.Response(200, json={**({"data": data} if data else {}), **({"errors": errors} if errors else {})})
        return super().handler(request)


async def test_one_renamed_account_does_not_stop_the_reads(db: Session, xweb: XWeb) -> None:
    """2026-11-26/27 production: @DeptofDefense no longer exists; looking it up alone raised not-found and every hourly read stopped there
    (0 posts read for days). It is now marked missing (asked again in a week) and the other accounts are read."""
    web = RenamedWeb(NOW)
    turn_on(db)
    await tick(db, web)  # every watched account is known after this
    turn_on(db, accounts=[*WATCH, {"handle": "DeptofDefense", "group": "geopolitics", "tier": "A"}])
    web.x_posts = [post(20, "zachxbt", HACK, 5)]
    await tick(db, web, NOW + timedelta(minutes=6))
    assert web.lookups[-1] == ["DeptofDefense"]
    st = engine.settings_row(db).state["x_intel"]
    assert "deptofdefense" in st["missing"]
    assert db.scalars(select(TtSignal).where(TtSignal.external_id == "xpost:20")).first() is not None  # the others were still read
    await tick(db, web, NOW + timedelta(minutes=12))
    assert web.lookups[-1] == ["DeptofDefense"] and len([x for x in web.lookups if x == ["DeptofDefense"]]) == 1  # not asked again for a week
