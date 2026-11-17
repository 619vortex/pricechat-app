"""Kiwi VPN: one customer's whole journey through the HTTP API, in payments' test mode (VPN_PAYMENTS_PUBLIC unset, production's default).

Passport sign-up by email code -> free trial (guards in monitor mode) -> a device whose config sends IPv4 AND IPv6 into the tunnel ->
the server sync -> paying (owner/tester only while in test mode, the start-now box required) -> signed NOWPayments webhooks that credit
exactly once (a replay, a part payment then the rest, the webhook and vpn.reconcile at the same moment, a late "expired") -> pro-rata
time stacked after the trial and after paid time -> reminders at 3, 1 and 0 days -> expiry -> the grace period -> removal of only this
customer's devices -> renewal. Also refunds, the anonymous key and the browser login.

TEST FIXTURES only: a fake NOWPayments (httpx.MockTransport, it never reaches the network), a fake node agent (with the browser proxy),
a fake email sender, a test IPN secret, and a test clock for the VPN code (the HTTP calls run at the clock's time). No real payment,
provider, server or secret is involved."""

import datetime as dt
import hashlib
import hmac
import json
from typing import Any, Callable

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.settings import get_settings
from app.db.base import utcnow
from app.members import service as members
from app.models.members import Member, MemberAuditLog, MemberNotification
from app.models.vpn import VpnAccount, VpnBrowser, VpnDevice, VpnPayment, VpnServer, VpnTrialSignal
from app.security import admin_auth, rate_limit
from app.vpn import billing, health, keys, node, reviewer, service as vpn, trial_guard
from tests.test_email_login import outbox, sign_in  # noqa: F401  (fixture)
from tests.test_vpn import TOKEN, member_at
from tests.test_vpn_browser import ProxyNode

IPN_SECRET = "journey-ipn-test-secret"
HOOK = "/api/members/vpn/webhooks/nowpayments"
DAY = 86400
OWNER = "owner-admin-session"
PAY = {"amount_usd": 9.99, "network": "solana", "start_now": True}


# --- test fixtures ---------------------------------------------------------------------------------------------------


class Clock:
    """TEST FIXTURE: the time the VPN code sees (service, billing, health, trial guards, reviewer). Sessions keep real time."""

    def __init__(self) -> None:
        self.now = utcnow()

    def advance(self, **kw: float) -> dt.datetime:
        self.now += dt.timedelta(**kw)
        return self.now

    def to(self, at: dt.datetime) -> dt.datetime:
        self.now = at
        return at


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    c = Clock()
    for mod in (vpn, billing, health, trial_guard, reviewer):
        monkeypatch.setattr(mod, "utcnow", lambda: c.now)
    return c


class NowPayments:
    """TEST FIXTURE: a fake NOWPayments API (create a payment, read one, the network minimum). Every request is recorded; any
    other host fails the test, so nothing can reach a real service."""

    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self.reads: list[str] = []
        self.answers: dict[str, dict[str, Any]] = {}
        self.on_read: dict[str, Callable[[], None]] = {}  # provider id -> runs while the provider is being asked about it: "at the same moment"
        self.next_id = 7000
        self.coins_per_usd = 1.0

    def handler(self, req: httpx.Request) -> httpx.Response:
        assert req.url.host == "api.nowpayments.io", req.url
        assert req.headers.get("x-api-key") == "np-test-key"
        path = req.url.path
        if path.endswith("/min-amount"):
            return httpx.Response(200, json={"fiat_equivalent": 0.5})
        if req.method == "POST" and path.endswith("/payment"):
            body = json.loads(req.content)
            self.created.append(body)
            self.next_id += 1
            return httpx.Response(201, json={"payment_id": self.next_id, "pay_address": f"So1TestAddress{self.next_id}",
                                             "pay_amount": round(body["price_amount"] * self.coins_per_usd, 6), "expiration_estimate_date": "2026-12-01T00:00:00Z"})
        if req.method == "GET" and "/payment/" in path:
            pid = path.rsplit("/", 1)[1]
            self.reads.append(pid)
            if pid in self.on_read:
                self.on_read.pop(pid)()
            return httpx.Response(200, json=self.answers.get(pid, {"payment_status": "waiting", "actually_paid": 0, "pay_currency": "usdtsol"}))
        return httpx.Response(404)


@pytest.fixture
def np(monkeypatch: pytest.MonkeyPatch) -> NowPayments:
    s = get_settings()
    monkeypatch.setattr(s, "nowpayments_api_key", SecretStr("np-test-key"), raising=False)
    monkeypatch.setattr(s, "nowpayments_ipn_secret", SecretStr(IPN_SECRET), raising=False)
    monkeypatch.setattr(s, "stripe_secret_key", None, raising=False)
    monkeypatch.setattr(s, "stripe_webhook_secret", None, raising=False)
    f = NowPayments()
    monkeypatch.setattr(billing, "transport", httpx.MockTransport(f.handler))
    billing._mins.clear()
    return f


@pytest.fixture
def agent(monkeypatch: pytest.MonkeyPatch) -> ProxyNode:
    """A fake node agent with WireGuard, an IPv6 pool and the browser proxy."""
    f = ProxyNode()
    f.address6 = "fd66:66::1/64"
    monkeypatch.setattr(node, "connect", lambda url, token, fp: node.NodeClient(url, token, fp, transport=httpx.MockTransport(f.handler)))
    return f


@pytest.fixture(autouse=True)
def _test_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """Production's default: payments switched on in Admin are a test only the owner and named testers can use. (conftest makes
    them public for the other VPN modules; this one runs as production does.)"""
    monkeypatch.setattr(get_settings(), "vpn_payments_public", False, raising=False)
    monkeypatch.setattr(admin_auth, "current", lambda _db, token: object() if token == OWNER else None)


def browser(client: TestClient) -> TestClient:
    """Another browser: its own cookies."""
    return TestClient(client.app)


def as_owner(c: TestClient) -> TestClient:
    c.cookies.set(admin_auth.COOKIE, OWNER)
    return c


def open_kiwi(client: TestClient, db: Session, **over: Any) -> int:
    """The owner adds a server and opens Kiwi VPN with the trial and payments on, through Admin's API."""
    server = vpn.add_server(db, {"name": "Helsinki", "region": "Europe", "country_code": "fi", "agent_url": "https://203.0.113.10:8443", "agent_token": TOKEN,
                                 "cert_sha256": "ab" * 32, "capacity": 100})
    r = client.put("/api/vpn/admin/settings", json={"enabled": True, "trial_enabled": True, "payments_enabled": True, **over})
    assert r.status_code == 200, r.text
    assert r.json()["payments_public"] is False and r.json()["trial_guard_mode"] == "monitor"
    return server.id


def ipn_body(order: str, payment_id: str, status: str, paid: float, currency: str = "usdtsol") -> str:
    return json.dumps({"payment_id": int(payment_id), "payment_status": status, "pay_address": "So1TestAddress", "price_amount": 9.99,
                       "price_currency": "usd", "pay_amount": paid, "actually_paid": paid, "pay_currency": currency, "order_id": order,
                       "order_description": "Kiwi VPN time", "outcome_amount": paid, "outcome_currency": currency})


def sign(body: str, secret: str = IPN_SECRET) -> str:
    """NOWPayments' documented signature: HMAC-SHA512 of the body with its keys sorted, under the IPN secret."""
    return hmac.new(secret.encode(), json.dumps(json.loads(body), sort_keys=True, separators=(",", ":")).encode(), hashlib.sha512).hexdigest()


def post_ipn(c: TestClient, pay: VpnPayment | tuple[str, str], status: str, paid: float, secret: str = IPN_SECRET) -> httpx.Response:
    order, pid = (pay.order_ref, pay.provider_ref) if isinstance(pay, VpnPayment) else pay
    body = ipn_body(order, pid, status, paid)
    return c.post(HOOK, content=body, headers={"x-nowpayments-sig": sign(body, secret)})


def acct_of(db: Session, member: Member) -> VpnAccount:
    db.expire_all()
    return db.scalar(select(VpnAccount).where(VpnAccount.member_id == member.id))


def pay_row(db: Session, pay_id: int) -> VpnPayment:
    db.expire_all()
    return db.get(VpnPayment, pay_id)


def aware(d: dt.datetime | None) -> dt.datetime | None:
    return vpn._aware(d)


def secs(usd: float) -> dt.timedelta:
    return dt.timedelta(seconds=billing.seconds_for(usd))


def parse_conf(text: str) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    section = ""
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            out[section] = {}
        elif "=" in line:
            k, v = line.split("=", 1)
            out[section][k.strip()] = v.strip()
    return out


def assert_no_leak(conf: dict[str, dict[str, str]]) -> None:
    """All IPv4 and all IPv6 go into the tunnel (with no IPv6 address on the server, IPv6 is dropped, never sent around it), and
    names are resolved by the tunnel's DNS."""
    allowed = {a.strip() for a in conf["Peer"]["AllowedIPs"].split(",")}
    assert {"0.0.0.0/0", "::/0"} <= allowed, allowed
    assert conf["Interface"]["DNS"].strip(), "no DNS server: names would be resolved outside the tunnel"


def peer_on_node(agent: ProxyNode, key: str) -> bool:
    return key in agent.peers


# --- the journey -------------------------------------------------------------------------------------------------------


def test_one_customer_from_sign_up_to_renewal(client: TestClient, db: Session, agent: ProxyNode, np: NowPayments, clock: Clock,
                                               outbox: list[dict]) -> None:  # noqa: F811
    server_id = open_kiwi(client, db)
    t0 = clock.now

    # Another customer who must never be touched by anything that happens to this one, and the owner's own device
    other = member_at(db, "egg", "bystander", paid=False)
    other_acct = vpn.account_of(db, other)
    other_acct.paid_until = t0 + dt.timedelta(days=400)
    db.commit()
    other_dev = vpn.create_device(db, other, "other phone", server_id)[0]
    owner_dev = vpn.create_device(db, vpn.owner_account(db), "owner pc", server_id, preview=True)[0]
    db.commit()
    other_key, owner_key = other_dev.public_key, owner_dev.public_key

    # 1. Sign-up: a brand-new Kiwi Passport by email code -----------------------------------------------------------------
    me = browser(client)
    out = sign_in(me, outbox, "journey.customer@example.com")
    assert out["signed_in"] is True and out["created"] is True
    member = db.scalar(select(Member).where(Member.email == "journey.customer@example.com"))
    assert member is not None and member.email_verified_at is not None
    view = me.get("/api/members/vpn").json()
    assert view["enabled"] is True and view["eligible"] is False and view["reason"] == vpn.START_TRIAL
    assert view["payments"] is False and view["billing"] is None  # test mode: the public sees no way to pay
    assert view["trial"]["available"] is True  # a minutes-old Passport: enforce would refuse it, monitor never does
    assert me.post("/api/members/vpn/devices", json={"name": "phone", "server_id": server_id}).status_code == 403  # nothing yet

    # 2. Trial: starts once; the guards only record (monitor mode) -------------------------------------------------------
    r = me.post("/api/members/vpn/trial")
    assert r.status_code == 200, r.text
    assert r.json()["plan"]["kind"] == "trial" and r.json()["plan"]["days_left"] == 30 and r.json()["eligible"] is True
    acct = acct_of(db, member)
    assert aware(acct.trial_ends_at) == t0 + dt.timedelta(days=30) and acct.paid_until is None
    assert [s.code for s in db.scalars(select(VpnTrialSignal).where(VpnTrialSignal.member_id == member.id))] == ["account_too_new"]
    again = me.post("/api/members/vpn/trial")
    assert again.status_code == 409 and again.headers["x-kiwi-reason"] == "used"
    # a second Passport with the same mailbox (Gmail-style +tag): monitor lets it through and records what enforce would refuse
    twin = browser(client)
    sign_in(twin, outbox, "journey.customer+again@example.com")
    assert twin.post("/api/members/vpn/trial").status_code == 200
    twin_member = db.scalar(select(Member).where(Member.email == "journey.customer+again@example.com"))
    assert {s.code for s in db.scalars(select(VpnTrialSignal).where(VpnTrialSignal.member_id == twin_member.id))} == {"identity_used", "account_too_new"}

    # 3. A device: the config sends all IPv4 AND IPv6 through the tunnel, with the tunnel's DNS ---------------------------
    made = me.post("/api/members/vpn/devices", json={"name": "phone", "server_id": server_id})
    assert made.status_code == 201, made.text
    conf = parse_conf(made.json()["config"])
    assert_no_leak(conf)
    addrs = [a.strip() for a in conf["Interface"]["Address"].split(",")]
    assert addrs[0].endswith("/32") and addrs[1].startswith("fd66:66::") and addrs[1].endswith("/128")  # its own IPv6 in the tunnel
    phone_id = made.json()["device"]["id"]
    phone_key = keys.public_from_private(conf["Interface"]["PrivateKey"])
    assert agent.peers[phone_key]["allowed_ip"] == f"{made.json()['device']['ip']}/32"
    # the Windows / Android apps make their own key: what they're given routes the same way
    desk_key = keys.generate_keypair()[1]
    desk = me.post("/api/members/vpn/devices", json={"name": "PC (1)", "server_id": server_id, "public_key": desk_key})
    assert desk.status_code == 201 and "config" not in desk.json()
    peer = desk.json()["peer"]
    assert peer["allowed_ips"] == ["0.0.0.0/0", "::/0"] and peer["dns"] and len(peer["addresses"]) == 2
    assert me.get(f"/api/members/vpn/devices/{phone_id}/peer").json()["allowed_ips"] == ["0.0.0.0/0", "::/0"]
    # the browser extension: a login on the server's HTTPS proxy (the extension sends every URL through it, see vpn-extension/lib.js)
    login = me.post("/api/members/vpn/browser", json={"server_id": server_id})
    assert login.status_code == 201 and login.json()["host"] == "hel1.vpn.example" and login.json()["username"] in agent.users
    login_name = login.json()["username"]

    # 4. The server sync keeps everyone's devices on the node --------------------------------------------------------------
    out = vpn.sync_all(db)
    assert out["online"] == 1 and out["revoked"] == 0 and out["suspended"] == 0 and out["removed"] == 0
    assert {phone_key, desk_key, other_key, owner_key} <= set(agent.peers) and login_name in agent.users

    # 5. Paying in test mode: the public can't, a named tester and the owner can, and only with the start-now box ----------
    rate_limit.reset_for_tests()
    assert me.post("/api/members/vpn/usdt", json=PAY).status_code == 403
    assert client.get("/api/members/vpn/public").json()["payments"] is False
    assert browser(client).post("/api/members/vpn/anon").status_code == 403  # nor an anonymous key
    assert np.created == [] and db.scalars(select(VpnPayment)).all() == []  # the provider was never asked
    tester = member_at(db, "egg", "tess", paid=False)
    tess = browser(client)
    tess.cookies.set("pc_session", members.new_session(db, tester, 7))
    assert client.post("/api/vpn/admin/testers", json={"username": "tess", "days": 7}).status_code == 200
    assert tess.get("/api/members/vpn").json()["payments_test"] is True
    assert tess.post("/api/members/vpn/usdt", json=PAY).status_code == 200  # a named tester can
    assert client.post("/api/vpn/admin/testers", json={"username": "tess", "days": 0}).status_code == 200
    assert tess.post("/api/members/vpn/usdt", json=PAY).status_code == 403  # and not once the test ends
    as_owner(me)  # the owner testing with their own Passport, signed in to Admin in this browser
    view = me.get("/api/members/vpn").json()
    assert view["payments"] is True and view["payments_test"] is True and view["billing"]["usdt"] is True and view["billing"]["card"] is False
    before = len(np.created)
    no_box = me.post("/api/members/vpn/usdt", json={**PAY, "start_now": False})
    assert no_box.status_code == 400 and no_box.headers["x-kiwi-reason"] == "start_now"
    assert len(np.created) == before  # refused before the provider is asked
    started = me.post("/api/members/vpn/usdt", json=PAY)
    assert started.status_code == 200, started.text
    p1 = pay_row(db, started.json()["id"])
    assert p1.status == "pending" and p1.account_id == acct.id and started.json()["days"] == 30.0
    sent = np.created[-1]
    assert sent["order_id"] == p1.order_ref and sent["price_amount"] == 9.99 and sent["pay_currency"] == "usdtsol"
    assert sent["ipn_callback_url"].endswith(HOOK) and "journey" not in json.dumps(sent)  # nothing about the customer goes to the provider
    consent = db.scalar(select(MemberAuditLog).where(MemberAuditLog.action == billing.CONSENT_ACTION, MemberAuditLog.member_id == member.id))
    assert consent is not None and consent.details["payment_id"] == p1.id and consent.details["consent"] == billing.START_NOW_CONSENT

    # 6. Webhooks: only a valid signature counts ----------------------------------------------------------------------------
    body = ipn_body(p1.order_ref, p1.provider_ref, "finished", 9.99)
    assert client.post(HOOK, content=body).status_code == 401  # no signature
    assert client.post(HOOK, content=body, headers={"x-nowpayments-sig": ""}).status_code == 401
    assert client.post(HOOK, content=body, headers={"x-nowpayments-sig": sign(body, "not-the-secret")}).status_code == 401
    assert client.post(HOOK, content=body.replace('"actually_paid": 9.99', '"actually_paid": 99.9'),
                       headers={"x-nowpayments-sig": sign(body)}).status_code == 401  # the amount was changed after signing
    assert pay_row(db, p1.id).status == "pending" and acct_of(db, member).paid_until is None  # nothing credited

    # 7. A part payment, then the rest, then the same message again, then a late "expired" -----------------------------------
    trial_end = t0 + dt.timedelta(days=30)
    assert post_ipn(client, p1, "waiting", 0).json() == {"status": "waiting"}
    assert post_ipn(client, p1, "partially_paid", 5).status_code == 200
    assert aware(acct_of(db, member).paid_until) == trial_end + secs(5)  # after the trial: paying early costs no trial day
    assert post_ipn(client, p1, "finished", 9.99).status_code == 200
    expect = trial_end + secs(5) + secs(4.99)
    assert aware(acct_of(db, member).paid_until) == expect and abs((expect - trial_end).total_seconds() - 30 * DAY) <= 1
    for status, paid in (("finished", 9.99), ("partially_paid", 5), ("expired", 0), ("failed", 0)):
        assert post_ipn(client, p1, status, paid).status_code == 200
        row = pay_row(db, p1.id)
        assert row.status == "credited" and row.paid_usd == 9.99 and aware(acct_of(db, member).paid_until) == expect, status
    assert billing.check_payment(db, pay_row(db, p1.id))[1] is None  # asking the provider afterwards adds nothing either

    # 8. A second payment, $19.98, reported by the webhook WHILE vpn.reconcile is asking the provider about it ---------------
    clock.advance(minutes=5)
    started = me.post("/api/members/vpn/usdt", json={**PAY, "amount_usd": 19.98})
    assert started.status_code == 200 and started.json()["days"] == 60.0
    p2 = pay_row(db, started.json()["id"])
    np.answers[p2.provider_ref] = {"payment_status": "finished", "actually_paid": 19.98, "pay_currency": "usdtsol", "order_id": p2.order_ref}
    hooked: list[int] = []
    np.on_read[p2.provider_ref] = lambda: hooked.append(post_ipn(client, p2, "finished", 19.98).status_code)
    result = vpn.reconcile_payments(db)
    assert hooked == [200] and p2.provider_ref in np.reads and result["credited"] == 0  # the webhook won; reconcile found nothing left
    assert aware(acct_of(db, member).paid_until) == expect + secs(19.98)  # stacked after the paid time, and only once
    assert post_ipn(client, p2, "finished", 19.98).status_code == 200  # and the webhook again, then Check now
    rate_limit.reset_for_tests()
    assert me.post(f"/api/members/vpn/payments/{p2.id}/check").json() == {"status": "credited"}
    until = expect + secs(19.98)
    assert aware(acct_of(db, member).paid_until) == until
    history = me.get("/api/members/vpn/payments").json()["items"]
    assert [(h["status"], h["paid_usd"], h["days"]) for h in history[:2]] == [("credited", 19.98, 60.0), ("credited", 9.99, 30.0)]
    assert all(h["address"] is None for h in history[:2])  # the address is shown only while a payment is awaited
    view = me.get("/api/members/vpn").json()
    assert view["plan"]["kind"] == "paid" and view["plan"]["until"] == until.isoformat()

    # 9. Reminders: 3 days, 1 day, the end; each once ------------------------------------------------------------------------
    def reminders() -> list[str]:
        db.expire_all()
        return [n.title for n in db.scalars(select(MemberNotification).where(MemberNotification.member_id == member.id,
                                                                            MemberNotification.kind == "vpn_expiry").order_by(MemberNotification.id))]

    assert vpn.due_reminders(db, until - dt.timedelta(days=4)) == [] and reminders() == []
    for at, title in ((until - dt.timedelta(days=3) + dt.timedelta(hours=1), "ends in 3 days"), (until - dt.timedelta(hours=23), "ends in 1 day"),
                      (until + dt.timedelta(minutes=30), "has ended")):
        mails = vpn.due_reminders(db, at)
        assert [m["to"] for m in mails] == ["journey.customer@example.com"] and title in mails[0]["subject"], (title, mails)
        assert vpn.due_reminders(db, at + dt.timedelta(minutes=10)) == []  # once
    assert len(reminders()) == 3 and all("paid time" in t for t in reminders())

    # 10. Expiry: this customer's devices and browser login go off the node at the next sync; the rows are kept --------------
    clock.to(until + dt.timedelta(hours=1))
    out = vpn.sync_all(db)
    assert out["suspended"] >= 3 and out["revoked"] == 0
    assert not peer_on_node(agent, phone_key) and not peer_on_node(agent, desk_key) and login_name not in agent.users
    assert peer_on_node(agent, other_key) and peer_on_node(agent, owner_key)  # nobody else is touched
    assert {d.id for d in db.scalars(select(VpnDevice).where(VpnDevice.account_id == acct.id))} == {phone_id, desk.json()["device"]["id"]}
    assert aware(acct_of(db, member).lapsed_at) == clock.now
    view = me.get("/api/members/vpn").json()
    assert view["eligible"] is False and view["plan"]["kind"] == "expired" and view["reason"] == vpn.TIME_ENDED
    assert me.get(f"/api/members/vpn/devices/{phone_id}/peer").status_code == 403
    assert me.post("/api/members/vpn/devices", json={"name": "tablet", "server_id": server_id}).status_code == 403

    # 11. Renewal inside the grace period: back at once, with no new setup; the time starts now, not at the old end ------------
    clock.advance(days=2)
    rate_limit.reset_for_tests()
    started = me.post("/api/members/vpn/usdt", json=PAY)
    assert started.status_code == 200
    p3 = pay_row(db, started.json()["id"])
    assert post_ipn(client, p3, "finished", 9.99).status_code == 200
    assert peer_on_node(agent, phone_key) and peer_on_node(agent, desk_key) and login_name in agent.users  # no sync needed
    assert aware(acct_of(db, member).paid_until) == clock.now + secs(9.99) and acct_of(db, member).lapsed_at is None
    assert me.get(f"/api/members/vpn/devices/{phone_id}/peer").status_code == 200
    assert vpn.sync_all(db)["suspended"] == 0

    # 12. Out of time again, past the 30-day grace: only this customer's devices and login are removed for good ------------
    until = aware(acct_of(db, member).paid_until)
    clock.to(until + dt.timedelta(hours=1))
    vpn.sync_all(db)
    clock.advance(days=29)
    assert vpn.sync_all(db)["revoked"] == 0 and db.scalars(select(VpnDevice).where(VpnDevice.account_id == acct.id)).all()  # still kept
    clock.advance(days=1, hours=1)
    out = vpn.sync_all(db)
    assert out["revoked"] == 3  # two devices and the browser login
    db.expire_all()
    assert db.scalars(select(VpnDevice).where(VpnDevice.account_id == acct.id)).all() == []
    assert db.scalars(select(VpnBrowser).where(VpnBrowser.account_id == acct.id)).all() == []
    assert not peer_on_node(agent, phone_key) and not peer_on_node(agent, desk_key) and login_name not in agent.users
    assert peer_on_node(agent, other_key) and peer_on_node(agent, owner_key)
    assert db.get(VpnDevice, other_dev.id) is not None and db.get(VpnDevice, owner_dev.id) is not None
    assert acct_of(db, member).status == "active"  # the account itself stays: it can come back

    # 13. Renewal after the grace period: paid, eligible, and a new device works ------------------------------------------------
    rate_limit.reset_for_tests()
    started = me.post("/api/members/vpn/usdt", json=PAY)
    p4 = pay_row(db, started.json()["id"])
    assert post_ipn(client, p4, "finished", 9.99).status_code == 200
    assert aware(acct_of(db, member).paid_until) == clock.now + secs(9.99)
    view = me.get("/api/members/vpn").json()
    assert view["eligible"] is True and view["plan"]["kind"] == "paid" and view["devices"] == []
    again = me.post("/api/members/vpn/devices", json={"name": "phone", "server_id": server_id})
    assert again.status_code == 201
    assert_no_leak(parse_conf(again.json()["config"]))
    assert vpn.sync_all(db)["revoked"] == 0 and len(agent.peers) == 3  # the new phone, the bystander's and the owner's
    # what Admin sees: every payment of this customer credited once, with its consent
    paid = [p for p in client.get("/api/vpn/admin/payments").json()["items"] if p["id"] in (p1.id, p2.id, p3.id, p4.id)]
    assert sorted((p["paid_usd"], p["days"], p["status"], p["start_now"]) for p in paid) == sorted(
        [(9.99, 30.0, "credited", True), (19.98, 60.0, "credited", True), (9.99, 30.0, "credited", True), (9.99, 30.0, "credited", True)])


# --- amounts -------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("usd", [5.0, 7.5, 9.99, 12.5, 19.98, 29.97, 100.0])
def test_any_amount_buys_its_share_of_30_days(client: TestClient, db: Session, agent: ProxyNode, np: NowPayments, clock: Clock, usd: float) -> None:
    open_kiwi(client, db)
    m = member_at(db, "egg", "buyer", paid=False)
    c = as_owner(browser(client))
    c.cookies.set("pc_session", members.new_session(db, m, 7))
    r = c.post("/api/members/vpn/usdt", json={**PAY, "amount_usd": usd})
    assert r.status_code == 200, r.text
    assert r.json()["days"] == round(usd / 9.99 * 30, 1)
    assert post_ipn(client, pay_row(db, r.json()["id"]), "finished", usd).status_code == 200
    got = (aware(acct_of(db, m).paid_until) - clock.now).total_seconds()
    assert got == pytest.approx(usd / 9.99 * 30 * DAY, abs=1)


def test_usdt_off_by_a_little_from_a_dollar_still_buys_what_the_invoice_promised(client: TestClient, db: Session, agent: ProxyNode,
                                                                                  np: NowPayments, clock: Clock) -> None:
    open_kiwi(client, db)
    np.coins_per_usd = 1.0012  # the invoice asks for 10.001988 USDT for $9.99
    m = member_at(db, "egg", "rate", paid=False)
    c = as_owner(browser(client))
    c.cookies.set("pc_session", members.new_session(db, m, 7))
    pay = pay_row(db, c.post("/api/members/vpn/usdt", json=PAY).json()["id"])
    assert post_ipn(client, pay, "finished", pay.pay_amount).status_code == 200
    assert aware(acct_of(db, m).paid_until) == clock.now + secs(9.99)


# --- refunds -------------------------------------------------------------------------------------------------------------


def test_refunds_take_the_time_back_once_and_are_final(client: TestClient, db: Session, agent: ProxyNode, np: NowPayments, clock: Clock) -> None:
    open_kiwi(client, db)
    m = member_at(db, "egg", "refundee", paid=False)
    c = as_owner(browser(client))
    c.cookies.set("pc_session", members.new_session(db, m, 7))
    p1 = pay_row(db, c.post("/api/members/vpn/usdt", json=PAY).json()["id"])
    p2 = pay_row(db, c.post("/api/members/vpn/usdt", json=PAY).json()["id"])
    for p in (p1, p2):
        assert post_ipn(client, p, "finished", 9.99).status_code == 200
    t = clock.now
    assert aware(acct_of(db, m).paid_until) == t + secs(9.99) * 2
    # Admin records a refund made in NOWPayments: that payment's time goes, the other's stays
    r = client.post(f"/api/vpn/admin/payments/{p2.id}/refund", json={"reason": "customer asked"})
    assert r.status_code == 200 and r.json()["status"] == "refunded"
    assert aware(acct_of(db, m).paid_until) == t + secs(9.99)
    assert client.post(f"/api/vpn/admin/payments/{p2.id}/refund", json={"reason": "again"}).status_code == 409
    assert post_ipn(client, p2, "finished", 9.99).status_code == 200  # an old message sent again can't bring it back
    assert client.post(f"/api/vpn/admin/payments/{p2.id}/credit", json={"usd": 9.99, "reason": "oops"}).status_code == 409
    row = pay_row(db, p2.id)
    assert row.status == "refunded" and row.seconds == 0 and row.paid_usd == 0 and aware(acct_of(db, m).paid_until) == t + secs(9.99)
    # the provider's own "refunded" report does the same for the other payment, once
    assert post_ipn(client, p1, "refunded", 0).status_code == 200
    assert post_ipn(client, p1, "refunded", 0).status_code == 200
    assert aware(acct_of(db, m).paid_until) == t and pay_row(db, p1.id).status == "refunded"
    assert vpn.access(db, acct_of(db, m)) == "lapsed"
    # a refund reported by the provider with a bad signature changes nothing
    p3 = pay_row(db, c.post("/api/members/vpn/usdt", json=PAY).json()["id"])
    assert post_ipn(client, p3, "finished", 9.99).status_code == 200
    assert post_ipn(client, p3, "refunded", 0, secret="forged").status_code == 401
    assert pay_row(db, p3.id).status == "credited" and aware(acct_of(db, m).paid_until) == t + secs(9.99)


# --- the anonymous key ---------------------------------------------------------------------------------------------------


def test_an_anonymous_key_pays_connects_expires_and_renews(client: TestClient, db: Session, agent: ProxyNode, np: NowPayments, clock: Clock) -> None:
    server_id = open_kiwi(client, db)
    assert browser(client).post("/api/members/vpn/anon").status_code == 403  # test mode: not for the public
    owner = as_owner(browser(client))
    key = owner.post("/api/members/vpn/anon").json()["key"]
    head = {"x-kiwi-key": key}
    acct = billing.by_key(db, key)
    assert acct is not None and acct.member_id is None and acct.key_hash != key
    view = owner.get("/api/members/vpn/anon", headers=head).json()
    assert view["anonymous"] is True and view["eligible"] is False and view["trial"]["available"] is False  # no trial without a Passport
    assert owner.post("/api/members/vpn/anon/devices", json={"name": "phone", "server_id": server_id}, headers=head).status_code == 403
    # paying: test mode means only from the owner's browser; the box is required; the consent row names no one
    stranger = browser(client)
    assert stranger.post("/api/members/vpn/anon/usdt", json=PAY, headers=head).status_code == 403
    no_box = owner.post("/api/members/vpn/anon/usdt", json={**PAY, "start_now": False}, headers=head)
    assert no_box.status_code == 400 and no_box.headers["x-kiwi-reason"] == "start_now"
    r = owner.post("/api/members/vpn/anon/usdt", json=PAY, headers=head)
    assert r.status_code == 200, r.text
    pay = pay_row(db, r.json()["id"])
    consent = db.scalar(select(MemberAuditLog).where(MemberAuditLog.action == billing.CONSENT_ACTION))
    assert consent.member_id is None and consent.actor == "anonymous" and key not in json.dumps(consent.details)
    assert post_ipn(client, pay, "finished", 9.99, secret="forged").status_code == 401
    assert post_ipn(client, pay, "finished", 9.99).status_code == 200
    assert post_ipn(client, pay, "finished", 9.99).status_code == 200
    assert aware(billing.by_key(db, key).paid_until) == clock.now + secs(9.99)
    # a device, from any browser that has the key
    made = stranger.post("/api/members/vpn/anon/devices", json={"name": "phone", "server_id": server_id}, headers=head)
    assert made.status_code == 201, made.text
    conf = parse_conf(made.json()["config"])
    assert_no_leak(conf)
    dev_key = keys.public_from_private(conf["Interface"]["PrivateKey"])
    assert vpn.sync_all(db)["suspended"] == 0 and dev_key in agent.peers
    # no reminders for a key (nobody to reach); the page shows the date instead
    assert vpn.due_reminders(db, clock.now + dt.timedelta(days=29, hours=1)) == []
    assert stranger.get("/api/members/vpn/anon", headers=head).json()["plan"]["kind"] == "paid"
    # expiry: off at the next sync, kept; renewal brings it straight back
    clock.advance(days=31)
    vpn.sync_all(db)
    assert dev_key not in agent.peers and db.scalars(select(VpnDevice)).one().public_key == dev_key
    pay2 = pay_row(db, owner.post("/api/members/vpn/anon/usdt", json=PAY, headers=head).json()["id"])
    assert post_ipn(client, pay2, "finished", 9.99).status_code == 200
    assert dev_key in agent.peers
    # the privacy scrub: a week on, a settled payment no longer points at the key's account (the first one already went with the
    # sync 31 days on, which runs the scrub)
    assert pay_row(db, pay.id).account_id is None and pay_row(db, pay2.id).account_id == acct.id
    assert billing.scrub(db, clock.now + dt.timedelta(days=8)) == 1
    db.expire_all()
    assert all(p.account_id is None and p.pay_address is None and p.provider_ref is None for p in db.scalars(select(VpnPayment)))
    assert stranger.get("/api/members/vpn/anon", headers=head).json()["plan"]["kind"] == "paid"  # the time stays


# --- the browser extension and an IPv4-only server ---------------------------------------------------------------------------


def test_an_ipv4_only_server_still_captures_ipv6(client: TestClient, db: Session, agent: ProxyNode, np: NowPayments, clock: Clock) -> None:
    agent.address6 = None
    server_id = open_kiwi(client, db)
    m = member_at(db, "egg", "v4only", paid=False)
    c = browser(client)
    c.cookies.set("pc_session", members.new_session(db, m, 7))
    assert c.post("/api/members/vpn/trial").status_code == 200
    assert db.get(VpnServer, server_id).address6 == ""
    conf = parse_conf(c.post("/api/members/vpn/devices", json={"name": "laptop", "server_id": server_id}).json()["config"])
    assert conf["Interface"]["Address"].endswith("/32") and "," not in conf["Interface"]["Address"]  # no IPv6 address to give
    assert_no_leak(conf)  # but ::/0 is still routed into the tunnel, where it goes nowhere: IPv6 never leaves outside it
