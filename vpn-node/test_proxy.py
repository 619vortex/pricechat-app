"""The real browser proxy over real TLS on 127.0.0.1, with a small origin server behind it.

    python -m pytest vpn-node/test_proxy.py -q -p no:cacheprovider
"""
import asyncio
import base64
import hashlib
import json
import os
import ssl
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import proxy as P  # noqa: E402

SECRET = "correct-horse-not-real"
HASH = hashlib.sha256(SECRET.encode()).hexdigest()


def basic(user="alice", secret=SECRET):
    return base64.b64encode(f"{user}:{secret}".encode()).decode()


@pytest.fixture(scope="module")
def certs(tmp_path_factory):
    d = tmp_path_factory.mktemp("tls")
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    import datetime as dt

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(days=1)).not_valid_after(now + dt.timedelta(days=2)).sign(key, hashes.SHA256()))
    (d / "c.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (d / "k.pem").write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return str(d / "c.pem"), str(d / "k.pem")


class Rig:
    """A proxy (loopback allowed only where a test says so) and an origin that records what it receives."""

    def __init__(self, tmp_path, certs, dest_ok=lambda ip: True, resolve=None, own="203.0.113.10"):
        self.users_file = tmp_path / "users.json"
        self.usage_file = tmp_path / "usage.json"
        self.users_file.write_text(json.dumps({"users": {"alice": HASH}}))
        self.proxy = P.Proxy({"USERS_FILE": str(self.users_file), "USAGE_FILE": str(self.usage_file), "PUBLIC_IPS": own}, dest_ok=dest_ok, resolve=resolve)
        self.certs = certs
        self.seen = []

    async def __aenter__(self):
        async def origin(r, w):
            head = await r.readuntil(b"\r\n\r\n")
            self.seen.append(head)
            body = b"hello from origin"
            w.write(b"HTTP/1.1 200 OK\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)
            await w.drain()
            w.close()

        self.origin = await asyncio.start_server(origin, "127.0.0.1", 0)
        self.origin_port = self.origin.sockets[0].getsockname()[1]
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(*self.certs)
        self.server = await self.proxy.serve(ctx, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc):
        for s in (self.server, self.origin):
            s.close()

    async def client(self):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return await asyncio.open_connection("127.0.0.1", self.port, ssl=ctx)

    async def ask(self, head: str):
        r, w = await self.client()
        w.write(head.encode())
        await w.drain()
        data = await asyncio.wait_for(r.read(65536), 5)
        w.close()
        return data


def run(coro):
    return asyncio.run(asyncio.wait_for(coro, 20))


def test_no_or_wrong_login_gets_407_and_no_connection(tmp_path, certs):
    async def go():
        async with Rig(tmp_path, certs) as rig:
            target = f"127.0.0.1:{rig.origin_port}"
            for auth in ("", f"Proxy-Authorization: Basic {basic(secret='wrong')}\r\n", f"Proxy-Authorization: Basic {basic(user='bob')}\r\n",
                         "Proxy-Authorization: Basic !!!not-base64\r\n", "Proxy-Authorization: Bearer x\r\n"):
                out = await rig.ask(f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n{auth}\r\n")
                assert out.startswith(b"HTTP/1.1 407") and b'Proxy-Authenticate: Basic realm="Kiwi VPN"' in out
            assert rig.seen == []
    run(go())


def test_opening_it_directly_isnt_a_website_and_asks_for_nothing(tmp_path, certs):
    async def go():
        async with Rig(tmp_path, certs) as rig:
            out = await rig.ask("GET / HTTP/1.1\r\nHost: x\r\n\r\n")
            assert out.startswith(b"HTTP/1.1 404") and b"Proxy-Authenticate" not in out
    run(go())


def test_connect_tunnels_and_counts_bytes(tmp_path, certs):
    async def go():
        async with Rig(tmp_path, certs) as rig:
            r, w = await rig.client()
            target = f"127.0.0.1:{rig.origin_port}"
            w.write(f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\nProxy-Authorization: Basic {basic()}\r\n\r\n".encode())
            await w.drain()
            assert (await asyncio.wait_for(r.readuntil(b"\r\n\r\n"), 5)).startswith(b"HTTP/1.1 200")
            w.write(b"GET /tunnelled HTTP/1.1\r\nHost: origin\r\n\r\n")
            await w.drain()
            data = await asyncio.wait_for(r.read(65536), 5)
            assert b"hello from origin" in data
            w.close()
            await asyncio.sleep(0.05)
            count = rig.proxy.counts["alice"]
            assert count[0] > 0 and count[1] > 0
            rig.proxy.flush_usage()
            saved = json.loads(rig.usage_file.read_text())["users"]["alice"]
            assert saved == {"rx_bytes": count[0], "tx_bytes": count[1]}
            assert b"GET /tunnelled" in rig.seen[0] and not rig.proxy.live  # the connection was cleaned up
    run(go())


def test_plain_http_is_forwarded_without_the_login_and_with_an_origin_style_request(tmp_path, certs):
    async def go():
        async with Rig(tmp_path, certs) as rig:
            out = await rig.ask(f"GET http://127.0.0.1:{rig.origin_port}/a?b=1 HTTP/1.1\r\nHost: 127.0.0.1:{rig.origin_port}\r\nProxy-Authorization: Basic {basic()}\r\nProxy-Connection: keep-alive\r\nAccept: */*\r\n\r\n")
            assert b"hello from origin" in out
            sent = rig.seen[0]
            assert sent.startswith(b"GET /a?b=1 HTTP/1.1\r\n") and b"Accept: */*" in sent and b"Connection: close" in sent
            assert b"roxy-" not in sent and SECRET.encode() not in sent and basic().encode() not in sent
    run(go())


def test_the_check_request_is_answered_here_with_the_exit_ip(tmp_path, certs):
    async def go():
        calls = []

        async def resolve(host, port):
            calls.append(host)
            return []

        async with Rig(tmp_path, certs, resolve=resolve) as rig:
            out = await rig.ask(f"GET http://{P.CHECK_HOST}/ HTTP/1.1\r\nHost: {P.CHECK_HOST}\r\nProxy-Authorization: Basic {basic()}\r\n\r\n")
            assert out.startswith(b"HTTP/1.1 200") and json.loads(out.split(b"\r\n\r\n", 1)[1]) == {"ok": True, "ip": "203.0.113.10"}
            assert calls == []  # never resolved
            assert (await rig.ask(f"GET http://{P.CHECK_HOST}/ HTTP/1.1\r\nHost: x\r\n\r\n")).startswith(b"HTTP/1.1 407")
    run(go())


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.8.0.1", "10.66.0.1", "192.168.1.1", "172.16.0.1", "169.254.169.254", "100.64.0.1", "::1", "fe80::1", "fc00::1",
                                "::ffff:10.8.0.1", "::ffff:127.0.0.1", "0.0.0.0", "224.0.0.1", "203.0.113.10", "not-an-ip"])
def test_public_only_refuses(ip):
    assert P.public_only(ip, frozenset({"203.0.113.10"})) is False


@pytest.mark.parametrize("ip", ["1.1.1.1", "8.8.8.8", "2606:4700:4700::1111", "::ffff:1.1.1.1"])
def test_public_only_allows(ip):
    assert P.public_only(ip, frozenset({"203.0.113.10"})) is True


@pytest.mark.parametrize("target", ["127.0.0.1:8443", "10.8.0.1:22", "[::1]:443", "169.254.169.254:80", "203.0.113.10:22", "2.29.149.29.nip.invalid.test:443"])
def test_default_rules_refuse_private_destinations_through_the_real_proxy(tmp_path, certs, target):
    async def go():
        async def resolve(host, port):  # the last target: a hostname that points at a private address
            return ["10.8.0.1"] if "nip" in host else [host]

        async with Rig(tmp_path, certs, dest_ok=None, resolve=resolve) as rig:
            out = await rig.ask(f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\nProxy-Authorization: Basic {basic()}\r\n\r\n")
            assert out.startswith(b"HTTP/1.1 403")
    run(go())


@pytest.mark.parametrize("port", [25, 465, 587])
def test_no_mail_ports(tmp_path, certs, port):
    async def go():
        async with Rig(tmp_path, certs) as rig:
            out = await rig.ask(f"CONNECT 1.1.1.1:{port} HTTP/1.1\r\nHost: x\r\nProxy-Authorization: Basic {basic()}\r\n\r\n")
            assert out.startswith(b"HTTP/1.1 403")
    run(go())


@pytest.mark.parametrize("target", ["nonsense", "host:0", "host:99999", "ho st:443", "[zz]:443", "a:b:c"])
def test_bad_targets_are_refused(tmp_path, certs, target):
    async def go():
        async with Rig(tmp_path, certs) as rig:
            out = await rig.ask(f"CONNECT {target} HTTP/1.1\r\nHost: x\r\nProxy-Authorization: Basic {basic()}\r\n\r\n")
            assert out.startswith(b"HTTP/1.1 400")
    run(go())


def test_a_mixed_answer_only_connects_to_the_public_address(tmp_path, certs):
    async def go():
        tried = []
        real_open = asyncio.open_connection

        async def spy(ip, port, **kw):
            tried.append(ip)
            raise OSError("refused")

        async def resolve(host, port):
            return ["10.8.0.1", "1.1.1.1"]

        async with Rig(tmp_path, certs, dest_ok=None, resolve=resolve) as rig:
            asyncio.open_connection = spy
            try:
                with pytest.raises(P.Refused):
                    await rig.proxy.open("mixed.test", 443)
            finally:
                asyncio.open_connection = real_open
            assert tried == ["1.1.1.1"]
    run(go())


def test_removing_a_login_cuts_its_open_tunnel_within_a_reload(tmp_path, certs):
    async def go():
        async with Rig(tmp_path, certs) as rig:
            async def silent(r, w):
                await r.read()
                w.close()

            held = await asyncio.start_server(silent, "127.0.0.1", 0)
            port = held.sockets[0].getsockname()[1]
            r, w = await rig.client()
            w.write(f"CONNECT 127.0.0.1:{port} HTTP/1.1\r\nHost: x\r\nProxy-Authorization: Basic {basic()}\r\n\r\n".encode())
            await w.drain()
            assert (await asyncio.wait_for(r.readuntil(b"\r\n\r\n"), 5)).startswith(b"HTTP/1.1 200")
            assert rig.proxy.live["alice"]
            rig.users_file.write_text(json.dumps({"users": {}}))
            rig.proxy.reload_users()
            assert await asyncio.wait_for(r.read(10), 5) == b""  # closed
            await asyncio.sleep(0.05)
            assert "alice" not in rig.proxy.live and "alice" not in rig.proxy.counts
            again = await rig.ask(f"CONNECT 127.0.0.1:{port} HTTP/1.1\r\nHost: x\r\nProxy-Authorization: Basic {basic()}\r\n\r\n")
            assert again.startswith(b"HTTP/1.1 407")
            held.close()
    run(go())


def test_a_brand_new_login_works_at_once(tmp_path, certs):
    async def go():
        async with Rig(tmp_path, certs) as rig:
            rig.users_file.write_text(json.dumps({"users": {"alice": HASH, "newbie": hashlib.sha256(b"fresh").hexdigest()}}))  # no reload has run
            out = await rig.ask(f"GET http://{P.CHECK_HOST}/ HTTP/1.1\r\nHost: x\r\nProxy-Authorization: Basic {basic('newbie', 'fresh')}\r\n\r\n")
            assert out.startswith(b"HTTP/1.1 200")
    run(go())


def test_a_damaged_logins_file_keeps_the_last_good_one(tmp_path, certs):
    async def go():
        async with Rig(tmp_path, certs) as rig:
            rig.users_file.write_text("{ not json")
            rig.proxy.reload_users()
            assert rig.proxy.users == {"alice": HASH}
    run(go())


def test_the_connection_limit_applies_per_login(tmp_path, certs, monkeypatch):
    async def go():
        monkeypatch.setattr(P, "MAX_PER_USER", 0)
        async with Rig(tmp_path, certs) as rig:
            out = await rig.ask(f"CONNECT 127.0.0.1:{rig.origin_port} HTTP/1.1\r\nHost: x\r\nProxy-Authorization: Basic {basic()}\r\n\r\n")
            assert out.startswith(b"HTTP/1.1 503") and rig.seen == []
    run(go())


def test_it_prints_nothing_about_connections(tmp_path, certs, capfd):
    async def go():
        async with Rig(tmp_path, certs) as rig:
            await rig.ask(f"GET http://127.0.0.1:{rig.origin_port}/secret-page HTTP/1.1\r\nHost: x\r\nProxy-Authorization: Basic {basic()}\r\n\r\n")
            await rig.ask(f"CONNECT 127.0.0.1:{rig.origin_port} HTTP/1.1\r\nHost: x\r\n\r\n")
            await rig.ask("CONNECT nonsense HTTP/1.1\r\nHost: x\r\n\r\n")
    run(go())
    out, err = capfd.readouterr()
    assert out == "" and err == ""


def test_it_needs_only_the_standard_library():
    out = subprocess.run([sys.executable, "-I", "-c", f"import runpy; runpy.run_path({P.__file__!r}, run_name='not_main'); print('ok')"], capture_output=True, text=True)
    assert out.stdout.strip() == "ok", out.stderr
