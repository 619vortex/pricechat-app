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
