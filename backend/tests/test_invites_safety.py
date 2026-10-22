"""Invite Bot safety: Telegram restrictions, dead sessions, never twice, pacing/limits across paths, no hammering.
TEST FIXTURES only: the fake Telegram gateway from test_invites (nothing connects to Telegram)."""

import asyncio
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.invites import gateway as gw
from app.invites import service as svc
from app.models.invites import InviteAccount, InviteConsent, InviteGroup, InviteSend
from tests.test_invites import FakeTG, _active, _msg, _setup, tg  # noqa: F401 - `tg` is a fixture


@pytest.fixture
def alerts(monkeypatch):  # type: ignore[no-untyped-def]
    """TEST FIXTURE: records owner alerts instead of sending them."""
    from app import alerts as real

    sent: list[tuple[str, str]] = []

    async def notify(db, key, text, **kw):  # type: ignore[no-untyped-def]
        sent.append((key, text))
        return True

    monkeypatch.setattr(real, "notify_owner", notify)
    return sent


def _wrap(monkeypatch, **over):  # type: ignore[no-untyped-def]
    """TEST FIXTURE: wraps the fake client's methods; counts connections."""
    real = svc.RT.factory
    stats = {"opened": 0, "stopped": 0}

    def make(*a, **kw):  # type: ignore[no-untyped-def]
        c = real(*a, **kw)
        stats["opened"] += 1
        inner_stop = c.stop

        async def stop() -> None:
            stats["stopped"] += 1
            await inner_stop()

        c.stop = stop
        for name, fn in over.items():
            setattr(c, name, fn(c))
        return c

    monkeypatch.setattr(svc.RT, "factory", make)
    return stats


def _asker(db: Session, aid: int, uid: int) -> None:
    svc.consent(db, uid, aid, f"u{uid}", "dm")
    db.commit()
    _active(db, aid, uid)


def _settings(client: TestClient):  # type: ignore[no-untyped-def]
    return client.app.state.runtime.settings  # type: ignore[attr-defined]


def test_peer_flood_pauses_every_account_alerts_once_and_keeps_the_person_queued(client: TestClient, db: Session, tg: FakeTG, alerts) -> None:  # type: ignore[no-untyped-def]
    a = _setup(client, db, tg, "main", "+971500000001")
    b = _setup(client, db, tg, "second", "+971500000002")
    for uid in (30, 31):
        _asker(db, a, uid)
    _asker(db, b, 40)
    tg.fail["u30"] = gw.RateLimited(24 * 3600, "Telegram limited messaging (PeerFloodError)")
    asyncio.run(svc.send_approved(db, _settings(client)))
    assert tg.sent == []  # nothing after the restriction, from any account
    acc = db.get(InviteAccount, a)
    assert acc.halted_until > utcnow() + timedelta(hours=23) and "PeerFlood" in acc.halt_reason
    assert [k for k, _ in alerts] == [f"invites.paused.{a}"]
    assert 30 in [c.user_id for c in svc.queue(db, a)]  # refused by Telegram = not delivered: still waiting, not burnt
    assert db.scalar(select(func.count()).select_from(InviteSend)) == 0
    asyncio.run(svc.send_approved(db, _settings(client)))
    assert tg.sent == [] and len(alerts) == 1  # still paused: no account takes over, no second alert
    assert client.post("/api/invites/one/send", json={"account_id": b, "user_id": 40}).status_code == 400


def test_a_dead_session_is_wiped_and_never_retried(client: TestClient, db: Session, tg: FakeTG, alerts, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    a = _setup(client, db, tg)
    _asker(db, a, 50)
    tg.fail["u50"] = gw.AccountGone("The Telegram session no longer works (AuthKeyUnregisteredError): log in again")
    asyncio.run(svc.send_approved(db, _settings(client)))
    acc = db.get(InviteAccount, a)
    assert acc.session_enc is None and acc.status == "error" and "AuthKeyUnregistered" in acc.last_error
    assert [k for k, _ in alerts] == [f"invites.account.{a}"]
    assert db.get(InviteConsent, 50).revoked_at is None  # their request isn't lost: nothing was sent
    stats = _wrap(monkeypatch)
    asyncio.run(svc.poll(db, _settings(client)))
    asyncio.run(svc.send_approved(db, _settings(client)))
    assert stats["opened"] == 0  # never reconnects with a dead key


def test_a_session_telegram_no_longer_authorises_is_wiped_by_the_monitor(client: TestClient, db: Session, tg: FakeTG, alerts, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    a = _setup(client, db, tg)

    def start(c):  # type: ignore[no-untyped-def]
        async def go() -> bool:
            return False
        return go

    stats = _wrap(monkeypatch, start=start)
    out = asyncio.run(svc.poll(db, _settings(client)))  # no exception escapes the job
    assert out["accounts"] == 0 and stats == {"opened": 1, "stopped": 1}  # the connection is closed
    acc = db.get(InviteAccount, a)
    assert acc.session_enc is None and acc.status == "error" and len(alerts) == 1
    asyncio.run(svc.poll(db, _settings(client)))
    assert stats["opened"] == 1


def test_flood_wait_while_reading_pauses_the_account_and_the_monitor_backs_off(client: TestClient, db: Session, tg: FakeTG, alerts, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    a = _setup(client, db, tg)
    client.post("/api/invites/groups", json={"account_id": a, "chat_id": -100, "title": "Crypto Talk"})

    def group_messages(c):  # type: ignore[no-untyped-def]
        async def go(*_a):  # type: ignore[no-untyped-def]
            raise gw.RateLimited(900, "Telegram rate limit (FloodWaitError): wait 900s")
        return go

    stats = _wrap(monkeypatch, group_messages=group_messages)
    asyncio.run(svc.poll(db, _settings(client)))
    assert db.get(InviteAccount, a).halted_until > utcnow() + timedelta(minutes=50) and len(alerts) == 1
    asyncio.run(svc.poll(db, _settings(client)))
    assert stats["opened"] == 1  # no reads at all until the wait is over


def test_a_group_the_account_left_doesnt_stop_the_inbox_or_other_groups(client: TestClient, db: Session, tg: FakeTG, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    a = _setup(client, db, tg)
    client.post("/api/invites/groups", json={"account_id": a, "chat_id": -100, "title": "Kicked From"})
    client.post("/api/invites/groups", json={"account_id": a, "chat_id": -200, "title": "Still Here"})
    tg.group_msgs[-200] = [_msg(1, 61, "gm")]
    tg.private = [_msg(1, 60, "stop", "nope")]

    def group_messages(c):  # type: ignore[no-untyped-def]
        inner = c.group_messages

        async def go(chat_id, after_id, limit):  # type: ignore[no-untyped-def]
            if chat_id == -100:
                raise gw.NotAllowed("ChannelPrivateError")
            return await inner(chat_id, after_id, limit)
        return go

    _wrap(monkeypatch, group_messages=group_messages)
    out = asyncio.run(svc.poll(db, _settings(client)))
    assert out == {"accounts": 1, "messages": 1, "opt_ins": 0, "opt_outs": 1}
    assert db.get(InviteConsent, 60).revoked_at is not None and db.get(InviteAccount, a).last_poll_at is not None


def test_a_send_whose_outcome_is_unknown_is_never_repeated(client: TestClient, db: Session, tg: FakeTG) -> None:
    """A crash between Telegram accepting the message and the database saving it leaves the 'sending' claim: that person
    counts as contacted (and towards the day's limit). A timeout counts as a failure: never retried."""
    a = _setup(client, db, tg)
    _asker(db, a, 70)
    _asker(db, a, 71)
    db.add(InviteSend(account_id=a, method="dm", user_id=70, status="sending", at=utcnow()))  # the crash leftover
    db.commit()
    tg.fail["u71"] = TimeoutError("timed out")
    t = asyncio.run(svc.send_approved(db, _settings(client)))
    assert tg.sent == [] and t["failed"] == 1 and svc.queue(db, a) == []
    tg.fail.clear()
    asyncio.run(svc.send_approved(db, _settings(client)))
    assert tg.sent == []
    assert client.post("/api/invites/one/send", json={"account_id": a, "user_id": 70}).status_code == 400
    assert svc.sent_today(db, a) == 2


def test_a_click_during_a_run_is_refused_at_once_and_the_run_never_sends_twice(client: TestClient, db: Session, tg: FakeTG, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    a = _setup(client, db, tg)
    for uid in (80, 81, 82):
        _asker(db, a, uid)
    s = _settings(client)
    seen: dict[str, object] = {}

    async def wait(_seconds: float) -> bool:  # between the run's sends (no Telegram connection, no lock held)
        if "click" not in seen:
            seen["click"] = (await svc.one_send(db, s, a, 81))["status"]  # the owner sends #81 by hand meanwhile
            svc.decline(db, 82)  # ... and skips #82
        return True

    monkeypatch.setattr(svc, "_wait", wait)
    t = asyncio.run(svc.send_approved(db, s))
    assert seen["click"] == "sent" and [p for _, p, _ in tg.sent] == ["u80", "u81"] and t["dm_sent"] == 1

    async def busy() -> str:  # while a send holds the account, a click is refused immediately (never waits for hours)
        async with svc.RT.lock(a):
            try:
                await svc.one_send(db, s, a, 999)
            except svc.InviteError as exc:
                return str(exc)
        return ""

    _asker(db, a, 999)
    assert "busy" in asyncio.run(busy())


def test_racing_claims_the_earlier_one_wins(client: TestClient, db: Session, tg: FakeTG) -> None:
    a = _setup(client, db, tg)
    _asker(db, a, 90)
    acc, s = db.get(InviteAccount, a), svc.settings_row(db)
    c = db.get(InviteConsent, 90)
    first = InviteSend(account_id=a, method="dm", user_id=90, status="sending", at=utcnow())  # another process got there first
    db.add(first)
    db.commit()
    row, reason = svc._claim(db, s, acc, "dm", c)
    assert row is None and reason == "already"
    assert db.scalar(select(func.count()).select_from(InviteSend)) == 1


def test_limit_and_pacing_hold_across_runs_and_clicks_and_failures_count(client: TestClient, db: Session, tg: FakeTG, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    a = _setup(client, db, tg)
    client.put("/api/invites/settings", json={"daily_limit": 2, "dm_interval_seconds": 300})
    for uid in (100, 101, 102, 103):
        _asker(db, a, uid)
    s = _settings(client)
    waits: list[float] = []

    async def wait(seconds: float) -> bool:
        waits.append(seconds)
        return True

    monkeypatch.setattr(svc, "_wait", wait)
    tg.fail["u100"] = gw.Unreachable("UserPrivacyRestrictedError")
    tg.fail["u101"] = gw.Unreachable("UserPrivacyRestrictedError")
    t = asyncio.run(svc.send_approved(db, s))
    assert t["failed"] == 2 and t["skipped_limit"] == 2 and tg.sent == []  # failed attempts count towards the limit
    assert len(waits) == 2  # and are paced like sends (never back-to-back)

    db.execute(InviteSend.__table__.update().values(at=utcnow() - timedelta(days=1)))
    db.commit()
    svc.RT.pace = None  # the real interval: a click right after a send must wait too
    assert client.post("/api/invites/one/send", json={"account_id": a, "user_id": 102}).json() == {"status": "sent", "mode": "auto"}
    r = client.post("/api/invites/one/send", json={"account_id": a, "user_id": 103})
    assert r.status_code == 400 and "Too soon" in r.json()["detail"] and [p for _, p, _ in tg.sent] == ["u102"]
    assert 103 in [c.user_id for c in svc.queue(db, a)]  # not burnt: sent later


def test_a_failing_group_post_waits_the_full_interval(client: TestClient, db: Session, tg: FakeTG) -> None:
    a = _setup(client, db, tg)
    client.put("/api/invites/settings", json={"group_posts_enabled": True})
    client.post("/api/invites/groups", json={"account_id": a, "chat_id": -200, "title": "Friendly Group", "allow_posts": True})
    tg.fail[-200] = ConnectionError("network")
    assert asyncio.run(svc.send_approved(db, _settings(client)))["failed"] == 1
    tg.fail.clear()
    asyncio.run(svc.send_approved(db, _settings(client)))
    assert tg.sent == [] and svc.posts_due(db, a, 7) == []  # not re-posted every 3 minutes
    assert db.scalar(select(InviteGroup.allow_posts).where(InviteGroup.chat_id == -200)) is True


def test_auto_never_sends_while_monitoring_is_off(client: TestClient, db: Session, tg: FakeTG) -> None:
    a = _setup(client, db, tg)
    _asker(db, a, 110)
    client.put("/api/invites/settings", json={"monitoring": False})
    assert svc.maybe_auto_send(db, _settings(client)) is False and tg.sent == []


def test_a_failed_login_closes_its_connection(client: TestClient, db: Session, tg: FakeTG, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    client.put("/api/invites/settings", json={"api_id": 12345, "api_hash": "a" * 32})

    def send_code(c):  # type: ignore[no-untyped-def]
        async def go(phone: str) -> str:
            raise RuntimeError("PhoneNumberInvalidError")
        return go

    stats = _wrap(monkeypatch, send_code=send_code)
    assert client.post("/api/invites/accounts/login", json={"name": "x", "phone": "+971500000009"}).status_code == 400
    assert stats == {"opened": 1, "stopped": 1} and svc.RT.pending == {}


# --- the real Telethon gateway's error mapping (no network: the client is replaced) ------------------------------------


def _telethon_gateway(send_error: Exception | None = None, resolves_to: int = 5):  # type: ignore[no-untyped-def]
    g = gw.TelethonGateway(1, "a" * 32)
    assert g.client.flood_sleep_threshold == 0  # Telethon never sleeps through a FloodWait and re-sends by itself
    calls: list[object] = []

    async def get_entity(peer):  # type: ignore[no-untyped-def]
        return SimpleNamespace(id=resolves_to)

    async def send_message(peer, text, **kw):  # type: ignore[no-untyped-def]
        calls.append(peer)
        if send_error:
            raise send_error

    g.client = SimpleNamespace(get_entity=get_entity, send_message=send_message)
    return g, calls


def test_telethon_errors_are_mapped_to_stop_the_account_not_burn_the_recipient() -> None:
    from telethon import errors as e

    cases = [
        (e.FloodWaitError(request=None, capture=120), gw.RateLimited),
        (e.FloodPremiumWaitError(request=None, capture=5), gw.RateLimited),
        (e.PeerFloodError(request=None), gw.RateLimited),
        (e.UserBannedInChannelError(request=None), gw.RateLimited),
        (e.FrozenMethodInvalidError(request=None), gw.RateLimited),
        (e.UserDeactivatedBanError(request=None), gw.RateLimited),
        (e.UserDeactivatedError(request=None), gw.AccountGone),  # THIS account, not the recipient
        (e.AuthKeyUnregisteredError(request=None), gw.AccountGone),
        (e.SessionRevokedError(request=None), gw.AccountGone),
        (e.AuthKeyDuplicatedError(request=None), gw.AccountGone),
        (e.UserPrivacyRestrictedError(request=None), gw.Unreachable),
        (e.UserIsBlockedError(request=None), gw.Unreachable),
        (e.ChatWriteForbiddenError(request=None), gw.NotAllowed),
    ]
    for err, expected in cases:
        g, _ = _telethon_gateway(err)
        with pytest.raises(expected):
            asyncio.run(g.send("someone", "hi", expect_id=5))
    g, _ = _telethon_gateway(e.FloodWaitError(request=None, capture=120))
    with pytest.raises(gw.RateLimited) as info:
        asyncio.run(g.send("someone", "hi"))
    assert info.value.seconds == 120


def test_a_username_that_now_belongs_to_someone_else_gets_nothing() -> None:
    g, calls = _telethon_gateway(resolves_to=6)
    with pytest.raises(gw.Unreachable):
        asyncio.run(g.send("someone", "hi", expect_id=5))
    assert calls == []
    g, calls = _telethon_gateway(resolves_to=5)
    asyncio.run(g.send("someone", "hi", expect_id=5))
    assert len(calls) == 1
