"""The store-review account in the Kiwi VPN Android and iPhone apps (owner, 2026-11-20): a reviewer signs in with the review
password on the app's sign-in page, presses Allow, and the app makes ONE WireGuard device with its own key on the review server.

Restricted: the password sign-in's browser session ("kr.") reaches only the VPN, who's signed in, deletion, sign-out and handing
the app its session; WireGuard devices only from an app session ("kd."), with the phone's own key, on SERVER, one per seat.
Every sign-in is its own seat, so reviewers at the same time never disconnect each other; a seat refuses unscoped sessions.
"""

import datetime as dt
import uuid
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session

from app.config.settings import get_settings
from app.db.base import utcnow
from app.db.migrate import alembic_config, upgrade_database
from app.db.session import build_engine
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


def test_one_device_per_seat_the_newest_replaces_the_last(client: TestClient, db: Session, account: None, fake: ProxyNode) -> None:  # noqa: F811
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
    vpn.account_of(db, db.get(Member, vpn.settings(db).reviewer_member_id)).status = "blocked"
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


def seats(db: Session) -> list[Member]:
    db.expire_all()
    return list(db.scalars(select(Member).where(Member.review_seat.is_(True)).order_by(Member.id)))


def test_two_reviewers_at_once_never_disconnect_each_other(client: TestClient, db: Session, account: None, fake: ProxyNode) -> None:  # noqa: F811
    """Google Play's reviewer on Android and App Review on an iPhone, at the same time: two seats, two devices, and one
    deleting their account leaves the other connected."""
    server, _ = helsinki(db, fake)
    android, iphone = app_session(client), app_session(client)
    assert len(seats(db)) == 2
    a_key, i_key = new_key(), new_key()
    assert client.post(DEVICES, json={"name": "Pixel 8", "server_id": server.id, "public_key": a_key}, headers=as_app(android)).status_code == 201
    assert client.post(DEVICES, json={"name": "iPhone 15", "server_id": server.id, "public_key": i_key}, headers=as_app(iphone)).status_code == 201
    assert set(fake.peers) == {a_key, i_key}
    vpn.sync_server(db, server)  # (both test servers share one fake node: sync only this one)
    assert set(fake.peers) == {a_key, i_key}
    mine = client.get("/api/members/vpn", headers=as_app(android)).json()
    assert [d["name"] for d in mine["devices"]] == ["Pixel 8"]  # each sees only its own device
    username = client.get("/api/members/me", headers=as_app(iphone)).json()["member"]["username"]
    assert client.request("DELETE", "/api/members/me", json={"confirm_username": username}, headers=as_app(iphone)).status_code == 200
    vpn.sync_server(db, server)
    assert set(fake.peers) == {a_key}
    assert client.get("/api/members/vpn", headers=as_app(android)).json()["eligible"] is True
    assert client.get("/api/members/vpn", headers=as_app(iphone)).status_code == 401
    assert len(seats(db)) == 1


def test_the_same_browser_signing_in_again_keeps_its_seat(client: TestClient, db: Session, account: None) -> None:  # noqa: F811
    assert sign_in(client) == 200
    first = [m.id for m in seats(db)]
    assert sign_in(client) == 200  # still holding the seat's "kr." cookie
    assert [m.id for m in seats(db)] == first and len(first) == 1
    client.cookies.clear()
    assert sign_in(client) == 200  # another browser: a new seat
    assert len(seats(db)) == 2


def test_seats_are_capped_reclaiming_unused_ones_first(client: TestClient, db: Session, account: None, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    monkeypatch.setattr(reviewer, "MAX_SEATS", 3)
    sign_in(client)
    client.post("/api/members/logout")  # seat 1: no live session
    client.cookies.clear()
    made = []
    for _ in range(2):
        sign_in(client)
        made.append(client.cookies.get("pc_session"))
        client.cookies.clear()
    one, two, three = (m.id for m in seats(db))
    for i, token in enumerate(made):  # seat 3 used more recently than seat 2
        row = db.scalar(select(MemberSession).where(MemberSession.token_hash == members._hash(token)))
        row.last_seen_at = utcnow() - dt.timedelta(hours=2 - i)
    db.commit()
    sign_in(client)
    client.cookies.clear()
    left = [m.id for m in seats(db)]
    assert one not in left and len(left) == 3
    sign_in(client)
    left = [m.id for m in seats(db)]
    assert two not in left and three in left and len(left) == 3


def test_a_seat_refuses_unscoped_sessions_at_once(client: TestClient, db: Session, account: None) -> None:  # noqa: F811
    """A browser session without the "kr." scope (from before the restriction) stops working on its next request, without
    waiting for anyone to sign in again, and is deleted."""
    assert sign_in(client) == 200
    seat = seats(db)[0]
    old = members.new_session(db, seat, 30)  # what the sign-in issued before it was scoped
    assert client.get("/api/members/me", headers={"Cookie": f"pc_session={old}"}).status_code == 401
    assert client.post("/api/members/username/check", json={"username": "x"}, headers={"Cookie": f"pc_session={old}"}).status_code == 401
    assert db.scalar(select(MemberSession).where(MemberSession.token_hash == members._hash(old))) is None
    assert client.get("/api/members/vpn").status_code == 200  # the scoped one still works


def test_signing_in_also_deletes_a_seats_unscoped_sessions(client: TestClient, db: Session, account: None) -> None:  # noqa: F811
    assert sign_in(client) == 200
    seat = seats(db)[0]
    old = members.new_session(db, seat, 30)
    assert sign_in(client) == 200
    assert db.scalar(select(MemberSession).where(MemberSession.token_hash == members._hash(old))) is None


def test_migration_makes_the_bound_review_account_a_seat_and_revokes_its_unscoped_sessions(tmp_path: Path) -> None:
    url = f"sqlite:///{(tmp_path / 'prod.db').as_posix()}"
    engine = build_engine(url, enforce_foreign_keys=False)
    with engine.begin() as conn:
        config = alembic_config()
        config.attributes["connection"] = conn
        command.upgrade(config, "0118_nd_editor")
        now = utcnow().isoformat()
        later = (utcnow() + dt.timedelta(days=20)).isoformat()

        def insert(table: str, **values: Any) -> None:
            cols = {c["name"]: c for c in inspect(conn).get_columns(table)}
            for name, c in cols.items():
                if name not in values and not c["nullable"] and c.get("default") is None and name != "id":
                    t = str(c["type"]).upper()
                    values[name] = 0 if ("INT" in t or "BOOL" in t) else now if ("DATE" in t or "TIME" in t) else "{}" if "JSON" in t else f"x{name}-{uuid.uuid4().hex[:8]}"
            conn.execute(text(f"INSERT INTO {table} ({', '.join(values)}) VALUES ({', '.join(':' + k for k in values)})"), values)

        insert("members", id=1, email=EMAIL, email_verified_at=now)
        insert("members", id=2, email="someone@example.com")
        insert("vpn_settings", id=1, reviewer_member_id=1)
        for i, kind in enumerate([None, "reviewer", "desktop", None]):
            insert("member_sessions", member_id=1, token_hash=f"r{i}", kind=kind, expires_at=later)
        insert("member_sessions", member_id=2, token_hash="p0", kind=None, expires_at=later)
    engine.dispose()
    upgrade_database(url)
    check = build_engine(url)
    with check.connect() as conn:
        rows = {r.id: r for r in conn.execute(text("SELECT id, email, email_verified_at, review_seat FROM members"))}
        kinds = sorted(conn.execute(text("SELECT kind FROM member_sessions WHERE member_id = 1")).scalars())
        others = list(conn.execute(text("SELECT token_hash FROM member_sessions WHERE member_id = 2")).scalars())
    check.dispose()
    assert rows[1].review_seat and rows[1].email is None and rows[1].email_verified_at is None
    assert not rows[2].review_seat and rows[2].email == "someone@example.com"
    assert kinds == ["desktop", "reviewer"] and others == ["p0"]


def test_ordinary_members_are_unchanged(client: TestClient, db: Session, account: None, fake: ProxyNode) -> None:  # noqa: F811
    server, other = helsinki(db, fake)
    vpn.settings(db).enabled = True
    db.commit()
    c = signed_in(client, db, member_at(db, "egg", "someone", paid=True))
    assert not (c.cookies.get("pc_session") or "").startswith(reviewer.REVIEW_TOKEN_PREFIX)
    assert c.post(DEVICES, json={"name": "laptop", "server_id": other.id}).status_code == 201  # a server-made config, any server
    assert c.post("/api/members/username/check", json={"username": "x"}).status_code == 200
