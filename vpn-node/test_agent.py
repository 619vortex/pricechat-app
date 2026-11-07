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


# ---- adblock flag (from agent.env) ----
def test_status_adblock_false_by_default(cfg):
    a = A.Agent(cfg, runner=lambda args: SAMPLE_DUMP)
    assert call(a, "GET", "/v1/status")[1]["adblock"] is False


@pytest.mark.parametrize("raw,expected", [("1", True), ("true", True), ("YES", True), ("on", True),
                                          ("0", False), ("", False), ("no", False), ("off", False)])
def test_status_adblock_from_env(cfg, raw, expected):
    a = A.Agent({**cfg, "ADBLOCK": raw, "DNS": "10.66.0.1"}, runner=lambda args: SAMPLE_DUMP)
    body = call(a, "GET", "/v1/status")[1]
    assert body["adblock"] is expected
    assert body["dns"] == ["10.66.0.1"]


def test_load_config_reads_adblock_and_dns(tmp_path):
    env = tmp_path / "agent.env"
    env.write_text(f"# managed\nTOKEN={TOKEN}\nDNS=10.66.0.1\nADBLOCK=1\n", encoding="utf-8")
    cfg = A.load_config(str(env))
    a = A.Agent({**cfg, "STATE_FILE": str(tmp_path / "peers.json")}, runner=lambda args: SAMPLE_DUMP)
    body = a.status()
    assert body["adblock"] is True and body["dns"] == ["10.66.0.1"]
    # An agent.env from before the adblock feature (no ADBLOCK line) reports false.
    env.write_text(f"TOKEN={TOKEN}\n", encoding="utf-8")
    assert A.Agent({**A.load_config(str(env)), "STATE_FILE": str(tmp_path / "p.json")},
                   runner=lambda args: SAMPLE_DUMP).status()["adblock"] is False


# ---- optional IPv6 ULA per peer (so clients' v6 gets a clean reject, not a hang) ----
def test_pool6_adds_matching_v6_and_status_reports_v4(cfg, wg):
    a = A.Agent({**cfg, "POOL6": "fd66:66::1/64"}, runner=wg)
    a.start()
    assert call(a, "PUT", f"/v1/peers/{urlkey(key(1))}", body={"allowed_ip": "10.66.1.2/32"})[0] == 200
    assert ["set", "wg0", "peer", key(1), "allowed-ips", "10.66.1.2/32,fd66:66::102/128"] in wg.calls
    assert a.peers == {key(1): "10.66.1.2/32"}  # the peer file stays IPv4-only
    assert a.status()["peers"][0]["allowed_ip"] == "10.66.1.2/32"


def test_parse_dump_prefers_v4_when_v6_listed_first():
    dump = f"P\t{SERVER_PUB}\t51820\toff\n{key(1)}\t(none)\t(none)\tfd66:66::5/128,10.66.0.5/32\t0\t0\t0\toff\n"
    assert A.parse_dump(dump)[1][0]["allowed_ip"] == "10.66.0.5/32"


@pytest.mark.parametrize("bad", ["10.0.0.1/8", "fd66:66::1/120", "nonsense"])
def test_bad_pool6_refused(cfg, wg, bad):
    with pytest.raises(ValueError):
        A.Agent({**cfg, "POOL6": bad}, runner=wg)


# ---- install.sh: syntax + rendered templates (via the --print-config dry run) ----
import re as _re  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


def _find_bash():
    for p in (r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files\Git\usr\bin\bash.exe"):
        if os.path.exists(p):
            return p
    found = shutil.which("bash")
    # On Windows, System32\bash.exe is the WSL launcher, not a usable bash.
    if found and "system32" not in found.lower():
        return found
    return None


BASH = _find_bash()
needs_bash = pytest.mark.skipif(BASH is None, reason="bash not available")


def _bash(*args, stdin=None):
    env = {k: v for k, v in os.environ.items() if k not in ("POOL", "POOL6", "DNS", "PUBLIC_IP", "WAN_IF")}
    return subprocess.run([BASH, *args], cwd=HERE, capture_output=True, text=True, timeout=60,
                          input=stdin, env=env)


def _render(*flags) -> dict:
    res = _bash("install.sh", "--print-config", *flags)
    assert res.returncode == 0, res.stderr
    files, cur = {}, None
    for line in res.stdout.splitlines():
        m = _re.fullmatch(r"##### (\S+) #####", line)
        if m:
            cur = m.group(1)
            files[cur] = ""
        elif cur:
            files[cur] += line + "\n"
    return files


@needs_bash
def test_install_sh_syntax():
    res = _bash("-n", "install.sh")
    assert res.returncode == 0, res.stderr


@needs_bash
def test_install_sh_lf_endings():
    with open(os.path.join(HERE, "install.sh"), "rb") as f:
        assert b"\r\n" not in f.read()


@needs_bash
@pytest.mark.parametrize("flags", [(), ("--adblock",)])
def test_nftables_hardening_rules(flags):
    nft = _render(*flags)["/etc/nftables.conf"]
    # IPv6 from clients is answered with an ICMP unreachable, never forwarded.
    assert _re.search(r'iifname "wg0" meta nfproto ipv6 fib daddr type != local reject with icmpx type no-route', nft)
    assert _re.search(r"chain forward \{[^}]*meta nfproto ipv6 reject", nft)
    # Peers cannot reach each other or the node's own services.
    assert 'iifname "wg0" oifname "wg0" reject' in nft
    assert 'iifname "wg0" reject with icmpx type admin-prohibited' in nft
    # No outbound SMTP abuse; MSS clamped; metadata/private ranges unreachable.
    assert 'iifname "wg0" tcp dport 25 reject' in nft
    assert "tcp option maxseg size set rt mtu" in nft
    assert "169.254.0.0/16" in nft
    assert 'oifname "eth0" ip saddr 10.66.0.0/16 masquerade' in nft
    # The isolation/25/v6 rules come before the catch-all accept for client traffic.
    fwd = nft[nft.index("chain forward"):nft.index("chain output")]
    accept = fwd.index('oifname "eth0" ip saddr 10.66.0.0/16 accept')
    for rule in ("client isolation", "smtp 25 blocked", "kiwi: v6 reject"):
        assert fwd.index(rule) < accept
    # No logging of any kind in the firewall.
    for line in nft.splitlines():
        if not line.strip().startswith("#"):
            assert not _re.search(r"(^|\s)log(\s|$)", line), line


@needs_bash
@pytest.mark.parametrize("flags", [(), ("--adblock",)])
def test_resolver_always_on_and_dns_never_changes(flags):
    # Toggling ad blocking must never change the DNS that existing device configs point at.
    files = _render(*flags)
    env = files["/etc/kiwi-vpn/agent.env"]
    lines = env.splitlines()
    assert "DNS=10.66.0.1" in lines
    assert ("ADBLOCK=1" if flags else "ADBLOCK=0") in lines
    nft = files["/etc/nftables.conf"]
    for proto in ("udp", "tcp"):
        assert f'iifname "wg0" ip saddr 10.66.0.0/16 ip daddr 10.66.0.1 {proto} dport 53 accept' in nft
    assert nft.index("dport 53") < nft.index("tunnel to host")  # DNS accepted before the reject
    ub = files["/etc/unbound/unbound.conf.d/kiwi-vpn.conf"]
    ub_lines = [ln.strip() for ln in ub.splitlines()]
    for must in ("interface: 10.66.0.1", "port: 53", "access-control: 0.0.0.0/0 refuse",
                 "access-control: 10.66.0.0/16 allow", "verbosity: 0", "log-queries: no",
                 "log-replies: no", "log-local-actions: no", "log-servfail: no",
                 'include: "/etc/unbound/kiwi-vpn-blocklist.conf"'):
        assert must in ub_lines, must
    assert _re.findall(r"^\s*interface:", ub, _re.M) == ["  interface:"]  # only the tunnel address
    # The blocklist updater + daily timer exist only with --adblock.
    has_updater = any("blocklist" in path for path in files)
    assert has_updater is bool(flags)


@needs_bash
def test_host_basics():
    files = _render()
    sysctl = files["/etc/sysctl.d/99-kiwi-vpn.conf"]
    assert "net.ipv4.ip_forward = 1" in sysctl and "net.ipv6.conf.all.forwarding = 0" in sysctl
    assert 'APT::Periodic::Unattended-Upgrade "1";' in files["/etc/apt/apt.conf.d/20auto-upgrades"]
    assert "Address = 10.66.0.1/16, fd66:66::1/64" in files["/etc/wireguard/wg0.conf"]


@needs_bash
def test_adblock_blocklist_updater():
    files = _render("--adblock")
    upd = files["/usr/local/sbin/kiwi-vpn-blocklist-update"]
    assert "--proto '=https'" in upd and "always_nxdomain" in upd
    assert "unbound-checkconf" in upd and "MIN_BYTES" in upd and "MAX_BYTES" in upd
    assert '.prev' in upd  # previous list kept/restored on failure
    assert "OnCalendar=daily" in files["/etc/systemd/system/kiwi-vpn-blocklist.timer"]


@needs_bash
def test_generated_scripts_parse():
    files = _render("--adblock")
    for path in ("/usr/local/bin/kiwi-vpn-doctor", "/usr/local/sbin/kiwi-vpn-blocklist-update"):
        res = _bash("-n", stdin=files[path])
        assert res.returncode == 0, (path, res.stderr)
    doctor = files["/usr/local/bin/kiwi-vpn-doctor"]
    assert "/v1/health" in doctor and "latest-handshakes" in doctor
    # Never reads keys, endpoints (client IPs) or the full dump.
    assert not _re.search(r"wg show \S+ (endpoints|dump|public-key|private-key|allowed-ips)", doctor)
    assert "wg show \"$WG_IF\" peers" in doctor  # peers are only counted


@needs_bash
def test_update_refuses_setting_changes():
    res = _bash("install.sh", "--update", "--adblock")
    assert res.returncode != 0 and "--update never changes settings" in res.stderr


@needs_bash
def test_blocklist_conversion():
    awk = _re.search(r"awk '(\{ sub.*?)' \"\$tmp/hosts\"", _render("--adblock")["/usr/local/sbin/kiwi-vpn-blocklist-update"], _re.S).group(1)
    hosts = ("# StevenBlack\r\n127.0.0.1 localhost\r\n0.0.0.0 0.0.0.0\r\n0.0.0.0 Ads.Example.com # c\r\n"
             "0.0.0.0 bad..name\r\n0.0.0.0 t_rack.example.co.uk\r\n::1 ip6-localhost\r\n"
             "0.0.0.0 localhost.localdomain\r\n0.0.0.0 -bad.com\r\n0.0.0.0 ads.example.com\r\n")
    res = _bash("-c", 'awk "$1" | sort -u', "_", awk, stdin=hosts)
    assert res.returncode == 0, res.stderr
    assert res.stdout.split() == ["ads.example.com", "t_rack.example.co.uk"]


# ---- address6 in status + the IPv4 -> IPv6 mapping rule the backend mirrors ----
def test_status_address6(cfg):
    assert A.Agent(cfg, runner=lambda a: SAMPLE_DUMP).status()["address6"] is None
    a = A.Agent({**cfg, "POOL6": "fd66:66::1/64"}, runner=lambda a: SAMPLE_DUMP)
    assert a.status()["address6"] == "fd66:66::1/64"


@pytest.mark.parametrize("ip4,ip6", [("10.66.0.5/32", "fd66:66::5/128"), ("10.66.1.2/32", "fd66:66::102/128"),
                                     ("10.66.255.254/32", "fd66:66::fffe/128")])
def test_ipv6_for_rule(ip4, ip6):
    assert A.ipv6_for(ip4, "10.66.0.1/16", "fd66:66::1/64") == ip6


def test_ipv6_for_rejects_outside_pool():
    with pytest.raises(ValueError):
        A.ipv6_for("10.67.0.5/32", "10.66.0.1/16", "fd66:66::1/64")


def _render_env(env_add, *flags) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in ("POOL", "POOL6", "DNS", "PUBLIC_IP", "WAN_IF", "AGENT_ALLOW", "WITH_PROXY")}
    env.update(env_add)
    res = subprocess.run([BASH, "install.sh", "--print-config", *flags], cwd=HERE, capture_output=True, text=True, timeout=60, env=env)
    assert res.returncode == 0, res.stderr
    return res.stdout


@needs_bash
def test_new_nodes_keep_the_agent_port_for_the_backend_and_open_the_proxy():
    out = _render_env({"AGENT_ALLOW": "152.55.180.240,162.220.234.242/32", "WITH_PROXY": "1"})
    nft = out[out.index("##### /etc/nftables.conf"):out.index("##### /etc/kiwi-vpn/agent.env")]
    assert 'ip saddr { 152.55.180.240, 162.220.234.242/32 } tcp dport 8443 accept' in nft
    assert "\n    tcp dport 8443 accept" not in nft  # never open to everyone as well
    assert 'tcp dport { 80, 443 } accept' in nft
    # the browser proxy's own table is loaded with the ruleset, so a reload (which flushes everything) keeps it
    assert nft.rstrip().endswith('include "/etc/kiwi-vpn/nft.d/*.nft"')
    assert nft.index("flush ruleset") < nft.index("include")


@needs_bash
def test_existing_behaviour_without_the_new_options():
    nft = _render_env({})
    assert "\n    tcp dport 8443 accept" in nft
    assert "dport { 80, 443 }" not in nft


@needs_bash
def test_a_bad_agent_allowlist_is_refused():
    env = {k: v for k, v in os.environ.items()}
    env["AGENT_ALLOW"] = "1.2.3.4; rm -rf /"
    res = subprocess.run([BASH, "install.sh", "--print-config"], cwd=HERE, capture_output=True, text=True, timeout=60, env=env)
    assert res.returncode != 0 and "AGENT_ALLOW" in res.stderr


def test_unattended_installs_never_print_the_agent_token():
    src = open(os.path.join(HERE, "install.sh"), encoding="utf-8").read()
    assert "agent_token:  $SHOW_TOKEN" in src and "agent_token:  $TOKEN" not in src
    assert '(umask 077; printf' in src and "/root/kiwi-node.txt" in src


def test_proxy_on_an_nftables_node_has_the_same_limits_as_on_a_shared_node():
    src = open(os.path.join(HERE, "install-proxy.sh"), encoding="utf-8").read()
    nft = src[src.index('cat > "$NFT_FILE" <<EOF'):src.index('nft -c -f "$NFT_FILE"')]
    assert 'meta skuid "$USER_NAME" jump guard' in nft
    for must in ("169.254.0.0/16", "10.0.0.0/8", "127.0.0.0/8", "ip daddr $PUBLIC_IP reject", "{ 25, 465, 587 }", "oif lo reject", "ip6 daddr { $V6_DENY } reject"):
        assert must in nft, must
    assert 'V6_DENY="$(echo "$OWN_NETS"' in src  # the server's own IPv6 networks are refused here too
    assert "delete table inet kiwi_proxy" in nft  # loading it twice never doubles the rules


def test_no_pipeline_that_fails_a_fresh_install():
    # install.sh runs with pipefail: a `$(cmd file 2>/dev/null | ...)` whose file doesn't exist yet stops a fresh install silently
    import re
    for name in ("install.sh", "install-proxy.sh"):
        for n, line in enumerate(open(os.path.join(HERE, name), encoding="utf-8"), 1):
            if re.search(r"\$\([^()]*2>/dev/null\s*\|", line):
                assert "|| true" in line, f"{name}:{n}: {line.strip()}"
