"""Trade execution API.

Members (/api/members/trading/..., the pc_session member, Earn's Kiwi Rank (Scout Kiwi, 4) for trading): their account, limits, AUTO,
STOP NEW TRADES, API-wallet authorization (their own wallet signs; no key is ever typed anywhere), ENTER preview →
confirm, closing, and the pre-live checklist. Admin (/api/tradingos/admin/execution..., owner login): accounts, positions,
orders, the LIVE allowlist, health, and a per-account STOP.
"""

import asyncio
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from pydantic import Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.api.deps import DbSession, Runtime
from app.api.routes.members import SessionToken, _require
from app.api.routes.tradingos import _actor
from app.api.schemas import StrictInput
from app.models.members import Member
from app.models.trading import TrAccount, TrOrder, TrPosition
from app.models.tradingos import TsSignal, TsSignalAction
from app.tradingos import accounts, execution, market, signals, terminal

router = APIRouter(tags=["trading"])
Network = Literal["TESTNET", "LIVE"]


def _fail(e: Exception) -> HTTPException:
    return HTTPException(status.HTTP_400_BAD_REQUEST, str(e))


def _member(db: Any, pc_session: str | None) -> Member:
    return _require(db, pc_session)


# ---------------------------------------------------------------- members
@router.get("/members/trading/account")
def get_account(response: Response, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    return accounts.state(db, _member(db, pc_session))


class SettingsIn(StrictInput):
    mode: Literal["PAPER", "TESTNET", "LIVE"] | None = None
    max_leverage: int | None = None
    max_loss_per_trade_usd: float | None = None
    max_allocation_pct: float | None = None
    max_positions: int | None = None
    daily_loss_limit_usd: float | None = None
    allowed_coins: str | None = Field(None, max_length=500)
    paused: bool | None = None
    ack_protection: bool | None = None
    auto_enabled: bool | None = None
    auto_hours: int | None = None
    timeframe: Literal["1h", "15m", "both"] | None = None
    frequency: Literal["standard", "active"] | None = None
    max_trades_per_day: int | None = None
    cooldown_minutes: int | None = None
    intraday_live: bool | None = None
    intraday_phrase: str | None = Field(None, max_length=40)


@router.put("/members/trading/settings")
def put_settings(payload: SettingsIn, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    m = _member(db, pc_session)
    try:
        accounts.update(db, m, payload.model_dump(exclude_none=True))
    except accounts.AccountError as e:
        db.rollback()
        raise _fail(e) from e
    return accounts.state(db, m)


class AgentStartIn(StrictInput):
    network: Network
    chain_id: str = Field(pattern=r"^0x[0-9a-fA-F]{1,16}$")


@router.post("/members/trading/agent/start")
def agent_start(payload: AgentStartIn, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    m = _member(db, pc_session)
    try:
        return accounts.agent_start(db, m, payload.network, payload.chain_id.lower())
    except accounts.AccountError as e:
        raise _fail(e) from e


class AgentFinishIn(StrictInput):
    network: Network
    chain_id: str = Field(pattern=r"^0x[0-9a-fA-F]{1,16}$")
    nonce: int
    signature: str = Field(min_length=130, max_length=140)


@router.post("/members/trading/agent/finish")
async def agent_finish(payload: AgentFinishIn, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    m = _member(db, pc_session)
    try:
        await asyncio.to_thread(accounts.agent_finish, db, m, payload.network, payload.nonce, payload.chain_id.lower(), payload.signature)
    except accounts.AccountError as e:
        raise _fail(e) from e
    return accounts.state(db, m)


class NetworkIn(StrictInput):
    network: Network


@router.post("/members/trading/agent/remove")
def agent_remove(payload: NetworkIn, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    m = _member(db, pc_session)
    try:
        accounts.revoke_local(db, m, payload.network)
    except accounts.AccountError as e:
        raise _fail(e) from e
    return accounts.state(db, m)


class PreviewIn(StrictInput):
    signal_id: int


@router.post("/members/trading/preview")
async def preview(payload: PreviewIn, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    m = _member(db, pc_session)
    sig = db.get(TsSignal, payload.signal_id)
    if sig is None or sig.published_at is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such signal.")
    try:
        it = await asyncio.to_thread(execution.preview, db, m, sig)
    except (execution.TradeError, market.MarketError) as e:
        raise _fail(e) from e
    return {"token": it.token, "mode": it.mode, "plan": it.plan, "text": execution.preview_text(sig, it), "expires_at": it.expires_at.isoformat()}


class TokenIn(StrictInput):
    token: str = Field(min_length=8, max_length=32)


@router.post("/members/trading/confirm")
async def confirm(payload: TokenIn, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    m = _member(db, pc_session)
    try:
        pos = await asyncio.to_thread(execution.confirm, db, m, payload.token)
    except (execution.TradeError, market.MarketError) as e:
        raise _fail(e) from e
    return {"position": accounts.pos_out(pos), "account": accounts.state(db, m)}


@router.post("/members/trading/cancel")
def cancel(payload: TokenIn, db: DbSession, pc_session: SessionToken = None) -> dict[str, bool]:
    execution.cancel_intent(db, _member(db, pc_session), payload.token)
    return {"ok": True}


@router.get("/members/trading/terminal")
async def get_terminal(response: Response, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    """The connected trader's terminal: account, overview (exchange or paper, labelled), positions, history, my signal status."""
    response.headers["Cache-Control"] = "no-store"
    m = _member(db, pc_session)
    out = await asyncio.to_thread(terminal.terminal, db, m)
    return out | {"account": accounts.state(db, m), "global_paused": signals.settings(db).paused}


class CloseIn(StrictInput):
    fraction: Literal[0.25, 0.5, 1.0] = 1.0


@router.post("/members/trading/positions/{pos_id}/close")
async def close(pos_id: int, db: DbSession, pc_session: SessionToken = None, payload: CloseIn | None = None) -> dict[str, Any]:
    """Reduce-only close of 25 / 50 / 100% of the member's own bot position; answers with the exchange's actual fill."""
    m = _member(db, pc_session)
    fraction = payload.fraction if payload else 1.0
    try:
        pos = await asyncio.to_thread(execution.close_position, db, m, pos_id, None, fraction)
    except (execution.TradeError, market.MarketError) as e:
        db.rollback()
        raise _fail(e) from e
    fill = db.scalar(select(TrOrder).where(TrOrder.position_id == pos.id, TrOrder.kind == "close").order_by(TrOrder.id.desc()).limit(1))
    return accounts.state(db, m) | {"close": {"position": pos.id, "coin": pos.coin, "status": pos.status, "remaining": pos.size,
                                              "filled": fill.filled_size if fill else None, "avg_px": fill.avg_px if fill else None,
                                              "order_status": fill.status if fill else None, "note": pos.error}}


class CloseAllIn(StrictInput):
    confirm_count: int = Field(ge=1, le=10)


@router.post("/members/trading/positions/close-all")
async def close_all(payload: CloseAllIn, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    """Emergency: close every bot position (reduce-only). The count must match what the member saw when confirming."""
    m = _member(db, pc_session)
    acc = execution.account(db, m)
    n = db.scalar(select(func.count()).select_from(TrPosition).where(TrPosition.account_id == acc.id,
                                                                     TrPosition.status.in_(execution.CLOSABLE), TrPosition.size > 0)) or 0
    if n != payload.confirm_count:
        raise HTTPException(status.HTTP_409_CONFLICT, f"You have {n} open bot positions now, not {payload.confirm_count}. Review and confirm again.")
    out = await asyncio.to_thread(execution.close_all, db, m)
    return accounts.state(db, m) | {"close_all": out}


class ProtectionIn(StrictInput):
    stop: float | None = Field(None, gt=0)
    tp1: float | None = Field(None, gt=0)
    tp2: float | None = Field(None, gt=0)
    dry_run: bool = True


@router.post("/members/trading/positions/{pos_id}/protection")
async def protection(pos_id: int, payload: ProtectionIn, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    """Move the stop (only tighter) or a take-profit. dry_run=true validates and describes; false sends it."""
    m = _member(db, pc_session)
    try:
        out = await asyncio.to_thread(execution.adjust_protection, db, m, pos_id, payload.stop, payload.tp1, payload.tp2, payload.dry_run)
    except (execution.TradeError, market.MarketError) as e:
        db.rollback()
        raise _fail(e) from e
    return {"result": out} | ({} if payload.dry_run else {"account": accounts.state(db, m)})


@router.post("/members/trading/signals/{sid}/reject")
def reject_signal(sid: int, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    """REJECT from the website: the same record as the Telegram button (nothing is traded)."""
    m = _member(db, pc_session)
    sig = db.get(TsSignal, sid)
    if sig is None or sig.published_at is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such signal.")
    if db.scalar(select(TrPosition.id).where(TrPosition.account_id == execution.account(db, m).id, TrPosition.signal_id == sid,
                                             TrPosition.status.in_(("OPENING", "OPEN", "PROTECTION_FAILED")))):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You're in this signal: use CLOSE POSITION instead.")
    try:
        with db.begin_nested():
            db.add(TsSignalAction(signal_id=sid, who=f"m:{m.id}", member_id=m.id, action="reject", verified=True))
    except IntegrityError:
        pass  # already answered from here
    signals.audit(db, f"m:{m.id}", "reject", sig, via="website")
    db.commit()
    return {"ok": True}


@router.post("/members/trading/live/checks")
async def live_checks(db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    m = _member(db, pc_session)
    if not execution.live_allowed(db, m):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "LIVE trading isn't available for this account.")
    await asyncio.to_thread(accounts.live_checks, db, m)
    return accounts.state(db, m)


class EnableIn(StrictInput):
    phrase: str = Field(max_length=32)


@router.post("/members/trading/live/enable")
def live_enable(payload: EnableIn, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    m = _member(db, pc_session)
    try:
        accounts.enable_live(db, m, payload.phrase)
    except accounts.AccountError as e:
        db.rollback()
        raise _fail(e) from e
    return accounts.state(db, m)


@router.post("/members/trading/live/disable")
def live_disable(db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    m = _member(db, pc_session)
    accounts.disable_live(db, m)
    return accounts.state(db, m)


# ---------------------------------------------------------------- Kiwi Brain (members): the decision timeline + live market numbers
@router.get("/members/kiwi-brain/events")
def kiwi_brain_events(response: Response, db: DbSession, pc_session: SessionToken = None, limit: int = Query(100, ge=1, le=500),
                      before_id: int | None = Query(None, ge=1), kind: str | None = Query(None, max_length=120),
                      signal_id: int | None = Query(None, ge=1)) -> dict[str, Any]:
    """The public events (market, strategy, signal) and THIS member's own (risk, orders, exchange answers, positions). Never
    another member's."""
    from app.tradingos import brain

    response.headers["Cache-Control"] = "no-store"
    m = _member(db, pc_session)
    kinds = [k for k in (kind or "").split(",") if k in brain.KINDS] or None
    rows = brain.events_for(db, m.id, limit=limit, before_id=before_id, kinds=kinds, signal_id=signal_id)
    return {"items": [brain.event_out(e) for e in rows]}


_snap_cache: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}


@router.get("/members/kiwi-brain/market")
async def kiwi_brain_market(db: DbSession, pc_session: SessionToken = None, coin: str = Query("BTC", min_length=1, max_length=20),
                            timeframe: str = Query("1h", pattern=r"^(15m|1h|4h)$")) -> dict[str, Any]:
    """Live Hyperliquid numbers and indicators for one market, every group with its source and timestamp (cached 15 s)."""
    import time as _time

    from app.tradingos import brain

    _member(db, pc_session)
    key = (coin.upper(), timeframe)
    hit = _snap_cache.get(key)
    if hit and _time.monotonic() - hit[0] < 15:
        return hit[1]
    try:
        snap = brain.jsonable(await asyncio.to_thread(brain.market_snapshot, market.data(), coin, timeframe))
    except market.MarketError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Hyperliquid data unavailable: {e}") from e
    _snap_cache[key] = (_time.monotonic(), snap)
    return snap


@router.get("/members/kiwi-brain/status")
def kiwi_brain_status(response: Response, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    """This member's Kiwi: mode, state, strategies, last data update, next evaluation, and paper / live results (separate)."""
    from app.tradingos import brain

    response.headers["Cache-Control"] = "no-store"
    out = brain.status(db, _member(db, pc_session))
    db.commit()
    return out


@router.get("/members/kiwi-brain/candles")
async def kiwi_brain_candles(db: DbSession, pc_session: SessionToken = None, coin: str = Query("BTC", min_length=1, max_length=20),
                             timeframe: str = Query("1h", pattern=r"^(5m|15m|1h|4h)$"), n: int = Query(120, ge=30, le=500)) -> dict[str, Any]:
    """Closed Hyperliquid candles for the chart, with the published signals on that market as markers."""
    from app.tradingos import brain

    _member(db, pc_session)
    try:
        return await asyncio.to_thread(brain.candles, market.data(), db, coin, timeframe, n)
    except market.MarketError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Hyperliquid data unavailable: {e}") from e


# ---------------------------------------------------------------- Connect Hyperliquid (website): link → referral → bot trading → connected
@router.get("/members/trading/connection")
async def connection(db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    m = _member(db, pc_session)
    return await asyncio.to_thread(accounts.connection, db, m)


@router.post("/members/trading/connection/recheck")
async def connection_recheck(db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    from app.api.routes.tradingbot import hyperliquid_client

    m = _member(db, pc_session)
    try:
        return await asyncio.to_thread(accounts.recheck_referral, db, m, hyperliquid_client())
    except accounts.AccountError as e:
        raise _fail(e) from e


class ActivateIn(StrictInput):
    consent: bool


class ApiKeyIn(StrictInput):
    address: str | None = Field(None, max_length=44)  # optional: Hyperliquid tells us the account from the key
    secret: str = Field(min_length=64, max_length=68)  # the API wallet's private key from app.hyperliquid.xyz/API (never echoed)
    consent: bool = False  # False (the default): connected in paper; live trading is a separate, explicit step
    replace: bool = False  # the member confirmed switching their Passport to the key's Hyperliquid account


@router.post("/members/trading/connection/api-key")
async def connection_api_key(payload: ApiKeyIn, runtime: Runtime, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    """CONNECT HYPERLIQUID with an API key created on Hyperliquid: proven by Hyperliquid listing it for that address. No wallet here.
    The account stays in paper until live trading is enabled explicitly; the member's Telegram bot says it's ready."""
    from app.api.routes.tradingbot import hyperliquid_client

    m = _member(db, pc_session)
    try:
        out = await asyncio.to_thread(accounts.connect_api_key, db, m, payload.address, payload.secret, payload.consent, hyperliquid_client(),
                                      None, payload.replace)
    except accounts.AccountError as e:
        db.rollback()
        raise _fail(e) from e
    if out.get("authorization", {}).get("status") == "ACTIVE":
        await _tell_ready(db, runtime, m, out)
    return out


async def _tell_ready(db: Any, runtime: Any, m: Member, conn: dict[str, Any]) -> None:
    """"Your trading bot is ready." in the member's private chat with the bot (only possible once they pressed Start)."""
    from sqlalchemy import select as _select

    from app.models.members import LinkedIdentity
    from app.telegram.service import make_client
    from app.tradingos.bot import SITE

    if not runtime.settings.telegram_configured:
        return
    tg = db.scalar(_select(LinkedIdentity).where(LinkedIdentity.member_id == m.id, LinkedIdentity.platform == "telegram"))
    if tg is None:
        return
    addr = (conn.get("wallet") or {}).get("address") or ""
    mode = "LIVE (real money)" if conn.get("connected") else "Paper (simulated): enable live trading on the website when you're ready"
    text = (f"✅ <b>Your trading bot is ready.</b>\n\nHyperliquid account {addr[:6]}…{addr[-4:]}: connected\nMode: {mode}\nAUTO: off\n\n"
            "Press ENTER on a signal to trade it. Never send a key or seed phrase here.")
    buttons = [[{"text": "📊 Dashboard", "url": f"{SITE}/earn"}, {"text": "📈 Positions", "url": f"{SITE}/earn#kt-positions"}],
               [{"text": "⚙ Settings", "url": f"{SITE}/earn#settings"}]]
    client = make_client(runtime.settings, runtime.telegram.transport)
    try:
        await client.send_message(int(tg.platform_user_id), text, buttons, html=True)
    except Exception:  # noqa: BLE001 - they haven't pressed Start yet: the website shows the same status
        pass
    finally:
        await client.aclose()


@router.post("/members/trading/paper")
def choose_paper(db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    """Step 4, the secondary option: start with paper trading (simulated on real prices). Live stays off."""
    m = _member(db, pc_session)
    signals.audit(db, f"m:{m.id}", "paper_chosen", None, via="website")
    db.commit()
    return {"ok": True}


@router.post("/members/trading/connection/activate")
async def connection_activate(payload: ActivateIn, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    """ENABLE BOT TRADING once the API wallet is approved: consent + every pre-live check, then LIVE with AUTO off. No order."""
    m = _member(db, pc_session)
    try:
        return await asyncio.to_thread(accounts.activate, db, m, payload.consent)
    except accounts.AccountError as e:
        db.rollback()
        raise _fail(e) from e


# ---------------------------------------------------------------- admin
@router.get("/tradingos/admin/execution")
def admin_execution(db: DbSession) -> dict[str, Any]:
    s = signals.settings(db)
    accs = db.scalars(select(TrAccount).order_by(TrAccount.id.desc()).limit(200)).all()
    names = {mm.id: mm.username for mm in db.scalars(select(Member).where(Member.id.in_([a.member_id for a in accs])))} if accs else {}
    positions = db.scalars(select(TrPosition).order_by(TrPosition.id.desc()).limit(100)).all()
    orders = db.scalars(select(TrOrder).order_by(TrOrder.id.desc()).limit(100)).all()
    by_status = dict(db.execute(select(TrPosition.status, func.count()).group_by(TrPosition.status)).all())
    try:
        market.data().markets()
        exchange = {"ok": True}
    except market.MarketError as e:
        exchange = {"ok": False, "error": str(e)}
    return {"live_allowlist": s.live_allowlist, "global_paused": s.paused, "exchange": exchange,
            "health": {"positions_by_status": {k: int(v) for k, v in by_status.items()},
                       "protection_failed": int(by_status.get("PROTECTION_FAILED", 0)),
                       "live_accounts": sum(1 for a in accs if a.mode == "LIVE")},
            "accounts": [{"id": a.id, "username": names.get(a.member_id), "mode": a.mode, "paused": a.paused, "paused_reason": a.paused_reason,
                          "auto": a.auto_enabled, "testnet_agent": bool(a.testnet_approved_at), "mainnet_agent": bool(a.mainnet_approved_at),
                          "live_enabled": bool(a.live_enabled_at), "max_leverage": a.max_leverage, "max_loss": a.max_loss_per_trade_usd,
                          "checks_passed": bool((a.live_checks or {}).get("passed"))} for a in accs],
            "positions": [accounts.pos_out(p) | {"account_id": p.account_id} for p in positions],
            "orders": [{"id": o.id, "position_id": o.position_id, "kind": o.kind, "is_buy": o.is_buy, "size": o.size, "price": o.price,
                        "status": o.status, "oid": o.oid, "filled": o.filled_size, "avg_px": o.avg_px, "error": o.error,
                        "at": o.created_at.isoformat()} for o in orders],
            "results": execution.results(db)}


class AllowIn(StrictInput):
    live_allowlist: str = Field(max_length=500)


@router.put("/tradingos/admin/execution/allowlist")
def admin_allowlist(payload: AllowIn, request: Request, db: DbSession) -> dict[str, Any]:
    s = signals.settings(db)
    s.live_allowlist = ",".join(u.strip() for u in payload.live_allowlist.split(",") if u.strip())
    signals.audit(db, _actor(db, request), "live_allowlist", None, value=s.live_allowlist)
    db.commit()
    return admin_execution(db)


@router.post("/tradingos/admin/execution/accounts/{acc_id}/stop")
def admin_stop(acc_id: int, request: Request, db: DbSession) -> dict[str, Any]:
    """STOP NEW TRADES for one account (its open positions keep their protective orders) and switch AUTO off."""
    a = db.get(TrAccount, acc_id)
    if a is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such account.")
    a.paused, a.paused_reason, a.auto_enabled = True, "Stopped by the owner", False
    signals.audit(db, _actor(db, request), "admin_stop_account", None, account=acc_id)
    db.commit()
    return admin_execution(db)
