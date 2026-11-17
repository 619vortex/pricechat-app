"""Kiwi VPN billing: paid time on an account. $9.99 buys 30 days and any amount buys its share ($5 is 5/9.99 of that).

USDT (Ethereum, Tron, Solana through NOWPayments) is open to anyone: a Passport account, or an anonymous account whose only
credential is a key. It's renewed by hand, and nothing is ever charged automatically. The Stripe code below is off behind
CARD_SUBSCRIPTIONS, keys or no keys, because it would bill by itself. A provider with no keys configured is simply off, and the
page says so; nothing here pretends a payment happened.

A payment reaches an account three ways, all through _apply_np, so none can credit twice: the provider's signed webhook, the
vpn.reconcile job asking the provider about anything unfinished, and someone pressing Check now.

For anonymous accounts: the key is shown once and only its hash is stored, there is no email or name, nothing identifying is
sent to the provider, and `scrub` cuts the link between a payment and its account after SCRUB_AFTER (the amount and date stay
for the books, the address and provider reference go).
"""

import hashlib
import hmac
import json
import logging
import math
import re
import secrets
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.config.settings import get_settings
from app.db.base import utcnow
from app.models.members import MemberAuditLog
from app.models.vpn import VpnAccount, VpnPayment

logger = logging.getLogger(__name__)

CARD_SUBSCRIPTIONS = False  # a card subscription renews by itself, and the owner's rule is no automatic billing: renewal is by hand
PRICE_USD = 9.99
PERIOD_DAYS = 30
MIN_USD = 5.0  # below this, network and processor fees eat the payment
MAX_USD = 500.0
MIN_TTL_S = 600.0
SCRUB_AFTER = timedelta(days=7)
UNFINISHED = ("pending", "review")  # the provider may still have news: these keep their account link and aren't scrubbed
GIVE_UP_AFTER = timedelta(days=14)  # still waiting after this: marked expired, and a late webhook can still credit it until the scrub
RECHECK_EVERY = timedelta(minutes=10)
RECHECK_SLOWLY_AFTER = timedelta(hours=48)
RECHECK_SLOWLY_EVERY = timedelta(hours=6)
RECONCILE_LIMIT = 50
CARD_PERIOD = timedelta(days=32)  # a card renews by calendar month: never lapse in a 31-day month while the renewal is on its way
NETWORKS = {"ethereum": ("usdterc20", "USDT on Ethereum"), "tron": ("usdttrc20", "USDT on Tron"), "solana": ("usdtsol", "USDT on Solana")}
NOWPAYMENTS = "https://api.nowpayments.io/v1"
STRIPE = "https://api.stripe.com/v1"
STRIPE_TOLERANCE_S = 300
transport: httpx.BaseTransport | None = None  # tests only
# What this process has seen from NOWPayments since it started, for Admin (totals) and the first real payment's check
# (docs/hardening/FIRST-PAYMENT.md). Counts and times only: no payment, address or account.
ipn: dict[str, Any] = {"signed": 0, "bad_signature": 0, "last_signed_at": None, "last_bad_signature_at": None, "last_form": None,
                       "credited_by_webhook": 0, "credited_by_check": 0}


class BillingError(ValueError):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def _aware(d: datetime | None) -> datetime | None:
    return d.replace(tzinfo=timezone.utc) if d is not None and d.tzinfo is None else d


def _secret(name: str) -> str | None:
    v = getattr(get_settings(), name, None)
    return v.get_secret_value() if v is not None else None


def _usdt_on() -> bool:
    return bool(_secret("nowpayments_api_key") and _secret("nowpayments_ipn_secret"))


def _card_on() -> bool:
    return bool(_secret("stripe_secret_key") and _secret("stripe_webhook_secret"))


_mins: dict[str, tuple[float, float | None]] = {}  # network -> (read at, the provider's minimum in USD)


def network_min(network: str) -> float:
    """The most this network requires right now: our floor, or the provider's live minimum if higher. It moves with network fees
    (Tron has been above the $9.99 price), so it's read from the provider and remembered for a while. A failed read falls back
    to the last known value, and the provider still has the final say when the payment is created."""
    now = time.monotonic()
    at, known = _mins.get(network, (-1e9, None))
    if now - at > MIN_TTL_S:
        code, fresh = NETWORKS[network][0], None
        try:
            with httpx.Client(timeout=5.0, transport=transport) as c:
                r = c.get(f"{NOWPAYMENTS}/min-amount", params={"currency_from": code, "currency_to": code, "fiat_equivalent": "usd"},
                          headers={"x-api-key": _secret("nowpayments_api_key") or ""})
            r.raise_for_status()
            fresh = float(r.json()["fiat_equivalent"])
        except (httpx.HTTPError, KeyError, TypeError, ValueError):
            logger.info("vpn: couldn't read the minimum for %s", network)
        known = fresh if fresh is not None else known
        _mins[network] = (now, known)
    return max(MIN_USD, math.ceil((known or 0) * 100) / 100)


def options() -> dict[str, Any]:
    """What the page can offer right now: only providers that have keys."""
    usdt = _usdt_on()
    return {"price_usd": PRICE_USD, "period_days": PERIOD_DAYS, "min_usd": MIN_USD, "max_usd": MAX_USD, "usdt": usdt, "card": CARD_SUBSCRIPTIONS and _card_on(),
            "networks": [{"key": k, "name": v[1], "min_usd": network_min(k)} for k, v in NETWORKS.items()] if usdt else []}


def can_charge() -> bool:
    return _usdt_on() or (CARD_SUBSCRIPTIONS and _card_on())


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


def _claim(db: Session, pay: VpnPayment, paid_usd: float) -> float | None:
    """Move this payment's credited amount from what was read to `paid_usd` in one conditional UPDATE, and return the amount before
    (None: nothing left to credit). The webhook, vpn.reconcile and Check now can read the same row at the same moment; only one
    of them can move it, and the other re-reads it and credits only what is still missing, so money is never credited twice."""
    db.flush()  # this session's own pending changes first: a refresh below must not throw them away
    for _ in range(5):
        before = pay.paid_usd
        if round(paid_usd - before, 6) <= 0:
            return None
        moved = db.execute(update(VpnPayment).where(VpnPayment.id == pay.id, VpnPayment.paid_usd == before)
                           .values(paid_usd=paid_usd).execution_options(synchronize_session=False)).rowcount
        if moved:
            return before
        db.refresh(pay)
    raise BillingError("The payment is busy. Try again in a moment.", 503)


def _credit(db: Session, acct: VpnAccount, pay: VpnPayment, paid_usd: float, now: datetime) -> int:
    """Add the time this payment hasn't been credited yet, and return the seconds added: a repeated, growing or simultaneous report
    never pays twice."""
    before = _claim(db, pay, paid_usd)
    if before is None:
        return 0
    db.refresh(acct, with_for_update=True)  # its latest paid time: another payment may have just added some
    extra = seconds_for(round(paid_usd - before, 6))
    # Added after whatever's left, a free trial included: paying early never costs anyone a day they already had
    trial = _aware(acct.trial_ends_at)
    start = max(now, _aware(acct.paid_until) or now, trial if trial is not None and trial > now else now)
    acct.paid_until = start + timedelta(seconds=extra)
    pay.paid_usd, pay.seconds, pay.status, pay.credited_at = paid_usd, pay.seconds + extra, "credited", now
    return extra


def _http() -> httpx.Client:
    return httpx.Client(timeout=20.0, transport=transport)


# --- USDT (NOWPayments) ------------------------------------------------------------------------------------------------


def start_usdt(db: Session, acct: VpnAccount, amount_usd: float, network: str, start_now: bool = False) -> dict[str, Any]:
    """`start_now`: the customer ticked the checkout box asking for their time to start as soon as the payment is confirmed (the
    EU/UK 14-day right to cancel, /vpn/refunds). It's recorded with the payment as a member audit row (START_NOW_CONSENT), in the
    same commit that creates the payment, so every payment the website started carries its proof. No migration: the audit table
    already exists and keeps its rows when a member is deleted (member_id set to NULL)."""
    key, ipn = _secret("nowpayments_api_key"), _secret("nowpayments_ipn_secret")
    if not (key and ipn):
        raise BillingError("USDT payments aren't open yet.", 503)
    if acct.status != "active":
        raise BillingError("This account can't use Kiwi VPN, so it can't be charged.", 403)
    if network not in NETWORKS:
        raise BillingError("Pick Ethereum, Tron or Solana.", 400)
    if not MIN_USD <= amount_usd <= MAX_USD:
        raise BillingError(f"Add between ${MIN_USD:.0f} and ${MAX_USD:.0f} at a time.", 400)
    floor = network_min(network)
    if amount_usd < floor:
        raise BillingError(f"{NETWORKS[network][1]} needs at least ${floor:.2f} right now. Add more or pick another network.", 400)
    amount_usd = round(amount_usd, 2)
    pay = VpnPayment(account_id=acct.id, provider="nowpayments", order_ref=secrets.token_urlsafe(18), network=network, amount_usd=amount_usd)
    db.add(pay)
    if start_now:
        db.flush()  # the payment's id, for the consent record
        record_start_now(db, acct, pay)
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


# The checkout box's wording, by version: what the customer agreed to is the text in the website's AddTime form (vpn.tsx START_NOW)
# on the date named here. Change the version whenever that wording changes.
START_NOW_CONSENT = "start-now/2026-11-17"
CONSENT_ACTION = "vpn_payment_consent"


def record_start_now(db: Session, acct: VpnAccount, pay: VpnPayment) -> None:
    """Proof that this payment's customer asked for their time to start at once and acknowledged what that means for their
    right to cancel. A member's row names the member; an anonymous key's names only the payment (no key, address or amount)."""
    db.add(MemberAuditLog(member_id=acct.member_id, actor="member" if acct.member_id is not None else "anonymous", action=CONSENT_ACTION,
                          details={"payment_id": pay.id, "receipt": f"KV-{pay.id:06d}", "consent": START_NOW_CONSENT}))


def start_now_ids(db: Session, payment_ids: list[int]) -> set[int]:
    """For Admin: which of these payments' customers ticked the start-now box (one audit row per payment the website started)."""
    if not payment_ids:
        return set()
    wanted = set(payment_ids)
    rows = db.scalars(select(MemberAuditLog.details).where(MemberAuditLog.action == CONSENT_ACTION)).all()
    return {d["payment_id"] for d in rows if isinstance(d, dict) and d.get("payment_id") in wanted}


class _Number(str):
    """A JSON number exactly as it was sent. Turning it into a float and back can change its text: 0.00005 becomes 5e-05."""


def _as_javascript(text: str) -> str:
    """How JSON.stringify writes this number."""
    value = float(text)
    if value.is_integer() and abs(value) < 1e21:
        return str(int(value))
    if 1e-6 <= abs(value) < 1e21:
        return format(Decimal(repr(value)), "f")
    mantissa, exponent = repr(value).split("e")
    return f"{mantissa}e{int(exponent):+d}"


def _as_python(text: str) -> str:
    return json.dumps(json.loads(text))


def _sorted_json(value: Any, number: Callable[[str], str], only: set[str] | None = None) -> str:
    """Keys sorted at every level, with no spaces. `only` is what JSON.stringify(data, Object.keys(data).sort()) does to nested keys."""
    if isinstance(value, dict):
        keys = sorted(k for k in value if only is None or k in only)
        return "{" + ",".join(f"{json.dumps(k, ensure_ascii=False)}:{_sorted_json(value[k], number, only)}" for k in keys) + "}"
    if isinstance(value, list):
        return "[" + ",".join(_sorted_json(v, number, only) for v in value) + "]"
    if isinstance(value, _Number):
        return number(value)
    return json.dumps(value, ensure_ascii=False)


def _np_named_forms(raw: bytes) -> list[tuple[str, bytes]]:
    """Every form of this body the provider may have signed, each with a name for the log. Only someone holding the IPN secret can
    match any of them, so accepting each is safe, and refusing a genuine one would lose a customer's payment. Sources disagree on the
    exact form: the first real payment's log line ("signed form ...") shows which one NOWPayments uses."""
    data = json.loads(raw, parse_float=_Number, parse_int=_Number)
    forms = [("raw", raw)]
    for label, number in (("text", str), ("javascript", _as_javascript), ("python", _as_python)):
        forms.append((f"sorted-{label}", _sorted_json(data, number).encode()))
        if isinstance(data, dict):
            forms.append((f"sorted-top-keys-{label}", _sorted_json(data, number, set(data)).encode()))
    seen: dict[bytes, str] = {}
    for name, form in forms:
        seen.setdefault(form, name)
    return [(name, form) for form, name in seen.items()]


def _np_signed_forms(raw: bytes) -> list[bytes]:
    return [form for _, form in _np_named_forms(raw)]


def _np_signature_form(raw: bytes, signature: str | None, secret: str) -> str | None:
    """The name of the form the signature matches, or None."""
    if not signature:
        return None
    given = signature.strip().lower()
    for name, form in _np_named_forms(raw):
        if hmac.compare_digest(hmac.new(secret.encode(), form, hashlib.sha512).hexdigest(), given):
            return name
    return None


def _np_signature_ok(raw: bytes, signature: str | None, secret: str) -> bool:
    return _np_signature_form(raw, signature, secret) is not None


def _set_status(db: Session, pay: VpnPayment, expect: str, status: str, note: str | None = None) -> None:
    """Change the status only if it's still what was read: a report read before another one credited this payment must not mark a
    credited payment failed or expired."""
    values: dict[str, Any] = {"status": status} if note is None else {"status": status, "note": note}
    db.flush()
    if db.execute(update(VpnPayment).where(VpnPayment.id == pay.id, VpnPayment.status == expect).values(**values)
                  .execution_options(synchronize_session=False)).rowcount:
        pay.status = status
        if note is not None:
            pay.note = note
        db.flush()
    else:
        db.refresh(pay)


def _apply_np(db: Session, pay: VpnPayment, status: str, actually_paid: Any, currency: Any, now: datetime) -> tuple[str, int | None]:
    """The one place a provider's report changes anything. Returns (the status, the account that just gained time, if any). The caller commits."""
    if pay.status == "refunded":  # final: an old "finished" sent again must not bring the time back
        return "refunded", None
    money_arrived = status in ("finished", "partially_paid")
    if pay.account_id is None:  # the privacy scrub cut its link: nobody can be credited automatically, so Admin is told
        if money_arrived and pay.status not in ("credited", "unmatched"):
            pay.status, pay.note = "unmatched", "Money arrived after the account link was removed. Credit it by hand when the customer gets in touch."
            logger.warning("vpn: payment %s arrived with no account to credit", pay.id)
            return "unmatched", None
        return "ignored", None
    if status == "wrong_asset_confirmed" or (money_arrived and str(currency).lower() != NETWORKS.get(pay.network or "", ("",))[0]):
        logger.warning("vpn: payment %s came in an unexpected currency", pay.id)
        if pay.status == "pending":
            _set_status(db, pay, "pending", "review", "Arrived in an unexpected currency. Check it in NOWPayments.")
        return "ignored", None
    acct = db.get(VpnAccount, pay.account_id)
    if money_arrived and acct is not None:
        try:
            coins = float(actually_paid)
        except (TypeError, ValueError):
            raise BillingError("Bad request.", 400) from None
        if not math.isfinite(coins):  # NaN would crash the time sum and infinity would credit the cap: neither is money
            raise BillingError("Bad request.", 400)
        # USDT isn't exactly $1: the invoice's own rate, so paying what it asked for buys exactly what it promised
        paid = round(coins * pay.amount_usd / pay.pay_amount, 2) if pay.pay_amount else coins
        added = _credit(db, acct, pay, min(paid, MAX_USD * 2), now)
        return status, acct.id if added > 0 else None
    if status == "refunded":
        _take_back(acct, pay)
    elif status in ("failed", "expired", "cancelled") and pay.status == "pending":
        _set_status(db, pay, "pending", "failed" if status == "cancelled" else status)
    return status, None


def _take_back(acct: VpnAccount | None, pay: VpnPayment) -> None:
    if acct is not None and acct.paid_until is not None and pay.seconds:
        acct.paid_until = _aware(acct.paid_until) - timedelta(seconds=pay.seconds)
    pay.status, pay.seconds, pay.paid_usd = "refunded", 0, 0.0


def nowpayments_webhook(db: Session, raw: bytes, signature: str | None, now: datetime | None = None) -> tuple[str, int | None]:
    """(the status, the account that just gained time, if any)."""
    secret = _secret("nowpayments_ipn_secret")
    if not secret:
        raise BillingError("Not configured.", 503)
    try:
        form = _np_signature_form(raw, signature, secret)
        data = json.loads(raw)
    except (ValueError, RecursionError):  # RecursionError: deeply nested JSON (audit I4) is a bad request, not a 500
        raise BillingError("Bad request.", 400) from None
    now = now or utcnow()
    if form is None:
        ipn["bad_signature"] += 1
        ipn["last_bad_signature_at"] = now.isoformat()
        # Most likely NOWPAYMENTS_IPN_SECRET on the server isn't the IPN secret in the NOWPayments dashboard. vpn.reconcile still
        # credits payments by asking the provider, so nobody loses money, but this must be fixed (docs/hardening/FIRST-PAYMENT.md).
        logger.warning("vpn: NOWPayments webhook refused: bad or missing signature")
        raise BillingError("Bad signature.", 401)
    ipn["signed"] += 1
    ipn["last_signed_at"], ipn["last_form"] = now.isoformat(), form
    if not isinstance(data, dict):
        raise BillingError("Bad request.", 400)
    pay = db.scalar(select(VpnPayment).where(VpnPayment.order_ref == str(data.get("order_id"))).with_for_update())
    if pay is None:  # not ours
        return "ignored", None
    payment_id = str(data.get("payment_id") or "")
    if payment_id and pay.provider_ref and payment_id != pay.provider_ref:
        pay = _repeat_deposit(db, pay, payment_id)  # more money sent to the same address: the provider gives it its own id
    seconds = pay.seconds
    status, credited = _apply_np(db, pay, str(data.get("payment_status")), data.get("actually_paid"), data.get("pay_currency"), now)
    db.commit()
    if credited is not None:
        ipn["credited_by_webhook"] += 1
    logger.info("vpn: NOWPayments webhook (signed form %s) for payment %s: provider says %s, now %s, credited %+d s",
                form, pay.id, str(data.get("payment_status"))[:20], pay.status, pay.seconds - seconds)
    return status, credited


def _repeat_deposit(db: Session, first: VpnPayment, payment_id: str) -> VpnPayment:
    ref = f"np:{payment_id}"
    row = db.scalar(select(VpnPayment).where(VpnPayment.order_ref == ref))
    if row is None:
        row = VpnPayment(account_id=first.account_id, provider="nowpayments", order_ref=ref, provider_ref=payment_id, network=first.network, amount_usd=first.amount_usd, pay_amount=first.pay_amount)
        db.add(row)
        db.flush()
    return row


# --- recovery: what happens when a webhook never arrives, or a person has to step in -------------------------------------


def _ask_provider(provider_ref: str) -> dict[str, Any]:
    """The provider's own record of a payment. Raises httpx.HTTPError or ValueError if it can't be read. Holds no database transaction."""
    with _http() as c:
        r = c.get(f"{NOWPAYMENTS}/payment/{provider_ref}", headers={"x-api-key": _secret("nowpayments_api_key") or ""})
    r.raise_for_status()
    out = r.json()
    if not isinstance(out, dict):
        raise ValueError("not an object")
    return out


def check_payment(db: Session, pay: VpnPayment, now: datetime | None = None) -> tuple[str, int | None]:
    """Ask the provider about one payment and apply its answer. Safe to repeat: time is only ever added for money not yet credited."""
    now = now or utcnow()
    if not pay.provider_ref or not _secret("nowpayments_api_key"):
        return pay.status, None
    pay_id, ref, order = pay.id, pay.provider_ref, pay.order_ref
    db.commit()
    try:
        out = _ask_provider(ref)
    except (httpx.HTTPError, ValueError) as e:
        logger.info("vpn: couldn't check payment %s: %s", pay_id, type(e).__name__)
        raise BillingError("The payment service didn't answer. Try again in a moment.", 502) from None
    pay = db.get(VpnPayment, pay_id)
    pay.checked_at = now
    if out.get("order_id") and str(out["order_id"]) != order and not order.startswith("np:"):
        logger.warning("vpn: the provider's record for payment %s names another order", pay_id)
        db.commit()
        return pay.status, None
    seconds = pay.seconds
    _, credited = _apply_np(db, pay, str(out.get("payment_status")), out.get("actually_paid"), out.get("pay_currency"), now)
    db.commit()
    if credited is not None:
        ipn["credited_by_check"] += 1
    logger.info("vpn: asked NOWPayments about payment %s: provider says %s, now %s, credited %+d s",
                pay.id, str(out.get("payment_status"))[:20], pay.status, pay.seconds - seconds)
    return pay.status, credited


def reconcile(db: Session, now: datetime | None = None) -> dict[str, Any]:
    """The vpn.reconcile job: a lost webhook mustn't cost a customer their payment, so anything unfinished is asked about, often at first and then rarely."""
    now = now or utcnow()
    out: dict[str, Any] = {"checked": 0, "credited": 0, "errors": 0, "gave_up": 0, "accounts": []}
    if not _usdt_on():
        return out
    # also a part-payment still short of its invoice, for a while: the rest may be on its way
    short = (VpnPayment.status == "credited") & (VpnPayment.paid_usd < VpnPayment.amount_usd) & (VpnPayment.created_at >= now - RECHECK_SLOWLY_AFTER)
    rows = db.scalars(select(VpnPayment).where(VpnPayment.provider == "nowpayments", VpnPayment.provider_ref.is_not(None), VpnPayment.account_id.is_not(None),
                                               VpnPayment.status.in_(UNFINISHED) | short).order_by(VpnPayment.id)).all()
    ids = []
    for pay in rows:
        age, seen = now - _aware(pay.created_at), _aware(pay.checked_at)
        if seen is None or now - seen >= (RECHECK_SLOWLY_EVERY if age > RECHECK_SLOWLY_AFTER else RECHECK_EVERY):
            ids.append(pay.id)
    for pay_id in ids[:RECONCILE_LIMIT]:
        pay = db.get(VpnPayment, pay_id)
        try:
            _, credited = check_payment(db, pay, now)
            out["checked"] += 1
            if credited is not None:
                out["credited"] += 1
                out["accounts"].append(credited)
        except BillingError:
            out["errors"] += 1
            pay = db.get(VpnPayment, pay_id)
            pay.checked_at = now
        if pay.status in UNFINISHED and now - _aware(pay.created_at) > GIVE_UP_AFTER:
            pay.status, pay.note = "expired", "No payment seen after 14 days."
            out["gave_up"] += 1
        db.commit()
    return out


def credit_by_hand(db: Session, pay: VpnPayment, usd: float, reason: str, acct: VpnAccount | None = None, now: datetime | None = None) -> VpnAccount:
    """Admin: this payment really is worth `usd` in total. Repeating it adds nothing, since it credits only what the payment hasn't had yet."""
    if pay.status == "refunded":
        raise BillingError("That payment was refunded.", 409)
    target = acct or (db.get(VpnAccount, pay.account_id) if pay.account_id is not None else None)
    if target is None:
        raise BillingError("This payment has no account any more. Name the account, or paste the customer's key.", 400)
    if pay.account_id is not None and pay.account_id != target.id:
        raise BillingError("This payment belongs to another account.", 409)
    if not 0 < usd <= MAX_USD * 2:
        raise BillingError(f"Enter an amount up to ${MAX_USD * 2:.0f}.", 400)
    if usd <= pay.paid_usd:
        raise BillingError(f"This payment has already been credited ${pay.paid_usd:.2f}.", 409)
    pay.account_id = target.id
    _credit(db, target, pay, round(usd, 2), now or utcnow())
    pay.note = f"Credited by hand: {reason}"[:200]
    logger.info("vpn: payment %s credited by hand", pay.id)
    db.commit()
    return target


def refund_by_hand(db: Session, pay: VpnPayment, reason: str) -> None:
    """Admin has returned the money in NOWPayments: record it, and take the time back."""
    if pay.status == "refunded":
        raise BillingError("That payment is already recorded as refunded.", 409)
    _take_back(db.get(VpnAccount, pay.account_id) if pay.account_id is not None else None, pay)
    pay.note = f"Refunded: {reason}"[:200]
    logger.info("vpn: payment %s recorded as refunded", pay.id)
    db.commit()


def history(db: Session, acct: VpnAccount, limit: int = 50) -> list[dict[str, Any]]:
    """The account's own payments. The address shows only while one is awaited, so a reloaded page doesn't lose it. Attempts the provider never accepted charged nothing, so they aren't listed."""
    rows = db.scalars(select(VpnPayment).where(VpnPayment.account_id == acct.id, ~((VpnPayment.status == "failed") & VpnPayment.provider_ref.is_(None))).order_by(VpnPayment.id.desc()).limit(limit))
    return [{"id": p.id, "receipt": f"KV-{p.id:06d}", "created_at": p.created_at.isoformat(), "credited_at": p.credited_at.isoformat() if p.credited_at else None, "network_name": NETWORKS[p.network][1] if p.network in NETWORKS else p.provider,
             "amount_usd": p.amount_usd, "paid_usd": p.paid_usd, "days": round(p.seconds / 86400, 1), "status": p.status,
             "address": p.pay_address if p.status == "pending" else None, "amount": p.pay_amount if p.status == "pending" else None, "can_check": p.status in UNFINISHED and bool(p.provider_ref)} for p in rows]


# --- cards (Stripe) ----------------------------------------------------------------------------------------------------


def start_card(db: Session, acct: VpnAccount) -> str:
    """A hosted Stripe checkout for the monthly subscription: the card never reaches Price Chat. Passport accounts only. Off unless the owner's no-automatic-billing rule changes."""
    if not CARD_SUBSCRIPTIONS:
        raise BillingError("Card payments aren't available. Kiwi VPN is paid in USDT and renewed by hand.", 403)
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
    if not CARD_SUBSCRIPTIONS:
        raise BillingError("No card subscription on this account.", 404)
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
    if not secret or not CARD_SUBSCRIPTIONS:
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
    """Anonymous accounts: after SCRUB_AFTER a payment no longer points at its account or its blockchain payment. One still unfinished keeps its link until it's settled, so a slow payment can still be credited."""
    now = now or utcnow()
    anon = select(VpnAccount.id).where(VpnAccount.member_id.is_(None))
    rows = db.scalars(select(VpnPayment).where(VpnPayment.account_id.in_(anon), VpnPayment.created_at < now - SCRUB_AFTER, VpnPayment.status.not_in(UNFINISHED))).all()
    for p in rows:
        p.account_id, p.pay_address, p.provider_ref, p.pay_amount = None, None, None, None
    if rows:
        db.commit()
    return len(rows)


def totals(db: Session, now: datetime | None = None) -> dict[str, Any]:
    now = now or utcnow()
    revenue = db.scalar(select(func.coalesce(func.sum(VpnPayment.paid_usd), 0.0)).where(VpnPayment.credited_at >= now - timedelta(days=30)))
    return {"paying": int(db.scalar(select(func.count()).select_from(VpnAccount).where(VpnAccount.paid_until > now)) or 0),
            "anonymous": int(db.scalar(select(func.count()).select_from(VpnAccount).where(VpnAccount.key_hash.is_not(None))) or 0),
            "revenue_30d_usd": round(float(revenue or 0), 2), "usdt": _usdt_on(), "card": CARD_SUBSCRIPTIONS and _card_on(), "ipn": dict(ipn)}
