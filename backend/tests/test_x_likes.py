"""X likes pilot: AI-recommended likes, human approval, guard, execution and memory.

Every X request goes to the in-memory FakeX; no test can perform a real like.
"""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.enums import SocialOpportunityKind
from app.models.social import SocialPost
from app.models.social_intel import SocialOpportunity, SocialProfile, SocialRelationship
from tests.fakes import OUR_ID, FakeX
from tests.test_x_social import connect

LIKES_PATH = f"/2/users/{OUR_ID}/likes"


def _recent(hours: float = 1) -> str:
    return (utcnow() - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _setup(c: TestClient, fake_x: FakeX, posts: list[tuple[str, str]] | None = None, user_keys: bool = True) -> list[dict]:
    """Monitored topic → X posts → AI opportunities (the fake model recommends a like for each)."""
    connect(c, user_keys=user_keys)
    c.post("/api/x/rules", json={"name": "Funding", "type": "keyword", "query": "funding", "max_results": 10})
    posts = posts or [("2001", "funding rates are running hot across majors this week")]
    for author, text in posts:
        fake_x.tweet(author, text, kind="search", created_at=_recent())
    c.post("/api/x/sync")
    c.post("/api/jobs/x.opportunities/run")
    return c.get("/api/x/likes", params={"include_filtered": True}).json()["candidates"]


def _propose(c: TestClient, opp_id: int) -> dict:
    r = c.post(f"/api/x/likes/{opp_id}/propose")
    assert r.status_code == 201, r.text
    return r.json()


def _decide(c: TestClient, approval_id: int, approve: bool = True) -> dict:
    return c.post(f"/api/approvals/{approval_id}/decision", json={"decision": "approved" if approve else "rejected"}).json()


def _live(c: TestClient) -> None:
    c.patch("/api/social/guard", json={"dry_run": False})


def _events(c: TestClient) -> list[dict]:
    return c.get("/api/activity", params={"level": "audit"}).json() + c.get("/api/activity").json()


# --- capability ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("user_keys", "level", "expected"), [(True, "read-write", "available"), (True, "read", "unavailable"),
                                                               (False, "read-write", "needs_user_auth")])
def test_like_capability_detection(x_client: TestClient, fake_x: FakeX, user_keys: bool, level: str, expected: str) -> None:
    fake_x.access_level = level
    connect(x_client, user_keys=user_keys)
    st = x_client.get("/api/x/likes").json()["status"]
    assert st["capability"] == expected and st["automatic"] == "locked" and st["permission"] == "approval_required"
    assert (st["daily_limit"], st["hourly_limit"], st["used_today"]) == (20, 5, 0)
    assert st["dry_run"] is True and st["ready_to_execute"] is False  # dry run is on by default
    assert fake_x.writes == []  # detection never writes


# --- recommendation & proposal -------------------------------------------------------------------------------------


def test_like_proposal_goes_to_the_queue_with_context(x_client: TestClient, fake_x: FakeX) -> None:
    cands = _setup(x_client, fake_x)
    assert len(cands) == 1
    c = cands[0]
    assert c["suggested_action"] == "like" and c["post"]["author"] == "bigtrader" and "low-value reply" in c["reason"]
    a = _propose(x_client, c["opportunity_id"])
    assert a["action"] == "like" and a["status"] == "pending" and a["target_external_id"] == c["post"]["external_id"]
    p = a["payload"]
    assert p["target_author_id"] == "2001" and p["daily_likes_used"] == 0 and p["daily_limit"] == 20
    assert p["original_post"]["url"] and p["reason"] == c["reason"]
    assert _propose(x_client, c["opportunity_id"])["id"] == a["id"]  # reuses the pending request
    assert x_client.get("/api/x/likes").json()["candidates"][0]["pending_approval_id"] == a["id"]
    assert any(e["message"].startswith("Like proposed: @bigtrader's post") for e in _events(x_client))
    assert fake_x.writes == []


def test_weak_candidates_are_filtered_with_reasons(x_client: TestClient, fake_x: FakeX, db: Session) -> None:
    _setup(x_client, fake_x, [("2001", "funding rates are running hot this week"), ("2002", "funding update from the news desk"),
                              ("2001", "another funding chart from big trader")])
    opps = {o.id: o for o in db.scalars(select(SocialOpportunity))}
    posts = {p.id: p for p in db.scalars(select(SocialPost))}
    first, second, third = sorted(opps)
    posts[opps[first].post_id].posted_at = utcnow() - timedelta(days=5)
    opps[second].kind = SocialOpportunityKind.QUESTION
    db.commit()
    out = x_client.get("/api/x/likes", params={"include_filtered": True}).json()
    assert [c["opportunity_id"] for c in out["candidates"]] == [third]
    problems = {f["opportunity_id"]: f["problems"] for f in out["filtered"]}
    assert "stale" in problems[first][0] and problems[second] == ["needs a reply, not a like"]
    assert x_client.post(f"/api/x/likes/{first}/propose").status_code == 409
    # the Growth Pilot feed can be filtered to like suggestions only
    feed = x_client.get("/api/growth/feed", params={"action": "like"}).json()
    assert feed and all("Like" in i["suggested_actions"] for i in feed)


# --- approval -------------------------------------------------------------------------------------------------------


def test_no_approval_no_like(x_client: TestClient, fake_x: FakeX, db: Session) -> None:
    import asyncio

    from app.models.social_intel import ApprovalRequest
    from app.social.approvals import execute

    c = _setup(x_client, fake_x)[0]
    a = _propose(x_client, c["opportunity_id"])
    assert a["guard_result"]["allowed"] is True and a["guard_result"]["executable"] is False
    _live(x_client)
    row = db.get(ApprovalRequest, a["id"])
    with pytest.raises(PermissionError):
        asyncio.run(execute(db, 1, row, settings=None, x_runtime=None))  # type: ignore[arg-type]
    assert fake_x.writes == []


def test_rejected_like_is_never_sent(x_client: TestClient, fake_x: FakeX) -> None:
    c = _setup(x_client, fake_x)[0]
    a = _propose(x_client, c["opportunity_id"])
    _live(x_client)
    assert _decide(x_client, a["id"], approve=False)["status"] == "rejected"
    assert fake_x.writes == []
    assert any(e["message"].startswith("Like rejected") for e in _events(x_client))


def test_dry_run_shows_would_like(x_client: TestClient, fake_x: FakeX) -> None:
    c = _setup(x_client, fake_x)[0]
    done = _decide(x_client, _propose(x_client, c["opportunity_id"])["id"])
    assert done["status"] == "approved" and done["execution_result"]["dry_run"] is True
    assert done["execution_result"]["would_execute"]["target_post_id"] == c["post"]["external_id"]
    assert fake_x.writes == []
    assert any(e["message"].startswith("WOULD LIKE POST: @bigtrader's post") for e in _events(x_client))
    assert x_client.get("/api/x/likes").json()["status"]["used_today"] == 0  # dry runs don't count


# --- execution -------------------------------------------------------------------------------------------------------


def test_successful_like_is_confirmed_recorded_and_remembered(x_client: TestClient, fake_x: FakeX, db: Session) -> None:
    c = _setup(x_client, fake_x)[0]
    a = _propose(x_client, c["opportunity_id"])
    _live(x_client)
    done = _decide(x_client, a["id"])
    assert done["status"] == "executed" and done["executed_at"]
    assert fake_x.writes == [(LIKES_PATH, {"tweet_id": c["post"]["external_id"]})]
    r = done["execution_result"]
    assert r["executed"] is True and r["response"] == {"liked": True} and r["approval_id"] == a["id"]
    assert r["target_post_id"] == c["post"]["external_id"] and r["target_author_id"] == "2001" and r["target_account"] == "bigtrader"
    assert r["executed_by"]["user_id"] == OUR_ID and r["url"] == c["post"]["url"] and r["executed_at"]

    rel = db.scalar(select(SocialRelationship).join(SocialProfile, SocialProfile.id == SocialRelationship.profile_id)
                    .where(SocialProfile.username == "bigtrader"))
    assert rel is not None and rel.our_like_count == 1 and rel.last_liked_post_id == c["post"]["external_id"]
    assert rel.last_liked_at is not None and rel.last_interaction_at is not None and "funding" in rel.topics

    assert any(e["message"] == "Liked @bigtrader's post about Funding rates." for e in _events(x_client))
    st = x_client.get("/api/x/likes").json()
    assert st["status"]["used_today"] == 1 and st["candidates"] == []  # liked posts are not recommended again


def test_unconfirmed_like_is_failed(x_client: TestClient, fake_x: FakeX) -> None:
    c = _setup(x_client, fake_x)[0]
    a = _propose(x_client, c["opportunity_id"])
    _live(x_client)
    fake_x.like_confirms = False
    done = _decide(x_client, a["id"])
    assert done["status"] == "failed" and done["execution_result"]["error_kind"] == "not_confirmed"
    assert x_client.get("/api/x/likes").json()["status"]["used_today"] == 0


@pytest.mark.parametrize(("status", "kind", "words"), [
    (401, "auth", "401"), (402, "billing", "402"), (403, "permission", "Read and write"),
    (404, "unavailable", "deleted"), (429, "rate_limit", "429"), (503, "x_service", "5xx"),
])
def test_x_errors_are_explained(x_client: TestClient, fake_x: FakeX, status: int, kind: str, words: str) -> None:
    c = _setup(x_client, fake_x)[0]
    a = _propose(x_client, c["opportunity_id"])
    _live(x_client)
    fake_x.write_status = status
    done = _decide(x_client, a["id"])
    r = done["execution_result"]
    assert done["status"] == "failed" and r["executed"] is False and r["error_kind"] == kind and words in r["error"]
    assert "oauth" not in r["error"].lower()  # no credentials or raw auth data
    assert any(e["message"].startswith("Like failed:") for e in _events(x_client))
    if status == 404:  # the guard won't propose the same post again
        fake_x.write_status = None
        again = x_client.post("/api/approvals", json={"platform": "x", "action": "like", "target_external_id": c["post"]["external_id"],
                                                      "target_username": "bigtrader"}).json()
        assert again["guard_result"]["allowed"] is False and "no longer available" in again["guard_result"]["reason"]


# --- guard -------------------------------------------------------------------------------------------------------------


def test_duplicate_like_is_blocked(x_client: TestClient, fake_x: FakeX) -> None:
    c = _setup(x_client, fake_x)[0]
    _live(x_client)
    first = _propose(x_client, c["opportunity_id"])
    second = x_client.post("/api/approvals", json={"platform": "x", "action": "like", "target_external_id": c["post"]["external_id"],
                                                   "target_username": "bigtrader"}).json()
    assert _decide(x_client, first["id"])["status"] == "executed"
    blocked = _decide(x_client, second["id"])
    assert blocked["status"] == "failed" and "Already liked this post" in blocked["execution_result"]["reason"]
    assert len(fake_x.writes) == 1
    assert any(e["message"].startswith("Like blocked: Already liked this post") for e in _events(x_client))


@pytest.mark.parametrize(("limits", "expected"), [({"max_per_day": 1, "max_per_hour": 5}, "Daily limit for like reached (1/day)"),
                                                  ({"max_per_day": 20, "max_per_hour": 1}, "Hourly limit for like reached (1/hour)")])
def test_daily_and_hourly_like_limits(x_client: TestClient, fake_x: FakeX, limits: dict, expected: str) -> None:
    cands = _setup(x_client, fake_x, [("2001", "funding rates are running hot this week"), ("2002", "funding update, open interest climbing")])
    assert len(cands) == 2
    x_client.put("/api/social/permissions/x/like", json=limits)
    _live(x_client)
    a, b = (_propose(x_client, c["opportunity_id"]) for c in cands)
    assert _decide(x_client, a["id"])["status"] == "executed"
    blocked = _decide(x_client, b["id"])
    assert blocked["status"] == "failed" and expected in blocked["execution_result"]["reason"]
    assert len(fake_x.writes) == 1


def test_same_account_cooldown(x_client: TestClient, fake_x: FakeX) -> None:
    _setup(x_client, fake_x, [("2001", "funding rates are running hot this week"), ("2001", "funding chart: open interest climbing")])
    x_client.patch("/api/social/guard", json={"cooldown_minutes": 0})  # only the like-specific account cooldown remains
    out = x_client.get("/api/x/likes", params={"include_filtered": True}).json()
    assert len(out["candidates"]) == 1  # one post per account in the list
    other = next(f for f in out["filtered"] if "already in the list" in f["problems"][0])
    _live(x_client)
    assert _decide(x_client, _propose(x_client, out["candidates"][0]["opportunity_id"])["id"])["status"] == "executed"
    second = x_client.post("/api/approvals", json={"platform": "x", "action": "like", "target_external_id": other["post"]["external_id"],
                                                   "target_username": "bigtrader"}).json()
    assert second["guard_result"]["allowed"] is False and "Already liked a post by @bigtrader" in second["guard_result"]["reason"]
    _decide(x_client, second["id"], approve=False)
    assert x_client.post(f"/api/x/likes/{other['opportunity_id']}/propose").status_code == 409  # over-targeted account
    assert len(fake_x.writes) == 1


def test_kill_switch_blocks_likes(x_client: TestClient, fake_x: FakeX) -> None:
    c = _setup(x_client, fake_x)[0]
    a = _propose(x_client, c["opportunity_id"])
    _live(x_client)
    x_client.put("/api/social/actions", json={"active": False})
    done = _decide(x_client, a["id"])
    assert done["status"] == "failed" and "paused" in done["execution_result"]["reason"]
    assert x_client.get("/api/x/likes").json()["status"]["problems"] == ["Social actions are paused"]
    assert fake_x.writes == []


def test_likes_need_user_auth_and_automatic_stays_locked(x_client: TestClient, fake_x: FakeX) -> None:
    c = _setup(x_client, fake_x, user_keys=False)[0]
    a = _propose(x_client, c["opportunity_id"])
    assert a["guard_result"]["allowed"] is False and "user-context" in a["guard_result"]["reason"]
    r = x_client.put("/api/social/permissions/x/like", json={"mode": "automatic"})
    assert r.status_code in (400, 422) and "locked" in r.text.lower()
    assert fake_x.writes == []
