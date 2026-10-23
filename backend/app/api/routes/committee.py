"""Kiwi AI Investment Committee API (/api/committee/*, contract: Committee run). Research only: nothing here trades.

Public (outside the admin login, admin_auth.PUBLIC_PREFIXES; rate-limited): status, run lists, a run's JSON, its chart candles
(the run's own market and timeframe, cached), the track record.
Run JSON never contains member data (who asked is only on the cm_runs row). Starting a run needs a signed-in Kiwi Passport
member (their daily limit) or the owner's admin login (unlimited). Backtests (start, list, read) are owner only.
"""

import asyncio
import inspect
import re
import threading
import time
from typing import Annotated, Any

from fastapi import APIRouter, Cookie, Depends, HTTPException, Query, Response, status
from pydantic import Field
from sqlalchemy import select

from app.api.deps import DbSession, Runtime
from app.api.routes.members import SessionToken, _current
from app.api.schemas import StrictInput
from app.committee import llm, orchestrator, outcomes, report as cm_report
from app.models.committee import CmRun
from app.security import admin_auth
from app.security.rate_limit import limit

router = APIRouter(tags=["committee"])
P = "/committee"
AdminCookie = Annotated[str | None, Cookie(alias=admin_auth.COOKIE)]


@router.get(f"{P}/status", dependencies=[Depends(limit("earn"))])
async def get_status(db: DbSession, runtime: Runtime) -> dict[str, Any]:
    s = runtime.settings
    backends = await llm.backends_status(db, runtime.provider, s)
    chosen, why = await llm.choose(db, runtime.provider, runtime.gate, s=s)
    providers: dict[str, Any] = {}
    try:  # the evidence providers' health (cm-evidence), when that package offers it
        from app.committee import evidence

        fn = getattr(evidence, "provider_status", None)
        if callable(fn):
            res = fn()
            providers = (await res) if inspect.isawaitable(res) else res
    except Exception:  # noqa: BLE001 - status never fails on a provider
        providers = {}
    return {"enabled": orchestrator.config(db)["on"], "backend": chosen.name, "backend_note": why, "backends": backends,
            "providers": providers, "budget": llm.budget(db, s),
            "concurrency": {"running": orchestrator.running(), "max": s.committee_max_concurrent,
                            "queued": len(db.scalars(select(CmRun.id).where(CmRun.status == "queued")).all())}}


class RunIn(StrictInput):
    symbol: str = Field(min_length=1, max_length=32)
    timeframe: str = Field("1h", max_length=4)
    depth: str = Field("full", pattern="^(full|quick)$")


@router.post(f"{P}/runs", status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(limit("committee"))])
async def create_run(body: RunIn, db: DbSession, runtime: Runtime, pc_session: SessionToken = None,
                     pc_admin: AdminCookie = None) -> dict[str, Any]:
    admin = admin_auth.current(db, pc_admin) is not None
    member = None if admin else _current(db, pc_session)
    if not admin and member is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Please sign in.")
    who = {"admin": True} if admin else {"member_id": member.id}
    before = orchestrator.cached_run(db, body.symbol.strip().upper().removeprefix("$"), body.timeframe)
    try:
        rid = orchestrator.start_run(db, body.symbol, body.timeframe, requested_by=who, depth=body.depth, provider=runtime.provider,
                                     gate=runtime.gate)
    except orchestrator.LimitReached as e:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, e.message) from None
    except orchestrator.CommitteeError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, e.message) from None
    return {"id": rid, "cached": before is not None and before.public_id == rid}


@router.get(f"{P}/runs", dependencies=[Depends(limit("earn"))])
def list_runs(db: DbSession, symbol: str | None = Query(None, max_length=32), limit_: int = Query(20, ge=1, le=100, alias="limit")) -> dict[str, Any]:
    q = select(CmRun).where(CmRun.status.in_(("queued", "running", "done", "failed")))
    if symbol:
        q = q.where(CmRun.symbol == symbol.strip().upper())
    return {"runs": [cm_report.public_run(outcomes.summary(db, r)) for r in db.scalars(q.order_by(CmRun.id.desc()).limit(limit_))]}


def _run(db: Any, run_id: str) -> CmRun:
    row = orchestrator.find(db, run_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such run.")
    return row


@router.get(f"{P}/runs/{{run_id}}", dependencies=[Depends(limit("earn"))])
def get_run(run_id: str, db: DbSession) -> dict[str, Any]:
    row = _run(db, run_id)
    out = dict(orchestrator.stored(row) or {})
    out["status"] = row.status
    out["outcomes"] = outcomes.outcomes_of(db, row)
    return cm_report.public_run(out)


CANDLES_N = 120
CANDLES_TTL = 60.0  # s: the chart's candles per market and timeframe (every visitor of a run shares them)
_candle_cache: dict[tuple[str, str, str], tuple[float, list[list[float]], str]] = {}
_candle_lock = threading.Lock()


def _listing(asset: dict[str, Any], row: CmRun, run: dict[str, Any]) -> Any:
    """The run's own market as a venues.Listing (what the evidence read), or None when it can't be named again."""
    from app.tradingos import venues

    venue = row.venue or asset.get("venue")
    mid = row.market_id or asset.get("market_id")
    kind = row.market_type or asset.get("market_type") or "spot"
    if not venue or not mid:
        return None
    network = None
    if venue == "dex":  # the pool's network, from the evidence's own candle source (GeckoTerminal .../networks/<net>/pools/<id>)
        for ref in ((run.get("evidence") or {}).get("refs") or []):
            m = re.search(r"/networks/([a-z0-9_-]+)/pools/", str((ref or {}).get("url") or ""))
            if m:
                network = m.group(1)
                break
        if network is None:
            return None
    elif venue not in ("hyperliquid", *venues.EXCHANGES):
        return None
    return venues.Listing(venue=venue, kind="perp" if venue == "hyperliquid" else kind, base=str(asset.get("symbol") or row.symbol),
                          quote="USD", symbol=str(mid), price=0.0, change_24h=None, volume_24h=None, network=network)


def _closed_candles(listing: Any, tf: str, n: int) -> tuple[list[list[float]], str]:
    """[[t ms, o, h, l, c, v]] of the last n CLOSED candles (cached CANDLES_TTL per market and timeframe), and their source."""
    from app.tradingos import market, venues

    key = (listing.venue, listing.symbol, tf)
    now = time.time()
    with _candle_lock:
        hit = _candle_cache.get(key)
        if hit is not None and now - hit[0] < CANDLES_TTL:
            return hit[1], hit[2]
    cs = venues.candles(listing, tf, n + 1, market.data(), venues.client())
    span = market.INTERVALS[tf] * 1000
    closed = [c for c in cs if c.t + span <= now * 1000][-n:]
    rows = [[c.t, c.o, c.h, c.l, c.c, c.v] for c in closed]
    source = (f"Hyperliquid {listing.symbol} perpetual" if listing.venue == "hyperliquid" else
              f"{venues.NAME.get(listing.venue, 'DEX')} {listing.symbol}" if listing.venue != "dex" else f"GeckoTerminal pool {listing.symbol}")
    with _candle_lock:
        if len(_candle_cache) > 500:
            _candle_cache.clear()
        _candle_cache[key] = (now, rows, source)
    return rows, source


@router.get(f"{P}/runs/{{run_id}}/candles", dependencies=[Depends(limit("committee_chart"))])
async def run_candles(run_id: str, db: DbSession) -> dict[str, Any]:
    """Public, read-only: the closed candles (~120) of the run's OWN market and timeframe, from the same source its evidence
    used (Hyperliquid via market.data(), other venues via venues), with the run's scenario levels. No member data."""
    row = _run(db, run_id)
    run = orchestrator.stored(row) or {}
    asset = run.get("asset") or {}
    tf = str(run.get("timeframe") or row.timeframe)
    listing = _listing(asset, row, run)
    if listing is None or tf not in orchestrator.TIMEFRAMES:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No chart for this run's market.")
    from app.tradingos import market, venues

    try:
        rows, source = await asyncio.to_thread(_closed_candles, listing, tf, CANDLES_N)
    except (market.MarketError, venues.NotFound, OSError, ValueError, KeyError) as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Candles unavailable right now: {str(e)[:160]}") from None
    scen = (run.get("verdict") or {}).get("scenarios") or {}
    levels = {side: {k: s.get(k) for k in ("trigger", "invalidation", "targets", "entry_zone")} if isinstance(s, dict) else None
              for side, s in (("long", scen.get("long")), ("short", scen.get("short")))}
    return {"run": row.public_id, "coin": listing.symbol, "symbol": asset.get("symbol") or row.symbol, "venue": listing.venue,
            "timeframe": tf, "source": f"{source} {tf} candles (closed only)", "candles": rows, "markers": [],
            "levels": levels, "price_at": (run.get("price") or {}).get("value")}


@router.get(f"{P}/runs/{{run_id}}/report.html", dependencies=[Depends(limit("earn"))])
def report(run_id: str, db: DbSession) -> Response:
    """The run as a standalone, downloadable HTML document (inline CSS, no scripts, escaped model text)."""
    row = _run(db, run_id)
    run = orchestrator.stored(row) or {}
    if row.status != "done" or not row.result:
        raise HTTPException(status.HTTP_409_CONFLICT, "This run has no finished report yet.")
    body = cm_report.render(run, outcomes.outcomes_of(db, row), row.created_at)
    name = cm_report.filename(run, row.created_at)
    return Response(body, media_type="text/html; charset=utf-8", headers={
        "Content-Disposition": f'attachment; filename="{name}"', "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; img-src data:; frame-ancestors 'none'",
        "Cache-Control": "private, max-age=60"})


@router.get(f"{P}/track", dependencies=[Depends(limit("earn"))])
def track(db: DbSession, limit_: int = Query(50, ge=1, le=200, alias="limit")) -> dict[str, Any]:
    return cm_report.public_run(outcomes.track(db, limit_))


@router.get(f"{P}/paper", dependencies=[Depends(limit("earn"))])
def paper(db: DbSession, limit_: int = Query(100, ge=1, le=500, alias="limit")) -> dict[str, Any]:
    """The committee's PAPER book (simulated fills on real candles; never real money, never member data)."""
    from app.committee import paper as paper_book

    return paper_book.view(db, limit_)


class BacktestIn(StrictInput):
    coins: list[str] = Field(default_factory=lambda: ["BTC", "ETH", "SOL", "HYPE"], min_length=1, max_length=8)
    timeframe: str = Field("4h", pattern="^(1h|4h|1d)$")
    every: int = Field(6, ge=1, le=48)
    mode: str = Field("rules", pattern="^(rules|llm)$")
    llm_max_decisions: int = Field(20, ge=1, le=60)
    llm_max_usd: float = Field(0.0, ge=0, le=5)
    start: str | None = Field(None, max_length=32)
    end: str | None = Field(None, max_length=32)


def _owner(db: Any, pc_admin: str | None) -> None:
    if admin_auth.current(db, pc_admin) is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Owner login required.")


@router.post(f"{P}/backtests", status_code=status.HTTP_202_ACCEPTED)
async def create_backtest(body: BacktestIn, db: DbSession, runtime: Runtime, pc_admin: AdminCookie = None) -> dict[str, Any]:
    """Owner only: start a committee backtest in the background (one at a time). Research only: nothing trades."""
    from app.committee import backtest as cbt

    _owner(db, pc_admin)
    try:
        cfg = cbt.Config.parse(body.model_dump())
    except (ValueError, TypeError) as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from None
    busy = cbt.running(db)
    if busy is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Backtest {busy.id} is still {busy.status}.")
    return {"id": cbt.start(db, cfg, runtime.provider, runtime.gate), "status": "queued"}


@router.get(f"{P}/backtests")
def backtests(db: DbSession, pc_admin: AdminCookie = None, limit_: int = Query(20, ge=1, le=100, alias="limit")) -> dict[str, Any]:
    from app.models.committee import CmBacktest

    _owner(db, pc_admin)
    rows = db.scalars(select(CmBacktest).order_by(CmBacktest.id.desc()).limit(limit_))
    return {"backtests": [{"id": r.id, "created_at": r.created_at.isoformat(), "status": r.status, "config": r.config,
                           "period": (r.result or {}).get("period"), "verdicts": (r.result or {}).get("verdicts"),
                           "error": (r.result or {}).get("error")} for r in rows]}


@router.get(f"{P}/backtests/{{bt_id}}")
def backtest(bt_id: str, db: DbSession, pc_admin: AdminCookie = None) -> dict[str, Any]:
    from app.models.committee import CmBacktest

    _owner(db, pc_admin)
    row = db.get(CmBacktest, int(bt_id)) if bt_id.isdigit() else None
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such backtest.")
    return {"id": row.id, "created_at": row.created_at.isoformat(), "status": row.status, "config": row.config, "result": row.result}
