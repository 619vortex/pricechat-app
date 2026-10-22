import asyncio
from datetime import timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.gate import AIGate
from app.connectors.x.client import XClient
from app.connectors.x.errors import XAuthError, XBudgetExceededError, XForbiddenError, XRateLimitError, XUnavailableError, XUsageCapError
from app.connectors.x.oauth1 import authorization_header
from app.db.base import utcnow
from app.db.session import SessionLocal
from app.models.agent import AgentConfig
from app.models.enums import Platform, SocialAction
from app.models.social_intel import SocialActionLog
from app.security.credentials import XCredentials
from app.services.safety import get_or_create_policy
from app.social.guard import ActionRequest, SocialActionGuard
from tests.fakes import OUR_ID, X_BEARER, X_USER_KEYS, FakeTelegram, FakeX

APP = XCredentials(X_BEARER, None, None, None, None)
FULL = XCredentials(X_BEARER, X_USER_KEYS["api_key"], X_USER_KEYS["api_secret"], X_USER_KEYS["access_token"], X_USER_KEYS["access_token_secret"])


def _client(fx: FakeX, creds: XCredentials = APP, **kw) -> XClient:  # type: ignore[no-untyped-def]
    return XClient(creds, "https://api.x.test", transport=fx.transport, **kw)


def connect(c: TestClient, user_keys: bool = True) -> dict:
    body = {"bearer_token": X_BEARER, "username": "pricechat", **(X_USER_KEYS if user_keys else {})}
    assert c.put("/api/x/credentials", json=body).status_code == 200
    return c.post("/api/x/test").json()


# --- client ---------------------------------------------------------------------------------------


def test_oauth1_matches_official_example() -> None:
    h = authorization_header(
        "POST", "https://api.twitter.com/1.1/statuses/update.json",
        {"include_entities": "true", "status": "Hello Ladies + Gentlemen, a signed OAuth request!"},
        "xvz1evFS4wEEPTGEFPHBog", "kAcSOqF21Fu85e7zjz7ZN2U4ZRhfV3WpwPAoE3Z7kBw",
        "370773112-GmHxMAgYyLbNEtIKZeRNFsMKPR9EyMZeS9weJAEb", "LswwdoUaIvS8ltyTt5jkRh4J50vUPVVHtR2YPi5kE",
        nonce="kYjzVBB8Y0ZFabxSWbWovY3uYSQ2pTgmZeNu2VS4cg", timestamp="1318622958",
    )
    assert 'oauth_signature="hCtSmYh%2BiHYCEqBWrE7C7hYmtUk%3D"' in h


async def test_client_writes_need_a_guard_permit_and_hide_credentials(fake_x: FakeX) -> None:
    c = _client(fake_x, FULL)
    with pytest.raises(PermissionError):
        await c.create_post("hello", permit=None)  # type: ignore[arg-type]
    with pytest.raises(PermissionError):
        await c.like("1600", "9001", permit=None)  # type: ignore[arg-type]
    assert fake_x.writes == []  # nothing reached X
    assert X_BEARER not in repr(c) and X_USER_KEYS["api_secret"] not in repr(FULL)


@pytest.mark.parametrize(
    ("setup", "error"),
    [
        (lambda fx: None, XAuthError),
        (lambda fx: fx.forbidden.add("search"), XForbiddenError),
        (lambda fx: fx.rate_limited.add("search"), XRateLimitError),
        (lambda fx: setattr(fx, "usage_capped", True), XUsageCapError),
    ],
)
async def test_error_mapping(fake_x: FakeX, setup, error) -> None:  # type: ignore[no-untyped-def]
    setup(fake_x)
    creds = XCredentials("wrong-bearer-token-000000000", None, None, None, None) if error is XAuthError else APP
    with pytest.raises(error) as exc:
        await _client(fake_x, creds).search_recent("bitcoin")
    assert X_BEARER not in str(exc.value)
    if error is XRateLimitError and error is not XUsageCapError:
        assert exc.value.reset_at is not None


async def test_network_failure() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(XUnavailableError) as exc:
        await XClient(APP, "https://api.x.test", transport=httpx.MockTransport(boom)).search_recent("x")
    assert exc.value.__cause__ is None


async def test_budget_blocks_before_spending_and_usage_is_estimated(fake_x: FakeX) -> None:
    usage: list[tuple] = []

    def check(cost: float) -> None:
        if cost > 0.04:
            raise XBudgetExceededError("over budget")

    c = _client(fake_x, on_usage=lambda *a: usage.append(a), budget_check=check)
    with pytest.raises(XBudgetExceededError):
        await c.search_recent("bitcoin", 10)  # 10 posts * $0.005 = $0.05 > limit
    assert fake_x.calls == []  # nothing was sent
    fake_x.tweet("2001", "bitcoin funding")
    await c.user_by_username("bigtrader")
    assert usage[-1][0] == "users/by/username" and usage[-1][3] == 0.01


async def test_owned_reads_use_user_context(fake_x: FakeX) -> None:
    fake_x.tweet(OUR_ID, "our post", kind="own")
    c = _client(fake_x, FULL)
    await c.user_posts(OUR_ID, 5, own=True)
    await c.search_recent("bitcoin")
    assert [m for _, _, m in fake_x.calls] == ["user", "app"]


# --- connection & capabilities via API -----------------------------------------------------------------


def test_credentials_saved_never_returned_and_capabilities_detected(x_client: TestClient, fake_x: FakeX) -> None:
    status = x_client.get("/api/x/status").json()
    assert status["configured"] is False and status["status"] == "not_configured"
    assert x_client.put("/api/x/credentials", json={"api_key": X_USER_KEYS["api_key"]}).status_code == 422  # keys come as a set

    fake_x.forbidden.add("search")  # e.g. an access level without search
    caps_status = connect(x_client)
    caps = {k: v["status"] for k, v in caps_status["capabilities"].items()}
    assert caps["own_account"] == "available" and caps["account_lookup"] == "available"
    assert caps["own_posts"] == "available" and caps["mentions"] == "available"
    assert caps["search_recent"] == "unavailable" and caps["write_actions"] == "approval" and caps["write_reply"] == "available"
    assert caps_status["status"] == "connected" and caps_status["account_username"] == "pricechat" and caps_status["account_id"] == OUR_ID
    assert caps_status["auth_modes"] == ["app_only", "user_context"]
    assert caps_status["usage_today"]["requests"] >= 4 and caps_status["usage_today"]["cost"] > 0
    for path in ("/api/x/status", "/api/connectors", "/api/activity", "/api/command-center"):
        text = x_client.get(path).text
        assert X_BEARER not in text and X_USER_KEYS["api_secret"] not in text and X_USER_KEYS["access_token_secret"] not in text
    assert {c["platform"]: c["status"] for c in x_client.get("/api/connectors").json()}["x"] == "connected"

    after = x_client.delete("/api/x/credentials").json()
    assert after["configured"] is False


def test_bad_credentials_report_auth_error(x_client: TestClient) -> None:
    x_client.put("/api/x/credentials", json={"bearer_token": "WrongBearerToken000000000000", "username": "pricechat"})
    resp = x_client.post("/api/x/test")
    assert resp.status_code == 502 and resp.json()["code"] == "x_auth"
    assert x_client.get("/api/x/status").json()["status"] == "error"


def test_app_only_access(x_client: TestClient) -> None:
    caps = {k: v["status"] for k, v in connect(x_client, user_keys=False)["capabilities"].items()}
    assert caps["own_account"] == "not_configured" and caps["account_lookup"] == "available" and caps["private_metrics"] == "not_configured"


# --- monitoring, watchlist, ingestion ---------------------------------------------------------------


def test_rules_watchlist_and_ingestion(x_client: TestClient, fake_x: FakeX) -> None:
    connect(x_client)
    rule = x_client.post("/api/x/rules", json={"name": "Funding", "type": "keyword", "query": "funding rates", "max_results": 10}).json()
    assert rule["type"] == "keyword"
    assert x_client.post("/api/x/rules", json={"name": "bad", "type": "keyword", "query": "x", "max_results": 5}).status_code == 422
    w = x_client.post("/api/x/watchlist", json={"username": "@bigtrader", "category": "major_creator", "reason": "large market creator"})
    assert w.status_code == 201 and w.json()["profile"]["followers_count"] == 250000
    assert x_client.post("/api/x/watchlist", json={"username": "bigtrader"}).status_code == 409
    assert x_client.post("/api/x/watchlist", json={"username": "nobodyhere"}).status_code == 404

    fake_x.tweet(OUR_ID, "Our explainer on funding", kind="own")
    fake_x.tweet("2002", "@pricechat what do funding rates mean?", kind="mention", reply_to_us=True)
    fake_x.tweet("2001", "funding rates are getting extreme on BTC", kind="search")
    fake_x.tweet("2001", "watchlist only post about markets", kind="other")

    run = x_client.post("/api/x/sync").json()
    assert run["status"] == "success" and run["result"]["new_posts"] == 4
    assert {p["text"] for p in x_client.get("/api/x/posts", params={"source": "mention"}).json()} == {"@pricechat what do funding rates mean?"}
    own = x_client.get("/api/x/posts", params={"source": "own"}).json()
    assert own and own[0]["is_own"] is True
    assert x_client.get("/api/x/posts", params={"rule_id": rule["id"]}).json()[0]["text"].startswith("funding rates")

    again = x_client.post("/api/x/sync").json()
    assert again["result"]["new_posts"] == 0  # since_id: nothing re-read

    watch = x_client.get("/api/x/watchlist").json()[0]
    assert watch["last_checked_at"] is not None
    rel_search = [w for w in x_client.get("/api/x/watchlist").json()]
    assert rel_search  # relationship memory comes from interactions (mentions) below

    analytics = x_client.get("/api/x/analytics").json()
    assert analytics["post_count"] == 1 and analytics["totals"]["impressions"] == 1500  # private metrics via user context
    assert analytics["totals"]["bookmarks"] is None  # not provided by the API → null, not 0


def test_relationship_memory_from_mentions(x_client: TestClient, fake_x: FakeX, db: Session) -> None:
    connect(x_client)
    fake_x.tweet("2002", "@pricechat great explainer on funding rates", kind="mention")
    fake_x.tweet("2002", "@pricechat another question about alerts", kind="mention", reply_to_us=True)
    x_client.post("/api/x/sync")
    w = x_client.post("/api/x/watchlist", json={"username": "newsdesk", "category": "news"}).json()
    rel = w["relationship"]
    assert rel["interaction_count"] == 2 and rel["mention_count"] == 1 and rel["reply_count"] == 1
    assert rel["first_interaction_at"] and "funding" in rel["topics"]
    assert rel["they_follow_us"] is None  # unknown unless the API tells us
    notes = x_client.put(f"/api/x/profiles/{w['profile']['id']}/relationship", json={"notes": "Friendly news account"}).json()
    assert notes["notes"] == "Friendly news account"


def test_rate_limit_pauses_and_budget_stops(x_client: TestClient, fake_x: FakeX) -> None:
    connect(x_client)
    x_client.post("/api/x/rules", json={"name": "Funding", "type": "keyword", "query": "funding", "max_results": 10})
    fake_x.rate_limited.add("search")
    run = x_client.post("/api/x/sync").json()
    assert run["status"] == "success" and "rate limit" in run["result"]["stopped"]
    assert x_client.get("/api/x/status").json()["blocked_until"] is not None
    assert x_client.post("/api/x/sync").json()["status"] == "skipped"  # waits until reset instead of retrying

    x_client.put("/api/x/reading", json={"enabled": True})  # resume clears the block
    fake_x.rate_limited.clear()
    x_client.app.state.runtime.settings.x_daily_budget_usd = 0.0  # type: ignore[attr-defined]
    run = x_client.post("/api/x/sync").json()
    assert "budget" in (run["result"].get("stopped") or "").lower()


def test_x_not_configured_job_skips(client: TestClient) -> None:
    assert client.post("/api/jobs/x.ingest/run").json()["status"] == "skipped"


# --- X opportunities, cross-signals --------------------------------------------------------------------


def test_x_opportunities_and_approval_simulation(x_client: TestClient, fake_x: FakeX) -> None:
    connect(x_client)
    x_client.post("/api/x/rules", json={"name": "Funding", "type": "keyword", "query": "funding", "max_results": 10})
    fake_x.tweet("2001", "funding rates look extreme, can anyone explain what this means for traders?", kind="search")
    fake_x.tweet("2002", "@pricechat giveaway! retweet to win, funding edition", kind="mention")
    x_client.post("/api/x/sync")
    run = x_client.post("/api/jobs/x.opportunities/run").json()
    assert run["status"] == "success" and run["result"]["created"] >= 1

    opps = x_client.get("/api/x/opportunities").json()
    o = next(x for x in opps if "explain" in x["post"]["text"])
    assert o["suggested_action"] == "reply" and o["relevance"] == 0.8  # 0-10 scale normalised
    assert o["post"]["author_username"] == "bigtrader" and o["reasons"]
    assert set(o["signals"]) >= {"relevance", "author_reach", "engagement", "recency", "community_overlap"}
    dismissed = x_client.get("/api/x/opportunities", params={"status": "dismissed"}).json()
    assert any("giveaway" in d["post"]["text"] for d in dismissed)

    approval = x_client.post(f"/api/x/opportunities/{o['id']}/propose", json={"text": "Funding rates measure..."}).json()
    assert approval["status"] == "pending" and approval["action"] == "reply" and approval["target_username"] == "bigtrader"
    assert approval["guard_result"]["allowed"] is True and approval["guard_result"]["executable"] is False  # waits for a human
    assert approval["payload"]["original_post"]["text"].startswith("funding rates look extreme")
    decided = x_client.post(f"/api/approvals/{approval['id']}/decision", json={"decision": "approved"}).json()
    assert decided["status"] == "approved" and decided["execution_result"]["dry_run"] is True  # dry run is on by default
    assert decided["execution_result"]["would_execute"]["action"] == "reply"
    assert fake_x.writes == []
    assert x_client.post(f"/api/approvals/{approval['id']}/decision", json={"decision": "approved"}).status_code == 409


def test_cross_platform_signal(x_client: TestClient, fake_x: FakeX, fake_telegram: FakeTelegram) -> None:
    import time as _t

    x_client.post("/api/telegram/sources", json={"chat_id": -100111})
    now = int(_t.time())
    for i, t in enumerate(["funding rates are spiking on bitcoin today", "bitcoin funding rates are spiking again",
                           "why are funding rates spiking on bitcoin", "funding rates on bitcoin are spiking hard today"]):
        fake_telegram.message(-100111, t, user_id=10 + i, date=now - 600)
    x_client.post("/api/telegram/sync")
    x_client.post("/api/jobs/community.process/run")
    x_client.post("/api/jobs/community.topics/run")

    connect(x_client)
    x_client.post("/api/x/rules", json={"name": "Funding", "type": "keyword", "query": "funding rates", "max_results": 10})
    ts = utcnow().strftime("%Y-%m-%dT%H:%M:%S.000Z")
    fake_x.tweet("2001", "funding rates are spiking on bitcoin again today", created_at=ts)
    fake_x.tweet("2002", "bitcoin funding rates spiking hard today", created_at=ts)
    x_client.post("/api/x/sync")
    run = x_client.post("/api/jobs/cross.signals/run").json()
    assert run["result"]["new"] == 1
    sig = x_client.get("/api/cross-signals").json()[0]
    assert sig["telegram_participants"] == 4 and sig["x_posts"] == 2
    assert sig["x_trend"] == "insufficient_data"  # too few posts to call a direction
    assert "consider" in sig["recommendation"].lower()
    assert x_client.post(f"/api/cross-signals/{sig['id']}/dismiss").json()["status"] == "dismissed"

    cc = x_client.get("/api/command-center").json()
    assert set(cc) >= {"ai", "telegram", "x", "trending", "cross_signals", "content", "recent_activity"}
    assert cc["telegram"]["communities"] == 1 and cc["x"]["status"] == "connected"


# --- SocialActionGuard & permissions ----------------------------------------------------------------------


def _enable(db: Session, agent_id: int, action: SocialAction = SocialAction.REPLY) -> None:
    from app.models.safety import ActionLimit

    policy = get_or_create_policy(db, agent_id)
    policy.outbound_enabled = True
    limit = db.scalar(select(ActionLimit).where(ActionLimit.policy_id == policy.id, ActionLimit.platform == Platform.X, ActionLimit.action == action))
    assert limit is not None
    limit.enabled = True
    db.commit()


def test_guard_checks_in_every_configuration(db: Session, agent_config: AgentConfig) -> None:
    from tests.conftest import connect_x_user

    g = SocialActionGuard(db, agent_config.id)
    req = ActionRequest(Platform.X, SocialAction.REPLY, "2001", "bigtrader", "hello")
    assert g.evaluate(req).reason == "X is not connected"
    connect_x_user(db)

    d = g.evaluate(req)
    assert d.allowed and d.requires_approval and d.executable is False  # never executable without a human approval
    assert d.checks[-1] == {"check": "approval", "passed": False, "detail": "waiting for human approval"}
    approved = g.evaluate(req, approved=True)
    assert approved.allowed and approved.executable

    policy = get_or_create_policy(db, agent_config.id)
    policy.platforms_enabled = {"x": False}
    db.commit()
    assert "disabled for social actions" in g.evaluate(req).reason
    policy.platforms_enabled = {"x": True}
    policy.blocked_targets = ["@BigTrader"]
    db.commit()
    assert g.evaluate(req).reason == "Target account is on the blocked list"
    policy.blocked_targets = []
    policy.watchlist_only = True
    db.commit()
    assert g.evaluate(req).reason == "Only watched accounts may be targeted"
    policy.watchlist_only = False
    db.commit()

    g.record_attempt(req, d, None, "executed")
    db.commit()
    assert "Duplicate" in g.evaluate(req).reason
    other_text = ActionRequest(Platform.X, SocialAction.REPLY, "2001", "bigtrader", "different text")
    assert "Cooldown" in g.evaluate(other_text).reason

    policy.kill_switch = True
    db.commit()
    assert g.evaluate(req).reason == "Social actions are paused"


def test_permissions_api_never_allows_automatic(x_client: TestClient) -> None:
    guard = x_client.get("/api/social/guard").json()
    assert guard["writes_available"] is True and guard["dry_run"] is True and "locked" in guard["automatic_locked_reason"]
    modes = {(p["platform"], p["action"]): p["mode"] for p in guard["permissions"]}
    assert all(modes[("x", a)] == "approval" for a in ("post", "reply", "like", "repost", "follow"))
    assert modes[("telegram", "post")] == "disabled"
    assert all(p["available_modes"] == ["disabled", "approval"] for p in guard["permissions"])
    limits = {p["action"]: (p["max_per_hour"], p["max_per_day"]) for p in guard["permissions"] if p["platform"] == "x"}
    assert limits == {"post": (1, 3), "reply": (3, 10), "like": (5, 20), "repost": (2, 5), "follow": (2, 5)}

    bad = x_client.put("/api/social/permissions/x/reply", json={"mode": "automatic"})
    assert bad.status_code == 422 and "locked" in bad.json()["detail"]
    ok = x_client.put("/api/social/permissions/x/reply", json={"mode": "approval", "max_per_hour": 2}).json()
    assert {(p["platform"], p["action"]): p["mode"] for p in ok["permissions"]}[("x", "reply")] == "approval"

    check = x_client.post("/api/social/guard/check", json={"platform": "x", "action": "reply", "target": "bigtrader", "text": "hi"}).json()
    assert check["executable"] is False  # no user-context credentials, and a check is never an approval
    x_client.patch("/api/settings/safety", json={"kill_switch": True})
    assert x_client.post("/api/social/guard/check", json={"platform": "x", "action": "post"}).json()["reason"] == "Social actions are paused"


def test_approval_queue_lifecycle(x_client: TestClient, db: Session) -> None:
    a = x_client.post("/api/approvals", json={"platform": "x", "action": "post", "text": "A proposed post"}).json()
    assert a["status"] == "pending" and a["expires_at"]
    rejected = x_client.post(f"/api/approvals/{a['id']}/decision", json={"decision": "rejected", "note": "not now"}).json()
    assert rejected["status"] == "rejected"
    b = x_client.post("/api/approvals", json={"platform": "x", "action": "like", "target_external_id": "123"}).json()
    assert x_client.post(f"/api/approvals/{b['id']}/decision", json={"decision": "executed"}).status_code == 422
    approved = x_client.post(f"/api/approvals/{b['id']}/decision", json={"decision": "approved"}).json()
    assert approved["execution_result"]["executed"] is False
    with SessionLocal() as s:
        logged = list(s.scalars(select(SocialActionLog.outcome)))
    assert logged == ["blocked"]  # the guard blocked it (likes disabled); nothing was executed

    from app.models.social_intel import ApprovalRequest

    c = x_client.post("/api/approvals", json={"platform": "x", "action": "post", "text": "old"}).json()
    with SessionLocal() as s:
        row = s.get(ApprovalRequest, c["id"])
        assert row is not None
        row.expires_at = utcnow() - timedelta(minutes=1)
        s.commit()
    assert x_client.post("/api/jobs/approvals.expire/run").json()["result"]["expired"] == 1
    assert x_client.get("/api/approvals", params={"status": "expired"}).json()[0]["id"] == c["id"]


def test_draft_to_approval(x_client: TestClient, db: Session) -> None:
    from app.models.content import Content
    from app.models.enums import ContentStatus

    c = Content(title="t", body="A draft post", status=ContentStatus.DRAFT, target_platform=Platform.X, draft_type="x_post")
    db.add(c)
    db.commit()
    a = x_client.post(f"/api/approvals/from-draft/{c.id}").json()
    assert a["source_kind"] == "draft" and a["payload"]["text"] == "A draft post"


# --- performance gate ---------------------------------------------------------------------------------


async def test_background_work_waits_for_interactive_chat() -> None:
    gate = AIGate(max_wait_seconds=5)
    order: list[str] = []

    async def chat() -> None:
        async with gate.interactive():
            order.append("chat start")
            await asyncio.sleep(0.1)
            order.append("chat end")

    async def job() -> None:
        await asyncio.sleep(0.01)
        async with gate.background("community.summary.daily"):
            order.append("job")

    await asyncio.gather(chat(), job())
    assert order == ["chat start", "chat end", "job"]
    assert gate.status()["background_task"] is None


def test_processing_status_endpoint(client: TestClient) -> None:
    body = client.get("/api/system/processing").json()
    assert set(body["gate"]) >= {"interactive_active", "background_task", "background_waiting"}


def test_username_from_settings_enables_bearer_only_mentions(x_client: TestClient) -> None:
    x_client.app.state.runtime.settings.x_account_username = "@pricechat"  # type: ignore[attr-defined]
    x_client.put("/api/x/credentials", json={"bearer_token": X_BEARER})
    caps = {k: v["status"] for k, v in x_client.post("/api/x/test").json()["capabilities"].items()}
    assert caps["account_lookup"] == "available" and caps["mentions"] == "available"


async def test_jobs_run_one_at_a_time(db: Session) -> None:
    from app.scheduler.scheduler import JobSpec, Scheduler

    active: list[str] = []
    overlap: list[bool] = []

    def make(name: str):  # type: ignore[no-untyped-def]
        async def run(ctx):  # type: ignore[no-untyped-def]
            overlap.append(bool(active))
            active.append(name)
            await asyncio.sleep(0.05)
            active.remove(name)
            return {}
        return run

    s = Scheduler(SessionLocal, [JobSpec(n, n, n, 60, make(n), initial_delay_seconds=0) for n in ("a.job", "b.job")])
    s.sync_jobs()
    a, b = await asyncio.gather(s.run_job("a.job"), s.run_job("b.job"))
    assert a.status == b.status == "success" and overlap == [False, False]


def test_no_credit_is_explained(x_client: TestClient, fake_x: FakeX) -> None:
    fake_x.no_credit = True
    status = connect(x_client, user_keys=False)
    caps = {k: v["status"] for k, v in status["capabilities"].items()}
    assert caps["account_lookup"] == "no_credit" and caps["search_recent"] == "no_credit"
    assert status["status"] == "error" and "no API credit" in status["last_error"]


def test_bearer_and_app_keys_without_access_tokens(x_client: TestClient) -> None:
    body = {"bearer_token": X_BEARER, "api_key": X_USER_KEYS["api_key"], "api_secret": X_USER_KEYS["api_secret"]}
    saved = x_client.put("/api/x/credentials", json=body)
    assert saved.status_code == 200 and saved.json()["configured"] is True
    assert X_USER_KEYS["api_secret"] not in saved.text
    status = x_client.post("/api/x/test").json()
    caps = {k: v["status"] for k, v in status["capabilities"].items()}
    assert list(caps)[:4] == ["app_auth", "public_read", "search_recent", "account_lookup"] and caps["app_auth"] == "available"
    assert caps["public_read"] == "available" and caps["search_recent"] == "available" and caps["account_lookup"] == "available"
    assert caps["own_account"] == "not_configured" and caps["write_actions"] == "not_configured"
    assert status["status"] == "connected" and status["auth_modes"] == ["app_only"]


def test_credential_pairs(x_client: TestClient) -> None:
    assert x_client.put("/api/x/credentials", json={"bearer_token": X_BEARER}).status_code == 200  # Bearer alone is fine
    r = x_client.put("/api/x/credentials", json={"api_secret": X_USER_KEYS["api_secret"]})
    assert r.status_code == 422 and "belong together" in r.json()["detail"]
    r = x_client.put("/api/x/credentials", json={"access_token": X_USER_KEYS["access_token"], "access_token_secret": X_USER_KEYS["access_token_secret"]})
    assert r.status_code == 422 and "Consumer Key" in r.json()["detail"]


# --- responsiveness: no write transaction held across awaited X requests ----------------------------


def test_budget_usage_is_committed_immediately(db: Session) -> None:
    from app.config.settings import get_settings
    from app.social.budget import PlatformBudget

    PlatformBudget(db, get_settings()).record("tweets/lookup", 1, 0, 0.005)
    assert not db.in_transaction()  # nothing left open while the caller awaits the next request


async def test_strong_tasks_get_longer_timeout(db: Session, fake_provider) -> None:  # type: ignore[no-untyped-def]
    from app.ai.routing import ModelRouter
    from app.config.settings import get_settings
    from app.providers.base import ChatMessage

    s = get_settings()
    router = ModelRouter(db, s, fake_provider)
    await router.complete("x.relevance", [ChatMessage(role="user", content="hi")])
    await router.complete("x.opportunity", [ChatMessage(role="user", content="hi")])
    fast_opts, strong_opts = fake_provider.calls[-2][1], fake_provider.calls[-1][1]
    assert fast_opts.timeout_seconds is None and strong_opts.timeout_seconds == s.llm_strong_timeout_seconds


async def test_read_now_queues_instead_of_waiting(x_client: TestClient) -> None:
    connect(x_client, user_keys=False)
    sched = x_client.app.state.runtime.scheduler  # type: ignore[attr-defined]
    async with sched._serial:  # another background job is running
        body = x_client.post("/api/x/sync").json()
    assert body["status"] == "queued"


def test_own_posts_only_read_for_read_and_earn(x_client: TestClient, fake_x: FakeX, db: Session) -> None:
    """x.own (production): Price Chat's own new posts and nothing else: no mentions, searches or watchlists are read."""
    import asyncio

    from app.members import x_feed
    from app.social.x_ingest import ingest_x

    connect(x_client)
    x_client.post("/api/x/rules", json={"name": "Funding", "type": "keyword", "query": "funding rates", "max_results": 10})
    fake_x.tweet(OUR_ID, "Our explainer on funding rates, in plain words", kind="own")
    fake_x.tweet("2002", "@pricechat what do funding rates mean?", kind="mention", reply_to_us=True)
    fake_x.tweet("2001", "funding rates are getting extreme on BTC", kind="search")
    fake_x.calls.clear()
    rt = x_client.app.state.runtime  # type: ignore[attr-defined]
    report = asyncio.run(ingest_x(db, rt.settings, rt.x, None, only_own=True))
    assert report.requests == 1 and not report.stopped
    paths = [str(c[0]) for c in fake_x.calls]
    assert len(paths) == 1 and "tweets" in paths[0] and not any("mentions" in p or "search" in p for p in paths)
    assert [p.text for p in x_feed.candidates(db)] == ["Our explainer on funding rates, in plain words"]
    assert asyncio.run(ingest_x(db, rt.settings, rt.x, None, only_own=True)).new_posts == 0  # since_id: nothing re-read
