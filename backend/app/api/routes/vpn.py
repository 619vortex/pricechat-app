"""Kiwi VPN API.

Member routes live under /api/members/vpn... (public prefix; the member is whoever the pc_session cookie says, never an id in
the request). Anonymous routes live under /api/members/vpn/anon...: the account is whoever the X-Kiwi-Key header's key says, and
the key is never logged or stored, only its hash. Payment webhooks prove themselves with the provider's signature. Admin routes live under /api/vpn/admin/... (owner login: every non-public /api route). The gate and all node
calls are in app.vpn.service, not here. A device's config (with its private key) is returned once and never stored.
"""

import json
from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query, Request, Response, status
from pydantic import Field, field_validator

from app.api.deps import DbSession, Runtime
from app.api.routes.members import SessionToken, _require
from app.api.schemas import StrictInput
from app.members.desktop_scope import DESKTOP_TOKEN_PREFIX
from app.models.vpn import VpnAccount
from app.security import admin_auth, client_ip, rate_limit
from app.security.rate_limit import LIMITS
from app.vpn import billing, service, support, trial_guard, unified
from app.vpn.billing import BillingError
from app.vpn.service import VpnError

router = APIRouter(tags=["vpn"])


def _fail(e: VpnError | BillingError) -> HTTPException:
    headers = {}
    if getattr(e, "code", None):
        headers["X-Kiwi-Reason"] = str(e.code)  # type: ignore[union-attr]
    if getattr(e, "retry_after", None):
        headers["Retry-After"] = str(e.retry_after)  # type: ignore[union-attr]
    return HTTPException(e.status, str(e), headers=headers or None)


def _too_many(wait: int | None, message: str) -> None:
    if wait is not None:
        raise HTTPException(429, message, headers={"Retry-After": str(wait)})


def _via(pc_session: str | None) -> str:
    return "app" if (pc_session or "").startswith(DESKTOP_TOKEN_PREFIX) else "web"


def _preview(db: DbSession, request: Request) -> bool:
    """The owner's own browser, signed in to Admin: may test while the VPN is closed to everyone else."""
    return admin_auth.current(db, request.cookies.get(admin_auth.COOKIE)) is not None


def _open_or_preview(db: DbSession, request: Request) -> None:
    """Nobody buys or gets a key while the VPN is closed, except the owner testing it."""
    if not service.settings(db).enabled and not _preview(db, request):
        raise HTTPException(403, service.CLOSED)


def _payments_on(db: DbSession, request: Request, acct: VpnAccount | None = None) -> None:
    """Nobody starts a payment while payments are off, and while they're in test mode (VPN_PAYMENTS_PUBLIC unset) only the owner
    signed in to Admin and named testers can. Webhooks still credit an invoice from before they were turned off."""
    s = service.settings(db)
    if not s.payments_enabled:
        raise HTTPException(403, "Payments are off right now.")
    if not service.can_pay(s, acct, _preview(db, request)):
        raise HTTPException(403, "Payments aren't open yet.")


# --- member -------------------------------------------------------------------------------------------------------


@router.get("/members/vpn")
def my_vpn(request: Request, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    return service.overview(db, _require(db, pc_session), _preview(db, request))


@router.get("/members/vpn/stats", dependencies=[Depends(rate_limit.limit("earn"))])
def stats(db: DbSession) -> dict[str, Any]:
    """The page's statistics and server list: real figures only, nothing private about a server."""
    out = service.public_stats(db)
    db.commit()
    return out


@router.post("/members/vpn/trial")
def start_trial(request: Request, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    """Takes no input: eligibility comes from the account, Admin's switch, the trial guards (app/vpn/trial_guard.py) and the
    server's clock. Every check runs before the rate limit is charged: a refused start spends nobody's tokens (audit M1).
    A refusal says why in `detail`, with a machine-readable X-Kiwi-Reason header."""
    member = _require(db, pc_session)
    preview = _preview(db, request)

    def charge() -> None:
        _too_many(LIMITS["vpn_trial"].hit(str(member.id)), "Too many tries. Please wait a few minutes.")

    try:
        service.start_trial(db, member, preview, ip=trial_guard.real_ip(request), charge=charge)
    except VpnError as e:
        raise _fail(e) from e
    return service.overview(db, member, preview)


class DeviceInput(StrictInput):
    name: str = Field(min_length=1, max_length=32)
    server_id: int
    public_key: str | None = Field(default=None, min_length=44, max_length=44)  # the Kiwi VPN app makes its own key: the private half stays on the PC


@router.post("/members/vpn/devices", status_code=status.HTTP_201_CREATED)
def create_device(payload: DeviceInput, request: Request, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    """Only a genuinely new key is charged to the rate limit. A key this member already registered adds nothing: the same
    key, server and name again (a retry whose answer was lost) returns that device; anything else with it is refused (409),
    free either way. Replacing a device the member just deleted is charged to its own small bucket (rate_limit.device_charge)."""
    member = _require(db, pc_session)
    if payload.public_key is not None:
        mine = service.own_device_with_key(db, member, payload.public_key)
        db.commit()
        if mine is not None:
            if mine.server_id != payload.server_id or mine.name != " ".join(payload.name.split()):
                raise HTTPException(409, "That key is already in use.")
            try:
                return {"device": service.device_out(mine), "peer": service.device_peer(db, member, mine.id)}
            except VpnError as e:
                raise _fail(e) from e
    preview = _preview(db, request)
    try:
        service.device_precheck(db, member, payload.name, payload.server_id, preview, _via(pc_session), payload.public_key)  # refused for free: nothing charged (audit M1)
    except VpnError as e:
        raise _fail(e) from e
    _too_many(rate_limit.device_charge(str(member.id), payload.server_id, payload.name), "Too many new devices. Please wait a few minutes.")
    try:
        dev, config, filename = service.create_device(db, member, payload.name, payload.server_id, preview, payload.public_key, via=_via(pc_session))
        if payload.public_key is not None:
            return {"device": service.device_out(dev), "peer": service.device_peer(db, member, dev.id)}
    except VpnError as e:
        raise _fail(e) from e
    return {"device": service.device_out(dev), "config": config, "filename": filename}


@router.get("/members/vpn/devices/{device_id}/peer")
def device_peer(device_id: int, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    member = _require(db, pc_session)
    try:
        return service.device_peer(db, member, device_id)
    except VpnError as e:
        raise _fail(e) from e


@router.get("/members/vpn/v1/state", dependencies=[Depends(rate_limit.limit("earn"))])
def unified_state(db: DbSession, runtime: Runtime, platform: str | None = Query(None, max_length=16), version: str | None = Query(None, max_length=40), pc_session: SessionToken = None) -> dict[str, Any]:
    """One account, the same on every client: see app.vpn.unified. Writes no XP and starts no trial."""
    return unified.state(db, _require(db, pc_session), runtime.settings.members_public_url, platform, version)


@router.get("/members/vpn/v1/capabilities", dependencies=[Depends(rate_limit.limit("earn"))])
def unified_capabilities(db: DbSession, platform: str | None = Query(None, max_length=16), version: str | None = Query(None, max_length=40)) -> dict[str, bool]:
    """What a client of this platform and version may show, signed in or not. It grants nothing: every route checks the account for itself."""
    out = unified.capabilities(db, platform, version)
    db.commit()  # the first call may seed the program settings row
    return out


@router.get("/members/vpn/ip", dependencies=[Depends(rate_limit.limit("earn"))])
def my_ip(request: Request, response: Response) -> dict[str, Any]:
    """The address this request came from, so the Kiwi VPN app can check its tunnel without telling an outside service. It's only returned, never stored or logged here.
    forwarded_hops: how many X-Forwarded-For entries arrived (a count, no addresses), so the owner can see how many entries
    Railway's own proxies add after the caller and set TRUSTED_PROXY_HOPS from evidence (client_ip.py)."""
    response.headers["Cache-Control"] = "no-store"
    return {"ip": client_ip.client_ip(request), "forwarded_hops": client_ip.forwarded_count(request)}


@router.delete("/members/vpn/devices/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_device(device_id: int, db: DbSession, pc_session: SessionToken = None) -> Response:
    member = _require(db, pc_session)
    try:
        server_id, name = service.delete_device(db, member, device_id)
    except VpnError as e:
        raise _fail(e) from e
    rate_limit.FREED.add(str(member.id), server_id, name)  # a new device in its place soon is a replacement, not a new one
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class BrowserInput(StrictInput):
    server_id: int
    replace: str | None = Field(None, max_length=40)  # this browser's previous login, when it changes server


@router.post("/members/vpn/browser", status_code=status.HTTP_201_CREATED)
def create_browser(payload: BrowserInput, request: Request, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    """The browser extension's login for one server. Shown this once; only its hash is kept."""
    member = _require(db, pc_session)
    preview = _preview(db, request)
    try:
        service.browser_precheck(db, member, payload.server_id, preview)  # refused for free: nothing charged (audit M1)
    except VpnError as e:
        raise _fail(e) from e
    _too_many(LIMITS["vpn_browser"].hit(str(member.id)), "Too many new connections. Please wait a few minutes.")
    try:
        return service.create_browser(db, member, payload.server_id, payload.replace, preview)
    except VpnError as e:
        raise _fail(e) from e


@router.delete("/members/vpn/browser/{username}", status_code=status.HTTP_204_NO_CONTENT)
def delete_browser(username: str, db: DbSession, pc_session: SessionToken = None) -> Response:
    try:
        service.delete_browser(db, _require(db, pc_session), username)
    except VpnError as e:
        raise _fail(e) from e
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- support -------------------------------------------------------------------------------------------------------


class SupportInput(StrictInput):
    kind: Literal["bug", "feedback"]
    message: str = Field(min_length=1, max_length=support.MAX_MESSAGE)
    app_version: str | None = Field(default=None, max_length=32)
    diagnostics: str | dict[str, Any] | None = Field(default=None)  # the app's own diagnostics; redacted and cut to support.MAX_DIAGNOSTICS

    @field_validator("message")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Please write a message.")
        return v

    @field_validator("diagnostics")
    @classmethod
    def _bounded(cls, v: str | dict[str, Any] | None) -> str | dict[str, Any] | None:
        size = len(v) if isinstance(v, str) else len(json.dumps(v, default=str)) if v is not None else 0
        if size > support.MAX_DIAGNOSTICS * 4:  # a little over the limit is cut when stored; far over it is refused
            raise ValueError("Diagnostics are too large.")
        return v


@router.post("/members/vpn/support", status_code=status.HTTP_201_CREATED)
def send_support(payload: SupportInput, background: BackgroundTasks, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    """A bug report or feedback from the Kiwi VPN app (its desktop session may reach this) or the website. 5 an hour per member."""
    member = _require(db, pc_session)
    wait = LIMITS["vpn_support"].hit(str(member.id))
    if wait is not None:
        raise HTTPException(429, "You've sent several reports already. Please try again later.", headers={"Retry-After": str(wait)})
    source = "desktop" if (pc_session or "").startswith(DESKTOP_TOKEN_PREFIX) else "web"
    row = support.create(db, member, payload.kind, payload.message, payload.app_version, payload.diagnostics, source)
    background.add_task(support.notify, support.alert_text(member, row))
    return {"ok": True, "id": row.id}


# --- paying --------------------------------------------------------------------------------------------------------


class UsdtInput(StrictInput):
    amount_usd: float = Field(gt=0, le=100000)
    network: str = Field(min_length=1, max_length=16)
    # The checkout box: start my time as soon as the payment is confirmed, and I understand what that means for my 14-day right to
    # cancel (EU/UK consumer law; /vpn/refunds). No payment starts without it, and it's recorded with the payment (billing.record_start_now).
    start_now: bool = False


START_NOW_NEEDED = "Tick the box to start your Kiwi VPN time as soon as your payment is confirmed. It explains your right to cancel."


def _usdt(db: DbSession, acct: VpnAccount, payload: UsdtInput) -> dict[str, Any]:
    if service.access(db, acct) == "blocked":  # a blocked account, or a banned member: it couldn't use what it paid for
        raise HTTPException(403, "This account can't use Kiwi VPN, so it can't be charged.")
    if not payload.start_now:
        raise HTTPException(400, START_NOW_NEEDED, headers={"X-Kiwi-Reason": "start_now"})
    try:
        return billing.start_usdt(db, acct, payload.amount_usd, payload.network, start_now=True)
    except BillingError as e:
        raise _fail(e) from e


@router.get("/members/vpn/public")
def public_info(request: Request, db: DbSession) -> dict[str, Any]:
    """For a signed-out visitor: is it open, is it free, and how can they pay."""
    s = service.settings(db)
    preview = _preview(db, request)
    enabled, payments = s.enabled, service.can_pay(s, None, preview)  # payments' test mode: a signed-out visitor sees none
    db.commit()
    trial = (enabled or preview) and s.trial_enabled
    free = service.is_free(s)
    return {"enabled": enabled or preview, "test_mode": preview and not enabled, "payments": payments, "trial": trial, "free": free, "billing": billing.options() if payments else None}


@router.post("/members/vpn/usdt", dependencies=[Depends(rate_limit.limit("vpn_pay"))])
def member_usdt(payload: UsdtInput, request: Request, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    _open_or_preview(db, request)
    acct = service.account_of(db, _require(db, pc_session))
    _payments_on(db, request, acct)
    db.commit()
    return _usdt(db, acct, payload)


@router.post("/members/vpn/card", dependencies=[Depends(rate_limit.limit("vpn_pay"))])
def member_card(request: Request, db: DbSession, pc_session: SessionToken = None) -> dict[str, str]:
    """Off: billing.CARD_SUBSCRIPTIONS. Kiwi VPN is paid in USDT and renewed by hand."""
    _open_or_preview(db, request)
    acct = service.account_of(db, _require(db, pc_session))
    _payments_on(db, request, acct)
    db.commit()
    try:
        return {"url": billing.start_card(db, acct)}
    except BillingError as e:
        raise _fail(e) from e


@router.post("/members/vpn/card/portal", dependencies=[Depends(rate_limit.limit("vpn_pay"))])
def member_card_portal(db: DbSession, pc_session: SessionToken = None) -> dict[str, str]:
    try:
        return {"url": billing.card_portal(service.account_of(db, _require(db, pc_session)))}
    except BillingError as e:
        raise _fail(e) from e


@router.get("/members/vpn/payments", dependencies=[Depends(rate_limit.limit("earn"))])
def my_payments(db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    out = {"items": billing.history(db, service.account_of(db, _require(db, pc_session)))}
    db.commit()
    return out


@router.post("/members/vpn/payments/{payment_id}/check", dependencies=[Depends(rate_limit.limit("vpn_pay"))])
def check_my_payment(payment_id: int, db: DbSession, pc_session: SessionToken = None) -> dict[str, str]:
    try:
        return {"status": service.check_my_payment(db, service.account_of(db, _require(db, pc_session)), payment_id)}
    except VpnError as e:
        raise _fail(e) from e


@router.post("/members/vpn/webhooks/nowpayments")
async def nowpayments_webhook(request: Request, db: DbSession) -> dict[str, str]:
    raw = await request.body()
    try:
        status_, credited = billing.nowpayments_webhook(db, raw, request.headers.get("x-nowpayments-sig"))
    except BillingError as e:
        raise _fail(e) from e
    if credited is not None:
        service.activate(db, credited)
    return {"status": status_}


@router.post("/members/vpn/webhooks/stripe")
async def stripe_webhook(request: Request, db: DbSession) -> dict[str, str]:
    raw = await request.body()
    try:
        return {"status": billing.stripe_webhook(db, raw, request.headers.get("stripe-signature"))}
    except BillingError as e:
        raise _fail(e) from e


# --- anonymous key ---------------------------------------------------------------------------------------------------


def _anon(db: DbSession, x_kiwi_key: str | None = Header(None)) -> VpnAccount:
    acct = billing.by_key(db, x_kiwi_key)
    if acct is None:
        raise HTTPException(401, "That key isn't valid.")
    return acct


@router.post("/members/vpn/anon", status_code=status.HTTP_201_CREATED)
def anon_create(request: Request, db: DbSession) -> dict[str, str]:
    """A new key. Shown this once: there is no way to recover it. Only while paying: free access goes with a Passport, so one person can't take every slot on a server.
    The rate limit is charged only once a key would really be made (audit M1: refusals don't drain the shared bucket)."""
    _open_or_preview(db, request)
    if not service.can_pay(service.settings(db), None, _preview(db, request)):  # off, or in test mode for anyone but the owner
        raise HTTPException(403, "New keys are off while payments are off. Sign in with a Passport instead.")
    _too_many(rate_limit.retry_after("vpn_new", request), "Too many requests. Please slow down.")
    return {"key": billing.new_anonymous(db)[1]}


@router.get("/members/vpn/anon", dependencies=[Depends(rate_limit.limit("vpn_key"))])
def anon_view(request: Request, db: DbSession, acct: VpnAccount = Depends(_anon)) -> dict[str, Any]:
    return service.overview(db, acct, _preview(db, request))


@router.post("/members/vpn/anon/devices", status_code=status.HTTP_201_CREATED, dependencies=[Depends(rate_limit.limit("vpn_key"))])
def anon_device(payload: DeviceInput, request: Request, db: DbSession, acct: VpnAccount = Depends(_anon)) -> dict[str, Any]:
    """Checked first, then charged per key AND per client address (a real one only: website visitors behind Vercel's shared
    egress aren't keyed together), so many keys from one place can't drain the shared bucket either (audit M1)."""
    preview = _preview(db, request)
    try:
        service.device_precheck(db, acct, payload.name, payload.server_id, preview)
    except VpnError as e:
        raise _fail(e) from e
    ip = trial_guard.real_ip(request) if service.settings(db).trial_guard_mode == "enforce" else None  # monitor/off: no extra limit beyond main's
    if ip is not None:
        _too_many(LIMITS["vpn_anon_ip"].hit(client_ip.bucket(ip)), "Too many new devices. Please wait a few minutes.")
    _too_many(LIMITS["vpn_anon_device"].hit(f"a{acct.id}"), "Too many new devices. Please wait a few minutes.")
    try:
        dev, config, filename = service.create_device(db, acct, payload.name, payload.server_id, preview, via="anon")
    except VpnError as e:
        raise _fail(e) from e
    return {"device": service.device_out(dev), "config": config, "filename": filename}


@router.delete("/members/vpn/anon/devices/{device_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(rate_limit.limit("vpn_key"))])
def anon_device_delete(device_id: int, db: DbSession, acct: VpnAccount = Depends(_anon)) -> Response:
    try:
        service.delete_device(db, acct, device_id)
    except VpnError as e:
        raise _fail(e) from e
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/members/vpn/anon/usdt", dependencies=[Depends(rate_limit.limit("vpn_pay"))])
def anon_usdt(payload: UsdtInput, request: Request, db: DbSession, acct: VpnAccount = Depends(_anon)) -> dict[str, Any]:
    _open_or_preview(db, request)
    _payments_on(db, request, acct)
    return _usdt(db, acct, payload)


@router.get("/members/vpn/anon/payments", dependencies=[Depends(rate_limit.limit("vpn_key"))])
def anon_payments(db: DbSession, acct: VpnAccount = Depends(_anon)) -> dict[str, Any]:
    return {"items": billing.history(db, acct)}


@router.post("/members/vpn/anon/payments/{payment_id}/check", dependencies=[Depends(rate_limit.limit("vpn_pay"))])
def anon_check_payment(payment_id: int, db: DbSession, acct: VpnAccount = Depends(_anon)) -> dict[str, str]:
    try:
        return {"status": service.check_my_payment(db, acct, payment_id)}
    except VpnError as e:
        raise _fail(e) from e


# --- admin (owner login) ------------------------------------------------------------------------------------------


@router.get("/vpn/admin/overview")
def admin_overview(db: DbSession) -> dict[str, Any]:
    return service.admin_overview(db)


@router.get("/vpn/admin/health")
def admin_health(db: DbSession) -> dict[str, Any]:
    """Per-server uptime, latency, load and traffic projection, and the fleet's capacity (app/vpn/health.py)."""
    from app.vpn import health

    return health.report(db)


class SettingsUpdate(StrictInput):
    enabled: bool | None = None
    payments_enabled: bool | None = None
    trial_enabled: bool | None = None
    max_devices: int | None = Field(None, ge=1, le=service.MAX_DEVICES_LIMIT)
    monthly_cap_gb: int | None = Field(None, ge=0, le=service.MAX_CAP_GB)  # per member per month, 0 = unlimited
    # free-trial guards (app/vpn/trial_guard.py); each number 0 = that check off
    trial_guard_mode: Literal["off", "monitor", "enforce"] | None = None  # monitor: evaluated and recorded, never refused
    trial_one_per_identity: bool | None = None
    trial_min_account_age_hours: int | None = Field(None, ge=0, le=service.TRIAL_GUARDS["trial_min_account_age_hours"][1])
    trial_min_xp: int | None = Field(None, ge=0, le=service.TRIAL_GUARDS["trial_min_xp"][1])
    trial_per_ip_day: int | None = Field(None, ge=0, le=service.TRIAL_GUARDS["trial_per_ip_day"][1])
    trial_per_network_day: int | None = Field(None, ge=0, le=service.TRIAL_GUARDS["trial_per_network_day"][1])
    sync_enforce_limits: bool | None = None  # the sync switches off (never deletes) devices past the limit and non-testers on testers-only servers
    count_pc_once: bool | None = None  # the Windows app's devices of one PC count as one slot


@router.put("/vpn/admin/settings")
def admin_settings(payload: SettingsUpdate, db: DbSession) -> dict[str, Any]:
    if payload.payments_enabled and not billing.can_charge():  # USDT only: never an automatic charge
        raise HTTPException(400, "Add the NOWPayments keys first: there's no way to take a payment yet.")
    try:
        return service.update_settings(db, payload.model_dump(exclude_unset=True))
    except VpnError as e:
        raise _fail(e) from e


class TesterInput(StrictInput):
    username: str = Field(min_length=1, max_length=64)
    days: int = Field(ge=0, le=service.MAX_TESTER_DAYS)


@router.post("/vpn/admin/testers")
def admin_tester(payload: TesterInput, db: DbSession) -> dict[str, Any]:
    """Let one named member use the VPN while it's closed, until a date. Days 0 ends it. The public stays closed out."""
    try:
        return service.set_tester(db, payload.username, payload.days)
    except VpnError as e:
        raise _fail(e) from e


@router.get("/vpn/admin/support")
def admin_support(db: DbSession) -> dict[str, Any]:
    """Members' bug reports and feedback, newest first (already redacted when stored)."""
    return {"items": support.admin_list(db)}


class SupportDone(StrictInput):
    done: bool


@router.post("/vpn/admin/support/{report_id}/done")
def admin_support_done(report_id: int, payload: SupportDone, db: DbSession) -> dict[str, Any]:
    row = support.set_done(db, report_id, payload.done)
    if row is None:
        raise HTTPException(404, "Report not found.")
    return row


@router.get("/vpn/admin/customers")
def admin_customers(db: DbSession) -> dict[str, Any]:
    return {"items": service.admin_customers(db)}


@router.get("/vpn/admin/payments")
def admin_payments(db: DbSession) -> dict[str, Any]:
    return {"items": service.admin_payments(db)}


@router.post("/vpn/admin/payments/{payment_id}/check")
def admin_check_payment(payment_id: int, db: DbSession) -> dict[str, Any]:
    """Ask the provider about this payment now."""
    try:
        return service.admin_check_payment(db, payment_id)
    except VpnError as e:
        raise _fail(e) from e


class CreditInput(StrictInput):
    usd: float = Field(gt=0, le=1000)  # this payment's total worth, not an extra amount: repeating it adds nothing
    reason: str = Field(min_length=3, max_length=160)
    account_id: int | None = None  # only for a payment that has lost its account link
    key: str | None = Field(None, min_length=8, max_length=80)  # or the customer's anonymous key. It's hashed for the lookup, never stored


@router.post("/vpn/admin/payments/{payment_id}/credit")
def admin_credit_payment(payment_id: int, payload: CreditInput, db: DbSession) -> dict[str, Any]:
    """The money arrived and nothing automatic could credit it."""
    try:
        return service.admin_credit_payment(db, payment_id, payload.usd, payload.reason, payload.account_id, payload.key)
    except VpnError as e:
        raise _fail(e) from e


class RefundInput(StrictInput):
    reason: str = Field(min_length=3, max_length=160)
    usd: float | None = Field(default=None, ge=0, le=1000)  # what was actually sent back; default the most the policy allows
    kind: Literal["cancel", "other"] | None = None  # the 14-day right to cancel, or a fault on our side (/vpn/refunds)


@router.get("/vpn/admin/payments/{payment_id}/refund")
def admin_refund_quote(payment_id: int, db: DbSession) -> dict[str, Any]:
    """What the refund policy allows for this payment now: its unused days and their value. Changes nothing."""
    try:
        return service.admin_refund_quote(db, payment_id)
    except VpnError as e:
        raise _fail(e) from e


@router.post("/vpn/admin/payments/{payment_id}/refund")
def admin_refund_payment(payment_id: int, payload: RefundInput, db: DbSession) -> dict[str, Any]:
    """Records a refund already sent, and ends that payment's unused time. It doesn't move any money itself."""
    try:
        return service.admin_refund_payment(db, payment_id, payload.reason, payload.usd, payload.kind)
    except VpnError as e:
        raise _fail(e) from e


class BlockInput(StrictInput):
    blocked: bool


@router.post("/vpn/admin/accounts/{account_id}/block")
def admin_block(account_id: int, payload: BlockInput, db: DbSession) -> dict[str, Any]:
    try:
        return service.set_blocked(db, account_id, payload.blocked)
    except VpnError as e:
        raise _fail(e) from e


class BlockKeyInput(StrictInput):
    key: str = Field(min_length=8, max_length=80)


@router.post("/vpn/admin/block-key")
def admin_block_key(payload: BlockKeyInput, db: DbSession) -> dict[str, Any]:
    try:
        return service.block_key(db, payload.key)
    except VpnError as e:
        raise _fail(e) from e


@router.post("/vpn/admin/devices", status_code=status.HTTP_201_CREATED)
def admin_create_device(payload: DeviceInput, db: DbSession) -> dict[str, Any]:
    """A device made right here, free whatever the payment setting. The config comes back once, like any other device's."""
    acct = service.owner_account(db)
    db.commit()
    try:
        dev, config, filename = service.create_device(db, acct, payload.name, payload.server_id, preview=True, via="admin")
    except VpnError as e:
        raise _fail(e) from e
    return {"device": service.device_out(dev), "config": config, "filename": filename}


@router.delete("/vpn/admin/browsers/{browser_id}", status_code=status.HTTP_204_NO_CONTENT)
def admin_delete_browser(browser_id: int, db: DbSession) -> Response:
    try:
        service.admin_delete_browser(db, browser_id)
    except VpnError as e:
        raise _fail(e) from e
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/vpn/admin/devices/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
def admin_delete_device(device_id: int, db: DbSession) -> Response:
    try:
        service.admin_delete_device(db, device_id)
    except VpnError as e:
        raise _fail(e) from e
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class ServerInput(StrictInput):
    name: str = Field(min_length=1, max_length=48)
    region: str = Field("", max_length=48)
    country_code: str = Field(min_length=2, max_length=2)
    agent_url: str = Field(min_length=9, max_length=255)
    agent_token: str = Field(min_length=32, max_length=512)
    cert_sha256: str = Field(min_length=64, max_length=128)
    capacity: int | None = Field(None, ge=1, le=100000)
    testers_only: bool = True  # a new server starts visible to the owner and testers only, until it's switched to everyone
    traffic_included_gb: int | None = Field(None, ge=0, le=1_000_000)  # the provider's monthly allowance; alerts at 80% and 100%


class ServerUpdate(StrictInput):
    name: str | None = Field(None, min_length=1, max_length=48)
    region: str | None = Field(None, max_length=48)
    country_code: str | None = Field(None, min_length=2, max_length=2)
    capacity: int | None = Field(None, ge=1, le=100000)
    enabled: bool | None = None
    testers_only: bool | None = None
    traffic_included_gb: int | None = Field(None, ge=0, le=1_000_000)
    agent_token: str | None = Field(None, min_length=32, max_length=512)
    cert_sha256: str | None = Field(None, min_length=64, max_length=128)


def _server(db: DbSession, server_id: int) -> dict[str, Any]:
    return next(s for s in service.admin_overview(db)["servers"] if s["id"] == server_id)


@router.post("/vpn/admin/servers")
def admin_add_server(payload: ServerInput, db: DbSession) -> dict[str, Any]:
    try:
        server = service.add_server(db, payload.model_dump())
    except VpnError as e:
        raise _fail(e) from e
    return _server(db, server.id)


@router.patch("/vpn/admin/servers/{server_id}")
def admin_update_server(server_id: int, payload: ServerUpdate, db: DbSession) -> dict[str, Any]:
    try:
        service.update_server(db, server_id, payload.model_dump(exclude_unset=True))
    except VpnError as e:
        raise _fail(e) from e
    return _server(db, server_id)


@router.delete("/vpn/admin/servers/{server_id}")
def admin_delete_server(server_id: int, db: DbSession) -> dict[str, Any]:
    try:
        service.delete_server(db, server_id)
    except VpnError as e:
        raise _fail(e) from e
    return {"ok": True}


@router.post("/vpn/admin/servers/{server_id}/test")
def admin_test_server(server_id: int, db: DbSession) -> dict[str, Any]:
    try:
        return service.test_server(db, server_id)
    except VpnError as e:
        raise _fail(e) from e
