"""Kiwi VPN API.

Member routes live under /api/members/vpn... (public prefix; the member is whoever the pc_session cookie says, never an id in
the request). Anonymous routes live under /api/members/vpn/anon...: the account is whoever the X-Kiwi-Key header's key says, and
the key is never logged or stored, only its hash. Payment webhooks prove themselves with the provider's signature. Admin routes live under /api/vpn/admin/... (owner login: every non-public /api route). The gate and all node
calls are in app.vpn.service, not here. A device's config (with its private key) is returned once and never stored.
"""

from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from pydantic import Field

from app.api.deps import DbSession, Runtime
from app.api.routes.members import SessionToken, _require
from app.api.schemas import StrictInput
from app.models.vpn import VpnAccount
from app.security import admin_auth, client_ip, rate_limit
from app.security import rate_limit
from app.security.rate_limit import LIMITS
from app.vpn import billing, service, unified
from app.vpn.billing import BillingError
from app.vpn.service import VpnError

router = APIRouter(tags=["vpn"])


def _fail(e: VpnError | BillingError) -> HTTPException:
    return HTTPException(e.status, str(e))


def _preview(db: DbSession, request: Request) -> bool:
    """The owner's own browser, signed in to Admin: may test while the VPN is closed to everyone else."""
    return admin_auth.current(db, request.cookies.get(admin_auth.COOKIE)) is not None


def _open_or_preview(db: DbSession, request: Request) -> None:
    """Nobody buys or gets a key while the VPN is closed, except the owner testing it."""
    if not service.settings(db).enabled and not _preview(db, request):
        raise HTTPException(403, service.CLOSED)


def _payments_on(db: DbSession) -> None:
    """Nobody starts a payment while Kiwi VPN is free. Webhooks still credit an invoice from before it was turned off."""
    if not service.settings(db).payments_enabled:
        raise HTTPException(403, "Payments are off: Kiwi VPN is free right now.")


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
    """Takes no input: eligibility comes from the account, Admin's switch and the server's clock."""
    member = _require(db, pc_session)
    wait = LIMITS["vpn_device"].hit(str(member.id))
    if wait is not None:
        raise HTTPException(429, "Too many tries. Please wait a few minutes.", headers={"Retry-After": str(wait)})
    preview = _preview(db, request)
    try:
        service.start_trial(db, member, preview)
    except VpnError as e:
        raise _fail(e) from e
    return service.overview(db, member, preview)


class DeviceInput(StrictInput):
    name: str = Field(min_length=1, max_length=32)
    server_id: int
    public_key: str | None = Field(default=None, min_length=44, max_length=44)  # the Kiwi VPN app makes its own key: the private half stays on the PC


@router.post("/members/vpn/devices", status_code=status.HTTP_201_CREATED)
def create_device(payload: DeviceInput, request: Request, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    member = _require(db, pc_session)
    wait = LIMITS["vpn_device"].hit(str(member.id))
    if wait is not None:
        raise HTTPException(429, "Too many new devices. Please wait a few minutes.", headers={"Retry-After": str(wait)})
    try:
        dev, config, filename = service.create_device(db, member, payload.name, payload.server_id, _preview(db, request), payload.public_key)
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
def my_ip(request: Request, response: Response) -> dict[str, str]:
    """The address this request came from, so the Kiwi VPN app can check its tunnel without telling an outside service. It's only returned, never stored or logged here."""
    response.headers["Cache-Control"] = "no-store"
    return {"ip": client_ip.client_ip(request)}


@router.delete("/members/vpn/devices/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_device(device_id: int, db: DbSession, pc_session: SessionToken = None) -> Response:
    try:
        service.delete_device(db, _require(db, pc_session), device_id)
    except VpnError as e:
        raise _fail(e) from e
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class BrowserInput(StrictInput):
    server_id: int
    replace: str | None = Field(None, max_length=40)  # this browser's previous login, when it changes server


@router.post("/members/vpn/browser", status_code=status.HTTP_201_CREATED)
def create_browser(payload: BrowserInput, request: Request, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    """The browser extension's login for one server. Shown this once; only its hash is kept."""
    member = _require(db, pc_session)
    wait = LIMITS["vpn_device"].hit(str(member.id))
    if wait is not None:
        raise HTTPException(429, "Too many new connections. Please wait a few minutes.", headers={"Retry-After": str(wait)})
    try:
        return service.create_browser(db, member, payload.server_id, payload.replace, _preview(db, request))
    except VpnError as e:
        raise _fail(e) from e


@router.delete("/members/vpn/browser/{username}", status_code=status.HTTP_204_NO_CONTENT)
def delete_browser(username: str, db: DbSession, pc_session: SessionToken = None) -> Response:
    try:
        service.delete_browser(db, _require(db, pc_session), username)
    except VpnError as e:
        raise _fail(e) from e
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- paying --------------------------------------------------------------------------------------------------------


class UsdtInput(StrictInput):
    amount_usd: float = Field(gt=0, le=100000)
    network: str = Field(min_length=1, max_length=16)


def _usdt(db: DbSession, acct: VpnAccount, payload: UsdtInput) -> dict[str, Any]:
    try:
        return billing.start_usdt(db, acct, payload.amount_usd, payload.network)
    except BillingError as e:
        raise _fail(e) from e


@router.get("/members/vpn/public")
def public_info(request: Request, db: DbSession) -> dict[str, Any]:
    """For a signed-out visitor: is it open, is it free, and how can they pay."""
    s = service.settings(db)
    enabled, payments, preview = s.enabled, s.payments_enabled, _preview(db, request)
    db.commit()
    trial = (enabled or preview) and s.trial_enabled
    free = service.is_free(s)
    return {"enabled": enabled or preview, "test_mode": preview and not enabled, "payments": payments, "trial": trial, "free": free, "billing": billing.options() if payments else None}


@router.post("/members/vpn/usdt", dependencies=[Depends(rate_limit.limit("vpn_pay"))])
def member_usdt(payload: UsdtInput, request: Request, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    _open_or_preview(db, request)
    _payments_on(db)
    acct = service.account_of(db, _require(db, pc_session))
    db.commit()
    return _usdt(db, acct, payload)


@router.post("/members/vpn/card", dependencies=[Depends(rate_limit.limit("vpn_pay"))])
def member_card(request: Request, db: DbSession, pc_session: SessionToken = None) -> dict[str, str]:
    _open_or_preview(db, request)
    _payments_on(db)
    acct = service.account_of(db, _require(db, pc_session))
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


@router.post("/members/vpn/webhooks/nowpayments")
async def nowpayments_webhook(request: Request, db: DbSession) -> dict[str, str]:
    raw = await request.body()
    try:
        return {"status": billing.nowpayments_webhook(db, raw, request.headers.get("x-nowpayments-sig"))}
    except BillingError as e:
        raise _fail(e) from e


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


@router.post("/members/vpn/anon", status_code=status.HTTP_201_CREATED, dependencies=[Depends(rate_limit.limit("vpn_new"))])
def anon_create(request: Request, db: DbSession) -> dict[str, str]:
    """A new key. Shown this once: there is no way to recover it. Only while paying: free access goes with a Passport, so one person can't take every slot on a server."""
    _open_or_preview(db, request)
    if not service.settings(db).payments_enabled:
        raise HTTPException(403, "New keys are off while Kiwi VPN is free. Sign in with a Passport instead.")
    return {"key": billing.new_anonymous(db)[1]}


@router.get("/members/vpn/anon", dependencies=[Depends(rate_limit.limit("vpn_key"))])
def anon_view(request: Request, db: DbSession, acct: VpnAccount = Depends(_anon)) -> dict[str, Any]:
    return service.overview(db, acct, _preview(db, request))


@router.post("/members/vpn/anon/devices", status_code=status.HTTP_201_CREATED, dependencies=[Depends(rate_limit.limit("vpn_key"))])
def anon_device(payload: DeviceInput, request: Request, db: DbSession, acct: VpnAccount = Depends(_anon)) -> dict[str, Any]:
    wait = LIMITS["vpn_device"].hit(f"a{acct.id}")
    if wait is not None:
        raise HTTPException(429, "Too many new devices. Please wait a few minutes.", headers={"Retry-After": str(wait)})
    try:
        dev, config, filename = service.create_device(db, acct, payload.name, payload.server_id, _preview(db, request))
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
    _payments_on(db)
    return _usdt(db, acct, payload)


# --- admin (owner login) ------------------------------------------------------------------------------------------


@router.get("/vpn/admin/overview")
def admin_overview(db: DbSession) -> dict[str, Any]:
    return service.admin_overview(db)


class SettingsUpdate(StrictInput):
    enabled: bool | None = None
    payments_enabled: bool | None = None
    trial_enabled: bool | None = None
    max_devices: int | None = Field(None, ge=1, le=service.MAX_DEVICES_LIMIT)
    monthly_cap_gb: int | None = Field(None, ge=0, le=service.MAX_CAP_GB)  # per member per month, 0 = unlimited


@router.put("/vpn/admin/settings")
def admin_settings(payload: SettingsUpdate, db: DbSession) -> dict[str, Any]:
    if payload.payments_enabled and not billing.can_charge():  # USDT only: never an automatic charge
        raise HTTPException(400, "Add the NOWPayments or Stripe keys first: there's no way to take a payment yet.")
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


@router.get("/vpn/admin/customers")
def admin_customers(db: DbSession) -> dict[str, Any]:
    return {"items": service.admin_customers(db)}


@router.get("/vpn/admin/payments")
def admin_payments(db: DbSession) -> dict[str, Any]:
    return {"items": service.admin_payments(db)}


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
        dev, config, filename = service.create_device(db, acct, payload.name, payload.server_id, preview=True)
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


class ServerUpdate(StrictInput):
    name: str | None = Field(None, min_length=1, max_length=48)
    region: str | None = Field(None, max_length=48)
    country_code: str | None = Field(None, min_length=2, max_length=2)
    capacity: int | None = Field(None, ge=1, le=100000)
    enabled: bool | None = None
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
