"""Server monitoring: this month's traffic per server, bandwidth alerts at 80% / 100% of the allowance, and the live load
Smart Connect uses."""
import asyncio
import datetime as dt

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.vpn import VpnDevice
from app.vpn import service as vpn
from tests.test_vpn import FakeNode, add_server, member_at, open_vpn, signed_in
from tests.test_vpn_browser import fake  # noqa: F401


def _use(db: Session, dev: VpnDevice, gb: float, now: dt.datetime) -> None:
    dev.usage_month, dev.rx_bytes, dev.tx_bytes = now.strftime("%Y-%m"), int(gb * 0.8e9), int(gb * 0.2e9)
    db.commit()


def test_traffic_per_server_counts_this_month_only(db: Session, fake: FakeNode) -> None:  # noqa: F811
    open_vpn(db)
    s = add_server(db, fake, "US East")
    m = member_at(db, "egg", "u1")
    dev = vpn.create_device(db, m, "pc", s.id)[0]
    now = utcnow()
    _use(db, dev, 12, now)
    assert vpn.server_traffic(db, now)[s.id] == 12_000_000_000
    assert vpn.server_traffic(db, now + dt.timedelta(days=40)).get(s.id, 0) == 0  # last month's bytes don't count


def test_bandwidth_alerts_once_at_80_and_once_at_100_percent_each_month(db: Session, fake: FakeNode) -> None:  # noqa: F811
    from app import alerts
    from tests.test_owner_alerts import SETTINGS, Bot

    tg = Bot()
    alerts.settings_row(db).telegram_chat = "424242"
    db.commit()
    open_vpn(db)
    s = add_server(db, fake, "US East")
    vpn.update_server(db, s.id, {"traffic_included_gb": 1000})
    dev = vpn.create_device(db, member_at(db, "egg", "u1"), "pc", s.id)[0]
    now = utcnow()
    run = lambda: asyncio.run(alerts.check_vpn_servers(db, settings=SETTINGS, transport=tg.transport, now=now))  # noqa: E731
    _use(db, dev, 500, now)
    assert run()["vpn_traffic"] == 0
    _use(db, dev, 810, now)
    assert run()["vpn_traffic"] == 1 and "at 80%" in tg.sent[-1]["text"] and "810 GB of 1000 GB" in tg.sent[-1]["text"]
    assert run()["vpn_traffic"] == 0  # not again
    _use(db, dev, 1001, now)
    assert run()["vpn_traffic"] == 1 and "billed" in tg.sent[-1]["text"]
    assert run()["vpn_traffic"] == 0
    vpn.update_server(db, s.id, {"traffic_included_gb": 0})  # off
    _use(db, dev, 5000, now)
    assert run()["vpn_traffic"] == 0


def test_members_get_live_load_and_the_proxy_name_for_smart_connect(client: TestClient, db: Session, fake: FakeNode) -> None:  # noqa: F811
    open_vpn(db)
    s = add_server(db, fake, "US East")
    m = member_at(db, "egg", "u1")
    dev = vpn.create_device(db, m, "pc", s.id)[0]
    dev.last_handshake = utcnow()
    db.commit()
    c = signed_in(client, db, m)
    row = c.get("/api/members/vpn").json()["servers"][0]
    assert row["live_load"] == round(1 / s.capacity, 4) and row["load"] == round(1 / s.capacity, 4)
    assert ("proxy_host" in row) and (row["proxy_host"] == (s.proxy_host or None))


def test_admin_sets_the_allowance_and_sees_this_months_traffic(client: TestClient, db: Session, fake: FakeNode) -> None:  # noqa: F811
    open_vpn(db)
    s = add_server(db, fake, "US East")
    assert client.patch(f"/api/vpn/admin/servers/{s.id}", json={"traffic_included_gb": 1024}).status_code in (200, 204)
    row = next(x for x in client.get("/api/vpn/admin/overview").json()["servers"] if x["id"] == s.id)
    assert row["traffic_included_gb"] == 1024 and row["traffic_month_bytes"] == 0
    assert client.patch(f"/api/vpn/admin/servers/{s.id}", json={"traffic_included_gb": -1}).status_code == 422
