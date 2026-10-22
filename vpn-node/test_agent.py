"""Tests for the Kiwi VPN node agent with `wg` faked.

Run from the repo root:
    python -m pytest vpn-node/test_agent.py -q -p no:cacheprovider
"""
import base64
import http.client
import json
import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agent as A  # noqa: E402

TOKEN = "t" * 43
SERVER_PUB = base64.b64encode(b"S" * 32).decode()


def key(n: int) -> str:
    return base64.b64encode(bytes([n]) * 32).decode()


def urlkey(k: str) -> str:
    return k.replace("+", "-").replace("/", "_")


class FakeWG:
    """Minimal in-memory stand-in for `wg` on interface wg0."""

    def __init__(self):
        self.calls = []
        self.peers = {}  # key -> ip
        self.fail = False

    def __call__(self, args):
        self.calls.append(list(args))
        if self.fail:
            raise RuntimeError("wg failed")
        if args[:3] == ["show", "wg0", "dump"]:
            lines = [f"PRIV\t{SERVER_PUB}\t51820\toff"]
            for k, ip in self.peers.items():
                lines.append(f"{k}\t(none)\t203.0.113.9:4444\t{ip}\t1700000000\t100\t200\toff")
            return "\n".join(lines) + "\n"
        if args[0] == "set" and args[4:] == ["remove"]:
            self.peers.pop(args[3], None)
            return ""
        if args[0] == "set" and args[4] == "allowed-ips":
            for k, ip in list(self.peers.items()):
                if ip == args[5] and k != args[3]:
                    self.peers[k] = "(none)"
            self.peers[args[3]] = args[5]
            return ""
        raise AssertionError(f"unexpected wg call {args}")


@pytest.fixture
def wg():
    return FakeWG()


@pytest.fixture
def cfg(tmp_path):
    return {"TOKEN": TOKEN, "POOL": "10.66.0.1/16", "ENDPOINT_HOST": "198.51.100.7",
            "DNS": "1.1.1.1, 9.9.9.9", "STATE_FILE": str(tmp_path / "state" / "peers.json")}


@pytest.fixture
def agent(cfg, wg):
    a = A.Agent(cfg, runner=wg)
    a.start()
    return a


def call(agent, method, path, token=TOKEN, body=None):
    auth = f"Bearer {token}" if token is not None else None
    raw = json.dumps(body).encode() if body is not None else b""
    return agent.handle(method, path, auth, raw)


# ---- auth ----
def test_health_needs_no_auth(agent):
    assert call(agent, "GET", "/v1/health", token=None) == (200, {"ok": True})


@pytest.mark.parametrize("token", [None, "", "wrong", TOKEN + "x", TOKEN[:-1]])
def test_bad_or_missing_token_is_401(agent, token):
    for method, path in [("GET", "/v1/status"), ("PUT", f"/v1/peers/{urlkey(key(1))}"),
                         ("DELETE", f"/v1/peers/{urlkey(key(1))}"), ("GET", "/v1/nope")]:
        code, body = call(agent, method, path, token=token, body={"allowed_ip": "10.66.0.5/32"})
        assert code == 401 and body == {"error": "unauthorized"}
    assert agent.peers == {}


def test_non_bearer_scheme_rejected(agent):
    assert agent.handle("GET", "/v1/status", f"Basic {TOKEN}", b"")[0] == 401


def test_short_token_refused(cfg, wg):
    with pytest.raises(ValueError):
        A.Agent({**cfg, "TOKEN": "short"}, runner=wg)


# ---- validation ----
@pytest.mark.parametrize("bad", ["abc", "x" * 44, base64.b64encode(b"k" * 31).decode() + "==",
                                 base64.b64encode(b"k" * 33).decode(), key(1)[:-1] + "A",
                                 "..%2F" + key(1)[5:]])
def test_bad_keys_400(agent, bad):
    code, _ = call(agent, "PUT", f"/v1/peers/{bad}", body={"allowed_ip": "10.66.0.5/32"})
    assert code in (400, 404)
    assert agent.peers == {}


def test_url_safe_and_standard_keys_accepted():
    k = base64.b64encode(bytes([0xFB, 0xFF]) * 16).decode()  # contains + and /
    assert "+" in k or "/" in k
    assert A.parse_key(urlkey(k)) == k
    assert A.parse_key(k) == k


@pytest.mark.parametrize("ip", ["10.66.0.5", "10.66.0.5/24", "10.66.0.0/32", "10.66.255.255/32",
                                "10.66.0.1/32", "10.67.0.5/32", "192.168.1.5/32", "::1/128",
                                "fd00::5/128", "nonsense", None, 5])
def test_bad_or_out_of_pool_ip_400(agent, ip):
    code, _ = call(agent, "PUT", f"/v1/peers/{urlkey(key(1))}", body={"allowed_ip": ip})
    assert code == 400
    assert agent.peers == {}


@pytest.mark.parametrize("ip", ["10.66.0.2/32", "10.66.255.254/32", "10.66.17.3/32"])
def test_pool_bounds_ok(agent, ip):
    assert call(agent, "PUT", f"/v1/peers/{urlkey(key(1))}", body={"allowed_ip": ip})[0] == 200


def test_bad_json_400(agent):
    assert agent.handle("PUT", f"/v1/peers/{urlkey(key(1))}", f"Bearer {TOKEN}", b"{nope")[0] == 400
    assert agent.handle("PUT", f"/v1/peers/{urlkey(key(1))}", f"Bearer {TOKEN}", b"[1]")[0] == 400


# ---- peers ----
def test_put_is_idempotent_and_replaces_ip(agent, wg):
    path = f"/v1/peers/{urlkey(key(1))}"
    assert call(agent, "PUT", path, body={"allowed_ip": "10.66.0.5/32"}) == (200, {"ok": True})
    assert call(agent, "PUT", path, body={"allowed_ip": "10.66.0.5/32"}) == (200, {"ok": True})
    assert agent.peers == {key(1): "10.66.0.5/32"}
    assert wg.peers == {key(1): "10.66.0.5/32"}
    assert call(agent, "PUT", path, body={"allowed_ip": "10.66.0.6/32"})[0] == 200
    assert agent.peers == {key(1): "10.66.0.6/32"}
    assert wg.peers == {key(1): "10.66.0.6/32"}


def test_ip_taken_by_another_peer_moves(agent, wg):
    call(agent, "PUT", f"/v1/peers/{urlkey(key(1))}", body={"allowed_ip": "10.66.0.5/32"})
    call(agent, "PUT", f"/v1/peers/{urlkey(key(2))}", body={"allowed_ip": "10.66.0.5/32"})
    assert agent.peers == {key(2): "10.66.0.5/32"}
    assert wg.peers == {key(2): "10.66.0.5/32"}


def test_delete_unknown_ok_and_delete_known(agent, wg):
    assert call(agent, "DELETE", f"/v1/peers/{urlkey(key(9))}") == (200, {"ok": True})
    call(agent, "PUT", f"/v1/peers/{urlkey(key(1))}", body={"allowed_ip": "10.66.0.5/32"})
    assert call(agent, "DELETE", f"/v1/peers/{urlkey(key(1))}") == (200, {"ok": True})
    assert agent.peers == {} and wg.peers == {}


def test_wg_uses_argument_lists(agent, wg):
    call(agent, "PUT", f"/v1/peers/{urlkey(key(1))}", body={"allowed_ip": "10.66.0.5/32"})
    assert ["set", "wg0", "peer", key(1), "allowed-ips", "10.66.0.5/32"] in wg.calls


def test_wg_failure_is_500_and_not_persisted(agent, wg):
    wg.fail = True
    code, body = call(agent, "PUT", f"/v1/peers/{urlkey(key(1))}", body={"allowed_ip": "10.66.0.5/32"})
    assert code == 500 and body == {"error": "internal"}
    assert agent.peers == {}


# ---- persistence ----
def test_persistence_and_reload(cfg, agent, wg):
    call(agent, "PUT", f"/v1/peers/{urlkey(key(1))}", body={"allowed_ip": "10.66.0.5/32"})
    call(agent, "PUT", f"/v1/peers/{urlkey(key(2))}", body={"allowed_ip": "10.66.0.6/32"})
    with open(cfg["STATE_FILE"]) as f:
        assert json.load(f) == {"peers": {key(1): "10.66.0.5/32", key(2): "10.66.0.6/32"}}
    assert not [n for n in os.listdir(os.path.dirname(cfg["STATE_FILE"])) if n.endswith(".tmp")]

    # Reboot: kernel is empty except a stray peer that must be removed.
    fresh = FakeWG()
    fresh.peers = {key(7): "10.66.0.99/32"}
    b = A.Agent(cfg, runner=fresh)
    b.start()
    assert b.peers == {key(1): "10.66.0.5/32", key(2): "10.66.0.6/32"}
    assert fresh.peers == b.peers


def test_reload_skips_invalid_entries(cfg, wg):
    os.makedirs(os.path.dirname(cfg["STATE_FILE"]))
    with open(cfg["STATE_FILE"], "w") as f:
        json.dump({"peers": {key(1): "10.66.0.5/32", "junk": "10.66.0.6/32", key(3): "8.8.8.8/32"}}, f)
    a = A.Agent(cfg, runner=wg)
    a.start()
    assert a.peers == {key(1): "10.66.0.5/32"}


# ---- status ----
SAMPLE_DUMP = (
    f"cPrivKeyNeverShown=\t{SERVER_PUB}\t51820\toff\n"
    f"{key(1)}\t(none)\t203.0.113.9:51000\t10.66.0.5/32\t1729600000\t123456\t654321\t25\n"
    f"{key(2)}\t(none)\t(none)\t10.66.0.6/32\t0\t0\t0\toff\n"
    f"{key(3)}\t(none)\t(none)\t(none)\t0\t0\t0\toff\n"
)


def test_parse_dump_sample():
    iface, peers = A.parse_dump(SAMPLE_DUMP)
    assert iface == {"public_key": SERVER_PUB, "listen_port": 51820}
    assert peers == [
        {"public_key": key(1), "allowed_ip": "10.66.0.5/32", "rx_bytes": 123456,
         "tx_bytes": 654321, "latest_handshake": 1729600000},
        {"public_key": key(2), "allowed_ip": "10.66.0.6/32", "rx_bytes": 0, "tx_bytes": 0,
         "latest_handshake": 0},
        {"public_key": key(3), "allowed_ip": "", "rx_bytes": 0, "tx_bytes": 0, "latest_handshake": 0},
    ]


def test_status_shape_and_no_endpoints(cfg):
    a = A.Agent(cfg, runner=lambda args: SAMPLE_DUMP)
    code, body = call(a, "GET", "/v1/status")
    assert code == 200
    assert body["public_key"] == SERVER_PUB
    assert body["listen_port"] == 51820
    assert body["endpoint_host"] == "198.51.100.7"
    assert body["address"] == "10.66.0.1/16"
    assert body["dns"] == ["1.1.1.1", "9.9.9.9"]
    assert len(body["peers"]) == 3
    text = json.dumps(body)
    assert "203.0.113.9" not in text and "PrivKey" not in text


# ---- HTTP layer (plain TCP; TLS is stdlib ssl in production) ----
@pytest.fixture
def http_server(agent):
    srv = A.AgentServer(("127.0.0.1", 0), A.make_handler(agent))
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv.server_address[1]
    srv.shutdown()
    srv.server_close()


def request(port, method, path, body=None, token=TOKEN, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    h = {"Authorization": f"Bearer {token}"} if token else {}
    h.update(headers or {})
    c.request(method, path, body=body, headers=h)
    r = c.getresponse()
    return r.status, r.read()


def test_http_roundtrip(http_server, capsys):
    port = http_server
    assert request(port, "GET", "/v1/health", token=None) == (200, b'{"ok": true}')
    assert request(port, "GET", "/v1/status", token="nope")[0] == 401
    path = f"/v1/peers/{urlkey(key(1))}"
    assert request(port, "PUT", path, json.dumps({"allowed_ip": "10.66.0.5/32"}))[0] == 200
    status, raw = request(port, "GET", "/v1/status")
    assert status == 200 and json.loads(raw)["peers"][0]["allowed_ip"] == "10.66.0.5/32"
    assert request(port, "DELETE", path)[0] == 200
    assert "127.0.0.1" not in capsys.readouterr().err  # no client addresses in logs


def test_http_body_limit(http_server):
    big = json.dumps({"allowed_ip": "10.66.0.5/32", "pad": "x" * (A.MAX_BODY + 1)})
    assert request(http_server, "PUT", f"/v1/peers/{urlkey(key(1))}", big)[0] == 413
