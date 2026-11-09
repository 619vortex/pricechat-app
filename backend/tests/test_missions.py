"""Profile V2: missions, verification levels, content-driven generation, notifications, activity, public profiles.
All data is created in the test database; no platform is contacted and no social action is performed."""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.members import insights, missions, service, verified
from app.members import xp as ledger
from app.members.catalog import ensure_catalog
from app.models.content_items import ContentItem
from app.models.enums import Platform
from app.models.members import Member, MemberMission, MemberNotification, Mission, ReputationEvent
from app.models.social import SocialPost
from app.models.social_intel import SocialProfile
from app.models.telegram import TelegramMessage, TelegramSource
from tests.test_members import app_and_client, fake_tg, google_sign_in, mc, me, providers, x_sign_in  # noqa: F401  (fixtures)

SELF_X = "9000"


def member(db: Session, name: str = "kiwi") -> Member:
    ensure_catalog(db)
    m = service.create_member(db)
    m.username, m.username_key = name, name.lower()
    m.created_at = utcnow() - timedelta(days=5)
    db.commit()
    return m


def link(db: Session, m: Member, platform: str, uid: str) -> None:
    i = service.link_identity(db, m, platform, uid, f"{platform}_{uid}", None)
    i.verified_at = utcnow() - timedelta(days=2)
    db.commit()


def short(db: Session, vid: str, hours_ago: float = 2, title: str = "He Put His Allowance Into Bitcoin #crypto") -> ContentItem:
    i = ContentItem(platform=Platform.YOUTUBE, external_id=vid, title=title, url=f"https://www.youtube.com/shorts/{vid}",
                    content_type="short", privacy_status="public", available=True, website_visible=True,
                    published_at=utcnow() - timedelta(hours=hours_ago), duration_seconds=45, metrics={"views": 1326})
    db.add(i)
    db.commit()
    return i


def x_world(db: Session) -> dict[str, SocialProfile]:
    me_ = SocialProfile(platform=Platform.X, external_id=SELF_X, username="pricechat", is_self=True)
    fan = SocialProfile(platform=Platform.X, external_id="42", username="kiwifan")
    other = SocialProfile(platform=Platform.X, external_id="43", username="otherfan")
    creator = SocialProfile(platform=Platform.X, external_id="77", username="creator")
    db.add_all([me_, fan, other, creator])
    db.flush()
    now = utcnow()
    db.add_all([
        SocialPost(platform=Platform.X, external_id="post-1", text="Bitcoin isn't moving the way people think", is_own=True,
                   url="https://x.com/pricechat/status/post-1", posted_at=now - timedelta(hours=3), metrics={"likes": 14, "replies": 13}),
        SocialPost(platform=Platform.X, external_id="rt-1", text="RT @someone: not ours", is_own=True,
                   referenced=[{"type": "retweeted", "id": "theirs"}], posted_at=now - timedelta(hours=2)),
        SocialPost(platform=Platform.X, external_id="reply-1", text="@creator Influencers love to promote leverage", is_own=True,
                   url="https://x.com/pricechat/status/reply-1", in_reply_to_external_id="77",
                   referenced=[{"type": "replied_to", "id": "creator-post"}], posted_at=now - timedelta(hours=1)),
    ])
    db.commit()
    return {"fan": fan, "other": other, "creator": creator}


def reply(db: Session, author: SocialProfile, pid: str, to_post: str, text: str = "@pricechat good take, the funding rates agree") -> None:
    db.add(SocialPost(platform=Platform.X, external_id=pid, text=text, author_profile_id=author.id, in_reply_to_external_id=SELF_X,
                      referenced=[{"type": "replied_to", "id": to_post}], posted_at=utcnow() - timedelta(minutes=10), source_kinds=["mention"]))
    db.commit()


def mission_of(db: Session, kind: str) -> Mission:
    m = db.scalar(select(Mission).where(Mission.kind == kind, Mission.status == "active"))
    assert m is not None, kind
    return m


def view(db: Session, m: Member) -> dict[str, dict]:
    return {x["kind"]: x for x in missions.for_member(db, m)}


# --- generation -------------------------------------------------------------------------------------------------


def test_missions_are_generated_from_real_content_and_deduplicated(db: Session) -> None:
    ensure_catalog(db)
    out = missions.generate(db, "https://t.me/thepricechat")
    kinds = set(db.scalars(select(Mission.kind)))
    assert {"daily_visit", "telegram_participation", "weekly_active_days", "weekly_content", "link_x", "link_google", "link_telegram"} <= kinds
    assert not kinds & {"open_short", "x_reply", "x_conversation"}  # no content yet: no content missions
    short(db, "vid1")
    x_world(db)
    out = missions.generate(db)
    assert out["created"] == 5  # latest Short; reply / repost / like on the latest post; the conversation
    ms = mission_of(db, "open_short")
    assert ms.target["title"] == "He Put His Allowance Into Bitcoin" and ms.verification == "verified_click"
    xr = mission_of(db, "x_reply")
    assert xr.target["external_id"] == "post-1" and xr.requires_platform == "x" and xr.xp == 0
    assert mission_of(db, "x_repost").xp == 0 and mission_of(db, "x_repost").requires_platform == "x"
    like = mission_of(db, "x_like")
    assert like.xp == 0 and like.requires_platform is None and like.verification == "unverifiable"
    conv = mission_of(db, "x_conversation")
    assert conv.target["external_id"] == "reply-1"  # Price Chat's own reply, never the creator's post
    assert conv.target["replied_to"] == "creator" and "pile on" in conv.description
    assert missions.generate(db)["created"] == 0  # idempotent
    short(db, "vid2", hours_ago=1)
    missions.generate(db)
    active = list(db.scalars(select(Mission).where(Mission.kind == "open_short", Mission.status == "active")))
    assert [m.target["external_id"] for m in active] == ["vid2"]  # one active per kind: the newer replaces the older


def test_missions_expire_and_old_content_creates_none(db: Session) -> None:
    ensure_catalog(db)
    short(db, "old", hours_ago=24 * 20)
    missions.generate(db)
    assert db.scalar(select(Mission).where(Mission.kind == "open_short")) is None  # older than 14 days
    short(db, "vid1", hours_ago=24 * 6.9)
    missions.generate(db)
    m = mission_of(db, "open_short")
    m.expires_at = utcnow() - timedelta(minutes=1)
    db.commit()
    missions.generate(db)
    db.refresh(m)
    assert m.status == "expired"
    mem = member(db)
    assert "open_short" not in view(db, mem)


# --- completion & verification ----------------------------------------------------------------------------------


def test_open_latest_short_is_verified_as_an_open_and_pays_once(db: Session) -> None:
    mem = member(db)
    short(db, "vid1")
    missions.generate(db)
    m = mission_of(db, "open_short")
    v = view(db, mem)["open_short"]
    assert v["status"] == "available" and v["xp"] == 3 and v["verification_label"] == "Verified as an open only"
    assert "doesn't tell us who watched" in v["verification_method"] and v["cta"]["label"] == "Watch"
    assert missions.open_mission(db, mem, m)["xp"] == 3
    assert missions.open_mission(db, mem, m)["xp"] == 0  # opening again pays nothing
    r = service.content_view(db, mem, "vid1")  # opening the same video from the homepage
    assert not r.awarded
    assert view(db, mem)["open_short"]["status"] == "completed"
    paid = db.scalars(select(ReputationEvent).where(ReputationEvent.member_id == mem.id, ReputationEvent.event_type.in_(
        ["mission.complete", "website.content_view"]))).all()
    assert [(e.event_type, e.xp, e.source) for e in paid] == [("mission.complete", 3, "youtube")]


def test_x_reply_mission_needs_the_linked_account_and_never_pays(db: Session) -> None:
    mem, other_member = member(db, "fanmember"), member(db, "othermember")
    people = x_world(db)
    missions.generate(db)
    m = mission_of(db, "x_reply")
    assert view(db, mem)["x_reply"]["status"] == "unavailable"  # X not linked
    with pytest.raises(ValueError):
        missions.claim_mission(db, mem, m)
    link(db, mem, "x", "42")
    link(db, other_member, "x", "43")
    v = view(db, mem)["x_reply"]
    assert v["status"] == "available" and v["xp"] == 0 and v["earns_xp"] is False
    assert v["cta"] == {"label": "Reconnect X", "action": "link", "platform": "x"}  # linked, but no X token to send with
    v = {x["kind"]: x for x in missions.for_member(db, mem, x_can_act=True)}["x_reply"]
    assert v["cta"] == {"label": "Reply", "action": "x_reply", "post_id": "post-1"}  # one button, inside Price Chat
    assert missions.claim_mission(db, mem, m) == {"status": "pending"}
    assert view(db, mem)["x_reply"]["status"] == "pending"
    assert db.scalar(select(func.count()).select_from(ReputationEvent).where(ReputationEvent.event_type == "mission.complete")) == 0

    reply(db, people["other"], "r-other", "post-1")  # someone else's reply never completes OUR mission
    reply(db, people["fan"], "r-short", "post-1", text="@pricechat 🔥")  # too short to count
    verified.x_interactions(db)
    assert view(db, mem)["x_reply"]["status"] == "pending"
    assert view(db, other_member)["x_reply"]["status"] == "completed"
    reply(db, people["fan"], "r-real", "post-1")
    out = verified.x_interactions(db)
    assert out["missions"] == 1
    assert view(db, mem)["x_reply"]["status"] == "completed"
    paid = [(e.event_type, e.xp) for e in db.scalars(select(ReputationEvent).where(
        ReputationEvent.member_id == mem.id, ReputationEvent.source == "x", ReputationEvent.event_type != "identity.x_linked"))]
    assert paid == []  # done, never paid: X's developer policy forbids rewards for actions on X
    reply(db, people["fan"], "r-again", "post-1", text="@pricechat and another long reply to the same post")
    verified.x_interactions(db)
    assert db.scalar(select(func.count()).select_from(ReputationEvent).where(
        ReputationEvent.member_id == mem.id, ReputationEvent.event_type == "mission.complete")) == 0


def test_unverified_claims_are_rejected_after_a_day(db: Session) -> None:
    mem = member(db)
    x_world(db)
    missions.generate(db)
    link(db, mem, "x", "42")
    m = mission_of(db, "x_reply")
    missions.claim_mission(db, mem, m)
    mm = db.scalar(select(MemberMission).where(MemberMission.member_id == mem.id))
    mm.claimed_at = utcnow() - timedelta(hours=25)
    db.commit()
    missions.expire(db)
    db.commit()
    assert db.scalar(select(MemberMission.status).where(MemberMission.id == mm.id)) == "rejected"
    kinds = set(db.scalars(select(MemberNotification.kind).where(MemberNotification.member_id == mem.id)))
    assert "mission_rejected" in kinds
    assert db.scalar(select(func.count()).select_from(ReputationEvent).where(ReputationEvent.event_type == "mission.complete")) == 0


def test_unverifiable_actions_never_pay(db: Session) -> None:
    mem = member(db)
    x_world(db)
    short(db, "vid1")
    missions.generate(db)
    for v in missions.for_member(db, mem):
        if v["verification"] in ("self_reported", "unverifiable"):
            assert v["xp"] == 0
    feed = insights.feed(db, {"youtube": "https://www.youtube.com/@pricechat", "telegram": "https://t.me/thepricechat", "x": None})
    post = next(p for p in feed["x_posts"] if p["id"] == "post-1")
    assert next(p for p in feed["x_posts"] if p["id"] == "rt-1")["type"] == "repost"
    assert post["actions"]["like"] == "https://x.com/intent/like?tweet_id=post-1"  # opens X; Price Chat never likes for anyone
    missions.record_action(db, mem, "like_intent", "x", "post-1")
    db.commit()
    assert db.scalar(select(func.count()).select_from(ReputationEvent).where(ReputationEvent.member_id == mem.id,
                                                                              ReputationEvent.source == "x")) == 0
    assert db.scalar(select(ReputationEvent.id).where(ReputationEvent.event_type.in_(["x.like_pricechat", "x.repost_pricechat"]))) is None


def test_daily_mission_cap(db: Session) -> None:
    mem = member(db)
    ensure_catalog(db)
    for i in range(12):
        db.add(Mission(key=f"manual:{i}", kind="open_link", cadence="once", title=f"Open {i}", platform="website", xp=5,
                       verification="verified_click", target={"url": "/website/preview"}, status="active"))
    db.commit()
    total = sum(missions.open_mission(db, mem, m)["xp"] for m in db.scalars(select(Mission).where(Mission.kind == "open_link")))
    assert total == 30  # mission.complete daily cap


def test_telegram_and_weekly_missions_follow_verified_activity(db: Session) -> None:
    mem = member(db)
    missions.generate(db, "https://t.me/thepricechat")
    assert view(db, mem)["telegram_participation"]["status"] == "unavailable"
    link(db, mem, "telegram", "555")
    v = view(db, mem)
    tg = v["telegram_participation"]
    assert tg["status"] == "available" and tg["xp"] == 5 and tg["goal"] == 3 and tg["progress"] == 0  # +3 first, +2 active day
    assert tg["title"] == "Be active in Price Chat today" and tg["cta"] == {"label": "Open chat", "action": "open",
                                                                            "url": "https://t.me/thepricechat"}
    src = TelegramSource(chat_id=-100111, display_name="Price Chat", enabled=True)
    db.add(src)
    db.flush()
    texts = ["what do you all think about gold this week", "silver looks like it is lagging again", "and the dollar index is flat"]
    db.add(TelegramMessage(source_id=src.id, chat_id=src.chat_id, message_id=1, author_id=555, author_name="U",
                           sent_at=utcnow() - timedelta(minutes=5), text=texts[0]))
    db.commit()
    verified.telegram_participation(db)
    tg = view(db, mem)["telegram_participation"]
    assert tg["status"] == "in_progress" and tg["progress"] == 1  # counted automatically, nothing to claim
    for i, t in enumerate(texts[1:]):
        db.add(TelegramMessage(source_id=src.id, chat_id=src.chat_id, message_id=2 + i, author_id=555, author_name="U",
                               sent_at=utcnow() - timedelta(minutes=4 - i), text=t))
    db.commit()
    verified.telegram_participation(db)
    tg = view(db, mem)["telegram_participation"]
    assert tg["status"] == "completed" and tg["progress"] == 3
    # weekly: 3 different active days this week (only days already in this week count)
    start = utcnow() - timedelta(days=utcnow().weekday())
    days_in_week = [d for d in range(3) if (utcnow() - timedelta(days=d)) >= start.replace(hour=0, minute=0, second=0, microsecond=0)]
    for d in days_in_week[1:]:
        ledger.award(db, mem, "telegram.first_participation", f"tg-first:test-{d}", occurred_at=utcnow() - timedelta(days=d))
    w = view(db, mem)["weekly_active_days"]
    assert w["progress"] == len(days_in_week) and w["goal"] == 3
    if len(days_in_week) == 3:
        assert w["status"] == "completed"


def test_link_missions_disappear_once_linked_and_relinking_pays_nothing(db: Session) -> None:
    mem = member(db)
    missions.generate(db)
    assert view(db, mem)["link_x"]["xp"] == 20
    link(db, mem, "x", "42")
    assert "link_x" not in view(db, mem)
    other = member(db, "second")
    link(db, mem, "telegram", "1")
    service.disconnect(db, mem, "x")  # unlink...
    link(db, other, "x", "42")  # ...and link the same X account to another member
    claimed = db.scalars(select(ReputationEvent).where(ReputationEvent.event_type == "identity.x_linked")).all()
    assert len(claimed) == 1  # first-time XP once per external account, ever


# --- notifications, rank-up, public profile ----------------------------------------------------------------------


def test_rank_up_badge_and_disconnect_notifications(db: Session) -> None:
    mem = member(db)
    link(db, mem, "telegram", "1")
    link(db, mem, "x", "2")
    ledger.adjust(db, mem, 40, actor="dashboard", reason="AMA host", ref="a")
    kinds = [n.kind for n in db.scalars(select(MemberNotification).where(MemberNotification.member_id == mem.id))]
    assert "rank_up" in kinds and "badge" in kinds and "xp" in kinds
    up = db.scalar(select(MemberNotification).where(MemberNotification.kind == "rank_up"))
    assert up.title == "Kiwi Chick unlocked"
    service.disconnect(db, mem, "x")
    assert db.scalar(select(MemberNotification.kind).where(MemberNotification.kind == "disconnected")) == "disconnected"
    ledger.award(db, mem, "website.login", "day:x")  # a daily visit is not worth a notification
    assert db.scalar(select(func.count()).select_from(MemberNotification).where(MemberNotification.body == "Daily visit")) == 0


def test_public_profile_is_off_by_default_and_shows_only_safe_fields(mc: TestClient, db: Session) -> None:  # noqa: F811
    google_sign_in(mc)
    mc.patch("/api/members/me", json={"username": "KiwiKing"})
    assert mc.get("/api/members/public/KiwiKing").status_code == 404
    assert mc.get("/api/members/public/nobody").status_code == 404  # unknown and private look the same
    assert mc.patch("/api/members/me", json={"public_profile": True}).status_code == 200
    pub = mc.get("/api/members/public/kiwiking").json()
    assert set(pub) == {"username", "joined_at", "rank", "badges", "roles"}  # roles: the public WORKER / MANAGER badge
    blob = str(pub)
    assert "g-1" not in blob and "google" not in blob.lower() and "xp" not in {k.lower() for k in pub}


def test_profile_payload_has_missions_activity_breakdown_and_rank_up(mc: TestClient, db: Session) -> None:  # noqa: F811
    google_sign_in(mc)
    ensure_catalog(db)
    missions.generate(db)
    p = me(mc)
    assert {"missions", "next_steps", "activity", "breakdown", "telegram", "notifications", "rank_up", "public_profile"} <= set(p)
    assert p["telegram"] is None  # Telegram not linked: no Telegram metrics shown
    assert p["breakdown"]["total"] == p["member"]["xp"]
    assert {s["source"] for s in p["breakdown"]["sources"]} == {"Website", "YouTube"}  # joined + connected Google
    assert p["activity"]["today"]["xp"] == p["member"]["xp"]
    assert all(s["xp"] > 0 for s in p["next_steps"])
    pid = p["member"]["id"]
    mc.post(f"/api/members-admin/members/{pid}/xp", json={"amount": 20, "reason": "Rank-up for the test"})
    p = me(mc)
    assert p["rank_up"] is not None and p["rank_up"]["title"] == "Kiwi Chick unlocked"
    mc.post("/api/members/me/notifications/read", json={"ids": [p["rank_up"]["id"]]})
    assert me(mc)["rank_up"] is None


def test_telegram_activity_view_counts_messages_honestly(db: Session) -> None:
    mem = member(db)
    link(db, mem, "telegram", "555")
    src = TelegramSource(chat_id=-100111, display_name="Price Chat", enabled=True)
    db.add(src)
    db.flush()
    texts = ["gm", "lol", "what is everyone watching on the dollar index", "what is everyone watching on the dollar index",
             "and silver is lagging gold again this month", "🚀🚀", "/help"]
    for i, t in enumerate(texts):
        db.add(TelegramMessage(source_id=src.id, chat_id=src.chat_id, message_id=i + 1, author_id=555, author_name="U",
                               sent_at=utcnow() - timedelta(minutes=30 - i), text=t))
    db.commit()
    verified.telegram_participation(db)
    t = insights.telegram_activity(db, mem)
    assert t["messages_today"] == 7 and t["eligible_today"] == 2
    assert t["xp_today"] == 4 and t["daily_cap"] == 10  # +3 first, +1 more; caps 3 + 5 + 2
    assert "further real message" in t["next"]


# --- admin ------------------------------------------------------------------------------------------------------


def test_admin_missions_create_pause_and_guardrails(mc: TestClient, db: Session) -> None:  # noqa: F811
    short(db, "vid1")
    bad = mc.post("/api/members-admin/missions", json={"title": "Reply to this critic", "url": "https://x.com/somecritic/status/1",
                                                      "platform": "x", "xp": 10})
    assert bad.status_code == 400 and "Price Chat's own content" in bad.json()["detail"]
    ok = mc.post("/api/members-admin/missions", json={"title": "Watch our Short", "url": "https://www.youtube.com/shorts/vid1",
                                                     "platform": "youtube", "xp": 4})
    assert ok.status_code == 200 and ok.json()["verification"] == "verified_click"
    mid = ok.json()["id"]
    google_sign_in(mc)
    assert any(m["id"] == mid for m in me(mc)["missions"])
    mc.patch(f"/api/members-admin/missions/{mid}", json={"status": "paused"})
    assert not any(m["id"] == mid for m in me(mc)["missions"])
    mc.patch(f"/api/members-admin/missions/{mid}", json={"status": "active", "xp": 6})
    assert next(m for m in me(mc)["missions"] if m["id"] == mid)["xp"] == 6
    mc.post("/api/members-admin/missions/generate")
    visit = next(m for m in mc.get("/api/members-admin/missions").json()["missions"] if m["kind"] == "daily_visit")
    assert mc.patch(f"/api/members-admin/missions/{visit['id']}", json={"xp": 50}).status_code == 400  # rule-paid missions
    actions = [e["action"] for e in mc.get("/api/members-admin/audit").json()["entries"]]
    assert actions.count("mission_changed") == 2 and "mission_created" in actions
    listing = mc.get("/api/members-admin/missions").json()
    assert listing["mission_daily_cap"] == 30 and {"completed", "xp_issued", "pending", "verification_label"} <= set(listing["missions"][0])


def test_a_parallel_mission_row_keeps_the_callers_work(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """Two requests creating the same member-mission row: the loser reuses the winner's row, and nothing else the caller
    did in the transaction (here an XP award) is rolled back with it."""
    m = member(db)
    missions.generate(db)
    mission = db.scalar(select(Mission).where(Mission.key == "daily:visit"))
    period = missions.period_for(mission.cadence)
    first = missions._row(db, m, mission, period)
    db.commit()
    m.display_name = "kept"
    db.flush()
    assert ledger.award(db, m, "website.login", "day:race", commit=False).awarded
    real_scalar = db.scalar

    def scalar(stmt, *a, **k):  # type: ignore[no-untyped-def]
        if "member_missions" in str(stmt) and not getattr(scalar, "missed", False):
            scalar.missed = True  # type: ignore[attr-defined]
            return None  # the lookup misses: a parallel request created the row after it
        return real_scalar(stmt, *a, **k)

    monkeypatch.setattr(db, "scalar", scalar)
    again = missions._row(db, m, mission, period)
    monkeypatch.undo()
    assert getattr(scalar, "missed", False) and again.id == first.id
    db.commit()
    db.expire_all()
    assert db.scalar(select(ReputationEvent.id).where(ReputationEvent.member_id == m.id, ReputationEvent.source_ref == "day:race"))
    assert db.get(Member, m.id).display_name == "kept"


def test_repost_card_is_hidden_while_x_write_is_off(db: Session) -> None:
    """Audit PP-14: a repost only completes when it is sent from Price Chat (X write on). With write off the card opened X and
    stayed open forever, so it isn't shown; a reply card stays, because a verified reply on X still completes it."""
    mem = member(db, "repostfan")
    x_world(db)
    missions.generate(db)
    mission_of(db, "x_repost")
    link(db, mem, "x", "42")
    off = {x["kind"]: x for x in missions.for_member(db, mem, x_can_act=True, x_write=False)}
    assert "x_repost" not in off and "x_reply" in off
    on = {x["kind"]: x for x in missions.for_member(db, mem, x_can_act=True, x_write=True)}
    assert on["x_repost"]["cta"]["action"] == "x_repost"
