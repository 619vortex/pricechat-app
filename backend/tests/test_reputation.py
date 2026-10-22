"""X member login (OAuth 2.0), Telegram identity, Combot legacy reputation and the Kiwi Rank Score.
Every X / Combot / Telegram call goes to an in-memory fake; nothing live is contacted."""

import asyncio
import json
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.members import combot, missions, reputation, service, verified
from app.members import xp as ledger
from app.members.catalog import ensure_catalog
from app.models.members import (
    CombotImport, CombotRecord, LinkedIdentity, Member, MemberNotification, Mission, ModerationFlag, ReputationEvent, XPRule,
)
from app.models.social import SocialPost
from app.models.enums import Platform
from app.models.telegram import TelegramMessage, TelegramSource
from tests.test_members import (  # noqa: F401  (fixtures)
    app_and_client, fake_tg, google_sign_in, mc, me, other_browser, providers, x_sign_in, x_start,
)


def member(db: Session, name: str) -> Member:
    ensure_catalog(db)
    m = service.create_member(db)
    m.username, m.username_key = name, name.lower()
    db.commit()
    return m


def link_tg(db: Session, m: Member, uid: str, username: str = "tguser") -> None:
    service.link_identity(db, m, "telegram", uid, username, None)
    db.commit()


CSV = """user_id,username,name,xp,messages,reputation,rank
5551,alice_old,Alice,2905,4100,37,Legend
5552,bob,Bob,1000,900,5,Regular
,ghost,No Id,500,10,0,
abc,bad,Bad Id,10,1,0,
5553,carol,Carol,-5,1,0,
5552,bob,Bob,800,850,5,Regular
"""


# --- X member login (OAuth 2.0 + PKCE) --------------------------------------------------------------------------


def test_x_login_uses_the_member_client_and_keeps_the_token_encrypted(mc: TestClient, providers, db: Session) -> None:  # noqa: F811
    mc.app.state.runtime.settings.x_member_write = True  # type: ignore[attr-defined]  # the X app allows replies / reposts
    r = x_sign_in(mc)
    assert r.headers["location"] == "/passport?welcome=x"
    p = me(mc)
    x = {l["platform"]: l for l in p["linked"]}["x"]
    assert x["connected"] and x["username"] == "kiwifan" and x["verified_at"]
    assert providers.revoked == [] and p["x"] == {"connected": True, "username": "kiwifan", "can_act": True, "needs_reconnect": False}
    from app.models.members import MemberXToken

    tok = db.scalar(select(MemberXToken))
    assert tok.x_user_id == "42" and "tweet.write" in tok.scopes.split()
    assert "x-user-token" not in tok.access_token and "x-refresh" not in (tok.refresh_token or "")  # encrypted at rest
    assert "x-user-token" not in mc.get("/api/members/me").text  # and never sent to the browser
    ident = db.scalar(select(LinkedIdentity).where(LinkedIdentity.platform == "x"))
    assert ident.platform_user_id == "42" and ident.display_name == "Kiwi Fan"
    assert not any("oauth/request_token" in u for _, u in providers.calls)  # the brand connector's OAuth 1.0a is not involved


@pytest.mark.parametrize("case,expect", [
    ("cancelled", "cancelled"), ("bad_state", "invalid_state"), ("callback", "callback_mismatch"),
    ("client", "client_rejected"), ("token", "token_failed"), ("permission", "permission"),
])
def test_x_login_errors_are_explained(case: str, expect: str, mc: TestClient, providers) -> None:  # noqa: F811
    state = x_start(mc)
    params = {"state": state, "code": "x-code"}
    if case == "cancelled":
        params = {"state": state, "error": "access_denied"}
    elif case == "bad_state":
        params = {"state": "forged", "code": "x-code"}
    elif case == "callback":
        providers.x_token_error = (400, {"error": "invalid_request", "error_description": "Value passed for the redirect uri did not match"})
    elif case == "client":
        providers.x_token_error = (401, {"error": "unauthorized_client", "error_description": "Missing valid authorization header"})
    elif case == "token":
        providers.x_token_error = (400, {"error": "invalid_request", "error_description": "Value passed for the authorization code was invalid."})
    elif case == "permission":
        providers.x_me_status = 403
    r = mc.get("/api/members/auth/x/callback", params=params, follow_redirects=False)
    q = parse_qs(urlparse(r.headers["location"]).query)
    assert q["code"] == [expect] and q["error"][0]
    assert mc.get("/api/members/session").json()["signed_in"] is False


def test_x_login_expired_state(mc: TestClient, db: Session) -> None:  # noqa: F811
    from app.models.members import MemberAuthRequest

    state = x_start(mc)
    req = db.scalar(select(MemberAuthRequest).where(MemberAuthRequest.provider == "x"))
    req.expires_at = utcnow() - timedelta(minutes=1)
    db.commit()
    r = mc.get("/api/members/auth/x/callback", params={"state": state, "code": "x-code"}, follow_redirects=False)
    assert parse_qs(urlparse(r.headers["location"]).query)["code"] == ["expired_state"]


def test_x_not_configured_is_clear(app_and_client, db: Session) -> None:  # noqa: F811
    from pydantic import SecretStr

    app, c = app_and_client
    app.state.runtime.settings.x_member_client_id = SecretStr("")
    r = c.get("/api/members/auth/x/start", follow_redirects=False)
    assert parse_qs(urlparse(r.headers["location"]).query)["code"] == ["not_configured"]


def test_duplicate_x_identity_is_blocked_flagged_and_resolvable(mc: TestClient, other_browser: TestClient, providers, db: Session) -> None:  # noqa: F811
    google_sign_in(mc)
    x_sign_in(mc, purpose="link")  # X 42 -> member A
    providers.google_sub = "g-2"
    google_sign_in(other_browser)
    r = x_sign_in(other_browser, purpose="link")
    q = parse_qs(urlparse(r.headers["location"]).query)
    assert q["code"] == ["identity_conflict"] and "Price Chat team" in q["error"][0]
    flag = db.scalar(select(ModerationFlag).where(ModerationFlag.kind == "identity_conflict"))
    assert flag is not None and flag.details["platform_user_id"] == "42"
    b = me(other_browser)["member"]["id"]
    out = mc.post(f"/api/members-admin/flags/{flag.id}/transfer-identity", json={"platform": "x", "to_member": b,
                                                                                 "reason": "Proved ownership to a moderator"})
    assert out.status_code == 200
    assert {l["platform"] for l in me(other_browser)["linked"] if l["connected"]} >= {"x"}
    assert not {l["platform"]: l for l in me(mc)["linked"]}["x"]["connected"]
    assert db.scalar(select(func.count()).select_from(ReputationEvent).where(ReputationEvent.event_type == "identity.x_linked")) == 1


# --- Combot import ------------------------------------------------------------------------------------------------


def test_csv_import_preview_then_apply(db: Session) -> None:
    a = member(db, "alice")
    link_tg(db, a, "5551", "alice_new")  # username changed since the export: the id still matches
    imp = combot.preview(db, combot.parse_file(CSV, "csv"), "csv", utcnow() - timedelta(days=2), filename="combot.csv")
    st = imp.stats
    assert (st["rows"], st["valid"], st["invalid"], st["duplicates"]) == (6, 2, 3, 1)
    assert (st["matched"], st["unmatched"]) == (1, 1)
    assert {e["reason"] for e in st["invalid_examples"]} == {"no numeric Telegram user id", "missing or negative XP"}
    assert db.scalar(select(func.count()).select_from(CombotRecord)) == 0  # nothing written before confirmation
    result = combot.apply(db, imp)
    assert result == {"written": 2, "skipped_older": 0}
    bob = db.scalar(select(CombotRecord).where(CombotRecord.telegram_user_id == "5552"))
    assert bob.combot_xp == 1000  # duplicate rows: the higher XP is kept
    db.refresh(a)
    assert a.legacy_points == 500 and a.rank_score == a.xp_total + 500  # 2905 x 0.25 = 726, capped at 500
    with pytest.raises(combot.CombotError):
        combot.apply(db, imp)  # a batch applies once


def test_username_alone_never_matches(db: Session) -> None:
    b = member(db, "bob")
    link_tg(db, b, "9999", "bob")  # same @username as row 5552, different numeric id
    combot.apply(db, combot.preview(db, combot.parse_file(CSV, "csv"), "csv", utcnow()))
    db.refresh(b)
    assert b.legacy_points == 0


def test_older_data_never_overwrites_newer(db: Session) -> None:
    combot.apply(db, combot.preview(db, [{"user_id": 7, "xp": 300}], "json", utcnow() - timedelta(days=1)))
    imp = combot.preview(db, [{"user_id": 7, "xp": 100}], "json", utcnow() - timedelta(days=10))
    assert imp.stats["older_skipped"] == 1
    assert combot.apply(db, imp) == {"written": 0, "skipped_older": 1}
    assert db.scalar(select(CombotRecord.combot_xp).where(CombotRecord.telegram_user_id == "7")) == 300


def test_json_import_and_admin_endpoints(mc: TestClient, db: Session) -> None:  # noqa: F811
    content = json.dumps({"data": [{"user_id": 5551, "xp": 1000, "msg_total": 50, "reputation": 3}]})
    pv = mc.post("/api/members-admin/reputation/import", json={"filename": "export.json", "format": "json", "content": content,
                                                               "source_date": "2026-10-01"}).json()
    assert pv["status"] == "preview" and pv["stats"]["valid"] == 1
    assert mc.post("/api/members-admin/reputation/import", json={"filename": "x.json", "format": "json", "content": "not json"}).status_code == 400
    applied = mc.post(f"/api/members-admin/reputation/import/{pv['id']}/apply").json()
    assert applied["result"]["written"] == 1 and applied["combot"]["records"] == 1 and applied["combot"]["status"] == "manual import"
    actions = {e["action"] for e in mc.get("/api/members-admin/audit").json()["entries"]}
    assert {"combot_import_previewed", "combot_import_applied"} <= actions


def test_rank_score_weighting_cap_and_switch(db: Session) -> None:
    a = member(db, "alice")
    link_tg(db, a, "5551")
    combot.apply(db, combot.preview(db, [{"user_id": 5551, "xp": 1000}], "json", utcnow()))
    db.refresh(a)
    base = a.xp_total
    assert a.legacy_points == 250 and a.rank_score == base + 250  # 1000 x 0.25
    rs = reputation.settings(db)
    rs.combot_weight, rs.legacy_cap = 0.5, 0
    reputation.recalc_all(db)
    db.refresh(a)
    assert a.legacy_points == 500
    rs.combot_enabled = False  # admin: legacy doesn't count
    reputation.recalc_all(db)
    db.refresh(a)
    assert a.legacy_points == 0 and a.rank_score == base
    math = reputation.explain(db, a)
    assert math["combot"]["xp"] == 1000 and math["rank_score"] == base and "Price Chat XP" in math["formula"]


def test_legacy_points_can_rank_a_member_up(db: Session) -> None:
    a = member(db, "veteran")
    combot.apply(db, combot.preview(db, [{"user_id": 4242, "xp": 2905}], "json", utcnow()))
    link_tg(db, a, "4242")
    db.refresh(a)
    assert a.rank_score == a.xp_total + 500 and a.rank_level == 4  # 40 XP + 500 legacy = 540: Scout Kiwi (400)
    notes = [n.title for n in db.scalars(select(MemberNotification).where(MemberNotification.member_id == a.id))]
    assert "Telegram history imported" in notes and "Scout Kiwi unlocked" in notes


def test_no_record_found_does_not_fail_linking(db: Session) -> None:
    reputation.settings(db).combot_mode = "manual"
    db.commit()
    a = member(db, "newbie")
    link_tg(db, a, "123")
    notes = [n.title for n in db.scalars(select(MemberNotification).where(MemberNotification.member_id == a.id))]
    assert "No historical Combot record found" in notes
    assert db.scalar(select(LinkedIdentity).where(LinkedIdentity.member_id == a.id)) is not None


def test_disconnect_removes_legacy_but_keeps_earned_xp(db: Session) -> None:
    a = member(db, "alice")
    link_tg(db, a, "5551")
    service.link_identity(db, a, "x", "77", "a_x", None)
    db.commit()
    combot.apply(db, combot.preview(db, [{"user_id": 5551, "xp": 1000}], "json", utcnow()))
    db.refresh(a)
    xp_before = a.xp_total
    service.disconnect(db, a, "telegram")
    db.refresh(a)
    assert a.legacy_points == 0 and a.xp_total == xp_before and a.rank_score == xp_before


# --- Combot API sync (mocked official API) ------------------------------------------------------------------------


class FakeCombot:
    def __init__(self, pro: bool = True) -> None:
        self.pro = pro
        self.calls: list[str] = []

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[-1]
        q = dict(request.url.params)
        self.calls.append(method)
        assert q["api_key"] == "combot-key" and q["chat_id"] == "-1001234567890"
        if method == "getChats":
            return httpx.Response(200, json={"ok": True, "result": [{"id": -1001234567890, "title": "Price Chat"}]})
        if method == "getChatUsers":
            if not self.pro:
                return httpx.Response(403, json={"ok": False, "error_code": 403, "description": "Pro plan required"})
            page = int(q["page"])
            data = [{"user_id": 5551, "username": "alice", "xp": 2000, "reputation": 9, "msg_total": 500, "active_days": 80}] if page == 0 else \
                   [{"user_id": 5552, "username": "bob", "xp": 40, "msg_total": 12}]
            return httpx.Response(200, json={"ok": True, "data": data, "has_next": page == 0})
        if method == "getXpLeaderboard":
            return httpx.Response(200, json={"ok": True, "result": {"users": [[1200, "Alice", "Legend", 5551, "alice"], [90, "No Id", "Rookie"]],
                                                                    "has_more": False}})
        if method == "getRepLeaderboard":
            return httpx.Response(200, json={"ok": True, "result": {"users": [[7, "Alice", 5551, "alice"]], "has_more": False}})
        if method == "getChatMember":
            if q["user_id"] == "777":
                return httpx.Response(200, json={"ok": True, "result": {"user_id": 777, "xp": 333, "msg_total": 30}})
            return httpx.Response(404, json={"ok": False, "error_code": 404, "description": "Member not found"})
        return httpx.Response(404, json={"ok": False, "error_code": 404, "description": "Method not found"})


def _connect(mc: TestClient, app, fake: FakeCombot) -> None:  # noqa: ANN001
    app.state.runtime.members_transport = fake.transport
    r = mc.put("/api/members-admin/reputation/combot", json={"api_key": "combot-key", "chat_id": "-1001234567890"})
    assert r.status_code == 200, r.text
    assert r.json()["combot"]["status"] == "connected"


def test_combot_api_sync_pro_plan(app_and_client, db: Session) -> None:  # noqa: F811
    app, c = app_and_client
    a, late = member(db, "alice"), member(db, "late")
    link_tg(db, a, "5551")
    link_tg(db, late, "777")  # not in the bulk list: fetched individually
    _connect(c, app, FakeCombot())
    out = c.post("/api/members-admin/reputation/combot/sync").json()
    assert out["result"]["source"] == "api" and out["result"]["written"] == 2 and out["result"]["filled_linked"] == 1
    assert out["combot"]["matched"] == 2 and out["combot"]["unmatched"] == 0 and out["combot"]["last_success_at"]
    db.expire_all()
    assert db.scalar(select(Member.legacy_points).where(Member.id == a.id)) == 500


def test_combot_sync_falls_back_to_leaderboards_without_pro(app_and_client, db: Session) -> None:  # noqa: F811
    app, c = app_and_client
    _connect(c, app, FakeCombot(pro=False))
    out = c.post("/api/members-admin/reputation/combot/sync").json()
    assert out["result"]["source"] == "leaderboard" and out["result"]["written"] == 1  # the row without a user id is dropped
    rec = db.scalar(select(CombotRecord).where(CombotRecord.telegram_user_id == "5551"))
    assert rec.combot_xp == 1200 and rec.combot_rep == 7 and rec.combot_rank == "Legend"


def test_combot_bad_key_and_job_skip(app_and_client, db: Session) -> None:  # noqa: F811
    app, c = app_and_client

    def deny(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"ok": False, "error_code": 401, "description": "Invalid API key"})

    app.state.runtime.members_transport = httpx.MockTransport(deny)
    r = c.put("/api/members-admin/reputation/combot", json={"api_key": "wrong-key-xyz", "chat_id": "-1001234567890"})
    assert r.status_code == 400 and "Invalid API key" in r.json()["detail"]
    with pytest.raises(combot.CombotError):
        asyncio.run(combot.sync(db, app.state.runtime.settings))  # not connected: refuses, never guesses


def test_weights_endpoint_recalculates_everyone(mc: TestClient, db: Session) -> None:  # noqa: F811
    a = member(db, "alice")
    link_tg(db, a, "5551")
    combot.apply(db, combot.preview(db, [{"user_id": 5551, "xp": 1000}], "json", utcnow()))
    r = mc.put("/api/members-admin/reputation", json={"combot_weight": 0.1, "legacy_cap": 0})
    assert r.status_code == 200
    db.expire_all()
    assert db.scalar(select(Member.legacy_points).where(Member.id == a.id)) == 100
    assert mc.put("/api/members-admin/reputation", json={"combot_weight": -1}).status_code == 422


# --- Telegram XP v3, leaderboard, reposts ---------------------------------------------------------------------------


def test_active_community_day_and_daily_telegram_cap(db: Session) -> None:
    a = member(db, "chatty")
    link_tg(db, a, "555")
    ident = db.scalar(select(LinkedIdentity).where(LinkedIdentity.member_id == a.id))
    ident.verified_at = utcnow() - timedelta(days=1)
    src = TelegramSource(chat_id=-100111, display_name="Price Chat", enabled=True)
    db.add(src)
    db.flush()
    for i in range(12):
        db.add(TelegramMessage(source_id=src.id, chat_id=src.chat_id, message_id=i + 1, author_id=555, author_name="U",
                               sent_at=utcnow() - timedelta(minutes=60 - i), text=f"genuinely different market thought number {i}"))
    db.commit()
    verified.telegram_participation(db)
    by = dict(db.execute(select(ReputationEvent.event_type, func.sum(ReputationEvent.xp)).where(
        ReputationEvent.member_id == a.id, ReputationEvent.event_type.like("telegram.%")).group_by(ReputationEvent.event_type)).all())
    assert by == {"telegram.first_participation": 3, "telegram.discussion": 5, "telegram.active_day": 2}  # 10 a day at most


def test_leaderboard_metrics(mc: TestClient, db: Session) -> None:  # noqa: F811
    vet, fresh = member(db, "veteran"), member(db, "fresh")
    link_tg(db, vet, "5551")
    combot.apply(db, combot.preview(db, [{"user_id": 5551, "xp": 1000}], "json", utcnow()))
    ledger.adjust(db, fresh, 100, actor="dashboard", reason="AMA", ref="a")
    default = mc.get("/api/members/leaderboard", params={"period": "all"}).json()
    assert default["metric"] == "score" and [r["username"] for r in default["rows"]][:2] == ["veteran", "fresh"]
    xp = mc.get("/api/members/leaderboard", params={"period": "all", "metric": "xp"}).json()["rows"]
    assert xp[0]["username"] == "fresh"
    legacy = mc.get("/api/members/leaderboard", params={"period": "week", "metric": "legacy"}).json()
    assert [r["username"] for r in legacy["rows"]] == ["veteran"] and "no weekly" in legacy["note"]
    week = mc.get("/api/members/leaderboard", params={"period": "week"}).json()
    assert "all-time" in week["note"] and week["rows"][0]["username"] == "fresh"  # this week: earned XP, not old legacy


def test_profile_shows_reputation_math_and_telegram_history(mc: TestClient, db: Session) -> None:  # noqa: F811
    google_sign_in(mc)
    m = db.scalar(select(Member))
    link_tg(db, m, "5551")
    combot.apply(db, combot.preview(db, [{"user_id": 5551, "xp": 2817, "msg_total": 4000}], "json", utcnow()))
    p = me(mc)
    rep = p["reputation"]
    assert rep["combot"]["xp"] == 2817 and rep["legacy_points"] == 500 and rep["capped"] is True
    assert rep["rank_score"] == p["member"]["xp"] + 500 == p["rank"]["score"]
    assert p["telegram"]["historical"]["combot_xp"] == 2817 and p["telegram"]["telegram_user_id"] == "5551"


def test_repost_xp_is_locked_off_so_nothing_is_checked_or_paid(db: Session) -> None:
    a = member(db, "fan")
    a.created_at = utcnow() - timedelta(days=3)
    service.link_identity(db, a, "x", "42", "fan", None)
    rule = db.scalar(select(XPRule).where(XPRule.event_type == "x.repost_pricechat"))
    assert rule.availability == "unavailable" and rule.enabled is False  # X's developer policy: no rewards for reposts
    db.add(SocialPost(platform=Platform.X, external_id="post-1", text="New Short is up", is_own=True, url="https://x.com/pricechat/status/post-1",
                      posted_at=utcnow() - timedelta(hours=2), metrics={"reposts": 2}))
    db.commit()
    missions.generate(db)
    assert db.scalar(select(Mission).where(Mission.kind == "x_repost", Mission.status == "active")) is not None

    class FakeX:
        calls = 0

        async def reposted_by(self, post_id: str, expected: int) -> dict:
            FakeX.calls += 1
            assert post_id == "post-1" and expected == 2
            return {"data": [{"id": "42", "username": "fan"}, {"id": "999", "username": "stranger"}]}

    assert asyncio.run(missions.verify_reposts(db, FakeX())) == {"checked": 0, "completed": 0}
    assert FakeX.calls == 0  # no X credits spent on a check that could only lead to a reward
    assert db.scalar(select(ReputationEvent.id).where(ReputationEvent.member_id == a.id, ReputationEvent.event_type == "mission.complete")) is None


def test_import_rows_are_not_kept_after_apply(db: Session) -> None:
    imp = combot.preview(db, [{"user_id": 1, "xp": 1}], "json", utcnow())
    assert imp.rows
    combot.apply(db, imp)
    assert db.get(CombotImport, imp.id).rows == []



def test_one_xp_per_telegram_message_up_to_50_a_day(db: Session) -> None:
    a = member(db, "talker")
    link_tg(db, a, "556")
    ident = db.scalar(select(LinkedIdentity).where(LinkedIdentity.member_id == a.id))
    ident.verified_at = utcnow() - timedelta(days=1)
    src = TelegramSource(chat_id=-100112, display_name="Price Chat", enabled=True)
    db.add(src)
    db.flush()
    texts = [f"market thought number {i}" for i in range(60)] + ["market thought number 1", "/start", "ok"]  # 60 distinct, a repeat, a command, too short
    for i, t in enumerate(texts):
        db.add(TelegramMessage(source_id=src.id, chat_id=src.chat_id, message_id=i + 1, author_id=556, author_name="U",
                               sent_at=utcnow() - timedelta(minutes=90) + timedelta(seconds=10 * i), text=t))
    db.commit()
    verified.telegram_participation(db)  # the older Telegram rewards are untouched by the per-message XP
    verified.telegram_message_xp(db)
    verified.telegram_message_xp(db)  # running again pays nothing twice
    xp = lambda t: int(db.scalar(select(func.coalesce(func.sum(ReputationEvent.xp), 0)).where(  # noqa: E731
        ReputationEvent.member_id == a.id, ReputationEvent.event_type == t)) or 0)
    assert xp("telegram.message") == 50  # +1 each, capped at 50 a day
    assert xp("telegram.first_participation") == 3 and xp("telegram.discussion") == 5 and xp("telegram.active_day") == 2
