"""The store-review account: a password sign-in that does nothing unless configured, with one login on one server for a limited time."""

import datetime as dt
import importlib.util
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.settings import get_settings
from app.models.members import Member
from app.models.vpn import VpnBrowser
from app.security import admin_auth
from app.vpn import reviewer
from app.vpn import service as vpn
from tests.test_vpn import add_server, member_at, open_vpn, signed_in
from tests.test_vpn_browser import ProxyNode, fake  # noqa: F401

EMAIL, PASSWORD = "reviewer@example.com", "a-long-random-test-password"
LOGIN = "/api/members/auth/reviewer"


def tomorrow() -> str:
    return (dt.datetime.now(dt.timezone.utc).date() + dt.timedelta(days=1)).isoformat()


@pytest.fixture
def account(monkeypatch: pytest.MonkeyPatch) -> None:
    s = get_settings()
    monkeypatch.setattr(s, "vpn_reviewer_email", EMAIL)
    monkeypatch.setattr(s, "vpn_reviewer_password_hash", SecretStr(reviewer.hash_password(PASSWORD)))
    monkeypatch.setattr(s, "vpn_reviewer_until", tomorrow())


def sign_in(client: TestClient, password: str = PASSWORD, email: str = EMAIL) -> int:
    return client.post(LOGIN, json={"email": email, "password": password}).status_code


def helsinki(db: Session, node: ProxyNode):
    server = add_server(db, node, "Helsinki 1")
    other = add_server(db, node, "Frankfurt")
    for x in (server, other):
        x.proxy_host, x.online = f"{x.name.split()[0].lower()}.example", True
    db.commit()
    return server, other


def test_off_unless_configured_and_never_says_why(client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    assert sign_in(client) == 401
    assert client.cookies.get("pc_session") is None
    s = get_settings()
    monkeypatch.setattr(s, "vpn_reviewer_email", EMAIL)
    monkeypatch.setattr(s, "vpn_reviewer_password_hash", SecretStr(reviewer.hash_password(PASSWORD)))
    monkeypatch.setattr(s, "vpn_reviewer_until", "not a date")
    assert sign_in(client) == 401


def test_wrong_details_are_refused_the_same_way(client: TestClient, db: Session, account: None) -> None:
    a = client.post(LOGIN, json={"email": EMAIL, "password": "wrong-password"})
    b = client.post(LOGIN, json={"email": "other@example.com", "password": PASSWORD})
    assert a.status_code == b.status_code == 401 and a.json()["detail"] == b.json()["detail"]
    assert db.scalar(select(Member).where(Member.review_seat.is_(True))) is None and db.scalar(select(Member).where(Member.email == EMAIL)) is None


def test_works_while_closed_with_one_login_on_one_server(client: TestClient, db: Session, account: None, fake: ProxyNode) -> None:  # noqa: F811
    server, other = helsinki(db, fake)
    assert vpn.settings(db).enabled is False
    db.commit()
    assert sign_in(client) == 200
    me = client.get("/api/members/vpn").json()
    assert me["enabled"] is True and me["eligible"] is True and me["test_mode"] is False and me["max_devices"] == 1
    assert [x["name"] for x in me["servers"]] == ["Helsinki 1"]
    assert client.post("/api/members/vpn/browser", json={"server_id": other.id}).status_code == 404
    assert client.post("/api/members/vpn/browser", json={"server_id": server.id}).status_code == 201
    assert client.post("/api/members/vpn/browser", json={"server_id": server.id}).status_code == 201
    assert len(fake.users) == 1 and db.scalar(select(VpnBrowser.id).limit(1)) is not None
    assert client.post("/api/members/vpn/devices", json={"name": "x", "server_id": server.id}).status_code == 403


def test_no_admin_rights_and_off_the_leaderboard(client: TestClient, db: Session, account: None) -> None:
    sign_in(client)
    assert client.cookies.get(admin_auth.COOKIE) is None  # the Admin session is a separate cookie, and this sign-in never sets it
    db.expire_all()
    seat = db.scalar(select(Member).where(Member.review_seat.is_(True)))
    assert seat.leaderboard_opt_out is True and seat.email is None  # no email: no email-code sign-in can reach a seat


def test_no_payment_needed_while_payments_are_on(client: TestClient, db: Session, account: None, fake: ProxyNode) -> None:  # noqa: F811
    server, _ = helsinki(db, fake)
    open_vpn(db)
    sign_in(client)
    assert client.post("/api/members/vpn/browser", json={"server_id": server.id}).status_code == 201


def test_ends_on_time_and_sync_takes_the_login_away(client: TestClient, db: Session, account: None, fake: ProxyNode, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    server, _ = helsinki(db, fake)
    sign_in(client)
    client.post("/api/members/vpn/browser", json={"server_id": server.id})
    assert fake.users
    monkeypatch.setattr(get_settings(), "vpn_reviewer_until", "2020-01-01")
    assert client.get("/api/members/vpn").json()["eligible"] is False
    assert client.post("/api/members/vpn/browser", json={"server_id": server.id}).status_code == 403
    vpn.sync_all(db)
    assert not fake.users
    client.cookies.clear()
    assert sign_in(client) == 401


def test_admin_block_still_wins(client: TestClient, db: Session, account: None, fake: ProxyNode) -> None:  # noqa: F811
    server, _ = helsinki(db, fake)
    sign_in(client)
    db.expire_all()
    member = db.scalar(select(Member).where(Member.review_seat.is_(True)))
    vpn.account_of(db, member).status = "blocked"
    db.commit()
    assert client.post("/api/members/vpn/browser", json={"server_id": server.id}).status_code == 403


def test_other_members_stay_out_while_closed(client: TestClient, db: Session, account: None, fake: ProxyNode) -> None:  # noqa: F811
    server, _ = helsinki(db, fake)
    c = signed_in(client, db, member_at(db, "egg", "someone", paid=True))
    assert c.get("/api/members/vpn").json()["enabled"] is False
    assert c.post("/api/members/vpn/browser", json={"server_id": server.id}).status_code == 403


def test_the_owners_script_makes_a_hash_the_server_accepts() -> None:
    path = Path(__file__).resolve().parents[2] / "vpn-extension" / "store" / "reviewer_account.py"
    spec = importlib.util.spec_from_file_location("reviewer_account", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert reviewer.check_password(mod.make_hash(PASSWORD), PASSWORD)
    assert not reviewer.check_password(mod.make_hash(PASSWORD), "wrong")
