"""Desktop sign-in: a code alone is useless, works once, expires, and the app's session works on the existing VPN routes. No password ever reaches the app."""

import base64
import hashlib
import secrets
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.members import service as members
from app.members.desktop_scope import DESKTOP_TOKEN_PREFIX
from app.models.desktop import MemberDesktopCode
from app.models.members import Member, MemberSession
from tests.test_vpn import FakeNode, add_server, fake, member_at, open_vpn, signed_in  # noqa: F401

DEVICES = "/api/members/vpn/devices"

CODE, TOKEN = "/api/members/desktop/code", "/api/members/desktop/token"


def pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    return verifier, base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


def code_for(client: TestClient, db: Session, name: str = "desk") -> tuple[str, str]:
    verifier, challenge = pair()
    c = signed_in(client, db, member_at(db, "egg", name, paid=False))
    code = c.post(CODE, json={"challenge": challenge}).json()["code"]
    c.cookies.clear()
    return code, verifier


def test_signed_out_gets_no_code(client: TestClient) -> None:
    assert client.post(CODE, json={"challenge": pair()[1]}).status_code == 401


def test_a_bad_challenge_is_refused(client: TestClient, db: Session) -> None:
    c = signed_in(client, db, member_at(db, "egg", "bad", paid=False))
    assert c.post(CODE, json={"challenge": "!" * 43}).status_code == 400
    assert c.post(CODE, json={"challenge": "short"}).status_code == 422


def test_the_app_gets_a_session_that_works_on_the_vpn_routes(client: TestClient, db: Session) -> None:
    code, verifier = code_for(client, db)
    out = client.post(TOKEN, json={"code": code, "verifier": verifier})
    assert out.status_code == 200 and out.json()["expires_in_days"] == 90 and "set-cookie" not in out.headers
    client.cookies.clear()
    assert client.get("/api/members/vpn").status_code == 401
    assert client.get("/api/members/vpn", headers={"Cookie": f"pc_session={out.json()['token']}"}).status_code == 200


def test_a_code_alone_is_useless_and_a_wrong_verifier_spends_it(client: TestClient, db: Session) -> None:
    code, verifier = code_for(client, db)
    assert client.post(TOKEN, json={"code": code, "verifier": secrets.token_urlsafe(48)}).status_code == 400
    assert client.post(TOKEN, json={"code": code, "verifier": verifier}).status_code == 400


def test_a_code_works_once(client: TestClient, db: Session) -> None:
    code, verifier = code_for(client, db)
    assert client.post(TOKEN, json={"code": code, "verifier": verifier}).status_code == 200
    assert client.post(TOKEN, json={"code": code, "verifier": verifier}).status_code == 400


def test_a_code_expires(client: TestClient, db: Session) -> None:
    code, verifier = code_for(client, db)
    row = db.query(MemberDesktopCode).one()
    row.expires_at = utcnow() - timedelta(seconds=1)
    db.commit()
    assert client.post(TOKEN, json={"code": code, "verifier": verifier}).status_code == 400


def test_the_code_itself_isnt_stored(client: TestClient, db: Session) -> None:
    code, _ = code_for(client, db)
    assert db.query(MemberDesktopCode).one().code_hash != code


def test_a_blocked_member_gets_no_session(client: TestClient, db: Session) -> None:
    verifier, challenge = pair()
    m = member_at(db, "egg", "blocked", paid=False)
    c = signed_in(client, db, m)
    code = c.post(CODE, json={"challenge": challenge}).json()["code"]
    m.status = "banned"
    db.commit()
    c.cookies.clear()
    assert client.post(TOKEN, json={"code": code, "verifier": verifier}).status_code == 400


def test_the_apps_session_reaches_only_the_vpn(client: TestClient, db: Session) -> None:
    code, verifier = code_for(client, db, "scoped")
    token = client.post(TOKEN, json={"code": code, "verifier": verifier}).json()["token"]
    assert token.startswith("kd.")
    client.cookies.set("pc_session", token)
    assert client.get("/api/members/vpn").status_code == 200
    assert client.get("/api/members/me").status_code == 200
    for method, path in [("GET", "/api/members/crew"), ("GET", "/api/members/packs"), ("POST", "/api/members/play/sell-all"), ("GET", "/api/vpn/admin/settings"), ("POST", "/api/members/desktop/code")]:
        refused = client.request(method, path)
        assert refused.status_code == 403 and refused.json()["code"] == "desktop_scope", path
    assert client.post("/api/members/logout").status_code == 200
    client.cookies.set("pc_session", token)
    assert client.get("/api/members/vpn").status_code == 401, "signing out ends it"


def test_a_browser_session_is_not_limited(client: TestClient, db: Session) -> None:
    c = signed_in(client, db, member_at(db, "egg", "browser", paid=False))
    assert not c.cookies.get("pc_session", "").startswith("kd.")
    assert c.get("/api/members/crew").json().get("code") != "desktop_scope"


# --- the app makes its own key, and asks for the rest

PUB = base64.b64encode(bytes(range(1, 33))).decode()


def app_client(client: TestClient, db: Session, name: str) -> tuple[TestClient, Member]:
    m = member_at(db, "egg", name, paid=True)
    client.cookies.clear()
    client.cookies.set("pc_session", members.new_session(db, m, 7, DESKTOP_TOKEN_PREFIX, kind="desktop"))
    return client, m


def test_the_app_registers_its_own_key_and_the_server_never_sees_a_private_one(client: TestClient, db: Session, fake: FakeNode) -> None:
    server = add_server(db, fake)
    open_vpn(db)
    c, _ = app_client(client, db, "ownkey")
    r = c.post(DEVICES, json={"name": "DESKTOP DE", "server_id": server.id, "public_key": PUB})
    assert r.status_code == 201, r.text
    body = r.json()
    assert "config" not in body and "PrivateKey" not in r.text
    peer = body["peer"]
    assert peer["server_public_key"] == server.public_key and peer["endpoint_host"] == server.endpoint_host
    assert peer["addresses"][0] == f"{body['device']['ip']}/32" and peer["keepalive"] == 25 and "0.0.0.0/0" in peer["allowed_ips"]
    assert PUB in fake.peers
    assert c.get(f"{DEVICES}/{body['device']['id']}/peer").json() == peer
    assert c.post(DEVICES, json={"name": "again", "server_id": server.id, "public_key": PUB}).status_code == 409


def test_a_bad_public_key_is_refused(client: TestClient, db: Session, fake: FakeNode) -> None:
    server = add_server(db, fake)
    open_vpn(db)
    c, _ = app_client(client, db, "badkey")
    for bad in ["A" * 44, base64.b64encode(bytes(32)).decode(), "!" * 44]:
        assert c.post(DEVICES, json={"name": "x", "server_id": server.id, "public_key": bad}).status_code == 400, bad
    assert c.post(DEVICES, json={"name": "x", "server_id": server.id, "public_key": "short"}).status_code == 422


def test_nobody_reads_anothers_peer_details(client: TestClient, db: Session, fake: FakeNode) -> None:
    server = add_server(db, fake)
    open_vpn(db)
    c, _ = app_client(client, db, "peerowner")
    device = c.post(DEVICES, json={"name": "mine", "server_id": server.id, "public_key": PUB}).json()["device"]["id"]
    other = signed_in(client, db, member_at(db, "egg", "peerother", paid=True))
    assert other.get(f"{DEVICES}/{device}/peer").status_code == 404


def test_app_sessions_are_recorded_apart_and_can_be_revoked_without_touching_browsers(client: TestClient, db: Session) -> None:
    m = member_at(db, "egg", "twosessions", paid=False)
    browser = members.new_session(db, m, 7)
    app = members.new_session(db, m, 7, DESKTOP_TOKEN_PREFIX, kind="desktop")
    client.cookies.set("pc_session", browser)
    assert client.get("/api/members/desktop/sessions").json() == {"count": 1}
    assert client.post("/api/members/desktop/sessions/revoke").json() == {"removed": 1}
    assert client.get("/api/members/me").status_code == 200, "the browser stays signed in"
    client.cookies.set("pc_session", app)
    assert client.get("/api/members/vpn").status_code == 401, "the app doesn't"


def test_the_app_can_ask_its_own_address_without_signing_in(client: TestClient) -> None:
    r = client.get("/api/members/vpn/ip")
    assert r.status_code == 200 and r.json()["ip"] and r.headers["cache-control"] == "no-store"


def test_the_token_swap_marks_the_session(client: TestClient, db: Session) -> None:
    code, verifier = code_for(client, db, "marked")
    client.post(TOKEN, json={"code": code, "verifier": verifier})
    assert db.query(MemberSession).filter(MemberSession.kind == "desktop").count() >= 1
