"""Audit VPN-1 / VPN-11: the rebased Kiwi VPN payments. Payments stay off until the owner turns them on, the trial's end says where to pay
once they are on (the apps show `reason` as it is), a signed message with a non-number amount credits nothing, and the boot self-check
names the payment jobs if production's job list leaves them out. Fake payments only."""

import datetime as dt
import hashlib
import hmac
import logging
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import observability
from app.config.settings import Settings
from app.db.base import utcnow
from app.db.session import SessionLocal
from app.models.vpn import VpnAccount, VpnPayment
from app.vpn import billing, service as vpn
from tests.test_vpn import member_at, open_vpn, signed_in
from tests.test_vpn_billing import IPN, keys  # noqa: F401

HOOK = "/api/members/vpn/webhooks/nowpayments"


def _end_trial(db: Session, member_id: int) -> None:
    acct = db.scalars(select(VpnAccount).where(VpnAccount.member_id == member_id)).one()
    acct.trial_ends_at = utcnow() - dt.timedelta(seconds=1)
    db.commit()


def test_payments_are_off_on_a_fresh_install_even_with_provider_keys(client: TestClient, db: Session, keys: None) -> None:  # noqa: F811
    vpn.update_settings(db, {"enabled": True})
    db.commit()
    out = client.get("/api/members/vpn/public").json()
    assert out["payments"] is False and out["billing"] is None and out["trial"] is True
    assert client.post("/api/members/vpn/anon").status_code == 403


def test_the_trial_end_says_where_to_pay_once_payments_are_on(client: TestClient, db: Session, keys: None) -> None:  # noqa: F811
    open_vpn(db, trial_enabled=True)
    db.commit()
    m = member_at(db, "egg", "ender", paid=False)
    c = signed_in(client, db, m)
    assert c.post("/api/members/vpn/trial").status_code == 200
    _end_trial(db, m.id)
    out = c.get("/api/members/vpn").json()
    assert out["eligible"] is False and out["billing"] is not None
    assert "trial has ended" in out["reason"] and "pricechat.live/vpn" in out["reason"]


def test_paid_time_running_out_says_where_to_pay(db: Session, keys: None) -> None:  # noqa: F811
    open_vpn(db)
    acct, _ = billing.new_anonymous(db)
    acct.paid_until = utcnow() - dt.timedelta(days=1)
    db.commit()
    assert "pricechat.live/vpn" in vpn.no_access_reason(db, acct)


def test_with_payments_off_the_trial_end_still_says_the_plan_isnt_open(client: TestClient, db: Session) -> None:
    vpn.update_settings(db, {"enabled": True})
    db.commit()
    m = member_at(db, "egg", "offend", paid=False)
    c = signed_in(client, db, m)
    assert c.post("/api/members/vpn/trial").status_code == 200
    _end_trial(db, m.id)
    assert c.get("/api/members/vpn").json()["reason"] == vpn.PAID_UNAVAILABLE


def _signed(client: TestClient, text: str) -> Any:
    return client.post(HOOK, content=text, headers={"x-nowpayments-sig": hmac.new(IPN.encode(), text.encode(), hashlib.sha512).hexdigest()})


def test_a_signed_message_with_an_infinite_or_nan_amount_credits_nothing(client: TestClient, db: Session, keys: None) -> None:  # noqa: F811
    open_vpn(db)
    acct, _ = billing.new_anonymous(db)
    billing.start_usdt(db, acct, 9.99, "solana")
    order = db.scalars(select(VpnPayment).order_by(VpnPayment.id.desc())).first().order_ref
    for amount in ('"Infinity"', '"NaN"', "Infinity"):
        text = f'{{"actually_paid":{amount},"order_id":"{order}","pay_currency":"usdtsol","payment_id":555,"payment_status":"finished"}}'
        assert _signed(client, text).status_code == 400
    db.expire_all()
    assert db.get(VpnAccount, acct.id).paid_until is None
    assert db.scalars(select(VpnPayment).where(VpnPayment.order_ref == order)).one().paid_usd == 0


def test_the_self_check_names_the_payment_jobs_when_payments_are_on_but_not_scheduled(caplog: Any, monkeypatch: Any) -> None:
    names = ["vpn.sync", "vpn.reconcile", "vpn.reminders", "system.alerts"]
    caplog.set_level(logging.INFO, logger="pricechat.ops")
    monkeypatch.setattr(observability, "DPAPI", False)
    with SessionLocal() as db:
        vpn.update_settings(db, {"enabled": True, "payments_enabled": True})
        db.commit()
        problems = observability.startup_self_check(Settings(scheduler_only_jobs="vpn.sync,system.", app_encryption_key="k" * 44), names, db)
        text = "; ".join(problems)
        assert "vpn.reconcile" in text and "vpn.reminders" in text
        assert observability.startup_self_check(Settings(scheduler_only_jobs="vpn.,system.", app_encryption_key="k" * 44), names, db) == []
        vpn.update_settings(db, {"payments_enabled": False, "enabled": False})
        db.commit()
        assert observability.startup_self_check(Settings(scheduler_only_jobs="vpn.sync,system.", app_encryption_key="k" * 44), names, db) == []
