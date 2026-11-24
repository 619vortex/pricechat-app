"""Admin › Price Chat Terminal. Owner only (admin_guard: nothing here is on the public list)."""

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import Field
from sqlalchemy import select

from app.api.deps import DbSession, Runtime
from app.api.schemas import StrictInput
from app.db.base import utcnow
from app.models.trending import TtTopic
from app.services.agent_service import get_agent_config
from app.terminal import feed

router = APIRouter(prefix="/terminal", tags=["terminal"])


def _part(fn: Any) -> Any:
    try:
        return fn()
    except Exception as e:  # noqa: BLE001 - one part failing never blanks the page
        return {"error": type(e).__name__}


@router.get("/validation")
def validation_progress(db: DbSession, runtime: Runtime) -> dict[str, Any]:
    """Settings › Validation (owner, 2026-11-19): the 30-candidate validation round (candidates judged, pictures audited, flags, the report),
    the AI editor's shadow comparison and cost estimate, the owner's feedback, story memory and leads, and how posts performed."""
    from app.newsdesk import chief, feedback, memory, performance, validation

    return {**validation.progress(db), "chief": _part(lambda: chief.evaluation(db, settings=runtime.settings)), "feedback": _part(lambda: feedback.summary(db)),
            "memory": _part(lambda: memory.summary(db)), "performance": _part(lambda: performance.measure(db))}


class FeedbackInput(StrictInput):
    target: str = Field(..., pattern=r"^(event|topic):\d{1,10}$")
    verdict: Literal["great", "boring", "wrong_image", "missed"]
    note: str = Field("", max_length=500)


@router.post("/feedback")
def give_feedback(payload: FeedbackInput, db: DbSession) -> dict[str, Any]:
    """One click: Great / Boring / Wrong image / Missed story, stored against the event or topic with what was decided and published."""
    from app.newsdesk import feedback

    try:
        return feedback.give(db, payload.target, payload.verdict, payload.note)
    except feedback.FeedbackError as e:
        raise HTTPException(409, str(e)) from None


@router.get("/insight")
def insight(db: DbSession, target: str = Query(..., pattern=r"^(event|topic):\d{1,10}$")) -> dict[str, Any]:
    """The story panel's extras: the owner's feedback, the story so far across every lane, its X performance, the AI editor's verdict."""
    from app.models.newsdesk import NdValidation
    from app.newsdesk import feedback, memory, performance

    try:
        snap = feedback.snapshot(db, target)
    except feedback.FeedbackError as e:
        raise HTTPException(404, str(e)) from None
    ch = db.scalar(select(NdValidation).where(NdValidation.kind == "chief", NdValidation.ref == target)) if target.startswith("event:") else None
    cd = ch.data if ch is not None else {}
    return {"feedback": feedback.current(db, target)["verdicts"], "published": bool(snap.get("published")),
            "picture": bool((snap.get("picture") or {}).get("kind")), "memory": memory.story_of(db, str(snap.get("title") or "")),
            "performance": _part(lambda: performance.of_target(db, target)),
            "chief": {k: cd.get(k) for k in ("status", "verdict", "why_care", "best_angle", "problems")} if ch is not None else None}


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


@router.get("/stories/{topic_id}/picture")
async def story_picture(topic_id: int, db: DbSession, fresh: bool = Query(False)) -> dict[str, Any]:
    """The picture a post of this story would carry, where it comes from, its licence and credit (reads only; sends nothing)."""
    from app.terminal import pictures

    t = _topic(db, topic_id)
    image, meta = await pictures.for_topic(db, t, utcnow(), fresh=fresh)
    return pictures.out(image, meta)


@router.get("/events/{event_id}/picture")
async def event_picture(event_id: int, db: DbSession) -> dict[str, Any]:
    """A News Desk event's picture: what its post used (or will use), or why it is held."""
    from app.terminal import pictures

    got = await pictures.for_event(db, event_id, utcnow())
    if got is None:
        raise HTTPException(404, "No such event.")
    return pictures.out(*got)


class HeldInput(StrictInput):
    action: Literal["retry", "text", "skip"]


@router.post("/events/{event_id}/held")
def release_held(event_id: int, payload: HeldInput, db: DbSession) -> dict[str, Any]:
    """The owner's decision on a post held because it had no picture: look again, send the words alone, or skip it."""
    try:
        return feed.release_held(db, event_id, payload.action, utcnow())
    except ValueError as e:
        raise HTTPException(409, str(e)) from None


class TelegramInput(StrictInput):
    text: str | None = Field(None, max_length=280)
    text_ok: bool = False  # the owner chose to send the words without a picture (never the default)


@router.post("/stories/{topic_id}/telegram")
async def telegram(topic_id: int, payload: TelegramInput, db: DbSession, runtime: Runtime) -> dict[str, Any]:
    t = _topic(db, topic_id)
    try:
        result = await feed.publish_telegram(db, runtime.settings, t, payload.text, text_ok=payload.text_ok)
    except ValueError as e:
        raise HTTPException(409, str(e)) from None
    except Exception as e:  # noqa: BLE001 - Telegram not configured or unreachable: say so, nothing was sent
        raise HTTPException(409, f"Not sent: {e}") from None
    return {**result, "story": feed.story_detail(db, t)}


class AutomationInput(StrictInput):
    on: bool


@router.post("/automation")
def automation(payload: AutomationInput, db: DbSession) -> dict[str, Any]:
    """Pause or resume automatic publishing (News Desk automation + Trending's automatic mode). Never touches live / dry run or the stops."""
    return feed.set_automation(db, payload.on)


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
    """The emergency stops, from the Terminal: Trending's stop, or the News Desk's emergency stop (all automatic posting: the Telegram channel,
    the News Desk on X and Trending on X; app/newsdesk/engine.emergency_stop)."""
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


# --- X Intelligence -------------------------------------------------------------------------------------------------------------------


@router.get("/x")
def x_overview(db: DbSession, runtime: Runtime) -> dict[str, Any]:
    return feed.x_overview(db, runtime.settings)


class XSettingsInput(StrictInput):
    on: bool | None = None
    confirm_paid: bool = False  # turning paid reads on needs this, sent only after the owner confirmed the price
    daily_usd: float | None = Field(None, ge=0.1, le=50)
    max_posts: int | None = Field(None, ge=10, le=100)


@router.put("/x/settings")
def x_settings(payload: XSettingsInput, db: DbSession, runtime: Runtime) -> dict[str, Any]:
    try:
        feed.x_settings(db, on=payload.on, confirm_paid=payload.confirm_paid, daily_usd=payload.daily_usd, max_posts=payload.max_posts)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    return feed.x_overview(db, runtime.settings)


class WatchEntry(StrictInput):
    handle: str = Field(min_length=1, max_length=16)
    group: str = Field(max_length=20)
    tier: Literal["A", "B", "C"]
    note: str = Field("", max_length=120)
    on: bool = True


class WatchlistInput(StrictInput):
    accounts: list[WatchEntry] = Field(max_length=300)


@router.put("/x/watchlist")
def x_watchlist(payload: WatchlistInput, db: DbSession, runtime: Runtime) -> dict[str, Any]:
    feed.x_settings(db, accounts=[a.model_dump() for a in payload.accounts])
    return feed.x_overview(db, runtime.settings)


@router.post("/x/read")
async def x_read_now(db: DbSession, runtime: Runtime) -> dict[str, Any]:
    """Read the watchlist now (only when X Intelligence is on: it's paid), then score."""
    from app.trending import engine as tt, sources as tsources

    if not (tsources.merged(tt.settings_row(db).sources).get("x_accounts") or {}).get("on"):
        raise HTTPException(409, "X Intelligence is off: reading X accounts is paid. Turn it on first.")
    await tt.tick(db, runtime.settings, runtime.x, get_agent_config(db).id, utcnow(), only={"x_accounts"})
    return feed.x_overview(db, runtime.settings)
