"""Short Market API (docs/SHORT_MARKET.md).

/api/short-market/*          owner only (the admin gate): story ideas, shorts, download
/api/short-market/worker/*   AI workers, with the same bearer token they use for Brand Manager

No social network is involved: a finished short is downloaded and posted by hand.
"""

import json
import re
from datetime import timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, File, Form, Header, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import Response
from pydantic import Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import DbSession, Runtime
from app.api.routes.brand_manager import ClaimIn, FailIn, ProgressIn, ScriptIn, _stream
from app.api.schemas import StrictInput
from app.brand_manager import queue as bm_queue
from app.brand_manager.store import MediaStore
from app.db.base import utcnow
from app.models.brand_manager import BmWorker
from app.models.short_market import SmIdea, SmShort
from app.short_market import defaults, ideas, queue

router = APIRouter(tags=["short-market"])
P = "/short-market"
OPEN = ("queued", "rendering")


def _transport(runtime: Any) -> Any:
    return getattr(runtime, "bm_transport", None)


def _short(db: Session, short_id: int) -> SmShort:
    s = db.get(SmShort, short_id)
    if s is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found.")
    return s


def caption(s: SmShort) -> str:
    """What the owner pastes under the video: title, description, hashtags."""
    sc = s.script or {}
    return "\n\n".join(p for p in (str(sc.get("title") or "").strip(), str(sc.get("description") or "").strip(),
                                   " ".join(sc.get("hashtags") or [])) if p)


def short_out(s: SmShort, detail: bool = False) -> dict[str, Any]:
    sc = s.script or {}
    out = {"id": s.id, "topic": s.topic, "category": s.category, "category_label": defaults.CATEGORIES.get(s.category, {}).get("label", s.category),
           "style": s.style, "style_label": defaults.STYLES.get(s.style, {}).get("label", s.style), "voice": s.voice, "seconds": s.seconds, "quality": s.quality,
           "status": s.status, "stages": s.stages, "title": sc.get("title"), "hashtags": sc.get("hashtags") or [], "duration": s.duration,
           "size_bytes": s.size_bytes, "has_video": bool(s.video_key), "has_thumb": bool(s.thumb_key), "error": s.error,
           "generation": s.generation, "attempts": s.attempts, "created_at": s.created_at.isoformat(),
           "downloaded_at": s.downloaded_at.isoformat() if s.downloaded_at else None}
    if detail:
        out.update({"script": sc, "brief": s.brief, "render_info": s.render_info, "caption": caption(s),
                    "sources": sc.get("sources") or s.brief.get("sources") or []})
    return out


# --- overview ------------------------------------------------------------------------------------------------------------


@router.get(f"{P}/overview")
def overview(db: DbSession) -> dict[str, Any]:
    now = utcnow()
    workers = [bm_queue.worker_out(w, now) for w in db.scalars(select(BmWorker).where(BmWorker.enabled.is_(True)).order_by(BmWorker.id))]
    online = [w for w in workers if w["online"]]
    info = online[0]["info"] if online else {}
    counts = dict(db.execute(select(SmShort.status, func.count(SmShort.id)).group_by(SmShort.status)).all())
    return {
        "worker": {"registered": len(workers), "online": bool(online), "name": online[0]["name"] if online else (workers[0]["name"] if workers else None),
                   "last_seen_at": max((w["last_seen_at"] for w in workers if w["last_seen_at"]), default=None),
                   "video_ai": info.get("video_ai") or None, "voices": info.get("tts") or [], "writer": (info.get("llm") or [None])[0]},
        "counts": {"queued": counts.get("queued", 0), "rendering": counts.get("rendering", 0), "ready": counts.get("ready", 0),
                   "failed": counts.get("failed", 0)},
        "options": {"categories": [{"key": k, "label": v["label"]} for k, v in defaults.CATEGORIES.items()],
                    "styles": [{"key": k, "label": v["label"], "accent": v["accent"]} for k, v in defaults.STYLES.items()],
                    "voices": [{"key": k, "label": v} for k, v in defaults.VOICES.items()], "lengths": list(defaults.LENGTHS),
                    "qualities": [{"key": k, **v} for k, v in defaults.QUALITIES.items()],
                    "defaults": {"style": defaults.DEFAULT_STYLE, "voice": defaults.DEFAULT_VOICE, "seconds": defaults.DEFAULT_LENGTH,
                                 "quality": defaults.DEFAULT_QUALITY}},
        "pipeline": [{"key": k, "label": label, "where": where} for k, label, where in defaults.STAGES],
        "max_open": defaults.MAX_OPEN,
    }


# --- ideas ---------------------------------------------------------------------------------------------------------------


def _ideas_out(db: Session, category: str | None) -> dict[str, Any]:
    newest = db.scalar(select(SmIdea.found_at).order_by(SmIdea.found_at.desc()).limit(1))
    return {"items": ideas.listing(db, category), "refreshed_at": newest.isoformat() if newest else None}


@router.get(f"{P}/ideas")
async def list_ideas(db: DbSession, runtime: Runtime, category: str | None = Query(None, max_length=32)) -> dict[str, Any]:
    """Story ideas, best first. The list refreshes itself from the sources when it is older than half an hour."""
    if category and category not in defaults.CATEGORIES:
        raise HTTPException(400, "Unknown category.")
    if ideas.stale(db):
        await ideas.refresh(db, _transport(runtime))
    return _ideas_out(db, category)


@router.post(f"{P}/ideas/refresh")
async def refresh_ideas(db: DbSession, runtime: Runtime) -> dict[str, Any]:
    newest = db.scalar(select(SmIdea.found_at).order_by(SmIdea.found_at.desc()).limit(1))
    if newest is None or utcnow() - newest > timedelta(seconds=60):  # the sources are free services: don't hammer them
        await ideas.refresh(db, _transport(runtime))
    return _ideas_out(db, None)


# --- shorts --------------------------------------------------------------------------------------------------------------


class MakeIn(StrictInput):
    idea_id: int | None = None  # None: the best fresh story (optionally within `category`)
    category: str | None = Field(None, max_length=32)
    style: str = Field(defaults.AUTO, max_length=24)  # auto: a different look for every short
    voice: str = Field(defaults.DEFAULT_VOICE, max_length=32)
    seconds: int | None = None  # None: as long as the story needs, 10 to 50 seconds
    quality: str = Field(defaults.DEFAULT_QUALITY, max_length=12)


def _options(style: str, voice: str, seconds: int, quality: str) -> None:
    if style not in defaults.STYLES:
        raise HTTPException(400, "Unknown style.")
    if voice not in defaults.VOICES:
        raise HTTPException(400, "Unknown voice.")
    if not defaults.MIN_SECONDS <= seconds <= defaults.MAX_SECONDS:
        raise HTTPException(400, f"Length is {defaults.MIN_SECONDS} to {defaults.MAX_SECONDS} seconds.")
    if quality not in defaults.QUALITIES:
        raise HTTPException(400, "Unknown quality.")


@router.post(f"{P}/shorts")
async def make_short(body: MakeIn, db: DbSession, runtime: Runtime) -> dict[str, Any]:
    style = body.style if body.style != defaults.AUTO else defaults.auto_style((db.scalar(select(func.max(SmShort.id))) or 0))
    _options(style, body.voice, body.seconds or defaults.DEFAULT_LENGTH, body.quality)
    if (db.scalar(select(func.count(SmShort.id)).where(SmShort.status.in_(OPEN))) or 0) >= defaults.MAX_OPEN:
        raise HTTPException(409, f"{defaults.MAX_OPEN} shorts are already being made. Wait for one to finish.")
    if body.idea_id is not None:
        idea = db.get(SmIdea, body.idea_id)
        if idea is None:
            raise HTTPException(404, "That story is no longer in the list. Refresh the ideas.")
    else:
        if body.category and body.category not in defaults.CATEGORIES:
            raise HTTPException(400, "Unknown category.")
        if ideas.stale(db):
            await ideas.refresh(db, _transport(runtime))
        fresh = [i for i in ideas.listing(db, body.category) if not i["used"]]
        if not fresh:
            raise HTTPException(409, "No fresh story with reliable sources right now. Try another topic or later.")
        idea = db.get(SmIdea, fresh[0]["id"])
        assert idea is not None
    now = utcnow()
    s = SmShort(idea_key=idea.key, category=idea.category, topic=idea.topic, brief=idea.brief, style=style, voice=body.voice,
                seconds=body.seconds or defaults.auto_seconds(idea.brief or {}), quality=body.quality, stages={}, generation=1)
    queue.enqueue(s, now)
    b = idea.brief or {}
    queue.set_stage(s, "idea", "done", f"{len(b.get('facts') or [])} facts · {len(b.get('sources') or [])} sources", now)
    db.add(s)
    db.commit()
    return short_out(s, detail=True)


@router.get(f"{P}/shorts")
def list_shorts(db: DbSession, page: int = Query(1, ge=1, le=500)) -> dict[str, Any]:
    queue.expire_leases(db, utcnow())
    db.commit()
    total = db.scalar(select(func.count(SmShort.id))) or 0
    rows = db.scalars(select(SmShort).order_by(SmShort.id.desc()).offset((page - 1) * 30).limit(30))
    return {"items": [short_out(s) for s in rows], "total": total, "page": page}


@router.get(f"{P}/shorts/{{short_id}}")
def get_short(short_id: int, db: DbSession) -> dict[str, Any]:
    return short_out(_short(db, short_id), detail=True)


class RemakeIn(StrictInput):
    style: str | None = Field(None, max_length=24)
    voice: str | None = Field(None, max_length=32)
    seconds: int | None = None
    quality: str | None = Field(None, max_length=12)


@router.post(f"{P}/shorts/{{short_id}}/remake")
def remake(short_id: int, body: RemakeIn, db: DbSession) -> dict[str, Any]:
    """The same story again: a new script, new voice take and new scenes (optionally in another style, voice or length)."""
    s = _short(db, short_id)
    style, voice, seconds, quality = body.style or s.style, body.voice or s.voice, body.seconds or s.seconds, body.quality or s.quality
    _options(style, voice, seconds, quality)
    s.style, s.voice, s.seconds, s.quality = style, voice, seconds, quality
    s.generation += 1  # the worker's claim on the old take ends: its uploads are refused
    s.script = None
    queue.enqueue(s)
    db.commit()
    return short_out(s, detail=True)


@router.delete(f"{P}/shorts/{{short_id}}")
def delete_short(short_id: int, db: DbSession, runtime: Runtime) -> dict[str, Any]:
    s = _short(db, short_id)
    store = MediaStore(runtime.settings)
    for key in (s.video_key, s.thumb_key):
        if key:
            try:
                store.delete(key)
            except Exception:  # noqa: BLE001 - the row goes either way; an orphan file is harmless
                pass
    db.delete(s)
    db.commit()
    return {"deleted": True}


def filename(s: SmShort) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str((s.script or {}).get("title") or s.topic).lower()).strip("-")[:60].strip("-")
    return f"{slug or 'short'}-{s.id}.mp4"


@router.api_route(f"{P}/shorts/{{short_id}}/media/{{kind}}", methods=["GET", "HEAD"])
def short_media(short_id: int, kind: Literal["video", "thumb"], request: Request, db: DbSession, runtime: Runtime,
                download: bool = False) -> Response:
    s = _short(db, short_id)
    key = s.video_key if kind == "video" else s.thumb_key
    if not key:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not made yet.")
    resp = _stream(request, MediaStore(runtime.settings), key, "video/mp4" if kind == "video" else "image/jpeg")
    if download and kind == "video":
        resp.headers["Content-Disposition"] = f'attachment; filename="{filename(s)}"'
        if request.method == "GET" and s.downloaded_at is None:
            s.downloaded_at = utcnow()
            db.commit()
    return resp


# --- the worker protocol (bearer token; outbound HTTPS from the worker only) ------------------------------------------------

Bearer = Annotated[str | None, Header(alias="Authorization")]


def _worker(db: Session, authorization: str | None) -> BmWorker:
    w = bm_queue.worker_for(db, (authorization or "").removeprefix("Bearer ").strip())
    if w is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Unknown or disabled worker token.")
    return w


def _q(exc: bm_queue.QueueError) -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, str(exc))


@router.post(f"{P}/worker/claim")
def worker_claim(body: ClaimIn, db: DbSession, runtime: Runtime, authorization: Bearer = None) -> Response:
    job = queue.claim(db, runtime.settings, _worker(db, authorization), body.info)
    if job is None:
        return Response(status_code=204)
    return Response(content=json.dumps(job, default=str), media_type="application/json")


@router.post(f"{P}/worker/jobs/{{job_id}}/progress")
def worker_progress(job_id: int, body: ProgressIn, db: DbSession, runtime: Runtime, authorization: Bearer = None) -> dict[str, Any]:
    try:
        return queue.progress(db, runtime.settings, _worker(db, authorization), job_id, body.stage, body.status, body.note)
    except bm_queue.QueueError as exc:
        raise _q(exc) from exc


@router.post(f"{P}/worker/jobs/{{job_id}}/script")
def worker_script(job_id: int, body: ScriptIn, db: DbSession, runtime: Runtime, authorization: Bearer = None) -> dict[str, Any]:
    try:
        return queue.submit_script(db, runtime.settings, _worker(db, authorization), job_id, body.script)
    except bm_queue.QueueError as exc:
        raise _q(exc) from exc


@router.post(f"{P}/worker/jobs/{{job_id}}/result")
def worker_result(job_id: int, db: DbSession, runtime: Runtime, video: UploadFile = File(...), thumb: UploadFile | None = File(None),
                  meta: str = Form(...), authorization: Bearer = None) -> dict[str, Any]:
    w = _worker(db, authorization)
    try:
        info = json.loads(meta)
    except ValueError:
        raise HTTPException(400, "meta must be JSON.") from None
    if not isinstance(info, dict):
        raise HTTPException(400, "meta must be a JSON object.")
    try:
        return queue.complete(db, runtime.settings, w, job_id, video.file, thumb.file if thumb else None, info)
    except bm_queue.QueueError as exc:
        raise _q(exc) from exc


@router.post(f"{P}/worker/jobs/{{job_id}}/fail")
def worker_fail(job_id: int, body: FailIn, db: DbSession, authorization: Bearer = None) -> dict[str, Any]:
    try:
        return queue.fail(db, _worker(db, authorization), job_id, body.error, body.retryable)
    except bm_queue.QueueError as exc:
        raise _q(exc) from exc
