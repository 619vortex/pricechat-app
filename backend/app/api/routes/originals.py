"""Admin › X › Originals: the Growth Engine's approval queue (owner login required: /api/originals is not on the public list).

Approve only MARKS a draft approved. Nothing in this module (or app.growth) posts to X or Telegram: the owner copies the text and downloads
the image and posts by hand.
"""

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.deps import DbSession
from app.config.settings import get_settings
from app.growth import drafts, graphics, sources, store
from app.models.market_memory import GrDraft

router = APIRouter(prefix="/originals", tags=["originals"])
OWNER = "owner"


def _err(e: drafts.DraftError) -> HTTPException:
    return HTTPException(status_code=e.status, detail=str(e))


@router.get("/drafts")
def list_drafts(db: DbSession, status: str | None = Query(None), format: str | None = Query(None), limit: int = Query(50, ge=1, le=200),
                offset: int = Query(0, ge=0)) -> dict[str, Any]:
    q = select(GrDraft)
    if status:
        if status not in drafts.STATUSES:
            raise HTTPException(422, f"status must be one of {', '.join(drafts.STATUSES)}")
        q = q.where(GrDraft.status == status)
    if format:
        q = q.where(GrDraft.format == format)
    rows = list(db.scalars(q.order_by(GrDraft.created_at.desc(), GrDraft.score.desc()).offset(offset).limit(limit)))
    counts = {s: n for s, n in db.execute(select(GrDraft.status, func.count()).group_by(GrDraft.status))}
    s = get_settings()
    return {"drafts": [drafts.view(d) for d in rows], "counts": {k: int(counts.get(k, 0)) for k in drafts.STATUSES},
            "switches": {"growth_enabled": bool(s.growth_enabled), "growth_collect_enabled": bool(s.growth_collect_enabled),
                         "intel_enabled": bool(s.intel_enabled)},
            "publishing": "manual: approving marks a draft approved; nothing is posted automatically"}


@router.get("/drafts/{draft_id}")
def get_draft(draft_id: int, db: DbSession) -> dict[str, Any]:
    d = db.get(GrDraft, draft_id)
    if d is None:
        raise HTTPException(404, "No such draft.")
    return drafts.view(d)


@router.get("/drafts/{draft_id}/image.png")
def image(draft_id: int, db: DbSession, fmt: Literal["x", "portrait"] = Query("x")) -> Response:
    d = db.get(GrDraft, draft_id)
    if d is None:
        raise HTTPException(404, "No such draft.")
    try:
        png = graphics.graphic_png(d.chart, fmt)
    except graphics.RendererMissing:
        raise HTTPException(503, "renderer not installed") from None
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    return Response(content=png, media_type="image/png", headers={"Cache-Control": "private, max-age=300",
                                                                  "Content-Disposition": f'inline; filename="original-{d.id}-{fmt}.png"'})


class EditIn(BaseModel):
    text: str = Field(min_length=1, max_length=1000)


class RejectIn(BaseModel):
    reason: str = Field("", max_length=500)


class RegenIn(BaseModel):
    chart_variant: bool = False


@router.post("/drafts/{draft_id}/approve")
def approve(draft_id: int, db: DbSession) -> dict[str, Any]:
    try:
        return drafts.view(drafts.approve(db, draft_id, OWNER))
    except drafts.DraftError as e:
        raise _err(e) from None


@router.post("/drafts/{draft_id}/edit")
def edit(draft_id: int, body: EditIn, db: DbSession) -> dict[str, Any]:
    try:
        return drafts.view(drafts.edit(db, draft_id, body.text, OWNER))
    except drafts.DraftError as e:
        raise _err(e) from None


@router.post("/drafts/{draft_id}/regenerate")
def regenerate(draft_id: int, db: DbSession, body: RegenIn | None = None) -> dict[str, Any]:
    try:
        return drafts.view(drafts.regenerate(db, draft_id, chart_variant=bool(body and body.chart_variant)))
    except drafts.DraftError as e:
        raise _err(e) from None


@router.post("/drafts/{draft_id}/reject")
def reject(draft_id: int, db: DbSession, body: RejectIn | None = None) -> dict[str, Any]:
    try:
        return drafts.view(drafts.reject(db, draft_id, body.reason if body else "", OWNER))
    except drafts.DraftError as e:
        raise _err(e) from None


@router.post("/generate")
def generate_now(db: DbSession) -> dict[str, Any]:
    """Run the detectors over Market Memory now and queue new drafts (works with growth_enabled off: the owner asked for it)."""
    out = drafts.generate(db)
    drafts.apply_writer_results(db)
    return out


@router.get("/memory")
def memory(db: DbSession) -> dict[str, Any]:
    """Market Memory freshness per series family, and the source registry (licence status)."""
    return {"families": store.freshness(db), "sources": sources.registry()}
