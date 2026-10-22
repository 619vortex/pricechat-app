"""Worker System Phase 1 (Telegram invite workers): roles, attribution, verification, engagement, daily reports, reviews.
A fake Telegram bot; nothing real is contacted."""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.members import LinkedIdentity
from app.models.telegram import TelegramMessage, TelegramSource
from app.models.workers import WkCredit, WkJoin, WkReview, WkSubmission
from app.security.admin_auth import is_public
from app.workers import service as wk
from tests.test_trading_os import kiwi

CHAT = -1001234


class Bot:
    def __init__(self, inside: set[int]) -> None:
        self.inside = inside

    async def get_chat_member(self, chat: Any, uid: int) -> dict[str, Any]:
        return {"status": "member" if uid in self.inside else "left"}


def setup(db: Session) -> tuple[Any, Any, Any]:
    s = wk.settings(db)
    s.community_chat_id, s.default_target = CHAT, 2
    db.commit()
    w, w2, mgr = kiwi(db, 1, "7001"), kiwi(db, 1, "7002"), kiwi(db, 1, "7003")
    for m in (w, w2):
        wk.grant(db, m.id, "WORKER", "owner")
    wk.grant(db, mgr.id, "MANAGER", "owner")
    wk.assign(db, w.id, "TELEGRAM_INVITES", None, mgr.id, "owner")
    wk.assign(db, w2.id, "TELEGRAM_INVITES", None, None, "owner")
    return w, w2, mgr


def join(db: Session, uid: int, username: str | None, at: datetime | None = None, link: str | None = None, key: str | None = None) -> None:
    upd = {"update_id": uid, "chat_member": {"chat": {"id": CHAT}, "date": int((at or utcnow()).timestamp()),
                                             "old_chat_member": {"status": "left"},
                                             "new_chat_member": {"status": "member", "user": {"id": uid, "username": username, "first_name": "F"}},
                                             **({"invite_link": {"name": link}} if link else {})}}
    if key:
        upd["update_id"] = key
    wk.joins_from_update(db, upd)
    db.commit()


def run(db: Session, inside: set[int]) -> dict[str, int]:
    return asyncio.run(wk.verify(db, Bot(inside)))


def test_roles_are_owner_only_independent_of_rank_and_keep_history(client: Any, db: Session) -> None:
    for path in ("/api/workers/admin/overview", "/api/workers/admin/roles", "/api/workers/admin/assignments", "/api/workers/admin/settings"):
        assert not is_public("GET", path) and not is_public("POST", path)  # behind the owner login
    from app.members import service as members

    m = kiwi(db, 3, "7010")
    rank_before = m.rank_level
    client.cookies.set("pc_session", members.new_session(db, m, 30))
    assert client.get("/api/members/workers/me").json() == {"roles": []}  # no tab for non-workers
    assert client.post("/api/members/workers/submissions", json={"username": "someone_new"}).status_code == 403
    assert client.post("/api/workers/admin/roles", json={"member_id": m.id, "role": "WORKER", "grant": True}).status_code == 200
    assert client.get("/api/members/me").json()["roles"] == ["WORKER"]
    db.refresh(m)
    assert m.rank_level == rank_before  # the rank is untouched
    m.public_profile = True
    db.commit()
    assert client.get(f"/api/members/public/{m.username}").json()["roles"] == ["WORKER"]
    client.post("/api/workers/admin/roles", json={"member_id": m.id, "role": "WORKER", "grant": False})
    assert client.get("/api/members/workers/me").json() == {"roles": []}
    hist = client.get("/api/workers/admin/overview").json()["roles"]
    assert any(r["member_id"] == m.id and r["revoked_at"] for r in hist)  # revoked, not deleted


def test_usernames_are_normalised_and_never_counted_twice(db: Session) -> None:
    w, w2, _ = setup(db)
    assert wk.normalise(" @Some_User ") == "some_user" and wk.normalise("https://t.me/Some_User") == "some_user"
    assert wk.normalise("ab") is None and wk.normalise("1abcde") is None
    s = wk.submit(db, w, "@Friend_One")
    assert s.username == "friend_one" and s.status == "SUBMITTED" and s.worker_id == w.id and s.created_at
    with pytest.raises(wk.WorkerError, match="already submitted"):
        wk.submit(db, w, "friend_one")
    with pytest.raises(wk.WorkerError, match="another worker"):
        wk.submit(db, w2, "t.me/FRIEND_ONE")
    li = db.scalar(select(LinkedIdentity).where(LinkedIdentity.member_id == w.id, LinkedIdentity.platform == "telegram"))
    li.username = "MyOwnName"
    db.commit()
    with pytest.raises(wk.WorkerError, match="your own"):
        wk.submit(db, w, "myownname")


def test_a_typed_username_is_only_verified_by_a_real_join(db: Session) -> None:
    w, _, _ = setup(db)
    sub = wk.submit(db, w, "new_friend")
    out = run(db, {101})
    db.refresh(sub)
    assert sub.status == "SUBMITTED" and "not seen joining" in sub.evidence["note"]  # typed, nothing seen: not an invite
    join(db, 101, "new_friend")
    run(db, {101})
    db.refresh(sub)
    assert sub.status == "VERIFIED" and sub.tg_user_id == 101 and sub.evidence["resolved_by"] == "join_seen"
    assert out["waiting"] == 1


def test_existing_members_bots_and_double_credit_are_refused(db: Session) -> None:
    w, w2, _ = setup(db)
    db.add(TelegramSource(chat_id=CHAT, display_name="PC", chat_type="supergroup", username="thepricechat"))
    db.flush()
    src = db.scalar(select(TelegramSource.id))
    db.add(TelegramMessage(source_id=src, chat_id=CHAT, message_id=1, author_id=202, sent_at=utcnow() - timedelta(days=30), text="old timer"))
    db.commit()
    a = wk.submit(db, w, "old_timer")
    join(db, 202, "old_timer")  # left and rejoined: posted before this join
    run(db, {202})
    db.refresh(a)
    assert a.status == "REJECTED" and "before this invite" in a.reject_reason
    link = wk.link_for(db, w2)
    link.chat_link_name = "wk-x"
    db.commit()
    join(db, 303, "dup_person", link="wk-x")  # joined through w2's link: attributed to w2
    run(db, {303})
    x = db.scalar(select(WkSubmission).where(WkSubmission.tg_user_id == 303))
    assert x.worker_id == w2.id and x.source == "chat_link" and x.status == "VERIFIED"
    other = WkSubmission(worker_id=w.id, job_type="TELEGRAM_INVITES", username="dup_alias", tg_user_id=303, source="form", evidence={})
    db.add(other)
    db.commit()
    run(db, {303})
    db.refresh(other)
    assert other.status == "REJECTED" and "already credited" in other.reject_reason
    assert db.scalar(select(WkCredit).where(WkCredit.tg_user_id == 303)).worker_id == w2.id  # one worker, ever


def test_in_the_group_but_join_unseen_waits_for_a_manager_and_joins_are_idempotent(db: Session) -> None:
    w, _, _ = setup(db)
    q = kiwi(db, 1, "404")  # a Passport member whose linked Telegram is id 404, @quiet_one
    li = db.scalar(select(LinkedIdentity).where(LinkedIdentity.member_id == q.id, LinkedIdentity.platform == "telegram"))
    li.username = "quiet_one"
    db.commit()
    s = wk.submit(db, w, "quiet_one")
    assert s.tg_user_id == 404  # resolved through a Passport-linked Telegram account
    run(db, {404})
    db.refresh(s)
    assert s.status == "SUBMITTED" and "manager can review" in s.evidence["note"]
    join(db, 505, "twice", key="k1")
    join(db, 505, "twice", key="k1")
    assert len(db.scalars(select(WkJoin).where(WkJoin.tg_user_id == 505)).all()) == 1


def test_bot_start_link_attributes_the_numeric_id(db: Session) -> None:
    w, _, _ = setup(db)
    code = wk.link_for(db, w).code
    db.commit()
    text = wk.on_start(db, code, 606, "Linked_Guy", False)
    assert "t.me/thepricechat" in text
    s = db.scalar(select(WkSubmission).where(WkSubmission.tg_user_id == 606))
    assert s.worker_id == w.id and s.source == "bot_start" and s.username == "linked_guy"
    wk.on_start(db, code, 606, "Linked_Guy", False)
    assert len(db.scalars(select(WkSubmission).where(WkSubmission.tg_user_id == 606)).all()) == 1  # first attribution stands
    join(db, 606, "linked_guy")
    run(db, {606})
    db.refresh(s)
    assert s.status == "VERIFIED"


def test_engagement_counts_real_messages_only() -> None:
    t = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)
    msgs = [(t, "hello all", None), (t + timedelta(seconds=5), "hello all", None), (t + timedelta(seconds=6), "/start", None),
            (t + timedelta(seconds=7), "k", None), (t + timedelta(seconds=8), "", "photo"), (t + timedelta(seconds=9), "nice", None),
            (t + timedelta(seconds=10), "more", None), (t + timedelta(seconds=11), "flood", None), (t + timedelta(minutes=2), "later", None)]
    assert wk.qualifying(msgs) == 4  # hello, photo, nice (then the minute cap), later


def test_daily_report_target_review_and_trail(db: Session) -> None:
    w, w2, mgr = setup(db)
    other_mgr = kiwi(db, 1, "7030")
    wk.grant(db, other_mgr.id, "MANAGER", "owner")
    for uid, name in ((701, "invitee_a"), (702, "invitee_b")):
        wk.submit(db, w, name)
        join(db, uid, name)
    assert wk.day_status(db, w.id, wk.today(db))["status"] == "IN_PROGRESS"
    with pytest.raises(wk.WorkerError, match="not ready"):
        wk.submit_report(db, w)
    run(db, {701, 702})
    assert wk.day_status(db, w.id, wk.today(db))["status"] == "READY_TO_SUBMIT"
    rep = wk.submit_report(db, w)
    assert rep.status == "PENDING" and rep.verified_count == 2
    with pytest.raises(wk.WorkerError, match="already submitted"):
        wk.submit_report(db, w)
    with pytest.raises(wk.WorkerError, match="assigned to you"):
        wk.decide(db, rep.id, True, None, actor_kind="manager", actor_member=other_mgr, actor_name=other_mgr.username)
    with pytest.raises(wk.WorkerError, match="reason"):
        wk.decide(db, rep.id, False, "", actor_kind="manager", actor_member=mgr, actor_name=mgr.username)
    wk.decide(db, rep.id, True, None, actor_kind="manager", actor_member=mgr, actor_name=mgr.username)
    assert wk.day_status(db, w.id, wk.today(db))["status"] == "APPROVED"
    assert {s.status for s in db.scalars(select(WkSubmission).where(WkSubmission.report_id == rep.id))} == {"APPROVED"}
    with pytest.raises(wk.WorkerError, match="already approved"):
        wk.decide(db, rep.id, False, "changed my mind", actor_kind="admin", actor_member=None, actor_name="owner")  # final
    trail = [(r.actor_kind, r.action) for r in db.scalars(select(WkReview).where(WkReview.report_id == rep.id).order_by(WkReview.id))]
    assert trail == [("worker", "SUBMITTED"), ("manager", "APPROVED")]
    view = wk.worker_view(db, w)
    assert view["totals"]["approved"] == 2 and view["today"]["status"] == "APPROVED"


def test_member_routes_for_worker_and_manager(client: Any, db: Session) -> None:
    from app.members import service as members

    w, _, mgr = setup(db)
    client.cookies.set("pc_session", members.new_session(db, w, 30))
    me = client.get("/api/members/workers/me").json()
    assert me["roles"] == ["WORKER"] and me["assignment"]["daily_target"] == 2 and me["today"]["status"] == "IN_PROGRESS"
    assert me["links"]["bot"].endswith(wk.link_for(db, w).code)
    r = client.post("/api/members/workers/submissions", json={"username": "@Route_Friend"})
    assert r.status_code == 200 and r.json()["username"] == "route_friend"
    assert client.post("/api/members/workers/submissions", json={"username": "route_friend"}).status_code == 409
    assert client.get("/api/members/workers/manager").status_code == 403  # a worker isn't a manager
    client.cookies.set("pc_session", members.new_session(db, mgr, 30))
    mv = client.get("/api/members/workers/manager").json()
    assert [x["id"] for x in mv["workers"]] == [w.id]  # only their own workers


def test_only_the_community_group_counts_and_its_id_is_learned(db: Session) -> None:
    s = wk.settings(db)
    s.community_chat_id = None
    db.commit()
    other = {"update_id": 1, "message": {"chat": {"id": -555, "username": "some_other_group"}, "date": 1_790_000_000, "message_id": 1,
                                         "new_chat_members": [{"id": 9001, "username": "x_user"}]}}
    assert wk.joins_from_update(db, other) == 0  # another group: ignored
    ours = {"update_id": 2, "message": {"chat": {"id": -777, "username": "thepricechat"}, "date": 1_790_000_000, "message_id": 2,
                                        "new_chat_members": [{"id": 9002, "username": "y_user"}]}}
    assert wk.joins_from_update(db, ours) == 1 and wk.settings(db).community_chat_id == -777
    assert wk.joins_from_update(db, other) == 0


def test_rows_stuck_in_submitted_do_not_starve_newer_submissions(db: Session) -> None:
    w, _, _ = setup(db)
    stuck = [wk.submit(db, w, f"stuck_user_{i}") for i in range(3)]  # never seen joining: stay SUBMITTED for days
    fresh = wk.submit(db, w, "fresh_friend")
    join(db, 301, "fresh_friend")
    for _ in range(2):  # a batch of 2 per run: in id order the stuck rows filled every batch
        asyncio.run(wk.verify(db, Bot({301}), limit=2))
    db.refresh(fresh)
    assert fresh.status == "VERIFIED"
    assert all(s.status == "SUBMITTED" for s in stuck)
