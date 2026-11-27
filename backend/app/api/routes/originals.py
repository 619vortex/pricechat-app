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
from app.growth import drafts, editorial, graphics, performance, sources, store
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
        q = q.where(GrDraft.status.in_(("pending", "regenerating")) if status == "pending" else GrDraft.status == status)  # to review: both
    if format:
        q = q.where(GrDraft.format == format)
    rows = list(db.scalars(q.order_by(GrDraft.created_at.desc(), GrDraft.score.desc()).offset(offset).limit(limit)))
    counts = {s: n for s, n in db.execute(select(GrDraft.status, func.count()).group_by(GrDraft.status))}
    s = get_settings()
    metrics = performance.draft_metrics(db, rows)
    views = [drafts.view(d) | {"performance": metrics.get(d.id)} for d in rows]
    pending = views if status == "pending" and not format and offset == 0 and len(rows) < limit else [
        drafts.view(d) for d in db.scalars(select(GrDraft).where(GrDraft.status == "pending"))]
    top = editorial.shortlist(pending, drafts.GENERATOR, _lift(db))
    return {"drafts": views, "counts": {k: int(counts.get(k, 0)) for k in drafts.STATUSES}, "top": top,
            "switches": {"growth_enabled": bool(s.growth_enabled), "growth_collect_enabled": bool(s.growth_collect_enabled),
                         "intel_enabled": bool(s.intel_enabled)},
            "publishing": "manual: approving marks a draft approved; nothing is posted automatically"}


_LIFT: dict[str, Any] = {"at": 0.0, "lift": {}}


def _lift(db: Any) -> dict[str, float]:
    """Measured engagement lift per Original format (G3), recomputed at most every 10 minutes: the shortlist's shareability once measured."""
    import time

    if time.monotonic() - _LIFT["at"] > 600:
        try:
            _LIFT["lift"] = performance.format_lift(performance.report(db))
        except Exception:  # noqa: BLE001 - a ranking hint: the priors stand in when it can't be computed
            db.rollback()
            _LIFT["lift"] = {}
        _LIFT["at"] = time.monotonic()
    return _LIFT["lift"]


@router.get("/performance")
def performance_report(db: DbSession, days: int = Query(30, ge=1, le=90)) -> dict[str, Any]:
    """G3: every Price Chat X post's measured numbers, Originals against the News Desk and Trending posts, the winning formats. Stored
    snapshots only: this reads nothing from X and costs nothing."""
    return performance.report(db, days=days)


class PostedIn(BaseModel):
    url: str = Field(min_length=5, max_length=300)


@router.post("/drafts/{draft_id}/posted")
def mark_posted(draft_id: int, body: PostedIn, db: DbSession) -> dict[str, Any]:
    """Links an approved draft to the X post the owner made from it (the post's link or id). Read-only: nothing is posted."""
    try:
        return drafts.view(performance.link(db, draft_id, body.url))
    except drafts.DraftError as e:
        raise _err(e) from None


@router.delete("/drafts/{draft_id}/posted")
def unmark_posted(draft_id: int, db: DbSession) -> dict[str, Any]:
    try:
        return drafts.view(performance.unlink(db, draft_id))
    except drafts.DraftError as e:
        raise _err(e) from None


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
        png, meta = graphics.render_graphic(d.chart, fmt, d.finding)
    except graphics.RendererMissing:
        raise HTTPException(503, "renderer not installed") from None
    except ValueError as e:  # chart_spec.SpecError too: the chart can't be drawn honestly
        raise HTTPException(422, str(e)) from None
    cache = dict(d.image_cache or {})
    keep = ("template", "version", "spec_version", "kind", "variant_of", "theme", "size", "label_overlaps", "phone_min_text_px", "sha256", "at")
    entry = {k: meta.get(k) for k in keep}
    if {k: v for k, v in (cache.get(fmt) or {}).items() if k != "at"} != {k: v for k, v in entry.items() if k != "at"}:
        cache[fmt] = entry  # render_info meta (template, version) for the growth learner
        d.image_cache = cache
        db.commit()
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
