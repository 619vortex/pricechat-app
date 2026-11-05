"""Admin › X › News Desk. Owner only (admin_guard: nothing here is on the public list)."""

from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import Field

from app.api.deps import DbSession, Runtime
from app.api.schemas import StrictInput
from app.db.base import utcnow
from app.models.newsdesk import NdPost
from app.newsdesk import engine, sources
from app.newsdesk import telegram as telegram_lane
from app.services.agent_service import get_agent_config

router = APIRouter(prefix="/x/newsdesk", tags=["newsdesk"])


@router.get("")
def overview(db: DbSession, runtime: Runtime) -> dict[str, Any]:
    return engine.overview(db, runtime.settings)


class SettingsInput(StrictInput):
    automation: bool | None = None
    mode: Literal["dry_run", "live"] | None = None
    kill_switch: bool | None = None
    resume: bool | None = None
    target_per_day: int | None = Field(None, ge=1, le=48)
    max_breaking_per_day: int | None = Field(None, ge=0, le=24)
    breaking_gap_minutes: int | None = Field(None, ge=5, le=720)
    sensitivity: Literal["low", "medium", "high"] | None = None
    categories: dict[str, bool] | None = None
    feeds: list[dict[str, Any]] | None = None
    watchlist: list[dict[str, Any]] | None = None


def _feeds_ok(feeds: list[dict[str, Any]]) -> None:
    for f in feeds:
        if not str(f.get("url", "")).startswith("https://") or not str(f.get("name", "")).strip():
            raise HTTPException(400, "Every feed needs a name and an https:// address.")


def _watch_ok(items: list[dict[str, Any]]) -> None:
    for w in items:
        if not w.get("symbol") or not w.get("name") or w.get("group") not in sources.THRESHOLDS or w.get("category") not in sources.CATEGORIES:
            raise HTTPException(400, "Every ticker needs a symbol, a name, a group and a category.")


@router.put("/settings")
def update(payload: SettingsInput, db: DbSession, runtime: Runtime) -> dict[str, Any]:
    s = engine.settings_row(db)
    data = payload.model_dump(exclude_none=True)
    if "feeds" in data:
        _feeds_ok(data["feeds"])
    if "watchlist" in data:
        _watch_ok(data["watchlist"])
    if data.pop("resume", False):
        s.paused_until, s.pause_reason = None, None
    if "categories" in data:
        data["categories"] = {c: bool(data["categories"].get(c, False)) for c in sources.CATEGORIES}
    for k, v in data.items():
        setattr(s, k, v)
    db.commit()
    return engine.overview(db, runtime.settings)


@router.get("/telegram")
def telegram_overview(db: DbSession) -> dict[str, Any]:
    return telegram_lane.overview(db)


class TelegramInput(StrictInput):
    enabled: bool | None = None
    max_per_day: int | None = Field(None, ge=1, le=48)
    gap_minutes: int | None = Field(None, ge=5, le=720)
    resume: bool | None = None


@router.put("/telegram")
def telegram_update(payload: TelegramInput, db: DbSession) -> dict[str, Any]:
    s = telegram_lane.settings_row(db)
    data = payload.model_dump(exclude_none=True)
    if data.pop("resume", False):
        s.state = {k: v for k, v in (s.state or {}).items() if k not in ("paused_until", "problem")}
    for k, v in data.items():
        setattr(s, k, v)
    db.commit()
    return telegram_lane.overview(db)


class TextInput(StrictInput):
    text: str = Field(min_length=1, max_length=280)


def _post(db: DbSession, post_id: int) -> NdPost:
    post = db.get(NdPost, post_id)
    if post is None:
        raise HTTPException(404, "No such post.")
    return post


@router.put("/posts/{post_id}")
def edit(post_id: int, payload: TextInput, db: DbSession) -> dict[str, Any]:
    post = _post(db, post_id)
    if post.status != "queued":
        raise HTTPException(409, "Only a queued post can be edited.")
    result = engine.edit_post(db, post, payload.text)
    if not result.ok:
        raise HTTPException(400, result.reason)
    db.commit()
    return engine.post_out(post)


@router.post("/posts")
def manual(payload: TextInput, db: DbSession) -> dict[str, Any]:
    post, result = engine.new_manual(db, payload.text)
    if post is None:
        raise HTTPException(400, result.reason)
    db.commit()
    return engine.post_out(post)


@router.post("/posts/{post_id}/reject")
def reject(post_id: int, db: DbSession) -> dict[str, Any]:
    post = _post(db, post_id)
    if post.status != "queued":
        raise HTTPException(409, "Only a queued post can be rejected.")
    post.status = "rejected"
    db.commit()
    return engine.post_out(post)


@router.post("/posts/{post_id}/publish")
async def publish_now(post_id: int, db: DbSession, runtime: Runtime) -> dict[str, Any]:
    """Override: this post goes next, now, whatever the spacing says. Every other safety still applies."""
    post = _post(db, post_id)
    if post.status != "queued":
        raise HTTPException(409, "Only a queued post can be published.")
    s = engine.settings_row(db)
    db.commit()
    if s.kill_switch:
        raise HTTPException(409, "The emergency stop is on.")
    await engine.publish(db, s, post, runtime.settings, runtime.x, get_agent_config(db).id, utcnow())
    db.refresh(post)
    return engine.post_out(post)
