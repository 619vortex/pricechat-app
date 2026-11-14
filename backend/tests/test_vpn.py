"""Kiwi VPN: the access gate, the device limit, IP allocation (unique under a racing insert), the config, the private key never
stored, own devices only, owner-only admin routes, the sync (revoke / re-add / remove unknown), certificate pinning and /vpn
only in private chats. No network except a local TLS server for the pinning test; the node agent is faked."""

import asyncio
import base64
import datetime as dt
import hashlib
import json
import ssl
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.members import service as members
from app.members import xp as ledger
from app.members.catalog import ensure_catalog
from app.models.members import Entitlement, Member, MemberEntitlement
from app.models.vpn import VpnDevice, VpnServer
from app.security.rate_limit import LIMITS
from app.tradingos import bot
from app.vpn import keys, node, service as vpn
from tests.test_production import prod  # noqa: F401  (fixture)

TOKEN = "t" * 40
FP = "ab" * 32
URL = "https://203.0.113.10:8443"


class FakeNode:
    """TEST FIXTURE: the node agent's HTTP API, in memory (checks the bearer token like the real one)."""

    def __init__(self, address: str = "10.66.0.1/16") -> None:
        self.server_key = keys.generate_keypair()[1]
        self.address = address
        self.peers: dict[str, dict[str, Any]] = {}
        self.fail = False
        self.adblock: bool | None = None  # None: an agent that doesn't report it
        self.address6: str | None = None
        self.calls: list[tuple[str, str]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.fail:
            raise httpx.ConnectError("down")
        if request.headers.get("authorization") != f"Bearer {TOKEN}":
            return httpx.Response(401)
        path = request.url.path
        self.calls.append((request.method, path))
        if path == "/v1/status":
            body = {"public_key": self.server_key, "listen_port": 51820, "endpoint_host": "203.0.113.10", "address": self.address,
                    "dns": ["10.66.0.1", "1.1.1.1"] if self.adblock else ["1.1.1.1", "1.0.0.1"],
                    "peers": [{"public_key": k, **v} for k, v in self.peers.items()]}
            if self.adblock is not None:
                body["adblock"] = self.adblock
            if self.address6 is not None:
                body["address6"] = self.address6
            return httpx.Response(200, json=body)
        key = path.removeprefix("/v1/peers/").replace("-", "+").replace("_", "/")
        if request.method == "PUT":
            self.peers[key] = {"allowed_ip": json.loads(request.content)["allowed_ip"], "rx_bytes": 0, "tx_bytes": 0, "latest_handshake": 0}
        elif request.method == "DELETE":
            self.peers.pop(key, None)
        return httpx.Response(200, json={"ok": True})


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeNode:
    f = FakeNode()
    monkeypatch.setattr(node, "connect", lambda url, token, fp: node.NodeClient(url, token, fp, transport=httpx.MockTransport(f.handler)))
    return f


def grant(db: Session, m: Member, **over: Any) -> MemberEntitlement:
    ent = db.scalar(select(Entitlement).where(Entitlement.key == vpn.ENTITLEMENT))
    row = MemberEntitlement(member_id=m.id, entitlement_id=ent.id, granted_by="test", **over)
    db.add(row)
    db.commit()
    return row


def ungrant(db: Session, m: Member) -> None:
    db.delete(db.scalar(select(MemberEntitlement).where(MemberEntitlement.member_id == m.id)))
    db.commit()


def member_at(db: Session, rank_key: str, name: str = "kiwi", paid: bool = True) -> Member:
    ensure_catalog(db)
    m = members.create_member(db)
    m.username, m.username_key = name, name.lower()
    m.created_at = utcnow() - dt.timedelta(days=30)  # past the trial's minimum account age (app/vpn/trial_guard.py)
    db.commit()
    need = next(r.min_xp for r in ledger.ranks(db) if r.key == rank_key)
    if need:
        ledger.adjust(db, m, need, actor="test", reason="rank", ref=f"r-{name}")
    db.refresh(m)
    if paid:
        grant(db, m)
    return m


def signed_in(client: TestClient, db: Session, m: Member) -> TestClient:
    client.cookies.set("pc_session", members.new_session(db, m, 7))
    return client


def open_vpn(db: Session, **over: Any) -> None:
    vpn.update_settings(db, {"enabled": True, "payments_enabled": True, "trial_enabled": False, **over})  # paid, with no trial, unless a test says otherwise


def add_server(db: Session, fake: FakeNode, name: str = "Frankfurt") -> VpnServer:
    return vpn.add_server(db, {"name": name, "region": "Europe", "country_code": "de", "agent_url": URL, "agent_token": TOKEN,
                               "cert_sha256": FP.upper(), "capacity": 100})


# --- keys & config --------------------------------------------------------------------------------------------------


def test_keys_and_config_contents() -> None:
    priv, pub = keys.generate_keypair()
    assert keys.is_key(priv) and keys.is_key(pub) and keys.public_from_private(priv) == pub
    assert keys.url_key("a+b/c=") == "a-b_c="
    server_pub = keys.generate_keypair()[1]
    cfg = keys.render_config(priv, "10.66.0.2", ["1.1.1.1"], server_pub, "203.0.113.10", 51820)
    assert cfg.startswith("[Interface]\n") and f"PrivateKey = {priv}" in cfg and "Address = 10.66.0.2/32" in cfg
    assert "DNS = 1.1.1.1" in cfg and f"[Peer]\nPublicKey = {server_pub}" in cfg and "Endpoint = 203.0.113.10:51820" in cfg
    assert "AllowedIPs = 0.0.0.0/0, ::/0" in cfg and "PersistentKeepalive = 25" in cfg
    assert "Endpoint = [2001:db8::1]:51820" in keys.render_config(priv, "10.66.0.2", [], server_pub, "2001:db8::1", 51820)
    assert "Address = 10.66.1.2/32, fd66:66::102/128\n" in keys.render_config(priv, "10.66.1.2", [], server_pub, "h", 1, "fd66:66::102")
    assert keys.ipv6_for("10.66.0.1/16", "10.66.1.2", "fd66:66::1/64") == "fd66:66::102"  # offset 258 = 0x102
    assert keys.ipv6_for("10.66.0.1/16", "10.66.0.2", "") is None and keys.ipv6_for("10.66.0.1/16", "10.66.0.2", "junk") is None
    assert keys.ipv6_for("10.66.0.1/16", "10.66.1.2", "fd66:66::1/120") is None  # doesn't fit the v6 pool
    fn = keys.filename("Frankfurt 1", "My iPhone 15 Pro Max!!")
    assert fn.startswith("kiwi-vpn-frankfurt-my-iphone") and fn.endswith(".conf") and len(fn) - 5 <= 32


# --- member API -----------------------------------------------------------------------------------------------------


def test_closed_and_paid_access_only(client: TestClient, db: Session, fake: FakeNode) -> None:
    server = add_server(db, fake)
    unpaid, paid = member_at(db, "knight", "unpaid", paid=False), member_at(db, "egg", "paid")  # below Royal Kiwi, rank decides nothing: high rank locked, lowest in
    r = signed_in(client, db, paid).get("/api/members/vpn").json()
    assert r["enabled"] is False and r["eligible"] is False and r["max_devices"] == 3  # closed by default
    assert client.post("/api/members/vpn/devices", json={"name": "phone", "server_id": server.id}).status_code == 403
    open_vpn(db)
    r = client.get("/api/members/vpn").json()
    assert r["eligible"] and r["reason"] is None and "required_rank" not in r
    assert r["servers"] == [{"id": server.id, "name": "Frankfurt", "region": "Europe", "country_code": "DE", "online": True, "load": 0.0,
                            "live_load": 0.0, "adblock": False, "recommended": True, "browser": False, "proxy_host": None}]
    assert r["usage"]["used_bytes"] == 0 and r["usage"]["cap_bytes"] == 0 and r["usage"]["paused_until"] is None
    r = signed_in(client, db, unpaid).get("/api/members/vpn").json()
    assert not r["eligible"] and "$9.99 for 30 days" in r["reason"] and "ank" not in r["reason"]
    assert client.post("/api/members/vpn/devices", json={"name": "phone", "server_id": server.id}).status_code == 403
    assert db.scalar(select(Entitlement.required_rank).where(Entitlement.key == "kiwi_vpn")) is None  # never unlocked by rank
    grant(db, unpaid)  # a grant unlocks it (by hand now; a subscription later)
    assert client.get("/api/members/vpn").json()["eligible"] is True
    assert client.get("/api/members/vpn").status_code == 200
    client.cookies.clear()
    assert client.get("/api/members/vpn").status_code == 401


def test_create_device_returns_config_once_and_stores_public_key_only(client: TestClient, db: Session, fake: FakeNode) -> None:
    server = add_server(db, fake)
    open_vpn(db)
    c = signed_in(client, db, member_at(db, "warrior"))
    r = c.post("/api/members/vpn/devices", json={"name": "My phone", "server_id": server.id})
    assert r.status_code == 201
    body = r.json()
    cfg, dev = body["config"], body["device"]
    assert body["filename"] == "kiwi-vpn-frankfurt-my-phone.conf"
    assert dev["ip"] == "10.66.0.2" and dev["name"] == "My phone" and dev["last_handshake"] is None and dev["rx_bytes"] == 0
    priv = next(line.split(" = ", 1)[1] for line in cfg.splitlines() if line.startswith("PrivateKey"))
    assert f"PublicKey = {fake.server_key}" in cfg and "DNS = 1.1.1.1\n" in cfg and "Endpoint = 203.0.113.10:51820" in cfg
    row = db.get(VpnDevice, dev["id"])
    pub = keys.public_from_private(priv)
    assert row is not None and row.public_key == pub and fake.peers[pub]["allowed_ip"] == "10.66.0.2/32"
    # the private key is in no column of any VPN row (and not in the raw database file either)
    db.expire_all()
    for table in ("vpn_devices", "vpn_servers", "vpn_settings"):
        for rec in db.execute(text(f"SELECT * FROM {table}")):
            assert priv not in " ".join(map(str, rec))
    assert "config" not in c.get("/api/members/vpn").text and priv not in c.get("/api/members/vpn").text


def test_device_limit_and_offline_server(client: TestClient, db: Session, fake: FakeNode) -> None:
    server = add_server(db, fake)
    open_vpn(db, max_devices=2)
    c = signed_in(client, db, member_at(db, "warrior"))
    for i in range(2):
        assert c.post("/api/members/vpn/devices", json={"name": f"d{i}", "server_id": server.id}).status_code == 201
    assert c.post("/api/members/vpn/devices", json={"name": "d3", "server_id": server.id}).status_code == 409
    vpn.update_settings(db, {"max_devices": 3})
    fake.fail = True  # the node call fails: nothing kept, 503
    assert c.post("/api/members/vpn/devices", json={"name": "d3", "server_id": server.id}).status_code == 503
    assert len(db.scalars(select(VpnDevice)).all()) == 2
    fake.fail = False
    server.online = False
    db.commit()
    assert c.post("/api/members/vpn/devices", json={"name": "d3", "server_id": server.id}).status_code == 503
    # refusals that need no node (an unknown server, over the limit) are checked before the rate limit and cost nothing (audit M1)
    tries = int(LIMITS["vpn_device"].burst) * 2
    assert [c.post("/api/members/vpn/devices", json={"name": "d3", "server_id": 999}).status_code for _ in range(tries)] == [404] * tries
    assert c.post("/api/members/vpn/devices", json={"name": "", "server_id": server.id}).status_code == 422


def test_ip_allocation_is_unique_and_skips_reserved(db: Session, fake: FakeNode, monkeypatch: pytest.MonkeyPatch) -> None:
    fake.address = "10.9.0.1/24"
    server = add_server(db, fake)
    open_vpn(db, max_devices=10)
    m = member_at(db, "warrior")
    ips = [vpn.create_device(db, m, f"d{i}", server.id)[0].ip for i in range(3)]
    assert ips == ["10.9.0.2", "10.9.0.3", "10.9.0.4"]
    # a racing insert: our view of used IPs is stale, the unique (server_id, ip) constraint makes us take the next one
    monkeypatch.setattr(vpn, "_used_ips", lambda db, sid: set())
    dev = vpn.create_device(db, m, "racer", server.id)[0]
    assert dev.ip == "10.9.0.5"
    monkeypatch.undo()
    all_ips = [d.ip for d in db.scalars(select(VpnDevice))]
    assert len(all_ips) == len(set(all_ips)) == 4
    # never .0, .1 (the server) or .255, and a full pool says so
    tiny = VpnServer(name="tiny", agent_url=URL, agent_token_enc=vpn.enc(TOKEN), cert_sha256=FP, address="10.8.0.1/30", dns=[])
    db.add(tiny)
    db.commit()
    assert vpn.allocate_ip(db, tiny) == "10.8.0.2"
    db.add(VpnDevice(account_id=vpn.account_of(db, m).id, server_id=tiny.id, name="x", public_key=keys.generate_keypair()[1], ip="10.8.0.2"))
    db.commit()
    with pytest.raises(vpn.VpnError) as e:
        vpn.allocate_ip(db, tiny)
    assert e.value.status == 503
    # in a /16 every x.y.z.0 / .1 / .255 is skipped too
    big = VpnServer(name="big", agent_url=URL, agent_token_enc=vpn.enc(TOKEN), cert_sha256=FP, address="10.7.0.1/16", dns=[])
    db.add(big)
    db.commit()
    taken = {f"10.7.0.{i}" for i in range(2, 255)}
    assert vpn.allocate_ip(db, big, taken) == "10.7.1.2"


def test_delete_only_own_devices(client: TestClient, db: Session, fake: FakeNode) -> None:
    server = add_server(db, fake)
    open_vpn(db)
    a, b = member_at(db, "warrior", "alice"), member_at(db, "warrior", "bob")
    dev_b = vpn.create_device(db, b, "bob phone", server.id)[0]
    dev_b_id, dev_b_key = dev_b.id, dev_b.public_key
    c = signed_in(client, db, a)
    assert c.delete(f"/api/members/vpn/devices/{dev_b_id}").status_code == 404
    assert db.get(VpnDevice, dev_b_id) is not None and dev_b_key in fake.peers
    dev_a = c.post("/api/members/vpn/devices", json={"name": "a", "server_id": server.id}).json()["device"]
    fake.fail = True  # the node DELETE is best effort: the row still goes (sync cleans the peer up later)
    r = c.delete(f"/api/members/vpn/devices/{dev_a['id']}")
    assert r.status_code == 204 and r.content == b""
    db.expire_all()
    assert db.get(VpnDevice, dev_a["id"]) is None and [d["id"] for d in c.get("/api/members/vpn").json()["devices"]] == []


# --- admin ----------------------------------------------------------------------------------------------------------


def test_admin_routes(client: TestClient, db: Session, fake: FakeNode) -> None:
    r = client.post("/api/vpn/admin/servers", json={"name": "Frankfurt", "region": "Europe", "country_code": "DE", "agent_url": URL,
                                                   "agent_token": TOKEN, "cert_sha256": ":".join(["AB"] * 32), "capacity": 50})
    assert r.status_code == 200, r.text
    sid = r.json()["id"]
    row = db.get(VpnServer, sid)
    assert row.cert_sha256 == FP and TOKEN not in row.agent_token_enc and vpn.agent_token(row) == TOKEN
    assert row.public_key == fake.server_key and row.address == "10.66.0.1/16"
    o = client.get("/api/vpn/admin/overview").json()
    assert o["settings"] == {"enabled": False, "payments_enabled": False, "trial_enabled": True, "max_devices": 3, "monthly_cap_gb": 0,
                             "trial_one_per_identity": True, "trial_min_account_age_hours": 24, "trial_min_xp": 0, "trial_per_ip_day": 3,
                             "trial_per_network_day": 10, "sync_enforce_limits": False, "count_pc_once": False} and "ranks" not in o
    assert o["servers"][0]["online"] is True and o["servers"][0]["capacity"] == 50 and "agent_token" not in json.dumps(o)
    assert o["totals"] == {"devices": 0, "members": 0, "active_24h": 0}
    assert client.put("/api/vpn/admin/settings", json={"max_devices": 5, "enabled": True}).json() == \
        {**o["settings"], "enabled": True, "max_devices": 5}
    assert client.put("/api/vpn/admin/settings", json={"monthly_cap_gb": 50}).json()["monthly_cap_gb"] == 50
    assert client.put("/api/vpn/admin/settings", json={"monthly_cap_gb": -1}).status_code == 422
    assert client.put("/api/vpn/admin/settings", json={"min_rank": "knight"}).status_code == 422  # no such setting any more
    assert client.put("/api/vpn/admin/settings", json={"max_devices": 11}).status_code == 422
    assert client.patch(f"/api/vpn/admin/servers/{sid}", json={"name": "FRA", "enabled": False}).json()["name"] == "FRA"
    assert client.post(f"/api/vpn/admin/servers/{sid}/test").json() == {"ok": True, "error": None, "peers": 0}
    fake.fail = True
    t = client.post(f"/api/vpn/admin/servers/{sid}/test").json()
    assert t["ok"] is False and t["error"]
    bad = client.post("/api/vpn/admin/servers", json={"name": "X", "country_code": "DE", "agent_url": URL, "agent_token": TOKEN,
                                                     "cert_sha256": FP})
    assert bad.status_code == 400 and "verify" in bad.json()["detail"]
    assert client.post("/api/vpn/admin/servers", json={"name": "X", "country_code": "DE", "agent_url": "http://1.2.3.4:8443",
                                                      "agent_token": TOKEN, "cert_sha256": FP}).status_code == 400
    fake.fail = False
    assert client.delete(f"/api/vpn/admin/servers/{sid}").json() == {"ok": True}
    assert db.scalars(select(VpnServer)).all() == []


def test_admin_routes_need_the_owner_login(prod: TestClient) -> None:  # noqa: F811
    for method, path in [("GET", "/api/vpn/admin/overview"), ("PUT", "/api/vpn/admin/settings"), ("POST", "/api/vpn/admin/servers"),
                         ("PATCH", "/api/vpn/admin/servers/1"), ("DELETE", "/api/vpn/admin/servers/1"),
                         ("POST", "/api/vpn/admin/servers/1/test")]:
        assert prod.request(method, path, json={}).status_code == 401, path
    assert prod.get("/api/members/vpn").status_code == 401  # member sign-in


# --- sync -----------------------------------------------------------------------------------------------------------


def test_sync_revokes_readds_and_removes_unknown(db: Session, fake: FakeNode) -> None:
    server = add_server(db, fake)
    open_vpn(db)
    keep, drop = member_at(db, "warrior", "keep"), member_at(db, "warrior", "drop")
    d_keep = vpn.create_device(db, keep, "k", server.id)[0]
    d_lost = vpn.create_device(db, keep, "lost", server.id)[0]
    d_drop = vpn.create_device(db, drop, "d", server.id)[0]
    keep_key, lost_key, drop_key, drop_id = d_keep.public_key, d_lost.public_key, d_drop.public_key, d_drop.id
    stranger = keys.generate_keypair()[1]
    fake.peers[stranger] = {"allowed_ip": "10.66.0.99/32", "rx_bytes": 5, "tx_bytes": 5, "latest_handshake": 0}
    del fake.peers[lost_key]  # e.g. the agent's file was lost
    now = dt.datetime.now(dt.timezone.utc)
    fake.peers[keep_key].update(rx_bytes=1000, tx_bytes=2000, latest_handshake=int(now.timestamp()) - 60)
    drop.status = "banned"
    db.commit()
    out = vpn.sync_all(db)
    assert out == {"servers": 1, "online": 1, "updated": 1, "readded": 1, "removed": 1, "revoked": 1, "suspended": 0, "errors": 0, "paused": 0, "resumed": 0}
    assert set(fake.peers) == {keep_key, lost_key} and fake.peers[lost_key]["allowed_ip"] == f"{d_lost.ip}/32"
    db.expire_all()
    assert db.get(VpnDevice, drop_id) is None and drop_key not in fake.peers
    k = db.get(VpnDevice, d_keep.id)
    assert (k.rx_bytes, k.tx_bytes) == (1000, 2000) and k.last_handshake is not None
    fake.peers[keep_key].update(rx_bytes=1500, tx_bytes=2100)  # counters grow: only the difference is added
    vpn.sync_all(db)
    db.expire_all()
    k = db.get(VpnDevice, d_keep.id)
    assert (k.rx_bytes, k.tx_bytes) == (1500, 2100)
    o = vpn.admin_overview(db)
    assert o["totals"] == {"devices": 2, "members": 1, "active_24h": 1}
    # access removed: off at the node, devices kept
    ungrant(db, keep)
    out = vpn.sync_all(db)
    assert out["suspended"] == 2 and out["revoked"] == 0 and fake.peers == {} and db.get(VpnDevice, d_keep.id) is not None
    # an unreachable agent: one failure keeps it online with the error noted; two in a row mark it offline, keeping everything else
    fake.fail = True
    assert vpn.sync_all(db)["online"] == 0
    db.expire_all()
    s = db.get(VpnServer, server.id)
    assert s.online is True and s.fail_streak == 1 and s.last_error
    vpn.sync_all(db)
    db.expire_all()
    s = db.get(VpnServer, server.id)
    assert s.online is False and s.fail_streak == 2 and s.last_error


def test_one_failed_check_doesnt_take_a_server_offline(db: Session, fake: FakeNode) -> None:
    """A blip (one sync or Admin test that can't reach the agent) leaves the server usable; a success in between resets it."""
    server = add_server(db, fake)
    open_vpn(db)
    for fail in (True, False, True, False):  # alternating: never two in a row
        fake.fail = fail
        vpn.sync_all(db)
        db.expire_all()
        assert db.get(VpnServer, server.id).online is True
    assert db.get(VpnServer, server.id).fail_streak == 0
    fake.fail = True
    assert vpn.test_server(db, server.id)["ok"] is False
    db.expire_all()
    assert db.get(VpnServer, server.id).online is True  # the Admin test counts as one check too
    vpn.sync_all(db)
    db.expire_all()
    assert db.get(VpnServer, server.id).online is False
    fake.fail = False
    vpn.sync_all(db)
    db.expire_all()
    s = db.get(VpnServer, server.id)
    assert s.online is True and s.fail_streak == 0 and s.last_error is None


def test_sync_checks_members_without_a_query_per_member(db: Session, fake: FakeNode) -> None:
    """Same decisions as qualifies() one by one (grant, expired grant), but grants are read once a server."""
    from sqlalchemy import event

    server = add_server(db, fake)
    open_vpn(db)
    paid = [member_at(db, "egg", f"w{i}") for i in range(6)]
    expired = member_at(db, "egg", "expired")
    devices = {m.username: vpn.create_device(db, m, "d", server.id)[0].public_key for m in [*paid, expired]}
    db.scalar(select(MemberEntitlement).where(MemberEntitlement.member_id == expired.id)).expires_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)
    db.commit()
    expected = {m.username: vpn.qualifies(db, m)[0] for m in [*paid, expired]}
    assert expected == {**{f"w{i}": True for i in range(6)}, "expired": False}
    sql: list[str] = []
    engine = db.get_bind()

    def on(_c, _cur, statement, *_a):  # type: ignore[no-untyped-def]
        sql.append(statement)

    event.listen(engine, "before_cursor_execute", on)
    try:
        out = vpn.sync_all(db)
    finally:
        event.remove(engine, "before_cursor_execute", on)
    assert out["suspended"] == 1 and set(fake.peers) == {k for n, k in devices.items() if expected[n]}
    assert sum("FROM member_entitlements" in q for q in sql) == 1
    assert sum("FROM members" in q for q in sql) <= 2


def test_sync_job_is_registered() -> None:
    from app.config.settings import get_settings
    from app.jobs import build_jobs
    from app.services.runtime import AppRuntime

    names = {s.name: s for s in build_jobs(AppRuntime.from_settings(get_settings(), None, None, False))}
    assert names["vpn.sync"].interval_seconds == 300


# --- certificate pinning --------------------------------------------------------------------------------------------


def test_pin_check_function() -> None:
    der = b"certificate-bytes"
    good = hashlib.sha256(der).hexdigest()
    node.check_pin(der, good)
    for bad_der, pin in [(der, "00" * 32), (b"other", good), (None, good), (der, "")]:
        with pytest.raises(node.PinMismatch):
            node.check_pin(bad_der, pin)
    assert node.normalize_fingerprint(" " + ":".join([good[i:i + 2].upper() for i in range(0, 64, 2)])) == good
    with pytest.raises(ValueError):
        node.normalize_fingerprint("abc")
    with pytest.raises(node.NodeError):
        node.NodeClient("http://1.2.3.4:8443", TOKEN, good)  # never without TLS


def _self_signed(tmp: Path) -> tuple[Path, Path, str]:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "kiwi-vpn-agent")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(1)
            .not_valid_before(now - dt.timedelta(days=1)).not_valid_after(now + dt.timedelta(days=30)).sign(key, hashes.SHA256()))
    cert_p, key_p = tmp / "cert.pem", tmp / "key.pem"
    cert_p.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_p.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return cert_p, key_p, hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest()


def test_pinning_refuses_a_wrong_certificate_before_sending_the_token(tmp_path: Path) -> None:
    cert, key, fp = _self_signed(tmp_path)
    seen: list[str | None] = []

    class Agent(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            seen.append(self.headers.get("Authorization"))
            body = json.dumps({"public_key": base64.b64encode(b"k" * 32).decode(), "listen_port": 51820, "endpoint_host": "127.0.0.1",
                               "address": "10.66.0.1/16", "dns": [], "peers": []}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a: Any) -> None:
            pass

    httpd = HTTPServer(("127.0.0.1", 0), Agent)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert, key)
    httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    url = f"https://127.0.0.1:{httpd.server_address[1]}"
    try:
        with node.NodeClient(url, TOKEN, fp) as ok:
            assert ok.status()["listen_port"] == 51820
        assert seen == [f"Bearer {TOKEN}"]
        with node.NodeClient(url, TOKEN, "00" * 32) as wrong, pytest.raises(node.NodeError, match="fingerprint"):
            wrong.status()
        assert len(seen) == 1  # the request (and the token) never reached the unpinned peer
    finally:
        httpd.shutdown()
        httpd.server_close()


# --- Telegram /vpn --------------------------------------------------------------------------------------------------


class DocTG:
    def __init__(self) -> None:
        self.sent: list[tuple] = []

    async def send_message(self, chat_id: Any, text: str, buttons: Any = None, html: bool = False, reply_to: Any = None) -> dict[str, Any]:
        self.sent.append(("msg", chat_id, text))
        return {"message_id": len(self.sent)}

    async def send_document(self, chat_id: Any, content: bytes, filename: str, caption: str = "", html: bool = False) -> dict[str, Any]:
        self.sent.append(("doc", chat_id, filename, content, caption))
        return {"message_id": len(self.sent)}


def test_vpn_command_private_only_and_sends_the_config(db: Session, fake: FakeNode) -> None:
    server = add_server(db, fake)
    open_vpn(db)
    m = member_at(db, "warrior")
    members.link_identity(db, m, "telegram", "4242", "kiwi", None)
    db.commit()
    tg = DocTG()
    asyncio.run(bot.handle_command(db, tg, -100123, 4242, "/vpn add phone", group=True, reply_to=7))
    assert tg.sent[-1][0] == "msg" and "Account commands work privately" in tg.sent[-1][2]
    assert db.scalars(select(VpnDevice)).all() == []
    asyncio.run(bot.handle_command(db, tg, 4242, 4242, "/vpn add My Phone"))
    kind, chat, filename, content, caption = tg.sent[-1]
    assert kind == "doc" and chat == 4242 and filename == "kiwi-vpn-frankfurt-my-phone.conf"
    assert b"PrivateKey = " in content and b"AllowedIPs = 0.0.0.0/0, ::/0" in content and "WireGuard" in caption
    dev = db.scalars(select(VpnDevice)).one()
    assert dev.name == "My Phone" and dev.server_id == server.id
    asyncio.run(bot.handle_command(db, tg, 4242, 4242, "/vpn"))
    assert f"#{dev.id}" in tg.sent[-1][2] and "1 of 3 devices" in tg.sent[-1][2]
    asyncio.run(bot.handle_command(db, tg, 4242, 4242, f"/vpn delete {dev.id}"))
    assert "deleted" in tg.sent[-1][2] and db.scalars(select(VpnDevice)).all() == []
    # an unlinked Telegram user / a member without access
    asyncio.run(bot.handle_command(db, tg, 777, 777, "/vpn"))
    assert "Link your Kiwi Passport" in tg.sent[-1][2]
    low = member_at(db, "knight", "egg", paid=False)
    members.link_identity(db, low, "telegram", "555", "egg", None)
    db.commit()
    asyncio.run(bot.handle_command(db, tg, 555, 555, "/vpn add phone"))
    assert "$9.99 for 30 days" in tg.sent[-1][2] and db.scalars(select(VpnDevice)).all() == []


# --- best server, ad blocking, the monthly cap ----------------------------------------------------------------------


def test_recommended_server_and_the_bot_uses_it(client: TestClient, db: Session, fake: FakeNode) -> None:
    a, b, c = add_server(db, fake, "A"), add_server(db, fake, "B"), add_server(db, fake, "C")
    open_vpn(db, max_devices=10)
    m = member_at(db, "warrior")
    rec = lambda: [s["id"] for s in signed_in(client, db, m).get("/api/members/vpn").json()["servers"] if s["recommended"]]  # noqa: E731
    assert rec() == [a.id]  # all empty: a tie, the lowest id wins
    vpn.create_device(db, m, "x", a.id)
    assert rec() == [b.id]
    b.online = False  # offline servers are never recommended
    db.commit()
    assert rec() == [c.id]
    for v in (a, c):
        v.online = False
    db.commit()
    assert rec() == []  # nothing online: none
    for v in (a, b, c):
        v.online = True
    c.capacity = 1
    vpn.create_device(db, m, "y", c.id)
    db.commit()
    assert rec() == [b.id]  # c is full; a has 1 / 100, b is empty
    members.link_identity(db, m, "telegram", "4242", "kiwi", None)
    db.commit()
    tg = DocTG()
    asyncio.run(bot.handle_command(db, tg, 4242, 4242, "/vpn add laptop"))
    assert tg.sent[-1][0] == "doc" and db.scalars(select(VpnDevice).where(VpnDevice.name == "laptop")).one().server_id == b.id
    asyncio.run(bot.handle_command(db, tg, 4242, 4242, "/vpn"))
    assert f"{a.id} A (recommended)" in tg.sent[-1][2]  # a and b now both 1 / 100: the lowest id


def test_adblock_server_uses_its_own_dns(client: TestClient, db: Session, fake: FakeNode) -> None:
    plain = add_server(db, fake, "Plain")
    assert plain.adblock is False  # the agent doesn't report it: no blocking
    fake.adblock = True
    shield = add_server(db, fake, "Shield")
    assert shield.adblock is True and shield.dns == ["10.66.0.1", "1.1.1.1"]
    open_vpn(db)
    c = signed_in(client, db, member_at(db, "warrior"))
    servers = {s["name"]: s for s in c.get("/api/members/vpn").json()["servers"]}
    assert servers["Shield"]["adblock"] is True and servers["Plain"]["adblock"] is False
    cfg = c.post("/api/members/vpn/devices", json={"name": "p", "server_id": shield.id}).json()["config"]
    assert "DNS = 10.66.0.1\n" in cfg  # the server's own resolver
    assert client.get("/api/vpn/admin/overview").json()["servers"][1]["adblock"] is True
    fake.adblock = False  # the agent turned it off: the next sync follows
    vpn.sync_all(db)
    db.refresh(shield)
    assert shield.adblock is False
    cfg = c.post("/api/members/vpn/devices", json={"name": "q", "server_id": plain.id}).json()["config"]
    assert "DNS = 1.1.1.1\n" in cfg  # always just the first one the agent reports


def test_monthly_cap_pauses_then_resumes_next_month(client: TestClient, db: Session, fake: FakeNode) -> None:
    server = add_server(db, fake)
    open_vpn(db, max_devices=5, monthly_cap_gb=1)
    m, other = member_at(db, "warrior", "heavy"), member_at(db, "warrior", "light")
    d1, d2 = vpn.create_device(db, m, "phone", server.id)[0], vpn.create_device(db, m, "laptop", server.id)[0]
    d3 = vpn.create_device(db, other, "phone", server.id)[0]
    k1, k2, k3, id1 = d1.public_key, d2.public_key, d3.public_key, d1.id
    now = utcnow().replace(day=20, hour=12, minute=0, second=0, microsecond=0)  # this month: create_device below reads the real clock
    month_after = vpn.next_month(now)
    fake.peers[k1].update(rx_bytes=600 * 1024 ** 2, tx_bytes=10)
    fake.peers[k2].update(rx_bytes=500 * 1024 ** 2)  # the two devices together pass 1 GB
    fake.peers[k3].update(rx_bytes=100)
    out = vpn.sync_all(db, now)
    assert out["paused"] == 2 and out["resumed"] == 0
    assert k1 not in fake.peers and k2 not in fake.peers and k3 in fake.peers  # peers removed at the node, devices kept
    db.expire_all()
    assert db.get(VpnDevice, id1).paused_until == month_after
    use = vpn.usage_out(db, m, vpn.settings(db), now)
    assert use["used_bytes"] == 1100 * 1024 ** 2 + 10 and use["cap_bytes"] == 1024 ** 3 and use["paused_until"].startswith(month_after.strftime("%Y-%m-01"))
    c = signed_in(client, db, m)
    body = c.get("/api/members/vpn").json()
    assert len(body["devices"]) == 2 and body["usage"]["cap_bytes"] == 1024 ** 3
    r = c.post("/api/members/vpn/devices", json={"name": "tablet", "server_id": server.id})
    assert r.status_code == 403 and "1 GB" in r.json()["detail"]
    # still paused on the next sync: the peers stay off
    assert vpn.sync_all(db, now + dt.timedelta(minutes=5))["readded"] == 0 and k1 not in fake.peers
    # the month rolls over: re-added automatically, counting from zero
    out = vpn.sync_all(db, month_after + dt.timedelta(minutes=5))
    assert out["readded"] == 2 and out["paused"] == 0 and k1 in fake.peers and k2 in fake.peers
    db.expire_all()
    assert db.get(VpnDevice, id1).paused_until is None
    # the owner removes the cap mid-month: a paused member resumes on the next sync
    vpn.sync_all(db, now)  # (back in this month's numbers: paused again)
    assert k1 not in fake.peers
    vpn.update_settings(db, {"monthly_cap_gb": 0})
    assert vpn.sync_all(db, now)["resumed"] == 2 and k1 in fake.peers


# --- owner alerts for servers ---------------------------------------------------------------------------------------


async def test_owner_alerts_offline_recovered_and_nearly_full(db: Session, fake: FakeNode) -> None:
    from app import alerts
    from tests.test_owner_alerts import SETTINGS, Bot

    tg = Bot()
    alerts.settings_row(db).telegram_chat = "424242"
    db.commit()
    server = add_server(db, fake)
    open_vpn(db, max_devices=10)
    run = lambda: alerts.check_vpn_servers(db, settings=SETTINGS, transport=tg.transport)  # noqa: E731
    fake.fail = True
    await asyncio.to_thread(vpn.sync_all, db)
    assert await run() == {"vpn_offline": 0, "vpn_recovered": 0, "vpn_full": 0, "vpn_traffic": 0}  # one failed sync: could be a blip
    for _ in range(3):  # two in a row: one message, however long it stays down
        await asyncio.to_thread(vpn.sync_all, db)
        await run()
    assert len(tg.sent) == 1 and "Kiwi VPN server offline" in tg.sent[0]["text"] and "Frankfurt" in tg.sent[0]["text"]
    fake.fail = False
    await asyncio.to_thread(vpn.sync_all, db)
    assert (await run())["vpn_recovered"] == 1 and "online again" in tg.sent[-1]["text"]
    await run()
    assert len(tg.sent) == 2  # recovered once
    # nearly full: once past 90%, again only after it dropped below 80%
    server.capacity = 10
    db.commit()
    m = member_at(db, "warrior")
    devs = [vpn.create_device(db, m, f"d{i}", server.id)[0] for i in range(9)]
    assert (await run())["vpn_full"] == 1 and "nearly full" in tg.sent[-1]["text"] and "9 of 10" in tg.sent[-1]["text"]
    vpn.delete_device(db, m, devs[0].id)  # 80%: not re-armed yet
    await run()
    vpn.create_device(db, m, "again", server.id)
    assert (await run())["vpn_full"] == 0 and len(tg.sent) == 3
    for d in devs[1:3]:
        vpn.delete_device(db, m, d.id)  # 70%: re-armed
    await run()
    for i in range(2):
        vpn.create_device(db, m, f"e{i}", server.id)
    assert (await run())["vpn_full"] == 1 and len(tg.sent) == 4


def test_ipv6_address_in_config_when_the_agent_has_a_v6_pool(client: TestClient, db: Session, fake: FakeNode) -> None:
    v4only = add_server(db, fake, "Old")
    assert v4only.address6 == ""  # an agent that doesn't report address6: IPv4 only
    fake.address6 = "fd66:66::1/64"
    dual = add_server(db, fake, "Dual")
    assert dual.address6 == "fd66:66::1/64"
    open_vpn(db)
    c = signed_in(client, db, member_at(db, "warrior"))
    cfg = c.post("/api/members/vpn/devices", json={"name": "p", "server_id": dual.id}).json()["config"]
    assert "Address = 10.66.0.2/32, fd66:66::2/128\n" in cfg
    cfg = c.post("/api/members/vpn/devices", json={"name": "q", "server_id": v4only.id}).json()["config"]
    assert "Address = 10.66.0.2/32\n" in cfg
    fake.address6 = "not-an-address"  # garbage from the agent: treated as no v6
    vpn.sync_all(db)
    db.refresh(dual)
    assert dual.address6 == ""


def test_adding_a_device_holds_no_write_lock_while_the_node_answers(client: TestClient, db: Session, fake: FakeNode) -> None:
    from tests.locks import http_calls_under_write_lock

    server = add_server(db, fake)
    open_vpn(db)
    c = signed_in(client, db, member_at(db, "warrior"))
    with http_calls_under_write_lock() as held:
        assert c.post("/api/members/vpn/devices", json={"name": "My phone", "server_id": server.id}).status_code == 201
        fake.fail = True  # a failed node call still leaves nothing behind
        assert c.post("/api/members/vpn/devices", json={"name": "Tablet", "server_id": server.id}).status_code == 503
    assert held == []
    assert [d.name for d in db.scalars(select(VpnDevice))] == ["My phone"]
