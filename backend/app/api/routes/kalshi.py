"""Kalshi API.

The bot reports under /api/kalshi/bot/... (public prefix; its own bearer token, made in the admin page). The admin page
reads under /api/kalshi/admin/... (owner login: every non-public /api route). Members link their own Kalshi key under
/api/members/kalshi (member sign-in; while Earn › Kalshi isn't open to everyone, only a signed-in owner can use it).
Read-only throughout: no route sends the bot anything, and nothing places, changes or cancels a Kalshi order.
"""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import Field

from app.api.deps import DbSession
from app.api.routes.members import SessionToken, _current, _require
from app.api.schemas import StrictInput
from app.kalshi import member as links
from app.kalshi import service
from app.kalshi.service import KalshiError
from app.security import admin_auth, rate_limit

router = APIRouter(tags=["kalshi"])
Bearer = Annotated[str | None, Header(alias="Authorization")]


def _fail(e: KalshiError) -> HTTPException:
    return HTTPException(e.status, str(e))


class ReportIn(StrictInput):
    kind: str = Field(max_length=16)
    payload: dict[str, Any]
    bot: dict[str, Any] = Field(default_factory=dict)


@router.post("/kalshi/bot/report")
def bot_report(body: ReportIn, db: DbSession, authorization: Bearer = None) -> dict[str, Any]:
    try:
        bot = service.authorize(db, authorization)
        return service.save_report(db, bot, body.kind, body.payload, body.bot)
    except KalshiError as e:
        raise _fail(e) from e


@router.get("/kalshi/admin/overview")
def admin_overview(db: DbSession) -> dict[str, Any]:
    return {**service.overview(db), "members": links.members_overview(db)}


class MembersOpen(StrictInput):
    open: bool


@router.post("/kalshi/admin/members")
def admin_members_open(body: MembersOpen, db: DbSession) -> dict[str, Any]:
    """Earn › Kalshi for every member (on) or only the signed-in owner (off)."""
    return {"open": links.set_open(db, body.open)}


@router.post("/kalshi/admin/token")
def admin_new_token(db: DbSession) -> dict[str, Any]:
    """A new bot token, shown once. Put it in the bot's .env as PRICECHAT_KALSHI_TOKEN."""
    return {"token": service.new_token(db)}


@router.delete("/kalshi/admin/token")
def admin_disconnect(db: DbSession) -> dict[str, Any]:
    service.disconnect(db)
    return {"connected": False}


# ---------------------------------------------------------------- members (Earn › Kalshi)
def _link_fail(e: links.KalshiLinkError) -> HTTPException:
    return HTTPException(e.status, str(e))


def _gate(db: Any, request: Request) -> bool:
    """→ is the visitor the signed-in owner. Raises 403 while the tab is owner-only and they aren't."""
    owner = admin_auth.current(db, request.cookies.get(admin_auth.COOKIE)) is not None
    if not links.is_open(db) and not owner:
        raise HTTPException(403, "Kalshi on Earn isn't open yet.")
    return owner


@router.get("/members/kalshi", dependencies=[Depends(rate_limit.limit("earn"))])
def member_state(request: Request, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    """Who's linked + what the owner's bot watches (market counts and the fee curve only: never the owner's account)."""
    owner = admin_auth.current(db, request.cookies.get(admin_auth.COOKIE)) is not None
    open_ = links.is_open(db)
    out: dict[str, Any] = {"open": open_, "owner": owner, "available": open_ or owner}
    if open_ or owner:
        out.update(links.state(db, _current(db, pc_session)))
    out["watch"] = service.public_watch(db)
    return out


class ConnectIn(StrictInput):
    env: Literal["production", "demo"]
    key_id: str = Field(max_length=64)
    private_key: str = Field(max_length=links.MAX_PEM)
    confirm: bool  # "Kalshi lets me trade where I live, and this is my own account"


@router.post("/members/kalshi/connect", dependencies=[Depends(rate_limit.limit("earn"))])
async def member_connect(body: ConnectIn, request: Request, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    _gate(db, request)
    m = _require(db, pc_session)
    if not body.confirm:
        raise HTTPException(400, "Please confirm Kalshi lets you use your account where you live, and that it's your own.")
    try:
        return await links.connect(db, m, body.env, body.key_id, body.private_key)
    except links.KalshiLinkError as e:
        raise _link_fail(e) from e


@router.delete("/members/kalshi")
def member_disconnect(db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    """Always allowed (even once the tab closes): forgets the sealed key."""
    links.disconnect(db, _require(db, pc_session))
    return {"link": None}


@router.get("/members/kalshi/activity", dependencies=[Depends(rate_limit.limit("earn"))])
def member_activity(request: Request, db: DbSession) -> dict[str, Any]:
    """The owner's bot, live: its last cycle's steps, the closest baskets and why it didn't (or did) buy. Account figures and
    orders only for the signed-in owner."""
    owner = admin_auth.current(db, request.cookies.get(admin_auth.COOKIE)) is not None
    return {"owner": owner, "activity": service.activity(db, owner)}


@router.get("/members/kalshi/account", dependencies=[Depends(rate_limit.limit("earn"))])
async def member_account(request: Request, db: DbSession, pc_session: SessionToken = None, fresh: bool = False) -> dict[str, Any]:
    _gate(db, request)
    m = _require(db, pc_session)
    try:
        return await links.account(db, m, fresh=fresh)
    except links.KalshiLinkError as e:
        raise _link_fail(e) from e
