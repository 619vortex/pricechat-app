"""Invite Bot. TEST FIXTURES only: a fake Telegram gateway (nothing connects to Telegram)."""

import asyncio
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.invites import gateway as gw
from app.invites import service as svc
from app.models.invites import InviteAccount, InviteGroup


class FakeTG:
    """One fake Telegram world shared by every 'client'."""

    def __init__(self) -> None:
        self.group_msgs: dict[int, list[gw.Msg]] = {}
        self.private: list[gw.Msg] = []
        self.sent: list[tuple[str, object, str]] = []
        self.fail: dict[object, Exception] = {}
        self.needs_password = False


def factory(world: FakeTG):  # type: ignore[no-untyped-def]
    class Client:
        def __init__(self, api_id: int, api_hash: str, session: str = "") -> None:
            self.session_str = session or ""

        async def start(self) -> bool:
            return True

        async def send_code(self, phone: str) -> str:
            return "hash"

        async def sign_in(self, phone: str, code: str, code_hash: str) -> None:
            if world.needs_password:
                raise gw.NeedPassword()
            self.session_str = f"SESSION-{phone}"

        async def sign_in_password(self, password: str) -> None:
            self.session_str = "SESSION-2fa"

        async def me(self) -> tuple[int, str | None]:
            return 999, "inviter"

        def session(self) -> str:
            return self.session_str

        async def groups(self) -> list[gw.GroupInfo]:
            return [gw.GroupInfo(-100, "Crypto Talk", "cryptotalk"), gw.GroupInfo(-200, "Friendly Group", None)]

        async def group_messages(self, chat_id: int, after_id: int, limit: int) -> list[gw.Msg]:
            return [m for m in world.group_msgs.get(chat_id, []) if m.id > after_id][-limit:]

        async def private_messages(self, since, limit):  # type: ignore[no-untyped-def]
            return [m for m in world.private if m.at > since]

        async def send(self, peer, text: str) -> None:  # type: ignore[no-untyped-def]
            if peer in world.fail:
                raise world.fail[peer]
            world.sent.append((self.session_str, peer, text))

        async def stop(self) -> None:
            pass

    return Client


@pytest.fixture
def tg(monkeypatch):  # type: ignore[no-untyped-def]
    world = FakeTG()
    monkeypatch.setattr(svc, "RT", svc.Runtime(factory=factory(world), pace=(0, 0)))
    return world


def _msg(i: int, uid: int, text: str, username: str | None = None, minutes_ago: int = 0) -> gw.Msg:
    return gw.Msg(i, uid, username or f"user{uid}", text, utcnow() - timedelta(minutes=minutes_ago))


def _active(db: Session, account_id: int, *user_ids: int) -> None:
    """TEST FIXTURE: these people chatted recently in a watched group of this account."""
    g = db.scalar(select(InviteGroup).where(InviteGroup.account_id == account_id, InviteGroup.chat_id == -999))
    if g is None:
        g = InviteGroup(account_id=account_id, chat_id=-999, title="Watched", allow_posts=False)
        db.add(g)
        db.flush()
    for u in user_ids:
        svc.seen(db, u, None, g.id)
    db.commit()


def _setup(client: TestClient, db: Session, tg: FakeTG, name: str = "main", phone: str = "+971500000001") -> int:
    assert client.put("/api/invites/settings", json={"api_id": 12345, "api_hash": "a" * 32}).status_code == 200
    assert client.post("/api/invites/accounts/login", json={"name": name, "phone": phone}).json()["status"] == "code_sent"
    r = client.post("/api/invites/accounts/code", json={"name": name, "code": "12345"}).json()
    if r["status"] == "password_needed":
        r = client.post("/api/invites/accounts/password", json={"name": name, "password": "pw"}).json()
    assert r["status"] == "connected"
    return db.scalar(select(InviteAccount.id).where(InviteAccount.name == name))


def test_login_stores_only_an_encrypted_session_and_groups_are_picked_from_the_accounts_own(client: TestClient, db: Session, tg: FakeTG) -> None:
    tg.needs_password = True
    aid = _setup(client, db, tg)
    raw = db.scalar(text("select session_enc from invite_accounts where id = :i"), {"i": aid})
    assert raw and "SESSION" not in raw  # encrypted at rest
    o = client.get("/api/invites").json()
    assert o["accounts"][0]["telegram"] == "@inviter" and o["accounts"][0]["daily_limit"] == 20 and "session" not in str(o["accounts"][0]).lower()
    assert o["settings"]["api_hash_saved"] and "a" * 32 not in str(o)
    groups = client.get(f"/api/invites/accounts/{aid}/groups").json()["groups"]
    assert [g["title"] for g in groups] == ["Crypto Talk", "Friendly Group"]
    client.post("/api/invites/groups", json={"account_id": aid, "chat_id": -100, "title": "Crypto Talk"})
    assert client.get(f"/api/invites/accounts/{aid}/groups").json()["groups"][0]["approved"]
    client.post(f"/api/invites/accounts/{aid}/disconnect")
    assert db.get(InviteAccount, aid).session_enc is None


def test_only_people_who_asked_get_one_private_invite_and_no_means_no(client: TestClient, db: Session, tg: FakeTG) -> None:
    aid = _setup(client, db, tg)
    client.post("/api/invites/groups", json={"account_id": aid, "chat_id": -100, "title": "Crypto Talk"})
    tg.group_msgs[-100] = [_msg(1, 10, "gm everyone"), _msg(2, 11, "!pricechat sounds fun")]
    first = asyncio.run(svc.poll(db, client.app.state.runtime.settings))  # type: ignore[attr-defined]
    assert first["messages"] == 2 and first["opt_ins"] == 0  # keywords from before the group was approved don't count
    tg.group_msgs[-100] += [_msg(3, 12, "hey !PriceChat invite me pls"), _msg(4, 13, "just chatting")]
    tg.group_msgs[-100] += [_msg(5, 20, "nice chart", "dmuser")]
    tg.private = [_msg(1, 20, "hi, saw your post", "dmuser"), _msg(2, 21, "STOP", "nope")]
    second = asyncio.run(svc.poll(db, client.app.state.runtime.settings))  # type: ignore[attr-defined]
    assert second["opt_ins"] == 2 and second["opt_outs"] == 1
    members = {m["username"]: m["opted_in"] for m in client.get("/api/invites/members").json()["members"]}
    assert members["user10"] == "no" and members["user12"] == "yes"
    q = client.get("/api/invites/queue").json()["queue"]
    assert sorted(x["target"] for x in q) == ["@dmuser", "@user12"]

    totals = asyncio.run(svc.send_approved(db, client.app.state.runtime.settings))  # type: ignore[attr-defined]
    assert totals["dm_sent"] == 2 and sorted(p for _, p, _ in tg.sent) == ["dmuser", "user12"]
    assert all("https://t.me/PriceChat" in t and t.startswith("Hey bro!") for _, _, t in tg.sent)
    tg.sent.clear()
    asyncio.run(svc.send_approved(db, client.app.state.runtime.settings))  # type: ignore[attr-defined]
    assert tg.sent == []  # once ever; active-but-silent members (10, 11, 13) and the "STOP" user are never messaged
    tg.private.append(_msg(3, 21, "actually invite me", "nope", minutes_ago=-1))
    asyncio.run(svc.poll(db, client.app.state.runtime.settings))  # type: ignore[attr-defined]
    asyncio.run(svc.send_approved(db, client.app.state.runtime.settings))  # type: ignore[attr-defined]
    assert tg.sent == []  # a "no" is never re-opened automatically


def test_group_posts_daily_limit_and_rate_limit_stop_all_accounts(client: TestClient, db: Session, tg: FakeTG) -> None:
    a = _setup(client, db, tg, "main", "+971500000001")
    b = _setup(client, db, tg, "second", "+971500000002")
    client.put("/api/invites/settings", json={"daily_limit": 2, "group_posts_enabled": True})
    client.post("/api/invites/groups", json={"account_id": a, "chat_id": -200, "title": "Friendly Group", "allow_posts": True})
    client.post("/api/invites/groups", json={"account_id": a, "chat_id": -100, "title": "Crypto Talk", "allow_posts": False})
    for uid in (30, 31, 32):
        svc.consent(db, uid, a, f"u{uid}", "dm")
    svc.consent(db, 40, b, "u40", "dm")
    db.commit()
    _active(db, a, 30, 31, 32, 40, 41)
    s = client.app.state.runtime.settings  # type: ignore[attr-defined]
    t = asyncio.run(svc.send_approved(db, s))
    assert t["dm_sent"] + t["group_sent"] == 3 and t["skipped_limit"] == 2  # 2 for main (limit), 1 for second
    assert -100 not in [p for _, p, _ in tg.sent]  # never posts where admins didn't allow it

    db.execute(text("delete from invite_sends"))
    db.commit()
    svc.consent(db, 41, b, "u41", "dm")
    db.commit()
    tg.sent.clear()
    tg.fail["u30"] = gw.RateLimited(600, "Telegram rate limit: wait 600s")
    asyncio.run(svc.send_approved(db, s))
    assert tg.sent == []  # stopped at the first rate limit: nothing from the other account either
    acc = db.get(InviteAccount, a)
    assert acc.halted_until and acc.halted_until > utcnow() + timedelta(minutes=50)  # paused at least an hour
    asyncio.run(svc.send_approved(db, s))
    assert tg.sent == []  # still paused: no account takes over
    assert client.get("/api/invites").json()["accounts"][0]["status"] == "paused"


def test_blocked_users_and_lost_group_permission_are_never_retried(client: TestClient, db: Session, tg: FakeTG) -> None:
    a = _setup(client, db, tg)
    client.put("/api/invites/settings", json={"group_posts_enabled": True})
    client.post("/api/invites/groups", json={"account_id": a, "chat_id": -200, "title": "Friendly Group", "allow_posts": True})
    svc.consent(db, 50, a, "blocker", "dm")
    db.commit()
    _active(db, a, 50)
    tg.fail["blocker"] = gw.Unreachable("UserIsBlockedError")
    tg.fail[-200] = gw.NotAllowed("ChatWriteForbiddenError")
    t = asyncio.run(svc.send_approved(db, client.app.state.runtime.settings))  # type: ignore[attr-defined]
    assert t["failed"] == 2
    assert db.scalar(select(InviteGroup.allow_posts).where(InviteGroup.chat_id == -200)) is False
    tg.fail.clear()
    asyncio.run(svc.send_approved(db, client.app.state.runtime.settings))  # type: ignore[attr-defined]
    assert tg.sent == []


def test_settings_validation(client: TestClient, db: Session, tg: FakeTG) -> None:
    assert client.put("/api/invites/settings", json={"daily_limit": 50}).status_code == 422
    assert client.put("/api/invites/settings", json={"invite_link": "https://evil.example"}).status_code == 422
    r = client.put("/api/invites/settings", json={"invite_link": "https://t.me/PriceChatVIP"}).json()
    assert r["settings"]["invite_link"] == "https://t.me/PriceChatVIP"


def test_poll_is_harmless_before_setup(client: TestClient, db: Session, tg: FakeTG) -> None:
    assert asyncio.run(svc.poll(db, client.app.state.runtime.settings))["accounts"] == 0  # type: ignore[attr-defined]
    assert client.post("/api/invites/accounts/login", json={"name": "x", "phone": "+971500000009"}).status_code == 400



def test_group_posts_are_off_by_default_and_the_interval_bar_is_bounded(client: TestClient, db: Session, tg: FakeTG) -> None:
    a = _setup(client, db, tg)
    client.post("/api/invites/groups", json={"account_id": a, "chat_id": -200, "title": "Friendly Group", "allow_posts": True})
    svc.consent(db, 60, a, "fan", "dm")
    db.commit()
    _active(db, a, 60)
    o = client.get("/api/invites").json()["settings"]
    assert o["group_posts_enabled"] is False and o["dm_interval_seconds"] == 300
    assert [x["target"] for x in client.get("/api/invites/queue").json()["queue"]] == ["@fan"]  # DM-only while posts are off
    asyncio.run(svc.send_approved(db, client.app.state.runtime.settings))  # type: ignore[attr-defined]
    assert [p for _, p, _ in tg.sent] == ["fan"]

    assert client.put("/api/invites/settings", json={"dm_interval_seconds": 5}).status_code == 422
    assert client.put("/api/invites/settings", json={"dm_interval_seconds": 7200}).status_code == 422
    assert client.put("/api/invites/settings", json={"dm_interval_seconds": 10}).json()["settings"]["dm_interval_seconds"] == 10
    svc.RT.pace = None
    s = svc.settings_row(db)
    for seconds in (10, 300, 3600):
        s.dm_interval_seconds = seconds
        assert all(10 <= svc.gap(s) <= 3600 and abs(svc.gap(s) - seconds) <= seconds * 0.15 + 0.001 for _ in range(50))


def test_stop_interrupts_a_long_wait(tg: FakeTG) -> None:
    async def go() -> bool:
        svc.RT.stop = False

        async def press_stop() -> None:
            await asyncio.sleep(0.2)
            svc.RT.stop = True

        t = asyncio.get_running_loop().create_task(press_stop())
        started = asyncio.get_running_loop().time()
        ok = await svc._wait(3600)
        await t
        return (not ok) and asyncio.get_running_loop().time() - started < 3

    assert asyncio.run(go())



def test_telegram_service_notifications_never_count_as_an_opt_in(client: TestClient, db: Session, tg: FakeTG) -> None:
    """Regression: the login-code message from "Telegram" (777000) was treated as someone asking for an invite."""
    _setup(client, db, tg)
    _active(db, db.scalar(select(InviteAccount.id)), 70)
    tg.private = [_msg(1, 777000, "Login code: 12345. Do not give this code to anyone", None), _msg(2, 70, "hi, invite me", "realperson")]
    asyncio.run(svc.poll(db, client.app.state.runtime.settings))  # type: ignore[attr-defined]
    assert [x["target"] for x in client.get("/api/invites/queue").json()["queue"]] == ["@realperson"]
    asyncio.run(svc.send_approved(db, client.app.state.runtime.settings))  # type: ignore[attr-defined]
    assert [p for _, p, _ in tg.sent] == ["realperson"]
    assert gw.is_real_person(type("U", (), {"id": 5, "bot": False})()) and not gw.is_real_person(type("U", (), {"id": 777000})())
    assert not gw.is_real_person(type("U", (), {"id": 9, "support": True})()) and not gw.is_real_person(type("U", (), {"id": 9, "deleted": True})())



def test_the_same_member_writing_several_times_in_one_poll(client: TestClient, db: Session, tg: FakeTG) -> None:
    aid = _setup(client, db, tg)
    client.post("/api/invites/groups", json={"account_id": aid, "chat_id": -100, "title": "Crypto Talk"})
    tg.group_msgs[-100] = [_msg(1, 10, "gm"), _msg(2, 11, "hi"), _msg(3, 10, "wagmi"), _msg(4, 10, "again")]
    out = asyncio.run(svc.poll(db, client.app.state.runtime.settings))  # type: ignore[attr-defined]
    assert out["messages"] == 4 and len(client.get("/api/invites/members").json()["members"]) == 2


def test_auto_mode_sends_by_itself_and_manual_mode_waits_for_approval(client: TestClient, db: Session, tg: FakeTG) -> None:
    a = _setup(client, db, tg)
    for uid, name in ((80, "asker1"), (81, "asker2"), (82, "asker3")):
        svc.consent(db, uid, a, name, "dm")
    db.commit()
    _active(db, a, 80, 81, 82)
    s = client.app.state.runtime.settings  # type: ignore[attr-defined]
    assert client.get("/api/invites").json()["settings"]["auto_send"] is True  # Auto is the default

    client.put("/api/invites/settings", json={"auto_send": False})
    assert svc.maybe_auto_send(db, s) is False and tg.sent == []  # Manual: nothing goes out by itself

    async def approve_one() -> None:
        svc.start_run(db.get_bind(), s, False, only_users={81})
        await svc.RT.run["task"]

    asyncio.run(approve_one())
    assert [p for _, p, _ in tg.sent] == ["asker2"]  # only the person approved by hand
    q = client.post("/api/invites/queue/82/skip").json()["queue"]
    assert [x["target"] for x in q] == ["@asker1"]  # skipped: never queued again

    client.put("/api/invites/settings", json={"auto_send": True})

    async def auto() -> bool:
        started = svc.maybe_auto_send(db, s)
        if started:
            await svc.RT.run["task"]
        return started

    assert asyncio.run(auto()) is True
    assert sorted(p for _, p, _ in tg.sent) == ["asker1", "asker2"] and svc.RT.run["trigger"] == "auto"
    assert asyncio.run(auto()) is False  # nothing left: no empty runs



def test_random_contacts_are_never_invited_only_people_active_in_watched_groups(client: TestClient, db: Session, tg: FakeTG) -> None:
    """A DM needs BOTH: the person asked, and they chatted recently in a watched group."""
    aid = _setup(client, db, tg)
    client.post("/api/invites/groups", json={"account_id": aid, "chat_id": -100, "title": "Watcher Guru Chat"})
    tg.group_msgs[-100] = [_msg(1, 90, "gm", "member")]
    tg.private = [_msg(1, 90, "hey, how do I join?", "member"), _msg(2, 91, "hi bro", "randomfriend")]
    asyncio.run(svc.poll(db, client.app.state.runtime.settings))  # type: ignore[attr-defined]
    assert [x["target"] for x in client.get("/api/invites/queue").json()["queue"]] == ["@member"]  # the random contact isn't queued
    tg.group_msgs[-100] = [_msg(1, 90, "gm", "member"), _msg(2, 92, "bullish", "silentmember")]
    asyncio.run(svc.poll(db, client.app.state.runtime.settings))  # type: ignore[attr-defined]
    asyncio.run(svc.send_approved(db, client.app.state.runtime.settings))  # type: ignore[attr-defined]
    assert [p for _, p, _ in tg.sent] == ["member"]  # active but didn't ask (92) and asked but not active (91): no DM
    db.execute(text("UPDATE invite_members SET last_active_at = :t"), {"t": utcnow() - timedelta(days=30)})
    db.commit()
    assert svc.queue(db, aid) == []  # activity older than "active days" no longer qualifies



def test_direct_contacts_switch(client: TestClient, db: Session, tg: FakeTG) -> None:
    aid = _setup(client, db, tg)
    tg.private = [_msg(1, 95, "hey, what's price chat?", "directfriend")]
    asyncio.run(svc.poll(db, client.app.state.runtime.settings))  # type: ignore[attr-defined]
    assert svc.queue(db, aid) == []  # off by default: a direct contact without group activity waits
    assert client.put("/api/invites/settings", json={"invite_direct_contacts": True}).json()["settings"]["invite_direct_contacts"] is True
    assert [c.user_id for c in svc.queue(db, aid)] == [95]
    asyncio.run(svc.send_approved(db, client.app.state.runtime.settings))  # type: ignore[attr-defined]
    assert [p for _, p, _ in tg.sent] == ["directfriend"]
    svc.consent(db, 96, aid, "keywordonly", "keyword")  # the switch never widens anything for people who didn't message
    db.commit()
    assert svc.queue(db, aid) == []


def test_one_click_tool_auto_for_askers_personal_for_others_never_twice(client: TestClient, db: Session, tg: FakeTG) -> None:
    a = _setup(client, db, tg, "DREAM", "+971500000001")
    b = _setup(client, db, tg, "LOTEX", "+971500000002")
    client.put("/api/invites/settings", json={"dm_message": "Hey bro! We just launched a new crypto community called Price Chat. You're welcome to join us!"})
    for acc in (a, b):
        client.post("/api/invites/groups", json={"account_id": acc, "chat_id": -100, "title": "Watcher Guru Chat"})
    tg.group_msgs[-100] = [_msg(1, 201, "gm", "older", minutes_ago=30), _msg(2, 202, "bullish", "newer", minutes_ago=5),
                           gw.Msg(3, 203, None, "hi", utcnow()), _msg(4, 204, "lfg", "asker", minutes_ago=10)]  # 203: no public username
    tg.private = [_msg(1, 204, "how do I join?", "asker")]
    asyncio.run(svc.poll(db, client.app.state.runtime.settings))  # type: ignore[attr-defined]

    v = client.get("/api/invites/one", params={"account_id": a}).json()
    assert v["message"].endswith("https://t.me/PriceChat") and v["message"].startswith("Hey bro! We just launched")  # link always included
    assert v["recipient"]["username"] == "asker" and v["recipient"]["mode"] == "auto"  # people who asked come first
    r = client.post("/api/invites/one/send", json={"account_id": a, "user_id": 204}).json()
    assert r == {"status": "sent", "mode": "auto"} and [p for _, p, _ in tg.sent] == ["asker"]

    v = client.get("/api/invites/one", params={"account_id": a}).json()
    rec = v["recipient"]
    assert rec["username"] == "newer" and rec["mode"] == "personal" and rec["group"] == "Watcher Guru Chat"  # most recent first; no-username skipped
    assert rec["open_url"].startswith("https://t.me/newer?text=Hey%20bro%21")
    r = client.post("/api/invites/one/send", json={"account_id": a, "user_id": 202}).json()
    assert r == {"status": "opened", "mode": "personal"} and len(tg.sent) == 1  # the bot NEVER sends to someone who didn't ask
    assert client.get("/api/invites/one", params={"account_id": a}).json()["sent_today"] == 2

    v = client.get("/api/invites/one", params={"account_id": b}).json()
    assert v["recipient"]["username"] == "older"  # LOTEX never gets someone DREAM already invited
    assert client.post("/api/invites/one/send", json={"account_id": b, "user_id": 202}).status_code == 400  # no duplicates across accounts
    client.post("/api/invites/one/skip", json={"account_id": b, "user_id": 201})
    assert client.get("/api/invites/one", params={"account_id": b}).json()["recipient"] is None  # skipped: not shown again
    assert client.get("/api/invites/one", params={"account_id": a, "exclude": "201"}).json()["recipient"] is None

    tg.private = [_msg(2, 202, "thanks, joined!", "newer", minutes_ago=-2)]
    asyncio.run(svc.poll(db, client.app.state.runtime.settings))  # type: ignore[attr-defined]
    h = client.get("/api/invites/history").json()["history"]
    assert {x["recipient"]: (x["account"], x["how"], bool(x["replied_at"])) for x in h} == {
        "@newer": ("DREAM", "you, in Telegram", True), "@asker": ("DREAM", "sent by the bot", False)}
    assert svc.queue(db, a) == [] and len(tg.sent) == 1  # a reply after a personal invite never triggers a second (bot) one


def test_poll_saves_each_group_before_reading_the_next(client: TestClient, db: Session, tg: FakeTG, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """No write is held open across a Telegram read (on SQLite that is the database-wide write lock every other job waits on)."""
    from sqlalchemy import event

    aid = _setup(client, db, tg)
    client.post("/api/invites/groups", json={"account_id": aid, "chat_id": -100, "title": "Crypto Talk"})
    client.post("/api/invites/groups", json={"account_id": aid, "chat_id": -200, "title": "Friendly Group"})
    tg.group_msgs[-100] = [_msg(1, 10, "gm everyone")]
    tg.group_msgs[-200] = [_msg(1, 11, "hello")]
    unsaved = {"writes": False}
    event.listen(db, "after_flush", lambda *_: unsaved.update(writes=True))
    event.listen(db, "after_commit", lambda *_: unsaved.update(writes=False))
    held: list[bool] = []
    real = svc.RT.factory

    def watching(*a, **kw):  # type: ignore[no-untyped-def]
        c = real(*a, **kw)
        inner = c.group_messages

        async def group_messages(chat_id: int, after_id: int, limit: int):  # type: ignore[no-untyped-def]
            held.append(unsaved["writes"])
            return await inner(chat_id, after_id, limit)

        c.group_messages = group_messages
        return c

    monkeypatch.setattr(svc.RT, "factory", watching)
    out = asyncio.run(svc.poll(db, client.app.state.runtime.settings))  # type: ignore[attr-defined]
    assert out["messages"] == 2 and held == [False, False]
