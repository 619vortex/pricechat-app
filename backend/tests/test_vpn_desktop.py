"""Desktop sign-in: a code alone is useless, works once, expires, and the app's session works on the existing VPN routes. No password ever reaches the app."""

import base64
import hashlib
import secrets
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.desktop import MemberDesktopCode
from tests.test_vpn import member_at, signed_in

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
