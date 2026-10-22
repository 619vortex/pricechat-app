"""Admin → Trading OS (owner login required: every non-public /api route is guarded by the admin middleware), plus the
website's read-only view of published signals."""

import asyncio
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from pydantic import Field
from sqlalchemy import func, select

from app.db.base import utcnow
from app.api.deps import DbSession, Runtime
from app.api.schemas import StrictInput
from app.models.tradingos import TsAudit, TsSignal
from app.security import admin_auth
from app.tradingos import bot, market, risk, signals

router = APIRouter(tags=["trading-os"])


def _actor(db: Any, request: Request) -> str:
    s = admin_auth.current(db, request.cookies.get(admin_auth.COOKIE))
    return f"admin:{s.username}" if s else "admin"


def sig_out(db: Any, s: TsSignal) -> dict[str, Any]:
    return {"id": s.id, "number": s.number, "coin": s.coin, "side": s.side, "timeframe": s.timeframe, "strategy": s.strategy,
            "entry": [s.entry_lo, s.entry_hi], "stop": s.stop, "target": s.target, "leverage": [s.lev_lo, s.lev_hi], "rr": s.rr,
            "status": s.status, "status_label": signals.STATUS_LABEL.get(s.status, s.status), "reason": s.reason,
            "created_at": s.created_at.isoformat(), "expires_at": s.expires_at.isoformat(),
            "published_at": s.published_at.isoformat() if s.published_at else None, "last_price": s.last_price,
            "activated_at": s.activated_at.isoformat() if s.activated_at else None, "closed_at": s.closed_at.isoformat() if s.closed_at else None,
            "tp2": risk.tp2_of((s.entry_lo + s.entry_hi) / 2, s.target),
            "model_pnl_pct": s.model_pnl_pct, "exit_price": s.exit_price, "tg_error": s.tg_error,
            "participation": signals.participation(db, s), "card": signals.card(s, signals.participation(db, s))}


@router.get("/tradingos/admin/overview")
def overview(db: DbSession) -> dict[str, Any]:
    s = signals.settings(db)
    md = market.data()
    try:
        _, ms = md.markets()
        exchange = {"ok": True, "markets": len(ms)}
    except market.MarketError as e:
        exchange = {"ok": False, "error": str(e)}
    db.commit()
    counts = {st: int(db.scalar(select(func.count()).select_from(TsSignal).where(TsSignal.status == st)) or 0)
              for st in signals.STATUS_LABEL}
    return {"settings": {k: getattr(s, k) for k in ("scanner_enabled", "auto_publish", "paused", "publish_chat", "main_chat", "chart_routing", "brief_enabled", "brief_hour", "timeframe", "universe_size",
                                                     "min_volume_usd", "max_spread_bps", "max_open_signals", "min_rr", "strategies", "allowed_coins")},
            "exchange": exchange, "counts": counts, "stats": signals.stats(db),
            "execution": "ENTER → private preview → confirm. PAPER by default; TESTNET with an approved API wallet; LIVE only for the "
                         "allowlist after the pre-live checks"}


@router.get("/tradingos/admin/performance")
def signal_performance(db: DbSession, days: int | None = Query(None, ge=1, le=3650)) -> dict[str, Any]:
    """The internal signal performance report (performance.py): outcomes, win rate, R, costs, data quality, findings."""
    from app.tradingos import performance

    return performance.report(db, days=days)


@router.get("/tradingos/admin/decisions")
def trade_decisions(db: DbSession, days: int = Query(30, ge=1, le=180), limit: int = Query(50, ge=1, le=200),
                    coin: str | None = Query(None, max_length=32), state: str | None = Query(None, max_length=24)) -> dict[str, Any]:
    """Engine v2: the structured kitchen decisions with their shadow outcomes, and the report (approved vs rejected, per
    filter, per regime, score calibration). Hypothetical results, labelled so."""
    from app.tradingos import decisions

    return {"report": decisions.report(db, days=days), "recent": decisions.recent(db, limit, coin, state)}


@router.get("/tradingos/admin/markets")
def markets(db: DbSession) -> dict[str, Any]:
    try:
        top = market.data().top(30)
    except market.MarketError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e)) from e
    return {"items": [{"coin": m.coin, "price": m.mark, "change_24h": round(m.change_24h, 2), "funding_h": m.funding, "oi_usd": m.oi_usd,
                       "volume_24h": m.day_volume, "spread_bps": m.spread_bps, "max_leverage": m.max_leverage} for m in top]}


@router.get("/tradingos/admin/signals")
def list_signals(db: DbSession, status_: str | None = Query(None, alias="status")) -> dict[str, Any]:
    q = select(TsSignal).order_by(TsSignal.number.desc()).limit(100)
    if status_:
        q = q.where(TsSignal.status == status_)
    return {"items": [sig_out(db, s) for s in db.scalars(q)]}


@router.post("/tradingos/admin/scan")
async def scan_now(request: Request, db: DbSession) -> dict[str, Any]:
    return await asyncio.to_thread(signals.scan, db, market.data(), _actor(db, request))


@router.get("/tradingos/admin/analyze")
async def analyze(db: DbSession, coin: str = Query(..., min_length=1, max_length=20)) -> dict[str, Any]:
    s = signals.settings(db)
    try:
        a = await asyncio.to_thread(signals.analyze, market.data(), coin, s.timeframe, s.min_volume_usd, s.max_spread_bps, s.min_rr)
    except market.MarketError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    return {"coin": a.coin, "decision": a.decision, "reasons": a.reasons, "indicators": a.indicators, "entry": a.entry, "stop": a.stop,
            "target": a.target, "rr": a.rr, "leverage": a.lev}


def _sig(db: Any, sid: int) -> TsSignal:
    s = db.get(TsSignal, sid)
    if s is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such signal.")
    return s


@router.post("/tradingos/admin/signals/{sid}/publish")
async def publish(sid: int, request: Request, db: DbSession, runtime: Runtime) -> dict[str, Any]:
    """Approve and post to the signal group. Idempotent (a published signal is never posted twice)."""
    from app.telegram.service import make_client

    sig = _sig(db, sid)
    if sig.status not in ("PENDING_REVIEW", "WAITING"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"A {sig.status} signal can't be published.")
    if signals.settings(db).paused:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Trading OS is paused.")
    client = make_client(runtime.settings, runtime.telegram.transport)
    try:
        await bot.publish(db, client, sig, _actor(db, request))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Telegram didn't take it: {sig.tg_error or e}") from e
    finally:
        await client.aclose()
    return sig_out(db, sig)


@router.post("/tradingos/admin/signals/{sid}/refresh")
async def refresh_card(sid: int, db: DbSession, runtime: Runtime) -> dict[str, Any]:
    """Redraw a published signal's Telegram card from its record (e.g. after the card layout changed). Nothing else changes."""
    from app.telegram.service import make_client

    sig = _sig(db, sid)
    if sig.tg_message_id:
        client = make_client(runtime.settings, runtime.telegram.transport)
        try:
            await bot.refresh(db, client, sig)
        finally:
            await client.aclose()
    return sig_out(db, sig)


@router.post("/tradingos/admin/signals/{sid}/cancel")
async def cancel(sid: int, request: Request, db: DbSession, runtime: Runtime) -> dict[str, Any]:
    from app.telegram.service import make_client

    sig = _sig(db, sid)
    if sig.status in bot.CLOSED:
        return sig_out(db, sig)
    sig.status, sig.closed_at = "CANCELLED", utcnow()  # the website's Kiwi chat says when it was cancelled
    signals.audit(db, _actor(db, request), "cancelled", sig)
    db.commit()
    if sig.tg_message_id:
        client = make_client(runtime.settings, runtime.telegram.transport)
        try:
            await bot.refresh(db, client, sig)
        finally:
            await client.aclose()
    return sig_out(db, sig)


class TestIn(StrictInput):
    coin: str = Field("BTC", min_length=1, max_length=20)


@router.post("/tradingos/admin/test-signal")
async def test_signal(payload: TestIn, request: Request, db: DbSession, runtime: Runtime) -> dict[str, Any]:
    """Post a TEST signal (real prices, labelled TEST, excluded from stats) to check Telegram + website sync end to end."""
    from app.telegram.service import make_client

    try:
        sig = await asyncio.to_thread(signals.test_signal, db, market.data(), payload.coin, _actor(db, request))
    except market.MarketError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    client = make_client(runtime.settings, runtime.telegram.transport)
    try:
        await bot.publish(db, client, sig, _actor(db, request))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Telegram didn't take it: {sig.tg_error or e}") from e
    finally:
        await client.aclose()
    return sig_out(db, sig)


@router.post("/tradingos/admin/manual-signal")
async def manual_signal(payload: TestIn, request: Request, db: DbSession, runtime: Runtime) -> dict[str, Any]:
    """Push a REAL signal now (the owner's manual call): posted to the signal group straight away, entered by members with AUTO on
    (within their own limits and mode), counted in the track record. Refused while signals are stopped."""
    from app.telegram.service import make_client

    if signals.settings(db).paused:
        raise HTTPException(status.HTTP_409_CONFLICT, "Signals are stopped: press Resume signals first.")
    try:
        sig = await asyncio.to_thread(signals.manual_signal, db, market.data(), payload.coin, _actor(db, request))
    except market.MarketError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    client = make_client(runtime.settings, runtime.telegram.transport)
    try:
        await bot.publish(db, client, sig, _actor(db, request))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Telegram didn't take it: {sig.tg_error or e}") from e
    finally:
        await client.aclose()
    return sig_out(db, sig)


class SettingsIn(StrictInput):
    scanner_enabled: bool | None = None
    auto_publish: bool | None = None
    paused: bool | None = None
    publish_chat: str | None = Field(None, max_length=64)
    main_chat: str | None = Field(None, max_length=64)  # "" turns the main-chat notices off
    chart_routing: str | None = Field(None, pattern=r"^(anywhere|bot_chat)$")  # /chart asked in the main chat: answer there, or in Bot Chat
    brief_enabled: bool | None = None  # the daily brief in the signal group
    brief_hour: int | None = Field(None, ge=0, le=23)  # UTC
    timeframe: str | None = Field(None, pattern=r"^(15m|1h|4h)$")
    universe_size: int | None = Field(None, ge=1, le=60)
    min_volume_usd: float | None = Field(None, ge=0)
    max_spread_bps: float | None = Field(None, gt=0, le=100)
    max_open_signals: int | None = Field(None, ge=1, le=10)
    min_rr: float | None = Field(None, ge=1, le=10)
    allowed_coins: str | None = Field(None, max_length=500)
    trend_pullback_v1: bool | None = None
    breakout_4h_v1: bool | None = None
    intraday_15m_v1: bool | None = None  # Kiwi Kitchen: READY proposes signals (off = shadow)
    intraday_live: bool | None = None  # 15m signals may trade TESTNET / LIVE for members who opted in (off = paper only)
    risk_v2_mode: str | None = Field(None, pattern=r"^(advisory|enforce)$")  # Engine v2 portfolio checks: record only, or refuse entries


@router.put("/tradingos/admin/settings")
def put_settings(payload: SettingsIn, request: Request, db: DbSession) -> dict[str, Any]:
    s = signals.settings(db)
    v = payload.model_dump(exclude_none=True)
    for k in ("trend_pullback_v1", "breakout_4h_v1", "intraday_15m_v1", "intraday_live"):
        if k in v:
            s.strategies = {**s.strategies, k: v.pop(k)}
    if "risk_v2_mode" in v:
        s.strategies = {**s.strategies, "risk_v2": {**((s.strategies or {}).get("risk_v2") or {}), "mode": v.pop("risk_v2_mode")}}
    for k, val in v.items():
        setattr(s, k, val.strip() if isinstance(val, str) else val)
    signals.audit(db, _actor(db, request), "settings", None, **{k: str(val) for k, val in payload.model_dump(exclude_none=True).items()})
    db.commit()
    return overview(db)


@router.get("/tradingos/admin/kiwi-brain/events")
def admin_brain_events(db: DbSession, limit: int = Query(200, ge=1, le=500), before_id: int | None = Query(None, ge=1),
                       member_id: int | None = Query(None, ge=1), kind: str | None = Query(None, max_length=120),
                       signal_id: int | None = Query(None, ge=1)) -> dict[str, Any]:
    """Admin: every Kiwi Brain event (or one member's / one signal's). Event data never holds secrets (brain._clean)."""
    from app.tradingos import brain

    kinds = [k for k in (kind or "").split(",") if k in brain.KINDS] or None
    rows = brain.events_for(db, member_id, limit=limit, before_id=before_id, kinds=kinds, admin=True, signal_id=signal_id)
    return {"items": [brain.event_out(e) | {"member_id": e.member_id} for e in rows]}


@router.get("/tradingos/admin/audit")
def audit_log(db: DbSession) -> dict[str, Any]:
    rows = db.scalars(select(TsAudit).order_by(TsAudit.id.desc()).limit(200))
    return {"items": [{"at": a.created_at.isoformat(), "actor": a.actor, "action": a.action, "signal_id": a.signal_id, "details": a.details} for a in rows]}


# ---------------------------------------------------------------- the website (members): published signals only
@router.get("/members/trading-os/signals")
def site_signals(db: DbSession) -> dict[str, Any]:
    # finished owner pipeline checks (strategy "test") stay out of members' feed; an open one is shown (labelled TEST)
    rows = db.scalars(select(TsSignal).where(TsSignal.published_at.is_not(None), (TsSignal.strategy != "test") | TsSignal.status.in_(signals.OPEN))
                      .order_by(TsSignal.number.desc()).limit(10)).all()
    return {"items": [{k: v for k, v in sig_out(db, s).items() if k != "tg_error"} for s in reversed(rows)], "stats": signals.stats(db)}


_charts: dict[tuple[int, int], bytes] = {}


@router.get("/members/trading-os/signals/{sid}/chart.png")
async def site_signal_chart(sid: int, db: DbSession) -> Response:
    """The same chart the Telegram card shows (real Hyperliquid candles, the signal's levels), redrawn at most once a minute."""
    from app.tradingos import chart

    s = db.get(TsSignal, sid)
    if s is None or s.published_at is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such signal.")
    key = (sid, int(time.time() // 60))
    png = _charts.get(key)
    if png is None:
        try:
            candles = await asyncio.to_thread(market.data().candles, s.coin, s.timeframe, 120)
            png = await asyncio.to_thread(chart.render, candles, f"#{s.number:03d} {s.coin} · {s.side}", (s.entry_lo, s.entry_hi), s.stop,
                                          s.target, f"{s.timeframe} · {s.strategy}")
        except (market.MarketError, ValueError) as e:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e)) from e
        if len(_charts) > 50:
            _charts.clear()
        _charts[key] = png
    return Response(png, media_type="image/png", headers={"Cache-Control": "public, max-age=60"})
