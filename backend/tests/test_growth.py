"""Supervised social growth pilot: early community signals, unified feed, quality filter, review sessions,
variants, skip, learning controls, analytics, usage, creator discovery, performance checkpoints.

All external APIs are fakes: no real X write, no Telegram message is ever sent.
"""

import time
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.db.session import SessionLocal
from app.models.social import SocialPost
from app.models.social_intel import SocialActionLog, SocialPostMetric
from app.social.cross_signals import _early
from app.social.growth import WEIGHTS
from tests.conftest import FakeProvider
from tests.fakes import OUR_ID, FakeTelegram, FakeX
from tests.test_x_operator import _approve, _live, _opportunity
from tests.test_x_social import connect

FUNDING = ["funding rates are spiking on bitcoin today", "bitcoin funding rates are spiking again",
           "why are funding rates spiking on bitcoin", "funding rates on bitcoin are spiking hard today"]


def _telegram_topic(c: TestClient, tg: FakeTelegram, minutes_ago: int = 10) -> None:
    c.post("/api/telegram/sources", json={"chat_id": -100111})
    now = int(time.time())
    for i, t in enumerate(FUNDING):
        tg.message(-100111, t, user_id=10 + i, date=now - 60 * minutes_ago)
    c.post("/api/telegram/sync")
    c.post("/api/jobs/community.process/run")
    c.post("/api/jobs/community.topics/run")


# --- early community signals ------------------------------------------------------------------------------


def test_early_signal_needs_lead_and_prior_monitoring() -> None:
    now = datetime(2026, 10, 3, 12, tzinfo=timezone.utc)
    tg = now - timedelta(hours=5)
    x = [now - timedelta(hours=2), now - timedelta(hours=1)]
    assert _early(tg, x, now - timedelta(days=3), now) == (True, 180)  # Telegram 3 h before X, X already monitored
    assert _early(tg, x, now - timedelta(hours=4), now) == (False, None)  # monitoring started after Telegram: no claim
    assert _early(tg, [now - timedelta(hours=4, minutes=30)], now - timedelta(days=3), now) == (False, None)  # < 60 min lead
    assert _early(now - timedelta(days=5), x, now - timedelta(days=9), now) == (False, None)  # old topic: outside the window


# --- feed, ranking, quality ---------------------------------------------------------------------------------


def test_feed_labels_sources_and_exposes_separate_signals(x_client: TestClient, fake_x: FakeX, fake_telegram: FakeTelegram) -> None:
    _telegram_topic(x_client, fake_telegram)
    x_client.post("/api/jobs/content.opportunities/run")
    o = _opportunity(x_client, fake_x)
    feed = x_client.get("/api/growth/feed").json()
    kinds = {i["kind"] for i in feed}
    assert {"x", "telegram"} <= kinds
    x_item = next(i for i in feed if i["key"] == f"x:{o['id']}")
    assert set(x_item["signals"]) == set(WEIGHTS) and x_item["label"] in ("high", "medium", "low")
    assert x_item["source"] in ("X", "Telegram + X") and x_item["evidence"] and x_item["suggested_actions"][0] == "X reply"
    tg_item = next(i for i in feed if i["kind"] == "telegram")
    assert tg_item["source"] in ("Telegram", "Telegram + X") and "X standalone post" in tg_item["suggested_actions"]
    assert tg_item["evidence"][0].startswith("Telegram:")


def test_quality_filter(x_client: TestClient, fake_x: FakeX, db: Session) -> None:
    o = _opportunity(x_client, fake_x)
    item = next(i for i in x_client.get("/api/growth/feed").json() if i["key"] == f"x:{o['id']}")
    assert item["passed"] is True
    # same account engaged recently → filtered
    db.add(SocialActionLog(platform="x", action="like", target_external_id="1", target_account="bigtrader", outcome="executed", reason=""))
    db.commit()
    item = next(i for i in x_client.get("/api/growth/feed").json() if i["key"] == f"x:{o['id']}")
    assert item["passed"] is False and any("already engaged" in p for p in item["problems"])
    # stale conversations are filtered too
    with SessionLocal() as s:
        post = s.scalar(select(SocialPost).where(SocialPost.external_id == o["post"]["external_id"]))
        post.posted_at = utcnow() - timedelta(days=3)
        s.commit()
    item = next(i for i in x_client.get("/api/growth/feed").json() if i["key"] == f"x:{o['id']}")
    assert any("stale" in p for p in item["problems"])
    assert all(i["key"] != f"x:{o['id']}" for i in x_client.get("/api/growth/feed", params={"include_filtered": "false"}).json())


def test_review_session_is_small_and_diverse(x_client: TestClient, fake_x: FakeX) -> None:
    connect(x_client)
    x_client.post("/api/x/rules", json={"name": "Funding", "type": "keyword", "query": "funding", "max_results": 10})
    for i in range(4):
        fake_x.tweet("2001", f"funding question {i}: can anyone explain what this means for traders?", kind="search")
    fake_x.tweet("2002", "funding rates look odd, can someone explain the mechanics?", kind="search")
    x_client.post("/api/x/sync")
    x_client.post("/api/jobs/x.opportunities/run")
    session = x_client.post("/api/growth/review-session", json={"size": 5}).json()
    authors = [i["post"]["author"] for i in session["items"] if i["post"]]
    assert 1 <= len(session["items"]) <= 5 and authors.count("bigtrader") <= 2  # never flooded with one account
    assert all(i["passed"] for i in session["items"])
    assert x_client.post("/api/growth/review-session", json={"size": 50}).status_code == 422


# --- drafting, variants, skip --------------------------------------------------------------------------------


def test_prepare_x_item_with_variants_and_reuse(x_client: TestClient, fake_x: FakeX, fake_provider: FakeProvider) -> None:
    o = _opportunity(x_client, fake_x)
    key = f"x:{o['id']}"
    from app.social import growth

    original = growth._label
    growth._label = lambda p: "high"  # force the high-priority path (variants)
    try:
        a = x_client.post(f"/api/growth/prepare/{key}", json={}).json()
    finally:
        growth._label = original
    assert a["action"] == "reply" and a["status"] == "pending" and a["payload"]["origin"] in ("x", "telegram+x")
    assert [v["style"] for v in a["payload"]["variants"]] == ["direct", "conversational", "insight-driven"]
    assert a["payload"]["text"] == a["payload"]["variants"][0]["text"]
    again = x_client.post(f"/api/growth/prepare/{key}", json={}).json()
    assert again["id"] == a["id"]  # no duplicate drafting for the same opportunity

    skipped = x_client.post(f"/api/growth/skip/{a['id']}").json()
    assert skipped["status"] == "pending"
    assert x_client.get("/api/growth/analytics").json()["skipped"] == 1


def test_prepare_telegram_item_proposes_an_x_post(x_client: TestClient, fake_telegram: FakeTelegram) -> None:
    _telegram_topic(x_client, fake_telegram)
    x_client.post("/api/jobs/content.opportunities/run")
    item = next(i for i in x_client.get("/api/growth/feed").json() if i["kind"] == "telegram")
    a = x_client.post(f"/api/growth/prepare/{item['key']}", json={})
    assert a.status_code == 200, a.text
    a = a.json()
    assert a["action"] == "post" and a["payload"]["origin"] in ("telegram", "telegram+x") and len(a["payload"]["text"]) <= 280
    assert a["payload"]["context"][0].startswith("Telegram community:")


def test_telegram_context_reaches_x_replies_in_aggregate(x_client: TestClient, fake_x: FakeX, fake_telegram: FakeTelegram,
                                                        fake_provider: FakeProvider) -> None:
    _telegram_topic(x_client, fake_telegram)
    for i in range(2):
        fake_telegram.message(-100111, "why are funding rates so high right now?", user_id=40 + i)
    x_client.post("/api/telegram/sync")
    x_client.post("/api/jobs/community.process/run")
    x_client.post("/api/jobs/community.topics/run")
    connect(x_client)
    x_client.post("/api/x/rules", json={"name": "Funding", "type": "keyword", "query": "funding rates", "max_results": 10})
    ts = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
    fake_x.tweet("2001", "funding rates are spiking on bitcoin again today, can anyone explain?", created_at=ts)
    fake_x.tweet("2002", "bitcoin funding rates spiking hard today", created_at=ts)
    x_client.post("/api/x/sync")
    x_client.post("/api/jobs/cross.signals/run")
    x_client.post("/api/jobs/x.opportunities/run")
    o = next(o for o in x_client.get("/api/x/opportunities").json() if "explain" in o["post"]["text"])
    a = x_client.post(f"/api/x/opportunities/{o['id']}/draft-reply", json={}).json()
    ctx = "\n".join(a["payload"]["context"])
    assert "Telegram community signal" in ctx
    prompt = fake_provider.calls[-1][0][0].content
    assert "Never name, quote or identify individual community members" in prompt
    assert "Member" not in ctx  # no member names in the context


# --- learning controls ------------------------------------------------------------------------------------------


def _edits(c: TestClient) -> None:
    connect(c)
    for t, e in (("Great insight! Funding is high #btc right now and it matters", "Funding is high."),
                 ("Love this! Open interest is climbing #markets this week a lot", "Open interest is climbing.")):
        a = c.post("/api/approvals", json={"platform": "x", "action": "post", "text": t}).json()
        _approve(c, a["id"], text=e)


def test_learning_controls_accept_disable_reset(x_client: TestClient, fake_x: FakeX, fake_provider: FakeProvider) -> None:
    _edits(x_client)
    keys = {s["key"]: s for s in x_client.get("/api/growth/learning").json()}
    assert keys["edit:removed_hashtags"]["state"] == "active"

    x_client.put("/api/growth/learning/edit:removed_hashtags", json={"action": "disable"})
    o = _opportunity(x_client, fake_x)
    x_client.post(f"/api/x/opportunities/{o['id']}/draft-reply", json={})
    assert "Do not use hashtags." not in fake_provider.calls[-1][0][0].content  # disabled preferences are not used
    assert {s["key"]: s["state"] for s in x_client.get("/api/growth/learning").json()}["edit:removed_hashtags"] == "disabled"

    x_client.put("/api/growth/learning/edit:removed_hashtags", json={"action": "reset"})
    assert "edit:removed_hashtags" not in {s["key"] for s in x_client.get("/api/growth/learning").json()}  # old evidence ignored

    x_client.put("/api/growth/learning/edit:removed_emoji", json={"action": "accept"})
    pinned = {s["key"]: s for s in x_client.get("/api/growth/learning").json()}["edit:removed_emoji"]
    assert pinned["state"] == "accepted" and pinned["text"].startswith("Pinned by you")
    assert x_client.put("/api/growth/learning/x", json={"action": "explode"}).status_code == 422


# --- analytics, summary, usage, status ---------------------------------------------------------------------------


def test_analytics_summary_and_usage(x_client: TestClient, fake_x: FakeX) -> None:
    _edits(x_client)
    a = x_client.post("/api/approvals", json={"platform": "x", "action": "post", "text": "unchanged post"}).json()
    _approve(x_client, a["id"])
    r = x_client.post("/api/approvals", json={"platform": "x", "action": "post", "text": "bad post"}).json()
    x_client.post(f"/api/approvals/{r['id']}/decision", json={"decision": "rejected"})
    an = x_client.get("/api/growth/analytics").json()
    assert an["approved_unchanged"] == 1 and an["approved_after_edit"] == 2 and an["rejected"] == 1
    assert an["approval_rate"] == 0.75 and an["edit_rate"] == 0.5 and "cause" in an["note"]
    summary = x_client.get("/api/growth/daily-summary").json()
    assert summary["internal_only"] is True and "PRICE CHAT SOCIAL PILOT — TODAY" in summary["text"]
    assert "1 approved unchanged, 2 edited, 1 rejected" in summary["text"]
    o = _opportunity(x_client, fake_x)
    assert o
    u = x_client.get("/api/growth/usage").json()
    assert u["models"]["fast_calls"] >= 1 and u["models"]["strong_calls"] >= 1  # screening (fast) then analysis (strong)
    assert u["x"]["today"]["requests"] > 0 and "estimate" in u["x"]["note"]


def test_growth_status_never_claims_untested_readiness(x_client: TestClient, fake_x: FakeX) -> None:
    rows = {r["key"]: r for r in x_client.get("/api/growth/status").json()["rows"]}
    assert rows["x_read"]["status"] == "needs_setup" and rows["telegram"]["status"] == "needs_setup"
    assert rows["community_data"]["status"] == "waiting" and rows["automatic"]["status"] == "locked"
    connect(x_client)
    rows = {r["key"]: r for r in x_client.get("/api/growth/status").json()["rows"]}
    assert rows["x_read"]["status"] == "ready" and rows["x_user_auth"]["status"] == "ready"
    assert rows["x_execution"]["status"] == "dry_run"


# --- creators & performance --------------------------------------------------------------------------------------


def test_creator_discovery_suggests_never_follows(x_client: TestClient, fake_x: FakeX) -> None:
    connect(x_client)
    x_client.post("/api/x/rules", json={"name": "Funding", "type": "keyword", "query": "funding", "max_results": 10})
    for i in range(3):
        fake_x.tweet("2002", f"funding update number {i}", kind="search")
    x_client.post("/api/x/sync")
    found = x_client.get("/api/x/creators").json()
    c = next(x for x in found if x["username"] == "newsdesk")
    assert "3 monitored posts" in c["why"] and "Funding" in c["topics"]
    assert x_client.post(f"/api/x/creators/{c['profile_id']}/ignore").status_code == 204
    assert all(x["username"] != "newsdesk" for x in x_client.get("/api/x/creators").json())
    assert fake_x.writes == []


def test_performance_checkpoints_after_execution(x_client: TestClient, fake_x: FakeX) -> None:
    connect(x_client)
    _live(x_client)
    a = x_client.post("/api/x/test-post").json()
    done = _approve(x_client, a["id"])
    x_id = done["execution_result"]["x_id"]
    x_client.post("/api/x/sync")  # first read: metrics attached
    with SessionLocal() as s:
        post = s.scalar(select(SocialPost).where(SocialPost.external_id == x_id))
        post.posted_at = utcnow() - timedelta(hours=1, minutes=5)  # the ~1 h checkpoint is now due
        post.metrics_updated_at = utcnow()  # fresh by the 6 h cache rule, so only the checkpoint triggers a read
        for m in s.scalars(select(SocialPostMetric).where(SocialPostMetric.post_id == post.id)):
            m.captured_at = post.posted_at + timedelta(minutes=2)  # earlier snapshots were taken right after posting
        s.commit()
        before = len(list(s.scalars(select(SocialPostMetric).where(SocialPostMetric.post_id == post.id))))
    x_client.post("/api/x/sync")
    with SessionLocal() as s:
        post = s.scalar(select(SocialPost).where(SocialPost.external_id == x_id))
        snaps = list(s.scalars(select(SocialPostMetric).where(SocialPostMetric.post_id == post.id)))
    assert len(snaps) == before + 1
    x_client.post("/api/x/sync")  # nothing more is due until ~6 h
    with SessionLocal() as s:
        assert len(list(s.scalars(select(SocialPostMetric).where(SocialPostMetric.post_id == post.id)))) == len(snaps)
    assert [w for w in fake_x.writes if w[0] == "/2/tweets"] and len(fake_x.writes) == 1  # only the one approved post


def test_no_telegram_message_is_ever_sent(x_client: TestClient, fake_telegram: FakeTelegram) -> None:
    _telegram_topic(x_client, fake_telegram)
    assert all("sendMessage" not in m for m in getattr(fake_telegram, "methods", []))
    from pathlib import Path

    import app

    # The client can post (the Trading OS bot does, on purpose); the growth / topic code never calls it.
    root = Path(app.__file__).parent
    growth_src = [p for p in root.rglob("*.py") if "growth" in p.name or p.parent.name == "growth"]
    assert growth_src and not any(m in p.read_text(encoding="utf-8") for p in growth_src for m in ("send_message", "send_photo", "forward_message"))


def test_own_posts_unaffected(x_client: TestClient, fake_x: FakeX) -> None:
    connect(x_client)
    fake_x.tweet(OUR_ID, "our regular post", kind="own")
    x_client.post("/api/x/sync")
    assert x_client.get("/api/x/posts", params={"source": "own"}).json()


def test_recurring_question_to_opportunity(tg_client: TestClient, fake_telegram: FakeTelegram) -> None:
    tg_client.post("/api/telegram/sources", json={"chat_id": -100111})
    for i in range(3):
        fake_telegram.message(-100111, "how do I set a price alert in the app?", user_id=50 + i)
    tg_client.post("/api/telegram/sync")
    tg_client.post("/api/jobs/community.process/run")
    tg_client.post("/api/jobs/community.topics/run")
    q = tg_client.get("/api/community/questions").json()[0]
    first = tg_client.post(f"/api/community/questions/{q['id']}/opportunity").json()
    assert first["created"] is True
    assert tg_client.post(f"/api/community/questions/{q['id']}/opportunity").json() == {"id": first["id"], "created": False}
    opp = tg_client.get(f"/api/opportunities/{first['id']}").json()["opportunity"]
    assert opp["origin_kind"] == "question" and "Asked 3 times by 3 members" in opp["why"]
