"""X Operator V1: supervised write actions.

Every X request goes to the in-memory FakeX; no test can reach the real X API or spend credits.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.agent import AgentConfig
from app.models.enums import Platform, SocialAction
from app.models.social_intel import SocialRelationship
from app.services.safety import get_or_create_policy
from app.social.guard import ActionRequest, SocialActionGuard, require_permit
from tests.conftest import FakeProvider, connect_x_user
from tests.fakes import OUR_ID, FakeX
from tests.test_x_social import connect

# --- helpers ---------------------------------------------------------------------------------------------


def _opportunity(c: TestClient, fake_x: FakeX, text: str = "funding rates look extreme, can anyone explain what this means for traders?") -> dict:
    connect(c)  # bearer + user-context keys; FakeX reports read-write access
    c.post("/api/x/rules", json={"name": "Funding", "type": "keyword", "query": "funding", "max_results": 10})
    fake_x.tweet("2001", text, kind="search")
    c.post("/api/x/sync")
    c.post("/api/jobs/x.opportunities/run")
    return next(o for o in c.get("/api/x/opportunities").json() if o["post"]["text"] == text)


def _live(c: TestClient) -> None:
    c.patch("/api/social/guard", json={"dry_run": False})


def _approve(c: TestClient, approval_id: int, text: str | None = None) -> dict:
    body = {"decision": "approved", **({"text": text} if text is not None else {})}
    return c.post(f"/api/approvals/{approval_id}/decision", json=body).json()


# --- authentication & capability detection -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("level", "expected"),
    [("read-write", "available"), ("read", "unavailable"), (None, "unknown")],
)
def test_write_capability_comes_from_what_x_reports(x_client: TestClient, fake_x: FakeX, level: str | None, expected: str) -> None:
    fake_x.access_level = level  # type: ignore[assignment]
    caps = connect(x_client)["capabilities"]
    assert {caps[f"write_{a}"]["status"] for a in ("post", "reply", "like", "repost", "follow")} == {expected}
    if expected == "unavailable":
        assert "Read and write" in caps["write_post"]["detail"]
    assert fake_x.writes == []  # detecting capability never writes anything


def test_operator_status_and_connection_sections(x_client: TestClient, fake_x: FakeX) -> None:
    connect(x_client)
    op = x_client.get("/api/x/operator").json()
    assert op["x_status"] == "connected" and op["user_context"] is True and op["dry_run"] is True
    assert op["social_actions_active"] is True and op["mode"] == "approval_required" and "locked" in op["automatic"]
    assert {t["action"]: (t["done"], t["limit"]) for t in op["today"]} == {"post": (0, 3), "reply": (0, 10), "like": (0, 20), "repost": (0, 5), "follow": (0, 5)}
    assert op["write_capability"]["reply"] == "available"


# --- guard ---------------------------------------------------------------------------------------------------


def test_permits_are_single_use_and_action_specific(db: Session, agent_config: AgentConfig) -> None:
    connect_x_user(db)
    g = SocialActionGuard(db, agent_config.id)
    req = ActionRequest(Platform.X, SocialAction.LIKE, "9001", "bigtrader")
    with pytest.raises(PermissionError):
        g.issue_permit(req, g.evaluate(req), 1)  # not approved → no permit
    permit = g.issue_permit(req, g.evaluate(req, approved=True), 1)
    with pytest.raises(PermissionError):
        require_permit(permit, SocialAction.FOLLOW, "9001")  # wrong action
    require_permit(permit, SocialAction.LIKE, "9001")
    with pytest.raises(PermissionError):
        require_permit(permit, SocialAction.LIKE, "9001")  # already used


def test_limits_duplicates_cooldown_and_interaction_history(db: Session, agent_config: AgentConfig) -> None:
    connect_x_user(db)
    g = SocialActionGuard(db, agent_config.id)
    policy = get_or_create_policy(db, agent_config.id)
    like = ActionRequest(Platform.X, SocialAction.LIKE, "9001", "bigtrader")
    g.record_attempt(like, None, None, "executed")
    db.commit()
    assert g.evaluate(ActionRequest(Platform.X, SocialAction.LIKE, "9001", "someoneelse")).reason == "Already liked this post"
    assert "Cooldown" in g.evaluate(ActionRequest(Platform.X, SocialAction.LIKE, "9002", "bigtrader")).reason

    policy.cooldown_minutes = 0
    db.commit()
    reply = ActionRequest(Platform.X, SocialAction.REPLY, "9003", "newsdesk", "first reply")
    g.record_attempt(reply, None, None, "executed")
    db.commit()
    again = g.evaluate(ActionRequest(Platform.X, SocialAction.REPLY, "9004", "NewsDesk", "another reply"))
    assert again.allowed is False and "Already replied to @newsdesk" in again.reason

    for i in range(2):
        g.record_attempt(ActionRequest(Platform.X, SocialAction.REPOST, f"95{i}", f"acct{i}"), None, None, "executed")
    db.commit()
    hourly = g.evaluate(ActionRequest(Platform.X, SocialAction.REPOST, "9600", "acct9"))
    assert hourly.reason == "Hourly limit for repost reached (2/hour)"


def test_dry_runs_do_not_count_against_limits(db: Session, agent_config: AgentConfig) -> None:
    connect_x_user(db)
    g = SocialActionGuard(db, agent_config.id)
    req = ActionRequest(Platform.X, SocialAction.POST, None, None, "same text")
    g.record_attempt(req, None, None, "dry_run")
    db.commit()
    assert g.evaluate(req).allowed  # a dry run is not a duplicate of anything real


def test_pausing_social_actions_blocks_new_writes_but_not_reading(x_client: TestClient, fake_x: FakeX) -> None:
    o = _opportunity(x_client, fake_x)
    paused = x_client.put("/api/social/actions", json={"active": False}).json()
    assert paused["social_actions_active"] is False
    a = x_client.post(f"/api/x/opportunities/{o['id']}/propose", json={"action": "like"}).json()
    assert a["guard_result"]["allowed"] is False and a["guard_result"]["reason"] == "Social actions are paused"
    _live(x_client)
    done = _approve(x_client, a["id"])
    assert done["status"] == "failed" and "paused" in done["execution_result"]["reason"]
    assert fake_x.writes == []
    assert x_client.post("/api/x/sync").json()["status"] == "success"  # reading continues
    assert x_client.put("/api/social/actions", json={"active": True}).json()["social_actions_active"] is True


# --- proposals ---------------------------------------------------------------------------------------------


def test_ai_reply_proposal_with_context(x_client: TestClient, fake_x: FakeX, fake_provider: FakeProvider) -> None:
    o = _opportunity(x_client, fake_x)
    fake_provider.calls.clear()
    a = x_client.post(f"/api/x/opportunities/{o['id']}/draft-reply", json={}).json()
    assert a["action"] == "reply" and a["status"] == "pending" and a["target_username"] == "bigtrader"
    p = a["payload"]
    assert p["text"].startswith("High funding means") and len(p["text"]) <= 280
    assert p["original_post"]["text"] == o["post"]["text"] and p["confidence"] == "high" and p["model"]
    assert any(line.startswith("Author: @bigtrader") for line in p["context"]) and any("Monitored topic" in line for line in p["context"])
    prompt = fake_provider.calls[-1][0]
    assert "Never open with praise" in prompt[0].content and "curious" in prompt[0].content  # rules + X voice
    assert fake_provider.models_used()[-1] == "strong-model"


def test_generic_praise_is_rewritten(x_client: TestClient, fake_x: FakeX) -> None:
    o = _opportunity(x_client, fake_x, "praise-bait: funding rates look extreme, explain?")
    a = x_client.post(f"/api/x/opportunities/{o['id']}/draft-reply", json={}).json()
    assert not a["payload"]["text"].lower().startswith("great insight") and not a["payload"]["warnings"]


def test_nothing_worthwhile_is_not_proposed(x_client: TestClient, fake_x: FakeX) -> None:
    o = _opportunity(x_client, fake_x, "nothing-to-add funding meme, explain?")
    r = x_client.post(f"/api/x/opportunities/{o['id']}/draft-reply", json={})
    assert r.status_code == 409 and "nothing worthwhile" in r.json()["detail"]


def test_like_repost_follow_proposals_show_exact_targets(x_client: TestClient, fake_x: FakeX) -> None:
    o = _opportunity(x_client, fake_x)
    assert o["engagement"]["suggestions"]["like"] is True and o["engagement"]["suggestions"]["follow_author"] is True
    like = x_client.post(f"/api/x/opportunities/{o['id']}/propose", json={"action": "like"}).json()
    assert like["target_external_id"] == o["post"]["external_id"] and like["target_username"] == "bigtrader"
    follow = x_client.post(f"/api/x/opportunities/{o['id']}/propose", json={"action": "follow"}).json()
    assert follow["target_external_id"] == "2001" and follow["payload"]["target_profile"]["followers"] == 250000
    assert x_client.post(f"/api/x/opportunities/{o['id']}/propose", json={"action": "reply"}).status_code == 422  # needs text


def test_test_post_and_draft_go_to_the_queue_only(x_client: TestClient, fake_x: FakeX, db: Session) -> None:
    from app.models.content import Content
    from app.models.enums import ContentStatus

    connect(x_client)
    t = x_client.post("/api/x/test-post").json()
    assert t["action"] == "post" and t["status"] == "pending" and "test" in t["payload"]["text"].lower()
    long = Content(title="t", body="x" * 300, status=ContentStatus.DRAFT, target_platform=Platform.X, draft_type="x_post")
    db.add(long)
    db.commit()
    r = x_client.post(f"/api/approvals/from-draft/{long.id}")
    assert r.status_code == 422 and "280" in r.json()["detail"]
    assert fake_x.writes == []


# --- execution -----------------------------------------------------------------------------------------------


def test_dry_run_runs_the_whole_pipeline_except_the_write(x_client: TestClient, fake_x: FakeX) -> None:
    connect(x_client)
    a = x_client.post("/api/x/test-post").json()
    done = _approve(x_client, a["id"])
    assert done["status"] == "approved" and done["execution_result"]["dry_run"] is True
    assert done["execution_result"]["would_execute"] == {"action": "post", "target_post_id": None, "target_account": None,
                                                         "target_user_id": None, "text": a["payload"]["text"], "quote_post_id": None}
    assert fake_x.writes == []
    assert any(e["event"] == "x_action_dry_run" for e in x_client.get("/api/activity", params={"level": "audit"}).json())


def test_executed_reply_is_confirmed_recorded_and_remembered(x_client: TestClient, fake_x: FakeX, db: Session) -> None:
    o = _opportunity(x_client, fake_x)
    a = x_client.post(f"/api/x/opportunities/{o['id']}/draft-reply", json={}).json()
    _live(x_client)
    done = _approve(x_client, a["id"])
    assert done["status"] == "executed" and done["executed_at"]
    res = done["execution_result"]
    assert res["executed"] is True and res["x_id"] and res["url"].endswith(res["x_id"])
    path, body = fake_x.writes[-1]
    assert path == "/2/tweets" and body["reply"] == {"in_reply_to_tweet_id": o["post"]["external_id"]}
    from app.models.social_intel import SocialProfile

    rel = db.scalar(select(SocialRelationship).join(SocialProfile, SocialProfile.id == SocialRelationship.profile_id)
                    .where(SocialProfile.username == "bigtrader"))
    assert rel is not None and rel.our_reply_count == 1 and rel.last_interaction_at is not None
    op = x_client.get("/api/x/operator").json()
    assert {t["action"]: t["done"] for t in op["today"]}["reply"] == 1 and op["usage_today"]["writes"] == 1
    events = [e["event"] for e in x_client.get("/api/activity", params={"level": "audit"}).json()]
    assert "x_action_executed" in events

    # performance: the next read attaches X's metrics to our new reply
    x_client.post("/api/x/sync")
    executed = x_client.get("/api/approvals", params={"status": "executed"}).json()[0]
    assert executed["performance"]["likes"] == 3 and executed["performance"]["impressions"] is not None


def test_like_repost_follow_execution(x_client: TestClient, fake_x: FakeX) -> None:
    o = _opportunity(x_client, fake_x)
    _live(x_client)
    x_client.patch("/api/social/guard", json={"cooldown_minutes": 0})
    for action, path in (("like", f"/2/users/{OUR_ID}/likes"), ("repost", f"/2/users/{OUR_ID}/retweets"), ("follow", f"/2/users/{OUR_ID}/following")):
        a = x_client.post(f"/api/x/opportunities/{o['id']}/propose", json={"action": action}).json()
        done = _approve(x_client, a["id"])
        assert done["status"] == "executed", done
        assert fake_x.writes[-1][0] == path
    assert fake_x.writes[-1][1] == {"target_user_id": "2001"}
    again = x_client.post(f"/api/x/opportunities/{o['id']}/propose", json={"action": "like"}).json()
    assert again["guard_result"]["reason"] == "Already liked this post"


@pytest.mark.parametrize(("status", "code"), [(500, "x_unavailable"), (401, "x_auth"), (429, "x_rate_limited")])
def test_execution_failure_is_recorded_not_marked_executed(x_client: TestClient, fake_x: FakeX, status: int, code: str) -> None:
    connect(x_client)
    _live(x_client)
    a = x_client.post("/api/x/test-post").json()
    fake_x.write_status = status
    done = _approve(x_client, a["id"])
    assert done["status"] == "failed" and done["execution_result"]["executed"] is False and done["execution_result"]["error_code"] == code
    assert x_client.get("/api/x/operator").json()["today"][0]["done"] == 0


def test_read_only_token_failure_disables_that_write(x_client: TestClient, fake_x: FakeX) -> None:
    connect(x_client)
    _live(x_client)
    fake_x.access_level = "read"  # e.g. the token was regenerated without write permission
    a = x_client.post("/api/x/test-post").json()
    done = _approve(x_client, a["id"])
    assert done["status"] == "failed"
    status = x_client.get("/api/x/status").json()
    assert status["capabilities"]["write_post"]["status"] == "unavailable"
    nxt = x_client.post("/api/approvals", json={"platform": "x", "action": "post", "text": "another post"}).json()
    assert nxt["guard_result"]["allowed"] is False and "does not allow post" in nxt["guard_result"]["reason"]


def test_daily_limit_blocks_at_execution(x_client: TestClient, fake_x: FakeX) -> None:
    connect(x_client)
    _live(x_client)
    x_client.put("/api/social/permissions/x/post", json={"max_per_day": 1, "max_per_hour": 5})
    first = x_client.post("/api/approvals", json={"platform": "x", "action": "post", "text": "post one"}).json()
    second = x_client.post("/api/approvals", json={"platform": "x", "action": "post", "text": "post two"}).json()
    assert _approve(x_client, first["id"])["status"] == "executed"
    blocked = _approve(x_client, second["id"])
    assert blocked["status"] == "failed" and "Daily limit for post reached" in blocked["execution_result"]["reason"]
    assert len(fake_x.writes) == 1


# --- edits & learning ------------------------------------------------------------------------------------------


def test_edit_and_approve_keeps_original_and_learns(x_client: TestClient, fake_x: FakeX, fake_provider: FakeProvider) -> None:
    connect(x_client)
    texts = ["Great insight! Funding is high right now #btc #crypto and Price Chat is watching it closely for you all today",
             "Love this! Open interest is climbing #markets and Price Chat members are talking about it a lot this week"]
    for i, t in enumerate(texts):
        a = x_client.post("/api/approvals", json={"platform": "x", "action": "post", "text": t}).json()
        edited = "Funding is high; open interest says the same." if i == 0 else "Open interest keeps climbing."
        done = _approve(x_client, a["id"], text=edited)
        assert done["payload"]["original_text"] == t and done["payload"]["text"] == edited
    r = x_client.post("/api/approvals", json={"platform": "x", "action": "post", "text": "meh"}).json()
    x_client.post(f"/api/approvals/{r['id']}/decision", json={"decision": "rejected"})

    learning = {s["key"] for s in x_client.get("/api/x/operator").json()["learning"]}
    assert {"edit:shortened", "edit:removed_hashtags", "edit:removed_brand_mention", "edit:removed_generic_opening"} <= learning
    kinds = {(f["target_kind"], f["decision"]) for f in x_client.get("/api/feedback").json()}
    assert {("social_action", "edit"), ("social_action", "reject")} <= kinds

    # the writer now gets these as instructions
    o = _opportunity(x_client, fake_x)
    x_client.post(f"/api/x/opportunities/{o['id']}/draft-reply", json={})
    system = fake_provider.calls[-1][0][0].content
    assert "Do not use hashtags." in system and "Do not mention Price Chat." in system


def test_feedback_endpoint_refuses_queue_items(x_client: TestClient) -> None:
    assert x_client.post("/api/feedback/social_action/1", json={"decision": "approve"}).status_code == 422


def test_metrics_are_cached_between_reads(x_client: TestClient, fake_x: FakeX) -> None:
    connect(x_client)
    fake_x.tweet(OUR_ID, "our explainer", kind="own")
    x_client.post("/api/x/sync")
    def metric_calls() -> list:
        return [c for c in fake_x.calls if c[0] == "/2/tweets" and c[1].get("ids") != "20"]  # 20 = connection-test probe

    first = metric_calls()
    x_client.post("/api/x/sync")  # minutes later: nothing is stale yet
    assert metric_calls() == first and len(first) == 1


def test_claims_about_price_chat_without_knowledge_are_flagged(x_client: TestClient, fake_x: FakeX, fake_provider: FakeProvider) -> None:
    o = _opportunity(x_client, fake_x, "self-claim: are the funding alerts broken? explain")
    a = x_client.post(f"/api/x/opportunities/{o['id']}/draft-reply", json={}).json()
    assert any("not in approved knowledge" in w for w in a["payload"]["warnings"])
    assert "Never state anything about Price Chat itself" in fake_provider.calls[-1][0][0].content


def test_placeholders_and_links_are_rewritten(x_client: TestClient, fake_x: FakeX) -> None:
    o = _opportunity(x_client, fake_x, "placeholder-bait: where is the funding recap? explain")
    a = x_client.post(f"/api/x/opportunities/{o['id']}/draft-reply", json={}).json()
    assert "[" not in a["payload"]["text"] and not a["payload"]["warnings"]


def test_invented_figures_and_sources_are_rewritten(x_client: TestClient, fake_x: FakeX) -> None:
    from app.social.x_replies import unsupported_facts

    assert unsupported_facts("Over $2B left ETH funds, per CoinShares.", "eth funds") == "$2B"
    assert unsupported_facts("Funding at 0.12% is high.", "funding is at 0.12% today") is None
    o = _opportunity(x_client, fake_x, "number-bait: is funding money leaving eth? explain")
    a = x_client.post(f"/api/x/opportunities/{o['id']}/draft-reply", json={}).json()
    assert "$2B" not in a["payload"]["text"] and not a["payload"]["warnings"]
