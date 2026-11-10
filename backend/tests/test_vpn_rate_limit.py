"""Kiwi VPN's provisioning rate limits: only a genuinely new device key is charged, and the app's devices, the browser
extension's logins, the trial and anonymous devices each have their own bucket, so one can't starve another. Over a limit
is still a 429 with Retry-After (the apps depend on it)."""

import datetime as dt

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.security.rate_limit import FREED, LIMITS
from app.vpn import billing, keys
from tests.test_vpn import add_server, member_at, open_vpn, signed_in
from tests.test_vpn_browser import ProxyNode, fake  # noqa: F401  (a fake agent that also runs a browser proxy)

DEVICES = "/api/members/vpn/devices"
BURST = int(LIMITS["vpn_device"].burst)


def new_key() -> str:
    return keys.generate_keypair()[1]


def add(c: TestClient, server_id: int, name: str, key: str | None = None):  # type: ignore[no-untyped-def]
    return c.post(DEVICES, json={"name": name, "server_id": server_id, "public_key": key or new_key()})


def assert_limited(r) -> None:  # type: ignore[no-untyped-def]
    assert r.status_code == 429, r.text
    assert int(r.headers["Retry-After"]) >= 1


def test_new_keys_are_charged_and_then_refused_with_retry_after(client: TestClient, db: Session, fake: ProxyNode) -> None:  # noqa: F811
    server = add_server(db, fake)
    open_vpn(db, max_devices=10)
    c = signed_in(client, db, member_at(db, "warrior", "newkeys"))
    assert [add(c, server.id, f"PC {i}").status_code for i in range(BURST)] == [201] * BURST
    r = add(c, server.id, "one too many")
    assert_limited(r)
    assert "Too many new devices" in r.text


def test_resending_a_registered_key_is_not_charged(client: TestClient, db: Session, fake: ProxyNode) -> None:  # noqa: F811
    server = add_server(db, fake)
    other = add_server(db, fake, "Helsinki")
    open_vpn(db, max_devices=10)
    c = signed_in(client, db, member_at(db, "warrior", "samekey"))
    key = new_key()
    first = add(c, server.id, "PC (1)", key)
    assert first.status_code == 201
    dev_id = first.json()["device"]["id"]
    for _ in range(BURST * 3):  # a retry whose answer was lost: the same device back, nothing new on the node
        again = add(c, server.id, "PC  (1)", key)  # spacing is normalised like the stored name
        assert again.status_code == 201 and again.json()["device"]["id"] == dev_id and again.json()["peer"] == first.json()["peer"]
    assert add(c, other.id, "PC (1)", key).status_code == 409  # the same key elsewhere is refused, and not charged either
    assert add(c, server.id, "renamed", key).status_code == 409
    assert len(fake.peers) == 1
    # only the first registration was charged
    assert [add(c, server.id, f"PC {i}").status_code for i in range(BURST - 1)] == [201] * (BURST - 1)
    assert_limited(add(c, server.id, "one too many"))


def test_someone_elses_key_is_still_refused(client: TestClient, db: Session, fake: ProxyNode) -> None:  # noqa: F811
    server = add_server(db, fake)
    open_vpn(db, max_devices=10)
    key = new_key()
    assert add(signed_in(client, db, member_at(db, "warrior", "owner1")), server.id, "PC", key).status_code == 201
    c = signed_in(client, db, member_at(db, "warrior", "copycat"))
    assert add(c, server.id, "PC", key).status_code == 409  # the service's check: never handed someone else's device


def test_a_reinstall_replacing_its_deleted_device_is_not_charged_but_churn_is(client: TestClient, db: Session, fake: ProxyNode) -> None:  # noqa: F811
    server = add_server(db, fake)
    open_vpn(db, max_devices=10)
    c = signed_in(client, db, member_at(db, "warrior", "reinstall"))
    dev = add(c, server.id, "DESKTOP (1)").json()["device"]  # charged: 1
    replaces = int(LIMITS["vpn_replace"].burst)
    for _ in range(replaces):  # what the Windows app does after a reinstall: delete the stale same-named device, make a new one
        assert c.delete(f"{DEVICES}/{dev['id']}").status_code == 204
        r = add(c, server.id, "DESKTOP (1)")
        assert r.status_code == 201
        dev = r.json()["device"]
    # a new name, or a deletion that has already been used, is a new device
    assert add(c, server.id, "DESKTOP (2)").status_code == 201  # charged: 2
    assert add(c, server.id, "DESKTOP (1) again").status_code == 201  # charged: 3
    # churning on past the replacement allowance: each cycle is charged like any new key, until the limit
    codes = []
    for _ in range(BURST):
        assert c.delete(f"{DEVICES}/{dev['id']}").status_code == 204
        r = add(c, server.id, "DESKTOP (1)")
        codes.append(r.status_code)
        if r.status_code != 201:
            assert_limited(r)
            break
        dev = r.json()["device"]
    assert codes == [201] * (BURST - 3) + [429]


def test_a_replacement_has_to_be_soon_and_on_the_same_server(client: TestClient, db: Session, fake: ProxyNode) -> None:  # noqa: F811
    server = add_server(db, fake)
    other = add_server(db, fake, "Helsinki")
    open_vpn(db, max_devices=10)
    c = signed_in(client, db, member_at(db, "warrior", "replacerules"))
    dev = add(c, server.id, "PC").json()["device"]
    assert c.delete(f"{DEVICES}/{dev['id']}").status_code == 204
    assert add(c, other.id, "PC").status_code == 201  # another server: charged
    dev = add(c, server.id, "PC 2").json()["device"]
    assert c.delete(f"{DEVICES}/{dev['id']}").status_code == 204
    FREED.ttl, ttl = -1, FREED.ttl  # the deletion is too old
    try:
        assert add(c, server.id, "PC 2").status_code == 201  # charged
    finally:
        FREED.ttl = ttl
    assert [add(c, server.id, f"x{i}").status_code for i in range(BURST - 4)] == [201] * (BURST - 4)
    assert_limited(add(c, server.id, "one too many"))


def test_browser_logins_do_not_drain_the_device_bucket(client: TestClient, db: Session, fake: ProxyNode) -> None:  # noqa: F811
    server = add_server(db, fake)
    open_vpn(db, max_devices=10)
    c = signed_in(client, db, member_at(db, "warrior", "extension"))
    logins = int(LIMITS["vpn_browser"].burst)
    assert [c.post("/api/members/vpn/browser", json={"server_id": server.id}).status_code for _ in range(logins)] == [201] * logins
    assert_limited(c.post("/api/members/vpn/browser", json={"server_id": server.id}))
    assert [add(c, server.id, f"PC {i}").status_code for i in range(BURST)] == [201] * BURST
    assert_limited(add(c, server.id, "one too many"))
    LIMITS["vpn_browser"].clear()
    assert c.post("/api/members/vpn/browser", json={"server_id": server.id}).status_code == 201  # devices didn't drain logins


def test_the_trial_does_not_drain_the_device_bucket(client: TestClient, db: Session, fake: ProxyNode) -> None:  # noqa: F811
    server = add_server(db, fake)
    open_vpn(db, max_devices=10, trial_enabled=True)
    c = signed_in(client, db, member_at(db, "egg", "trialist", paid=False))
    tries = int(LIMITS["vpn_trial"].burst)
    assert [c.post("/api/members/vpn/trial").status_code for _ in range(tries)] == [200] + [409] * (tries - 1)
    assert_limited(c.post("/api/members/vpn/trial"))
    assert [add(c, server.id, f"PC {i}").status_code for i in range(BURST)] == [201] * BURST


def test_anonymous_devices_are_still_limited(client: TestClient, db: Session, fake: ProxyNode) -> None:  # noqa: F811
    server = add_server(db, fake)
    open_vpn(db, max_devices=10)
    acct, key = billing.new_anonymous(db)
    acct.paid_until = utcnow() + dt.timedelta(days=30)
    db.commit()
    head = {"x-kiwi-key": key}
    burst = int(LIMITS["vpn_anon_device"].burst)
    codes = [client.post("/api/members/vpn/anon/devices", json={"name": f"phone {i}", "server_id": server.id}, headers=head).status_code for i in range(burst)]
    assert codes == [201] * burst
    r = client.post("/api/members/vpn/anon/devices", json={"name": "one too many", "server_id": server.id}, headers=head)
    assert_limited(r)
    assert "Too many new devices" in r.text


def test_the_telegram_bot_bucket_still_exists() -> None:
    # tradingos/bot.py's /vpn add charges "vpn_device" directly: the same purpose as the app's devices
    assert {"vpn_device", "vpn_replace", "vpn_browser", "vpn_trial", "vpn_anon_device"} <= set(LIMITS)
