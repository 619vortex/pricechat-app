"""Kiwi VPN: the rank gate, the device limit, IP allocation (unique under a racing insert), the config, the private key never
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
        self.calls: list[tuple[str, str]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.fail:
            raise httpx.ConnectError("down")
        if request.headers.get("authorization") != f"Bearer {TOKEN}":
            return httpx.Response(401)
        path = request.url.path
        self.calls.append((request.method, path))
        if path == "/v1/status":
            return httpx.Response(200, json={"public_key": self.server_key, "listen_port": 51820, "endpoint_host": "203.0.113.10",
                                             "address": self.address, "dns": ["1.1.1.1", "1.0.0.1"],
                                             "peers": [{"public_key": k, **v} for k, v in self.peers.items()]})
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


def member_at(db: Session, rank_key: str, name: str = "kiwi") -> Member:
    ensure_catalog(db)
    m = members.create_member(db)
    m.username, m.username_key = name, name.lower()
    db.commit()
    need = next(r.min_xp for r in ledger.ranks(db) if r.key == rank_key)
    if need:
        ledger.adjust(db, m, need, actor="test", reason="rank", ref=f"r-{name}")
    db.refresh(m)
    return m


def signed_in(client: TestClient, db: Session, m: Member) -> TestClient:
    client.cookies.set("pc_session", members.new_session(db, m, 7))
    return client


def open_vpn(db: Session, **over: Any) -> None:
    vpn.update_settings(db, {"enabled": True, **over})


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
    fn = keys.filename("Frankfurt 1", "My iPhone 15 Pro Max!!")
    assert fn.startswith("kiwi-vpn-frankfurt-my-iphone") and fn.endswith(".conf") and len(fn) - 5 <= 32


# --- member API -----------------------------------------------------------------------------------------------------


def test_closed_and_rank_gate(client: TestClient, db: Session, fake: FakeNode) -> None:
    server = add_server(db, fake)
    low, high = member_at(db, "scout", "low"), member_at(db, "warrior", "high")
    r = signed_in(client, db, high).get("/api/members/vpn").json()
    assert r["enabled"] is False and r["eligible"] is False and r["max_devices"] == 3  # closed by default
    assert client.post("/api/members/vpn/devices", json={"name": "phone", "server_id": server.id}).status_code == 403
    open_vpn(db)
    r = client.get("/api/members/vpn").json()
    assert r["eligible"] and r["reason"] is None and r["required_rank"] == "Warrior Kiwi"
    assert r["servers"] == [{"id": server.id, "name": "Frankfurt", "region": "Europe", "country_code": "DE", "online": True, "load": 0.0}]
    r = signed_in(client, db, low).get("/api/members/vpn").json()
    assert not r["eligible"] and "Warrior Kiwi" in r["reason"]
    assert client.post("/api/members/vpn/devices", json={"name": "phone", "server_id": server.id}).status_code == 403
    # a manual grant of the kiwi_vpn entitlement unlocks it, like the other perks
    ent = db.scalar(select(Entitlement).where(Entitlement.key == "kiwi_vpn"))
    db.add(MemberEntitlement(member_id=low.id, entitlement_id=ent.id, granted_by="test"))
    db.commit()
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
    assert f"PublicKey = {fake.server_key}" in cfg and "DNS = 1.1.1.1, 1.0.0.1" in cfg and "Endpoint = 203.0.113.10:51820" in cfg
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
    assert c.post("/api/members/vpn/devices", json={"name": "d3", "server_id": 999}).status_code == 429  # 5 tries, then slow down
    LIMITS["vpn_device"].clear()
    assert c.post("/api/members/vpn/devices", json={"name": "d3", "server_id": 999}).status_code == 404
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
    db.add(VpnDevice(member_id=m.id, server_id=tiny.id, name="x", public_key=keys.generate_keypair()[1], ip="10.8.0.2"))
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
    assert o["settings"] == {"enabled": False, "min_rank": "warrior", "max_devices": 3}
    assert o["ranks"][0] == "egg" and "warrior" in o["ranks"]
    assert o["servers"][0]["online"] is True and o["servers"][0]["capacity"] == 50 and "agent_token" not in json.dumps(o)
    assert o["totals"] == {"devices": 0, "members": 0, "active_24h": 0}
    assert client.put("/api/vpn/admin/settings", json={"min_rank": "knight", "max_devices": 5, "enabled": True}).json() == \
        {"enabled": True, "min_rank": "knight", "max_devices": 5}
    assert db.scalar(select(Entitlement.required_rank).where(Entitlement.key == "kiwi_vpn")) == 6  # the entitlement follows
    assert client.put("/api/vpn/admin/settings", json={"min_rank": "nope"}).status_code == 400
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
    assert out == {"servers": 1, "online": 1, "updated": 1, "readded": 1, "removed": 1, "revoked": 1, "errors": 0}
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
    # rank dropped (XP taken back): revoked too
    ledger.adjust(db, keep, -keep.rank_score, actor="test", reason="reset", ref="down")
    db.commit()
    assert vpn.sync_all(db)["revoked"] == 2 and fake.peers == {}
    # an unreachable agent marks the server offline, keeps everything else
    fake.fail = True
    assert vpn.sync_all(db)["online"] == 0
    db.expire_all()
    s = db.get(VpnServer, server.id)
    assert s.online is False and s.last_error


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
    # an unlinked Telegram user / a member below the rank
    asyncio.run(bot.handle_command(db, tg, 777, 777, "/vpn"))
    assert "Link your Kiwi Passport" in tg.sent[-1][2]
    low = member_at(db, "egg", "egg")
    members.link_identity(db, low, "telegram", "555", "egg", None)
    db.commit()
    asyncio.run(bot.handle_command(db, tg, 555, 555, "/vpn add phone"))
    assert "Warrior Kiwi" in tg.sent[-1][2] and db.scalars(select(VpnDevice)).all() == []
