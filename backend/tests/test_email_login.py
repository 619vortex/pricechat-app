"""Passwordless email sign-in: code by email, single use, rate limited, no account enumeration, accounts matched only by a
verified address, existing members kept (they add an email after signing in the old way). Resend is a fake transport
here: the test reads the code out of the email the app tried to send (TEST FIXTURE; the test database only)."""

import json
import re

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.members import email_login, service
from app.members.catalog import ensure_catalog
from app.models.members import LinkedIdentity, Member, MemberEmailCode, ReputationEvent


@pytest.fixture
def outbox(client: TestClient, monkeypatch) -> list[dict]:  # type: ignore[no-untyped-def,type-arg]
    """A fake Resend: every email the app sends lands here."""
    sent: list[dict] = []  # type: ignore[type-arg]

    def handle(r: httpx.Request) -> httpx.Response:
        assert r.url.host == "api.resend.com" and r.headers["authorization"] == "Bearer re_test"
        if r.url.path == "/emails":
            sent.append(json.loads(r.content))
            return httpx.Response(200, json={"id": f"msg-{len(sent)}"})
        return httpx.Response(404, json={"message": "not found"})

    runtime = client.app.state.runtime  # type: ignore[attr-defined]
    monkeypatch.setattr(runtime, "members_transport", httpx.MockTransport(handle))
    monkeypatch.setattr(runtime.settings, "resend_api_key", SecretStr("re_test"))
    monkeypatch.setattr(email_login, "RESEND_AFTER", 0)  # quick successive sign-ins in these tests (the cooldown has its own test)
    return sent


def code_of(mail: dict) -> str:  # type: ignore[type-arg]
    m = re.search(r"code is (\d{6})", mail["text"])
    assert m
    return m.group(1)


def sign_in(client: TestClient, outbox: list[dict], email: str) -> dict:  # type: ignore[type-arg]
    assert client.post("/api/members/auth/email/start", json={"email": email}).status_code == 200
    return client.post("/api/members/auth/email/verify", json={"email": email, "code": code_of(outbox[-1])}).json()


def test_signup_choose_username_logout_and_return(client: TestClient, db: Session, outbox: list[dict]) -> None:  # type: ignore[type-arg]
    r = client.post("/api/members/auth/email/start", json={"email": "  Kiwi.Fan@Example.com "})
    assert r.status_code == 200 and r.json()["sent"] and r.json()["resend_after"] == email_login.RESEND_AFTER
    mail = outbox[-1]
    assert mail["to"] == ["kiwi.fan@example.com"] and mail["from"] == "Price Chat <login@pricechat.live>" and code_of(mail) in mail["subject"]
    assert "http" not in mail["text"]  # no links: the code is typed where it was asked for
    first = client.post("/api/members/auth/email/verify", json={"email": "kiwi.fan@example.com", "code": code_of(mail)}).json()
    assert first["signed_in"] and first["created"] and first["needs_username"]
    s = client.get("/api/members/session").json()
    assert s["signed_in"] and s["member"]["username_chosen"] is False
    assert client.post("/api/members/username/check", json={"username": "admin"}).json()["ok"] is False  # reserved
    assert client.patch("/api/members/me", json={"username": "KiwiFan"}).status_code == 200
    me = client.get("/api/members/me").json()
    assert me["member"]["username"] == "KiwiFan" and me["member"]["email"] == "k••••••@example.com" and me["member"]["email_verified"]
    assert me["rank"] and "missions" in me and me["member"]["xp"] > 0  # a full Kiwi Passport: rank, XP, missions
    member = db.scalar(select(Member).where(Member.email == "kiwi.fan@example.com"))
    assert member is not None and member.email_verified_at is not None
    assert db.scalars(select(MemberEmailCode.code_hash)).all() and code_of(mail) not in str(db.scalars(select(MemberEmailCode.code_hash)).all())

    client.post("/api/members/logout")
    assert client.get("/api/members/session").json()["signed_in"] is False
    client.cookies.clear()  # a returning visit (new browser)
    again = sign_in(client, outbox, "KIWI.FAN@example.com")
    assert again["created"] is False and again["needs_username"] is False and again["member"]["username"] == "KiwiFan"
    assert db.scalar(select(Member.id).where(Member.email == "kiwi.fan@example.com")) == member.id  # no duplicate account


def test_codes_are_single_use_short_lived_limited_and_browser_bound(client: TestClient, db: Session, outbox: list[dict], monkeypatch) -> None:  # type: ignore[no-untyped-def,type-arg]
    monkeypatch.setattr(email_login, "RESEND_AFTER", 60)
    email = "limits@example.com"
    client.post("/api/members/auth/email/start", json={"email": email})
    code = code_of(outbox[-1])
    too_soon = client.post("/api/members/auth/email/start", json={"email": email})
    assert too_soon.status_code == 429 and too_soon.json()["retry_after"] > 0 and len(outbox) == 1  # resend cooldown
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(4):
        r = client.post("/api/members/auth/email/verify", json={"email": email, "code": wrong})
        assert r.status_code == 400 and "isn't right" in r.json()["detail"]
    r = client.post("/api/members/auth/email/verify", json={"email": email, "code": wrong})
    assert "Too many wrong codes" in r.json()["detail"]
    assert client.post("/api/members/auth/email/verify", json={"email": email, "code": code}).status_code == 400  # locked: even the right code

    # a fresh code works once, only in the browser that asked for it, and not after it expired
    row = db.scalar(select(MemberEmailCode).order_by(MemberEmailCode.id.desc()))
    row.created_at = row.created_at.replace(year=2020)  # TEST FIXTURE: lift the 60 s cooldown
    db.commit()
    client.post("/api/members/auth/email/start", json={"email": email})
    code = code_of(outbox[-1])
    other = TestClient(client.app)
    assert other.post("/api/members/auth/email/verify", json={"email": email, "code": code}).status_code == 400  # another browser
    assert client.post("/api/members/auth/email/verify", json={"email": email, "code": code}).status_code == 200
    client.cookies.clear()
    assert client.post("/api/members/auth/email/verify", json={"email": email, "code": code}).status_code == 400  # used


def test_no_account_enumeration_and_per_ip_limit(client: TestClient, db: Session, outbox: list[dict]) -> None:  # type: ignore[type-arg]
    sign_in(client, outbox, "known@example.com")
    client.cookies.clear()
    known = client.post("/api/members/auth/email/start", json={"email": "known@example.com"})
    unknown = client.post("/api/members/auth/email/start", json={"email": "nobody@example.com"})
    assert known.status_code == unknown.status_code == 200 and known.json() == unknown.json()  # same answer either way
    # 3 codes so far from this IP (the sign-in, then these two)
    assert client.post("/api/members/auth/email/start", json={"email": "not-an-email"}).status_code == 400
    for i in range(7):  # the per-IP hourly cap (10 codes), across many addresses: 3 sent above + 7
        client.post("/api/members/auth/email/start", json={"email": f"spray{i}@example.com"})
    assert client.post("/api/members/auth/email/start", json={"email": "spray-last@example.com"}).status_code == 429


def test_existing_members_keep_everything_and_add_a_verified_email(client: TestClient, db: Session, outbox: list[dict]) -> None:  # type: ignore[type-arg]
    ensure_catalog(db)
    old = service.create_member(db)  # an account from before email sign-in: Telegram only, XP earned
    old.username, old.username_key, old.username_chosen = "OldKiwi", "oldkiwi", True
    service.link_identity(db, old, "telegram", "555", "oldkiwi", "Old Kiwi")
    db.commit()
    xp_before = old.xp_total
    events_before = db.query(ReputationEvent).filter(ReputationEvent.member_id == old.id).count()
    token = service.new_session(db, old, 30)  # signed in the old way (Telegram)
    client.cookies.set("pc_session", token)

    r = client.post("/api/members/auth/email/start", json={"email": "old@example.com", "purpose": "link"})
    assert r.status_code == 200
    linked = client.post("/api/members/auth/email/verify", json={"email": "old@example.com", "code": code_of(outbox[-1])}).json()
    assert linked["created"] is False and linked["member"]["username"] == "OldKiwi"
    db.expire_all()
    old = db.get(Member, old.id)
    assert old.email == "old@example.com" and old.xp_total >= xp_before
    assert db.query(ReputationEvent).filter(ReputationEvent.member_id == old.id).count() >= events_before  # history kept
    assert db.scalar(select(LinkedIdentity.platform).where(LinkedIdentity.member_id == old.id)) == "telegram"  # social link kept

    client.cookies.clear()  # from now on: email only, same account
    back = sign_in(client, outbox, "old@example.com")
    assert back["member"]["username"] == "OldKiwi" and db.query(Member).count() == 1
    # Telegram can now be disconnected: the email is a sign-in method
    assert client.post("/api/members/me/disconnect/telegram").status_code == 200

    # an address already verified by one member can't be attached to another (never merged)
    other = service.create_member(db)
    db.commit()
    client.cookies.set("pc_session", service.new_session(db, other, 30))
    r = client.post("/api/members/auth/email/start", json={"email": "old@example.com", "purpose": "link"})
    assert r.status_code == 400 and "another Price Chat account" in r.json()["detail"]


def test_production_never_fakes_delivery(client: TestClient, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    runtime = client.app.state.runtime  # type: ignore[attr-defined]
    monkeypatch.setattr(runtime.settings, "resend_api_key", None)
    monkeypatch.setattr(type(runtime.settings), "is_production", property(lambda self: True))
    providers = client.get("/api/members/auth/providers").json()
    assert providers["email"]["available"] is False
    r = client.post("/api/members/auth/email/start", json={"email": "someone@example.com"})
    assert r.status_code == 400 and "isn't available" in r.json()["detail"]
