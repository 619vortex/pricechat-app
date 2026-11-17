"""Kiwi VPN refunds: the Admin action must do what the published refund policy (/vpn/refunds) says. Fake payments only: no money
moves and no real provider is called.

The policy: a cancellation within 14 days refunds the unused part of that payment at its own rate (the whole payment when the
customer never gave the start-now consent); other refunds are the unused part less fees; only unused time ends; once only."""

import datetime as dt
import hashlib
import hmac
import json
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.members import MemberAuditLog
from app.models.vpn import VpnAccount, VpnPayment
from app.vpn import billing, service as vpn
from tests.test_vpn_billing import IPN, keys  # noqa: F401

DAY = 86400
T0 = utcnow().replace(microsecond=0)


def at(days: float) -> dt.datetime:
    return T0 + dt.timedelta(days=days)


def report(db: Session, pay: VpnPayment, status: str, paid: float, now: dt.datetime) -> tuple[str, int | None]:
    raw = json.dumps({"order_id": pay.order_ref, "payment_status": status, "actually_paid": paid, "pay_currency": "usdtsol", "payment_id": pay.provider_ref}).encode()
    sig = hmac.new(IPN.encode(), json.dumps(json.loads(raw), sort_keys=True, separators=(",", ":")).encode(), hashlib.sha512).hexdigest()
    return billing.nowpayments_webhook(db, raw, sig, now)


def paid(db: Session, acct: VpnAccount, now: dt.datetime, usd: float = 9.99, start_now: bool = True) -> VpnPayment:
    billing.start_usdt(db, acct, usd, "solana", start_now=start_now)
    pay = db.scalars(select(VpnPayment).order_by(VpnPayment.id.desc())).first()
    report(db, pay, "finished", usd, now)
    db.refresh(pay)
    assert pay.status == "credited"
    return pay


def until(db: Session, acct: VpnAccount) -> dt.datetime:
    db.expire_all()
    return billing._aware(db.get(VpnAccount, acct.id).paid_until)


@pytest.fixture
def acct(db: Session, keys: None) -> VpnAccount:  # noqa: F811
    a, _ = billing.new_anonymous(db)
    return a


def test_a_cancellation_refunds_only_the_unused_part_and_ends_only_that_time(db: Session, acct: VpnAccount) -> None:
    pay = paid(db, acct, at(0))
    q = billing.refund_quote(db, pay, at(10))  # 10 of 30 days used
    assert q["within_cancel_window"] and q["start_now"]
    assert q["days_unused"] == pytest.approx(20, abs=0.01) and q["unused_usd"] == pytest.approx(6.66, abs=0.01)
    assert q["max_usd"] == {"cancel": q["unused_usd"], "other": q["unused_usd"]}
    assert until(db, acct) == at(30)  # a quote changes nothing
    out = billing.refund_by_hand(db, pay, "customer cancelled", now=at(10))
    assert out["kind"] == "cancel" and out["refunded_usd"] == q["unused_usd"]
    assert until(db, acct) == at(10)  # access ends now; the 10 used days stay used
    db.refresh(pay)
    assert pay.status == "refunded" and pay.seconds == pytest.approx(10 * DAY, abs=2) and pay.paid_usd == pytest.approx(9.99 - q["unused_usd"], abs=0.01)
    assert "6.66" in pay.note and "cancel" in pay.note
    row = db.scalars(select(MemberAuditLog).where(MemberAuditLog.action == billing.REFUND_ACTION)).one()
    assert row.details["payment_id"] == pay.id and row.details["usd"] == q["unused_usd"] and row.actor == "admin"


def test_without_the_start_now_consent_a_cancellation_refunds_the_whole_payment(db: Session, acct: VpnAccount) -> None:
    pay = paid(db, acct, at(0), start_now=False)
    q = billing.refund_quote(db, pay, at(10))
    assert not q["start_now"] and q["max_usd"]["cancel"] == 9.99 and q["max_usd"]["other"] == pytest.approx(6.66, abs=0.01)
    billing.refund_by_hand(db, pay, "cancelled, no consent on record", now=at(10))
    db.refresh(pay)
    assert pay.paid_usd == 0 and until(db, acct) == at(10)  # the used days can't be taken back, only the unused ones end


def test_after_14_days_only_an_other_refund_is_possible_and_never_more_than_the_unused_value(db: Session, acct: VpnAccount) -> None:
    pay = paid(db, acct, at(0))
    q = billing.refund_quote(db, pay, at(20))
    assert not q["within_cancel_window"] and q["max_usd"]["cancel"] is None and q["unused_usd"] == pytest.approx(3.33, abs=0.01)
    with pytest.raises(billing.BillingError, match="14 days to cancel ended"):
        billing.refund_by_hand(db, pay, "too late", kind="cancel", now=at(20))
    with pytest.raises(billing.BillingError, match="allows up to \\$3.33"):
        billing.refund_by_hand(db, pay, "too much", usd=5, kind="other", now=at(20))
    for bad in (-1, float("nan"), float("inf")):
        with pytest.raises(billing.BillingError):
            billing.refund_by_hand(db, pay, "bad", usd=bad, kind="other", now=at(20))
    with pytest.raises(billing.BillingError, match="cancel or other"):
        billing.refund_by_hand(db, pay, "bad", kind="full", now=at(20))
    db.refresh(pay)
    assert pay.status == "credited" and until(db, acct) == at(30)  # refused attempts change nothing
    out = billing.refund_by_hand(db, pay, "server fault, less the network fee", usd=3.0, now=at(20))  # less fees: what was really sent
    assert out["kind"] == "other" and out["refunded_usd"] == 3.0 and until(db, acct) == at(20)


def test_a_fully_used_payment_refunds_nothing_and_ends_no_time(db: Session, acct: VpnAccount) -> None:
    first = paid(db, acct, at(0))
    second = paid(db, acct, at(1))  # stacks: days 30-60
    q = billing.refund_quote(db, first, at(31))  # the first payment's days are all used
    assert q["days_unused"] == 0 and q["unused_usd"] == 0
    # refunding the older payment must not eat the newer payment's days
    assert billing.refund_quote(db, second, at(31))["days_unused"] == pytest.approx(29, abs=0.01)
    billing.refund_by_hand(db, first, "goodwill", usd=0, kind="other", now=at(31))
    assert until(db, acct) == at(60)


def test_stacked_payments_the_newest_holds_the_time_left(db: Session, acct: VpnAccount) -> None:
    older = paid(db, acct, at(0))
    newer = paid(db, acct, at(5))  # days 30-60
    assert billing.refund_quote(db, newer, at(5))["days_unused"] == pytest.approx(30, abs=0.01)
    assert billing.refund_quote(db, older, at(5))["days_unused"] == pytest.approx(25, abs=0.01)
    billing.refund_by_hand(db, older, "cancelled the first", now=at(5))
    assert until(db, acct) == at(35)  # 25 unused days of the older one ended; all 30 of the newer one kept
    assert billing.refund_quote(db, newer, at(5))["days_unused"] == pytest.approx(30, abs=0.01)


def test_time_during_a_trial_is_all_unused(db: Session, acct: VpnAccount) -> None:
    acct.trial_ends_at = at(3)
    db.commit()
    pay = paid(db, acct, at(0))  # starts after the trial: days 3-33
    assert until(db, acct) == at(33)
    q = billing.refund_quote(db, pay, at(1))
    assert q["days_unused"] == pytest.approx(30, abs=0.01) and q["max_usd"]["cancel"] == 9.99
    billing.refund_by_hand(db, pay, "cancelled in the trial", now=at(1))
    assert until(db, acct) == at(3)  # back to the trial's end


def test_a_duplicate_request_records_one_refund(db: Session, acct: VpnAccount) -> None:
    pay = paid(db, acct, at(0))
    billing.refund_by_hand(db, pay, "cancelled", now=at(2))
    after = until(db, acct)
    with pytest.raises(billing.BillingError, match="already recorded as refunded"):
        billing.refund_by_hand(db, pay, "double click", now=at(2))
    with pytest.raises(billing.BillingError, match="already recorded"):
        billing.refund_quote(db, pay, at(2))
    assert until(db, acct) == after
    assert len(db.scalars(select(MemberAuditLog).where(MemberAuditLog.action == billing.REFUND_ACTION)).all()) == 1


def test_a_second_admin_racing_the_first_is_refused_by_the_compare_and_swap(db: Session, acct: VpnAccount, monkeypatch: pytest.MonkeyPatch) -> None:
    pay = paid(db, acct, at(0))
    real_quote = billing.refund_quote

    def quote_then_someone_else_refunds(d: Session, p: VpnPayment, now: Any = None) -> dict[str, Any]:
        q = real_quote(d, p, now)  # read while still credited...
        d.execute(billing.update(VpnPayment).where(VpnPayment.id == p.id).values(status="refunded"))  # ...then the other request wins
        return q

    monkeypatch.setattr(billing, "refund_quote", quote_then_someone_else_refunds)
    with pytest.raises(billing.BillingError, match="already recorded as refunded"):
        billing.refund_by_hand(db, pay, "second admin", now=at(2))
    assert until(db, acct) == at(30)  # the loser ended no time


def test_the_provider_cannot_undo_or_repeat_a_refund(db: Session, acct: VpnAccount) -> None:
    pay = paid(db, acct, at(0))
    billing.refund_by_hand(db, pay, "cancelled", now=at(10))
    for status in ("refunded", "finished", "partially_paid", "failed", "expired"):
        report(db, pay, status, 9.99, at(11))
        db.refresh(pay)
        assert pay.status == "refunded", status
    assert until(db, acct) == at(10)


def test_the_providers_own_refund_ends_only_the_unused_time(db: Session, acct: VpnAccount) -> None:
    older = paid(db, acct, at(0))
    paid(db, acct, at(1))  # the newer payment's days 30-60 must survive
    report(db, older, "refunded", 0, at(10))
    db.refresh(older)
    assert older.status == "refunded" and older.paid_usd == 0 and older.seconds == pytest.approx(10 * DAY, abs=2)
    assert until(db, acct) == at(40)


def test_nothing_to_refund_unless_credited(db: Session, acct: VpnAccount) -> None:
    billing.start_usdt(db, acct, 9.99, "solana", start_now=True)
    pay = db.scalars(select(VpnPayment).order_by(VpnPayment.id.desc())).first()
    for status in ("pending", "failed", "expired", "review"):
        pay.status = status
        db.commit()
        with pytest.raises(billing.BillingError, match="Nothing has been credited"):
            billing.refund_by_hand(db, pay, "x", now=at(1))
    pay = paid(db, acct, at(0))
    pay.account_id = None  # an anonymous payment whose link was scrubbed
    db.commit()
    with pytest.raises(billing.BillingError, match="no account"):
        billing.refund_quote(db, pay, at(1))


def test_recording_a_refund_never_calls_the_provider(db: Session, acct: VpnAccount, monkeypatch: pytest.MonkeyPatch) -> None:
    pay = paid(db, acct, at(0))

    def down(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("NOWPayments is down")

    monkeypatch.setattr(billing, "transport", httpx.MockTransport(down))
    billing.refund_quote(db, pay, at(2))
    assert billing.refund_by_hand(db, pay, "sent from the wallet", now=at(2))["status"] == "refunded"


def test_the_admin_routes_quote_then_record(client: TestClient, db: Session, acct: VpnAccount) -> None:
    pay = paid(db, acct, utcnow())
    q = client.get(f"/api/vpn/admin/payments/{pay.id}/refund")
    assert q.status_code == 200 and q.json()["within_cancel_window"] and q.json()["max_usd"]["cancel"] == pytest.approx(9.99, abs=0.01)
    assert client.post(f"/api/vpn/admin/payments/{pay.id}/refund", json={"reason": "x"}).status_code == 422  # a reason is required
    assert client.post(f"/api/vpn/admin/payments/{pay.id}/refund", json={"reason": "asked", "kind": "full"}).status_code == 422
    assert client.post(f"/api/vpn/admin/payments/{pay.id}/refund", json={"reason": "asked", "usd": 50}).status_code == 400
    r = client.post(f"/api/vpn/admin/payments/{pay.id}/refund", json={"reason": "asked", "kind": "cancel", "usd": 9.5})
    assert r.status_code == 200 and r.json()["status"] == "refunded" and r.json()["refunded_usd"] == 9.5
    assert client.post(f"/api/vpn/admin/payments/{pay.id}/refund", json={"reason": "asked"}).status_code == 409
    assert client.get(f"/api/vpn/admin/payments/{pay.id}/refund").status_code == 409
    assert client.get("/api/vpn/admin/payments/999999/refund").status_code == 404
    db.expire_all()
    assert vpn.admin_payments(db)[0]["status"] == "refunded"
