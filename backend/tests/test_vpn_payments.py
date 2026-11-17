"""Kiwi VPN payments end to end, with fake payments only: a fake NOWPayments and a fake VPN server. Nothing here reaches a real service."""

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
from app.models.members import MemberNotification
from app.models.vpn import VpnAccount, VpnBrowser, VpnDevice, VpnPayment
from app.vpn import billing, node, service as vpn
from tests.test_production import prod  # noqa: F401
from tests.test_vpn import add_server, fake, member_at, open_vpn, signed_in  # noqa: F401
from tests.test_vpn_browser import ProxyNode
from tests.test_vpn_billing import IPN, SENT, days_left, ipn, keys  # noqa: F401

USDT = "usdtsol"
HOOK = "/api/members/vpn/webhooks/nowpayments"


def later(**kw: Any) -> dt.datetime:
    return utcnow() + dt.timedelta(**kw)


def account(db: Session, acct_id: int) -> VpnAccount:
    db.expire_all()
    return db.get(VpnAccount, acct_id)


def payment(db: Session) -> VpnPayment:
    db.expire_all()
    return db.scalars(select(VpnPayment).order_by(VpnPayment.id.desc())).first()


def signed(client: TestClient, text: str, secret: str = IPN) -> httpx.Response:
    return client.post(HOOK, content=text, headers={"x-nowpayments-sig": hmac.new(secret.encode(), text.encode(), hashlib.sha512).hexdigest()})


class Provider:
    """A fake NOWPayments answering GET /payment/{id}."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.answer: dict[str, Any] = {"payment_status": "waiting", "actually_paid": 0, "pay_currency": USDT}
        self.asked = 0
        self.fail = False
        old = billing.transport

        def handler(req: httpx.Request) -> httpx.Response:
            if req.method == "GET" and "/payment/" in req.url.path:
                self.asked += 1
                return httpx.Response(500) if self.fail else httpx.Response(200, json=self.answer)
            return old.handle_request(req)  # type: ignore[union-attr]

        monkeypatch.setattr(billing, "transport", httpx.MockTransport(handler))


@pytest.fixture
def provider(keys: None, monkeypatch: pytest.MonkeyPatch) -> Provider:  # noqa: F811
    return Provider(monkeypatch)


# --- webhook verification ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("number", ["9.99", "0.00005", "0.0000001", "10.0", "10", "9.812345678901234567", "1e-7", "1E+2"])
def test_a_genuine_signature_is_accepted_whatever_the_numbers_look_like(client: TestClient, db: Session, keys: None, number: str) -> None:  # noqa: F811
    acct, _ = billing.new_anonymous(db)
    billing.start_usdt(db, acct, 9.99, "solana")
    order = payment(db).order_ref
    text = f'{{"actually_paid":9.99,"order_id":"{order}","outcome_amount":{number},"pay_currency":"{USDT}","payment_id":555,"payment_status":"finished"}}'
    assert signed(client, text).status_code == 200
    assert days_left(account(db, acct.id)) == pytest.approx(30, abs=0.01)


def test_each_way_the_provider_may_sign_the_body_is_accepted(client: TestClient, db: Session, keys: None) -> None:  # noqa: F811
    acct, _ = billing.new_anonymous(db)
    billing.start_usdt(db, acct, 9.99, "solana")
    order = payment(db).order_ref
    body = f'{{"payment_status":"waiting","order_id":"{order}","fee":{{"withdrawalFee":0.00005,"currency":"usdtsol"}},"payment_id":555,"actually_paid":0,"pay_currency":"{USDT}"}}'
    forms = {
        "the raw body": body,
        "sorted at every level": f'{{"actually_paid":0,"fee":{{"currency":"usdtsol","withdrawalFee":0.00005}},"order_id":"{order}","pay_currency":"{USDT}","payment_id":555,"payment_status":"waiting"}}',
        "nested keys dropped, as JSON.stringify with a key list does": f'{{"actually_paid":0,"fee":{{}},"order_id":"{order}","pay_currency":"{USDT}","payment_id":555,"payment_status":"waiting"}}',
    }
    for name, form in forms.items():
        sig = hmac.new(IPN.encode(), form.encode(), hashlib.sha512).hexdigest()
        assert client.post(HOOK, content=body, headers={"x-nowpayments-sig": sig}).status_code == 200, name
        assert client.post(HOOK, content=body, headers={"x-nowpayments-sig": sig.upper()}).status_code == 200, name


def test_a_forged_or_altered_message_is_refused_and_credits_nothing(client: TestClient, db: Session, keys: None) -> None:  # noqa: F811
    acct, _ = billing.new_anonymous(db)
    billing.start_usdt(db, acct, 9.99, "solana")
    order = payment(db).order_ref
    text = f'{{"actually_paid":9.99,"order_id":"{order}","pay_currency":"{USDT}","payment_id":555,"payment_status":"finished"}}'
    assert signed(client, text, secret="not-the-secret").status_code == 401
    sig = hmac.new(IPN.encode(), text.encode(), hashlib.sha512).hexdigest()
    assert client.post(HOOK, content=text.replace("9.99", "999"), headers={"x-nowpayments-sig": sig}).status_code == 401  # the amount changed
    assert client.post(HOOK, content=text).status_code == 401
    assert client.post(HOOK, content=text, headers={"x-nowpayments-sig": ""}).status_code == 401
    assert client.post(HOOK, content="not json", headers={"x-nowpayments-sig": "00"}).status_code == 400
    assert signed(client, "[1,2]").status_code == 400
    assert account(db, acct.id).paid_until is None and payment(db).status == "pending"


# --- paid access activates, expired access is revoked ------------------------------------------------------------------


def test_the_whole_life_of_a_customer(client: TestClient, db: Session, fake: Any, keys: None) -> None:  # noqa: F811
    server = add_server(db, fake)
    open_vpn(db, trial_enabled=True)
    m = member_at(db, "egg", "life", paid=False)
    c = signed_in(client, db, m)
    new_device = {"name": "phone", "server_id": server.id}
    assert c.post("/api/members/vpn/devices", json=new_device).status_code == 403  # nothing yet
    assert c.post("/api/members/vpn/trial").status_code == 200
    made = c.post("/api/members/vpn/devices", json=new_device)
    assert made.status_code == 201
    dev_id = made.json()["device"]["id"]
    key = next(iter(fake.peers))
    acct_id = vpn.account_of(db, m).id
    # the trial ends: off at the next sync, every way in refused, and the device kept
    trial_over = later(days=31)
    assert vpn.sync_all(db, trial_over)["suspended"] == 1 and key not in fake.peers
    assert db.get(VpnDevice, dev_id) is not None
    acct = account(db, acct_id)
    acct.trial_ends_at = utcnow() - dt.timedelta(minutes=1)
    db.commit()
    assert vpn.qualifies(db, m)[0] is False
    assert c.get(f"/api/members/vpn/devices/{dev_id}/peer").status_code == 403
    # pays: the device is back straight away, with no sync and no new setup
    assert c.post("/api/members/vpn/usdt", json={"amount_usd": 9.99, "network": "solana"}).status_code == 200
    assert c.get("/api/members/vpn").json()["eligible"] is False  # not until the money arrives
    assert key not in fake.peers
    assert ipn(c, payment(db).order_ref, "finished", 9.99, USDT).status_code == 200
    assert key in fake.peers and account(db, acct_id).lapsed_at is None
    view = c.get("/api/members/vpn").json()
    assert view["eligible"] is True and view["plan"]["kind"] == "paid" and view["plan"]["days_left"] in (29, 30)
    assert c.get(f"/api/members/vpn/devices/{dev_id}/peer").status_code == 200
    # 30 days on, out of time: revoked again
    assert vpn.sync_all(db, later(days=29))["suspended"] == 0 and key in fake.peers
    assert vpn.sync_all(db, later(days=31))["suspended"] == 1 and key not in fake.peers


def test_a_browser_login_follows_the_same_rules(client: TestClient, db: Session, keys: None, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    proxy = ProxyNode()
    monkeypatch.setattr(node, "connect", lambda url, token, fp: node.NodeClient(url, token, fp, transport=httpx.MockTransport(proxy.handler)))
    server = add_server(db, proxy)
    open_vpn(db)
    acct, _ = billing.new_anonymous(db)
    with pytest.raises(vpn.VpnError):
        vpn.create_browser(db, acct, server.id)
    billing.start_usdt(db, acct, 9.99, "solana")
    billing._credit(db, acct, payment(db), 9.99, utcnow())
    db.commit()
    name = vpn.create_browser(db, acct, server.id)["username"]
    assert name in proxy.users
    expired = later(days=31)
    vpn.sync_all(db, expired)
    assert name not in proxy.users and db.scalars(select(VpnBrowser)).one().username == name  # off, and kept
    # pays again: the login is back at once, with no sync
    acct = account(db, acct.id)
    assert acct.lapsed_at is not None
    acct.paid_until = utcnow() - dt.timedelta(minutes=1)
    db.commit()
    billing.start_usdt(db, acct, 9.99, "solana")
    assert ipn(client, payment(db).order_ref, "finished", 9.99, USDT).status_code == 200
    assert name in proxy.users
    # out of time for too long: gone for good
    gone = later(days=31)
    vpn.sync_all(db, gone)
    vpn.sync_all(db, gone + vpn.KEEP_LAPSED + dt.timedelta(days=1))
    assert name not in proxy.users and db.scalars(select(VpnBrowser)).all() == []


def test_closing_the_vpn_switches_customers_off_and_opening_brings_them_back(db: Session, fake: Any, keys: None) -> None:  # noqa: F811
    server = add_server(db, fake)
    open_vpn(db)
    payer, _ = billing.new_anonymous(db)
    payer.paid_until = later(days=5)
    db.commit()
    key = vpn.create_device(db, payer, "phone", server.id)[0].public_key
    owner = vpn.owner_account(db)
    db.commit()
    own = vpn.create_device(db, owner, "mine", server.id, preview=True)[0].public_key
    vpn.update_settings(db, {"enabled": False})
    vpn.sync_all(db)
    assert key not in fake.peers and own in fake.peers  # the owner's own device stays
    assert db.scalars(select(VpnDevice).where(VpnDevice.public_key == key)).one() is not None
    assert account(db, payer.id).lapsed_at is None  # closed isn't out of time
    vpn.update_settings(db, {"enabled": True})
    vpn.sync_all(db)
    assert key in fake.peers


def test_a_failing_server_doesnt_lose_the_payment(client: TestClient, db: Session, fake: Any, keys: None, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    server = add_server(db, fake)
    open_vpn(db)
    acct, _ = billing.new_anonymous(db)
    acct.paid_until = later(days=1)
    db.commit()
    key = vpn.create_device(db, acct, "phone", server.id)[0].public_key
    vpn.sync_all(db, later(days=2))
    assert key not in fake.peers
    acct = account(db, acct.id)
    acct.paid_until = utcnow() - dt.timedelta(minutes=1)
    db.commit()
    billing.start_usdt(db, acct, 9.99, "solana")
    real = vpn.node.client_for

    def down(_server: Any) -> Any:
        raise vpn.node.NodeError("offline")

    monkeypatch.setattr(vpn.node, "client_for", down)
    assert ipn(client, payment(db).order_ref, "finished", 9.99, USDT).status_code == 200
    assert days_left(account(db, acct.id)) == pytest.approx(30, abs=0.01)  # credited all the same
    monkeypatch.setattr(vpn.node, "client_for", real)
    vpn.sync_all(db)
    assert key in fake.peers  # and the next sync finishes it


# --- trial days ----------------------------------------------------------------------------------------------------------


def test_paying_during_a_trial_keeps_every_trial_day_and_a_refund_goes_back_to_the_trial(client: TestClient, db: Session, fake: Any, keys: None) -> None:  # noqa: F811
    open_vpn(db, trial_enabled=True)
    m = member_at(db, "egg", "early", paid=False)
    c = signed_in(client, db, m)
    c.post("/api/members/vpn/trial")
    c.post("/api/members/vpn/usdt", json={"amount_usd": 9.99, "network": "solana"})
    order = payment(db).order_ref
    ipn(c, order, "finished", 9.99, USDT)
    acct_id = vpn.account_of(db, m).id
    assert days_left(account(db, acct_id)) == pytest.approx(60, abs=0.01)
    ipn(c, order, "refunded", 0, USDT)
    acct = account(db, acct_id)
    assert days_left(acct) == pytest.approx(30, abs=0.01)
    assert vpn.access(db, acct, now=later(days=29)) == "active" and vpn.access(db, acct, now=later(days=31)) == "lapsed"


def test_a_trial_already_over_adds_nothing(db: Session, fake: Any, keys: None) -> None:  # noqa: F811
    m = member_at(db, "egg", "over", paid=False)
    acct = vpn.account_of(db, m)
    acct.trial_ends_at = utcnow() - dt.timedelta(days=3)
    db.commit()
    billing.start_usdt(db, acct, 9.99, "solana")
    billing._credit(db, acct, payment(db), 9.99, utcnow())
    assert days_left(acct) == pytest.approx(30, abs=0.01)


# --- duplicates and refunds ----------------------------------------------------------------------------------------------


def test_a_refund_is_final_even_if_an_old_message_is_sent_again(client: TestClient, db: Session, keys: None) -> None:  # noqa: F811
    acct, _ = billing.new_anonymous(db)
    billing.start_usdt(db, acct, 9.99, "solana")
    order = payment(db).order_ref
    ipn(client, order, "finished", 9.99, USDT)
    ipn(client, order, "refunded", 0, USDT)
    assert days_left(account(db, acct.id)) == pytest.approx(0, abs=0.01)
    for status in ("finished", "partially_paid", "refunded", "finished"):
        assert ipn(client, order, status, 9.99, USDT).status_code == 200
        assert days_left(account(db, acct.id)) == pytest.approx(0, abs=0.01), status  # checked after each, so a later message can't hide it
        assert payment(db).status == "refunded" and payment(db).paid_usd == 0, status


def test_webhook_and_recheck_together_only_pay_once(client: TestClient, db: Session, provider: Provider) -> None:
    acct, _ = billing.new_anonymous(db)
    billing.start_usdt(db, acct, 9.99, "solana")
    pay = payment(db)
    ipn(client, pay.order_ref, "finished", 9.99, USDT)
    provider.answer = {"payment_status": "finished", "actually_paid": 9.99, "pay_currency": USDT, "order_id": pay.order_ref}
    assert billing.check_payment(db, payment(db))[1] is None
    assert billing.check_payment(db, payment(db))[1] is None
    assert days_left(account(db, acct.id)) == pytest.approx(30, abs=0.01)


def test_money_sent_again_to_the_same_address_counts_once_each(client: TestClient, db: Session, keys: None) -> None:  # noqa: F811
    acct, _ = billing.new_anonymous(db)
    billing.start_usdt(db, acct, 9.99, "solana")
    order = payment(db).order_ref
    ipn(client, order, "finished", 9.99, USDT)
    body = {"order_id": order, "payment_id": 777, "parent_payment_id": 555, "payment_status": "finished", "actually_paid": 9.99, "pay_currency": USDT}
    for _ in range(3):
        assert signed(client, json.dumps(body, sort_keys=True, separators=(",", ":"))).status_code == 200
    assert days_left(account(db, acct.id)) == pytest.approx(60, abs=0.01)
    rows = db.scalars(select(VpnPayment).order_by(VpnPayment.id)).all()
    assert len(rows) == 2 and rows[1].order_ref == "np:777" and rows[1].paid_usd == 9.99 and rows[0].paid_usd == 9.99


def test_failed_and_expired_never_undo_a_credit(client: TestClient, db: Session, keys: None) -> None:  # noqa: F811
    acct, _ = billing.new_anonymous(db)
    billing.start_usdt(db, acct, 9.99, "solana")
    order = payment(db).order_ref
    ipn(client, order, "expired", 0, USDT)
    assert payment(db).status == "expired"
    ipn(client, order, "finished", 9.99, USDT)  # it arrived after all
    assert payment(db).status == "credited"
    ipn(client, order, "failed", 0, USDT)
    ipn(client, order, "cancelled", 0, USDT)
    assert payment(db).status == "credited" and days_left(account(db, acct.id)) == pytest.approx(30, abs=0.01)


def test_the_wrong_coin_goes_to_a_person_and_credits_nothing(client: TestClient, db: Session, keys: None) -> None:  # noqa: F811
    acct, _ = billing.new_anonymous(db)
    billing.start_usdt(db, acct, 9.99, "solana")
    ipn(client, payment(db).order_ref, "finished", 9.99, "usdterc20")
    assert payment(db).status == "review" and payment(db).note and account(db, acct.id).paid_until is None


# --- blocked accounts ----------------------------------------------------------------------------------------------------


def test_a_blocked_account_or_banned_member_cant_start_a_payment(client: TestClient, db: Session, fake: Any, keys: None) -> None:  # noqa: F811
    open_vpn(db)
    anon, key = billing.new_anonymous(db)
    vpn.set_blocked(db, anon.id, True)
    pay = {"amount_usd": 9.99, "network": "solana"}
    assert client.post("/api/members/vpn/anon/usdt", json=pay, headers={"x-kiwi-key": key}).status_code == 403
    m = member_at(db, "egg", "blocked", paid=False)
    c = signed_in(client, db, m)
    acct = vpn.account_of(db, m)
    db.commit()
    vpn.set_blocked(db, acct.id, True)
    assert c.post("/api/members/vpn/usdt", json=pay).status_code == 403
    with pytest.raises(billing.BillingError):
        billing.start_usdt(db, acct, 9.99, "solana")
    assert db.scalars(select(VpnPayment)).all() == [] and not SENT  # the provider was never asked


def test_a_member_banned_after_signing_in_cant_start_a_payment(client: TestClient, db: Session, fake: Any, keys: None) -> None:  # noqa: F811
    """Their VPN account itself is still marked active: only the member's status says no."""
    open_vpn(db)
    m = member_at(db, "egg", "banned", paid=False)
    c = signed_in(client, db, m)
    acct = vpn.account_of(db, m)
    assert acct.status == "active"
    m.status = "banned"
    db.commit()
    assert c.post("/api/members/vpn/usdt", json={"amount_usd": 9.99, "network": "solana"}).status_code in (401, 403)
    assert db.scalars(select(VpnPayment)).all() == [] and not SENT


def test_a_blocked_account_is_refused_before_the_provider_is_asked_through_the_route(client: TestClient, db: Session, fake: Any, keys: None, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    """access() also says blocked for a member whose status isn't active, whichever check runs first."""
    open_vpn(db)
    m = member_at(db, "egg", "suspended", paid=False)
    acct = vpn.account_of(db, m)
    db.commit()
    monkeypatch.setattr(vpn, "access", lambda *_a, **_k: "blocked")
    with pytest.raises(Exception) as refused:
        from app.api.routes.vpn import UsdtInput, _usdt

        _usdt(db, acct, UsdtInput(amount_usd=9.99, network="solana"))
    assert getattr(refused.value, "status_code", None) == 403
    assert not SENT


def test_blocked_stays_off_even_with_paid_time(client: TestClient, db: Session, fake: Any, keys: None) -> None:  # noqa: F811
    server = add_server(db, fake)
    open_vpn(db)
    acct, _ = billing.new_anonymous(db)
    billing.start_usdt(db, acct, 9.99, "solana")  # started before the block
    order = payment(db).order_ref
    vpn.set_blocked(db, acct.id, True)
    assert ipn(client, order, "finished", 9.99, USDT).status_code == 200  # the record is kept, for a refund decision
    acct = account(db, acct.id)
    assert acct.paid_until is not None and vpn.access(db, acct) == "blocked"
    assert fake.peers == {} and server.id


# --- missed payments and recovery ------------------------------------------------------------------------------------------


def test_a_lost_webhook_is_found_and_the_device_comes_back(db: Session, fake: Any, provider: Provider) -> None:
    server = add_server(db, fake)
    open_vpn(db)
    acct, _ = billing.new_anonymous(db)
    acct.paid_until = later(days=1)
    db.commit()
    key = vpn.create_device(db, acct, "phone", server.id)[0].public_key
    vpn.sync_all(db, later(days=2))
    acct = account(db, acct.id)
    acct.paid_until = utcnow() - dt.timedelta(minutes=1)
    db.commit()
    billing.start_usdt(db, acct, 9.99, "solana")
    pay = payment(db)
    now = utcnow()
    out = vpn.reconcile_payments(db, now)  # still waiting
    assert out["checked"] == 1 and out["credited"] == 0 and payment(db).status == "pending" and provider.asked == 1
    assert vpn.reconcile_payments(db, now + dt.timedelta(minutes=1))["checked"] == 0  # not asked again so soon
    provider.answer = {"payment_status": "finished", "actually_paid": 9.99, "pay_currency": USDT, "order_id": pay.order_ref}
    out = vpn.reconcile_payments(db, now + dt.timedelta(minutes=11))
    assert out["credited"] == 1 and out["restored"] == 1 and key in fake.peers
    assert payment(db).status == "credited" and days_left(account(db, acct.id)) == pytest.approx(30, abs=0.01)
    assert vpn.reconcile_payments(db, now + dt.timedelta(minutes=22))["checked"] == 0  # settled: nothing left to ask


def test_reconcile_survives_the_provider_being_down_and_refuses_another_orders_answer(db: Session, provider: Provider) -> None:
    acct, _ = billing.new_anonymous(db)
    billing.start_usdt(db, acct, 9.99, "solana")
    now = utcnow()
    provider.fail = True
    assert vpn.reconcile_payments(db, now)["errors"] == 1
    assert payment(db).status == "pending"
    provider.fail = False
    provider.answer = {"payment_status": "finished", "actually_paid": 9.99, "pay_currency": USDT, "order_id": "someone-elses"}
    assert vpn.reconcile_payments(db, now + dt.timedelta(minutes=11))["credited"] == 0
    assert account(db, acct.id).paid_until is None


def test_reconcile_gives_up_after_14_days_and_a_late_webhook_still_credits(client: TestClient, db: Session, provider: Provider) -> None:
    acct, _ = billing.new_anonymous(db)
    billing.start_usdt(db, acct, 9.99, "solana")
    order = payment(db).order_ref
    assert vpn.reconcile_payments(db, later(days=15))["gave_up"] == 1
    assert payment(db).status == "expired"
    assert vpn.reconcile_payments(db, later(days=16))["checked"] == 0
    assert ipn(client, order, "finished", 9.99, USDT).status_code == 200
    assert payment(db).status == "credited"


def test_a_part_payment_short_of_its_invoice_keeps_being_checked_for_a_while(db: Session, provider: Provider) -> None:
    acct, _ = billing.new_anonymous(db)
    billing.start_usdt(db, acct, 9.99, "solana")
    pay = payment(db)
    billing._credit(db, acct, pay, 4.0, utcnow())
    db.commit()
    now = utcnow()
    provider.answer = {"payment_status": "finished", "actually_paid": 9.99, "pay_currency": USDT, "order_id": pay.order_ref}
    assert vpn.reconcile_payments(db, now)["credited"] == 1
    assert days_left(account(db, acct.id)) == pytest.approx(30, abs=0.01)


def test_with_no_keys_reconcile_does_nothing(db: Session, keys: None, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    acct, _ = billing.new_anonymous(db)
    billing.start_usdt(db, acct, 9.99, "solana")
    monkeypatch.setattr(billing, "_usdt_on", lambda: False)
    assert vpn.reconcile_payments(db)["checked"] == 0


def test_a_slow_anonymous_payment_keeps_its_link_past_the_seven_day_scrub(client: TestClient, db: Session, keys: None) -> None:  # noqa: F811
    acct, _ = billing.new_anonymous(db)
    billing.start_usdt(db, acct, 9.99, "solana")
    order = payment(db).order_ref
    assert billing.scrub(db, later(days=8)) == 0
    assert ipn(client, order, "finished", 9.99, USDT).status_code == 200
    assert days_left(account(db, acct.id)) == pytest.approx(30, abs=0.01)
    assert billing.scrub(db, later(days=8)) == 1  # settled: now it goes
    assert payment(db).account_id is None and payment(db).paid_usd == 9.99


def test_money_after_the_scrub_goes_to_a_person_who_can_credit_it_with_the_key(client: TestClient, db: Session, keys: None, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    acct, key = billing.new_anonymous(db)
    billing.start_usdt(db, acct, 9.99, "solana")
    pay = payment(db)
    order, pay_id = pay.order_ref, pay.id
    pay.status = "expired"
    db.commit()
    assert billing.scrub(db, later(days=8)) == 1
    assert ipn(client, order, "finished", 9.99, USDT).json()["status"] == "unmatched"
    assert payment(db).status == "unmatched" and payment(db).note and account(db, acct.id).paid_until is None
    with pytest.raises(vpn.VpnError, match="no account"):
        vpn.admin_credit_payment(db, pay_id, 9.99, "customer got in touch")
    with pytest.raises(vpn.VpnError, match="No account matches"):
        vpn.admin_credit_payment(db, pay_id, 9.99, "customer got in touch", key="KIWI-WRONG-KEY")
    out = vpn.admin_credit_payment(db, pay_id, 9.99, "customer got in touch", key=key)
    assert out["status"] == "credited" and days_left(account(db, acct.id)) == pytest.approx(30, abs=0.01)
    assert "customer got in touch" in payment(db).note and key not in payment(db).note


def test_crediting_by_hand_twice_adds_nothing_and_cant_cross_accounts(db: Session, keys: None) -> None:  # noqa: F811
    acct, _ = billing.new_anonymous(db)
    other, _ = billing.new_anonymous(db)
    billing.start_usdt(db, acct, 9.99, "solana")
    pay_id = payment(db).id
    vpn.admin_credit_payment(db, pay_id, 9.99, "webhook never came")
    with pytest.raises(vpn.VpnError, match="already been credited"):
        vpn.admin_credit_payment(db, pay_id, 9.99, "webhook never came")
    with pytest.raises(vpn.VpnError, match="another account"):
        vpn.admin_credit_payment(db, pay_id, 19.98, "wrong person", account_id=other.id)
    assert days_left(account(db, acct.id)) == pytest.approx(30, abs=0.01) and account(db, other.id).paid_until is None
    vpn.admin_refund_payment(db, pay_id, "asked for it back")
    assert days_left(account(db, acct.id)) == pytest.approx(0, abs=0.01) and payment(db).status == "refunded"
    with pytest.raises(vpn.VpnError, match="was refunded"):
        vpn.admin_credit_payment(db, pay_id, 9.99, "no")
    with pytest.raises(vpn.VpnError, match="already"):
        vpn.admin_refund_payment(db, pay_id, "again")


def test_recovery_routes_are_for_admin_and_for_the_payments_owner_only(client: TestClient, prod: TestClient, db: Session, fake: Any, provider: Provider) -> None:  # noqa: F811
    open_vpn(db)
    buyer = member_at(db, "egg", "buyer", paid=False)
    c = signed_in(client, db, buyer)
    c.post("/api/members/vpn/usdt", json={"amount_usd": 9.99, "network": "solana"})
    pay = payment(db)
    pay_id, order = pay.id, pay.order_ref
    other_key = client.post("/api/members/vpn/anon").json()["key"]
    assert client.post(f"/api/members/vpn/anon/payments/{pay_id}/check", headers={"x-kiwi-key": other_key}).status_code == 404
    assert client.get("/api/members/vpn/anon/payments", headers={"x-kiwi-key": other_key}).json()["items"] == []
    assert client.get("/api/members/vpn/anon/payments").status_code == 401
    # the buyer's own: Check now finds the money
    provider.answer = {"payment_status": "finished", "actually_paid": 9.99, "pay_currency": USDT, "order_id": order}
    assert c.post(f"/api/members/vpn/payments/{pay_id}/check").json()["status"] == "credited"
    client.cookies.clear()
    assert client.get("/api/members/vpn/payments").status_code == 401
    assert client.post(f"/api/members/vpn/payments/{pay_id}/check").status_code == 401
    for path, body in (("check", {}), ("credit", {"usd": 9.99, "reason": "xxx"}), ("refund", {"reason": "xxx"})):
        assert prod.post(f"/api/vpn/admin/payments/{pay_id}/{path}", json=body).status_code == 401, path  # production needs the owner's login
    # and with it, each works through its route
    assert client.post(f"/api/vpn/admin/payments/{pay_id}/check", json={}).json()["status"] == "credited"
    assert client.post(f"/api/vpn/admin/payments/{pay_id}/credit", json={"usd": 12, "reason": "the rest arrived"}).json()["status"] == "credited"
    assert client.post(f"/api/vpn/admin/payments/{pay_id}/credit", json={"usd": 12, "reason": "the rest arrived"}).status_code == 409
    row = client.get("/api/vpn/admin/payments").json()["items"][0]
    assert row["note"] == "Credited by hand: the rest arrived" and row["can_check"] is False
    assert client.post(f"/api/vpn/admin/payments/{pay_id}/refund", json={"reason": "asked"}).json()["status"] == "refunded"
    assert client.post("/api/vpn/admin/payments/999999/check", json={}).status_code == 404
    assert client.post(f"/api/vpn/admin/payments/{pay_id}/credit", json={"usd": 12, "reason": "x"}).status_code == 422  # a reason is required


# --- payment history ----------------------------------------------------------------------------------------------------


def test_history_shows_your_own_payments_with_the_address_only_while_waiting(client: TestClient, db: Session, fake: Any, keys: None, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    open_vpn(db)
    key = client.post("/api/members/vpn/anon").json()["key"]
    head = {"x-kiwi-key": key}
    assert client.get("/api/members/vpn/anon/payments", headers=head).json()["items"] == []
    client.post("/api/members/vpn/anon/usdt", json={"amount_usd": 9.99, "network": "solana"}, headers=head)
    row = client.get("/api/members/vpn/anon/payments", headers=head).json()["items"][0]
    assert row["status"] == "pending" and row["address"] == "TAddr" and row["amount"] == 9.99 and row["can_check"] is True and row["receipt"].startswith("KV-")
    assert row["network_name"] == "USDT on Solana"
    ipn(client, payment(db).order_ref, "finished", 9.99, USDT)
    row = client.get("/api/members/vpn/anon/payments", headers=head).json()["items"][0]
    assert row["status"] == "credited" and row["address"] is None and row["days"] == 30.0 and row["paid_usd"] == 9.99 and row["can_check"] is False
    assert not {"order_ref", "provider_ref", "account_id", "note"} & set(row)
    # an attempt the provider never accepted charged nothing, so it isn't listed
    monkeypatch.setattr(billing, "transport", httpx.MockTransport(lambda r: httpx.Response(500) if r.method == "POST" else httpx.Response(200, json={"fiat_equivalent": 0.5})))
    assert client.post("/api/members/vpn/anon/usdt", json={"amount_usd": 9.99, "network": "solana"}, headers=head).status_code == 502
    assert len(client.get("/api/members/vpn/anon/payments", headers=head).json()["items"]) == 1


# --- reminders ------------------------------------------------------------------------------------------------------------


def reminders(db: Session, member_id: int) -> list[str]:
    db.expire_all()
    return [n.title for n in db.scalars(select(MemberNotification).where(MemberNotification.member_id == member_id, MemberNotification.kind == "vpn_expiry").order_by(MemberNotification.id))]


def test_reminders_go_once_each_at_3_days_1_day_and_the_end(db: Session, fake: Any, keys: None) -> None:  # noqa: F811
    open_vpn(db)
    m = member_at(db, "egg", "soon", paid=False)
    acct = vpn.account_of(db, m)
    now = utcnow()
    acct.paid_until = now + dt.timedelta(days=10)
    m.email = "soon@example.com"
    db.commit()
    assert vpn.due_reminders(db, now) == [] and reminders(db, m.id) == []
    mails = vpn.due_reminders(db, now + dt.timedelta(days=7, hours=1))
    assert [x["to"] for x in mails] == ["soon@example.com"] and "ends in 3 days" in mails[0]["subject"] and "nothing renews by itself" in mails[0]["text"]
    assert vpn.due_reminders(db, now + dt.timedelta(days=7, hours=2)) == []  # once
    assert "ends in 1 day" in vpn.due_reminders(db, now + dt.timedelta(days=9, hours=1))[0]["subject"]
    assert vpn.due_reminders(db, now + dt.timedelta(days=9, hours=2)) == []
    assert "has ended" in vpn.due_reminders(db, now + dt.timedelta(days=10, hours=1))[0]["subject"]
    assert vpn.due_reminders(db, now + dt.timedelta(days=10, hours=2)) == []
    assert len(reminders(db, m.id)) == 3
    assert vpn.due_reminders(db, now + dt.timedelta(days=30)) == []


def test_adding_time_starts_the_reminders_again_and_silences_the_old_date(db: Session, fake: Any, keys: None) -> None:  # noqa: F811
    open_vpn(db)
    m = member_at(db, "egg", "renews", paid=False)
    acct = vpn.account_of(db, m)
    now = utcnow()
    acct.paid_until = now + dt.timedelta(days=2)
    db.commit()
    vpn.due_reminders(db, now)
    assert len(reminders(db, m.id)) == 1
    account(db, acct.id).paid_until = now + dt.timedelta(days=32)
    db.commit()
    assert vpn.due_reminders(db, now + dt.timedelta(days=3)) == [] and len(reminders(db, m.id)) == 1  # renewed: no "ended" message
    vpn.due_reminders(db, now + dt.timedelta(days=30))
    assert len(reminders(db, m.id)) == 2


def test_a_trial_gets_reminders_and_says_so(db: Session, fake: Any, keys: None) -> None:  # noqa: F811
    open_vpn(db, trial_enabled=True, payments_enabled=False)
    m = member_at(db, "egg", "trying", paid=False)
    vpn.start_trial(db, m)
    vpn.due_reminders(db, later(days=27, hours=1))
    assert reminders(db, m.id) == ["Your Kiwi VPN free trial ends in 3 days"]
    body = db.scalars(select(MemberNotification).where(MemberNotification.kind == "vpn_expiry")).one().body
    assert "aren't open yet" in body and "$9.99" not in body  # payments are off: it doesn't offer what can't be bought


def test_nobody_with_free_access_is_told_their_time_is_ending(db: Session, fake: Any, keys: None) -> None:  # noqa: F811
    open_vpn(db)
    royal = member_at(db, "royal", "royal", paid=False)
    blocked = member_at(db, "egg", "stopped", paid=False)
    anon, _ = billing.new_anonymous(db)
    now = utcnow()
    for acct in (vpn.account_of(db, royal), vpn.account_of(db, blocked), anon):
        acct.paid_until = now + dt.timedelta(days=2)
    vpn.account_of(db, blocked).status = "blocked"
    db.commit()
    assert vpn.is_royal(royal)
    vpn.due_reminders(db, now)
    assert reminders(db, royal.id) == [] and reminders(db, blocked.id) == []
    assert db.scalars(select(MemberNotification).where(MemberNotification.kind == "vpn_expiry")).all() == []


def test_closed_means_no_reminders(db: Session, fake: Any, keys: None) -> None:  # noqa: F811
    m = member_at(db, "egg", "closed", paid=False)
    acct = vpn.account_of(db, m)
    acct.paid_until = later(days=2)
    db.commit()
    assert vpn.settings(db).enabled is False
    assert vpn.due_reminders(db) == [] and reminders(db, m.id) == []


# --- no recurring billing -----------------------------------------------------------------------------------------------


def test_no_card_route_works_even_with_stripe_keys(client: TestClient, db: Session, fake: Any, keys: None) -> None:  # noqa: F811
    assert billing.CARD_SUBSCRIPTIONS is False
    open_vpn(db)
    m = member_at(db, "egg", "cardless", paid=False)
    c = signed_in(client, db, m)
    assert c.post("/api/members/vpn/card").status_code == 403
    assert c.post("/api/members/vpn/card/portal").status_code == 404
    assert client.post("/api/members/vpn/webhooks/stripe", content="{}", headers={"stripe-signature": "t=1,v1=00"}).status_code == 503
    assert not SENT
    b = c.get("/api/members/vpn").json()["billing"]
    assert b["card"] is False and b["usdt"] is True
    assert billing.totals(db)["card"] is False


def test_the_owner_still_cant_turn_payments_on_with_no_way_to_take_one(client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(billing, "can_charge", lambda: False)
    r = client.put("/api/vpn/admin/settings", json={"payments_enabled": True})
    assert r.status_code == 400 and "NOWPayments" in r.json()["detail"]
    db.expire_all()
    assert vpn.settings(db).payments_enabled is False
