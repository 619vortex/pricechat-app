"""Kiwi VPN end to end: the backend against the REAL node agent (vpn-node/agent.py), over real TLS on 127.0.0.1.

Only `wg` is faked (an in-memory WireGuard interface that answers `wg set` / `wg show <if> dump` like the real tool), so
everything between the admin/member API and the agent's peer file is the production code: certificate pinning, the bearer
token, URL-safe keys in paths, the status JSON, the agent's IP checks and its peer file across a restart."""

import base64
import datetime as dt
import hashlib
import importlib.util
import ssl
import sys
import threading
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.members import xp as ledger
from app.models.vpn import VpnDevice, VpnServer
from app.vpn import keys, service as vpn
from tests.test_vpn import member_at, open_vpn, signed_in

AGENT_PY = Path(__file__).resolve().parents[2] / "vpn-node" / "agent.py"
TOKEN = "kiwi-agent-token-" + "x" * 32


def _load_agent_module() -> Any:
    spec = importlib.util.spec_from_file_location("kiwi_vpn_agent_e2e", AGENT_PY)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


agent_mod = _load_agent_module()


class FakeWg:
    """TEST FIXTURE: `wg` for one interface, in memory. Allowed IPs are unique across peers, like the kernel's."""

    def __init__(self, iface: str = "wg0", listen_port: int = 51820) -> None:
        self.iface = iface
        self.private_key, self.public_key = keys.generate_keypair()
        self.listen_port = listen_port
        self.peers: dict[str, dict[str, Any]] = {}  # standard base64 key -> {allowed_ip, rx, tx, hs}
        self.lock = threading.Lock()
        self.calls: list[list[str]] = []

    def reboot(self) -> None:
        """The interface comes back (same key, from wg0.conf) with no peers: the agent must re-apply its file."""
        self.peers = {}

    def __call__(self, args: list) -> str:
        with self.lock:
            self.calls.append(list(args))
            if args[:3] == ["show", self.iface, "dump"]:
                return self._dump()
            if len(args) >= 5 and args[0] == "set" and args[1] == self.iface and args[2] == "peer":
                key = args[3]
                assert keys.is_key(key), f"wg got a non-standard key: {key!r}"
                if args[4:] == ["remove"]:
                    self.peers.pop(key, None)
                    return ""
                if args[4] == "allowed-ips" and len(args) == 6:
                    ip = args[5]
                    for other in self.peers.values():  # an allowed IP moves to the newest peer
                        if other["allowed_ip"] == ip:
                            other["allowed_ip"] = ""
                    p = self.peers.setdefault(key, {"allowed_ip": "", "rx": 0, "tx": 0, "hs": 0})
                    p["allowed_ip"] = ip
                    return ""
            raise RuntimeError(f"wg {args[0] if args else ''} failed (rc=1)")

    def _dump(self) -> str:
        lines = [f"{self.private_key}\t{self.public_key}\t{self.listen_port}\toff"]
        for k, p in self.peers.items():
            lines.append("\t".join([k, "(none)", "203.0.113.77:40000", p["allowed_ip"] or "(none)", str(p["hs"]), str(p["rx"]),
                                    str(p["tx"]), "off"]))
        return "\n".join(lines) + "\n"


def _self_signed(tmp: Path, cn: str = "kiwi-vpn-agent") -> tuple[Path, Path, bytes]:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(days=1)).not_valid_after(now + dt.timedelta(days=30)).sign(key, hashes.SHA256()))
    cert_p, key_p = tmp / f"{cn}.crt", tmp / f"{cn}.key"
    cert_p.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_p.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return cert_p, key_p, cert.public_bytes(serialization.Encoding.DER)


class RunningAgent:
    """The real agent (Agent + AgentServer + handler) on 127.0.0.1 over TLS; every request that reached the agent is logged."""

    def __init__(self, tmp: Path, wg: FakeWg) -> None:
        self.cert, self.key, der = _self_signed(tmp)
        self.fp = hashlib.sha256(der).hexdigest()
        self.wg = wg
        self.state_file = tmp / "state" / "peers.json"
        self.cfg = {"TOKEN": TOKEN, "POOL": "10.66.0.1/16", "ENDPOINT_HOST": "198.51.100.7", "LISTEN_PORT": "51820",
                    "DNS": "1.1.1.1,1.0.0.1", "WG_INTERFACE": "wg0", "STATE_FILE": str(self.state_file)}
        self.requests: list[tuple[str, str, str | None]] = []
        self.port = 0
        self.agent: Any = None
        self.server: Any = None
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        agent = agent_mod.Agent(self.cfg, runner=self.wg)
        agent.start()
        real_handle = agent.handle

        def handle(method: str, path: str, auth: str | None, body: bytes) -> tuple[int, dict]:
            self.requests.append((method, path, auth))
            return real_handle(method, path, auth, body)

        agent.handle = handle
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(str(self.cert), str(self.key))
        self.server = agent_mod.AgentServer(("127.0.0.1", self.port), agent_mod.make_handler(agent), ctx)
        self.port = self.server.server_address[1]
        self.agent = agent
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
            self.server = None

    @property
    def url(self) -> str:
        return f"https://127.0.0.1:{self.port}"

    def tokens_seen(self) -> set[str | None]:
        return {auth for _, _, auth in self.requests}


@pytest.fixture
def wg() -> FakeWg:
    return FakeWg()


@pytest.fixture
def agent(tmp_path: Path, wg: FakeWg) -> Any:
    a = RunningAgent(tmp_path, wg)
    a.start()
    yield a
    a.stop()


def colon_upper(fp: str) -> str:
    return ":".join(fp[i:i + 2] for i in range(0, 64, 2)).upper()


def server_body(agent: RunningAgent, fp: str, name: str = "Frankfurt") -> dict[str, Any]:
    return {"name": name, "region": "Europe", "country_code": "de", "agent_url": agent.url, "agent_token": TOKEN, "cert_sha256": fp,
            "capacity": 100}


def add_server(client: TestClient, agent: RunningAgent, fp: str | None = None) -> int:
    r = client.post("/api/vpn/admin/servers", json=server_body(agent, fp or agent.fp))
    assert r.status_code == 200, r.text
    return r.json()["id"]


def parse_conf(text: str) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    section = ""
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            out[section] = {}
        elif "=" in line:
            k, v = line.split("=", 1)
            out[section][k.strip()] = v.strip()
    return out


# --- tests ----------------------------------------------------------------------------------------------------------


def test_admin_adds_the_real_agent_and_status_fills_the_server(client: TestClient, db: Session, agent: RunningAgent, wg: FakeWg) -> None:
    sid = add_server(client, agent)
    row = db.get(VpnServer, sid)
    assert row.public_key == wg.public_key and row.listen_port == 51820 and row.endpoint_host == "198.51.100.7"
    assert row.address == "10.66.0.1/16" and row.dns == ["1.1.1.1", "1.0.0.1"] and row.online and row.cert_sha256 == agent.fp
    assert agent.tokens_seen() == {f"Bearer {TOKEN}"} and agent.requests[-1][:2] == ("GET", "/v1/status")
    # the colon / upper-case fingerprint (as `openssl x509 -fingerprint -sha256` prints it) is the same pin
    sid2 = add_server(client, agent, colon_upper(agent.fp))
    assert db.get(VpnServer, sid2).cert_sha256 == agent.fp
    assert client.post(f"/api/vpn/admin/servers/{sid}/test").json() == {"ok": True, "error": None, "peers": 0}
    o = client.get("/api/vpn/admin/overview").json()
    assert {s["id"] for s in o["servers"]} == {sid, sid2} and all(s["online"] for s in o["servers"])


def test_member_device_round_trip_through_the_agent(client: TestClient, db: Session, agent: RunningAgent, wg: FakeWg) -> None:
    sid = add_server(client, agent)
    open_vpn(db)
    c = signed_in(client, db, member_at(db, "warrior"))
    ov = c.get("/api/members/vpn").json()
    assert ov["eligible"] and [s["id"] for s in ov["servers"]] == [sid] and ov["servers"][0]["online"]
    r = c.post("/api/members/vpn/devices", json={"name": "My phone", "server_id": sid})
    assert r.status_code == 201, r.text
    body = r.json()
    conf = parse_conf(body["config"])
    priv = conf["Interface"]["PrivateKey"]
    assert len(base64.b64decode(priv, validate=True)) == 32
    pub = keys.public_from_private(priv)
    # the agent (and the "kernel") now has exactly this peer, with the device's tunnel IP
    assert agent.agent.peers == {pub: f"{body['device']['ip']}/32"}
    assert wg.peers[pub]["allowed_ip"] == f"{body['device']['ip']}/32" == conf["Interface"]["Address"]
    assert conf["Peer"]["PublicKey"] == wg.public_key and conf["Peer"]["Endpoint"] == "198.51.100.7:51820"
    assert conf["Interface"]["DNS"] == "1.1.1.1, 1.0.0.1" and conf["Peer"]["AllowedIPs"] == "0.0.0.0/0, ::/0"
    st = agent.agent.status()
    assert [p["public_key"] for p in st["peers"]] == [pub]
    # the peer file on disk has it too (survives a reboot)
    assert pub in agent.state_file.read_text(encoding="utf-8")
    put = [p for m, p, _ in agent.requests if m == "PUT"]
    assert put == [f"/v1/peers/{keys.url_key(pub)}"]
    assert c.delete(f"/api/members/vpn/devices/{body['device']['id']}").status_code == 204
    assert agent.agent.peers == {} and wg.peers == {} and pub not in agent.state_file.read_text(encoding="utf-8")
    assert c.get("/api/members/vpn").json()["devices"] == []
    # a key with both `+` and `/` survives the URL-safe path both ways (deterministic, not left to random keys)
    from app.vpn import node

    key = next(k for k in iter(lambda: keys.generate_keypair()[1], None) if "+" in k and "/" in k)
    row = db.get(VpnServer, sid)
    with node.client_for(row) as nc:
        nc.put_peer(key, "10.66.3.3/32")
        assert wg.peers[key]["allowed_ip"] == "10.66.3.3/32" and any(p["public_key"] == key for p in nc.status()["peers"])
        nc.delete_peer(key)
    assert key not in wg.peers and key not in agent.agent.peers


def test_sync_against_the_agent(client: TestClient, db: Session, agent: RunningAgent, wg: FakeWg) -> None:
    sid = add_server(client, agent)
    open_vpn(db)
    keep, drop = member_at(db, "warrior", "keep"), member_at(db, "warrior", "drop")
    c = signed_in(client, db, keep)
    d1 = c.post("/api/members/vpn/devices", json={"name": "one", "server_id": sid}).json()["device"]
    d2 = c.post("/api/members/vpn/devices", json={"name": "two", "server_id": sid}).json()["device"]
    dd = vpn.create_device(db, drop, "drop", sid)[0]
    k1, k2, kd = (db.get(VpnDevice, d1["id"]).public_key, db.get(VpnDevice, d2["id"]).public_key, dd.public_key)
    assert vpn.sync_all(db) == {"servers": 1, "online": 1, "updated": 3, "readded": 0, "removed": 0, "revoked": 0, "errors": 0}

    # a peer vanished from the kernel -> re-added; an unknown peer -> removed
    del wg.peers[k2]
    stranger = keys.generate_keypair()[1]
    wg.peers[stranger] = {"allowed_ip": "10.66.9.9/32", "rx": 1, "tx": 1, "hs": 0}
    out = vpn.sync_all(db)
    assert (out["readded"], out["removed"], out["errors"]) == (1, 1, 0)
    assert set(wg.peers) == {k1, k2, kd} and wg.peers[k2]["allowed_ip"] == f"{d2['ip']}/32" and stranger not in wg.peers

    # traffic counters and the handshake reach the member's device list
    hs = int(dt.datetime.now(dt.timezone.utc).timestamp()) - 30
    wg.peers[k1].update(rx=12345, tx=67890, hs=hs)
    vpn.sync_all(db)
    dev = next(d for d in c.get("/api/members/vpn").json()["devices"] if d["id"] == d1["id"])
    assert (dev["rx_bytes"], dev["tx_bytes"]) == (12345, 67890)
    assert dt.datetime.fromisoformat(dev["last_handshake"]).timestamp() == hs

    # a member who loses eligibility is revoked at the agent
    ledger.adjust(db, drop, -drop.rank_score, actor="test", reason="reset", ref="down-e2e")
    db.commit()
    out = vpn.sync_all(db)
    assert out["revoked"] == 1 and out["errors"] == 0
    assert kd not in wg.peers and kd not in agent.agent.peers and set(agent.agent.peers) == {k1, k2}
    db.expire_all()
    assert db.scalars(select(VpnDevice).where(VpnDevice.member_id == drop.id)).all() == []
    assert vpn.sync_all(db) == {"servers": 1, "online": 1, "updated": 2, "readded": 0, "removed": 0, "revoked": 0, "errors": 0}


def test_pinning_a_wrong_certificate_never_sends_the_token(client: TestClient, db: Session, agent: RunningAgent, tmp_path: Path) -> None:
    wrong = hashlib.sha256(_self_signed(tmp_path, "someone-else")[2]).hexdigest()
    r = client.post("/api/vpn/admin/servers", json=server_body(agent, wrong))
    assert r.status_code == 400 and "fingerprint" in r.json()["detail"]
    assert db.scalars(select(VpnServer)).all() == []
    assert agent.requests == []  # the TLS handshake failed: no request (and no bearer token) ever reached the agent
    # a stored server whose pin is changed to a wrong one fails its test and goes offline, again without a request
    sid = add_server(client, agent)
    n = len(agent.requests)
    assert client.patch(f"/api/vpn/admin/servers/{sid}", json={"cert_sha256": colon_upper(wrong)}).status_code == 200
    t = client.post(f"/api/vpn/admin/servers/{sid}/test").json()
    assert t["ok"] is False and "fingerprint" in t["error"] and len(agent.requests) == n
    assert vpn.sync_all(db)["online"] == 0 and len(agent.requests) == n
    db.expire_all()
    assert db.get(VpnServer, sid).online is False
    # a wrong token is refused by the agent with 401
    vpn.update_server(db, sid, {"cert_sha256": agent.fp, "agent_token": "z" * 40})
    t = client.post(f"/api/vpn/admin/servers/{sid}/test").json()
    assert t["ok"] is False and "token" in t["error"] and agent.requests[-1][2] == f"Bearer {'z' * 40}"


def test_agent_restart_reapplies_peers_and_sync_is_clean(client: TestClient, db: Session, agent: RunningAgent, wg: FakeWg) -> None:
    sid = add_server(client, agent)
    open_vpn(db)
    c = signed_in(client, db, member_at(db, "warrior"))
    devs = [c.post("/api/members/vpn/devices", json={"name": f"d{i}", "server_id": sid}).json()["device"] for i in range(2)]
    before = dict(agent.agent.peers)
    assert len(before) == 2
    wg.peers[next(iter(before))].update(rx=500, tx=700)
    vpn.sync_all(db)

    agent.stop()  # the box reboots: the interface comes back empty (plus a stray peer), the agent re-reads its file
    wg.reboot()
    wg.peers[keys.generate_keypair()[1]] = {"allowed_ip": "10.66.5.5/32", "rx": 0, "tx": 0, "hs": 0}
    assert vpn.sync_all(db)["online"] == 0  # down meanwhile
    agent.start()
    assert agent.agent.peers == before
    assert {k: p["allowed_ip"] for k, p in wg.peers.items()} == before
    out = vpn.sync_all(db)
    assert out == {"servers": 1, "online": 1, "updated": 2, "readded": 0, "removed": 0, "revoked": 0, "errors": 0}
    # counters restarted at zero on the node: the monthly totals keep what was counted before
    k = next(iter(before))
    wg.peers[k].update(rx=100, tx=100)
    vpn.sync_all(db)
    db.expire_all()
    row = db.scalars(select(VpnDevice).where(VpnDevice.public_key == k)).one()
    assert (row.rx_bytes, row.tx_bytes) == (600, 800)
    assert {d["id"] for d in c.get("/api/members/vpn").json()["devices"]} == {d["id"] for d in devs}
    assert db.get(VpnServer, sid).online is True
