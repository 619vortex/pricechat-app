"""Admin › Price Chat Terminal. Owner only (admin_guard: nothing here is on the public list)."""

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import Field

from app.api.deps import DbSession, Runtime
from app.api.schemas import StrictInput
from app.models.trending import TtTopic
from app.services.agent_service import get_agent_config
from app.terminal import feed

router = APIRouter(prefix="/terminal", tags=["terminal"])


@router.get("")
def overview(db: DbSession, runtime: Runtime, q: str = Query("", max_length=120), lane: str = Query("", max_length=20),
             category: str = Query("", max_length=20), min_viral: float = Query(0, ge=0, le=100), min_evidence: float = Query(0, ge=0, le=100),
             hours: int = Query(24, ge=1, le=72)) -> dict[str, Any]:
    return feed.overview(db, runtime.settings, q=q, lane=lane, category=category, min_viral=min_viral, min_evidence=min_evidence, hours=hours)


def _topic(db: DbSession, topic_id: int) -> TtTopic:
    t = db.get(TtTopic, topic_id)
    if t is None:
        raise HTTPException(404, "No such story.")
    return t


@router.get("/stories/{topic_id}")
def story(topic_id: int, db: DbSession) -> dict[str, Any]:
    return feed.story_detail(db, _topic(db, topic_id))


@router.post("/stories/{topic_id}/hooks")
async def hooks(topic_id: int, db: DbSession, runtime: Runtime) -> dict[str, Any]:
    t = _topic(db, topic_id)
    try:
        await feed.model_hooks(db, runtime.settings, t)
    except ValueError as e:
        raise HTTPException(409, str(e)) from None
    return feed.story_detail(db, t)


class DraftInput(StrictInput):
    text: str | None = Field(None, max_length=280)


@router.post("/stories/{topic_id}/draft")
async def draft(topic_id: int, payload: DraftInput, db: DbSession, runtime: Runtime) -> dict[str, Any]:
    t = _topic(db, topic_id)
    try:
        await feed.draft(db, runtime.settings, t, payload.text)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    return feed.story_detail(db, t)


@router.post("/stories/{topic_id}/publish")
async def publish(topic_id: int, payload: DraftInput, db: DbSession, runtime: Runtime) -> dict[str, Any]:
    t = _topic(db, topic_id)
    try:
        result = await feed.publish_now(db, runtime.settings, runtime.x, get_agent_config(db).id, t, payload.text)
    except ValueError as e:
        raise HTTPException(409, str(e)) from None
    return {**result, "story": feed.story_detail(db, t)}


class RejectInput(StrictInput):
    reason: str = Field("", max_length=300)


@router.post("/stories/{topic_id}/reject")
def reject(topic_id: int, payload: RejectInput, db: DbSession) -> dict[str, Any]:
    t = _topic(db, topic_id)
    feed.reject(db, t, payload.reason)
    return feed.story_detail(db, t)


class StopInput(StrictInput):
    which: Literal["trending", "newsdesk"]
    on: bool


@router.post("/stop")
def stop(payload: StopInput, db: DbSession, runtime: Runtime) -> dict[str, Any]:
    """The emergency stops, from the Terminal: Trending's stop or the News Desk's emergency stop (which stops both desks' X posting)."""
    from app.models.newsdesk import NdSettings
    from app.trending import engine

    if payload.which == "trending":
        engine.settings_row(db).kill_switch = payload.on
    else:
        nd = db.get(NdSettings, 1)
        if nd is None:
            raise HTTPException(409, "The News Desk isn't set up.")
        nd.kill_switch = payload.on
    db.commit()
    return feed.controls(db, engine.settings_row(db))
