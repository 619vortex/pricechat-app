#!/usr/bin/env python3
"""Kiwi VPN browser proxy: an HTTPS forward proxy for the browser extension (stdlib only). See vpn-node/README.md.

Browsers can't run WireGuard, so the extension sends its traffic here over TLS. Logins are in a file the agent writes: username ->
sha256(password). A login that disappears from the file is cut off within seconds, including its open connections.

Privacy: nothing is logged per connection: no client address, no destination, no URL. Only bytes per login are counted, for the
monthly data cap. Destinations are resolved here, so the browser's DNS questions stay inside the tunnel. With RESOLVER set (the
installer sets 127.0.0.1, a no-logs unbound on this server) names are asked of that resolver alone, never the hosting provider's.

Load: only logged-in connections count toward MAX_TOTAL; before login each source address may hold PRE_LOGIN_PER_IP connections
and has HEAD_TIMEOUT seconds to send its request, so idle strangers can't fill the server for paying users. The address is used
for that count in memory only, never written anywhere.

Safety: the proxy reaches the public internet and nothing else. Every resolved address is checked before connecting, so a hostname
that points at a private address is refused too: never this host, wg0, wg1, a private range or the cloud metadata service. install-proxy.sh
repeats that in the firewall for this service's user.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import hmac
import ipaddress
import json
import os
import re
import signal
import socket
import ssl
import sys
import tempfile
import time
from typing import Callable, Optional
from urllib.parse import urlsplit

CONFIG_FILE = "/etc/kiwi-vpn-proxy/proxy.env"
DEFAULTS = {
    "PROXY_BIND": "0.0.0.0",
    "PROXY_PORT": "443",
    "TLS_CERT": "/etc/kiwi-vpn-proxy/tls/fullchain.pem",
    "TLS_KEY": "/etc/kiwi-vpn-proxy/tls/privkey.pem",
    "USERS_FILE": "/var/lib/kiwi-vpn-proxy/users.json",
    "USAGE_FILE": "/var/lib/kiwi-vpn-proxy/usage.json",
    "PUBLIC_IPS": "",
    # This server's own networks, refused like PUBLIC_IPS but never shown (comma-separated CIDRs, e.g. the provider's IPv6 /64).
    "OWN_NETS": "",
    # The resolver to ask, e.g. 127.0.0.1 (install-proxy.sh's unbound). Empty = the system's (getaddrinfo), as before.
    "RESOLVER": "",
}
CHECK_HOST = "check.kiwi-vpn.invalid"  # the extension's "am I really going through the tunnel?" request; answered here, never resolved
BLOCKED_PORTS = frozenset({25, 465, 587})  # no mail
MAX_HEAD = 16 * 1024
HEAD_TIMEOUT = 5.0
TLS_HANDSHAKE_TIMEOUT = 10.0
CONNECT_TIMEOUT = 10.0
IDLE_TIMEOUT = 300.0
DRAIN_AFTER_CLIENT_EOF = 30.0
CHUNK = 64 * 1024
MAX_PER_USER = 300
MAX_TOTAL = 3000  # logged-in connections
PRE_LOGIN_PER_IP = 50  # connections from one address that haven't logged in yet
DNS_TIMEOUT = 4.0
RELOAD_EVERY = 2.0
FLUSH_EVERY = 15.0
_HOST = re.compile(r"^[A-Za-z0-9._-]{1,253}$")
_HOP_BY_HOP = {b"proxy-authorization", b"proxy-connection", b"connection", b"keep-alive", b"te", b"trailer", b"upgrade"}
_DUMMY = "0" * 64


def log(msg: str) -> None:
    print(f"kiwi-vpn-proxy: {msg}", file=sys.stderr, flush=True)


def load_config(path: str = CONFIG_FILE) -> dict:
    cfg = dict(DEFAULTS)
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                cfg[k.strip()] = v.strip().strip('"').strip("'")
    return cfg


def parse_nets(raw: str) -> tuple:
    """'2a01:db8::/64, 198.51.100.0/24' -> networks. A bad entry raises: a typo must stop the proxy, not leave a hole."""
    return tuple(ipaddress.ip_network(x.strip(), strict=False) for x in raw.split(",") if x.strip())


def public_only(ip: str, own: frozenset[str] = frozenset(), nets: tuple = ()) -> bool:
    """A public internet address that isn't this host (its addresses in `own`, its networks in `nets`). Anything unparseable is refused."""
    try:
        a = ipaddress.ip_address(ip.split("%", 1)[0])
    except ValueError:
        return False
    mapped = getattr(a, "ipv4_mapped", None)
    if mapped is not None:
        a = mapped
    return a.is_global and not a.is_multicast and str(a) not in own and not any(a.version == n.version and a in n for n in nets)


def parse_target(raw: str) -> Optional[tuple[str, int]]:
    """'host:port' or '[v6]:port' -> (host, port); None when it isn't one."""
    m = re.fullmatch(r"\[([0-9A-Fa-f:.]+)\]:(\d{1,5})", raw) or re.fullmatch(r"([^:\[\]]+):(\d{1,5})", raw)
    if not m:
        return None
    host, port = m.group(1), int(m.group(2))
    if not 1 <= port <= 65535 or (":" not in host and not _HOST.fullmatch(host)):
        return None
    return host, port


# ----- a small DNS client for RESOLVER (stdlib only): A + AAAA, UDP with a TCP retry when the answer is truncated -----
def _dns_question(name: str, qtype: int, qid: int) -> bytes:
    labels = name.rstrip(".").encode("idna").split(b".")
    if not labels or any(not 0 < len(x) < 64 for x in labels):
        raise UnicodeError("bad name")
    return (qid.to_bytes(2, "big") + b"\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00"
            + b"".join(bytes([len(x)]) + x for x in labels) + b"\x00" + qtype.to_bytes(2, "big") + b"\x00\x01")


def _skip_name(msg: bytes, i: int) -> int:
    while True:
        n = msg[i]
        if n == 0:
            return i + 1
        if n & 0xC0 == 0xC0:
            return i + 2
        i += 1 + n


def parse_dns_answer(msg: bytes, qid: int) -> tuple[bool, list[str]]:
    """(truncated, addresses) from a reply to `qid`. NXDOMAIN / no data = no addresses; anything malformed raises OSError."""
    try:
        if len(msg) < 12 or int.from_bytes(msg[:2], "big") != qid or not msg[2] & 0x80:
            raise OSError("not the answer")
        if msg[2] & 0x02:
            return True, []
        rcode = msg[3] & 0x0F
        if rcode == 3:
            return False, []
        if rcode != 0:
            raise OSError(f"resolver rcode {rcode}")
        qd, an = int.from_bytes(msg[4:6], "big"), int.from_bytes(msg[6:8], "big")
        i = 12
        for _ in range(qd):
            i = _skip_name(msg, i) + 4
        out = []
        for _ in range(an):
            i = _skip_name(msg, i)
            rtype, rlen = int.from_bytes(msg[i:i + 2], "big"), int.from_bytes(msg[i + 8:i + 10], "big")
            data = msg[i + 10:i + 10 + rlen]
            if len(data) != rlen or i + 10 > len(msg):
                raise OSError("short answer")
            if rtype == 1 and rlen == 4:
                out.append(str(ipaddress.IPv4Address(data)))
            elif rtype == 28 and rlen == 16:
                out.append(str(ipaddress.IPv6Address(data)))
            i += 10 + rlen
        return False, out
    except (IndexError, ValueError) as e:
        raise OSError("bad answer") from e


class _DnsUdp(asyncio.DatagramProtocol):
    def __init__(self, qid: int) -> None:
        self.qid = qid
        self.done: asyncio.Future = asyncio.get_running_loop().create_future()

    def datagram_received(self, data: bytes, addr) -> None:
        if not self.done.done() and len(data) >= 2 and int.from_bytes(data[:2], "big") == self.qid:
            self.done.set_result(data)

    def error_received(self, exc: Exception) -> None:
        if not self.done.done():
            self.done.set_exception(exc)


async def _dns_ask(server: str, name: str, qtype: int, port: int = 53) -> list[str]:
    qid = int.from_bytes(os.urandom(2), "big")
    q = _dns_question(name, qtype, qid)
    loop = asyncio.get_running_loop()
    transport, proto = await loop.create_datagram_endpoint(lambda: _DnsUdp(qid), remote_addr=(server, port))
    try:
        transport.sendto(q)
        truncated, ips = parse_dns_answer(await asyncio.wait_for(proto.done, DNS_TIMEOUT), qid)
    finally:
        transport.close()
    if not truncated:
        return ips
    r, w = await asyncio.wait_for(asyncio.open_connection(server, port), DNS_TIMEOUT)
    try:
        w.write(len(q).to_bytes(2, "big") + q)
        await w.drain()
        size = int.from_bytes(await asyncio.wait_for(r.readexactly(2), DNS_TIMEOUT), "big")
        return parse_dns_answer(await asyncio.wait_for(r.readexactly(size), DNS_TIMEOUT), qid)[1]
    finally:
        w.close()


async def dns_lookup(server: str, host: str, port: int = 53) -> list[str]:
    """IPv4 then IPv6 addresses of `host`, asked of `server` only. An address literal is returned as it is."""
    try:
        return [str(ipaddress.ip_address(host.strip("[]")))]
    except ValueError:
        pass
    got = await asyncio.gather(_dns_ask(server, host, 1, port), _dns_ask(server, host, 28, port), return_exceptions=True)
    ips = [ip for g in got if isinstance(g, list) for ip in g]
    if not ips and any(isinstance(g, BaseException) for g in got):
        raise OSError("resolver failed")
    return ips


class Refused(Exception):
    def __init__(self, status: bytes) -> None:
        super().__init__(status.decode())
        self.status = status


class Proxy:
    def __init__(self, cfg: dict, dest_ok: Optional[Callable[[str], bool]] = None,
                 resolve: Optional[Callable[[str, int], "asyncio.Future"]] = None) -> None:
        self.cfg = {**DEFAULTS, **cfg}
        # the first one is the exit address the check request shows (a floating IP goes first); all of them are refused
        self.own_list = [x.strip() for x in self.cfg["PUBLIC_IPS"].split(",") if x.strip()]
        self.own = frozenset(self.own_list)
        self.own_nets = parse_nets(self.cfg["OWN_NETS"])
        self.dest_ok = dest_ok or (lambda ip: public_only(ip, self.own, self.own_nets))
        resolver = self.cfg["RESOLVER"].strip()
        if resolver:
            ipaddress.ip_address(resolver)  # a typo must stop the proxy, not fall back to the provider's resolver
        self.resolve = resolve or ((lambda host, port: dns_lookup(resolver, host)) if resolver else self._resolve)
        self.users: dict[str, str] = {}
        self.counts: dict[str, list[int]] = {}  # username -> [from the client, to the client]; only ever grow while running
        self.live: dict[str, set[asyncio.Task]] = {}
        self.total = 0  # logged-in connections
        self.pending: dict[str, int] = {}  # source address -> connections not logged in yet (memory only)
        self._stamp: Optional[tuple[int, int]] = None
        self._dirty = False

    # ----- logins and usage -----
    def reload_users(self) -> None:
        """Pick up the agent's file when it changed; a login that's gone loses its open connections now."""
        path = self.cfg["USERS_FILE"]
        try:
            st = os.stat(path)
        except FileNotFoundError:
            stamp = None
        else:
            stamp = (st.st_mtime_ns, st.st_size)
        if stamp == self._stamp:
            return
        try:
            with open(path, encoding="utf-8") as f:
                raw = json.load(f).get("users") or {} if stamp else {}
        except FileNotFoundError:
            raw = {}
        except (OSError, ValueError):
            log("the logins file couldn't be read: keeping the last good one")
            return
        self._stamp = stamp
        self.users = {str(k): str(v) for k, v in raw.items()}
        for name in list(self.live):
            if name not in self.users:
                for task in list(self.live.get(name, ())):
                    task.cancel()
        for name in list(self.counts):
            if name not in self.users:
                del self.counts[name]
                self._dirty = True

    def flush_usage(self) -> None:
        if not self._dirty:
            return
        path = self.cfg["USAGE_FILE"]
        d = os.path.dirname(os.path.abspath(path))
        fd, tmp = tempfile.mkstemp(dir=d, prefix=".usage.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"users": {k: {"rx_bytes": v[0], "tx_bytes": v[1]} for k, v in self.counts.items()}}, f)
            os.chmod(tmp, 0o640)
            os.replace(tmp, path)
            self._dirty = False
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def login(self, header: Optional[bytes]) -> Optional[str]:
        """The username a Proxy-Authorization header proves, or None. Same work whether or not the name exists."""
        name, secret = "", b""
        if header and header[:6].lower() == b"basic ":
            try:
                name_b, _, secret = base64.b64decode(header[6:].strip(), validate=True).partition(b":")
                name = name_b.decode()
            except (binascii.Error, ValueError):
                name, secret = "", b""
        digest = hashlib.sha256(secret).hexdigest()
        if not hmac.compare_digest(digest, self.users.get(name, _DUMMY)):
            self.reload_users()  # a login made a moment ago must work at once, not at the next reload
        ok = hmac.compare_digest(digest, self.users.get(name, _DUMMY))
        return name if ok and name in self.users else None

    # ----- one client connection -----
    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        name: Optional[str] = None
        upstream: Optional[asyncio.StreamWriter] = None
        counted = False
        peer = writer.get_extra_info("peername")
        src = str(peer[0]) if isinstance(peer, tuple) and peer else "?"
        waiting = self.pending.get(src, 0)
        if waiting >= PRE_LOGIN_PER_IP:  # too many unauthenticated connections from one address: close without a word
            writer.close()
            return
        self.pending[src] = waiting + 1
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), HEAD_TIMEOUT)
            lines = head[:-4].split(b"\r\n")
            parts = lines[0].split(b" ")
            if len(parts) != 3 or not parts[2].startswith(b"HTTP/1."):
                raise Refused(b"400 Bad Request")
            method, target = parts[0], parts[1].decode("ascii", "replace")
            headers = [ln.split(b":", 1) for ln in lines[1:] if b":" in ln]
            auth = next((v.strip() for k, v in headers if k.strip().lower() == b"proxy-authorization"), None)
            is_proxy_form = method == b"CONNECT" or target.lower().startswith("http://")
            if not is_proxy_form:  # someone opened this host directly: it isn't a website, and this doesn't invite a password box
                raise Refused(b"404 Not Found")
            name = self.login(auth)
            self._release(src)
            src = ""
            if name is None:
                writer.write(b'HTTP/1.1 407 Proxy Authentication Required\r\nProxy-Authenticate: Basic realm="Kiwi VPN"\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')
                await writer.drain()
                return
            conns = self.live.setdefault(name, set())
            if len(conns) >= MAX_PER_USER or self.total >= MAX_TOTAL:
                raise Refused(b"503 Service Unavailable")
            conns.add(task)
            self.total += 1
            counted = True
            count = self.counts.setdefault(name, [0, 0])
            if method == b"CONNECT":
                t = parse_target(target)
                if t is None:
                    raise Refused(b"400 Bad Request")
                up_r, upstream = await self.open(*t)
                writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                await writer.drain()
                first = b""
            else:
                u = urlsplit(target)
                if (u.hostname or "").lower() == CHECK_HOST:
                    await self._answer_check(writer)
                    return
                if u.hostname is None or u.scheme.lower() != "http":
                    raise Refused(b"400 Bad Request")
                try:
                    port = u.port or 80
                except ValueError:
                    raise Refused(b"400 Bad Request") from None
                up_r, upstream = await self.open(u.hostname, port)
                first = self._origin_request(parts, u, headers)
            await self._relay(reader, writer, up_r, upstream, count, first)
        except Refused as e:
            try:
                writer.write(b"HTTP/1.1 " + e.status + b"\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                await writer.drain()
            except (ConnectionError, OSError):
                pass
        except (asyncio.TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, asyncio.CancelledError,
                ConnectionError, OSError, ssl.SSLError):
            pass  # never logged: there's nothing here worth keeping about a person's connection
        finally:
            if src:
                self._release(src)
            if counted:
                self.total -= 1
            if name is not None and name in self.live:
                self.live[name].discard(task)
                if not self.live[name]:
                    del self.live[name]
            for w in (upstream, writer):
                if w is not None:
                    w.close()

    def _release(self, src: str) -> None:
        n = self.pending.get(src, 0) - 1
        if n > 0:
            self.pending[src] = n
        else:
            self.pending.pop(src, None)

    async def _answer_check(self, writer: asyncio.StreamWriter) -> None:
        body = json.dumps({"ok": True, "ip": self.own_list[0] if self.own_list else None}).encode()
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nCache-Control: no-store\r\nContent-Length: " + str(len(body)).encode() + b"\r\nConnection: close\r\n\r\n" + body)
        await writer.drain()

    @staticmethod
    def _origin_request(parts: list[bytes], u, headers: list[list[bytes]]) -> bytes:
        path = (u.path or "/") + (f"?{u.query}" if u.query else "")
        out = [parts[0] + b" " + path.encode("ascii", "replace") + b" " + parts[2]]
        seen_host = False
        for k, v in headers:
            key = k.strip().lower()
            if key in _HOP_BY_HOP:
                continue
            seen_host = seen_host or key == b"host"
            out.append(k + b":" + v)
        if not seen_host:
            out.append(b"Host: " + (u.netloc.rsplit("@", 1)[-1]).encode("ascii", "replace"))
        out.append(b"Connection: close")
        return b"\r\n".join(out) + b"\r\n\r\n"

    async def _resolve(self, host: str, port: int) -> list[str]:
        infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
        return [i[4][0] for i in infos]

    async def open(self, host: str, port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        if port in BLOCKED_PORTS:
            raise Refused(b"403 Forbidden")
        try:
            ips = await asyncio.wait_for(self.resolve(host, port), CONNECT_TIMEOUT)
        except (OSError, asyncio.TimeoutError, UnicodeError):
            raise Refused(b"502 Bad Gateway") from None
        allowed = [ip for ip in ips if self.dest_ok(ip)]
        if not allowed:
            raise Refused(b"403 Forbidden" if ips else b"502 Bad Gateway")
        for ip in allowed[:4]:  # connect to the address that was checked, not the name again
            try:
                return await asyncio.wait_for(asyncio.open_connection(ip, port), CONNECT_TIMEOUT)
            except (OSError, asyncio.TimeoutError):
                continue
        raise Refused(b"502 Bad Gateway")

    async def _relay(self, c_r: asyncio.StreamReader, c_w: asyncio.StreamWriter, u_r: asyncio.StreamReader, u_w: asyncio.StreamWriter,
                     count: list[int], first: bytes) -> None:
        last = time.monotonic()
        if first:
            u_w.write(first)
            await u_w.drain()

        async def pump(src: asyncio.StreamReader, dst: asyncio.StreamWriter, slot: int) -> None:
            nonlocal last
            while True:
                try:
                    data = await asyncio.wait_for(src.read(CHUNK), 30.0)
                except asyncio.TimeoutError:
                    if time.monotonic() - last > IDLE_TIMEOUT:  # quiet in both directions
                        return
                    continue
                if not data:
                    return
                last = time.monotonic()
                count[slot] += len(data)
                self._dirty = True
                dst.write(data)
                await dst.drain()

        up = asyncio.ensure_future(pump(c_r, u_w, 0))
        down = asyncio.ensure_future(pump(u_r, c_w, 1))
        try:
            done, _ = await asyncio.wait({up, down}, return_when=asyncio.FIRST_COMPLETED)
            if up in done and down not in done:  # the client finished sending: the answer may still be coming
                if u_w.can_write_eof():
                    u_w.write_eof()
                await asyncio.wait({down}, timeout=DRAIN_AFTER_CLIENT_EOF)
        finally:
            for t in (up, down):
                t.cancel()
            await asyncio.gather(up, down, return_exceptions=True)

    # ----- running -----
    async def housekeeping(self) -> None:
        next_flush = time.monotonic() + FLUSH_EVERY
        while True:
            await asyncio.sleep(RELOAD_EVERY)
            try:
                self.reload_users()
                if time.monotonic() >= next_flush:
                    next_flush = time.monotonic() + FLUSH_EVERY
                    self.flush_usage()
            except OSError as e:
                log(f"housekeeping: {type(e).__name__}")

    async def serve(self, ctx: Optional[ssl.SSLContext], host: str, port: int) -> asyncio.AbstractServer:
        self.reload_users()
        kwargs = {"ssl": ctx, "ssl_handshake_timeout": TLS_HANDSHAKE_TIMEOUT} if ctx is not None else {}
        return await asyncio.start_server(self.handle, host, port, limit=MAX_HEAD, **kwargs)


async def amain() -> None:
    cfg = load_config(os.environ.get("KIWI_PROXY_CONFIG", CONFIG_FILE))
    proxy = Proxy(cfg)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(cfg["TLS_CERT"], cfg["TLS_KEY"])
    server = await proxy.serve(ctx, cfg["PROXY_BIND"], int(cfg["PROXY_PORT"]))
    keeper = asyncio.ensure_future(proxy.housekeeping())
    stop = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        asyncio.get_running_loop().add_signal_handler(sig, stop.set)
    log(f"listening on port {cfg['PROXY_PORT']}")
    await stop.wait()
    keeper.cancel()
    server.close()
    proxy.flush_usage()
    log("stopped")


if __name__ == "__main__":
    asyncio.run(amain())
