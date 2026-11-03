"""Kiwi VPN billing: time by amount, USDT webhooks, anonymous keys, cards, lapsed devices and the privacy scrub. TEST FIXTURES only."""

import datetime as dt
import hashlib
import hmac
import json
import time
from typing import Any

import httpx
import pytest
from pydantic import SecretStr
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.settings import get_settings
from app.db.base import utcnow
from app.models.vpn import VpnAccount, VpnDevice, VpnPayment
from app.vpn import billing, service as vpn
from tests.test_vpn import add_server, fake, grant, member_at, open_vpn, signed_in  # noqa: F401
from tests.test_production import prod  # noqa: F401

DAY = 86400
IPN, HOOK = "ipn-test-secret", "whsec_test"
SENT: list[httpx.Request] = []
MINS = {"usdttrc20": 10.9507}
RATE = {"coins_per_usd": 1.0}


@pytest.fixture
def keys(monkeypatch: pytest.MonkeyPatch) -> None:
    s = get_settings()
    for name, value in (("nowpayments_api_key", "np-key"), ("nowpayments_ipn_secret", IPN), ("stripe_secret_key", "sk_test"), ("stripe_webhook_secret", HOOK)):
        monkeypatch.setattr(s, name, SecretStr(value), raising=False)
    SENT.clear()
    billing._mins.clear()

    def answer(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/min-amount"):
            return httpx.Response(200, json={"fiat_equivalent": MINS.get(req.url.params["currency_from"], 0.5)})
        SENT.append(req)
        if "nowpayments" in str(req.url):
            price = json.loads(req.content)["price_amount"]
            return httpx.Response(201, json={"payment_id": 555, "pay_address": "TAddr", "pay_amount": price * RATE["coins_per_usd"], "expiration_estimate_date": "2026-11-04T00:00:00Z"})
        return httpx.Response(200, json={"url": "https://checkout.stripe.test/s"})

    monkeypatch.setattr(billing, "transport", httpx.MockTransport(answer))


def ipn(client: TestClient, order: str, status: str, paid: float, currency: str = "usdttrc20", secret: str = IPN) -> httpx.Response:
    body = {"order_id": order, "payment_status": status, "actually_paid": paid, "pay_currency": currency, "payment_id": 555}
    sig = hmac.new(secret.encode(), json.dumps(body, sort_keys=True, separators=(",", ":")).encode(), hashlib.sha512).hexdigest()
    return client.post("/api/members/vpn/webhooks/nowpayments", content=json.dumps(body), headers={"x-nowpayments-sig": sig})


def stripe(client: TestClient, event: dict[str, Any], secret: str = HOOK, at: int | None = None) -> httpx.Response:
    raw = json.dumps(event)
    t = at or int(time.time())
    v1 = hmac.new(secret.encode(), f"{t}.{raw}".encode(), hashlib.sha256).hexdigest()
    return client.post("/api/members/vpn/webhooks/stripe", content=raw, headers={"stripe-signature": f"t={t},v1={v1}"})


def days_left(acct: VpnAccount) -> float:
    return (vpn._aware(acct.paid_until) - utcnow()).total_seconds() / DAY


def test_time_is_worth_its_share_of_the_price() -> None:
    assert billing.seconds_for(9.99) == 30 * DAY
    assert billing.seconds_for(5) == pytest.approx(5 / 9.99 * 30 * DAY, abs=1)
    assert billing.seconds_for(19.98) == 60 * DAY


def test_anonymous_key_buys_time_with_no_identity_anywhere(client: TestClient, db: Session, fake: Any, keys: None) -> None:  # noqa: F811
    server = add_server(db, fake)
    assert client.post("/api/members/vpn/anon").status_code == 403  # closed until the owner opens it
    open_vpn(db)
    key = client.post("/api/members/vpn/anon").json()["key"]
    assert key.startswith("KIWI-") and len(key) == 44
    row = db.scalars(select(VpnAccount)).one()
    assert row.member_id is None and row.key_hash != key and key not in row.key_hash  # only the hash is kept
    head = {"x-kiwi-key": key}
    assert client.get("/api/members/vpn/anon").status_code == 401
    assert client.get("/api/members/vpn/anon", headers={"x-kiwi-key": "KIWI-NOPE"}).status_code == 401
    view = client.get("/api/members/vpn/anon", headers=head).json()
    assert view["eligible"] is False and view["anonymous"] is True and view["paid_until"] is None and view["billing"]["usdt"] is True
    assert client.post("/api/members/vpn/anon/devices", json={"name": "phone", "server_id": server.id}, headers=head).status_code == 403  # no time yet
    # a lower-case key with odd spacing still works: it's typed by people
    assert client.get("/api/members/vpn/anon", headers={"x-kiwi-key": " " + key.lower()}).status_code == 200
    pay = client.post("/api/members/vpn/anon/usdt", json={"amount_usd": 5, "network": "solana"}, headers=head)
    assert pay.status_code == 200 and pay.json()["address"] == "TAddr" and pay.json()["days"] == pytest.approx(15, abs=0.1)
    sent = json.loads(SENT[-1].content)
    assert sent["pay_currency"] == "usdtsol" and sent["order_description"] == "Kiwi VPN time" and len(sent["order_id"]) >= 20  # a random reference, not the account
    assert not {"email", "customer", "name"} & set(sent)
    order = db.scalars(select(VpnPayment)).one().order_ref
    assert client.get("/api/members/vpn/anon", headers=head).json()["eligible"] is False  # nothing until the money arrives
    assert ipn(client, order, "waiting", 0, "usdtsol").status_code == 200
    db.expire_all()
    assert db.scalars(select(VpnAccount)).one().paid_until is None
    assert ipn(client, order, "finished", 5.0, "USDTSOL").status_code == 200  # the provider's casing varies
    db.expire_all()
    acct = db.scalars(select(VpnAccount)).one()
    assert days_left(acct) == pytest.approx(5 / 9.99 * 30, abs=0.01)
    r = client.post("/api/members/vpn/anon/devices", json={"name": "phone", "server_id": server.id}, headers=head)
    assert r.status_code == 201 and "PrivateKey" in r.json()["config"]
    assert client.get("/api/members/vpn/anon", headers=head).json()["eligible"] is True
    assert client.post("/api/members/vpn/card", headers=head).status_code == 401  # a key is not a Passport session


def test_webhook_needs_the_signature_and_never_pays_twice(client: TestClient, db: Session, fake: Any, keys: None) -> None:  # noqa: F811
    open_vpn(db)
    acct, _ = billing.new_anonymous(db)
    billing.start_usdt(db, acct, 10, "solana")
    order = db.scalars(select(VpnPayment)).one().order_ref
    assert ipn(client, order, "finished", 10, "usdtsol", secret="wrong").status_code == 401
    assert client.post("/api/members/vpn/webhooks/nowpayments", content="{}").status_code == 401
    db.expire_all()
    assert db.get(VpnAccount, acct.id).paid_until is None
    assert ipn(client, order, "finished", 10, "usdttrc20").json()["status"] == "ignored"  # wrong network for this payment
    assert ipn(client, "someone-elses", "finished", 10, "usdtsol").json()["status"] == "ignored"
    assert ipn(client, order, "finished", 10, "usdtsol").status_code == 200
    db.expire_all()
    first = db.get(VpnAccount, acct.id).paid_until
    assert ipn(client, order, "finished", 10, "usdtsol").status_code == 200  # the provider repeats itself
    db.expire_all()
    assert db.get(VpnAccount, acct.id).paid_until == first
    # underpaid, then topped up: each dollar counts once, and an overpayment still buys time
    billing.start_usdt(db, acct, 10, "solana")
    second = db.scalars(select(VpnPayment).order_by(VpnPayment.id.desc())).first().order_ref
    ipn(client, second, "partially_paid", 4, "usdtsol")
    ipn(client, second, "finished", 12, "usdtsol")
    db.expire_all()
    assert days_left(db.get(VpnAccount, acct.id)) == pytest.approx(22 / 9.99 * 30, abs=0.01)
    assert db.scalars(select(VpnPayment).where(VpnPayment.order_ref == second)).one().paid_usd == 12
    ipn(client, second, "refunded", 0, "usdtsol")
    db.expire_all()
    assert days_left(db.get(VpnAccount, acct.id)) == pytest.approx(10 / 9.99 * 30, abs=0.01)


def test_each_network_shows_and_enforces_its_live_minimum(client: TestClient, db: Session, keys: None, monkeypatch: pytest.MonkeyPatch) -> None:
    nets = {n["key"]: n["min_usd"] for n in billing.options()["networks"]}
    assert nets == {"ethereum": 5.0, "tron": 10.96, "solana": 5.0}  # our $5 floor, or the provider's minimum when higher, rounded up
    acct, _ = billing.new_anonymous(db)
    with pytest.raises(billing.BillingError, match=r"USDT on Tron needs at least \$10\.96"):
        billing.start_usdt(db, acct, 9.99, "tron")
    assert billing.start_usdt(db, acct, 9.99, "solana")["address"] == "TAddr"
    # the provider is down: the last known minimum stands, and the page still loads
    monkeypatch.setattr(billing, "transport", httpx.MockTransport(lambda r: httpx.Response(500)))
    monkeypatch.setattr(billing, "MIN_TTL_S", -1.0)
    assert billing.network_min("tron") == 10.96


def test_time_follows_the_invoice_rate_not_one_usdt_per_dollar(client: TestClient, db: Session, keys: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(RATE, "coins_per_usd", 0.99722)  # as seen on a real invoice: $12 asked for 11.966 USDT
    acct, _ = billing.new_anonymous(db)
    billing.start_usdt(db, acct, 12, "solana")
    pay = db.scalars(select(VpnPayment)).one()
    assert ipn(client, pay.order_ref, "finished", pay.pay_amount, "usdtsol").status_code == 200
    db.expire_all()
    assert db.scalars(select(VpnPayment)).one().paid_usd == 12.0  # paid what it asked: exactly the $12 of time promised
    assert days_left(db.get(VpnAccount, acct.id)) == pytest.approx(12 / 9.99 * 30, abs=0.01)


def test_amounts_networks_and_provider_failure(db: Session, fake: Any, keys: None, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    acct, _ = billing.new_anonymous(db)
    with pytest.raises(billing.BillingError, match="between"):
        billing.start_usdt(db, acct, 4.99, "solana")
    with pytest.raises(billing.BillingError, match="Ethereum, Tron or Solana"):
        billing.start_usdt(db, acct, 5, "bitcoin")
    monkeypatch.setattr(billing, "transport", httpx.MockTransport(lambda r: httpx.Response(500)))
    with pytest.raises(billing.BillingError, match="Nothing was charged"):
        billing.start_usdt(db, acct, 5, "solana")
    assert db.scalars(select(VpnPayment)).one().status == "failed"


def test_no_keys_means_that_way_of_paying_is_off(client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("nowpayments_api_key", "nowpayments_ipn_secret", "stripe_secret_key", "stripe_webhook_secret"):
        monkeypatch.setattr(get_settings(), name, None, raising=False)
    b = client.get("/api/members/vpn/public").json()["billing"]
    assert b["usdt"] is False and b["card"] is False and b["networks"] == [] and b["price_usd"] == 9.99
    acct, _ = billing.new_anonymous(db)
    with pytest.raises(billing.BillingError, match="aren't open yet"):
        billing.start_usdt(db, acct, 5, "tron")
    assert client.post("/api/members/vpn/webhooks/nowpayments", content="{}").status_code == 503  # no secret: never credits


def test_card_needs_a_passport_and_renews_without_stacking(client: TestClient, db: Session, fake: Any, keys: None) -> None:  # noqa: F811
    open_vpn(db)
    anon, _ = billing.new_anonymous(db)
    with pytest.raises(billing.BillingError, match="Passport"):
        billing.start_card(db, anon)
    m = member_at(db, "egg", "cardy", paid=False)
    c = signed_in(client, db, m)
    assert c.post("/api/members/vpn/card").json()["url"].startswith("https://checkout.stripe.test")
    form = SENT[-1].content.decode()
    assert "unit_amount%5D=999" in form and "interval%5D=month" in form
    acct = vpn.account_of(db, m)
    invoice = {"type": "invoice.paid", "data": {"object": {"id": "in_1", "amount_paid": 999, "customer": "cus_1",
                                                           "parent": {"subscription_details": {"metadata": {"vpn_account": str(acct.id)}}}}}}
    assert stripe(c, invoice, secret="wrong").status_code == 401
    assert stripe(c, invoice, at=int(time.time()) - 3600).status_code == 401  # an old replay
    assert stripe(c, invoice).status_code == 200
    assert stripe(c, invoice).status_code == 200  # repeated: one payment
    assert stripe(c, {"type": "checkout.session.completed", "data": {"object": {"client_reference_id": str(acct.id), "customer": "cus_1"}}}).status_code == 200
    db.expire_all()
    acct = db.get(VpnAccount, acct.id)
    assert len(db.scalars(select(VpnPayment)).all()) == 1 and days_left(acct) == pytest.approx(32, abs=0.01) and acct.stripe_customer == "cus_1"
    # the next month's invoice, found by customer: the date moves, unused days don't pile up
    assert stripe(c, {"type": "invoice.paid", "data": {"object": {"id": "in_2", "amount_paid": 999, "customer": "cus_1"}}}).status_code == 200
    db.expire_all()
    assert days_left(db.get(VpnAccount, acct.id)) == pytest.approx(32, abs=0.01) and len(db.scalars(select(VpnPayment)).all()) == 2
    assert c.get("/api/members/vpn").json()["eligible"] is True
    assert c.post("/api/members/vpn/card/portal").json()["url"].startswith("https://")


def test_a_passport_can_pay_usdt_too(client: TestClient, db: Session, fake: Any, keys: None) -> None:  # noqa: F811
    open_vpn(db)
    m = member_at(db, "egg", "usdt", paid=False)
    c = signed_in(client, db, m)
    assert c.post("/api/members/vpn/usdt", json={"amount_usd": 9.99, "network": "ethereum"}).status_code == 200
    assert ipn(c, db.scalars(select(VpnPayment)).one().order_ref, "finished", 9.99, "usdterc20").status_code == 200
    body = c.get("/api/members/vpn").json()
    assert body["eligible"] is True and body["anonymous"] is False and body["paid_until"]


def test_out_of_time_switches_devices_off_keeps_them_and_paying_brings_them_back(db: Session, fake: Any, keys: None) -> None:  # noqa: F811
    server = add_server(db, fake)
    open_vpn(db)
    acct, _ = billing.new_anonymous(db)
    acct.paid_until = utcnow() + dt.timedelta(days=1)
    db.commit()
    dev = vpn.create_device(db, acct, "phone", server.id)[0]
    key, dev_id = dev.public_key, dev.id
    assert key in fake.peers and vpn.sync_all(db)["suspended"] == 0
    later = utcnow() + dt.timedelta(days=2)
    out = vpn.sync_all(db, later)
    assert out["suspended"] == 1 and out["removed"] == 0 and key not in fake.peers
    db.expire_all()
    assert db.get(VpnDevice, dev_id) is not None and vpn._aware(db.get(VpnAccount, acct.id).lapsed_at) == later
    assert vpn.sync_all(db, later + dt.timedelta(minutes=5))["readded"] == 0  # stays off
    # pays again: the same config works, no new setup
    db.get(VpnAccount, acct.id).paid_until = later + dt.timedelta(days=5)
    db.commit()
    out = vpn.sync_all(db, later + dt.timedelta(hours=1))
    assert out["readded"] == 1 and key in fake.peers
    db.expire_all()
    assert db.get(VpnAccount, acct.id).lapsed_at is None and db.get(VpnDevice, dev_id).public_key == key
    # never comes back: gone after 30 days
    lapse = later + dt.timedelta(days=6)
    vpn.sync_all(db, lapse)
    assert key not in fake.peers
    assert vpn.sync_all(db, lapse + dt.timedelta(days=29))["revoked"] == 0
    assert vpn.sync_all(db, lapse + dt.timedelta(days=31))["revoked"] == 1
    db.expire_all()
    assert db.get(VpnDevice, dev_id) is None


def test_blocked_key_loses_devices_at_once(db: Session, fake: Any, keys: None) -> None:  # noqa: F811
    server = add_server(db, fake)
    open_vpn(db)
    acct, _ = billing.new_anonymous(db)
    acct.paid_until = utcnow() + dt.timedelta(days=30)
    db.commit()
    vpn.create_device(db, acct, "phone", server.id)
    acct.status = "blocked"
    db.commit()
    assert vpn.sync_all(db)["revoked"] == 1 and fake.peers == {}


def test_scrub_cuts_the_link_for_anonymous_payments_only(db: Session, fake: Any, keys: None) -> None:  # noqa: F811
    anon, _ = billing.new_anonymous(db)
    billing.start_usdt(db, anon, 5, "solana")
    m = member_at(db, "egg", "named", paid=False)
    named = vpn.account_of(db, m)
    db.commit()
    billing.start_usdt(db, named, 5, "solana")
    anon_pay, named_pay = db.scalars(select(VpnPayment).order_by(VpnPayment.id)).all()
    anon_pay.status, anon_pay.paid_usd, anon_pay.credited_at = "credited", 5.0, utcnow()
    db.commit()
    assert billing.scrub(db, utcnow() + dt.timedelta(days=6)) == 0
    assert billing.scrub(db, utcnow() + dt.timedelta(days=8)) == 1
    db.expire_all()
    assert anon_pay.account_id is None and anon_pay.pay_address is None and anon_pay.provider_ref is None and anon_pay.paid_usd == 5.0  # the books keep the amount
    assert named_pay.account_id == named.id and named_pay.pay_address == "TAddr"
