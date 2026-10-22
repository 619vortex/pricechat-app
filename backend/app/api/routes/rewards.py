"""Kiwi Passport rank rewards and Vortex Realm API.

Member routes live under /api/members/... (public prefix; the member is whoever the pc_session cookie says, never an id in
the request). Admin routes live under /api/rewards-admin/... (the owner-login middleware guards every non-public /api route).
Every gate is enforced in app.members.rewards / app.members.realm, not here.
"""

import gzip
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Cookie, HTTPException, Query, Request, Response, WebSocket, status
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import Field
from sqlalchemy.orm import Session

from app.api.deps import DbSession
from app.api.routes.members import SessionToken, _current, _require
from app.api.schemas import StrictInput
from app.members import realm, realm_online, rewards
from app.members.rewards import RewardError
from app.security import admin_auth

router = APIRouter(tags=["rewards"])
AdminToken = Annotated[str | None, Cookie(alias=admin_auth.COOKIE)]
ACTOR = "dashboard"


def _fail(e: RewardError) -> HTTPException:
    return HTTPException(e.status, str(e))


# --- member -------------------------------------------------------------------------------------------------------


@router.get("/members/rewards")
def my_rewards(db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    """Every rank as a milestone: the reward, locked / unlocked, claim state. Signed out: the catalog only."""
    out = rewards.overview(db, _current(db, pc_session))
    db.commit()  # first call seeds the catalog
    return out


@router.get("/members/rewards/beginner")
def beginner(db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    try:
        return rewards.beginner_content(db, _require(db, pc_session))
    except RewardError as e:
        raise _fail(e) from e


class ProfileReward(StrictInput):
    display_name: str | None = Field(None, max_length=64)
    accent: str | None = Field(None, max_length=16)
    clear_accent: bool = False


@router.patch("/members/rewards/profile")
def profile_reward(payload: ProfileReward, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    member = _require(db, pc_session)
    try:
        if payload.display_name is not None:
            rewards.set_display_name(db, member, payload.display_name)
        if payload.accent is not None or payload.clear_accent:
            rewards.set_accent(db, member, None if payload.clear_accent else payload.accent)
    except RewardError as e:
        raise _fail(e) from e
    return rewards.overview(db, member)["profile"]


class ClaimInput(StrictInput):
    note: str = Field("", max_length=500)


@router.post("/members/rewards/knight/claim")
def claim_knight(payload: ClaimInput, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    try:
        return {"claim": rewards.claim_out(rewards.claim_knight(db, _require(db, pc_session), payload.note))}
    except RewardError as e:
        raise _fail(e) from e


@router.post("/members/rewards/royal/request")
def request_vip(payload: ClaimInput, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    try:
        return {"claim": rewards.claim_out(rewards.request_vip(db, _require(db, pc_session), payload.note))}
    except RewardError as e:
        raise _fail(e) from e


@router.get("/members/rewards/royal")
def vip(db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    try:
        return rewards.vip_access(db, _require(db, pc_session))
    except RewardError as e:
        raise _fail(e) from e


# --- Vortex Realm (Warrior Kiwi) --------------------------------------------------------------------------------


def _staff(db: Session, admin_token: str | None) -> bool:
    return admin_auth.current(db, admin_token) is not None


@router.get("/members/realm")
def realm_status(db: DbSession, pc_session: SessionToken = None, pc_admin: AdminToken = None) -> dict[str, Any]:
    staff = _staff(db, pc_admin)
    member = _current(db, pc_session)
    if member is None and not staff:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Please sign in.")
    return realm.status(db, member, staff)


@router.get("/members/realm/online/ticket")
def realm_ticket(request: Request, db: DbSession, pc_session: SessionToken = None, pc_admin: AdminToken = None) -> dict[str, Any]:
    """Vortex Realm online: a ticket for the game's WebSocket, only for players who've unlocked the game. The name others
    see is the Passport username (as on the leaderboards), never anything private."""
    from app.config.settings import get_settings

    member, staff = _current(db, pc_session), _staff(db, pc_admin)
    if not realm.allowed(db, member, staff):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Vortex Realm unlocks at Warrior Kiwi.")
    name = member.username if member else "Price Chat"
    ws_url = get_settings().realm_ws_url or f"{'wss' if request.url.scheme == 'https' else 'ws'}://{request.url.netloc}/api/members/realm/ws"
    return {"ticket": realm_online.hub.mint(member.id if member else None, name), "ws_url": ws_url, "name": name,
            "online": realm_online.hub.online()}


@router.websocket("/members/realm/ws")
async def realm_ws(ws: WebSocket, ticket: str = "") -> None:
    """The live Realm: positions, looks, chat and parties, relayed by app.members.realm_online (nothing is stored)."""
    from app.config.settings import get_settings

    origin = (ws.headers.get("origin") or "").rstrip("/")
    s = get_settings()
    ok_origins = {o.rstrip("/") for o in s.cors_origins} | {s.members_public_url.rstrip("/")}
    t = realm_online.hub.redeem(ticket)
    if t is None or (origin and origin != "null" and origin not in ok_origins):
        await ws.close(code=4401)
        return
    await ws.accept()
    player = realm_online.hub.join(t)
    if player is None:
        await ws.send_text('{"t":"sys","m":"The Realm is full right now. Try again soon."}')
        await ws.close(code=4429)
        return
    await realm_online.serve(ws, player)


@router.get("/members/realm/play/{name}")
def realm_file(name: str, request: Request, db: DbSession, pc_session: SessionToken = None, pc_admin: AdminToken = None) -> Response:
    """The game's files, only for players who've unlocked it. Stored gzipped and sent as-is (Content-Encoding: gzip)."""
    if not realm.allowed(db, _current(db, pc_session), _staff(db, pc_admin)):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Vortex Realm unlocks at Warrior Kiwi.")
    db.close()  # get_db closes only after the response is sent: don't hold a pooled connection while the game (MBs) streams out
    path = realm.game_file(name)
    if path is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found.")
    media = realm.TYPES[Path(name).suffix]
    # always revalidate (a game update reaches players on their next load), but an unchanged file costs only a 304
    tag = realm.etag(path)
    headers = {"Cache-Control": "private, no-cache", "Vary": "Accept-Encoding", "ETag": tag}
    if tag in request.headers.get("if-none-match", ""):
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
    if "gzip" in request.headers.get("accept-encoding", ""):
        return FileResponse(path, media_type=media, headers={**headers, "Content-Encoding": "gzip"})

    def unzip() -> Iterator[bytes]:
        with gzip.open(path, "rb") as f:
            while chunk := f.read(1 << 20):
                yield chunk
    return StreamingResponse(unzip(), media_type=media, headers=headers)


# --- admin (owner login) ------------------------------------------------------------------------------------------


@router.get("/rewards-admin/overview")
def admin_overview(db: DbSession) -> dict[str, Any]:
    from sqlalchemy import select

    from app.models.rewards import BeginnerContent

    rows = rewards.reward_rows(db)
    out = {
        "rewards": [{"rank_key": r.rank_key, "kind": r.kind, "title": r.title, "description": r.description, "enabled": r.enabled}
                    for r in (rows[d["rank_key"]] for d in rewards.REWARDS if d["rank_key"] in rows)],
        "settings": rewards.settings_out(db),
        "knight": rewards.knight_state(db),
        "content": [rewards.content_out(c, admin=True) for c in db.scalars(select(BeginnerContent)
                                                                           .order_by(BeginnerContent.position, BeginnerContent.id))],
    }
    db.commit()
    return out


class RewardUpdate(StrictInput):
    title: str | None = Field(None, max_length=96)
    description: str | None = Field(None, max_length=2000)
    enabled: bool | None = None


@router.put("/rewards-admin/rewards/{rank_key}")
def admin_reward(rank_key: str, payload: RewardUpdate, db: DbSession) -> dict[str, Any]:
    try:
        r = rewards.update_reward(db, ACTOR, rank_key, payload.model_dump(exclude_unset=True))
    except RewardError as e:
        raise _fail(e) from e
    return {"rank_key": r.rank_key, "title": r.title, "description": r.description, "enabled": r.enabled}


class SettingsUpdate(StrictInput):
    knight_status: Literal["NOT_CONFIGURED", "ACTIVE", "PAUSED"] | None = None
    knight_amount_usd: int | None = Field(None, ge=0, le=1000)
    knight_terms: str | None = Field(None, max_length=5000)
    knight_regions: str | None = Field(None, max_length=2000)
    knight_expires_at: datetime | None = None
    knight_budget: int | None = Field(None, ge=0, le=100000)
    vip_invite_url: str | None = Field(None, max_length=300)
    vip_description: str | None = Field(None, max_length=2000)
    arena_kills_per_xp: int | None = Field(None, ge=1, le=1000)
    arena_run_xp_cap: int | None = Field(None, ge=0, le=500)
    arena_daily_xp_cap: int | None = Field(None, ge=0, le=1000)
    arena_min_run_seconds: int | None = Field(None, ge=0, le=3600)
    arena_runs_per_hour: int | None = Field(None, ge=1, le=120)


@router.put("/rewards-admin/settings")
def admin_settings(payload: SettingsUpdate, db: DbSession) -> dict[str, Any]:
    try:
        return rewards.update_settings(db, ACTOR, payload.model_dump(exclude_unset=True))
    except RewardError as e:
        raise _fail(e) from e


class ContentInput(StrictInput):
    title: str | None = Field(None, max_length=160)
    description: str | None = Field(None, max_length=5000)
    video_url: str | None = Field(None, max_length=500)
    thumbnail_url: str | None = Field(None, max_length=500)
    guide_text: str | None = Field(None, max_length=20000)
    position: int | None = Field(None, ge=0, le=10000)
    published: bool | None = None


@router.post("/rewards-admin/content")
def admin_content_new(payload: ContentInput, db: DbSession) -> dict[str, Any]:
    try:
        return rewards.content_out(rewards.save_content(db, ACTOR, payload.model_dump(exclude_unset=True)), admin=True)
    except RewardError as e:
        raise _fail(e) from e


@router.put("/rewards-admin/content/{item_id}")
def admin_content_edit(item_id: int, payload: ContentInput, db: DbSession) -> dict[str, Any]:
    try:
        return rewards.content_out(rewards.save_content(db, ACTOR, payload.model_dump(exclude_unset=True), item_id), admin=True)
    except RewardError as e:
        raise _fail(e) from e


@router.delete("/rewards-admin/content/{item_id}")
def admin_content_delete(item_id: int, db: DbSession) -> dict[str, Any]:
    try:
        rewards.delete_content(db, ACTOR, item_id)
    except RewardError as e:
        raise _fail(e) from e
    return {"deleted": True}


@router.get("/rewards-admin/claims")
def admin_claims(db: DbSession, reward: Literal["knight", "royal"] | None = None, status: str | None = None) -> dict[str, Any]:
    return {"claims": rewards.claims(db, reward, status)}


class Decision(StrictInput):
    note: str = Field("", max_length=2000)
    reference: str | None = Field(None, max_length=200)


@router.post("/rewards-admin/claims/{public_id}/{action}")
def admin_decide(public_id: str, action: Literal["approve", "fulfill", "reject", "revoke", "restore", "reopen"], payload: Decision,
                 db: DbSession) -> dict[str, Any]:
    try:
        c = rewards.decide(db, public_id, action, ACTOR, payload.note, payload.reference)
    except RewardError as e:
        raise _fail(e) from e
    return {"claim": rewards.claim_out(c)}


@router.get("/rewards-admin/history")
def admin_history(db: DbSession, limit: int = Query(200, ge=1, le=1000)) -> dict[str, Any]:
    return {"items": rewards.history(db, limit)}
