"""Opportunity screening robustness and the Analytics › Growth dashboard.

All X traffic goes to the in-memory FakeX and all model output comes from FakeProvider.
"""

import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.structured import salvage_items
from app.analytics import growth
from app.analytics.snapshots import MIN_GAP, record, record_x_account, x_snapshot_due
from app.config.settings import get_settings
from app.connectors.state import get_connector_state
from app.db.base import utcnow
from app.models.enums import ApprovalStatus, Platform, SocialAction
from app.models.social import SocialPost
from app.models.social_intel import ApprovalRequest, GrowthSnapshot, SocialActionLog, SocialPostMetric
from tests.conftest import FakeProvider, connect_x_user
from tests.fakes import OUR_ID, FakeTelegram, FakeX
from tests.test_x_social import connect

# --- opportunity screening ----------------------------------------------------------------------------------


class _It(BaseModel):
    id: str
    relevant: bool


def test_salvage_recovers_complete_items_only() -> None:
    full = json.dumps({"items": [{"id": "p1", "relevant": True}, {"id": "p2", "relevant": False}, {"id": "p3", "relevant": True}]})
    cut = full[: full.index('"p3"') + 6]  # ends inside the third object
    assert [i.id for i in salvage_items(cut, "items", _It)] == ["p1", "p2"]
    assert [i.id for i in salvage_items('{"items": [{"id": "p1", "relevant": true}, {"id": 5}, {"id": "p2", "relevant": false}]}', "items", _It)] == ["p1", "p2"]
    assert salvage_items("no json here", "items", _It) == []
    assert salvage_items('{"items": [', "items", _It) == []


def _many_posts(c: TestClient, fake_x: FakeX, n: int) -> None:
    connect(c)
    c.post("/api/x/rules", json={"name": "Funding", "type": "keyword", "query": "funding", "max_results": 100})
    for i in range(n):
        fake_x.tweet("2001" if i % 2 else "2002", f"funding rates update number {i}: open interest and basis are moving")
    c.post("/api/x/sync")


def _batch_sizes(fp: FakeProvider) -> list[int]:
    sizes = []
    for msgs, opts in fp.calls:
        if opts and opts.response_schema and opts.response_schema.get("title") == "_Batch":
            sizes.append(sum(1 for line in msgs[-1].content.splitlines() if line.startswith("[p")))
    return sizes


def test_screening_uses_small_batches(x_client: TestClient, fake_x: FakeX, fake_provider: FakeProvider) -> None:
    _many_posts(x_client, fake_x, 25)
    fake_provider.calls.clear()
    r = x_client.post("/api/jobs/x.opportunities/run").json()
    assert r["status"] == "success"
    res = r["result"]
    assert res["screened"] == 25 and res["unscreened"] == 0 and res["model_calls"] == 3 and res["created"] > 0
    assert _batch_sizes(fake_provider) == [10, 10, 5]


def test_truncated_output_is_salvaged_and_the_rest_rescreened(x_client: TestClient, fake_x: FakeX, fake_provider: FakeProvider) -> None:
    _many_posts(x_client, fake_x, 10)

    def cut_long_batches(title: str, text: str) -> str:
        if title == "_Batch" and len(json.loads(text)["items"]) > 4:
            return text[: int(len(text) * 0.6)]  # the answer stops mid-way, like hitting the output limit
        return text

    fake_provider.mangle = cut_long_batches
    res = x_client.post("/api/jobs/x.opportunities/run").json()["result"]
    assert res["screened"] == 10 and res["unscreened"] == 0 and res["salvaged"] >= 4
    assert res["model_calls"] <= 4 and res["created"] > 0


def test_malformed_output_does_not_fail_the_run(x_client: TestClient, fake_x: FakeX, fake_provider: FakeProvider) -> None:
    _many_posts(x_client, fake_x, 10)
    fake_provider.mangle = lambda title, text: "Sure! Here are my thoughts..." if title == "_Batch" else text
    r = x_client.post("/api/jobs/x.opportunities/run").json()
    assert r["status"] == "success"
    res = r["result"]
    assert res["unscreened"] == 10 and res["created"] == 0 and res["model_calls"] < 20  # bounded splitting, nothing guessed
    fake_provider.mangle = None
    assert x_client.post("/api/jobs/x.opportunities/run").json()["result"]["screened"] == 0  # no endless re-screening


def test_bad_advice_for_one_post_skips_only_that_post(x_client: TestClient, fake_x: FakeX, fake_provider: FakeProvider) -> None:
    _many_posts(x_client, fake_x, 3)
    seen = {"n": 0}

    def break_first_advice(title: str, text: str) -> str:
        if title == "_Advice":
            seen["n"] += 1
            if seen["n"] <= 2:  # first attempt and its retry
                return "{not json"
        return text

    fake_provider.mangle = break_first_advice
    res = x_client.post("/api/jobs/x.opportunities/run").json()["result"]
    assert res["advice_failed"] == 1 and res["created"] == 2


# --- snapshots ------------------------------------------------------------------------------------------------------


def test_follower_snapshots_dedupe_and_due(db: Session) -> None:
    now = utcnow()
    user = {"id": OUR_ID, "username": "pricechat", "public_metrics": {"followers_count": 1200, "following_count": 80, "tweet_count": 300}}
    assert record_x_account(db, user, "x_response", at=now) is not None
    assert record_x_account(db, user, "x_response", at=now + timedelta(minutes=10)) is None  # same values, too soon
    user["public_metrics"]["followers_count"] = 1201
    assert record_x_account(db, user, "x_response", at=now + timedelta(minutes=11)) is not None  # changed → kept
    assert record_x_account(db, {**user, "public_metrics": {}}, "x_response") is None  # nothing known → nothing stored
    assert record(db, "x_account", OUR_ID, source="t", at=now + MIN_GAP + timedelta(minutes=20), followers=1201) is not None
    db.commit()
    assert not x_snapshot_due(db, OUR_ID, now + timedelta(hours=1)) and x_snapshot_due(db, OUR_ID, now + timedelta(hours=8))
    assert x_snapshot_due(db, None) is False


def test_x_reads_record_followers_without_extra_requests(x_client: TestClient, fake_x: FakeX, db: Session) -> None:
    connect(x_client)  # the connection test's account lookup is the first snapshot
    snaps = list(db.scalars(select(GrowthSnapshot).where(GrowthSnapshot.kind == "x_account")))
    assert len(snaps) == 1 and snaps[0].followers == 1200 and snaps[0].subject == OUR_ID
    fake_x.calls.clear()
    x_client.post("/api/x/sync")
    me_calls = [c for c in fake_x.calls if c[0].endswith("/users/me")]
    assert me_calls == []  # a fresh snapshot exists: no lookup just for the dashboard


def test_stale_snapshot_triggers_one_small_lookup(x_client: TestClient, fake_x: FakeX, db: Session) -> None:
    connect(x_client)
    for s in db.scalars(select(GrowthSnapshot)):
        s.taken_at = utcnow() - timedelta(hours=8)
    db.commit()
    fake_x.users[OUR_ID]["public_metrics"]["followers_count"] = 1234
    fake_x.calls.clear()
    x_client.post("/api/x/sync")
    assert len([c for c in fake_x.calls if c[0].endswith("/users/me")]) == 1
    db.expire_all()
    latest = db.scalar(select(GrowthSnapshot).order_by(GrowthSnapshot.taken_at.desc()))
    assert latest.followers == 1234 and latest.source == "users_me"
    fake_x.calls.clear()
    x_client.post("/api/x/sync")
    assert not [c for c in fake_x.calls if c[0].endswith("/users/me")]


def test_telegram_member_snapshots(x_client: TestClient, fake_telegram: FakeTelegram, db: Session) -> None:
    fake_telegram.add_chat(-100111, "Price Chat Community")
    fake_telegram.member_counts[-100111] = 321
    x_client.post("/api/telegram/sources", json={"chat_id": -100111})
    fake_telegram.message(-100111, "gm, what is everyone watching today?")
    x_client.post("/api/telegram/sync")
    r = x_client.post("/api/jobs/growth.snapshots/run").json()
    assert r["status"] == "success" and r["result"]["recorded"] == 1
    snap = db.scalar(select(GrowthSnapshot).where(GrowthSnapshot.kind == "telegram_chat"))
    assert snap.members == 321 and snap.messages == 1 and snap.label == "Price Chat Community"
    del fake_telegram.member_counts[-100111]  # can't be read → members unknown, not guessed
    fake_telegram.message(-100111, "another message")
    x_client.post("/api/telegram/sync")
    x_client.post("/api/jobs/growth.snapshots/run")
    latest = db.scalar(select(GrowthSnapshot).where(GrowthSnapshot.kind == "telegram_chat").order_by(GrowthSnapshot.id.desc()))
    assert latest.members is None and latest.messages == 2


# --- calculations ------------------------------------------------------------------------------------------------


def test_period_windows_and_change() -> None:
    now = utcnow()
    w = growth.window("7d", now)
    assert w.start == now - timedelta(days=7) and w.prev_start == now - timedelta(days=14)
    assert growth.window("all", now).start is None and growth.window("all", now).prev_start is None
    with pytest.raises(ValueError):
        growth.window("1y", now)
    assert growth.change(120, 100) == {"delta": 20, "pct": 20.0}
    assert growth.change(5, 0) == {"delta": 5, "pct": None}
    assert growth.change(None, 3) == {"delta": None, "pct": None}


def test_follower_change_never_backfills() -> None:
    now = utcnow()
    w = growth.window("7d", now)
    assert growth.follower_change([], w) == (None, None, "No follower snapshots yet")
    pts = [(now - timedelta(days=2), 100)]
    assert growth.follower_change(pts, w)[0] is None  # one point: no change
    pts = [(now - timedelta(days=2), 100), (now - timedelta(hours=1), 112)]
    ch, prev, note = growth.follower_change(pts, w)
    assert ch == 12 and prev is None and note.startswith("since snapshots began")
    pts = [(now - timedelta(days=15), 80), (now - timedelta(days=8), 90), (now - timedelta(days=1), 120)]
    assert growth.follower_change(pts, w) == (30, 10, "")
    assert growth.value_at(pts, now - timedelta(days=20)) is None


def _own(db: Session, ext: str, hours_ago: float, metrics: dict, reply_to: str | None = None) -> SocialPost:
    p = SocialPost(platform=Platform.X, external_id=ext, text=f"post {ext}", is_own=True, posted_at=utcnow() - timedelta(hours=hours_ago),
                   metrics=metrics, source_kinds=["own"], in_reply_to_external_id=reply_to, url=f"https://x.com/pricechat/status/{ext}")
    db.add(p)
    db.flush()
    return p


def test_dashboard_kpis_missing_metrics_and_comparison(db: Session, agent_config) -> None:  # type: ignore[no-untyped-def]
    connect_x_user(db)
    s = get_settings()
    _own(db, "a1", 400, {"likes": 1, "replies": 0, "reposts": 0, "impressions": 50})  # older: gives the 7d comparison coverage
    _own(db, "a2", 200, {"likes": 2, "replies": 1, "reposts": 0, "impressions": 100})  # previous 7d
    _own(db, "a3", 10, {"likes": 5, "replies": 2, "reposts": 1, "impressions": 200, "profile_visits": 4})
    _own(db, "a4", 5, {"likes": 1, "replies": 0}, reply_to="999")  # reply without impressions
    db.add(GrowthSnapshot(kind="x_account", subject="1600", taken_at=utcnow() - timedelta(days=9), followers=1000, source="t"))
    db.add(GrowthSnapshot(kind="x_account", subject="1600", taken_at=utcnow() - timedelta(hours=1), followers=1015, source="t"))
    db.commit()
    d = growth.dashboard(db, s, "7d")
    k = {x["key"]: x for x in d["kpis"]}
    assert k["followers"]["value"] == 1015 and k["follower_change"]["value"] == 15
    assert k["posts"]["value"] == 1 and k["replies"]["value"] == 1 and k["posts"]["previous"] == 1
    assert k["impressions"]["value"] == 200 and k["impressions"]["previous"] == 100 and k["impressions"]["change"] == {"delta": 100, "pct": 100.0}
    assert k["engagements"]["value"] == 9  # 8 + 1 on the reply without impressions
    assert k["engagement_rate"]["value"] == 0.04  # 8 / 200: only posts with impressions count
    assert k["profile_visits"]["value"] == 4
    assert {x["key"] for x in d["kpis"]} >= {"followers", "follower_change", "impressions", "engagements", "engagement_rate", "posts",
                                             "replies", "likes", "reposts", "follows", "profile_visits", "api_spend"}
    # 24h: nothing published with impressions → unavailable, never zero-filled
    d24 = {x["key"]: x for x in growth.dashboard(db, s, "24h")["kpis"]}
    assert d24["impressions"]["value"] == 200
    empty = {x["key"]: x for x in growth.dashboard(db, s, "24h", now=utcnow() + timedelta(days=60))["kpis"]}
    assert empty["impressions"]["available"] is False and empty["impressions"]["value"] is None
    assert empty["profile_visits"]["available"] is False


def test_chart_timeline_and_content_performance(db: Session, agent_config) -> None:  # type: ignore[no-untyped-def]
    connect_x_user(db)
    p = _own(db, "c1", 30, {"likes": 9, "replies": 3, "reposts": 2, "bookmarks": 1, "impressions": 300})
    _own(db, "c2", 20, {"likes": 1, "impressions": 40})
    for h, imps in ((1, 50), (6, 150), (24, 280)):
        db.add(SocialPostMetric(post_id=p.id, captured_at=utcnow(), source=f"checkpoint_{h}h", impressions=imps, likes=h))
    appr = ApprovalRequest(platform=Platform.X, action=SocialAction.POST, payload={"text": "post c1", "original_text": "draft", "model": "gemma3:27b",
                                                                                     "topic": "Funding rates"},
                           status=ApprovalStatus.EXECUTED, decided_at=utcnow() - timedelta(hours=31), execution_result={"x_id": "c1"})
    db.add(appr)
    db.flush()
    db.add(SocialActionLog(platform=Platform.X, action=SocialAction.LIKE, target_external_id="777", target_account="bigtrader",
                           outcome="executed", approval_id=None, created_at=utcnow() - timedelta(hours=3)))
    db.add(SocialActionLog(platform=Platform.X, action=SocialAction.LIKE, target_external_id="778", target_account="bigtrader",
                           outcome="dry_run", created_at=utcnow() - timedelta(hours=2)))
    db.commit()
    d = growth.dashboard(db, get_settings(), "7d")
    assert [t["title"] for t in d["timeline"]] == ["Liked @bigtrader's post"]  # dry runs are not actions
    ch = d["chart"]
    assert ch["step"] == "day" and len(ch["buckets"]) == 7 and sum(ch["posts"]) == 2 and sum(ch["likes"]) == 1
    assert sum(v or 0 for v in ch["impressions"]) == 340 and all(v is None for v in ch["followers"])  # no snapshots → no line
    top = d["content"][0]
    assert top["id"] == p.id and top["engagements"] == 15 and top["engagement_rate"] == 0.05
    assert top["via_price_chat"] is True and top["ai_drafted"] is True and top["human_edited"] is True and top["topic"] == "Funding rates"
    assert top["checkpoints"]["6h"]["impressions"] == 150 and d["content"][1]["checkpoints"]["1h"] is None
    assert d["content"][1]["via_price_chat"] is False and d["content"][1]["ai_drafted"] is None


def test_ai_performance_and_trust(db: Session, agent_config) -> None:  # type: ignore[no-untyped-def]
    connect_x_user(db)
    now = utcnow()
    rows = [
        (SocialAction.REPLY, ApprovalStatus.EXECUTED, {"model": "m", "text": "a"}),
        (SocialAction.REPLY, ApprovalStatus.APPROVED, {"model": "m", "text": "b", "original_text": "x"}),
        (SocialAction.REPLY, ApprovalStatus.REJECTED, {"model": "m", "text": "c"}),
        (SocialAction.LIKE, ApprovalStatus.EXECUTED, {}),
        (SocialAction.LIKE, ApprovalStatus.PENDING, {}),
    ]
    for action, st, payload in rows:
        db.add(ApprovalRequest(platform=Platform.X, action=action, payload=payload, status=st,
                               decided_at=None if st == ApprovalStatus.PENDING else now - timedelta(hours=1)))
    db.add(SocialActionLog(platform=Platform.X, action=SocialAction.REPLY, outcome="failed", reason="x"))
    db.commit()
    ai = growth.dashboard(db, get_settings(), "7d")["ai"]
    assert ai["proposals"] == 5 and ai["approved"] == 3 and ai["rejected"] == 1 and ai["pending"] == 1
    assert ai["ai_drafts_reviewed"] == 3 and ai["ai_acceptance_rate"] == round(2 / 3, 3) and ai["human_edit_rate"] == 0.5
    assert ai["likes"]["approved"] == 1 and ai["likes"]["proposed"] == 2 and ai["failed_or_blocked"] == 1
    assert ai["trust_trend"][-1]["reviewed"] == 3 and ai["trust_trend"][-1]["approved_unchanged_rate"] == round(1 / 3, 3)


def test_cost_calculations(db: Session, agent_config) -> None:  # type: ignore[no-untyped-def]
    connect_x_user(db)
    now = utcnow()
    state = get_connector_state(db, Platform.X)
    day = lambda n: (now - timedelta(days=n)).strftime("%Y-%m-%d")  # noqa: E731
    state.usage = {day(0): {"cost": 1.5, "requests": 30}, day(3): {"cost": 0.5, "requests": 10}, day(20): {"cost": 2.0, "requests": 40},
                   day(45): {"cost": 9.0, "requests": 100}}
    db.add(ApprovalRequest(platform=Platform.X, action=SocialAction.LIKE, payload={}, status=ApprovalStatus.EXECUTED, decided_at=now))
    db.commit()
    c = growth.cost_section(db, get_settings(), now)
    assert c["today"]["cost"] == 1.5 and c["d7"]["cost"] == 2.0 and c["d30"]["cost"] == 4.0 and c["d30"]["requests"] == 80
    assert c["per_approved_action_30d"] == 4.0 and c["approved_actions_30d"] == 1
    assert c["per_useful_opportunity_30d"] is None  # no opportunities → not calculable, not zero
    k = {x["key"]: x for x in growth.dashboard(db, get_settings(), "7d", now=now)["kpis"]}
    assert k["api_spend"]["value"] == 2.0 and k["api_spend"]["previous"] == 0.0  # usage tracked back to day 45


def test_relationships_and_telegram_section(x_client: TestClient, fake_x: FakeX, fake_telegram: FakeTelegram, db: Session) -> None:
    from tests.test_x_likes import _decide, _live, _propose, _setup

    c = _setup(x_client, fake_x)[0]
    _live(x_client)
    _decide(x_client, _propose(x_client, c["opportunity_id"])["id"])
    fake_telegram.add_chat(-100111, "Price Chat Community")
    x_client.post("/api/telegram/sources", json={"chat_id": -100111})
    fake_telegram.message(-100111, "What does Price Chat actually do?")
    x_client.post("/api/telegram/sync")
    d = x_client.get("/api/analytics/growth", params={"period": "24h"}).json()
    rel = d["relationships"][0]
    assert rel["account"] == "bigtrader" and rel["likes_from_us"] == 1 and rel["history"][0]["action"] == "like" and "funding" in rel["topics"]
    assert d["timeline"][0]["title"] == "Liked @bigtrader's post" and d["timeline"][0]["url"]
    tg = d["telegram"]
    assert tg["connected_sources"] == 1 and tg["messages"] >= 0 and "member_note" in tg
    assert x_client.get("/api/analytics/growth", params={"period": "1y"}).status_code == 422


def test_dashboard_never_calls_x(x_client: TestClient, fake_x: FakeX) -> None:
    connect(x_client)
    fake_x.calls.clear()
    writes = len(fake_x.writes)
    for period in ("24h", "7d", "30d", "all"):
        d = x_client.get("/api/analytics/growth", params={"period": period}).json()
        assert d["period"] == period and d["not_provided"] and "does not show the action caused it" in d["disclaimer"]
    assert fake_x.calls == [] and len(fake_x.writes) == writes


def test_dashboard_loads_only_the_period_s_posts_and_actions(db: Session, agent_config, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    from sqlalchemy import event

    connect_x_user(db)
    for i in range(30):  # a year of history the 7d view doesn't need
        _own(db, f"old{i}", 24 * (30 + 10 * i), {"likes": 1})
        db.add(SocialActionLog(platform=Platform.X, action=SocialAction.LIKE, target_external_id=f"o{i}", target_account="x",
                               outcome="executed", created_at=utcnow() - timedelta(days=30 + 10 * i)))
    _own(db, "new", 5, {"likes": 3, "impressions": 30})
    db.add(SocialActionLog(platform=Platform.X, action=SocialAction.LIKE, target_external_id="n", target_account="x",
                           outcome="executed", created_at=utcnow() - timedelta(hours=2)))
    db.commit()
    loaded: list[int] = []
    real = growth._own_posts
    monkeypatch.setattr(growth, "_own_posts", lambda db_, since=None: loaded.append(len(r := real(db_, since))) or r)
    action_sql: list[str] = []
    spy = lambda *a: action_sql.append(a[2]) if "FROM social_actions" in a[2] and "executed" in str(a[3]) else None  # noqa: E731
    event.listen(db.get_bind(), "before_cursor_execute", spy)
    try:
        d = growth.dashboard(db, get_settings(), "7d")
    finally:
        event.remove(db.get_bind(), "before_cursor_execute", spy)
    everything = growth.dashboard(db, get_settings(), "all")
    assert loaded == [1, 31]  # 7d (+ its comparison week) reads one post; "all" still reads them all
    assert sum(d["chart"]["likes"]) == 1 and sum(everything["chart"]["likes"]) == 31
    assert action_sql and all("created_at >=" in q for q in action_sql)  # bounded in SQL for a fixed period
