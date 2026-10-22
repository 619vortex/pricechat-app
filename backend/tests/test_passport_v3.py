"""Kiwi Passport V3: replies and reposts sent from Price Chat as the member (never paid: X developer policy), the
member's X token (encrypted, refreshed, never shared), "Today" (at most 3 missions), the Telegram daily cap, and the
Telegram allow-list repair. Every X / Telegram call goes to an in-memory fake."""

from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.members import missions, reputation, service, verified
from app.members.catalog import ensure_catalog
from app.models.enums import Platform
from app.models.members import LinkedIdentity, Member, MemberAction, MemberAuditLog, MemberMission, MemberXToken, ReputationEvent
from app.models.social import SocialPost
from app.models.telegram import TelegramMessage, TelegramSource
from app.telegram.ingestion import looks_like_unsigned_group_id, repair_source_ids
from tests.test_members import (  # noqa: F401  (fixtures)
    app_and_client, fake_tg, google_sign_in, mc, me, other_browser, providers, x_sign_in, x_start,
)

POST = "1840000000000000001"  # numeric, like real X post ids


def own_post(db: Session, pid: str = POST, hours_ago: float = 2) -> SocialPost:
    p = SocialPost(platform=Platform.X, external_id=pid, text="Bitcoin isn't moving the way people think", is_own=True,
                   url=f"https://x.com/pricechat/status/{pid}", posted_at=utcnow() - timedelta(hours=hours_ago), metrics={"likes": 3})
    db.add(p)
    db.commit()
    return p


def xp_of(db: Session, name: str) -> int:
    db.expire_all()
    return db.scalar(select(Member.xp_total).where(Member.username_key == name.lower())) or 0


# --- X: reply / repost inside Price Chat ---------------------------------------------------------------------------


def test_reply_is_sent_as_the_member_and_never_pays_xp(mc: TestClient, providers, db: Session) -> None:  # noqa: F811
    mc.app.state.runtime.settings.x_member_write = True  # type: ignore[attr-defined]  # in-card replies / reposts on
    x_sign_in(mc)
    own_post(db)
    missions.generate(db)
    p = me(mc)
    xp_before = p["member"]["xp"]
    reply_card = next(m for m in p["missions"] if m["kind"] == "x_reply")
    assert reply_card["cta"] == {"label": "Reply", "action": "x_reply", "post_id": POST} and reply_card["xp"] == 0

    r = mc.post("/api/members/me/x/reply", json={"post_id": POST, "text": "  The funding rates agree with you  "}).json()
    assert r["ok"] and r["code"] == "sent" and r["message"] == "Replied" and r["url"].endswith("/status/91")
    assert providers.tweets == [{"text": "The funding rates agree with you", "reply": {"in_reply_to_tweet_id": POST},
                                 "auth": "Bearer x-user-token"}]  # the member's own token, the member's own words
    p = me(mc)
    assert p["member"]["xp"] == xp_before  # X developer policy: no reward for actions on X
    assert next(m for m in p["missions"] if m["kind"] == "x_reply")["status"] == "completed"
    assert not [e for e in p["recent"] if e["source"] == "x" and "Connected" not in e["reason"]]
    audit = db.scalar(select(MemberAuditLog).where(MemberAuditLog.action == "x_reply"))
    assert audit.details == {"post_id": POST, "result_id": "91"} and "token" not in str(audit.details)


def test_repost_is_sent_once_and_never_pays_xp(mc: TestClient, providers, db: Session) -> None:  # noqa: F811
    x_sign_in(mc)
    own_post(db)
    missions.generate(db)
    xp_before = me(mc)["member"]["xp"]
    r = mc.post("/api/members/me/x/repost", json={"post_id": POST}).json()
    assert r["ok"] and r["message"] == "Reposted"
    assert providers.reposts == [("42", POST, "Bearer x-user-token")]
    again = mc.post("/api/members/me/x/repost", json={"post_id": POST}).json()
    assert again["ok"] and again["message"] == "Reposted" and len(providers.reposts) == 1
    p = me(mc)
    assert p["member"]["xp"] == xp_before
    assert next(m for m in p["missions"] if m["kind"] == "x_repost")["status"] == "completed"


def test_actions_only_target_price_chat_posts_and_need_a_connected_member(mc: TestClient, other_browser: TestClient, providers,  # noqa: F811
                                                                          db: Session) -> None:
    assert mc.post("/api/members/me/x/repost", json={"post_id": POST}).status_code == 401  # signed out
    google_sign_in(mc)
    own_post(db)
    assert mc.post("/api/members/me/x/repost", json={"post_id": POST}).json()["code"] == "not_connected"
    x_sign_in(mc, "link")
    stranger = SocialPost(platform=Platform.X, external_id="1840000000000000099", text="someone else's post", is_own=False)
    db.add(stranger)
    db.commit()
    r = mc.post("/api/members/me/x/repost", json={"post_id": "1840000000000000099"}).json()
    assert not r["ok"] and r["code"] == "not_allowed"
    assert mc.post("/api/members/me/x/repost", json={"post_id": "not-a-number"}).status_code == 422
    assert mc.post("/api/members/me/x/reply", json={"post_id": POST, "text": "   "}).status_code == 400
    assert mc.post("/api/members/me/x/reply", json={"post_id": POST, "text": "x" * 281}).json()["code"] == "invalid"
    # another member (another browser) can never use this member's token
    providers.google_sub = "g-2"
    google_sign_in(other_browser)
    r = other_browser.post("/api/members/me/x/repost", json={"post_id": POST}).json()
    assert r["code"] == "not_connected" and providers.reposts == []


def test_plain_language_errors_and_reconnect(mc: TestClient, providers, db: Session) -> None:  # noqa: F811
    mc.app.state.runtime.settings.x_member_write = True  # type: ignore[attr-defined]  # in-card replies / reposts on
    x_sign_in(mc)
    own_post(db)
    providers.x_write_error = (429, {"title": "Too Many Requests"})
    r = mc.post("/api/members/me/x/repost", json={"post_id": POST}).json()
    assert r == {"ok": False, "code": "rate_limited", "message": "X is limiting how fast this can happen. Please try again in a few minutes.",
                 "url": None, "reconnect": False}
    providers.x_write_error = (403, {"title": "Forbidden", "detail": "Your client app is not configured with the appropriate oauth1 app "
                                                                      "permissions for this endpoint.", "type": "oauth scope"})
    r = mc.post("/api/members/me/x/reply", json={"post_id": POST, "text": "a real reply"}).json()
    assert r["code"] == "reconnect" and r["reconnect"] and "Reconnect" in r["message"]
    assert "oauth" not in r["message"].lower()  # never technical wording for members
    p = me(mc)
    assert p["x"]["needs_reconnect"] and not p["x"]["can_act"]
    assert db.scalar(select(MemberXToken.status)) == "reconnect"
    # nothing was marked done, nothing recorded as sent
    assert db.scalar(select(func.count()).select_from(MemberAction).where(MemberAction.kind.in_(["x_reply", "x_repost"]))) == 0
    providers.x_write_error = None
    x_sign_in(mc, "link")  # reconnecting stores a fresh token
    assert me(mc)["x"]["can_act"]


def test_expired_token_is_refreshed_and_rotated(mc: TestClient, providers, db: Session) -> None:  # noqa: F811
    x_sign_in(mc)
    own_post(db)
    tok = db.scalar(select(MemberXToken))
    tok.expires_at = utcnow() - timedelta(minutes=1)
    db.commit()
    r = mc.post("/api/members/me/x/repost", json={"post_id": POST}).json()
    assert r["ok"] and providers.refreshes == 1 and providers.reposts[0][2] == "Bearer x-user-token-1"
    db.expire_all()
    tok = db.scalar(select(MemberXToken))
    assert tok.expires_at > utcnow() and "x-refresh-2" not in (tok.refresh_token or "")  # rotated, still encrypted


def test_disconnecting_x_deletes_the_token(mc: TestClient, providers, db: Session) -> None:  # noqa: F811
    google_sign_in(mc)
    x_sign_in(mc, "link")
    assert db.scalar(select(func.count()).select_from(MemberXToken)) == 1
    assert mc.post("/api/members/me/disconnect/x").status_code == 200
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(MemberXToken)) == 0


def test_x_scopes_never_include_likes(mc: TestClient, providers) -> None:  # noqa: F811
    x_start(mc)  # asserts the exact scopes (no like.write: X removed likes from the API)


# --- Today --------------------------------------------------------------------------------------------------------


def test_today_shows_at_most_three_and_puts_xp_first(mc: TestClient, providers, db: Session) -> None:  # noqa: F811
    google_sign_in(mc)
    own_post(db)
    from tests.test_missions import short

    short(db, "vid1")
    missions.generate(db, "https://t.me/thepricechat")
    p = me(mc)
    assert len(p["today"]) == 3
    assert [m["kind"] for m in p["today"]] == ["open_short", "link_telegram", "link_x"]  # new content first
    assert all(m["cta"] for m in p["today"]) and "daily_visit" not in {m["kind"] for m in p["today"] + p["more_missions"]}
    # X isn't connected: no X missions at all, the "Connect X" card stands in for them
    assert not {"x_like", "x_reply", "x_repost"} & {m["kind"] for m in p["today"] + p["more_missions"]}


# --- Telegram -----------------------------------------------------------------------------------------------------


def test_telegram_xp_has_a_configurable_daily_cap(db: Session) -> None:
    ensure_catalog(db)
    m = service.create_member(db)
    m.created_at = utcnow() - timedelta(days=3)
    service.link_identity(db, m, "telegram", "777", "tguser", None)
    db.scalar(select(LinkedIdentity).where(LinkedIdentity.member_id == m.id)).verified_at = utcnow() - timedelta(days=1)
    src = TelegramSource(chat_id=-100555, display_name="Price Chat", enabled=True, ingestion_enabled=True)
    db.add(src)
    db.flush()
    start = utcnow().replace(hour=0, minute=5, second=0, microsecond=0)
    for i in range(12):
        db.add(TelegramMessage(source_id=src.id, chat_id=-100555, message_id=500 + i, author_id=777, author_is_bot=False,
                               text=f"a genuinely different thought about the market number {i}", sent_at=start + timedelta(minutes=i)))
    db.commit()
    verified.telegram_participation(db)

    def tg_today() -> int:
        return int(db.scalar(select(func.coalesce(func.sum(ReputationEvent.xp), 0)).where(
            ReputationEvent.member_id == m.id, ReputationEvent.event_type.like("telegram.%"),
            ReputationEvent.event_type != "telegram.message")) or 0)  # the per-message XP has its own cap

    assert tg_today() == 10  # +3 first, +1 each after (cap 5), +2 active day = 10, never more than the cap
    rs = reputation.settings(db)
    rs.telegram_daily_cap = 6
    db.commit()
    db.query(ReputationEvent).filter(ReputationEvent.member_id == m.id, ReputationEvent.event_type.like("telegram.%")).delete()
    db.commit()
    verified.telegram_participation(db)
    assert tg_today() == 6


def test_unsigned_group_ids_are_repaired(db: Session) -> None:
    assert looks_like_unsigned_group_id(1004310183499) and not looks_like_unsigned_group_id(-1004310183499)
    assert not looks_like_unsigned_group_id(6359691272)  # a user id is left alone
    db.add(TelegramSource(chat_id=1004310183499, display_name="1004310183499", enabled=True, ingestion_enabled=True))
    db.commit()
    assert repair_source_ids(db) == 1
    src = db.scalar(select(TelegramSource))
    assert src.chat_id == -1004310183499 and src.display_name == "-1004310183499"
    assert repair_source_ids(db) == 0


# --- admin ----------------------------------------------------------------------------------------------------------


def test_admin_sees_x_connection_state_but_never_tokens(mc: TestClient, providers, db: Session) -> None:  # noqa: F811
    x_sign_in(mc)
    pid = me(mc)["member"]["public_id"] if "public_id" in me(mc)["member"] else db.scalar(select(Member.public_id))
    detail = mc.get(f"/api/members-admin/members/{pid}")
    assert detail.status_code == 200
    x = detail.json()["x_connection"]
    assert x["x_user_id"] == "42" and "tweet.write" in x["scopes"] and x["status"] == "active" and x["has_refresh_token"]
    assert "x-user-token" not in detail.text and "x-refresh" not in detail.text
    r = mc.put("/api/members-admin/reputation", json={"telegram_daily_cap": 8})
    assert r.json()["settings"]["telegram_daily_cap"] == 8


def test_today_keeps_one_slot_for_supporting_on_x_once_connected(mc: TestClient, providers, db: Session) -> None:  # noqa: F811
    mc.app.state.runtime.settings.x_member_write = True  # type: ignore[attr-defined]  # in-card replies / reposts on
    x_sign_in(mc)
    own_post(db)
    from tests.test_missions import short

    short(db, "vid1")
    missions.generate(db, "https://t.me/thepricechat")
    p = me(mc)
    assert [m["kind"] for m in p["today"]] == ["open_short", "link_telegram", "x_reply"]  # XP first, then one in-app X action
    assert {"x_repost", "x_like"} <= {m["kind"] for m in p["more_missions"]}
    like = next(m for m in p["more_missions"] if m["kind"] == "x_like")
    assert like["cta"]["action"] == "open" and like["cta"]["url"].endswith(POST) and like["xp"] == 0  # honest: opens X


def test_admin_mission_engine_shows_caps_and_x_policy(mc: TestClient, providers, db: Session) -> None:  # noqa: F811
    x_sign_in(mc)
    own_post(db)
    missions.generate(db)
    rows = {r["kind"]: r for r in mc.get("/api/members-admin/missions").json()["missions"]}
    assert rows["x_reply"]["pays_xp"] is False and "policy" in rows["x_reply"]["no_xp_reason"] and rows["x_reply"]["xp"] == 0
    assert rows["telegram_participation"]["xp"] == 5 and rows["telegram_participation"]["daily_cap"] == 2  # active-day rule
    assert rows["link_x"]["pays_xp"] is True and rows["link_x"]["verification_method"]


def test_read_only_x_missions_open_the_post_on_x_instead_of_asking_to_reconnect(mc: TestClient, providers, db: Session) -> None:  # noqa: F811
    """Connect X is read-only by default: the X missions open the post on X, and nobody is told to reconnect."""
    x_sign_in(mc)  # signs in with X (which links it); write access stays off
    own_post(db)
    missions.generate(db)
    p = me(mc)
    assert p["x"]["needs_reconnect"] is False and p["x"]["can_act"] is False
    ctas = [m["cta"] for m in p["missions"] if m["kind"] in ("x_reply", "x_conversation", "x_repost") and m["cta"]]
    assert ctas and all(c["action"] == "open" and c["url"].startswith("https://x.com/intent/") for c in ctas)
    assert not any(c.get("label") == "Reconnect X" for c in ctas)
