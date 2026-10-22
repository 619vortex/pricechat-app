"""Invite Bot API (dashboard › Invite Bot). Owner only: every /api/invites route is behind the admin login.
Login codes and 2-step passwords go straight to Telegram; they are never stored, logged or returned."""

from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import Field

from app.api.deps import DbSession, Runtime
from app.api.schemas import StrictInput
from app.invites import service as svc
from app.models.invites import InviteGroup

router = APIRouter(prefix="/invites", tags=["invites"])


def _fail(exc: Exception) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND if str(exc) == "Not found." else status.HTTP_400_BAD_REQUEST, str(exc))


@router.get("")
def overview(db: DbSession, runtime: Runtime) -> dict[str, Any]:
    return svc.overview(db, runtime.settings)


class SettingsIn(StrictInput):
    api_id: int | None = Field(None, ge=1)
    api_hash: str | None = Field(None, pattern=r"^[0-9a-fA-F]{32}$")
    invite_link: str | None = Field(None, pattern=r"^https://t\.me/[A-Za-z0-9_+/-]{3,100}$")
    dm_message: str | None = Field(None, min_length=10, max_length=1000)
    group_message: str | None = Field(None, min_length=10, max_length=1000)
    daily_limit: int | None = Field(None, ge=1, le=20)
    group_post_interval_days: int | None = Field(None, ge=1, le=90)
    opt_in_keywords: str | None = Field(None, min_length=2, max_length=300)
    opt_out_words: str | None = Field(None, min_length=2, max_length=300)
    active_days: int | None = Field(None, ge=1, le=90)
    retention_days: int | None = Field(None, ge=7, le=365)
    monitoring: bool | None = None
    group_posts_enabled: bool | None = None
    auto_send: bool | None = None
    invite_direct_contacts: bool | None = None
    dm_interval_seconds: int | None = Field(None, ge=10, le=3600)


@router.put("/settings")
def save_settings(payload: SettingsIn, db: DbSession, runtime: Runtime) -> dict[str, Any]:
    try:
        svc.update_settings(db, runtime.settings, **payload.model_dump(exclude_unset=True))
    except svc.InviteError as exc:
        raise _fail(exc) from exc
    return svc.overview(db, runtime.settings)


class LoginIn(StrictInput):
    name: str = Field(min_length=1, max_length=40, pattern=r"^[\w .-]+$")
    phone: str = Field(pattern=r"^\+?[0-9][0-9 ()-]{5,20}$")


class CodeIn(StrictInput):
    name: str = Field(min_length=1, max_length=40)
    code: str = Field(min_length=3, max_length=10)


class PasswordIn(StrictInput):
    name: str = Field(min_length=1, max_length=40)
    password: str = Field(min_length=1, max_length=200)


class NameIn(StrictInput):
    name: str = Field(min_length=1, max_length=40)


@router.post("/accounts/login")
async def login(payload: LoginIn, db: DbSession, runtime: Runtime) -> dict[str, str]:
    try:
        await svc.start_login(db, runtime.settings, payload.name, payload.phone)
    except Exception as exc:  # noqa: BLE001 - Telegram's reason (bad number, flood...) shown to the owner
        raise _fail(exc) from exc
    return {"status": "code_sent"}


@router.post("/accounts/code")
async def code(payload: CodeIn, db: DbSession) -> dict[str, str]:
    try:
        return {"status": await svc.submit_code(db, payload.name, payload.code)}
    except Exception as exc:  # noqa: BLE001
        raise _fail(exc) from exc


@router.post("/accounts/password")
async def password(payload: PasswordIn, db: DbSession) -> dict[str, str]:
    try:
        return {"status": await svc.submit_password(db, payload.name, payload.password)}
    except Exception as exc:  # noqa: BLE001
        raise _fail(exc) from exc


@router.post("/accounts/cancel")
async def cancel(payload: NameIn) -> dict[str, bool]:
    await svc.cancel_login(payload.name)
    return {"cancelled": True}


@router.post("/accounts/{account_id}/disconnect")
def disconnect(account_id: int, db: DbSession, runtime: Runtime) -> dict[str, Any]:
    try:
        svc.disconnect(db, account_id)
    except svc.InviteError as exc:
        raise _fail(exc) from exc
    return svc.overview(db, runtime.settings)


@router.get("/accounts/{account_id}/groups")
async def account_groups(account_id: int, db: DbSession, runtime: Runtime) -> dict[str, Any]:
    try:
        return {"groups": await svc.account_groups(db, runtime.settings, account_id)}
    except Exception as exc:  # noqa: BLE001
        raise _fail(exc) from exc


class GroupIn(StrictInput):
    account_id: int
    chat_id: int
    title: str = Field(min_length=1, max_length=200)
    username: str | None = Field(None, max_length=64)
    allow_posts: bool = False


@router.post("/groups", status_code=status.HTTP_201_CREATED)
def approve_group(payload: GroupIn, db: DbSession, runtime: Runtime) -> dict[str, Any]:
    svc.approve_group(db, payload.account_id, payload.chat_id, payload.title, payload.username, payload.allow_posts)
    return svc.overview(db, runtime.settings)


class GroupPatch(StrictInput):
    allow_posts: bool


@router.patch("/groups/{group_id}")
def patch_group(group_id: int, payload: GroupPatch, db: DbSession, runtime: Runtime) -> dict[str, Any]:
    g = db.get(InviteGroup, group_id)
    if g is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found.")
    g.allow_posts = payload.allow_posts
    db.commit()
    return svc.overview(db, runtime.settings)


@router.delete("/groups/{group_id}")
def remove_group(group_id: int, db: DbSession, runtime: Runtime) -> dict[str, Any]:
    g = db.get(InviteGroup, group_id)
    if g is not None:
        db.delete(g)
        db.commit()
    return svc.overview(db, runtime.settings)


@router.get("/members")
def members(db: DbSession, days: int = Query(14, ge=1, le=90)) -> dict[str, Any]:
    return {"members": svc.members(db, days)}


@router.get("/queue")
def queue(db: DbSession) -> dict[str, Any]:
    return {"queue": svc.queue_view(db)}


@router.post("/poll")
async def poll_now(db: DbSession, runtime: Runtime) -> dict[str, Any]:
    out = await svc.poll(db, runtime.settings)
    return {**out, "auto_started": svc.maybe_auto_send(db, runtime.settings)}


class SendIn(StrictInput):
    dry_run: bool = False
    user_ids: list[int] | None = Field(None, max_length=200)  # manual mode: approve just these people


@router.post("/send")
async def send(payload: SendIn, db: DbSession, runtime: Runtime) -> dict[str, Any]:
    try:
        return svc.start_run(db.get_bind(), runtime.settings, payload.dry_run, set(payload.user_ids) if payload.user_ids else None)
    except svc.InviteError as exc:
        raise _fail(exc) from exc


@router.post("/queue/{user_id}/skip")
def skip(user_id: int, db: DbSession) -> dict[str, Any]:
    try:
        svc.decline(db, user_id)
    except svc.InviteError as exc:
        raise _fail(exc) from exc
    return {"queue": svc.queue_view(db)}


@router.get("/run")
def run() -> dict[str, Any]:
    return svc.run_status()


@router.post("/stop")
def stop() -> dict[str, Any]:
    svc.RT.stop = True
    return svc.run_status()



# --- one-click tool ----------------------------------------------------------------------------------------------------------


@router.get("/one")
def one(db: DbSession, account_id: int, exclude: str = Query("", max_length=4000)) -> dict[str, Any]:
    ids = {int(x) for x in exclude.split(",") if x.strip().lstrip("-").isdigit()}
    try:
        return svc.one_view(db, account_id, ids)
    except svc.InviteError as exc:
        raise _fail(exc) from exc


class OneIn(StrictInput):
    account_id: int
    user_id: int


@router.post("/one/send")
async def one_send(payload: OneIn, db: DbSession, runtime: Runtime) -> dict[str, Any]:
    try:
        return await svc.one_send(db, runtime.settings, payload.account_id, payload.user_id)
    except svc.InviteError as exc:
        raise _fail(exc) from exc


@router.post("/one/skip")
def one_skip(payload: OneIn, db: DbSession) -> dict[str, bool]:
    try:
        svc.one_skip(db, payload.account_id, payload.user_id)
    except svc.InviteError as exc:
        raise _fail(exc) from exc
    return {"skipped": True}


@router.get("/history")
def history(db: DbSession, limit: int = Query(50, ge=1, le=200)) -> dict[str, Any]:
    return {"history": svc.history(db, limit)}
