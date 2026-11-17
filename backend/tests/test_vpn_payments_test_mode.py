"""Kiwi VPN payments' test mode (VPN_PAYMENTS_PUBLIC unset, the production default) and the first real payment's safety nets: only the
owner signed in to Admin and named testers can pay while it's on; money that arrives is credited exactly once even when the webhook
and vpn.reconcile report it at the same moment; and what the webhook saw is visible to Admin. Fake payments only: nothing here reaches
a real service. conftest's _vpn_payments_public fixture skips this module, so the production default applies here."""

import hashlib
import hmac
import json
import logging
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.settings import get_settings
from app.db.base import utcnow
from app.db.session import SessionLocal
from app.security import admin_auth
from app.models.vpn import VpnAccount, VpnPayment
from app.vpn import billing, service as vpn
from tests.test_vpn import add_server, fake, member_at, open_vpn, signed_in  # noqa: F401
from tests.test_vpn_billing import IPN, SENT, days_left, ipn, keys  # noqa: F401

PAY = {"amount_usd": 9.99, "network": "solana"}


def owner_cookie(c: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(admin_auth, "current", lambda _db, token: object() if token == "owner" else None)
    c.cookies.set(admin_auth.COOKIE, "owner")


def last_payment(db: Session) -> VpnPayment:
    db.expire_all()
    return db.scalars(select(VpnPayment).order_by(VpnPayment.id.desc())).first()


def test_the_default_is_test_mode() -> None:
    assert get_settings().vpn_payments_public is False
    assert vpn.payments_public() is False


def test_switching_payments_on_changes_nothing_for_the_public(client: TestClient, db: Session, keys: None) -> None:  # noqa: F811
    open_vpn(db, trial_enabled=True)
    pub = client.get("/api/members/vpn/public").json()
    assert pub["payments"] is False and pub["billing"] is None and pub["trial"] is True and pub["free"] is False
    assert client.post("/api/members/vpn/anon").status_code == 403  # no anonymous keys for the public either
    m = member_at(db, "warrior", "public", paid=False)
    c = signed_in(client, db, m)
    out = c.get("/api/members/vpn").json()
    assert out["payments"] is False and out["billing"] is None and out["payments_test"] is False
    r = c.post("/api/members/vpn/usdt", json=PAY)
    assert r.status_code == 403
    assert not SENT and db.scalars(select(VpnPayment)).all() == []  # the provider was never asked
    # once their trial has ended they're told the plan isn't open, not sent to a payment they can't make
    acct = vpn.account_of(db, m)
    acct.trial_started_at, acct.trial_ends_at = utcnow(), utcnow()
    db.commit()
    assert vpn.no_access_reason(db, acct) == vpn.PAID_UNAVAILABLE


def test_trial_off_and_payments_in_test_mode_stays_free_for_everyone(db: Session, keys: None) -> None:  # noqa: F811
    open_vpn(db, trial_enabled=False)  # payments on, trial off: with public payments that would mean paid only
    assert vpn.is_free(vpn.settings(db)) is True


def test_the_owner_signed_in_to_admin_can_pay_and_sees_it_is_a_test(client: TestClient, db: Session, fake: Any, keys: None, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    add_server(db, fake)
    open_vpn(db, trial_enabled=True)
    m = member_at(db, "warrior", "owner", paid=False)
    c = signed_in(client, db, m)
    owner_cookie(c, monkeypatch)
    out = c.get("/api/members/vpn").json()
    assert out["payments"] is True and out["payments_test"] is True and out["billing"] is not None
    assert c.get("/api/members/vpn/public").json()["payments"] is True
    r = c.post("/api/members/vpn/usdt", json=PAY)
    assert r.status_code == 200, r.text
    pay = last_payment(db)
    assert ipn(c, pay.order_ref, "finished", 9.99, "usdtsol").status_code == 200
    acct = db.scalar(select(VpnAccount).where(VpnAccount.member_id == m.id))
    db.refresh(acct)
    assert 29.9 < days_left(acct) <= 30.0 and last_payment(db).status == "credited"
    c.cookies.delete(admin_auth.COOKIE)  # without Admin: the same member can't start another
    assert c.post("/api/members/vpn/usdt", json=PAY).status_code == 403


def test_a_named_tester_can_pay_in_test_mode(client: TestClient, db: Session, keys: None) -> None:  # noqa: F811
    open_vpn(db, trial_enabled=True)
    m = member_at(db, "warrior", "tess", paid=False)
    vpn.set_tester(db, "tess", 7)
    c = signed_in(client, db, m)
    assert c.get("/api/members/vpn").json()["payments_test"] is True
    assert c.post("/api/members/vpn/usdt", json=PAY).status_code == 200


def test_going_public_opens_payments_to_everyone(client: TestClient, db: Session, keys: None, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    monkeypatch.setattr(get_settings(), "vpn_payments_public", True, raising=False)
    open_vpn(db, trial_enabled=True)
    assert client.get("/api/members/vpn/public").json()["payments"] is True
    c = signed_in(client, db, member_at(db, "warrior", "buyer", paid=False))
    out = c.get("/api/members/vpn").json()
    assert out["payments"] is True and out["payments_test"] is False
    assert c.post("/api/members/vpn/usdt", json=PAY).status_code == 200
    assert client.get("/api/vpn/admin/overview").json()["settings"]["payments_public"] is True


def test_a_webhook_still_credits_after_payments_are_switched_off(client: TestClient, db: Session, keys: None) -> None:  # noqa: F811
    open_vpn(db, trial_enabled=True)
    m = member_at(db, "warrior", "late", paid=False)
    vpn.set_tester(db, "late", 7)
    c = signed_in(client, db, m)
    assert c.post("/api/members/vpn/usdt", json=PAY).status_code == 200
    vpn.update_settings(db, {"payments_enabled": False})
    assert ipn(c, last_payment(db).order_ref, "finished", 9.99, "usdtsol").status_code == 200
    assert last_payment(db).status == "credited"


# --- crediting exactly once ----------------------------------------------------------------------------------------------


def _pending(db: Session, acct: VpnAccount) -> int:
    pay = VpnPayment(account_id=acct.id, provider="nowpayments", order_ref="race-1", provider_ref="555", network="solana", amount_usd=9.99, pay_amount=9.99)
    db.add(pay)
    db.commit()
    return pay.id


def test_the_webhook_and_reconcile_reporting_at_once_credit_once(db: Session, keys: None) -> None:  # noqa: F811
    m = member_at(db, "warrior", "racer", paid=False)
    acct = vpn.account_of(db, m)
    db.commit()
    pay_id = _pending(db, acct)
    first, second = SessionLocal(), SessionLocal()
    try:
        a, b = first.get(VpnPayment, pay_id), second.get(VpnPayment, pay_id)  # both read it unpaid
        assert a.paid_usd == 0 and b.paid_usd == 0
        now = utcnow()
        billing._apply_np(first, a, "finished", "9.99", "usdtsol", now)
        first.commit()
        status, credited = billing._apply_np(second, b, "finished", "9.99", "usdtsol", now)  # its copy still says unpaid
        second.commit()
        assert credited is None  # nothing was left to credit
    finally:
        first.close()
        second.close()
    db.expire_all()
    pay, acct = db.get(VpnPayment, pay_id), db.get(VpnAccount, acct.id)
    assert pay.paid_usd == 9.99 and pay.seconds == billing.seconds_for(9.99)
    assert 29.9 < days_left(acct) <= 30.0  # 30 days, not 60


def test_a_part_payment_then_the_rest_at_once_adds_only_the_rest(db: Session, keys: None) -> None:  # noqa: F811
    m = member_at(db, "warrior", "parts", paid=False)
    acct = vpn.account_of(db, m)
    db.commit()
    pay_id = _pending(db, acct)
    first, second = SessionLocal(), SessionLocal()
    try:
        a, b = first.get(VpnPayment, pay_id), second.get(VpnPayment, pay_id)
        billing._apply_np(first, a, "partially_paid", "5", "usdtsol", utcnow())
        first.commit()
        billing._apply_np(second, b, "finished", "9.99", "usdtsol", utcnow())  # stale: read before the part payment was credited
        second.commit()
    finally:
        first.close()
        second.close()
    db.expire_all()
    pay = db.get(VpnPayment, pay_id)
    assert pay.paid_usd == 9.99 and pay.seconds == billing.seconds_for(5) + billing.seconds_for(4.99)
    assert 29.9 < days_left(db.get(VpnAccount, acct.id)) <= 30.0


def test_a_stale_expired_report_never_marks_a_credited_payment_expired(db: Session, keys: None) -> None:  # noqa: F811
    m = member_at(db, "warrior", "stale", paid=False)
    acct = vpn.account_of(db, m)
    db.commit()
    pay_id = _pending(db, acct)
    first, second = SessionLocal(), SessionLocal()
    try:
        a, b = first.get(VpnPayment, pay_id), second.get(VpnPayment, pay_id)
        billing._apply_np(first, a, "finished", "9.99", "usdtsol", utcnow())
        first.commit()
        billing._apply_np(second, b, "expired", "0", "usdtsol", utcnow())  # read as pending before the credit
        second.commit()
    finally:
        first.close()
        second.close()
    db.expire_all()
    assert db.get(VpnPayment, pay_id).status == "credited"


# --- what the webhook saw ------------------------------------------------------------------------------------------------


def test_admin_sees_signed_and_refused_webhooks_and_the_log_names_the_form(client: TestClient, db: Session, keys: None, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:  # noqa: F811
    monkeypatch.setattr(billing, "ipn", {**billing.ipn, "signed": 0, "bad_signature": 0, "last_form": None, "credited_by_webhook": 0})
    m = member_at(db, "warrior", "seen", paid=False)
    acct = vpn.account_of(db, m)
    db.commit()
    order = db.get(VpnPayment, _pending(db, acct)).order_ref
    caplog.set_level(logging.INFO, logger="app.vpn.billing")
    assert ipn(client, order, "finished", 9.99, "usdtsol", secret="wrong-secret").status_code == 401
    assert billing.ipn["bad_signature"] == 1 and billing.ipn["last_bad_signature_at"]
    assert "webhook refused: bad or missing signature" in caplog.text
    # the provider's raw body, signed as sent
    body = json.dumps({"payment_id": 555, "payment_status": "finished", "pay_currency": "usdtsol", "actually_paid": 9.99, "order_id": order})
    sig = hmac.new(IPN.encode(), body.encode(), hashlib.sha512).hexdigest()
    assert client.post("/api/members/vpn/webhooks/nowpayments", content=body, headers={"x-nowpayments-sig": sig}).status_code == 200
    assert billing.ipn["signed"] == 1 and billing.ipn["last_form"] == "raw" and billing.ipn["credited_by_webhook"] == 1
    assert "signed form raw" in caplog.text and "credited +" in caplog.text
    assert IPN not in caplog.text and sig not in caplog.text  # no secret or signature in the log
    assert client.get("/api/vpn/admin/overview").json()["billing"]["ipn"]["signed"] == 1


def test_every_signed_form_has_a_name() -> None:
    raw = json.dumps({"b": 1, "a": {"d": 0.00005, "c": 2}}).encode()
    named = billing._np_named_forms(raw)
    assert [f for _, f in named] == billing._np_signed_forms(raw)
    assert len({n for n, _ in named}) == len(named) and named[0][0] == "raw"
    for name, form in named:
        sig = hmac.new(b"s", form, hashlib.sha512).hexdigest()
        assert billing._np_signature_form(raw, sig, "s") == name
    assert billing._np_signature_form(raw, "0" * 128, "s") is None and billing._np_signature_form(raw, None, "s") is None
