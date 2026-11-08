"""AUDIT (2026-11-08, read-only diagnostics): what Price Chat's Kiwi VPN device limiter really counts, and how the
one-device-per-server model of the Windows/Android apps meets the per-account device cap. Not part of the normal suite."""

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.security.rate_limit import LIMITS
from app.vpn import keys
from tests.test_vpn import add_server, member_at, open_vpn, signed_in
from tests.test_vpn_browser import ProxyNode, fake  # noqa: F401  (fixture)


def _pub() -> str:
    return keys.generate_keypair()[1]


def test_audit_deletes_do_not_spend_the_device_limit(client: TestClient, db: Session, fake: ProxyNode) -> None:  # noqa: F811
    LIMITS["vpn_device"].clear()
    server = add_server(db, fake)
    open_vpn(db, payments_enabled=False, trial_enabled=False, max_devices=3)
    c = signed_in(client, db, member_at(db, "warrior", "deleter", paid=False))
    ids = [c.post("/api/members/vpn/devices", json={"name": f"d{i}", "server_id": server.id, "public_key": _pub()}).json()["device"]["id"] for i in range(3)]
    for i in ids:  # three deletes right after three creates
        assert c.delete(f"/api/members/vpn/devices/{i}").status_code == 204
    # two creates left in the burst of 5: deletes took nothing
    assert c.post("/api/members/vpn/devices", json={"name": "e1", "server_id": server.id, "public_key": _pub()}).status_code == 201
    assert c.post("/api/members/vpn/devices", json={"name": "e2", "server_id": server.id, "public_key": _pub()}).status_code == 201
    assert c.post("/api/members/vpn/devices", json={"name": "e3", "server_id": server.id, "public_key": _pub()}).status_code == 429


def test_audit_browser_logins_and_devices_share_one_bucket(client: TestClient, db: Session, fake: ProxyNode) -> None:  # noqa: F811
    LIMITS["vpn_device"].clear()
    server = add_server(db, fake)
    open_vpn(db, payments_enabled=False, trial_enabled=False, max_devices=3)
    c = signed_in(client, db, member_at(db, "warrior", "sharer", paid=False))
    for _ in range(4):  # the extension re-logs in on every server change and every failed connection check
        assert c.post("/api/members/vpn/browser", json={"server_id": server.id}).status_code == 201
    assert c.post("/api/members/vpn/devices", json={"name": "pc", "server_id": server.id, "public_key": _pub()}).status_code == 201
    assert c.post("/api/members/vpn/devices", json={"name": "phone", "server_id": server.id, "public_key": _pub()}).status_code == 429


def test_audit_one_device_per_server_fills_the_cap(client: TestClient, db: Session, fake: ProxyNode) -> None:  # noqa: F811
    LIMITS["vpn_device"].clear()
    servers = [add_server(db, fake, name=n) for n in ("Helsinki", "Germany", "Ashburn")]
    open_vpn(db, payments_enabled=False, trial_enabled=False, max_devices=3)
    c = signed_in(client, db, member_at(db, "warrior", "roamer", paid=False))
    for s in servers:  # one Windows PC visiting each country once, as flow.rs does (a device per server)
        assert c.post("/api/members/vpn/devices", json={"name": f"PC ({s.id})", "server_id": s.id, "public_key": _pub()}).status_code == 201
    # the same person's phone can no longer connect anywhere
    assert c.post("/api/members/vpn/devices", json={"name": "Pixel", "server_id": servers[0].id, "public_key": _pub()}).status_code == 409
