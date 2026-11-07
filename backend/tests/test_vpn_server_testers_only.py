"""A testers-only server (a new country being tried out): only the owner and testers see it or can use it."""
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.vpn import VpnServer
from app.vpn import service as vpn
from tests.test_vpn import FakeNode, add_server, member_at, open_vpn, signed_in, FP, TOKEN, URL
from tests.test_vpn_browser import fake  # noqa: F401  (a fake agent that also runs a browser proxy)

ADMIN = "/api/vpn/admin/servers"


def names(out: dict) -> list[str]:
    return sorted(s["name"] for s in out["servers"])


def test_members_never_see_or_use_a_testers_only_server(client: TestClient, db: Session, fake: FakeNode) -> None:  # noqa: F811
    open_vpn(db)
    live = add_server(db, fake, "Helsinki 1")
    new = add_server(db, fake, "US East")
    vpn.update_server(db, new.id, {"testers_only": True})
    db.commit()
    assert [s["name"] for s in client.get("/api/members/vpn/stats").json()["servers"]] == ["Helsinki 1"]  # public: not even counted
    member = member_at(db, "egg", "member1")
    c = signed_in(client, db, member)
    out = c.get("/api/members/vpn").json()
    assert names(out) == ["Helsinki 1"]
    assert c.post("/api/members/vpn/devices", json={"name": "x", "server_id": new.id}).status_code == 404
    assert c.post("/api/members/vpn/browser", json={"server_id": new.id}).status_code == 404
    assert c.post("/api/members/vpn/devices", json={"name": "ok", "server_id": live.id}).status_code == 201
    # nothing about the hidden server is recommended to them either
    assert all(s["id"] != new.id for s in out["servers"])


def test_testers_and_the_owner_can_use_it(client: TestClient, db: Session, fake: FakeNode) -> None:  # noqa: F811
    open_vpn(db)
    add_server(db, fake, "Helsinki 1")
    new = add_server(db, fake, "US East")
    vpn.update_server(db, new.id, {"testers_only": True})
    tester = member_at(db, "egg", "tester1", paid=False)
    assert client.post("/api/vpn/admin/testers", json={"username": "tester1", "days": 14}).status_code == 200
    db.commit()
    c = signed_in(client, db, tester)
    assert names(c.get("/api/members/vpn").json()) == ["Helsinki 1", "US East"]
    assert c.post("/api/members/vpn/devices", json={"name": "Windows", "server_id": new.id}).status_code == 201
    assert c.post("/api/members/vpn/browser", json={"server_id": new.id}).status_code in (200, 201)
    owner = vpn.owner_account(db)
    assert vpn.sees(owner, db.get(VpnServer, new.id))


def test_admin_adds_new_servers_as_testers_only_and_can_open_them_to_everyone(client: TestClient, db: Session, fake: FakeNode) -> None:  # noqa: F811
    open_vpn(db)
    r = client.post(ADMIN, json={"name": "US East", "region": "US East", "country_code": "us", "agent_url": URL, "agent_token": TOKEN, "cert_sha256": FP})
    assert r.status_code in (200, 201), r.text
    sid = db.query(VpnServer).filter_by(name="US East").one().id
    row = next(s for s in client.get("/api/vpn/admin/overview").json()["servers"] if s["id"] == sid)
    assert row["testers_only"] is True
    assert client.get("/api/members/vpn/stats").json()["servers"] == []
    assert client.patch(f"{ADMIN}/{sid}", json={"testers_only": False}).status_code in (200, 204)
    db.expire_all()
    assert [s["name"] for s in client.get("/api/members/vpn/stats").json()["servers"]] == ["US East"]


def test_existing_servers_stay_visible(db: Session, fake: FakeNode) -> None:  # noqa: F811
    s = add_server(db, fake, "Helsinki 1")  # added the way every existing server was
    assert s.testers_only is False
