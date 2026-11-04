"""Named testers may use the VPN while it's closed, until a date. Nobody else gains anything, and the public stays closed out."""

from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.vpn import VpnAccount
from app.vpn import service as vpn
from tests.test_vpn import FakeNode, add_server, member_at, signed_in
from tests.test_production import prod  # noqa: F401
from tests.test_vpn_browser import fake  # noqa: F401

ADD = "/api/vpn/admin/testers"


def me(c: TestClient) -> dict:
    return c.get("/api/members/vpn").json()


def test_a_named_tester_gets_in_while_it_stays_closed_to_everyone_else(client: TestClient, db: Session, fake: FakeNode) -> None:  # noqa: F811
    server = add_server(db, fake, "Helsinki 1")
    db.commit()
    tester, other = member_at(db, "egg", "tester1", paid=False), member_at(db, "egg", "other1", paid=False)
    assert client.post(ADD, json={"username": "TESTER1", "days": 14}).status_code == 200  # any capitalisation
    assert vpn.settings(db).enabled is False
    db.commit()
    assert client.get("/api/members/vpn/public").json()["enabled"] is False
    c = signed_in(client, db, tester)
    out = me(c)
    assert out["enabled"] is True and out["eligible"] is True and out["plan"]["kind"] == "tester" and out["plan"]["days_left"] == 14
    made = c.post("/api/members/vpn/devices", json={"name": "Windows", "server_id": server.id})
    assert made.status_code == 201 and "[Interface]" in made.json()["config"]
    client.cookies.clear()
    c2 = signed_in(client, db, other)
    out = me(c2)
    assert out["enabled"] is False and out["eligible"] is False
    assert c2.post("/api/members/vpn/devices", json={"name": "x", "server_id": server.id}).status_code == 403


def test_it_ends_on_time_and_can_be_ended_early(client: TestClient, db: Session) -> None:
    m = member_at(db, "egg", "ending", paid=False)
    client.post(ADD, json={"username": "ending", "days": 5})
    c = signed_in(client, db, m)
    assert me(c)["eligible"] is True
    acct = db.query(VpnAccount).filter_by(member_id=m.id).one()
    db.refresh(acct)
    acct.tester_until = utcnow() - timedelta(minutes=1)
    db.commit()
    assert me(c)["eligible"] is False
    client.cookies.clear()
    client.post(ADD, json={"username": "ending", "days": 5})
    c = signed_in(client, db, m)
    assert me(c)["eligible"] is True
    assert client.post(ADD, json={"username": "ending", "days": 0}).json()["tester_until"] is None
    assert me(c)["eligible"] is False


def test_an_admin_block_beats_being_a_tester(client: TestClient, db: Session) -> None:
    m = member_at(db, "egg", "blockedt", paid=False)
    client.post(ADD, json={"username": "blockedt", "days": 5})
    db.expire_all()
    db.query(VpnAccount).filter_by(member_id=m.id).one().status = "blocked"
    db.commit()
    assert me(signed_in(client, db, m))["eligible"] is False


def test_limits(client: TestClient, db: Session) -> None:
    assert client.post(ADD, json={"username": "nobody", "days": 5}).status_code == 404
    member_at(db, "egg", "toolong", paid=False)
    assert client.post(ADD, json={"username": "toolong", "days": 91}).status_code == 422
    for i in range(vpn.MAX_TESTERS):
        member_at(db, "egg", f"t{i}", paid=False)
        assert client.post(ADD, json={"username": f"t{i}", "days": 1}).status_code == 200
    assert client.post(ADD, json={"username": "toolong", "days": 1}).status_code == 409
    assert client.post(ADD, json={"username": "t0", "days": 2}).status_code == 200  # extending an existing one is fine


def test_only_admin_can_add_a_tester(prod: TestClient) -> None:  # noqa: F811
    assert prod.post(ADD, json={"username": "x", "days": 1}).status_code == 401
