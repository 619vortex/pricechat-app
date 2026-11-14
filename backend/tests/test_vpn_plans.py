"""Kiwi VPN's plans: the 30-day trial (off until Admin enables it, one per account), Royal Kiwi from the real rank, and real public statistics."""

import datetime as dt
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.vpn import VpnAccount, VpnBrowser, VpnDevice
from app.vpn import service as vpn
from tests.test_vpn import FakeNode, add_server, member_at, open_vpn, signed_in
from tests.test_vpn_browser import fake  # noqa: F401

TRIAL = "/api/members/vpn/trial"


def me(c: TestClient) -> dict:
    return c.get("/api/members/vpn").json()


def trial_on(db: Session) -> None:
    open_vpn(db, trial_enabled=True)
    db.commit()


def test_the_trial_is_off_until_enabled_even_when_open_and_paid(client: TestClient, db: Session) -> None:
    open_vpn(db)
    db.commit()
    c = signed_in(client, db, member_at(db, "egg", "early", paid=False))
    assert me(c)["trial"] == {"available": False, "days": 30, "code": "off", "reason": "The free trial isn't available right now."}
    assert c.post(TRIAL).status_code == 403
    assert me(c)["plan"]["kind"] == "not_activated"


def test_it_cant_start_while_closed_and_free_for_all_takes_both_switches_off(client: TestClient, db: Session) -> None:
    assert vpn.settings(db).trial_enabled is True and vpn.settings(db).enabled is False and vpn.settings(db).payments_enabled is False  # as it launches
    db.commit()
    c = signed_in(client, db, member_at(db, "egg", "closed", paid=False))
    assert c.post(TRIAL).status_code == 403  # closed
    vpn.update_settings(db, {"enabled": True, "trial_enabled": False})
    db.commit()
    assert c.post(TRIAL).status_code == 403  # free for everyone: nothing to start
    assert me(c)["plan"]["kind"] == "free"


def test_the_launch_rules_with_payments_off_trial_then_unavailable(client: TestClient, db: Session) -> None:
    vpn.update_settings(db, {"enabled": True})  # payments stay off, the trial stays on
    db.commit()
    m = member_at(db, "egg", "newcomer", paid=False)
    c = signed_in(client, db, m)
    before = me(c)
    assert before["eligible"] is False and before["plan"]["kind"] == "not_activated" and before["trial"]["available"] is True
    assert "free 30-day trial" in before["reason"] and before["billing"] is None
    out = c.post(TRIAL).json()
    assert out["eligible"] is True and out["plan"]["kind"] == "trial" and out["plan"]["days_left"] == 30
    acct = db.query(VpnAccount).filter_by(member_id=m.id).one()
    db.refresh(acct)
    acct.trial_ends_at = utcnow() - dt.timedelta(seconds=1)
    db.commit()
    after = me(c)
    assert after["eligible"] is False and after["plan"]["kind"] == "expired" and "isn't available yet" in after["reason"] and after["billing"] is None
    assert c.post(TRIAL).status_code == 409


def test_no_automatic_billing_is_offered(monkeypatch) -> None:
    from app.vpn import billing

    monkeypatch.setattr(billing, "_card_on", lambda: True)
    monkeypatch.setattr(billing, "_usdt_on", lambda: False)
    assert billing.options()["card"] is False and billing.can_charge() is False


def test_the_launch_migration_closes_it_and_sets_three_devices() -> None:
    text = (Path(__file__).resolve().parents[1] / "migrations" / "versions" / "0100_vpn_trial.py").read_text(encoding="utf-8")
    assert "SET enabled = :off, max_devices = 3" in text and "server_default=sa.true()" in text


def test_a_trial_gives_30_days_once_and_never_again(client: TestClient, db: Session) -> None:
    trial_on(db)
    m = member_at(db, "egg", "trial", paid=False)
    c = signed_in(client, db, m)
    assert me(c)["trial"]["available"] is True and me(c)["eligible"] is False
    r = c.post(TRIAL)
    assert r.status_code == 200
    out = r.json()
    assert out["eligible"] is True and out["plan"]["kind"] == "trial" and out["plan"]["days_left"] == 30
    assert out["trial"]["available"] is False
    assert c.post(TRIAL).status_code == 409
    acct = db.query(VpnAccount).filter_by(member_id=m.id).one()
    db.refresh(acct)
    acct.trial_ends_at = utcnow() - dt.timedelta(days=1)
    db.commit()
    after = me(c)
    assert after["eligible"] is False and after["plan"]["kind"] == "expired" and after["trial"]["available"] is False
    assert c.post(TRIAL).status_code == 409


def test_a_signed_out_visitor_or_anonymous_key_cant_start_one(client: TestClient, db: Session) -> None:
    trial_on(db)
    assert client.post(TRIAL).status_code == 401
    assert client.get("/api/members/vpn/public").json()["trial"] is True


def test_no_trial_is_offered_to_someone_who_already_has_access(client: TestClient, db: Session) -> None:
    trial_on(db)
    c = signed_in(client, db, member_at(db, "royal", "queen", paid=False))
    out = me(c)
    assert out["eligible"] is True and out["plan"]["kind"] == "royal" and out["trial"]["available"] is False
    assert c.post(TRIAL).status_code == 409


def test_royal_comes_from_the_real_rank_and_a_lower_rank_gets_nothing(client: TestClient, db: Session) -> None:
    open_vpn(db)
    db.commit()
    c = signed_in(client, db, member_at(db, "knight", "almost", paid=False))
    out = me(c)
    assert out["eligible"] is False and out["plan"]["kind"] == "not_activated" and out["royal"] is False
    # no input can set a rank: the route takes no body, and the page only reads what the server says
    client.cookies.clear()
    high = signed_in(client, db, member_at(db, "mythic", "top", paid=False))
    out = me(high)
    assert out["eligible"] is True and out["royal"] is True and out["plan"]["kind"] == "royal" and out["plan"]["until"] is None


def test_losing_the_rank_or_having_xp_suspended_ends_royal_access(client: TestClient, db: Session) -> None:
    open_vpn(db)
    db.commit()
    m = member_at(db, "royal", "fallen", paid=False)
    c = signed_in(client, db, m)
    assert me(c)["plan"]["kind"] == "royal"
    m.xp_suspended = True
    db.commit()
    assert me(c)["eligible"] is False
    m.xp_suspended, m.rank_level = False, 6
    db.commit()
    assert me(c)["eligible"] is False


def test_royal_doesnt_open_a_closed_vpn(client: TestClient, db: Session) -> None:
    c = signed_in(client, db, member_at(db, "royal", "closed-royal", paid=False))
    assert vpn.settings(db).enabled is False
    db.commit()
    out = me(c)
    assert out["enabled"] is False and out["eligible"] is False


def test_paid_time_shows_as_paid_with_its_end_date(client: TestClient, db: Session) -> None:
    open_vpn(db)
    db.commit()
    m = member_at(db, "egg", "payer", paid=False)
    c = signed_in(client, db, m)
    me(c)
    acct = db.query(VpnAccount).filter_by(member_id=m.id).one()
    db.refresh(acct)
    acct.paid_until = utcnow() + dt.timedelta(days=10)
    db.commit()
    plan = me(c)["plan"]
    assert plan["kind"] == "paid" and plan["days_left"] == 10 and plan["until"]


def test_stats_are_real_and_include_no_private_server_details(client: TestClient, db: Session, fake: FakeNode) -> None:  # noqa: F811
    empty = client.get("/api/members/vpn/stats").json()
    assert empty == {"active_users": 0, "countries": 0, "online_servers": 0, "uptime": None, "servers": []}
    server = add_server(db, fake, "Helsinki 1")
    server.country_code, server.online = "FI", True
    other = add_server(db, fake, "Offline 1")
    other.country_code, other.online = "DE", False
    db.flush()
    a, b, old = VpnAccount(), VpnAccount(), VpnAccount()
    db.add_all([a, b, old])
    db.flush()
    now = utcnow()
    db.add_all([VpnDevice(account_id=a.id, server_id=server.id, name="a", public_key="k1", ip="10.66.0.2", last_handshake=now),
                VpnDevice(account_id=a.id, server_id=server.id, name="a2", public_key="k2", ip="10.66.0.3", last_handshake=now),
                VpnBrowser(account_id=b.id, server_id=server.id, username="b", secret_hash="x" * 64, last_active=now),
                VpnDevice(account_id=old.id, server_id=server.id, name="o", public_key="k3", ip="10.66.0.4", last_handshake=now - dt.timedelta(hours=1))])
    db.commit()
    out = client.get("/api/members/vpn/stats").json()
    assert out["active_users"] == 2 and out["countries"] == 1 and out["online_servers"] == 1 and out["uptime"] is None
    assert [s["name"] for s in out["servers"]] == ["Helsinki 1", "Offline 1"]
    assert set(out["servers"][0]) == {"id", "name", "region", "country_code", "online", "recommended", "browser"}
