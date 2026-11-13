"""Admin › X › News Desk: the editorial desk (owner, 2026-11-10). Story history, the preview queue for the next slot, and the owner's block.
Included by app/api/routes/newsdesk (same owner-only prefix); kept in its own file so the X delivery work in engine.py and newsdesk.py
merges without touching it."""

from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import Field

from app.api.deps import DbSession, Runtime
from app.api.schemas import StrictInput
from app.db.base import utcnow
from app.newsdesk import editorial, engine, events
from app.newsdesk import queue as preview_queue

router = APIRouter(prefix="/editorial", tags=["newsdesk"])


@router.get("/queue")
def queue(db: DbSession) -> dict[str, Any]:
    """The next slot's candidates as the posts would look, with every gate's result and whether each will post."""
    return preview_queue.build(db, utcnow(), engine.settings_row(db).state or {})


@router.get("/preview/{event_id}.jpg")
async def preview(event_id: int, db: DbSession) -> Response:
    got = await preview_queue.preview(db, event_id, utcnow())
    if got is None:
        raise HTTPException(404, "No such candidate.")
    return Response(got[0], media_type="image/jpeg", headers={"Cache-Control": "private, max-age=300"})


@router.get("/history")
def history(db: DbSession) -> dict[str, Any]:
    """Every story of the last 72 hours: posts made, and the later candidates merged or skipped, with why."""
    return {"rule": editorial.RULE, "stories": editorial.history(db, utcnow())}


@router.get("/decisions")
def decisions(db: DbSession) -> dict[str, Any]:
    """Every candidate of the last 24 hours and what the desk did with it, and why: selected, rejected, merged as a repeat, held for
    diversity, expired; plus the slots left empty on purpose."""
    return {"decisions": editorial.decisions(db, utcnow()), "skipped_slots": events.skipped_slots(db)}


@router.get("/monitor/scan")
def monitor_scan(db: DbSession, hours: int = 12) -> dict[str, Any]:
    """World Monitor, live: every story read from the feeds in the last hours and how the desk judges it (verified, held, rejected, why)."""
    from app.newsdesk import monitor

    desk = engine.settings_row(db)
    return monitor.scan(db, utcnow(), hours=max(1, min(hours, 36)), categories=desk.categories or None, desk_state=desk.state or {})


@router.post("/monitor/scan-now")
async def monitor_scan_now(db: DbSession) -> dict[str, Any]:
    """Read every feed now (as the desk does every 5 minutes) and store what's new. It never decides or posts: the desk's own tick does
    that, by its rules. At most once a minute."""
    import httpx

    from app.newsdesk import monitor

    desk = engine.settings_row(db)
    now = utcnow()
    last = (desk.state or {}).get("manual_scan_at")
    if last and (now - __import__("datetime").datetime.fromisoformat(last)).total_seconds() < 60:
        raise HTTPException(429, "Scanned less than a minute ago: try again shortly.")
    desk.state = {**(desk.state or {}), "manual_scan_at": now.isoformat()}
    db.commit()
    async with httpx.AsyncClient() as client:
        got = await engine.collect(db, desk, client, now)
    db.commit()
    return {"new_stories": got.get("new_stories", 0), "feed_problems": got.get("feed_problems", {}),
            **monitor.scan(db, now, categories=desk.categories or None, desk_state=desk.state or {})}


@router.get("/monitor/app")
def monitor_app(db: DbSession) -> dict[str, Any]:
    """The World Monitor desktop app on the owner's PC, as its bridge last reported it: connected or not, each feed's health, and the
    signals (earthquakes, instability, military air activity, FAA, climate, cyber). Read-only; nothing here posts."""
    from app.newsdesk import wm_app

    return wm_app.view(db, utcnow())


@router.get("/monitor/upcoming")
async def monitor_upcoming(db: DbSession, runtime: Runtime) -> dict[str, Any]:
    """What the desk is about to post: the exact Telegram caption, X post, links and picture, in the order they would go."""
    from app.newsdesk import monitor

    return await monitor.upcoming(db, utcnow(), runtime.settings, engine.settings_row(db).state or {})


class BlockInput(StrictInput):
    event_id: int
    block: bool
    reason: str = Field("", max_length=200)


@router.post("/block")
def block(payload: BlockInput, db: DbSession) -> dict[str, Any]:
    """Block (or unblock) a queued post: never released while blocked; if already released, neither platform delivers it."""
    ev = preview_queue.block(db, payload.event_id, payload.block, payload.reason, utcnow())
    if ev is None:
        raise HTTPException(404, "No such event.")
    return events.event_out(ev)


# --- the media engine (owner, 2026-11-13): editorial scores, rejection reasons, headline variants, performance ------------------------


@router.get("/scorecards")
def scorecards(db: DbSession, hours: int = 48, limit: int = 60) -> dict[str, Any]:
    """Every news candidate of the last hours with its editorial scorecard (five dimensions, beat, audience points), the five ranked
    hooks with their accuracy problems, the debate question, the picture plan, the accuracy gate's result and what the desk decided."""
    from app.newsdesk import scorecard

    now = utcnow()
    p = events.policy(engine.settings_row(db).state or {})
    return {"minimum": p.editorial_min, "dimensions": list(scorecard.DIMENSIONS), "beats": scorecard.BEAT_NAMES,
            "stories": scorecard.overview(db, now, hours=max(1, min(hours, 168)), limit=max(1, min(limit, 200)), minimum=p.editorial_min)}


@router.get("/performance")
def performance(db: DbSession, runtime: Runtime) -> dict[str, Any]:
    """Engagement on Telegram and X, normalized per platform, by beat, hook strategy, picture format and question; what the learner adds;
    which metric sources are connected (Telegram needs the owner's Telegram account; the Bot API has no view counts)."""
    from app.newsdesk import engagement

    return engagement.performance(db, utcnow(), runtime.settings)


class QualityInput(StrictInput):
    editorial_min: int = Field(ge=0, le=100)


@router.put("/quality")
def quality(payload: QualityInput, db: DbSession) -> dict[str, Any]:
    """The editorial bar: a news story under this scorecard total is not posted and an empty slot is skipped. 0 turns the bar off (the
    factual-safety and accuracy gates stay on regardless)."""
    desk = engine.settings_row(db)
    state = dict(desk.state or {})
    state["policy"] = {**(state.get("policy") or {}), "editorial_min": int(payload.editorial_min)}
    desk.state = state
    db.commit()
    return {"editorial_min": events.policy(state).editorial_min}
