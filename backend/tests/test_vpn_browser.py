"""The browser extension's side of Kiwi VPN: a login per browser on a server's proxy, only its hash kept, in step with the account."""

import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.settings import get_settings
from app.db.base import utcnow
from app.models.vpn import VpnAccount, VpnBrowser, VpnServer
from app.vpn import node
from app.vpn import service as vpn
from tests.test_production import prod  # noqa: F401
from tests.test_vpn import TOKEN, FakeNode, add_server, member_at, open_vpn, signed_in
from tests.test_vpn_e2e import TOKEN as AGENT_TOKEN
from tests.test_vpn_e2e import FakeWg, RunningAgent


class ProxyNode(FakeNode):
    """A node agent that also runs the browser proxy."""

    def __init__(self) -> None:
        super().__init__()
        self.users: dict[str, str] = {}
        self.counts: dict[str, dict[str, int]] = {}
        self.proxy_fail = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if self.fail or request.headers.get("authorization") != f"Bearer {TOKEN}":
            return super().handler(request)
        if path.startswith("/v1/proxy-users/"):
            if self.proxy_fail:
                return httpx.Response(500, json={"error": "internal"})
            name = path.rsplit("/", 1)[1]
            if request.method == "PUT":
                self.users[name] = json.loads(request.content)["secret_hash"]
            else:
                self.users.pop(name, None)
            return httpx.Response(200, json={"ok": True})
        r = super().handler(request)
        if path == "/v1/status":
            body = r.json()
            body["proxy"] = {"host": "hel1.vpn.example", "port": 443}
            body["proxy_users"] = [{"username": n, **self.counts.get(n, {"rx_bytes": 0, "tx_bytes": 0})} for n in self.users]
            return httpx.Response(200, json=body)
        return r


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> ProxyNode:
    f = ProxyNode()
    monkeypatch.setattr(node, "connect", lambda url, token, fp: node.NodeClient(url, token, fp, transport=httpx.MockTransport(f.handler)))
    return f


def test_a_login_is_issued_once_and_only_its_hash_is_kept(client: TestClient, db: Session, fake: ProxyNode) -> None:
    server = add_server(db, fake)
    open_vpn(db, payments_enabled=False)
    c = signed_in(client, db, member_at(db, "egg", "browser", paid=False))
    assert c.get("/api/members/vpn").json()["servers"][0]["browser"] is True
    r = c.post("/api/members/vpn/browser", json={"server_id": server.id})
    assert r.status_code == 201
    out = r.json()
    assert out["host"] == "hel1.vpn.example" and out["port"] == 443 and out["server_id"] == server.id and len(out["password"]) >= 40
    row = db.scalars(select(VpnBrowser)).one()
    assert row.username == out["username"] and row.secret_hash == hashlib.sha256(out["password"].encode()).hexdigest()
    assert fake.users == {out["username"]: row.secret_hash}
    assert out["password"] not in str({k: v for k, v in row.__dict__.items() if not k.startswith("_")})


def test_no_session_no_access(client: TestClient, db: Session, fake: ProxyNode) -> None:
    server = add_server(db, fake)
    open_vpn(db, payments_enabled=False)
    assert client.post("/api/members/vpn/browser", json={"server_id": server.id}).status_code == 401
    assert client.delete("/api/members/vpn/browser/abc").status_code == 401
    assert not fake.users


def test_closed_or_unpaid_gets_no_login(client: TestClient, db: Session, fake: ProxyNode) -> None:
    server = add_server(db, fake)
    db.commit()
    c = signed_in(client, db, member_at(db, "egg", "early", paid=False))
    assert c.post("/api/members/vpn/browser", json={"server_id": server.id}).status_code == 403  # closed
    open_vpn(db)  # open and charging, with no time
    r = c.post("/api/members/vpn/browser", json={"server_id": server.id})
    assert r.status_code == 403 and "$9.99" in r.json()["detail"]
    assert not fake.users


def test_a_server_without_the_proxy_isnt_offered(client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    plain = FakeNode()  # an agent from before the proxy
    monkeypatch.setattr(node, "connect", lambda url, token, fp: node.NodeClient(url, token, fp, transport=httpx.MockTransport(plain.handler)))
    server = add_server(db, plain)
    open_vpn(db, payments_enabled=False)
    c = signed_in(client, db, member_at(db, "egg", "old", paid=False))
    assert c.get("/api/members/vpn").json()["servers"][0]["browser"] is False
    assert c.post("/api/members/vpn/browser", json={"server_id": server.id}).status_code == 404
    vpn.sync_all(db)
    db.refresh(server)
    assert server.online is True and not any("proxy-users" in path for _, path in plain.calls)  # the sync doesn't ask it about logins


def test_changing_server_replaces_and_the_limit_retires_the_oldest(client: TestClient, db: Session, fake: ProxyNode) -> None:
    server = add_server(db, fake)
    open_vpn(db, payments_enabled=False, max_devices=2)
    c = signed_in(client, db, member_at(db, "egg", "many", paid=False))
    first = c.post("/api/members/vpn/browser", json={"server_id": server.id}).json()["username"]
    second = c.post("/api/members/vpn/browser", json={"server_id": server.id, "replace": first}).json()["username"]
    assert set(fake.users) == {second}
    third = c.post("/api/members/vpn/browser", json={"server_id": server.id}).json()["username"]
    fourth = c.post("/api/members/vpn/browser", json={"server_id": server.id}).json()["username"]
    assert set(fake.users) == {third, fourth}


def test_someone_elses_login_cant_be_replaced_or_deleted(client: TestClient, db: Session, fake: ProxyNode) -> None:
    server = add_server(db, fake)
    open_vpn(db, payments_enabled=False)
    theirs = signed_in(client, db, member_at(db, "egg", "theirs", paid=False)).post("/api/members/vpn/browser", json={"server_id": server.id}).json()["username"]
    client.cookies.clear()
    c = signed_in(client, db, member_at(db, "egg", "attacker", paid=False))
    assert c.delete(f"/api/members/vpn/browser/{theirs}").status_code == 404
    assert c.post("/api/members/vpn/browser", json={"server_id": server.id, "replace": theirs}).status_code == 201
    assert theirs in fake.users


def test_if_the_node_refuses_nothing_is_kept(client: TestClient, db: Session, fake: ProxyNode) -> None:
    server = add_server(db, fake)
    open_vpn(db, payments_enabled=False)
    fake.proxy_fail = True
    c = signed_in(client, db, member_at(db, "egg", "flaky", paid=False))
    assert c.post("/api/members/vpn/browser", json={"server_id": server.id}).status_code == 503
    assert db.scalars(select(VpnBrowser)).all() == []


def login_for(client: TestClient, db: Session, fake: ProxyNode, server: VpnServer, name: str = "sync") -> tuple[VpnBrowser, VpnAccount, Any]:
    m = member_at(db, "egg", name, paid=False)
    out = signed_in(client, db, m).post("/api/members/vpn/browser", json={"server_id": server.id}).json()
    return db.scalars(select(VpnBrowser).where(VpnBrowser.username == out["username"])).one(), vpn.account_of(db, m), m


def test_sync_counts_bytes_and_survives_a_proxy_restart(client: TestClient, db: Session, fake: ProxyNode) -> None:
    server = add_server(db, fake)
    open_vpn(db, payments_enabled=False)
    b, acct, _ = login_for(client, db, fake, server)
    fake.counts[b.username] = {"rx_bytes": 100, "tx_bytes": 1000}
    vpn.sync_all(db)
    db.refresh(b)
    assert (b.rx_bytes, b.tx_bytes) == (100, 1000) and b.last_active is not None
    fake.counts[b.username] = {"rx_bytes": 150, "tx_bytes": 1500}
    vpn.sync_all(db)
    db.refresh(b)
    assert (b.rx_bytes, b.tx_bytes) == (150, 1500)
    seen = b.last_active
    vpn.sync_all(db)  # nothing moved: not "active"
    db.refresh(b)
    assert b.last_active == seen
    fake.counts[b.username] = {"rx_bytes": 10, "tx_bytes": 20}  # restarted: counters from zero
    vpn.sync_all(db)
    db.refresh(b)
    assert (b.rx_bytes, b.tx_bytes) == (160, 1520)
    assert vpn.account_usage(db, acct.id) == 1680


def test_sync_puts_back_a_lost_login_and_removes_unknown_ones(client: TestClient, db: Session, fake: ProxyNode) -> None:
    server = add_server(db, fake)
    open_vpn(db, payments_enabled=False)
    b, _, _ = login_for(client, db, fake, server)
    fake.users.clear()
    fake.users["0123456789abcdef"] = "f" * 64
    vpn.sync_all(db)
    assert fake.users == {b.username: b.secret_hash}


def test_out_of_time_turns_a_login_off_and_paying_turns_it_back_on(client: TestClient, db: Session, fake: ProxyNode) -> None:
    server = add_server(db, fake)
    open_vpn(db, payments_enabled=False)
    b, acct, _ = login_for(client, db, fake, server)
    vpn.update_settings(db, {"payments_enabled": True})
    vpn.sync_all(db)
    db.refresh(acct)
    assert b.username not in fake.users and db.get(VpnBrowser, b.id) is not None and acct.lapsed_at is not None
    acct.paid_until = utcnow() + dt.timedelta(days=3)
    db.commit()
    vpn.sync_all(db)
    db.refresh(acct)
    assert fake.users[b.username] == b.secret_hash and acct.lapsed_at is None


def test_out_of_time_for_too_long_deletes_the_row(client: TestClient, db: Session, fake: ProxyNode) -> None:
    server = add_server(db, fake)
    open_vpn(db)
    m = member_at(db, "egg", "gone")  # a manual grant
    out = signed_in(client, db, m).post("/api/members/vpn/browser", json={"server_id": server.id}).json()
    acct = vpn.account_of(db, m)
    from tests.test_vpn import ungrant
    ungrant(db, m)
    acct.lapsed_at = utcnow() - dt.timedelta(days=40)
    db.commit()
    vpn.sync_all(db)
    assert out["username"] not in fake.users and db.scalars(select(VpnBrowser)).all() == []


def test_the_data_cap_counts_browsers_and_turns_them_off_until_it_resets(client: TestClient, db: Session, fake: ProxyNode) -> None:
    server = add_server(db, fake)
    open_vpn(db, payments_enabled=False, monthly_cap_gb=1)
    b, acct, _ = login_for(client, db, fake, server)
    fake.counts[b.username] = {"rx_bytes": vpn.GB, "tx_bytes": 1}
    vpn.sync_all(db)  # counted and over: this very sync takes it off
    assert b.username not in fake.users and db.get(VpnBrowser, b.id) is not None
    assert vpn.account_usage(db, acct.id) > vpn.GB
    r = client.post("/api/members/vpn/browser", json={"server_id": server.id})
    assert r.status_code == 403 and "1 GB" in r.json()["detail"]
    vpn.update_settings(db, {"monthly_cap_gb": 0})  # raised: back on
    vpn.sync_all(db)
    assert b.username in fake.users


def test_blocking_removes_browsers_now(client: TestClient, db: Session, fake: ProxyNode) -> None:
    server = add_server(db, fake)
    open_vpn(db, payments_enabled=False)
    b, acct, _ = login_for(client, db, fake, server)
    username = b.username
    assert vpn.set_blocked(db, acct.id, True)["devices_removed"] == 1
    assert username not in fake.users and db.scalars(select(VpnBrowser)).all() == []


def test_admin_sees_and_revokes_a_browser_and_never_a_secret(client: TestClient, db: Session, fake: ProxyNode) -> None:
    server = add_server(db, fake)
    open_vpn(db, payments_enabled=False)
    b, _, _ = login_for(client, db, fake, server)
    username, secret_hash, browser_id = b.username, b.secret_hash, b.id
    assert client.get("/api/vpn/admin/overview").json()["servers"][0]["proxy_host"] == "hel1.vpn.example"
    res = client.get("/api/vpn/admin/customers")
    row = res.json()["items"][0]
    assert row["browsers"][0]["id"] == browser_id and row["browsers"][0]["server"] == "Frankfurt"
    assert secret_hash not in res.text and username not in res.text
    assert client.delete(f"/api/vpn/admin/browsers/{browser_id}").status_code == 204
    assert not fake.users


def test_only_our_extension_may_call_only_this_route_across_origins(client: TestClient, db: Session, fake: ProxyNode, prod: Any) -> None:  # noqa: F811
    ours = get_settings().vpn_extension_origins[0]
    assert ours.startswith("chrome-extension://")
    for origin, path, expect_blocked in ((ours, "/api/members/vpn/browser", False), (ours, "/api/members/vpn/usdt", True),
                                         ("chrome-extension://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "/api/members/vpn/browser", True), ("https://evil.example", "/api/members/vpn/browser", True)):
        r = prod.post(path, json={"server_id": 1}, headers={"origin": origin})
        assert (r.status_code == 403 and r.json().get("code") == "bad_origin") is expect_blocked, (origin, path, r.status_code)


def test_through_the_real_agent(client: TestClient, db: Session, tmp_path: Path) -> None:
    """The real agent's HTTP API and files, with the real node client: what the proxy will read is what the backend sent."""
    (tmp_path / "proxy").mkdir()
    users, usage = tmp_path / "proxy" / "users.json", tmp_path / "proxy" / "usage.json"
    agent = RunningAgent(tmp_path, FakeWg())
    agent.cfg.update({"PROXY_HOST": "hel1.vpn.example", "PROXY_PORT": "443", "PROXY_USERS_FILE": str(users), "PROXY_USAGE_FILE": str(usage)})
    agent.start()
    try:
        server = vpn.add_server(db, {"name": "Helsinki", "region": "Finland", "country_code": "fi", "agent_url": agent.url, "agent_token": AGENT_TOKEN, "cert_sha256": agent.fp, "capacity": 10})
        assert server.proxy_host == "hel1.vpn.example" and server.proxy_port == 443
        open_vpn(db, payments_enabled=False)
        c = signed_in(client, db, member_at(db, "egg", "real", paid=False))
        out = c.post("/api/members/vpn/browser", json={"server_id": server.id}).json()
        saved = json.loads(users.read_text())["users"]
        assert saved == {out["username"]: hashlib.sha256(out["password"].encode()).hexdigest()}
        assert out["password"] not in users.read_text()
        usage.write_text(json.dumps({"users": {out["username"]: {"rx_bytes": 7, "tx_bytes": 9}}}))
        vpn.sync_all(db)
        row = db.scalars(select(VpnBrowser)).one()
        assert (row.rx_bytes, row.tx_bytes) == (7, 9)
        assert c.delete(f"/api/members/vpn/browser/{out['username']}").status_code == 204
        assert json.loads(users.read_text())["users"] == {}
        # the agent refuses bad input, and the token is still required
        assert agent.agent.handle("PUT", "/v1/proxy-users/" + "a" * 24, f"Bearer {AGENT_TOKEN}", b'{"secret_hash": "nope"}')[0] == 400
        assert agent.agent.handle("PUT", "/v1/proxy-users/../../etc", f"Bearer {AGENT_TOKEN}", b"{}")[0] == 404
        assert agent.agent.handle("PUT", "/v1/proxy-users/" + "a" * 24, "Bearer wrong", b'{"secret_hash": "' + b"a" * 64 + b'"}')[0] == 401
    finally:
        agent.stop()
