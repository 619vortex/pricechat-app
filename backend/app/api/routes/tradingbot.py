"""VIP Trading Bot access funnel API.

Member routes live under /api/members/trading-bot (public prefix; the member is whoever the pc_session cookie says).
Admin routes live under /api/tradingbot/admin (the owner-login middleware guards every non-public /api route).
While the funnel is closed (Admin → Trading Bot Access → not public yet), only a signed-in owner can use it.
"""

import hashlib
from typing import Any, Literal
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from pydantic import Field

from app.api.deps import DbSession, Runtime
from app.api.routes.members import SessionToken, _current, _require
from app.api.schemas import StrictInput
from app.security import admin_auth
from app.tradingbot import service
from app.tradingbot.hyperliquid import InfoClient

router = APIRouter(tags=["trading-bot"])

# Swappable in tests (no network): the read-only Hyperliquid client and the Telegram bot client.
hyperliquid_client = InfoClient


def telegram_bot(runtime: Any) -> Any:
    from app.telegram.errors import TelegramError
    from app.telegram.service import make_client

    try:
        return make_client(runtime.settings, transport=runtime.telegram.transport)
    except (TelegramError, ValueError):
        return None


def _fail(e: Exception) -> HTTPException:
    if isinstance(e, PermissionError):
        return HTTPException(status.HTTP_403_FORBIDDEN, str(e))
    if isinstance(e, LookupError):
        return HTTPException(status.HTTP_404_NOT_FOUND, str(e))
    return HTTPException(status.HTTP_400_BAD_REQUEST, str(e))


def _is_admin(db: Any, request: Request) -> bool:
    return admin_auth.current(db, request.cookies.get(admin_auth.COOKIE)) is not None


def _visitor(request: Request) -> str:
    return hashlib.sha256(f"{request.client.host if request.client else ''}|{request.headers.get('user-agent', '')}".encode()).hexdigest()


def _open_or_admin(db: Any, request: Request) -> None:
    if not service.settings(db).public_enabled and not _is_admin(db, request):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "The VIP Trading Bot isn't open yet.")


# ---------------------------------------------------------------- member
@router.get("/members/trading-bot")
def get_state(request: Request, response: Response, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    member = _current(db, pc_session)
    w = service.wallet_of(db, member) if member else None
    if w is not None and (w.checked_at is None or service.utcnow() - service._aware(w.checked_at) > service.AUTO_RECHECK):
        service.run_check(db, member, w, hyperliquid_client())  # fresh numbers (and any new deposit tier) when Earn opens
    service.track(db, "view", member, visitor=None if member else _visitor(request))
    if member:
        service.track(db, "passport", member, once=True)
    out = service.state(db, member, admin=_is_admin(db, request))
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    return out


class EventIn(StrictInput):
    kind: Literal["view", "cta", "ref_click"]


@router.post("/members/trading-bot/event")
def event(payload: EventIn, request: Request, db: DbSession, pc_session: SessionToken = None) -> dict[str, bool]:
    """Clicks the browser reports (a referral click is only ever counted as a click, never as a signup)."""
    member = _current(db, pc_session)
    try:
        service.limit("event", f"m{member.id}" if member else _visitor(request))
    except service.AccessError:
        return {"ok": False}
    service.track(db, payload.kind, member, visitor=None if member else _visitor(request))
    db.commit()
    return {"ok": True}


class NonceIn(StrictInput):
    address: str = Field(min_length=42, max_length=42)


@router.post("/members/trading-bot/wallet/nonce")
def wallet_nonce(payload: NonceIn, request: Request, db: DbSession, runtime: Runtime, pc_session: SessionToken = None) -> dict[str, str]:
    member = _require(db, pc_session)
    _open_or_admin(db, request)
    host = urlparse(runtime.settings.members_public_url).netloc or "pricechat.live"
    try:
        return service.new_nonce(db, member, payload.address, host)
    except (service.AccessError, ValueError) as e:
        raise _fail(e) from e


class VerifyIn(StrictInput):
    nonce: str = Field(min_length=8, max_length=64)
    signature: str = Field(min_length=130, max_length=140)


@router.post("/members/trading-bot/wallet/verify")
def wallet_verify(payload: VerifyIn, request: Request, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    member = _require(db, pc_session)
    _open_or_admin(db, request)
    try:
        service.verify_wallet(db, member, payload.nonce, payload.signature, hyperliquid_client())
    except service.AccessError as e:
        raise _fail(e) from e
    return service.state(db, member, admin=_is_admin(db, request))


@router.post("/members/trading-bot/wallet/recheck")
def wallet_recheck(request: Request, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    member = _require(db, pc_session)
    _open_or_admin(db, request)
    try:
        service.recheck(db, member, hyperliquid_client())
    except service.AccessError as e:
        raise _fail(e) from e
    return service.state(db, member, admin=_is_admin(db, request))


@router.post("/members/trading-bot/apply")
def apply(request: Request, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    member = _require(db, pc_session)
    _open_or_admin(db, request)
    try:
        service.apply(db, member)
    except service.AccessError as e:
        raise _fail(e) from e
    return service.state(db, member, admin=_is_admin(db, request))


@router.post("/members/trading-bot/access/group")
async def access_group(request: Request, db: DbSession, runtime: Runtime, pc_session: SessionToken = None) -> dict[str, Any]:
    """Step 4a: Telegram confirms the member is in the bot group."""
    member = _require(db, pc_session)
    _open_or_admin(db, request)
    try:
        r = await service.check_group(db, member, telegram_bot(runtime))
    except service.AccessError as e:
        raise _fail(e) from e
    return {**r, "state": service.state(db, member, admin=_is_admin(db, request))}


@router.post("/members/trading-bot/access/bot")
def access_bot(request: Request, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    """Step 4b: Hyperliquid shows the trading bot connected (an approved API wallet)."""
    member = _require(db, pc_session)
    _open_or_admin(db, request)
    try:
        r = service.check_bot(db, member, hyperliquid_client())
    except service.AccessError as e:
        raise _fail(e) from e
    return {**r, "state": service.state(db, member, admin=_is_admin(db, request))}


@router.post("/members/trading-bot/hyperliquid/joined")
def hl_joined(request: Request, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    """Step 2: opened Hyperliquid with our link (once)."""
    member = _require(db, pc_session)
    _open_or_admin(db, request)
    return {**service.joined_hyperliquid(db, member), "state": service.state(db, member, admin=_is_admin(db, request))}


@router.post("/members/trading-bot/access/bot-confirm")
def bot_confirm(request: Request, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    """Step 3b: "I gave the bot my API" (checked against Hyperliquid when a wallet is known)."""
    member = _require(db, pc_session)
    _open_or_admin(db, request)
    ok, why = service.access_ready(db, member)
    if not ok:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, why or "Finish the steps above first.")
    return {**service.confirm_bot(db, member, hyperliquid_client()), "state": service.state(db, member, admin=_is_admin(db, request))}


@router.get("/members/trading-bot/signals")
def bot_signals(db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    """The bot's latest signals from the Telegram group (bot posts only)."""
    _require(db, pc_session)
    return service.signals(db)


class SignalIn(StrictInput):
    action: Literal["join", "skip", "auto"]


@router.post("/members/trading-bot/signals/{signal_id}")
def signal_act(signal_id: int, payload: SignalIn, request: Request, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    member = _require(db, pc_session)
    _open_or_admin(db, request)
    try:
        r = service.signal_action(db, member, signal_id, payload.action)
    except service.AccessError as e:
        raise _fail(e) from e
    return {**r, "state": service.state(db, member, admin=_is_admin(db, request))}


@router.get("/members/trading-bot/leaderboard")
def bot_leaderboard(db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    """Every member with a valid referral (Hyperliquid lists their verified wallet under our code), with their trades and
    PnL over 30 days, ranked by 30-day PnL. Hyperliquid's own numbers; a member it couldn't be read for is listed last."""
    from concurrent.futures import ThreadPoolExecutor

    me = _current(db, pc_session)
    people = service.leaderboard_members(db)[:200]
    client = hyperliquid_client()
    with ThreadPoolExecutor(max_workers=4) as pool:  # a few at a time: Hyperliquid rate-limits by IP
        pnls = list(pool.map(lambda mw: _pnl(mw[1].address, client), people))
        trades = list(pool.map(lambda mw: _trades(mw[1].address, client), people))
    rows = []
    for (m, _w), p, t in zip(people, pnls, trades, strict=True):
        p = p or {}
        rows.append({"username": m.username, "me": bool(me and me.id == m.id), "pnl_30d": (p.get("30d") or {}).get("pnl"),
                     "pnl_all": (p.get("all") or {}).get("pnl"),  # never the account balance: P&L only, on a public page
                     "trades_30d": t[0] if t else None, "trades_capped": bool(t and t[1])})
    rows.sort(key=lambda r: (r["pnl_30d"] is None, -(r["pnl_30d"] or 0), -(r["pnl_all"] or 0), -(r["trades_30d"] or 0)))
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return {"items": rows, "me": next((r for r in rows if r["me"]), None), "count": len(rows),
            "metric": "Members whose wallet Hyperliquid lists as referred with our code. Trades: orders filled in the last 30 days. "
                      "P&L: 30-day account P&L in USD from Hyperliquid's portfolio (realized + unrealized, after fees). Members can leave the leaderboard in their Passport settings."}


@router.get("/members/trading-bot/kitchen")
def kitchen_state(response: Response, db: DbSession) -> dict[str, Any]:
    """Kiwi Kitchen: the 15m/30m strategy's current state per market and its latest events (public market data, no accounts)."""
    from app.tradingos import kitchen

    response.headers["Cache-Control"] = "no-store"
    return kitchen.snapshot(db)


@router.get("/members/trading-bot/kitchen/stream")
async def kitchen_stream(request: Request, db: DbSession, pc_session: SessionToken = None, after: int | None = Query(None, ge=0),
                         once: bool = Query(False)) -> Any:
    """Server-Sent Events: every kitchen state change as the engine writes it (kb_events, source "kitchen"), from `after`
    (or the Last-Event-ID the browser resends on reconnect). Signed-in members only. Closes after 10 minutes; the browser
    reconnects by itself and loses nothing."""
    import asyncio
    import json
    import time

    from fastapi.responses import StreamingResponse
    from sqlalchemy import func, select
    from sqlalchemy.orm import sessionmaker

    from app.models.tradingos import KbEvent
    from app.tradingos import kitchen

    _require(db, pc_session)
    factory = sessionmaker(bind=db.get_bind(), autoflush=False, expire_on_commit=False)
    resend = request.headers.get("last-event-id", "")
    last = int(resend) if resend.isdigit() else after
    if last is None:  # a fresh connection starts from now (the snapshot already gave the current state)
        last = db.scalar(select(func.max(KbEvent.id)).where(KbEvent.source == kitchen.SOURCE)) or 0

    def read(after_id: int) -> list[dict[str, Any]]:
        with factory() as s:
            return kitchen.events_after(s, after_id)

    async def events() -> Any:
        nonlocal last
        yield "retry: 5000\n\n"
        started = beat = time.monotonic()
        while True:
            for e in await asyncio.to_thread(read, last):
                last = e["id"]
                yield f"id: {e['id']}\nevent: kitchen\ndata: {json.dumps(e, default=str)}\n\n"
            if once or time.monotonic() - started > 600 or await request.is_disconnected():
                return
            if time.monotonic() - beat > 15:
                beat = time.monotonic()
                yield ": ping\n\n"
            await asyncio.sleep(2)

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@router.post("/members/trading-bot/invite")
async def invite(request: Request, response: Response, db: DbSession, runtime: Runtime, pc_session: SessionToken = None) -> dict[str, Any]:
    """The approved member's own join-request link. POST + no-store: never cached, never in page HTML."""
    member = _require(db, pc_session)
    response.headers["Cache-Control"] = "no-store"
    try:
        return await service.invite(db, member, telegram_bot(runtime))
    except (service.AccessError, PermissionError) as e:
        raise _fail(e) from e


@router.get("/members/me/wallet")
def my_wallet(db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    """The Passport's wallet row: the verified wallet (if any) and whether its free level is still to claim."""
    from sqlalchemy import select

    from app.models.members import ReputationEvent

    member = _require(db, pc_session)
    claimed = db.scalar(select(ReputationEvent.id).where(ReputationEvent.member_id == member.id, ReputationEvent.event_type == "wallet.verified")) is not None
    return {"wallet": service.wallet_out(service.wallet_of(db, member)), "free_level_claimed": claimed}


@router.post("/members/me/wallet/nonce")
def my_wallet_nonce(payload: NonceIn, db: DbSession, runtime: Runtime, pc_session: SessionToken = None) -> dict[str, str]:
    """Link a wallet from the Passport (open to every member, whether or not the Earn funnel is open)."""
    member = _require(db, pc_session)
    host = urlparse(runtime.settings.members_public_url).netloc or "pricechat.live"
    try:
        return service.new_nonce(db, member, payload.address, host)
    except (service.AccessError, ValueError) as e:
        raise _fail(e) from e


@router.post("/members/me/wallet/verify")
def my_wallet_verify(payload: VerifyIn, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    member = _require(db, pc_session)
    before = member.rank_level
    try:
        service.verify_wallet(db, member, payload.nonce, payload.signature, hyperliquid_client())
    except service.AccessError as e:
        raise _fail(e) from e
    db.refresh(member)
    return {**my_wallet(db, pc_session), "leveled_up": member.rank_level > before, "rank_level": member.rank_level}


_pnl_cache: dict[str, tuple[float, dict[str, Any] | None]] = {}


def _pnl(address: str, client: Any = None) -> dict[str, Any] | None:
    """Hyperliquid's PnL for an address (24h / 7d / 30d / all), cached 5 minutes. None when Hyperliquid can't be reached."""
    import time

    from app.tradingbot.hyperliquid import HyperliquidError, pnl_summary

    hit = _pnl_cache.get(address)
    if hit and time.monotonic() - hit[0] < 300:
        return hit[1]
    try:
        out: dict[str, Any] | None = pnl_summary((client or hyperliquid_client()).portfolio(address))
    except HyperliquidError:
        out = None
    _pnl_cache[address] = (time.monotonic(), out)
    return out


_trades_cache: dict[str, tuple[float, tuple[int, bool] | None]] = {}


def _trades(address: str, client: Any = None) -> tuple[int, bool] | None:
    """Orders the address filled on Hyperliquid in the last 30 days, and whether that is a floor (too many fills to read),
    cached 5 minutes. None when Hyperliquid can't be reached."""
    import time

    from app.tradingbot.hyperliquid import HyperliquidError, trade_count

    hit = _trades_cache.get(address)
    if hit and time.monotonic() - hit[0] < 300:
        return hit[1]
    try:
        fills, capped = (client or hyperliquid_client()).fills(address, int(time.time() * 1000) - 30 * 86_400_000)
        out: tuple[int, bool] | None = (trade_count(fills), capped)
    except HyperliquidError:
        out = None
    _trades_cache[address] = (time.monotonic(), out)
    return out


@router.get("/members/trading-bot/track-record")
def bot_track_record(db: DbSession) -> dict[str, Any]:
    """The bot's public track record: its Hyperliquid account's PnL, straight from Hyperliquid (transparency)."""
    s = service.settings(db)
    if not s.bot_wallet:
        return {"configured": False}
    return {"configured": True, "address": service.evm.checksum(s.bot_wallet), "bot": s.bot_username, "pnl": _pnl(s.bot_wallet)}


@router.get("/members/me/pnl")
def my_pnl(db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    """The member's own Hyperliquid PnL (their verified wallet) and the bot's, for the Passport's track record."""
    member = _require(db, pc_session)
    w = service.wallet_of(db, member)
    s = service.settings(db)
    return {"wallet": service.evm.checksum(w.address) if w else None, "mine": _pnl(w.address) if w else None,
            "bot": {"username": s.bot_username, "address": service.evm.checksum(s.bot_wallet), "pnl": _pnl(s.bot_wallet)} if s.bot_wallet else None,
            "tiers": service.tiers_out(w)}


# ---------------------------------------------------------------- sign in with Ethereum (a Kiwi Passport login)
@router.post("/members/auth/wallet/nonce")
def wallet_login_nonce(payload: NonceIn, request: Request, db: DbSession, runtime: Runtime) -> dict[str, str]:
    host = urlparse(runtime.settings.members_public_url).netloc or "pricechat.live"
    try:
        return service.login_nonce(db, payload.address, host, _visitor(request))
    except (service.AccessError, ValueError) as e:
        raise _fail(e) from e


@router.post("/members/auth/wallet/verify")
def wallet_login_verify(payload: VerifyIn, request: Request, response: Response, db: DbSession, runtime: Runtime) -> dict[str, Any]:
    """Signs the wallet's owner in (a new Passport if this wallet has none yet), with the usual session cookie."""
    from app.api.routes.members import _set_session
    from app.members import service as members

    try:
        member, created = service.login_verify(db, payload.nonce, payload.signature, _visitor(request))
    except service.AccessError as e:
        raise _fail(e) from e
    _set_session(response, runtime.settings, members.new_session(db, member, runtime.settings.member_session_days))
    return {"signed_in": True, "created": created, "username": member.username}


# ---------------------------------------------------------------- admin (behind the owner login)
def _actor(db: Any, request: Request) -> str:
    s = admin_auth.current(db, request.cookies.get(admin_auth.COOKIE))
    return f"admin:{s.username}" if s else "admin"


@router.get("/tradingbot/admin/overview")
def admin_overview(db: DbSession, days: int = Query(30, ge=1, le=365)) -> dict[str, Any]:
    out = {"stats": service.stats(db, days), "settings": service.settings_out(service.settings(db))}
    db.commit()
    return out


class SettingsIn(StrictInput):
    public_enabled: bool | None = None
    min_rank_level: int | None = Field(None, ge=1, le=50)
    min_deposit_usd: float | None = Field(None, ge=0, le=1_000_000)
    referral_code: str | None = Field(None, max_length=64)
    referral_url: str | None = Field(None, max_length=200)
    telegram_group_id: str | None = Field(None, max_length=64)
    auto_verify: bool | None = None
    manual_approval: bool | None = None
    applications_per_day: int | None = Field(None, ge=1, le=10_000)
    invite_ttl_hours: int | None = Field(None, ge=1, le=720)
    review_rules: str | None = Field(None, max_length=4000)
    bot_username: str | None = Field(None, max_length=64)
    bot_wallet: str | None = Field(None, max_length=42)


@router.put("/tradingbot/admin/settings")
def admin_settings(payload: SettingsIn, request: Request, db: DbSession) -> dict[str, Any]:
    try:
        s = service.update_settings(db, _actor(db, request), payload.model_dump(exclude_none=True))
    except service.AccessError as e:
        raise _fail(e) from e
    return service.settings_out(s)


@router.get("/tradingbot/admin/applications")
def admin_applications(db: DbSession, status_: str | None = Query(None, alias="status")) -> dict[str, Any]:
    return {"items": service.queue(db, status_ if status_ in service.APP_STATUSES else None)}


class DecisionIn(StrictInput):
    notes: str = Field("", max_length=2000)


@router.post("/tradingbot/admin/applications/{public_id}/{decision}")
async def admin_decide(public_id: str, decision: Literal["approve", "reject", "revoke"], payload: DecisionIn, request: Request,
                       db: DbSession, runtime: Runtime) -> dict[str, Any]:
    try:
        a = service.review(db, public_id, decision, _actor(db, request), payload.notes)
    except (service.AccessError, LookupError) as e:
        raise _fail(e) from e
    if decision == "revoke":
        await service.revoke_access(db, a, telegram_bot(runtime))
    return {"items": service.queue(db)}


@router.post("/tradingbot/admin/applications/{public_id}/recheck")
def admin_recheck(public_id: str, request: Request, db: DbSession) -> dict[str, Any]:
    """Read the applicant's wallet from Hyperliquid again (e.g. before deciding a manual review)."""
    from sqlalchemy import select

    from app.models.members import Member
    from app.models.tradingbot import TbApplication

    a = db.scalar(select(TbApplication).where(TbApplication.public_id == public_id))
    if a is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such application.")
    member = db.get(Member, a.member_id)
    w = service.wallet_of(db, member) if member else None
    if w is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The applicant no longer has a verified wallet.")
    service.run_check(db, member, w, hyperliquid_client())
    a.evidence = {**a.evidence, "recheck": {"at": w.checked_at.isoformat() if w.checked_at else None, "wallet": service.wallet_out(w),
                                            "hyperliquid": w.evidence}}
    service.audit(db, a, None, _actor(db, request), "rechecked", {"referral": w.referral_status, "deposit": w.deposit_status})
    db.commit()
    return {"items": service.queue(db)}


# ---------------------------------------------------------------- Hyperliquid affiliate wallet (owner only: /api/tradingbot/admin is private)
_aff: dict[str, Any] = {}


@router.get("/tradingbot/admin/affiliate")
async def admin_affiliate(db: DbSession, refresh: bool = Query(False)) -> dict[str, Any]:
    """Our referrer wallet on Hyperliquid, every referred account (live balances) and the Passport member each one is."""
    import asyncio
    import time as _t

    from app.tradingbot import affiliate
    from app.tradingos import market

    hit = _aff.get("r")
    if hit and not refresh and _t.monotonic() - hit[0] < 60:
        return hit[1]
    try:
        out = await asyncio.to_thread(affiliate.report, db, market.data()._post)
    except market.MarketError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Hyperliquid couldn't be read: {e}") from e
    out["history"] = affiliate.history(db)
    _aff["r"] = (_t.monotonic(), out)
    return out


@router.post("/tradingbot/admin/affiliate/snapshot")
async def admin_affiliate_snapshot(db: DbSession) -> dict[str, Any]:
    import asyncio

    from app.tradingbot import affiliate
    from app.tradingos import market

    try:
        row = await asyncio.to_thread(affiliate.snapshot, db, market.data()._post)
    except market.MarketError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Hyperliquid couldn't be read: {e}") from e
    _aff.clear()
    return {"ok": True, "at": row.created_at.isoformat()}
