"""Kiwi VPN billing: paid time on an account. $9.99 buys 30 days and any amount buys its share ($5 is 5/9.99 of that).

Two ways in. USDT (Ethereum, Tron, Solana through NOWPayments) is open to anyone: a Passport account, or an anonymous account
whose only credential is a key. Cards (Stripe) need a Passport account and renew monthly. A provider with no keys configured
is simply off, and the page says so; nothing here pretends a payment happened.

For anonymous accounts: the key is shown once and only its hash is stored, there is no email or name, nothing identifying is
sent to the provider, and `scrub` cuts the link between a payment and its account after SCRUB_AFTER (the amount and date stay
for the books, the address and provider reference go).
"""

import hashlib
import hmac
import json
import logging
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config.settings import get_settings
from app.db.base import utcnow
from app.models.vpn import VpnAccount, VpnPayment

logger = logging.getLogger(__name__)

PRICE_USD = 9.99
PERIOD_DAYS = 30
MIN_USD = 5.0  # below this, network and processor fees eat the payment
MAX_USD = 500.0
SCRUB_AFTER = timedelta(days=7)
CARD_PERIOD = timedelta(days=32)  # a card renews by calendar month: never lapse in a 31-day month while the renewal is on its way
NETWORKS = {"ethereum": ("usdterc20", "USDT on Ethereum"), "tron": ("usdttrc20", "USDT on Tron"), "solana": ("usdtsol", "USDT on Solana")}
NOWPAYMENTS = "https://api.nowpayments.io/v1"
STRIPE = "https://api.stripe.com/v1"
STRIPE_TOLERANCE_S = 300
transport: httpx.BaseTransport | None = None  # tests only


class BillingError(ValueError):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def _aware(d: datetime | None) -> datetime | None:
    return d.replace(tzinfo=timezone.utc) if d is not None and d.tzinfo is None else d


def _secret(name: str) -> str | None:
    v = getattr(get_settings(), name, None)
    return v.get_secret_value() if v is not None else None


def options() -> dict[str, Any]:
    """What the page can offer right now: only providers that have keys."""
    usdt = bool(_secret("nowpayments_api_key") and _secret("nowpayments_ipn_secret"))
    card = bool(_secret("stripe_secret_key") and _secret("stripe_webhook_secret"))
    return {"price_usd": PRICE_USD, "period_days": PERIOD_DAYS, "min_usd": MIN_USD, "max_usd": MAX_USD, "usdt": usdt, "card": card,
            "networks": [{"key": k, "name": v[1]} for k, v in NETWORKS.items()] if usdt else []}


def seconds_for(usd: float) -> int:
    return int(usd / PRICE_USD * PERIOD_DAYS * 86400)


# --- anonymous keys ----------------------------------------------------------------------------------------------------


def hash_key(key: str) -> str:
    """Case, spaces and dashes don't matter: people paste and type these."""
    return hashlib.sha256(re.sub(r"[^A-Z0-9]", "", (key or "").upper()).encode()).hexdigest()


def new_anonymous(db: Session) -> tuple[VpnAccount, str]:
    """A new account and its key: 160 random bits, shown once. Only the hash is kept, so nobody can recover or look it up."""
    raw = "".join(secrets.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ234567") for _ in range(32))
    key = "KIWI-" + "-".join(raw[i:i + 4] for i in range(0, 32, 4))
    acct = VpnAccount(key_hash=hash_key(key))
    db.add(acct)
    db.commit()
    return acct, key


def by_key(db: Session, key: str | None) -> VpnAccount | None:
    return db.scalar(select(VpnAccount).where(VpnAccount.key_hash == hash_key(key))) if key else None


# --- crediting ---------------------------------------------------------------------------------------------------------


def _credit(acct: VpnAccount, pay: VpnPayment, paid_usd: float, now: datetime) -> None:
    """Add the time this payment hasn't been credited yet: a repeated or growing webhook never pays twice."""
    delta = round(paid_usd - pay.paid_usd, 6)
    if delta <= 0:
        return
    extra = seconds_for(delta)
    start = max(now, _aware(acct.paid_until) or now)
    acct.paid_until = start + timedelta(seconds=extra)
    pay.paid_usd, pay.seconds, pay.status, pay.credited_at = paid_usd, pay.seconds + extra, "credited", now


def _http() -> httpx.Client:
    return httpx.Client(timeout=20.0, transport=transport)


# --- USDT (NOWPayments) ------------------------------------------------------------------------------------------------


def start_usdt(db: Session, acct: VpnAccount, amount_usd: float, network: str) -> dict[str, Any]:
    key, ipn = _secret("nowpayments_api_key"), _secret("nowpayments_ipn_secret")
    if not (key and ipn):
        raise BillingError("USDT payments aren't open yet.", 503)
    if network not in NETWORKS:
        raise BillingError("Pick Ethereum, Tron or Solana.", 400)
    if not MIN_USD <= amount_usd <= MAX_USD:
        raise BillingError(f"Add between ${MIN_USD:.0f} and ${MAX_USD:.0f} at a time.", 400)
    amount_usd = round(amount_usd, 2)
    pay = VpnPayment(account_id=acct.id, provider="nowpayments", order_ref=secrets.token_urlsafe(18), network=network, amount_usd=amount_usd)
    db.add(pay)
    db.commit()  # no database transaction waits on the provider
    body = {"price_amount": amount_usd, "price_currency": "usd", "pay_currency": NETWORKS[network][0], "order_id": pay.order_ref,
            "order_description": "Kiwi VPN time", "ipn_callback_url": f"{get_settings().members_public_url}/api/members/vpn/webhooks/nowpayments"}
    try:
        with _http() as c:
            r = c.post(f"{NOWPAYMENTS}/payment", json=body, headers={"x-api-key": key})
        r.raise_for_status()
        out = r.json()
        pay.provider_ref, pay.pay_address, pay.pay_amount = str(out["payment_id"]), str(out["pay_address"]), float(out["pay_amount"])
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as e:
        pay.status = "failed"
        db.commit()
        logger.warning("vpn: creating a USDT payment failed: %s", type(e).__name__)
        raise BillingError("The payment service didn't answer. Nothing was charged: try again in a moment.", 502) from None
    db.commit()
    return {"id": pay.id, "network": network, "network_name": NETWORKS[network][1], "address": pay.pay_address, "amount": pay.pay_amount, "amount_usd": amount_usd,
            "expires_at": out.get("expiration_estimate_date"), "days": round(seconds_for(amount_usd) / 86400, 1)}


def _np_signature(data: Any, secret: str) -> str:
    """HMAC-SHA512 of the JSON with its keys sorted at every level, as NOWPayments signs it."""
    return hmac.new(secret.encode(), json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(), hashlib.sha512).hexdigest()


def nowpayments_webhook(db: Session, raw: bytes, signature: str | None, now: datetime | None = None) -> str:
    secret = _secret("nowpayments_ipn_secret")
    if not secret:
        raise BillingError("Not configured.", 503)
    try:
        data = json.loads(raw)
    except ValueError:
        raise BillingError("Bad request.", 400) from None
    if not signature or not hmac.compare_digest(_np_signature(data, secret), signature.lower()):
        raise BillingError("Bad signature.", 401)
    now = now or utcnow()
    pay = db.scalar(select(VpnPayment).where(VpnPayment.order_ref == str(data.get("order_id"))).with_for_update())
    if pay is None or pay.account_id is None:  # not ours, or already scrubbed
        return "ignored"
    if data.get("pay_currency") != NETWORKS.get(pay.network or "", ("",))[0]:
        logger.warning("vpn: payment %s came in an unexpected currency", pay.id)
        return "ignored"
    acct = db.get(VpnAccount, pay.account_id)
    status = str(data.get("payment_status"))
    if status in ("finished", "partially_paid") and acct is not None:
        try:
            paid = float(data["actually_paid"])  # 1 USDT counts as $1
        except (KeyError, TypeError, ValueError):
            raise BillingError("Bad request.", 400) from None
        _credit(acct, pay, min(paid, MAX_USD * 2), now)
    elif status == "refunded" and acct is not None and pay.seconds:
        if acct.paid_until is not None:
            acct.paid_until = _aware(acct.paid_until) - timedelta(seconds=pay.seconds)
        pay.status, pay.seconds, pay.paid_usd = "refunded", 0, 0.0
    elif status in ("failed", "expired") and pay.status == "pending":
        pay.status = status
    db.commit()
    return status


# --- cards (Stripe) ----------------------------------------------------------------------------------------------------


def start_card(db: Session, acct: VpnAccount) -> str:
    """A hosted Stripe checkout for the monthly subscription: the card never reaches Price Chat. Passport accounts only."""
    key = _secret("stripe_secret_key")
    if not (key and _secret("stripe_webhook_secret")):
        raise BillingError("Card payments aren't open yet.", 503)
    if acct.member_id is None:
        raise BillingError("Cards need a Passport account. Anonymous keys pay with USDT.", 403)
    back = f"{get_settings().members_public_url}/vpn"
    form = {"mode": "subscription", "success_url": f"{back}?paid=1", "cancel_url": back, "client_reference_id": str(acct.id),
            "line_items[0][quantity]": "1", "line_items[0][price_data][currency]": "usd", "line_items[0][price_data][unit_amount]": str(round(PRICE_USD * 100)),
            "line_items[0][price_data][recurring][interval]": "month", "line_items[0][price_data][product_data][name]": "Kiwi VPN",
            "subscription_data[metadata][vpn_account]": str(acct.id)}
    if acct.stripe_customer:
        form["customer"] = acct.stripe_customer
    return _stripe_url(key, "checkout/sessions", form)


def card_portal(acct: VpnAccount) -> str:
    """Stripe's page for changing the card or cancelling."""
    key = _secret("stripe_secret_key")
    if not key or not acct.stripe_customer:
        raise BillingError("No card subscription on this account.", 404)
    return _stripe_url(key, "billing_portal/sessions", {"customer": acct.stripe_customer, "return_url": f"{get_settings().members_public_url}/vpn"})


def _stripe_url(key: str, path: str, form: dict[str, str]) -> str:
    try:
        with _http() as c:
            r = c.post(f"{STRIPE}/{path}", data=form, auth=(key, ""))
        r.raise_for_status()
        return str(r.json()["url"])
    except (httpx.HTTPError, KeyError, ValueError) as e:
        logger.warning("vpn: Stripe %s failed: %s", path, type(e).__name__)
        raise BillingError("The payment service didn't answer. Nothing was charged: try again in a moment.", 502) from None


def _verify_stripe(raw: bytes, header: str | None, secret: str, now_s: float) -> None:
    parts = [p.split("=", 1) for p in (header or "").split(",") if "=" in p]
    stamp = next((v for k, v in parts if k == "t"), "")
    expected = hmac.new(secret.encode(), f"{stamp}.".encode() + raw, hashlib.sha256).hexdigest()
    if not stamp.isdigit() or abs(now_s - int(stamp)) > STRIPE_TOLERANCE_S or not any(k == "v1" and hmac.compare_digest(v, expected) for k, v in parts):
        raise BillingError("Bad signature.", 401)


def _invoice_account(db: Session, inv: dict[str, Any]) -> VpnAccount | None:
    meta = ((inv.get("parent") or {}).get("subscription_details") or {}).get("metadata") or (inv.get("subscription_details") or {}).get("metadata") or {}
    if str(meta.get("vpn_account", "")).isdigit():
        return db.get(VpnAccount, int(meta["vpn_account"]))
    return db.scalar(select(VpnAccount).where(VpnAccount.stripe_customer == inv.get("customer"))) if inv.get("customer") else None


def stripe_webhook(db: Session, raw: bytes, header: str | None, now: datetime | None = None) -> str:
    secret = _secret("stripe_webhook_secret")
    if not secret:
        raise BillingError("Not configured.", 503)
    _verify_stripe(raw, header, secret, time.time())
    now = now or utcnow()
    try:
        event = json.loads(raw)
        kind, obj = event["type"], event["data"]["object"]
    except (ValueError, KeyError, TypeError):
        raise BillingError("Bad request.", 400) from None
    if kind == "checkout.session.completed" and str(obj.get("client_reference_id", "")).isdigit():
        acct = db.get(VpnAccount, int(obj["client_reference_id"]))
        if acct is not None and obj.get("customer"):
            acct.stripe_customer = str(obj["customer"])
    elif kind == "invoice.paid" and obj.get("amount_paid"):
        acct = _invoice_account(db, obj)
        ref = f"stripe:{obj.get('id')}"
        if acct is not None and db.scalar(select(VpnPayment.id).where(VpnPayment.order_ref == ref)) is None:
            usd = int(obj["amount_paid"]) / 100
            until = now + CARD_PERIOD
            if until > (_aware(acct.paid_until) or now):  # a renewal sets the date: unused days of a card period don't stack
                acct.paid_until = until
            db.add(VpnPayment(account_id=acct.id, provider="stripe", order_ref=ref, provider_ref=str(obj.get("id")), amount_usd=usd, paid_usd=usd,
                              seconds=int(CARD_PERIOD.total_seconds()), status="credited", credited_at=now))
    else:
        return "ignored"
    db.commit()
    return kind


# --- upkeep and totals -------------------------------------------------------------------------------------------------


def scrub(db: Session, now: datetime | None = None) -> int:
    """Anonymous accounts: after SCRUB_AFTER a payment no longer points at its account or its blockchain payment."""
    now = now or utcnow()
    anon = select(VpnAccount.id).where(VpnAccount.member_id.is_(None))
    rows = db.scalars(select(VpnPayment).where(VpnPayment.account_id.in_(anon), VpnPayment.created_at < now - SCRUB_AFTER)).all()
    for p in rows:
        p.account_id, p.pay_address, p.provider_ref, p.pay_amount = None, None, None, None
        if p.status == "pending":
            p.status = "expired"
    if rows:
        db.commit()
    return len(rows)


def totals(db: Session, now: datetime | None = None) -> dict[str, Any]:
    now = now or utcnow()
    revenue = db.scalar(select(func.coalesce(func.sum(VpnPayment.paid_usd), 0.0)).where(VpnPayment.credited_at >= now - timedelta(days=30)))
    return {"paying": int(db.scalar(select(func.count()).select_from(VpnAccount).where(VpnAccount.paid_until > now)) or 0),
            "anonymous": int(db.scalar(select(func.count()).select_from(VpnAccount).where(VpnAccount.key_hash.is_not(None))) or 0),
            "revenue_30d_usd": round(float(revenue or 0), 2), **{k: options()[k] for k in ("usdt", "card")}}
