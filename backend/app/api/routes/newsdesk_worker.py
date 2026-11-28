"""The GPU PC's side of News Desk pictures (app/newsdesk/render_queue.py). Public prefix (admin_auth.PUBLIC_PREFIXES "/api/newsdesk-worker/"):
every call carries the Price Chat AI worker's own bearer token (Brand Manager › Settings › AI), never the admin cookie. The worker only
ever calls out; nothing here reaches the PC."""

import base64
import binascii
import json
from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException, Response, status
from pydantic import Field
from sqlalchemy.orm import Session

from app.api.deps import DbSession
from app.api.schemas import StrictInput
from app.brand_manager import queue as bm_queue
from app.models.brand_manager import BmWorker
from app.newsdesk import render_queue as rq

router = APIRouter(prefix="/newsdesk-worker", tags=["newsdesk"])
Bearer = Annotated[str | None, Header(alias="Authorization")]
MAX_IMAGE = 6 * 1024 * 1024


def _worker(db: Session, authorization: str | None) -> BmWorker:
    w = bm_queue.worker_for(db, (authorization or "").removeprefix("Bearer ").strip())
    if w is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Unknown or disabled worker token.")
    return w


class ClaimIn(StrictInput):
    info: dict[str, Any] | None = None


@router.post("/claim")
def claim(body: ClaimIn, db: DbSession, authorization: Bearer = None) -> Response:
    job = rq.claim(db, _worker(db, authorization), body.info)
    if job is None:
        return Response(status_code=204)
    return Response(content=json.dumps(job, default=str), media_type="application/json")


class ResultIn(StrictInput):
    image_b64: str = Field(..., max_length=MAX_IMAGE * 4 // 3 + 16)
    result: dict[str, Any] = Field(default_factory=dict)


@router.post("/jobs/{job_id}/result")
def result(job_id: int, body: ResultIn, db: DbSession, authorization: Bearer = None) -> dict[str, Any]:
    w = _worker(db, authorization)
    try:
        data = base64.b64decode(body.image_b64, validate=True)
    except (binascii.Error, ValueError) as e:
        raise HTTPException(400, "image_b64 isn't base64.") from e
    try:
        return rq.complete(db, job_id, w, data, body.result)
    except rq.RenderError as e:
        raise HTTPException(e.status, str(e)) from e


class FailIn(StrictInput):
    error: str = Field(..., max_length=500)


@router.post("/jobs/{job_id}/fail")
def fail(job_id: int, body: FailIn, db: DbSession, authorization: Bearer = None) -> dict[str, Any]:
    w = _worker(db, authorization)
    try:
        return rq.fail(db, job_id, w, body.error)
    except rq.RenderError as e:
        raise HTTPException(e.status, str(e)) from e


class WorldMonitorIn(StrictInput):
    """One snapshot from the World Monitor desktop app on the PC (app/newsdesk/wm_app.py; worker/pricechat_worker/worldmonitor.py)."""

    app_version: str = Field("", max_length=20)
    bridge_version: str = Field("", max_length=20)
    collected_at: str = Field("", max_length=40)
    feeds: dict[str, dict[str, Any]] = Field(default_factory=dict)
    signals: list[dict[str, Any]] = Field(default_factory=list, max_length=400)
    summary: dict[str, Any] = Field(default_factory=dict)
    feeds_ok: int | None = Field(None, ge=0, le=1000)  # bridge 1.1.0+; older bridges leave them out (counted from `feeds`)
    feeds_failed: int | None = Field(None, ge=0, le=1000)


@router.post("/world-monitor")
def world_monitor(body: WorldMonitorIn, db: DbSession, authorization: Bearer = None) -> dict[str, Any]:
    """The PC bridge's snapshot of the World Monitor desktop app, shown in Admin › World Monitor › Desktop app. Never posts."""
    from app.db.base import utcnow
    from app.newsdesk import wm_app

    w = _worker(db, authorization)
    try:
        return wm_app.save(db, w.name, body.model_dump(), utcnow())
    except wm_app.WmError as e:
        raise HTTPException(413, str(e)) from e
