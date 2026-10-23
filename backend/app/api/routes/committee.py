"""Kiwi AI Investment Committee API (/api/committee/*, contract: Committee run). Research only: nothing here trades.

Public (outside the admin login, admin_auth.PUBLIC_PREFIXES; rate-limited): status, run lists, a run's JSON, the track record.
Run JSON never contains member data (who asked is only on the cm_runs row). Starting a run needs a signed-in Kiwi Passport
member (their daily limit) or the owner's admin login (unlimited). The report, paper book and backtests are stage 2.
"""

import inspect
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


@router.get(f"{P}/paper")
def paper() -> dict[str, Any]:
    return {"status": "not_built"}


@router.get(f"{P}/backtests")
def backtests() -> dict[str, Any]:
    return {"status": "not_built"}


@router.get(f"{P}/backtests/{{bt_id}}")
def backtest(bt_id: str) -> dict[str, Any]:
    return {"status": "not_built"}
