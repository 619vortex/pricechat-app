#!/usr/bin/env python3
"""Kiwi VPN node agent.

A tiny HTTPS API (stdlib only) that the Price Chat backend uses to add/remove
WireGuard peers on this box. See vpn-node/README.md.

Privacy: the agent keeps only peer public keys + tunnel IPs. It never logs
client IPs, endpoints or traffic (the request logger is disabled and the
endpoint column of `wg show dump` is never read).
"""
from __future__ import annotations

import base64
import binascii
import hmac
import ipaddress
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Optional

CONFIG_FILE = "/etc/kiwi-vpn/agent.env"
MAX_BODY = 4096
MAX_CONNECTIONS = 32  # request threads at once; more are closed at once (the backend makes a few at a time)
DEFAULTS = {
    "TOKEN": "",
    "POOL": "10.66.0.1/16",
    "ENDPOINT_HOST": "",
    "LISTEN_PORT": "51820",
    "DNS": "1.1.1.1",
    "ADBLOCK": "0",
    # Optional IPv6 ULA on wg0 (e.g. fd66:66::1/64). Each peer then also gets the /128 at the
    # same offset as its IPv4 (10.66.0.5 -> fd66:66::5), so a client that is given that
    # address gets a fast ICMPv6 "no route" from the node's firewall instead of a hang.
    "POOL6": "",
    "AGENT_BIND": "0.0.0.0",
    "AGENT_PORT": "8443",
    "TLS_CERT": "/etc/kiwi-vpn/tls.crt",
    "TLS_KEY": "/etc/kiwi-vpn/tls.key",
    "WG_INTERFACE": "wg0",
    "STATE_FILE": "/var/lib/kiwi-vpn/peers.json",
    # The browser proxy (proxy.py), if this node runs one: its public name and port, and the files shared with it. Empty = none.
    "PROXY_HOST": "",
    "PROXY_PORT": "443",
    "PROXY_USERS_FILE": "/var/lib/kiwi-vpn-proxy/users.json",
    "PROXY_USAGE_FILE": "/var/lib/kiwi-vpn-proxy/usage.json",
}

Runner = Callable[[list], str]


def ipv6_for(ip4: str, pool: str, pool6: str) -> str:
    """The IPv6 /128 that belongs to a peer's IPv4 /32 (the backend mirrors this rule).

    Rule: offset = int(peer IPv4) - int(network address of POOL);
          IPv6   = network address of POOL6 + offset, as a /128.
    Example with POOL=10.66.0.1/16, POOL6=fd66:66::1/64:
        10.66.0.5/32   -> fd66:66::5/128       (offset 5)
        10.66.1.2/32   -> fd66:66::102/128     (offset 258 = 0x102)
        10.66.255.254/32 -> fd66:66::fffe/128  (offset 65534)
    A client config then uses `Address = <v4>/32, <v6>/128`.
    """
    v4 = ipaddress.ip_interface(ip4).ip
    net4 = ipaddress.ip_interface(pool).network
    net6 = ipaddress.ip_interface(pool6).network
    offset = int(v4) - int(net4.network_address)
    if v4 not in net4 or offset >= net6.num_addresses:
        raise ValueError("IPv4 outside POOL or POOL6 too small")
    return f"{net6.network_address + offset}/128"


def log(msg: str) -> None:
    print(f"kiwi-vpn-agent: {msg}", file=sys.stderr, flush=True)


def load_config(path: str = CONFIG_FILE) -> dict:
    """KEY=VALUE file (comments/blank lines ignored, optional quotes)."""
    cfg = dict(DEFAULTS)
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            cfg[k.strip()] = v.strip().strip('"').strip("'")
    return cfg


def run_wg(args: list) -> str:
    """Run `wg <args>` without a shell; raises on failure."""
    res = subprocess.run(["wg", *args], capture_output=True, text=True, timeout=15)
    if res.returncode != 0:
        raise RuntimeError(f"wg {args[0] if args else ''} failed (rc={res.returncode})")
    return res.stdout


def truthy(value: str) -> bool:
    return str(value).strip().lower() in ("1", "true", "yes", "on")


class BadRequest(Exception):
    pass


def parse_key(raw: str) -> str:
    """Accept a standard or URL-safe base64 WireGuard key; return standard base64."""
    key = raw.replace("-", "+").replace("_", "/")
    if len(key) != 44 or not re.fullmatch(r"[A-Za-z0-9+/]{43}=", key):
        raise BadRequest("bad key")
    try:
        if len(base64.b64decode(key, validate=True)) != 32:
            raise BadRequest("bad key")
    except (binascii.Error, ValueError):
        raise BadRequest("bad key")
    return key


def parse_dump(text: str) -> tuple[dict, list]:
    """Parse `wg show <if> dump`. Returns (interface, peers).

    Interface line: private-key public-key listen-port fwmark
    Peer line: public-key preshared-key endpoint allowed-ips latest-handshake rx tx keepalive
    The endpoint (client IP) is deliberately ignored.
    """
    iface: dict = {"public_key": "", "listen_port": 0}
    peers: list = []
    lines = [ln for ln in text.splitlines() if ln.strip()]
    for i, line in enumerate(lines):
        f = line.split("\t")
        if i == 0 and len(f) == 4:
            iface = {"public_key": f[1], "listen_port": int(f[2]) if f[2].isdigit() else 0}
        elif len(f) >= 8:
            ips = [] if f[3] == "(none)" else f[3].split(",")
            v4 = [ip for ip in ips if "." in ip]  # the contract reports the IPv4 /32
            peers.append({
                "public_key": f[0],
                "allowed_ip": (v4 or ips or [""])[0],
                "rx_bytes": int(f[5]),
                "tx_bytes": int(f[6]),
                "latest_handshake": int(f[4]),
            })
    return iface, peers


class Agent:
    def __init__(self, cfg: dict, runner: Runner = run_wg):
        self.cfg = {**DEFAULTS, **cfg}
        self.token = self.cfg["TOKEN"].encode()
        if len(self.token) < 32:
            raise ValueError("TOKEN must be at least 32 characters")
        self.address = ipaddress.ip_interface(self.cfg["POOL"])
        if self.address.version != 4:
            raise ValueError("POOL must be IPv4, e.g. 10.66.0.1/16")
        self.address6 = None
        if self.cfg["POOL6"].strip():
            self.address6 = ipaddress.ip_interface(self.cfg["POOL6"].strip())
            if (self.address6.version != 6
                    or self.address6.network.num_addresses < self.address.network.num_addresses):
                raise ValueError("POOL6 must be an IPv6 prefix at least as large as POOL")
        self.adblock = truthy(self.cfg["ADBLOCK"])
        self.iface = self.cfg["WG_INTERFACE"]
        self.state_file = self.cfg["STATE_FILE"]
        self.run = runner
        self.lock = threading.Lock()
        self.peers: dict = {}  # standard-base64 key -> "10.66.0.5/32"

    # ----- validation -----
    def parse_ip(self, raw) -> str:
        if not isinstance(raw, str) or not raw.endswith("/32"):
            raise BadRequest("bad ip")
        try:
            ip = ipaddress.ip_interface(raw)
        except ValueError:
            raise BadRequest("bad ip")
        net = self.address.network
        if (ip.version != 4 or ip.ip not in net or ip.ip in
                (net.network_address, net.broadcast_address, self.address.ip)):
            raise BadRequest("ip outside pool")
        return f"{ip.ip}/32"

    # ----- persistence -----
    def load_state(self) -> None:
        try:
            with open(self.state_file, encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            data = {}
        peers = {}
        for k, ip in (data.get("peers") or {}).items():
            try:
                peers[parse_key(k)] = self.parse_ip(ip)
            except BadRequest:
                log("skipping an invalid entry in the peer file")
        self.peers = peers

    def save_state(self) -> None:
        d = os.path.dirname(os.path.abspath(self.state_file))
        os.makedirs(d, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=d, prefix=".peers.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"peers": self.peers}, f, indent=1, sort_keys=True)
                f.flush()
                os.fsync(f.fileno())
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.state_file)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        if hasattr(os, "O_DIRECTORY"):
            dfd = os.open(d, os.O_DIRECTORY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)

    # ----- wg operations -----
    def allowed_ips(self, ip: str) -> str:
        """The kernel allowed-ips for a peer: its /32, plus the matching IPv6 /128 if POOL6 is set."""
        if self.address6 is None:
            return ip
        return f"{ip},{ipv6_for(ip, str(self.address), str(self.address6))}"

    def _wg_add(self, key: str, ip: str) -> None:
        self.run(["set", self.iface, "peer", key, "allowed-ips", self.allowed_ips(ip)])

    def _wg_remove(self, key: str) -> None:
        self.run(["set", self.iface, "peer", key, "remove"])

    def start(self) -> None:
        """Load the peer file and make the kernel match it."""
        with self.lock:
            self.load_state()
            _, live = parse_dump(self.run(["show", self.iface, "dump"]))
            for p in live:
                if p["public_key"] not in self.peers:
                    self._wg_remove(p["public_key"])
            for key, ip in self.peers.items():
                self._wg_add(key, ip)
        log(f"started with {len(self.peers)} peers")

    def put_peer(self, raw_key: str, body: dict) -> None:
        key = parse_key(raw_key)
        ip = self.parse_ip(body.get("allowed_ip") if isinstance(body, dict) else None)
        with self.lock:
            # An IP can belong to one peer only; the newest assignment wins.
            for other, other_ip in list(self.peers.items()):
                if other != key and other_ip == ip:
                    self._wg_remove(other)
                    del self.peers[other]
            if self.peers.get(key) != ip:
                self._wg_add(key, ip)
                self.peers[key] = ip
                self.save_state()
            else:
                self._wg_add(key, ip)  # cheap; heals kernel drift

    def delete_peer(self, raw_key: str) -> None:
        key = parse_key(raw_key)
        with self.lock:
            self._wg_remove(key)  # `wg set ... remove` is fine for unknown peers
            if self.peers.pop(key, None) is not None:
                self.save_state()

    # ----- browser proxy logins (proxy.py reads this file; only hashes, never a password) -----
    def _proxy_users(self) -> dict:
        try:
            with open(self.cfg["PROXY_USERS_FILE"], encoding="utf-8") as f:
                return json.load(f).get("users") or {}
        except FileNotFoundError:
            return {}

    def _save_proxy_users(self, users: dict) -> None:
        path = self.cfg["PROXY_USERS_FILE"]
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(path)), prefix=".users.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"users": users}, f, sort_keys=True)
            os.chmod(tmp, 0o640)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def put_proxy_user(self, username: str, body: dict) -> None:
        secret_hash = body.get("secret_hash") if isinstance(body, dict) else None
        if not isinstance(secret_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", secret_hash):
            raise BadRequest("bad hash")
        with self.lock:
            users = self._proxy_users()
            if users.get(username) != secret_hash:
                users[username] = secret_hash
                self._save_proxy_users(users)

    def delete_proxy_user(self, username: str) -> None:
        with self.lock:
            users = self._proxy_users()
            if users.pop(username, None) is not None:
                self._save_proxy_users(users)

    def proxy_status(self) -> list:
        """Every login on this node with the bytes the proxy counted since it started (like a WireGuard peer's counters)."""
        try:
            with open(self.cfg["PROXY_USAGE_FILE"], encoding="utf-8") as f:
                usage = json.load(f).get("users") or {}
        except (FileNotFoundError, ValueError):
            usage = {}
        out = []
        for name in sorted(self._proxy_users()):
            u = usage.get(name) or {}
            out.append({"username": name, "rx_bytes": int(u.get("rx_bytes") or 0), "tx_bytes": int(u.get("tx_bytes") or 0)})
        return out

    def status(self) -> dict:
        iface, peers = parse_dump(self.run(["show", self.iface, "dump"]))
        proxy = {"proxy": {"host": self.cfg["PROXY_HOST"], "port": int(self.cfg["PROXY_PORT"])}, "proxy_users": self.proxy_status()} if self.cfg["PROXY_HOST"] else {}
        return {
            **proxy,
            "public_key": iface["public_key"],
            "listen_port": iface["listen_port"] or int(self.cfg["LISTEN_PORT"]),
            "endpoint_host": self.cfg["ENDPOINT_HOST"],
            "address": str(self.address),
            "dns": [d.strip() for d in self.cfg["DNS"].split(",") if d.strip()],
            "adblock": self.adblock,
            # wg0's IPv6 ULA, e.g. "fd66:66::1/64" (None if POOL6 is unset). Peers' v6 = ipv6_for().
            "address6": str(self.address6) if self.address6 is not None else None,
            "peers": peers,
        }

    # ----- HTTP-independent request handling -----
    def authorized(self, header: Optional[str]) -> bool:
        if not header or header[:7].lower() != "bearer ":
            hmac.compare_digest(b"x" * len(self.token), self.token)
            return False
        return hmac.compare_digest(header[7:].strip().encode(), self.token)

    def health_allowed(self, peer: str, auth: Optional[str]) -> bool:
        """/v1/health answers without the token only on loopback (kiwi-vpn-doctor, the installers); from anywhere else it
        needs the token like everything else. The backend never calls it: it reads /v1/status."""
        return _loopback(peer) or self.authorized(auth)

    def handle(self, method: str, path: str, auth: Optional[str], body: bytes) -> tuple[int, dict]:
        path = path.split("?", 1)[0]
        if path == "/v1/health":  # the HTTP handler has checked health_allowed() for callers from the network
            return (200, {"ok": True}) if method == "GET" else (405, {"error": "method"})
        if not self.authorized(auth):
            return 401, {"error": "unauthorized"}
        try:
            if path == "/v1/status":
                if method != "GET":
                    return 405, {"error": "method"}
                return 200, self.status()
            pm = re.fullmatch(r"/v1/proxy-users/([a-z0-9]{16,40})", path)
            if pm and self.cfg["PROXY_HOST"]:
                if method == "PUT":
                    try:
                        data = json.loads(body or b"{}")
                    except ValueError:
                        raise BadRequest("bad json")
                    self.put_proxy_user(pm.group(1), data)
                    return 200, {"ok": True}
                if method == "DELETE":
                    self.delete_proxy_user(pm.group(1))
                    return 200, {"ok": True}
                return 405, {"error": "method"}
            m = re.fullmatch(r"/v1/peers/([^/]+)", path)
            if not m:
                return 404, {"error": "not found"}
            if method == "PUT":
                try:
                    data = json.loads(body or b"{}")
                except ValueError:
                    raise BadRequest("bad json")
                self.put_peer(m.group(1), data)
                return 200, {"ok": True}
            if method == "DELETE":
                self.delete_peer(m.group(1))
                return 200, {"ok": True}
            return 405, {"error": "method"}
        except BadRequest as e:
            return 400, {"error": str(e)}
        except Exception as e:  # noqa: BLE001 - never leak details to the client
            log(f"error: {type(e).__name__}: {e}")
            return 500, {"error": "internal"}


def _loopback(peer: str) -> bool:
    try:
        a = ipaddress.ip_address(peer.split("%", 1)[0])
    except ValueError:
        return False
    return a.is_loopback or bool(getattr(a, "ipv4_mapped", None) and a.ipv4_mapped.is_loopback)


def make_handler(agent: Agent):
    class Handler(BaseHTTPRequestHandler):
        timeout = 15

        def version_string(self):
            return "kiwi-vpn-agent"

        def log_message(self, *args):  # never log client addresses or paths
            pass

        def _serve(self):
            length = self.headers.get("Content-Length")
            try:
                n = int(length) if length else 0
            except ValueError:
                n = -1
            if n < 0 or n > MAX_BODY:
                return self._send(413, {"error": "body too large"})
            body = self.rfile.read(n) if n else b""
            auth = self.headers.get("Authorization")
            if self.path.split("?", 1)[0] == "/v1/health" and not agent.health_allowed(str(self.client_address[0]), auth):
                return self._send(401, {"error": "unauthorized"})
            code, payload = agent.handle(self.command, self.path, auth, body)
            self._send(code, payload)

        def _send(self, code: int, payload: dict):
            data = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        do_GET = do_PUT = do_DELETE = do_POST = _serve

    return Handler


class AgentServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, addr, handler, ssl_ctx=None, max_connections: int = MAX_CONNECTIONS):
        super().__init__(addr, handler)
        self.ssl_ctx = ssl_ctx
        self._slots = threading.BoundedSemaphore(max_connections)

    def process_request(self, request, client_address):
        # A bounded number of threads: a flood of connections can't exhaust memory; the extra ones are closed at once.
        if not self._slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()

    def finish_request(self, request, client_address):
        # TLS handshake runs in the per-connection thread, not the accept loop.
        request.settimeout(15)
        if self.ssl_ctx is not None:
            try:
                request = self.ssl_ctx.wrap_socket(request, server_side=True)
            except OSError:
                return
        super().finish_request(request, client_address)

    def handle_error(self, request, client_address):  # don't print client address
        log(f"connection error: {sys.exc_info()[0].__name__}")


def main() -> None:
    import signal
    import ssl

    cfg = load_config(os.environ.get("KIWI_VPN_CONFIG", CONFIG_FILE))
    agent = Agent(cfg)
    agent.start()
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(cfg["TLS_CERT"], cfg["TLS_KEY"])
    server = AgentServer((cfg["AGENT_BIND"], int(cfg["AGENT_PORT"])), make_handler(agent), ctx)

    def stop(*_):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    log(f"listening on port {cfg['AGENT_PORT']}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        log("stopped")


if __name__ == "__main__":
    main()
