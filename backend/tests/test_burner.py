"""Kiwi Passport burner wallet API. TEST FIXTURES only: made-up ciphertext (the real encryption happens in the browser;
this API never sees a recovery phrase or a password)."""

import base64

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.members import service as members
from app.models.members import MemberAuditLog, MemberBurnerWallet
from tests.test_trading_os import kiwi

ADDR = "0x" + "ab" * 20


def b64(n: int) -> str:
    return base64.b64encode(bytes(range(n))).decode()


def vault(**over: object) -> dict[str, object]:
    return {"v": 1, "kdf": "PBKDF2-SHA256", "cipher": "AES-GCM", "iterations": 310_000, "salt": b64(16), "iv": b64(12), "ct": b64(120), **over}


def signed_in(client: TestClient, db: Session, tg: str = "6161") -> int:
    m = kiwi(db, 2, tg)
    client.cookies.set("pc_session", members.new_session(db, m, 7))
    return m.id


def test_create_reveal_data_and_remove(client: TestClient, db: Session) -> None:
    mid = signed_in(client, db)
    assert client.get("/api/members/me/burner").json() == {"burner": None}
    r = client.post("/api/members/me/burner", json={"address": ADDR, "vault": vault()})
    assert r.status_code == 200, r.text
    got = client.get("/api/members/me/burner")
    assert got.headers["cache-control"] == "no-store"
    assert got.json()["burner"]["address"] == ADDR and got.json()["burner"]["vault"]["ct"] == b64(120)  # ciphertext back, for the browser
    assert client.post("/api/members/me/burner", json={"address": ADDR, "vault": vault()}).status_code == 409  # one per Passport
    assert client.request("DELETE", "/api/members/me/burner", json={"address": "0x" + "cd" * 20}).status_code == 404  # wrong address
    assert client.request("DELETE", "/api/members/me/burner", json={"address": ADDR}).json() == {"burner": None}
    actions = [a.action for a in db.scalars(select(MemberAuditLog).where(MemberAuditLog.member_id == mid))]
    assert "burner_created" in actions and "burner_removed" in actions


def test_only_well_formed_ciphertext_is_stored_never_a_phrase(client: TestClient, db: Session) -> None:
    signed_in(client, db)
    phrase = "abandon " * 11 + "about"
    for bad in (vault(ct=phrase), vault(iterations=1000), vault(kdf="none"), vault(salt=b64(4)), {**vault(), "phrase": phrase}):
        assert client.post("/api/members/me/burner", json={"address": ADDR, "vault": bad}).status_code == 422, bad
    assert client.post("/api/members/me/burner", json={"address": "0x123", "vault": vault()}).status_code == 422
    assert db.scalar(select(MemberBurnerWallet)) is None


def test_needs_sign_in_and_is_per_member(client: TestClient, db: Session) -> None:
    client.cookies.clear()
    assert client.get("/api/members/me/burner").status_code == 401
    signed_in(client, db, "6262")
    client.post("/api/members/me/burner", json={"address": ADDR, "vault": vault()})
    signed_in(client, db, "6363")  # someone else
    assert client.get("/api/members/me/burner").json() == {"burner": None}
