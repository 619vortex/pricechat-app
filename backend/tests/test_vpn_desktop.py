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
from app.models.vpn import VpnAccount
from app.vpn import service as vpn
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
PUB2 = base64.b64encode(bytes(range(2, 34))).decode()


def test_public_beta_a_new_customer_needs_no_admin_and_no_tester_listing(client: TestClient, db: Session, fake: FakeNode) -> None:
    server = add_server(db, fake)
    open_vpn(db, payments_enabled=False, trial_enabled=True, max_devices=1)
    m = member_at(db, "egg", "newcustomer", paid=False)
    client.cookies.clear()
    client.cookies.set("pc_session", members.new_session(db, m, 7, DESKTOP_TOKEN_PREFIX, kind="desktop"))
    first = client.get("/api/members/vpn").json()
    assert first["enabled"] is True and first["eligible"] is False and first["trial"] == {"available": True, "days": 30} and [s["id"] for s in first["servers"]] == [server.id]
    # the same call the app makes by itself, with the app's limited session
    started = client.post("/api/members/vpn/trial")
    assert started.status_code == 200, started.text
    body = started.json()
    assert body["eligible"] is True and body["reason"] is None and body["plan"]["kind"] == "trial" and body["plan"]["days_left"] in (29, 30) and body["trial"]["available"] is False
    assert db.query(VpnAccount).filter_by(member_id=m.id).one().tester_until is None
    assert client.post("/api/members/vpn/trial").status_code == 409, "one trial per account"
    assert client.post(DEVICES, json={"name": "PC (1)", "server_id": server.id, "public_key": PUB}).status_code == 201
    assert client.post(DEVICES, json={"name": "PC (2)", "server_id": server.id, "public_key": PUB2}).status_code == 409, "the device limit still holds"


def test_closed_shows_unavailable_and_never_a_running_trial_and_the_trial_comes_back(client: TestClient, db: Session, fake: FakeNode) -> None:
    server = add_server(db, fake)
    open_vpn(db, payments_enabled=False, trial_enabled=True)
    m = member_at(db, "egg", "closedcustomer", paid=False)
    c = signed_in(client, db, m)
    assert c.post("/api/members/vpn/trial").json()["plan"]["kind"] == "trial"
    vpn.update_settings(db, {"enabled": False})
    closed = c.get("/api/members/vpn").json()
    assert closed["enabled"] is False and closed["eligible"] is False and closed["reason"] == vpn.CLOSED
    assert closed["plan"] == {"kind": "closed", "until": None, "days_left": None} and closed["trial"]["available"] is False
    assert c.post(DEVICES, json={"name": "PC", "server_id": server.id, "public_key": PUB}).status_code == 403
    vpn.update_settings(db, {"enabled": True})
    back = c.get("/api/members/vpn").json()
    assert back["eligible"] is True and back["plan"]["kind"] == "trial" and back["plan"]["days_left"] in (29, 30)


def test_closed_offers_no_trial_to_a_new_customer(client: TestClient, db: Session, fake: FakeNode) -> None:
    add_server(db, fake)
    open_vpn(db, payments_enabled=False, trial_enabled=True)
    vpn.update_settings(db, {"enabled": False})
    c = signed_in(client, db, member_at(db, "egg", "tooearly", paid=False))
    out = c.get("/api/members/vpn").json()
    assert out["trial"]["available"] is False and out["plan"]["kind"] == "closed"
    assert c.post("/api/members/vpn/trial").status_code == 403


def test_a_tester_still_gets_in_while_closed(client: TestClient, db: Session, fake: FakeNode) -> None:
    add_server(db, fake)
    m = member_at(db, "egg", "tester1", paid=False)
    vpn.set_tester(db, "tester1", 7)
    out = signed_in(client, db, m).get("/api/members/vpn").json()
    assert out["enabled"] is True and out["eligible"] is True and out["plan"]["kind"] == "tester"


def test_a_customer_can_not_reach_the_admin_controls(client: TestClient, db: Session) -> None:
    m = member_at(db, "egg", "notadmin", paid=False)
    client.cookies.clear()
    client.cookies.set("pc_session", members.new_session(db, m, 7, DESKTOP_TOKEN_PREFIX, kind="desktop"))
    refused = client.put("/api/vpn/admin/settings", json={"enabled": True})
    assert refused.status_code == 403 and refused.json()["code"] == "desktop_scope"
    assert client.post("/api/vpn/admin/testers", json={"username": "notadmin", "days": 7}).status_code == 403


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
    assert r.json()["forwarded_hops"] == 0, "a count only: no addresses beyond the caller's own"
    assert client.get("/api/members/vpn/ip", headers={"x-forwarded-for": "1.2.3.4, 5.6.7.8"}).json()["forwarded_hops"] == 2
    assert client.get("/api/members/vpn/ip", headers={"x-vercel-forwarded-for": "1.2.3.4, 5.6.7.8"}).json()["vercel_hops"] == 2


def test_the_token_swap_marks_the_session(client: TestClient, db: Session) -> None:
    code, verifier = code_for(client, db, "marked")
    client.post(TOKEN, json={"code": code, "verifier": verifier})
    assert db.query(MemberSession).filter(MemberSession.kind == "desktop").count() >= 1


def test_refresh_replaces_the_app_session_and_the_old_token_dies(client: TestClient, db: Session) -> None:
    m = member_at(db, "egg", "refresher", paid=False)
    old = members.new_session(db, m, 7, DESKTOP_TOKEN_PREFIX, kind="desktop")
    client.cookies.set("pc_session", old)
    new = client.post("/api/members/desktop/refresh").json()["token"]
    assert new != old and new.startswith("kd.")
    assert client.get("/api/members/me").status_code == 401, "the old token is gone"
    client.cookies.set("pc_session", new)
    assert client.get("/api/members/me").status_code == 200


def test_a_browser_session_cant_be_turned_into_an_app_session(client: TestClient, db: Session) -> None:
    client.cookies.set("pc_session", members.new_session(db, member_at(db, "egg", "browseronly", paid=False), 7))
    assert client.post("/api/members/desktop/refresh").status_code == 403
    assert client.get("/api/members/me").status_code == 200, "and it keeps working"
