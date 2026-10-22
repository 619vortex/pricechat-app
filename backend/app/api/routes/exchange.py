"""Pokémon Paper Exchange API. PAPER TRADING ONLY: no deposits, withdrawals or real money anywhere.

/api/members/exchange/*   market data is public; trading needs a Kiwi Passport session (the member is always the
                          session's member, never one named in a request)
/api/exchange/admin/*     owner only (behind the admin gate like every non-member /api path)
"""

import threading
import time
from collections import defaultdict, deque
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import Field
from sqlalchemy import func, select

from app.api.deps import DbSession, Runtime
from app.api.routes.members import SessionToken, _require
from app.api.schemas import StrictInput
from app.exchange import admin, engine, maker, views
from app.exchange.ledger import ExchangeError, account_for, ensure_markets
from app.models.exchange import ExMarket



def _retired(request: Request, runtime: Runtime) -> None:
    """The Paper Exchange is retired from the website (settings.exchange_enabled). Its data is kept; members get 410."""
    if not runtime.settings.exchange_enabled and request.url.path.startswith("/api/members/exchange"):
        raise HTTPException(status.HTTP_410_GONE, "The Paper Exchange has closed. Visit the Price Chat Vault at /pokemon.")


router = APIRouter(tags=["exchange"], dependencies=[Depends(_retired)])

# rate limit: per member, in this process (bounded memory: one small deque per active member)
_RATE: dict[int, deque[float]] = defaultdict(deque)
_RATE_LOCK = threading.Lock()
PER_10S, PER_HOUR = 12, 600


def _rate(member_id: int) -> None:
    now = time.monotonic()
    with _RATE_LOCK:
        q = _RATE[member_id]
        while q and now - q[0] > 3600:
            q.popleft()
        if sum(1 for t in q if now - t <= 10) >= PER_10S or len(q) >= PER_HOUR:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Slow down: too many orders in a short time.")
        q.append(now)


def _market(db: DbSession, symbol: str) -> ExMarket:
    if (db.scalar(select(func.count()).select_from(ExMarket)) or 0) == 0:
        ensure_markets(db)
    m = db.scalar(select(ExMarket).where(ExMarket.symbol == symbol.upper()))
    if m is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown market.")
    return m


def _fail(exc: ExchangeError) -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, str(exc))


@router.get("/members/exchange/markets")
def markets(db: DbSession) -> dict[str, Any]:
    if (db.scalar(select(func.count()).select_from(ExMarket)) or 0) == 0:
        ensure_markets(db)
    return {"paper_trading": True, "markets": views.markets(db)}


@router.get("/members/exchange/markets/{symbol}")
def market(symbol: str, db: DbSession) -> dict[str, Any]:
    m = _market(db, symbol)
    return {"paper_trading": True, "market": views.market_out(db, m), "book": views.book(db, m), "trades": views.trades(db, m)}


@router.get("/members/exchange/markets/{symbol}/candles")
def candles(symbol: str, db: DbSession, interval: Literal["1m", "5m", "15m", "1h", "4h", "1D", "1W"] = "1h") -> dict[str, Any]:
    return views.candles(db, _market(db, symbol), interval)


@router.get("/members/exchange/leaderboard")
def leaderboard(db: DbSession, board: Literal["equity", "7d", "30d", "trades"] = "equity") -> dict[str, Any]:
    return views.leaderboard(db, board)


@router.get("/members/exchange/me")
def me(db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    member = _require(db, pc_session)
    acct = account_for(db, member)
    assert acct is not None
    return views.account_view(db, acct)


class OrderIn(StrictInput):
    symbol: str = Field(min_length=2, max_length=16)
    side: Literal["buy", "sell"]
    type: Literal["limit", "market"]
    qty: int = Field(ge=1, le=1000)
    price: int | None = Field(None, ge=1, le=10_000_000_000)  # paper USDT cents (limit orders)
    client_id: str = Field(min_length=6, max_length=40, pattern=r"^[A-Za-z0-9_-]+$")


@router.post("/members/exchange/orders")
def place(payload: OrderIn, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    member = _require(db, pc_session)
    _rate(member.id)
    m = _market(db, payload.symbol)
    acct = account_for(db, member)
    assert acct is not None
    try:
        out = engine.place(db, acct, m, payload.side, payload.type, payload.qty, payload.price, payload.client_id)
    except ExchangeError as exc:
        raise _fail(exc) from exc
    if out["fills"]:
        try:
            maker.requote(db, m)  # the simulated maker re-centres its quotes after a trade
        except Exception:  # noqa: BLE001 - quoting can wait for the next minute; the order is done
            db.rollback()
    return out


@router.post("/members/exchange/orders/{order_id}/cancel")
def cancel(order_id: int, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    member = _require(db, pc_session)
    acct = account_for(db, member)
    assert acct is not None
    try:
        return engine.cancel(db, acct, order_id)
    except ExchangeError as exc:
        raise _fail(exc) from exc


# --- admin (owner) --------------------------------------------------------------------------------------------------------


@router.get("/exchange/admin/overview")
def admin_overview(db: DbSession) -> dict[str, Any]:
    ensure_markets(db)
    return admin.overview(db)


@router.post("/exchange/admin/markets/{symbol}/{action}")
def admin_market(symbol: str, action: Literal["pause", "resume"], db: DbSession) -> dict[str, Any]:
    try:
        return admin.set_status(db, symbol.upper(), "paused" if action == "pause" else "active", "owner")
    except ExchangeError as exc:
        raise _fail(exc) from exc


@router.post("/exchange/admin/oracle/refresh")
def admin_oracle(db: DbSession) -> dict[str, Any]:
    ensure_markets(db)
    return admin.run_oracle(db, "owner")


@router.post("/exchange/admin/maker/requote")
def admin_requote(db: DbSession) -> dict[str, Any]:
    return maker.requote_all(db)


class AutoIn(StrictInput):
    enabled: bool | None = None
    spot: bool | None = None
    perps: bool | None = None
    activity: float | None = Field(None, ge=0.5, le=12)
    swing_pct: float | None = Field(None, ge=0.3, le=4)
    max_trade_value: int | None = Field(None, ge=1_000, le=200_000)


@router.get("/exchange/admin/auto")
def admin_auto(db: DbSession) -> dict[str, Any]:
    from app.exchange import auto

    ensure_markets(db)
    return auto.status(db)


@router.put("/exchange/admin/auto")
def admin_auto_set(payload: AutoIn, db: DbSession) -> dict[str, Any]:
    from app.exchange import auto, perps

    ensure_markets(db)
    before = auto.config(db)
    cfg = auto.set_config(db, payload.model_dump(exclude_none=True))
    admin.audit(db, "owner", "auto.config", "auto-trader", {"before": before, "after": cfg})
    db.commit()
    maker.requote_all(db)  # re-centre now (back on the reference when switched off)
    for pm in perps.ensure_perps(db):
        perps.requote(db, pm)
    return auto.status(db)


@router.post("/exchange/admin/auto/tick")
def admin_auto_tick(db: DbSession) -> dict[str, Any]:
    from app.exchange import auto

    return auto.tick(db)


class ResetIn(StrictInput):
    account_id: int = Field(ge=1)


@router.post("/exchange/admin/reset")
def admin_reset(payload: ResetIn, db: DbSession) -> dict[str, Any]:
    try:
        return admin.reset_account(db, payload.account_id, "owner")
    except ExchangeError as exc:
        raise _fail(exc) from exc


# --- perpetuals (paper trading only) -------------------------------------------------------------------------------------


def _perp(db: DbSession, symbol: str):  # type: ignore[no-untyped-def]
    from app.exchange import perps
    from app.models.exchange import PerpMarket

    if (db.scalar(select(func.count()).select_from(PerpMarket)) or 0) == 0:
        ensure_markets(db)
        perps.ensure_perps(db)
    pm = db.scalar(select(PerpMarket).where(PerpMarket.symbol == symbol.upper()))
    if pm is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown perpetual.")
    return pm


@router.get("/members/exchange/perps")
def perp_markets(db: DbSession) -> dict[str, Any]:
    from app.exchange import perps
    from app.models.exchange import PerpMarket

    if (db.scalar(select(func.count()).select_from(PerpMarket)) or 0) == 0:
        ensure_markets(db)
        perps.ensure_perps(db)
    return {"paper_trading": True, "markets": perps.markets(db)}


@router.get("/members/exchange/perps/{symbol}")
def perp_market(symbol: str, db: DbSession) -> dict[str, Any]:
    from app.exchange import perps

    pm = _perp(db, symbol)
    return {"paper_trading": True, "market": perps.market_out(db, pm), "book": perps.book(db, pm), "trades": perps.trades(db, pm)}


@router.get("/members/exchange/perps/{symbol}/candles")
def perp_candles(symbol: str, db: DbSession, interval: Literal["1m", "5m", "15m", "1h", "4h", "1D", "1W"] = "1h") -> dict[str, Any]:
    from app.exchange import perps

    return perps.candles(db, _perp(db, symbol), interval)


class PerpOrderIn(StrictInput):
    symbol: str = Field(min_length=2, max_length=20)
    side: Literal["buy", "sell"]
    type: Literal["limit", "market"]
    qty: int = Field(ge=1, le=1000)
    price: int | None = Field(None, ge=1, le=10_000_000_000)
    leverage: int = Field(ge=1, le=10)
    reduce_only: bool = False
    client_id: str = Field(min_length=6, max_length=40, pattern=r"^[A-Za-z0-9_-]+$")


@router.post("/members/exchange/perps/orders")
def perp_place(payload: PerpOrderIn, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    from app.exchange import perps

    member = _require(db, pc_session)
    _rate(member.id)
    pm = _perp(db, payload.symbol)
    acct = account_for(db, member)
    assert acct is not None
    try:
        out = perps.place(db, acct, pm, payload.side, payload.type, payload.qty, payload.price, payload.leverage, payload.client_id, payload.reduce_only)
    except ExchangeError as exc:
        raise _fail(exc) from exc
    if out["fills"]:
        try:
            perps.requote(db, pm)
        except Exception:  # noqa: BLE001 - quoting can wait for the next minute
            db.rollback()
    return out


@router.post("/members/exchange/perps/orders/{order_id}/cancel")
def perp_cancel(order_id: int, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    from app.exchange import perps

    member = _require(db, pc_session)
    acct = account_for(db, member)
    assert acct is not None
    try:
        return perps.cancel(db, acct, order_id)
    except ExchangeError as exc:
        raise _fail(exc) from exc


class CloseIn(StrictInput):
    client_id: str = Field(min_length=6, max_length=40, pattern=r"^[A-Za-z0-9_-]+$")


@router.post("/members/exchange/perps/{symbol}/close")
def perp_close(symbol: str, payload: CloseIn, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    from app.exchange import perps

    member = _require(db, pc_session)
    _rate(member.id)
    pm = _perp(db, symbol)
    acct = account_for(db, member)
    assert acct is not None
    try:
        out = perps.close_position(db, acct, pm, payload.client_id)
    except ExchangeError as exc:
        raise _fail(exc) from exc
    try:
        perps.requote(db, pm)
    except Exception:  # noqa: BLE001
        db.rollback()
    return out
