"""The store-review account in the Kiwi VPN Android and iPhone apps (owner, 2026-11-20): a reviewer signs in with the review
password on the app's sign-in page, presses Allow, and the app makes ONE WireGuard device with its own key on the review server.

Restricted: the password sign-in's browser session ("kr.") reaches only the VPN, who's signed in, deletion, sign-out and handing
the app its session; WireGuard devices only from an app session ("kd."), with the phone's own key, on SERVER, one at a time.
"""

import datetime as dt

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.settings import get_settings
from app.members import service as members
from app.models.members import Member, MemberSession
from app.models.vpn import VpnDevice
from app.vpn import keys, reviewer
from app.vpn import service as vpn
from tests.test_vpn import member_at, signed_in
from tests.test_vpn_browser import ProxyNode, fake  # noqa: F401
from tests.test_vpn_desktop import CODE, TOKEN, pair
from tests.test_vpn_reviewer import EMAIL, LOGIN, PASSWORD, account, helsinki, sign_in  # noqa: F401

DEVICES = "/api/members/vpn/devices"


def app_session(client: TestClient) -> str:
    """What the phone does: the review sign-in in the browser sheet, Allow, then the code and verifier swapped for the app's session."""
    assert sign_in(client) == 200
    verifier, challenge = pair()
    code = client.post(CODE, json={"challenge": challenge})
    assert code.status_code == 200, code.text
    client.cookies.clear()
    out = client.post(TOKEN, json={"code": code.json()["code"], "verifier": verifier})
    assert out.status_code == 200, out.text
    return out.json()["token"]


def as_app(token: str) -> dict[str, str]:
    return {"Cookie": f"pc_session={token}"}


def new_key() -> str:
    return keys.generate_keypair()[1]


def test_the_password_sign_in_is_a_scoped_review_session(client: TestClient, db: Session, account: None) -> None:  # noqa: F811
    assert sign_in(client) == 200
    token = client.cookies.get("pc_session")
    assert token and token.startswith(reviewer.REVIEW_TOKEN_PREFIX)
    row = db.scalar(select(MemberSession).where(MemberSession.token_hash == members._hash(token)))
    assert row is not None and row.kind == "reviewer"
    assert client.get("/api/members/vpn").status_code == 200
    assert client.get("/api/members/me").status_code == 200
    assert client.get("/api/members/auth/providers").status_code == 200
    # the rest of Price Chat is out of reach, including linking another sign-in to this shared account
    for method, path, body in [("POST", "/api/members/username/check", {"username": "x"}), ("GET", "/api/members/desktop/sessions", None),
                               ("POST", "/api/members/auth/email/start", {"email": "me@example.com", "purpose": "link"}),
                               ("GET", "/api/members/auth/google/start?purpose=link", None), ("PATCH", "/api/members/me", {"display_name": "x"}),
                               ("PATCH", "/api/members/me", {"leaderboard_opt_out": False})]:
        r = client.request(method, path, json=body)
        assert r.status_code == 403 and r.json()["code"] == "review_scope", (path, r.status_code)
    assert client.post("/api/members/logout").status_code in (200, 204)


def test_scope_rules() -> None:
    may = reviewer.review_token_may
    assert may("/api/members/vpn") and may("/api/members/vpn/devices") and may("/api/members/vpn/v1/state") and may("/api/members/me/")
    assert may("/api/members/desktop/code") and may("/api/members/auth/reviewer") and may("/api/members/me", "DELETE")
    assert not may("/api/members/me", "PATCH") and not may("/api/members/me", "POST")
    for path in ("/api/members/me/notifications", "/api/members/auth/email/verify", "/api/members/desktop/token", "/api/members/vpnx",
                 "/api/members/xp", "/api/admin/anything", "/api/tradingbot/orders"):
        assert not may(path), path


def test_the_website_session_cant_make_a_wireguard_device(client: TestClient, db: Session, account: None, fake: ProxyNode) -> None:  # noqa: F811
    server, _ = helsinki(db, fake)
    sign_in(client)
    r = client.post(DEVICES, json={"name": "web", "server_id": server.id})
    assert r.status_code == 403 and r.json()["detail"] == vpn.REVIEW_APP_ONLY
    r = client.post(DEVICES, json={"name": "web", "server_id": server.id, "public_key": new_key()})
    assert r.status_code == 403 and not fake.peers  # a key from the website is no better: only the app's session


def test_the_app_connects_on_the_review_server_with_its_own_key(client: TestClient, db: Session, account: None, fake: ProxyNode) -> None:  # noqa: F811
    server, other = helsinki(db, fake)
    assert vpn.settings(db).enabled is False  # closed to everyone else
    db.commit()
    token = app_session(client)
    assert token.startswith("kd.")
    me = client.get("/api/members/vpn", headers=as_app(token)).json()
    assert me["eligible"] is True and me["enabled"] is True and [s["name"] for s in me["servers"]] == [reviewer.SERVER]
    state = client.get("/api/members/vpn/v1/state?platform=ios&version=0.2.0", headers=as_app(token))
    assert state.status_code == 200
    key = new_key()
    assert client.post(DEVICES, json={"name": "iPhone", "server_id": other.id, "public_key": key}, headers=as_app(token)).status_code == 404
    assert client.post(DEVICES, json={"name": "iPhone", "server_id": server.id}, headers=as_app(token)).status_code == 403  # no server-made key
    r = client.post(DEVICES, json={"name": "iPhone", "server_id": server.id, "public_key": key}, headers=as_app(token))
    assert r.status_code == 201, r.text
    peer = r.json()["peer"]
    assert peer["endpoint_host"] == fake.endpoint and peer["server_public_key"] == fake.server_key and peer["addresses"]
    assert "private_key" not in r.text and "PrivateKey" not in r.text  # the phone's key never came here
    assert list(fake.peers) == [key]
    # the same key again (a retry whose answer was lost) is the same device, nothing new
    again = client.post(DEVICES, json={"name": "iPhone", "server_id": server.id, "public_key": key}, headers=as_app(token))
    assert again.status_code in (200, 201) and again.json()["device"]["id"] == r.json()["device"]["id"]


def test_one_device_at_a_time_the_newest_replaces_the_last(client: TestClient, db: Session, account: None, fake: ProxyNode) -> None:  # noqa: F811
    server, _ = helsinki(db, fake)
    token = app_session(client)
    first, second = new_key(), new_key()
    assert client.post(DEVICES, json={"name": "Pixel 8 (1)", "server_id": server.id, "public_key": first}, headers=as_app(token)).status_code == 201
    assert client.post(DEVICES, json={"name": "iPhone 15", "server_id": server.id, "public_key": second}, headers=as_app(token)).status_code == 201
    db.expire_all()
    assert [d.public_key for d in db.scalars(select(VpnDevice))] == [second]
    assert list(fake.peers) == [second]


def test_ends_on_time_and_sync_takes_the_device_off_the_server(client: TestClient, db: Session, account: None, fake: ProxyNode,  # noqa: F811
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    server, _ = helsinki(db, fake)
    token = app_session(client)
    assert client.post(DEVICES, json={"name": "Pixel", "server_id": server.id, "public_key": new_key()}, headers=as_app(token)).status_code == 201
    assert fake.peers
    monkeypatch.setattr(get_settings(), "vpn_reviewer_until", "2020-01-01")
    assert client.get("/api/members/vpn", headers=as_app(token)).json()["eligible"] is False
    assert client.post(DEVICES, json={"name": "Pixel 2", "server_id": server.id, "public_key": new_key()}, headers=as_app(token)).status_code == 403
    vpn.sync_all(db)
    assert not fake.peers


def test_removing_the_variables_ends_it_too(client: TestClient, db: Session, account: None, fake: ProxyNode, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    server, _ = helsinki(db, fake)
    token = app_session(client)
    client.post(DEVICES, json={"name": "Pixel", "server_id": server.id, "public_key": new_key()}, headers=as_app(token))
    monkeypatch.setattr(get_settings(), "vpn_reviewer_email", "")
    vpn.sync_all(db)
    assert not fake.peers  # an ordinary member now, with its trial used: nothing


def test_admin_block_still_wins_in_the_app(client: TestClient, db: Session, account: None, fake: ProxyNode) -> None:  # noqa: F811
    server, _ = helsinki(db, fake)
    token = app_session(client)
    db.expire_all()
    vpn.account_of(db, db.scalar(select(Member).where(Member.email == EMAIL))).status = "blocked"
    db.commit()
    assert client.post(DEVICES, json={"name": "Pixel", "server_id": server.id, "public_key": new_key()}, headers=as_app(token)).status_code == 403


def test_a_reviewer_can_delete_the_account_and_sign_in_again_afterwards(client: TestClient, db: Session, account: None, fake: ProxyNode) -> None:  # noqa: F811
    """App Review tests deletion for real. It deletes, and the next reviewer's sign-in makes a fresh review account."""
    server, _ = helsinki(db, fake)
    token = app_session(client)
    assert client.post(DEVICES, json={"name": "iPhone", "server_id": server.id, "public_key": new_key()}, headers=as_app(token)).status_code == 201
    username = client.get("/api/members/me", headers=as_app(token)).json()["member"]["username"]
    first = vpn.settings(db).reviewer_member_id
    first_public = db.get(Member, first).public_id
    r = client.request("DELETE", "/api/members/me", json={"confirm_username": username}, headers=as_app(token))
    assert r.status_code == 200 and r.json() == {"deleted": True}
    db.expire_all()
    assert db.get(Member, first) is None and not list(db.scalars(select(VpnDevice)))
    assert client.get("/api/members/vpn", headers=as_app(token)).status_code == 401
    client.cookies.clear()
    token2 = app_session(client)
    db.expire_all()
    fresh = db.get(Member, vpn.settings(db).reviewer_member_id)  # SQLite may reuse the number: a new member all the same
    assert fresh is not None and fresh.public_id != first_public and reviewer.is_reviewer(db, fresh)
    assert client.post(DEVICES, json={"name": "iPhone", "server_id": server.id, "public_key": new_key()}, headers=as_app(token2)).status_code == 201


def test_signing_in_ends_old_unscoped_browser_sessions(client: TestClient, db: Session, account: None) -> None:  # noqa: F811
    assert sign_in(client) == 200
    db.expire_all()
    rev = db.get(Member, vpn.settings(db).reviewer_member_id)
    old = members.new_session(db, rev, 30)  # what the sign-in issued before it was scoped
    assert client.get("/api/members/me", headers={"Cookie": f"pc_session={old}"}).status_code == 200
    client.cookies.clear()
    assert sign_in(client) == 200
    assert client.get("/api/members/me", headers={"Cookie": f"pc_session={old}"}).status_code == 401


def test_ordinary_members_are_unchanged(client: TestClient, db: Session, account: None, fake: ProxyNode) -> None:  # noqa: F811
    server, other = helsinki(db, fake)
    vpn.settings(db).enabled = True
    db.commit()
    c = signed_in(client, db, member_at(db, "egg", "someone", paid=True))
    assert not (c.cookies.get("pc_session") or "").startswith(reviewer.REVIEW_TOKEN_PREFIX)
    assert c.post(DEVICES, json={"name": "laptop", "server_id": other.id}).status_code == 201  # a server-made config, any server
    assert c.post("/api/members/username/check", json={"username": "x"}).status_code == 200
