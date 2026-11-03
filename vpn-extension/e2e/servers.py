"""For run.mjs: the REAL proxy (vpn-node/proxy.py) over TLS, a fake account API, and two websites only reachable through the proxy.

No real accounts, keys or servers. Prints one JSON line with the ports, then runs until killed.
"""
import asyncio
import datetime as dt
import hashlib
import json
import os
import ssl
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "vpn-node"))
import proxy as P  # noqa: E402

from cryptography import x509  # noqa: E402
from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from cryptography.x509.oid import NameOID  # noqa: E402

tmp = tempfile.mkdtemp(prefix="kiwi-e2e-")
users_file = os.path.join(tmp, "users.json")
SEEN = {"origin": [], "api": []}
STATE = {"refuse_new": False, "proxy_port": 0, "issued": 0}


def cert():
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = dt.datetime.now(dt.timezone.utc)
    c = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number())
         .not_valid_before(now - dt.timedelta(days=1)).not_valid_after(now + dt.timedelta(days=2))
         .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost"), x509.DNSName("origin.test")]), False).sign(key, hashes.SHA256()))
    a, b = os.path.join(tmp, "c.pem"), os.path.join(tmp, "k.pem")
    open(a, "wb").write(c.public_bytes(serialization.Encoding.PEM))
    open(b, "wb").write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return a, b


def set_users(users):
    with open(users_file, "w", encoding="utf-8") as f:
        json.dump({"users": users}, f)


def http(status, body, ctype="application/json", extra=""):
    raw = body if isinstance(body, bytes) else (json.dumps(body) if not isinstance(body, str) else body).encode()
    return f"HTTP/1.1 {status}\r\nContent-Type: {ctype}\r\nContent-Length: {len(raw)}\r\nCache-Control: no-store\r\nConnection: close\r\n{extra}\r\n".encode() + raw


ACCOUNT = {"enabled": True, "test_mode": False, "payments": False, "eligible": True, "reason": None, "max_devices": 3, "billing": None, "devices": [],
           "servers": [{"id": 1, "name": "Helsinki 1", "region": "Finland", "country_code": "FI", "online": True, "load": 0.1, "adblock": False, "recommended": True, "browser": True}],
           "usage": {"month": "2026-11", "used_bytes": 1610612736, "cap_bytes": 0, "paused_until": None}}


async def read_all(r):
    head = await r.readuntil(b"\r\n\r\n")
    n = 0
    for line in head.split(b"\r\n"):
        if line.lower().startswith(b"content-length:"):
            n = int(line.split(b":")[1])
    body = await r.readexactly(n) if n else b""
    return head.decode("latin1"), body


async def api(r, w):
    try:
        head, body = await read_all(r)
        first = head.split("\r\n")[0]
        method, path = first.split(" ")[:2]
        low = head.lower()
        cookie = "pc_session=member" in low
        origin = next((ln.split(":", 1)[1].strip() for ln in head.split("\r\n") if ln.lower().startswith("origin:")), None)
        SEEN["api"].append({"method": method, "path": path, "cookie": cookie, "origin": origin})
        if path == "/__sign_in":
            out = http("200 OK", "<!doctype html><title>signed in</title>signed in", "text/html", "Set-Cookie: pc_session=member; Path=/; HttpOnly; SameSite=Lax\r\n")
        elif path == "/__sign_out":
            out = http("200 OK", "<!doctype html>out", "text/html", "Set-Cookie: pc_session=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax\r\n")
        elif path == "/__refuse":
            STATE["refuse_new"] = True
            out = http("200 OK", {"ok": True})
        elif path == "/__revoke_all":
            set_users({})
            out = http("200 OK", {"ok": True})
        elif path == "/__seen":
            out = http("200 OK", SEEN)
        elif not cookie:
            out = http("401 Unauthorized", {"detail": "Sign in"})
        elif path == "/api/members/vpn":
            out = http("200 OK", ACCOUNT)
        elif path == "/api/members/vpn/browser" and method == "POST":
            if STATE["refuse_new"]:
                out = http("403 Forbidden", {"detail": "Your account can't use Kiwi VPN."})
            else:
                STATE["issued"] += 1
                name, password = f"e2euser{STATE['issued']:012d}", f"e2e-secret-{STATE['issued']}"
                set_users({name: hashlib.sha256(password.encode()).hexdigest()})
                out = http("201 Created", {"username": name, "password": password, "host": "localhost", "port": STATE["proxy_port"], "server_id": json.loads(body)["server_id"], "server": "Helsinki 1"})
        elif path.startswith("/api/members/vpn/browser/") and method == "DELETE":
            set_users({})
            out = http("204 No Content", b"")
        else:
            out = http("404 Not Found", {"detail": "nope"})
        w.write(out)
        await w.drain()
    except (asyncio.IncompleteReadError, ConnectionError):
        pass
    finally:
        w.close()


async def origin(r, w):
    try:
        head, _ = await read_all(r)
        SEEN["origin"].append(head)
        path = head.split(" ")[1]
        if path.startswith("/needs-password"):
            w.write(http("401 Unauthorized", "site wants a password", "text/plain", 'WWW-Authenticate: Basic realm="A website"\r\n'))
        else:
            w.write(http("200 OK", f"<!doctype html><title>reached origin</title>reached {path}", "text/html"))
        await w.drain()
    except (asyncio.IncompleteReadError, ConnectionError, ssl.SSLError):
        pass
    finally:
        w.close()


async def main():
    set_users({})
    c, k = cert()
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(c, k)
    plain_site = await asyncio.start_server(origin, "127.0.0.1", 0)
    tls_site = await asyncio.start_server(origin, "127.0.0.1", 0, ssl=tls)
    account_api = await asyncio.start_server(api, "127.0.0.1", 0)

    async def resolve(host, port):
        return ["127.0.0.1"] if host == "origin.test" else []

    px = P.Proxy({"USERS_FILE": users_file, "USAGE_FILE": os.path.join(tmp, "usage.json"), "PUBLIC_IPS": "203.0.113.10"}, dest_ok=lambda ip: True, resolve=resolve)
    server = await px.serve(tls, "127.0.0.1", 0)
    STATE["proxy_port"] = server.sockets[0].getsockname()[1]
    asyncio.ensure_future(px.housekeeping())
    print(json.dumps({"api": account_api.sockets[0].getsockname()[1], "http": plain_site.sockets[0].getsockname()[1], "https": tls_site.sockets[0].getsockname()[1], "proxy": STATE["proxy_port"]}), flush=True)
    await asyncio.Event().wait()


asyncio.run(main())
