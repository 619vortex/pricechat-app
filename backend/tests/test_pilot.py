"""Real-data pilot: readiness, monitoring status, evidence, activity lines, ranking signals, edits.

Everything external is mocked (FakeTelegram, FakeX, FakeProvider): no real API credits are spent.
"""

import time

import httpx
import pytest
from fastapi.testclient import TestClient

from app.connectors.x.client import XClient
from app.connectors.x.errors import XUnavailableError
from app.security.credentials import XCredentials
from tests.conftest import FakeProvider
from tests.fakes import TOKEN, X_BEARER, FakeTelegram, FakeX
from tests.test_x_social import connect


def _checks(c: TestClient) -> dict[str, dict]:
    return {k["key"]: k for k in c.get("/api/pilot-status").json()["checks"]}


def _install_models(c: TestClient, fake_provider: FakeProvider) -> None:
    rt = c.app.state.runtime  # type: ignore[attr-defined]
    s = rt.settings
    fake_provider.installed = [m for m in {fake_provider.model, s.model_tier_fast, s.model_tier_strong, rt.embedder.model} if m]
    rt.invalidate_health()


def _telegram_with_messages(c: TestClient, tg: FakeTelegram, texts: list[str]) -> None:
    c.put("/api/telegram/credentials", json={"token": TOKEN})
    c.post("/api/telegram/sources", json={"chat_id": -100111})
    now = int(time.time())
    for i, t in enumerate(texts):
        tg.message(-100111, t, user_id=10 + i, date=now - 600)
    c.post("/api/telegram/sync")


FUNDING = ["funding rates are spiking on bitcoin today", "bitcoin funding rates are spiking again",
           "why are funding rates spiking on bitcoin", "funding rates on bitcoin are spiking hard today"]


# --- pilot readiness --------------------------------------------------------------------------------


def test_nothing_is_ready_until_tested(client: TestClient) -> None:
    body = client.get("/api/pilot-status").json()
    checks = {k["key"]: k for k in body["checks"]}
    assert body["ready"] is False
    assert checks["telegram"]["status"] == "needs_setup" and "not connected" in checks["telegram"]["detail"].lower()
    assert checks["x"]["status"] == "needs_setup"
    assert checks["opportunities"]["status"] == "waiting"
    assert checks["write_actions"]["status"] == "needs_setup"  # no user-context credentials yet


def test_ai_readiness_reflects_installed_models(client: TestClient, fake_provider: FakeProvider) -> None:
    assert _checks(client)["ai"]["status"] == "needs_setup"  # tier models not installed in the fake
    _install_models(client, fake_provider)
    assert _checks(client)["ai"]["status"] == "ready" and _checks(client)["memory"]["status"] == "ready"
    fake_provider.fail = True
    client.app.state.runtime.invalidate_health()  # type: ignore[attr-defined]
    assert _checks(client)["ai"]["status"] == "problem"


def test_telegram_ready_only_after_real_ingestion(x_client: TestClient, fake_telegram: FakeTelegram) -> None:
    x_client.app.state.runtime.telegram.transport = fake_telegram.transport  # type: ignore[attr-defined]
    x_client.put("/api/telegram/credentials", json={"token": TOKEN})
    tg = _checks(x_client)["telegram"]
    assert tg["status"] == "needs_setup" and "no chat is authorised" in tg["detail"]
    x_client.post("/api/telegram/sources", json={"chat_id": -100111})
    assert _checks(x_client)["telegram"]["status"] == "waiting"  # authorised, but never ingested yet
    x_client.post("/api/telegram/sync")
    assert _checks(x_client)["telegram"]["status"] == "ready"
    x_client.put("/api/telegram/ingestion", json={"enabled": False})
    assert _checks(x_client)["telegram"]["status"] == "needs_setup"


def test_x_ready_requires_tested_public_read(x_client: TestClient, fake_x: FakeX) -> None:
    x_client.put("/api/x/credentials", json={"bearer_token": X_BEARER})
    assert _checks(x_client)["x"]["status"] == "needs_setup" and "never tested" in _checks(x_client)["x"]["detail"]
    x_client.post("/api/x/test")
    x = _checks(x_client)["x"]
    assert x["status"] == "ready" and "public read" in x["detail"]
    fake_x.no_credit = True
    x_client.post("/api/x/test")
    assert _checks(x_client)["x"]["status"] == "problem"


def test_command_center_has_pilot_models_and_queue(x_client: TestClient) -> None:
    cc = x_client.get("/api/command-center").json()
    assert {"ready", "checks"} <= set(cc["pilot"]) and {"models", "jobs"} <= set(cc["ai"])
    assert set(cc["ai"]["models"]) >= {"fast", "strong", "embeddings"}
    assert cc["ai"]["jobs"] == {"running": None, "waiting": []}
    assert "topics" in cc["telegram"] and "posts_total" in cc["x"]


# --- X monitoring status, normalised posts, dedup, errors ------------------------------------------------


def test_rule_status_topics_and_dedup(x_client: TestClient, fake_x: FakeX) -> None:
    connect(x_client, user_keys=False)
    rule = x_client.post("/api/x/rules", json={"name": "Funding", "type": "keyword", "query": "funding rates", "max_results": 10}).json()
    assert x_client.patch(f"/api/x/rules/{rule['id']}", json={"priority": 1}).json()["priority"] == 1
    ts = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
    fake_x.tweet("2001", "funding rates are getting extreme", created_at=ts)
    x_client.post("/api/x/sync")
    x_client.post("/api/x/sync")  # second read: nothing new, nothing duplicated
    r = x_client.get("/api/x/rules").json()[0]
    assert r["last_success_at"] and r["posts_matched"] == 1 and r["posts_24h"] == 1
    posts = x_client.get("/api/x/posts", params={"rule_id": rule["id"]}).json()
    assert len(posts) == 1 and posts[0]["topics"] == ["Funding"] and posts[0]["ingested_at"] and posts[0]["external_id"]
    overview = x_client.get("/api/x/overview").json()
    assert overview["topic_activity"][0]["posts_24h"] == 1 and "estimate" in overview["cost_note"]


def test_watchlist_priority_notes_and_success(x_client: TestClient) -> None:
    connect(x_client, user_keys=False)
    w = x_client.post("/api/x/watchlist", json={"username": "@BigTrader", "category": "major_creator"}).json()
    assert w["profile"]["external_id"] == "2001"  # resolved to the real account id
    upd = x_client.patch(f"/api/x/watchlist/{w['id']}", json={"priority": 1, "notes": "large audience", "enabled": False}).json()
    assert upd["priority"] == 1 and upd["notes"] == "large audience" and upd["enabled"] is False
    x_client.patch(f"/api/x/watchlist/{w['id']}", json={"enabled": True})
    x_client.post("/api/x/sync")
    assert x_client.get("/api/x/watchlist").json()[0]["last_success_at"] is not None


@pytest.mark.parametrize(("code", "expected"), [(500, XUnavailableError), (503, XUnavailableError)])
async def test_x_server_errors(code: int, expected: type) -> None:
    transport = httpx.MockTransport(lambda req: httpx.Response(code, json={"title": "Service Unavailable"}))
    with pytest.raises(expected) as exc:
        await XClient(XCredentials(X_BEARER, None, None, None, None), "https://api.x.test", transport=transport).search_recent("x")
    assert str(code) in str(exc.value)


def test_capabilities_report_app_auth_and_user_context_needs(x_client: TestClient) -> None:
    caps = connect(x_client, user_keys=False)["capabilities"]
    assert caps["app_auth"]["status"] == "available"
    assert caps["own_account"]["status"] == "not_configured" and "user-context" in caps["own_account"]["detail"]
    assert caps["write_actions"]["status"] == "not_configured" and "Access Token" in caps["write_post"]["detail"]


# --- intelligence: evidence, activity lines, ranking, cross-platform ------------------------------------


def test_activity_lines_and_strong_analysis_logging(x_client: TestClient, fake_x: FakeX) -> None:
    connect(x_client, user_keys=False)
    x_client.post("/api/x/rules", json={"name": "Funding", "type": "keyword", "query": "funding", "max_results": 10})
    fake_x.tweet("2001", "funding rates look extreme, can anyone explain what this means for traders?")
    x_client.post("/api/jobs/x.opportunities/run")  # nothing read yet: runs, but finds nothing
    assert _checks(x_client)["opportunities"]["status"] == "waiting"  # a run over no data is not "ready"
    x_client.post("/api/x/sync")
    x_client.post("/api/jobs/x.opportunities/run")
    opp_check = _checks(x_client)["opportunities"]
    assert opp_check["status"] == "ready" and "from X" in opp_check["detail"]
    events = [a["event"] for a in x_client.get("/api/activity").json()]
    assert "x_ingested" in events and "x_posts_analysed" in events
    assert "ai_analysis_started" in events and "ai_analysis_completed" in events
    done = next(a for a in x_client.get("/api/activity", params={"category": "ai"}).json() if a["event"] == "ai_analysis_completed")
    assert " s (" in done["message"] and done["details"]["model"]
    assert x_client.get("/api/activity", params={"category": "social"}).json()  # X events have their own filter


async def test_strong_analysis_timeout_is_logged_and_raised(db, fake_provider: FakeProvider) -> None:  # type: ignore[no-untyped-def]
    from app.ai.gate import AIGate
    from app.ai.routing import ModelRouter
    from app.config.settings import get_settings
    from app.models.activity import ActivityLog
    from app.providers.base import ChatMessage
    from app.providers.errors import ProviderTimeoutError

    fake_provider.error = ProviderTimeoutError("no answer after 300 s")
    router = ModelRouter(db, get_settings(), fake_provider, gate=AIGate())
    with pytest.raises(ProviderTimeoutError):
        await router.complete("x.opportunity", [ChatMessage(role="user", content="x")])
    db.commit()
    events = [e for (e,) in db.query(ActivityLog.event)]
    assert "ai_analysis_started" in events and "ai_analysis_timeout" in events


def test_processing_job_summary_line(tg_client: TestClient, fake_telegram: FakeTelegram) -> None:
    tg_client.post("/api/telegram/sources", json={"chat_id": -100111})
    for i, t in enumerate(FUNDING):
        fake_telegram.message(-100111, t, user_id=10 + i)
    tg_client.post("/api/telegram/sync")
    tg_client.post("/api/jobs/community.process/run")
    line = next(a for a in tg_client.get("/api/activity").json() if a["event"] == "telegram_messages_processed")
    assert line["message"].startswith("Processed 4 Telegram messages") and line["category"] == "community"
    pilot = tg_client.get("/api/community/pilot").json()
    assert pilot["authorised_chats"] == 1 and pilot["last_message_at"] and pilot["last_processed_at"]


def test_cross_signal_x_evidence_and_ranking_signal(x_client: TestClient, fake_x: FakeX, fake_telegram: FakeTelegram) -> None:
    _telegram_with_messages(x_client, fake_telegram, FUNDING)
    x_client.post("/api/jobs/community.process/run")
    x_client.post("/api/jobs/community.topics/run")
    connect(x_client)
    w = x_client.post("/api/x/watchlist", json={"username": "bigtrader", "category": "major_creator"}).json()
    assert w["id"]
    x_client.post("/api/x/rules", json={"name": "Funding", "type": "keyword", "query": "funding rates", "max_results": 10})
    ts = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
    fake_x.tweet("2001", "funding rates are spiking on bitcoin again today", created_at=ts)
    fake_x.tweet("2002", "bitcoin funding rates spiking hard today", created_at=ts)
    x_client.post("/api/x/sync")
    x_client.post("/api/jobs/cross.signals/run")
    sig = x_client.get("/api/cross-signals").json()[0]
    assert sig["x_authors"] == 2 and sig["x_watched_authors"] == 1 and set(sig["x_author_names"]) == {"bigtrader", "newsdesk"}
    assert sig["telegram_participants"] == 4

    x_client.post("/api/jobs/content.opportunities/run")
    x_client.post("/api/jobs/opportunities.rank/run")
    opps = x_client.get("/api/opportunities").json()
    topic_opp = next(o for o in opps if o["origin_kind"] == "topic")
    assert topic_opp["signals"]["x_relevance"] > 0 and "participants" in topic_opp["signals"]
    assert any("also discussed on X" in r for r in topic_opp["reasons"])


# --- human feedback: edit is recorded for every target -------------------------------------------------


def test_edit_feedback_for_topics_questions_and_opportunities(tg_client: TestClient, fake_telegram: FakeTelegram, db) -> None:  # type: ignore[no-untyped-def]
    from tests.test_learning_drafts import _opp

    tg_client.post("/api/telegram/sources", json={"chat_id": -100111})
    for i, t in enumerate(FUNDING + ["how do I set a price alert?", "how can I set a price alert in the app?"]):
        fake_telegram.message(-100111, t, user_id=10 + i)
    tg_client.post("/api/telegram/sync")
    for job in ("community.process", "community.topics"):
        tg_client.post(f"/api/jobs/{job}/run")
    topic = tg_client.get("/api/community/topics").json()[0]
    assert tg_client.post(f"/api/feedback/topic/{topic['id']}", json={"decision": "edit", "edits": {"name": "BTC funding rates"}}).status_code == 201
    assert tg_client.get(f"/api/community/topics/{topic['id']}").json()["topic"]["name"] == "BTC funding rates"
    q = tg_client.get("/api/community/questions", params={"recurring_only": "false"}).json()[0]
    tg_client.post(f"/api/feedback/question/{q['id']}", json={"decision": "edit", "edits": {"text": "How do I set a price alert?"}})
    opp = {"id": _opp(db, "Why funding rates spike").id}
    tg_client.post(f"/api/feedback/opportunity/{opp['id']}", json={"decision": "edit", "edits": {"title": "Funding rates, explained"}})
    kinds = {(f["target_kind"], f["decision"]) for f in tg_client.get("/api/feedback").json()}
    assert {("topic", "edit"), ("question", "edit"), ("opportunity", "edit")} <= kinds
