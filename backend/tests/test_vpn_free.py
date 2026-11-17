"""Kiwi VPN is free until Admin turns payments on. Devices made in Admin are free either way."""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.vpn import VpnAccount, VpnDevice, VpnPayment
from app.vpn import billing
from app.vpn import service as vpn
from tests.test_vpn import add_server, fake, member_at, signed_in  # noqa: F401
from tests.test_vpn_billing import SENT, ipn, keys  # noqa: F401


def go_free(db: Session) -> None:
    vpn.update_settings(db, {"enabled": True, "trial_enabled": False})  # free for all takes Admin switching the trial off too
    assert vpn.settings(db).payments_enabled is False  # the default


def test_free_means_no_time_needed_and_no_payment_route(client: TestClient, db: Session, fake: Any, keys: None) -> None:  # noqa: F811
    server = add_server(db, fake)
    go_free(db)
    pub = client.get("/api/members/vpn/public").json()
    assert pub == {"enabled": True, "test_mode": False, "payments": False, "trial": False, "free": True, "billing": None}
    assert client.post("/api/members/vpn/anon").status_code == 403  # free access goes with a Passport
    c = signed_in(client, db, member_at(db, "egg", "free", paid=False))
    view = c.get("/api/members/vpn").json()
    assert view["eligible"] is True and view["reason"] is None and view["payments"] is False and view["billing"] is None
    assert c.post("/api/members/vpn/usdt", json={"amount_usd": 9.99, "network": "solana", "start_now": True}).status_code == 403
    assert c.post("/api/members/vpn/card").status_code == 403
    assert c.post("/api/members/vpn/devices", json={"name": "phone", "server_id": server.id}).status_code == 201
    assert not SENT and db.scalars(select(VpnPayment)).all() == []  # the provider was never called


def test_closed_stays_closed_even_when_free(client: TestClient, db: Session, fake: Any) -> None:  # noqa: F811
    server = add_server(db, fake)
    db.commit()
    c = signed_in(client, db, member_at(db, "egg", "early", paid=False))
    view = c.get("/api/members/vpn").json()
    assert view["enabled"] is False and view["eligible"] is False
    assert c.post("/api/members/vpn/devices", json={"name": "phone", "server_id": server.id}).status_code == 403


def test_blocked_stays_blocked_when_free(client: TestClient, db: Session, fake: Any) -> None:  # noqa: F811
    server = add_server(db, fake)
    go_free(db)
    m = member_at(db, "egg", "bad", paid=False)
    acct = vpn.account_of(db, m)
    db.commit()
    vpn.set_blocked(db, acct.id, True)
    c = signed_in(client, db, m)
    assert c.get("/api/members/vpn").json()["eligible"] is False
    assert c.post("/api/members/vpn/devices", json={"name": "phone", "server_id": server.id}).status_code == 403


def test_switching_payments_on_and_off(client: TestClient, db: Session, fake: Any, keys: None) -> None:  # noqa: F811
    server = add_server(db, fake)
    go_free(db)
    c = signed_in(client, db, member_at(db, "egg", "switch", paid=False))
    public_key = c.post("/api/members/vpn/devices", json={"name": "phone", "server_id": server.id}).json()["device"]
    dev = db.scalars(select(VpnDevice)).one()
    assert public_key["id"] == dev.id and dev.public_key in fake.peers

    # on: no paid time means off at the next sync, the device kept; the payment routes work again
    assert client.put("/api/vpn/admin/settings", json={"payments_enabled": True}).json()["payments_enabled"] is True
    assert c.get("/api/members/vpn").json()["billing"]["usdt"] is True
    vpn.sync_all(db)
    db.refresh(dev)
    assert dev.public_key not in fake.peers and db.get(VpnDevice, dev.id) is not None
    assert c.post("/api/members/vpn/usdt", json={"amount_usd": 5, "network": "solana", "start_now": True}).status_code == 200

    # an invoice from while payments were on still pays out after they're off
    order = db.scalars(select(VpnPayment)).one().order_ref
    assert client.put("/api/vpn/admin/settings", json={"payments_enabled": False}).status_code == 200
    assert ipn(c, order, "finished", 5, "usdtsol").status_code == 200
    assert db.scalars(select(VpnPayment)).one().status == "credited"

    # off again: back on at the next sync, nothing was lost
    vpn.sync_all(db)
    db.refresh(dev)
    assert dev.public_key in fake.peers and db.get(VpnAccount, dev.account_id).lapsed_at is None


def test_payments_cant_be_turned_on_with_no_provider(client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(billing, "can_charge", lambda: False)
    r = client.put("/api/vpn/admin/settings", json={"payments_enabled": True})
    assert r.status_code == 400 and "keys" in r.json()["detail"]
    assert client.put("/api/vpn/admin/settings", json={"payments_enabled": False}).status_code == 200  # turning off always works
    assert vpn.settings(db).payments_enabled is False


def test_admin_makes_a_device_while_closed_and_whatever_the_payment_setting(client: TestClient, db: Session, fake: Any) -> None:  # noqa: F811
    server = add_server(db, fake)
    assert vpn.settings(db).enabled is False
    db.commit()
    r = client.post("/api/vpn/admin/devices", json={"name": "my phone", "server_id": server.id})
    assert r.status_code == 201
    out = r.json()
    assert "PrivateKey" in out["config"] and out["filename"].endswith(".conf")
    dev = db.get(VpnDevice, out["device"]["id"])
    assert dev is not None and dev.public_key in fake.peers
    assert "PrivateKey" not in str(dev.__dict__)  # only its public half is kept

    # still free once payments are on, and the sync leaves it alone
    vpn.update_settings(db, {"payments_enabled": True})
    vpn.sync_all(db)
    db.refresh(dev)
    assert dev.public_key in fake.peers

    # over the member device limit, under the owner's
    vpn.update_settings(db, {"max_devices": 1})
    assert client.post("/api/vpn/admin/devices", json={"name": "laptop", "server_id": server.id}).status_code == 201

    # in Customers as "Admin", can't be blocked as a whole, each device can be revoked
    row = next(c for c in client.get("/api/vpn/admin/customers").json()["items"] if c["owner"])
    assert row["label"] == "Admin" and row["anonymous"] is False and row["state"] == "active" and len(row["devices"]) == 2
    assert client.post(f"/api/vpn/admin/accounts/{row['id']}/block", json={"blocked": True}).status_code == 400
    assert client.delete(f"/api/vpn/admin/devices/{dev.id}").status_code == 204
    assert dev.public_key not in fake.peers
    assert db.scalars(select(VpnAccount).where(VpnAccount.is_owner.is_(True))).one().id == row["id"]  # one owner account, reused


def test_a_data_cap_never_pauses_the_owners_devices(client: TestClient, db: Session, fake: Any) -> None:  # noqa: F811
    server = add_server(db, fake)
    db.commit()
    assert client.post("/api/vpn/admin/devices", json={"name": "my phone", "server_id": server.id}).status_code == 201
    dev = db.scalars(select(VpnDevice)).one()
    dev.rx_bytes = 5 * vpn.GB
    dev.usage_month = utcnow().strftime("%Y-%m")
    db.commit()
    vpn.update_settings(db, {"monthly_cap_gb": 1})
    vpn.enforce_caps(db)
    db.refresh(dev)
    assert dev.paused_until is None
