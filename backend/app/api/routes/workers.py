"""Worker System API.

Members (/api/members/workers/..., the Passport session): a worker's own tab (role checked server-side), a manager's review
queue (only reports of workers assigned to them). Owner (/api/workers/admin/..., private by default in admin_auth): roles,
jobs, targets, settings, everything and the full approval history.
"""

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import Field
from sqlalchemy import select

from app.api.deps import DbSession, Runtime
from app.api.routes.members import SessionToken, _require
from app.api.schemas import StrictInput
from app.models.workers import JOB_TELEGRAM_INVITES, WkAudit, WkReport, WkReview, WkRole
from app.workers import service as wk

router = APIRouter(tags=["workers"])


def _fail(e: wk.WorkerError) -> HTTPException:
    return HTTPException(e.status, str(e))


def _admin_name(request: Request) -> str:
    return "owner"


# ---------------------------------------------------------------- worker
@router.get("/members/workers/me")
async def me(response: Response, db: DbSession, runtime: Runtime, pc_session: SessionToken = None, page: int = Query(1, ge=1, le=1000)) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    m = _require(db, pc_session)
    roles = wk.roles_of(db, m.id)
    if "WORKER" in roles:
        link = wk.link_for(db, m)
        if not link.chat_link and not link.chat_link_error and runtime.settings.telegram_configured:
            from app.telegram.service import make_client

            tg = make_client(runtime.settings, runtime.telegram.transport)
            try:
                await wk.ensure_chat_link(db, m, tg)
            finally:
                await tg.aclose()
    return wk.worker_view(db, m, page)


class SubmitIn(StrictInput):
    username: str = Field(min_length=2, max_length=80)


@router.post("/members/workers/submissions")
def submit(payload: SubmitIn, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    m = _require(db, pc_session)
    try:
        return wk.sub_out(wk.submit(db, m, payload.username))
    except wk.WorkerError as e:
        raise _fail(e) from e


@router.get("/members/workers/reports")
def my_reports(db: DbSession, pc_session: SessionToken = None, page: int = Query(1, ge=1, le=1000)) -> dict[str, Any]:
    m = _require(db, pc_session)
    try:
        wk.require(db, m, "WORKER")
    except wk.WorkerError as e:
        raise _fail(e) from e
    return wk.reports_of(db, m.id, page)


@router.post("/members/workers/reports/submit")
def submit_report(db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    m = _require(db, pc_session)
    try:
        return wk.report_out(db, wk.submit_report(db, m))
    except wk.WorkerError as e:
        raise _fail(e) from e


# ---------------------------------------------------------------- manager
@router.get("/members/workers/manager")
def manager(db: DbSession, pc_session: SessionToken = None, status: str | None = Query(None, pattern="^(PENDING|APPROVED|REJECTED)$"),
            page: int = Query(1, ge=1, le=1000)) -> dict[str, Any]:
    m = _require(db, pc_session)
    try:
        wk.require(db, m, "MANAGER")
    except wk.WorkerError as e:
        raise _fail(e) from e
    return wk.manager_view(db, m, status=status, page=page)


@router.get("/members/workers/manager/reports/{rid}")
def manager_report(rid: int, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    m = _require(db, pc_session)
    rep = db.get(WkReport, rid)
    if rep is None or not wk.can_review(db, m, rep):
        raise HTTPException(404, "No such report among your workers.")
    return wk.report_out(db, rep, detail=True)


class DecisionIn(StrictInput):
    approve: bool
    reason: str | None = Field(None, max_length=1000)


@router.post("/members/workers/manager/reports/{rid}/decision")
def manager_decide(rid: int, payload: DecisionIn, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    m = _require(db, pc_session)
    try:
        rep = wk.decide(db, rid, payload.approve, payload.reason, actor_kind="manager", actor_member=m, actor_name=m.username)
    except wk.WorkerError as e:
        raise _fail(e) from e
    return wk.report_out(db, rep, detail=True)


# ---------------------------------------------------------------- owner (admin)
@router.get("/workers/admin/overview")
def admin_overview(db: DbSession, status: str | None = Query(None, pattern="^(PENDING|APPROVED|REJECTED)$"), page: int = Query(1, ge=1, le=1000)) -> dict[str, Any]:
    from app.models.members import Member

    view = wk.manager_view(db, None, admin=True, status=status, page=page)
    roles = []
    for r in db.scalars(select(WkRole).order_by(WkRole.id.desc()).limit(300)):
        m = db.get(Member, r.member_id)
        roles.append({"id": r.id, "member_id": r.member_id, "username": m.username if m else "?", "role": r.role, "granted_by": r.granted_by,
                      "granted_at": wk._iso(r.created_at), "revoked_at": wk._iso(r.revoked_at), "revoked_by": r.revoked_by, "note": r.note})
    s = wk.settings(db)
    db.commit()
    return view | {"roles": roles, "settings": {"timezone": s.timezone, "community_chat": s.community_chat, "default_target": s.default_target,
                                              "join_window_hours": s.join_window_hours, "community_chat_id": s.community_chat_id}}


@router.get("/workers/admin/members")
def admin_members(db: DbSession, q: str = Query("", max_length=64)) -> dict[str, Any]:
    return {"items": wk.search_members(db, q)}


class RoleIn(StrictInput):
    member_id: int
    role: str = Field(pattern="^(WORKER|MANAGER)$")
    grant: bool
    note: str | None = Field(None, max_length=300)


@router.post("/workers/admin/roles")
def admin_role(payload: RoleIn, request: Request, db: DbSession) -> dict[str, Any]:
    try:
        if payload.grant:
            wk.grant(db, payload.member_id, payload.role, _admin_name(request), payload.note)
        else:
            wk.revoke(db, payload.member_id, payload.role, _admin_name(request))
    except wk.WorkerError as e:
        raise _fail(e) from e
    return {"roles": wk.public_roles(db, payload.member_id)}


class AssignIn(StrictInput):
    member_id: int
    job_type: str = JOB_TELEGRAM_INVITES
    daily_target: int | None = Field(None, ge=1, le=500)
    manager_id: int | None = None


@router.post("/workers/admin/assignments")
def admin_assign(payload: AssignIn, request: Request, db: DbSession) -> dict[str, Any]:
    try:
        a = wk.assign(db, payload.member_id, payload.job_type, payload.daily_target, payload.manager_id, _admin_name(request))
    except wk.WorkerError as e:
        raise _fail(e) from e
    return {"id": a.id, "daily_target": a.daily_target, "manager_id": a.manager_id}


@router.post("/workers/admin/assignments/{aid}/end")
def admin_end(aid: int, request: Request, db: DbSession) -> dict[str, bool]:
    try:
        wk.end_assignment(db, aid, _admin_name(request))
    except wk.WorkerError as e:
        raise _fail(e) from e
    return {"ok": True}


class SettingsIn(StrictInput):
    timezone: str | None = Field(None, max_length=64)
    community_chat: str | None = Field(None, pattern=r"^@[A-Za-z0-9_]{4,64}$")
    default_target: int | None = Field(None, ge=1, le=500)
    join_window_hours: int | None = Field(None, ge=1, le=336)


@router.put("/workers/admin/settings")
def admin_settings(payload: SettingsIn, request: Request, db: DbSession) -> dict[str, Any]:
    from zoneinfo import ZoneInfo

    s = wk.settings(db)
    ch = payload.model_dump(exclude_none=True)
    if "timezone" in ch:
        try:
            ZoneInfo(ch["timezone"])
        except Exception as e:  # noqa: BLE001
            raise HTTPException(400, "Unknown timezone (use e.g. UTC, Europe/London, Asia/Jerusalem).") from e
    if "community_chat" in ch and ch["community_chat"] != s.community_chat:
        s.community_chat_id = None
    for k, v in ch.items():
        setattr(s, k, v)
    wk.audit(db, _admin_name(request), "settings_changed", None, **ch)
    db.commit()
    return {"ok": True}


@router.get("/workers/admin/reports/{rid}")
def admin_report(rid: int, db: DbSession) -> dict[str, Any]:
    rep = db.get(WkReport, rid)
    if rep is None:
        raise HTTPException(404, "No such report.")
    return wk.report_out(db, rep, detail=True)


@router.post("/workers/admin/reports/{rid}/decision")
def admin_decide(rid: int, payload: DecisionIn, request: Request, db: DbSession) -> dict[str, Any]:
    try:
        rep = wk.decide(db, rid, payload.approve, payload.reason, actor_kind="admin", actor_member=None, actor_name=_admin_name(request))
    except wk.WorkerError as e:
        raise _fail(e) from e
    return wk.report_out(db, rep, detail=True)


@router.get("/workers/admin/history")
def admin_history(db: DbSession, page: int = Query(1, ge=1, le=1000)) -> dict[str, Any]:
    """The approval trail and the owner's role/job changes, newest first (read-only)."""
    reviews = [{"at": wk._iso(v.created_at), "report_id": v.report_id, "actor_kind": v.actor_kind, "actor": v.actor_name, "action": v.action, "reason": v.reason}
               for v in db.scalars(select(WkReview).order_by(WkReview.id.desc()).offset((page - 1) * 50).limit(50))]
    audit = [{"at": wk._iso(a.created_at), "actor": a.actor, "action": a.action, "member_id": a.member_id, "details": a.details}
             for a in db.scalars(select(WkAudit).order_by(WkAudit.id.desc()).offset((page - 1) * 50).limit(50))]
    return {"reviews": reviews, "audit": audit, "page": page}


@router.post("/workers/admin/verify")
async def admin_verify(db: DbSession, runtime: Runtime) -> dict[str, Any]:
    """Run the verification now (also runs every 5 minutes)."""
    from app.telegram.service import make_client

    if not runtime.settings.telegram_configured:
        return await wk.verify(db, None)
    tg = make_client(runtime.settings, runtime.telegram.transport)
    try:
        return await wk.verify(db, tg)
    finally:
        await tg.aclose()
