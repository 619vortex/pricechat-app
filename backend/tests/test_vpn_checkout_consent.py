"""Kiwi VPN checkout: the start-now box (EU/UK 14-day right to cancel, /vpn/refunds). No payment starts without it, the provider
is never asked, and every payment the website starts carries its proof in the member audit log, for Passports and anonymous keys
alike. Fake payments only: nothing here reaches a real service."""

from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.members import MemberAuditLog
from app.models.vpn import VpnPayment
from app.vpn import billing, service as vpn
from tests.test_vpn import fake, member_at, open_vpn, signed_in  # noqa: F401
from tests.test_vpn_billing import SENT, keys  # noqa: F401


def consents(db: Session) -> list[MemberAuditLog]:
    db.expire_all()
    return list(db.scalars(select(MemberAuditLog).where(MemberAuditLog.action == billing.CONSENT_ACTION)).all())


def test_a_passport_payment_needs_the_box_and_records_it(client: TestClient, db: Session, keys: None) -> None:  # noqa: F811
    open_vpn(db)
    m = member_at(db, "egg", "consent", paid=False)
    c = signed_in(client, db, m)
    for body in ({"amount_usd": 9.99, "network": "solana"}, {"amount_usd": 9.99, "network": "solana", "start_now": False}):
        r = c.post("/api/members/vpn/usdt", json=body)
        assert r.status_code == 400 and r.headers.get("X-Kiwi-Reason") == "start_now"
        assert "Tick the box" in r.json()["detail"]
    assert not SENT and db.scalars(select(VpnPayment)).all() == [] and consents(db) == []  # nothing made, nothing asked
    r = c.post("/api/members/vpn/usdt", json={"amount_usd": 9.99, "network": "solana", "start_now": True})
    assert r.status_code == 200, r.text
    pay = db.scalars(select(VpnPayment)).one()
    [row] = consents(db)
    assert row.member_id == m.id and row.actor == "member"
    assert row.details == {"payment_id": pay.id, "receipt": f"KV-{pay.id:06d}", "consent": billing.START_NOW_CONSENT}
    # Admin sees it on the payment
    [shown] = vpn.admin_payments(db)
    assert shown["id"] == pay.id and shown["start_now"] is True


def test_an_anonymous_payment_records_only_the_payment(client: TestClient, db: Session, keys: None) -> None:  # noqa: F811
    open_vpn(db)
    key = client.post("/api/members/vpn/anon").json()["key"]
    head = {"x-kiwi-key": key}
    assert client.post("/api/members/vpn/anon/usdt", json={"amount_usd": 5, "network": "solana"}, headers=head).status_code == 400
    assert client.post("/api/members/vpn/anon/usdt", json={"amount_usd": 5, "network": "solana", "start_now": True}, headers=head).status_code == 200
    pay = db.scalars(select(VpnPayment)).one()
    [row] = consents(db)
    assert row.member_id is None and row.actor == "anonymous"
    assert row.details["payment_id"] == pay.id
    assert key not in str(row.details) and "TAddr" not in str(row.details)  # no key, no address


def test_the_box_is_checked_after_the_payment_switches(client: TestClient, db: Session, keys: None) -> None:  # noqa: F811
    """Payments off: the answer is still 'payments are off' (403), whatever the box says. Nothing is recorded."""
    open_vpn(db)
    s = vpn.settings(db)
    s.payments_enabled = False
    db.commit()
    c = signed_in(client, db, member_at(db, "egg", "off", paid=False))
    assert c.post("/api/members/vpn/usdt", json={"amount_usd": 9.99, "network": "solana"}).status_code == 403
    assert c.post("/api/members/vpn/usdt", json={"amount_usd": 9.99, "network": "solana", "start_now": True}).status_code == 403
    assert consents(db) == []


def test_a_payment_without_a_consent_row_shows_as_such_in_admin(db: Session, keys: None) -> None:  # noqa: F811
    """Payments started before this change (or by hand) carry no row: Admin says so rather than guessing."""
    open_vpn(db)
    acct = vpn.account_of(db, member_at(db, "egg", "old", paid=False))
    db.commit()
    billing.start_usdt(db, acct, 9.99, "solana")
    assert consents(db) == []
    assert vpn.admin_payments(db)[0]["start_now"] is False


def test_the_route_rejects_unknown_fields_still(client: TestClient, db: Session, keys: None, fake: Any) -> None:  # noqa: F811
    open_vpn(db)
    c = signed_in(client, db, member_at(db, "egg", "strict", paid=False))
    assert c.post("/api/members/vpn/usdt", json={"amount_usd": 9.99, "network": "solana", "start_now": True, "extra": 1}).status_code == 422
