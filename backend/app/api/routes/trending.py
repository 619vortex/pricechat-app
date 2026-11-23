"""Admin › X › Trending Topics. Owner only (admin_guard: nothing here is on the public list)."""

from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import Field

from app.api.deps import DbSession, Runtime
from app.api.schemas import StrictInput
from app.db.base import utcnow
from app.models.trending import TtPost, TtTopic
from app.services.agent_service import get_agent_config
from app.trending import engine, rank, sources

router = APIRouter(prefix="/x/trending", tags=["trending"])


@router.get("")
def overview(db: DbSession, runtime: Runtime) -> dict[str, Any]:
    return engine.overview(db, runtime.settings)


class SettingsInput(StrictInput):
    enabled: bool | None = None
    kill_switch: bool | None = None  # Trending's own stop: no trend post goes out (scanning and suggestions go on)
    mode: Literal["approval", "automatic"] | None = None
    confirm_automatic: bool | None = None  # switching to automatic needs this, sent only after the owner's confirmation
    live: bool | None = None
    confirm_live: bool | None = None
    hot_score: float | None = Field(None, ge=30, le=100)
    suggest_score: float | None = Field(None, ge=10, le=100)
    max_per_day: int | None = Field(None, ge=0, le=24)
    gap_minutes: int | None = Field(None, ge=15, le=1440)
    topic_cooldown_hours: int | None = Field(None, ge=1, le=168)
    newsdesk_gap_minutes: int | None = Field(None, ge=0, le=240)
    shared_daily_cap: int | None = Field(None, ge=1, le=96)
    categories: dict[str, bool] | None = None
    sources: dict[str, dict[str, Any]] | None = None


@router.put("/settings")
def update(payload: SettingsInput, db: DbSession, runtime: Runtime) -> dict[str, Any]:
    s = engine.settings_row(db)
    data = payload.model_dump(exclude_none=True)
    confirm_auto, confirm_live = data.pop("confirm_automatic", False), data.pop("confirm_live", False)
    if data.get("mode") == "automatic" and (s.mode != "automatic" or s.auto_confirmed_at is None):
        if not confirm_auto:
            raise HTTPException(400, "Automatic publishing needs an explicit confirmation.")
        s.auto_confirmed_at = utcnow()
    if data.get("mode") == "approval":
        s.auto_confirmed_at = None
    if data.get("live") is True and not s.live and not confirm_live:
        raise HTTPException(400, "Going live needs an explicit confirmation.")
    if "suggest_score" in data or "hot_score" in data:
        if float(data.get("suggest_score", s.suggest_score)) > float(data.get("hot_score", s.hot_score)):
            raise HTTPException(400, "The suggestion threshold can't be above the hot threshold.")
    if "categories" in data:
        data["categories"] = {c: bool(data["categories"].get(c, True)) for c in rank.CATEGORIES}
    if "sources" in data:
        merged = sources.merged(s.sources)
        for k, v in data["sources"].items():
            if k not in merged:
                raise HTTPException(400, f"Unknown source: {k}")
            allowed = set(sources.DEFAULT_SOURCES[k])
            if k == "x_accounts" and v.get("on") and not merged[k].get("on"):
                raise HTTPException(400, "Reading X accounts is paid: turn it on in Price Chat Terminal › X Intelligence, which shows the price and asks you to confirm.")
            if set(v) - allowed:
                raise HTTPException(400, f"Unknown option for {k}: {', '.join(sorted(set(v) - allowed))}")
            merged[k].update(v)
        data["sources"] = merged
    for k, v in data.items():
        setattr(s, k, v)
    db.commit()
    return engine.overview(db, runtime.settings)


@router.post("/scan")
async def scan_now(db: DbSession, runtime: Runtime) -> dict[str, Any]:
    """Run one tick now (reads the sources that are due, scores, suggests; publishes only what the mode allows)."""
    result = await engine.tick(db, runtime.settings, runtime.x, get_agent_config(db).id, utcnow())
    return {"result": result, **engine.overview(db, runtime.settings)}


def _post(db: DbSession, post_id: int) -> TtPost:
    post = db.get(TtPost, post_id)
    if post is None:
        raise HTTPException(404, "No such tweet.")
    return post


@router.post("/posts/{post_id}/approve")
async def approve(post_id: int, db: DbSession, runtime: Runtime) -> dict[str, Any]:
    post = _post(db, post_id)
    try:
        outcome = await engine.approve(db, runtime.settings, runtime.x, get_agent_config(db).id, post, utcnow())
    except ValueError as e:
        raise HTTPException(409, str(e)) from None
    db.refresh(post)
    return {"outcome": outcome, "post": engine.post_out(post)}


@router.post("/posts/{post_id}/reject")
def reject(post_id: int, db: DbSession) -> dict[str, Any]:
    post = _post(db, post_id)
    if post.status not in ("pending", "queued", "held"):
        raise HTTPException(409, "Only a tweet that hasn't gone out can be rejected.")
    post.status = "rejected"
    db.commit()
    return engine.post_out(post)


class ReleaseInput(StrictInput):
    action: Literal["retry", "text"]


@router.post("/posts/{post_id}/release")
def release(post_id: int, payload: ReleaseInput, db: DbSession) -> dict[str, Any]:
    """Owner, 2026-11-23: a tweet held for having no picture and no link: look again, or post it as words alone (still needs Approve)."""
    post = _post(db, post_id)
    try:
        engine.release_held(db, post, payload.action)
    except ValueError as e:
        raise HTTPException(409, str(e)) from None
    return engine.post_out(post)


class TextInput(StrictInput):
    text: str = Field(min_length=1, max_length=280)


@router.put("/posts/{post_id}")
def edit(post_id: int, payload: TextInput, db: DbSession) -> dict[str, Any]:
    post = _post(db, post_id)
    try:
        result = engine.edit(db, post, payload.text)
    except ValueError as e:
        raise HTTPException(409, str(e)) from None
    if not result["ok"]:
        raise HTTPException(400, result["reason"])
    return engine.post_out(post)


@router.post("/topics/{topic_id}/dismiss")
def dismiss(topic_id: int, db: DbSession) -> dict[str, Any]:
    t = db.get(TtTopic, topic_id)
    if t is None:
        raise HTTPException(404, "No such topic.")
    t.status = "dismissed"
    db.commit()
    return {"id": t.id, "status": t.status}
