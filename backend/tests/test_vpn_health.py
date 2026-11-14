"""Server health and capacity (app/vpn/health.py): the sync's checks, uptime / latency / load / traffic figures, the members'
`health` field, the Admin report and the fleet alerts."""
import asyncio
import datetime as dt

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.vpn import VpnServer, VpnServerCheck
from app.vpn import health
from app.vpn import service as vpn
from tests.test_production import prod  # noqa: F401  (fixture)
from tests.test_vpn import FakeNode, add_server, fake, member_at, open_vpn, signed_in  # noqa: F401


def _checks(db: Session, server: VpnServer, now: dt.datetime, results: list[bool], ms: int = 40, live: int = 0) -> None:
    """Synthetic history: one check per 5 minutes going back from now, newest last."""
    for i, ok in enumerate(results):
        at = now - dt.timedelta(minutes=5 * (len(results) - i))
        db.add(VpnServerCheck(server_id=server.id, at=at, ok=ok, ms=ms if ok else None, live=live, peers=live))
    db.commit()


def _row(db: Session, server: VpnServer, now: dt.datetime | None = None) -> dict:
    return next(r for r in health.server_rows(db, now or utcnow()) if r["id"] == server.id)


def test_each_sync_records_a_check_with_latency_peers_and_live_tunnels(db: Session, fake: FakeNode) -> None:  # noqa: F811
    s = add_server(db, fake)
    open_vpn(db)
    m = member_at(db, "warrior")
    a, _, _ = vpn.create_device(db, m, "a", s.id)
    vpn.create_device(db, m, "b", s.id)
    fake.peers[a.public_key]["latest_handshake"] = int(utcnow().timestamp()) - 60  # one in use, one idle
    vpn.sync_all(db)
    c = db.scalars(select(VpnServerCheck)).one()
    assert c.ok and c.ms is not None and c.ms >= 0 and c.peers == 2 and c.live == 1 and c.error is None
    fake.fail = True
    vpn.sync_all(db)
    bad = db.scalars(select(VpnServerCheck).order_by(VpnServerCheck.id.desc())).first()
    assert bad is not None and not bad.ok and bad.ms is None and bad.error


def test_checks_older_than_14_days_are_deleted_by_the_sync(db: Session, fake: FakeNode) -> None:  # noqa: F811
    s = add_server(db, fake)
    now = utcnow()
    db.add(VpnServerCheck(server_id=s.id, at=now - dt.timedelta(days=15), ok=True, ms=10))
    db.add(VpnServerCheck(server_id=s.id, at=now - dt.timedelta(days=13), ok=True, ms=10))
    db.commit()
    vpn.sync_all(db, now)
    assert db.scalar(select(func.count()).select_from(VpnServerCheck)) == 2  # the 13-day-old one and this sync's


def test_uptime_and_latency_figures(db: Session, fake: FakeNode) -> None:  # noqa: F811
    s = add_server(db, fake)
    now = utcnow()
    _checks(db, s, now, [True] * 99 + [False])
    r = _row(db, s, now)
    assert r["checks_24h"] == 100 and r["uptime_24h"] == 0.99 and r["uptime_7d"] == 0.99
    assert r["ms_p50_24h"] == 40 and r["ms_p95_24h"] == 40 and r["status"] == "healthy"


def test_degraded_on_low_uptime_slow_agent_or_nearly_full_but_not_from_a_few_checks(db: Session, fake: FakeNode) -> None:  # noqa: F811
    s = add_server(db, fake)
    now = utcnow()
    _checks(db, s, now, [True, False])  # 50% of two checks: too few to judge
    assert _row(db, s, now)["status"] == "healthy"
    _checks(db, s, now - dt.timedelta(hours=1), [True] * 10 + [False] * 10)
    r = _row(db, s, now)
    assert r["status"] == "degraded" and any("uptime" in x for x in r["reasons"])
    db.query(VpnServerCheck).delete()
    _checks(db, s, now, [True] * 20, ms=4000)
    r = _row(db, s, now)
    assert r["status"] == "degraded" and any("slow" in x for x in r["reasons"])
    db.query(VpnServerCheck).delete()
    db.commit()
    s.capacity = 2
    db.commit()
    open_vpn(db)
    m = member_at(db, "warrior")
    vpn.create_device(db, m, "a", s.id)
    vpn.create_device(db, m, "b", s.id)
    r = _row(db, s, now)
    assert r["status"] == "degraded" and any("nearly full" in x for x in r["reasons"]) and r["load"] == 1.0


def test_offline_and_disabled_states(db: Session, fake: FakeNode) -> None:  # noqa: F811
    s = add_server(db, fake)
    fake.fail = True
    vpn.sync_all(db)
    r = _row(db, s)
    assert r["status"] == "healthy" or r["status"] == "degraded"  # one failure: still online (a blip), but flagged
    assert any("failed" in x for x in r["reasons"])
    vpn.sync_all(db)
    assert _row(db, s)["status"] == "offline"
    vpn.update_server(db, s.id, {"enabled": False})
    assert _row(db, s)["status"] == "disabled"


def test_traffic_projection_after_the_first_days_of_the_month(db: Session, fake: FakeNode) -> None:  # noqa: F811
    s = add_server(db, fake)
    vpn.update_server(db, s.id, {"traffic_included_gb": 1000})
    open_vpn(db)
    dev = vpn.create_device(db, member_at(db, "warrior"), "pc", s.id)[0]
    now = dt.datetime(2026, 11, 16, 0, 0, tzinfo=dt.timezone.utc)  # half of November gone
    dev.usage_month, dev.rx_bytes, dev.tx_bytes = "2026-11", 600 * 10**9, 0
    db.commit()
    r = _row(db, s, now)
    assert r["traffic_month_gb"] == 600 and r["traffic_projected_gb"] == 1200 and r["traffic_projected_share"] == 1.2
    assert any("on pace" in w for w in r["warnings"])
    early = dt.datetime(2026, 11, 2, tzinfo=dt.timezone.utc)
    assert health.projected_bytes(10**9, early) is None  # day 2: too early to project
    assert health.month_fraction(dt.datetime(2026, 2, 15, tzinfo=dt.timezone.utc)) == 0.5


def test_members_see_health_ok_or_degraded(client: TestClient, db: Session, fake: FakeNode) -> None:  # noqa: F811
    good, bad = add_server(db, fake, "Good"), add_server(db, fake, "Bad")
    now = utcnow()
    _checks(db, good, now, [True] * 20)
    _checks(db, bad, now, [True] * 10 + [False] * 10)
    open_vpn(db)
    c = signed_in(client, db, member_at(db, "warrior"))
    rows = {r["name"]: r for r in c.get("/api/members/vpn").json()["servers"]}
    assert rows["Good"]["health"] == "ok" and rows["Bad"]["health"] == "degraded"


def test_fleet_view_counts_public_servers_and_gives_advice(db: Session, fake: FakeNode) -> None:  # noqa: F811
    a, b = add_server(db, fake, "A"), add_server(db, fake, "B")
    t = add_server(db, fake, "Trial")
    vpn.update_server(db, t.id, {"testers_only": True})
    f = health.fleet(health.server_rows(db))
    assert f["public_servers"] == 2 and f["public_healthy"] == 2 and f["testers_only"] == 1 and f["capacity"] == 200
    assert f["countries"] == {"DE": 2} and f["single_server_countries"] == [] and f["advice"] == []
    vpn.update_server(db, b.id, {"enabled": False})
    f = health.fleet(health.server_rows(db))
    assert f["public_healthy"] == 1 and f["single_server_countries"] == ["DE"] and any("only 1 healthy" in x for x in f["advice"])
    a.capacity = 2
    db.commit()
    open_vpn(db)
    m = member_at(db, "warrior")
    vpn.create_device(db, m, "x", a.id)
    vpn.create_device(db, m, "y", a.id)
    f = health.fleet(health.server_rows(db))
    assert f["utilization"] == 1.0 and any("public capacity" in x for x in f["advice"])


def test_admin_health_report(client: TestClient, db: Session, fake: FakeNode) -> None:  # noqa: F811
    s = add_server(db, fake)
    vpn.sync_all(db)
    out = client.get("/api/vpn/admin/health").json()
    row = next(r for r in out["servers"] if r["id"] == s.id)
    assert row["status"] == "healthy" and row["checks_24h"] == 1 and out["fleet"]["public_servers"] == 1
    assert out["limits"]["keep_days"] == 14


def test_admin_health_needs_the_owner(prod: TestClient) -> None:  # noqa: F811
    assert prod.get("/api/vpn/admin/health").status_code == 401


async def test_fleet_alerts_redundancy_capacity_and_traffic_pace(db: Session, fake: FakeNode) -> None:  # noqa: F811
    from app import alerts
    from tests.test_owner_alerts import SETTINGS, Bot

    tg = Bot()
    alerts.settings_row(db).telegram_chat = "424242"
    db.commit()
    a = add_server(db, fake, "A")
    open_vpn(db, max_devices=10)
    run = lambda now=None: alerts.check_vpn_servers(db, settings=SETTINGS, transport=tg.transport, now=now)  # noqa: E731
    # one public server while open: no fallback -> once
    assert (await run())["vpn_fleet"] == 1 and "1 healthy public server" in tg.sent[-1]["text"]
    assert (await run())["vpn_fleet"] == 0
    b = add_server(db, fake, "B")
    await run()  # two healthy: closed quietly
    n = len(tg.sent)
    vpn.update_server(db, b.id, {"enabled": False})
    assert (await run())["vpn_fleet"] == 1 and len(tg.sent) == n + 1  # re-armed
    vpn.update_server(db, b.id, {"enabled": True})
    await run()
    # capacity: 80% of public capacity
    a.capacity = b.capacity = 5
    db.commit()
    m = member_at(db, "warrior")
    for i in range(8):
        vpn.create_device(db, m, f"d{i}", (a.id if i < 4 else b.id))
    out = await run()
    assert out["vpn_fleet"] == 1 and "capacity" in tg.sent[-1]["text"] and "8 of 10" in tg.sent[-1]["text"]
    assert (await run())["vpn_fleet"] == 0
    # traffic pace: once per server and month
    a.capacity = b.capacity = 100
    vpn.update_server(db, a.id, {"traffic_included_gb": 1000})
    dev = db.scalars(select(vpn.VpnDevice).where(vpn.VpnDevice.server_id == a.id)).first()
    mid = dt.datetime(2026, 11, 16, tzinfo=dt.timezone.utc)
    dev.usage_month, dev.rx_bytes = "2026-11", 700 * 10**9
    db.commit()
    assert (await run(mid))["vpn_fleet"] == 1 and "on pace for 1400 GB" in tg.sent[-1]["text"]
    assert (await run(mid))["vpn_fleet"] == 0


async def test_one_blip_on_one_of_two_servers_is_not_a_redundancy_alert(db: Session, fake: FakeNode) -> None:  # noqa: F811
    """The flap guard on main (OFFLINE_AFTER) applies to the fleet alert too: one failed sync of one of two public servers
    pages nobody; OFFLINE_AFTER in a row do."""
    from app import alerts
    from tests.test_owner_alerts import SETTINGS, Bot

    tg = Bot()
    alerts.settings_row(db).telegram_chat = "424242"
    db.commit()
    a = add_server(db, fake, "A")
    add_server(db, fake, "B")
    open_vpn(db)
    run = lambda: alerts.check_vpn_servers(db, settings=SETTINGS, transport=tg.transport)  # noqa: E731
    await run()
    n = len(tg.sent)
    a.fail_streak, a.last_error = 1, "timeout"
    db.commit()
    assert _row(db, a)["status"] == "degraded"
    out = await run()
    assert out["vpn_fleet"] == 0 and out["vpn_offline"] == 0 and len(tg.sent) == n
    a.fail_streak, a.online = vpn.OFFLINE_AFTER, False
    db.commit()
    out = await run()
    assert out["vpn_offline"] == 1 and out["vpn_fleet"] == 1 and "1 healthy public server" in tg.sent[-1]["text"]


async def test_no_redundancy_alert_while_the_vpn_is_closed(db: Session, fake: FakeNode) -> None:  # noqa: F811
    from app import alerts
    from tests.test_owner_alerts import SETTINGS, Bot

    tg = Bot()
    alerts.settings_row(db).telegram_chat = "424242"
    db.commit()
    add_server(db, fake, "A")
    assert (await alerts.check_vpn_servers(db, settings=SETTINGS, transport=tg.transport))["vpn_fleet"] == 0


@pytest.mark.parametrize("peers,expected", [([], 0), ([{"latest_handshake": 0}], 0)])
def test_live_peers_ignores_peers_that_never_connected(peers: list, expected: int) -> None:
    assert health.live_peers(peers, utcnow()) == expected
