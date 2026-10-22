"""Brand Manager API (docs/BRAND_MANAGER.md).

/api/brand-manager/*          owner only (the admin gate): overview, START/STOP AUTO, brand + settings, TikTok, videos,
                              calendar, analytics, workers
/api/brand-manager/worker/*   AI workers, with their own bearer token (registered in Settings › AI)
/api/brand-manager/media/*    signed, expiring video URLs (TikTok's PULL_FROM_URL fetches from here)
"""

import json
import re
from datetime import timedelta
from typing import Annotated, Any, Literal
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Cookie, File, Form, Header, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import RedirectResponse, Response, StreamingResponse
from pydantic import Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import DbSession, Runtime
from app.api.schemas import StrictInput
from app.brand_manager import analytics, brands, defaults, discovery, engine, hashtags, queue, tiktok, trends
from app.brand_manager import timeline as tline
from app.brand_manager.store import MediaStore, verify
from app.security.admin_auth import COOKIE as ADMIN_COOKIE
from app.db.base import utcnow
from app.models.brand_manager import BmJob, BmMetric, BmTrendDay, BmVideo, BmWorker

router = APIRouter(tags=["brand-manager"])
P = "/brand-manager"


def _transport(runtime: Any) -> Any:
    return getattr(runtime, "bm_transport", None)


def _video(db: Session, video_id: int) -> BmVideo:
    b = brands.ensure_default(db)
    v = db.get(BmVideo, video_id)
    if v is None or v.brand_id != b.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found.")
    return v


def video_out(v: BmVideo, detail: bool = False) -> dict[str, Any]:
    s = v.script or {}
    out = {"id": v.id, "topic": v.topic, "category": v.category, "category_label": defaults.CATEGORIES.get(v.category, {}).get("label", v.category),
           "template": v.template, "template_label": defaults.TEMPLATES.get(v.template, {}).get("label", v.template), "status": v.status,
           "score": v.score, "slot_at": v.slot_at.isoformat() if v.slot_at else None, "title": s.get("title"), "hashtags": s.get("hashtags") or [],
           "duration": v.duration, "has_video": bool(v.video_key), "has_thumb": bool(v.thumb_key), "publish_mode": v.publish_mode,
           "publish_status": v.publish_status, "published_at": v.published_at.isoformat() if v.published_at else None, "metrics": v.metrics,
           "error": v.error, "created_at": v.created_at.isoformat(), "stages": v.stages, "approved": v.approved_at is not None,
           "private": (v.post_settings or {}).get("privacy_level") == "SELF_ONLY", "generation": v.generation}
    if detail:
        out.update({"script": s, "brief": v.brief, "qc": v.qc, "render_info": v.render_info, "post_settings": v.post_settings,
                    "sources": s.get("sources") or v.brief.get("sources") or [], "creative": v.creative or {},
                    "media": tline.assets_of(v), "timeline": v.timeline})
    return out


# --- overview + automation ---------------------------------------------------------------------------------------------


@router.get(f"{P}/overview")
def overview(db: DbSession, runtime: Runtime) -> dict[str, Any]:
    s = runtime.settings
    b = brands.ensure_default(db)
    st = brands.strategy(b)
    now = utcnow()
    tz = ZoneInfo(st["timezone"]) if st.get("timezone") else ZoneInfo("UTC")
    day_start = now.astimezone(tz).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(ZoneInfo("UTC"))
    vids = list(db.scalars(select(BmVideo).where(BmVideo.brand_id == b.id)))
    created_today = sum(1 for v in vids if v.video_key and v.qc and v.qc.get("checked_at", "") >= day_start.isoformat())
    published_today = sum(1 for v in vids if v.status == "published" and v.published_at and v.published_at >= day_start)
    drafts_today = sum(1 for v in vids if v.status == "sent_to_drafts" and v.updated_at >= day_start)
    upcoming = sorted((v for v in vids if v.slot_at and v.slot_at > now and v.status in engine.UNPUBLISHED), key=lambda v: v.slot_at)
    measured = [v for v in vids if (v.metrics or {}).get("views") is not None]
    followers = list(db.scalars(select(BmMetric).where(BmMetric.brand_id == b.id, BmMetric.video_id.is_(None), BmMetric.followers.is_not(None))
                                .order_by(BmMetric.at)))
    a = brands.automation(db, b)
    base = next((m for m in followers if a.started_at is None or m.at >= a.started_at), followers[0] if followers else None)
    best = max(measured, key=lambda v: v.metrics["views"], default=None)
    acc = tiktok.account(db, b.id)
    return {
        "brand": {"id": b.id, "name": b.name}, "automation": engine.automation_out(db, s, b), "tiktok": tiktok.account_out(acc, s),
        "stats": {"created_today": created_today, "published_today": published_today, "sent_to_drafts_today": drafts_today,
                  "upcoming": len(upcoming), "total_views": sum(v.metrics["views"] for v in measured) if measured else None,
                  "followers": followers[-1].followers if followers else None,
                  "followers_gained": (followers[-1].followers - base.followers) if followers and base else None,
                  "best_video": video_out(best) if best else None, "measured_videos": len(measured)},
        "upcoming": [video_out(v) for v in upcoming[:5]],
        "pipeline": [{"key": k, "label": label, "where": where} for k, label, where in defaults.STAGES],
        "in_progress": [video_out(v) for v in sorted((v for v in vids if v.status in ("queued", "rendering", "publishing")), key=lambda v: v.id)[:5]],
        "workers": [queue.worker_out(w, now) for w in db.scalars(select(BmWorker).order_by(BmWorker.id))],
        "events": brands.events(db, b.id, limit=25),
    }


class StartIn(StrictInput):
    consent: bool = False


@router.post(f"{P}/auto/start")
def auto_start(body: StartIn, db: DbSession, runtime: Runtime) -> dict[str, Any]:
    return engine.start(db, runtime.settings, brands.ensure_default(db), body.consent)


@router.post(f"{P}/auto/stop")
def auto_stop(db: DbSession, runtime: Runtime) -> dict[str, Any]:
    return engine.stop(db, runtime.settings, brands.ensure_default(db))


class EmergencyIn(StrictInput):
    on: bool


@router.post(f"{P}/auto/emergency")
def auto_emergency(body: EmergencyIn, db: DbSession, runtime: Runtime) -> dict[str, Any]:
    return engine.emergency(db, runtime.settings, brands.ensure_default(db), body.on)


@router.post(f"{P}/auto/tick")
async def auto_tick(db: DbSession, runtime: Runtime) -> dict[str, Any]:
    """Run one scheduler tick now (the scheduler also runs it every minute)."""
    return await engine.tick(db, runtime.settings, _transport(runtime))


# --- brand + settings ----------------------------------------------------------------------------------------------------


@router.get(f"{P}/brand")
def get_brand(db: DbSession) -> dict[str, Any]:
    return {**brands.brand_out(brands.ensure_default(db)), "categories": defaults.CATEGORIES, "templates": defaults.TEMPLATES, "voices": defaults.VOICES,
            "defaults": defaults.STRATEGY}


class BrandIn(StrictInput):
    name: str | None = Field(None, max_length=80)
    website: str | None = Field(None, max_length=200)
    description: str | None = Field(None, max_length=4000)
    audience: str | None = Field(None, max_length=2000)
    industry: str | None = Field(None, max_length=80)
    language: str | None = Field(None, max_length=32)
    personality: str | None = Field(None, max_length=2000)
    logo_url: str | None = Field(None, max_length=500)
    cta: str | None = Field(None, max_length=200)
    topics: list[str] | None = None
    avoid: list[str] | None = None
    colors: list[str] | None = None
    competitors: list[str] | None = None
    examples: list[dict[str, Any]] | None = None
    strategy: dict[str, Any] | None = None


def _valid_strategy(st: dict[str, Any]) -> None:
    m = defaults.merged(st)
    if not 1 <= int(m["videos_per_day"]) <= 10:
        raise HTTPException(400, "Videos per day must be 1-10 (TikTok caps posts per account).")
    if not 10 <= int(m["min_seconds"]) <= int(m["max_seconds"]) <= 120:
        raise HTTPException(400, "Video length must be between 10 and 120 seconds, min ≤ max.")
    if any(not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", t) for t in m["post_times"]):
        raise HTTPException(400, "Posting times look like 09:30.")
    try:
        ZoneInfo(m["timezone"])
    except Exception:  # noqa: BLE001
        raise HTTPException(400, f"Unknown time zone: {m['timezone']}") from None
    if m["publish_mode"] not in ("drafts", "direct"):
        raise HTTPException(400, "Publishing is drafts or direct.")
    if m["voice"] not in ("auto", "kokoro", "openai", "piper", "system"):
        raise HTTPException(400, "Unknown voice engine.")
    if m["voice_name"] not in defaults.VOICES:
        raise HTTPException(400, "Unknown narrator voice.")
    if not 0.8 <= float(m["voice_speed"]) <= 1.4:
        raise HTTPException(400, "Voice speed must be between 0.8 and 1.4.")
    if m["music"] and not str(m["music"]).startswith("https://"):
        raise HTTPException(400, "The music track must be an https URL of a track you hold a licence for.")
    _valid_creative(m.get("creative") or {})


def _valid_creative(c: dict[str, Any]) -> None:
    for k, allowed in defaults.CREATIVE_OPTIONS.items():
        if k in ("visual",) or c.get(k) in (None, ""):
            continue
        if c[k] not in allowed:
            raise HTTPException(400, f"Unknown {k}: {c[k]}.")
    if c.get("narration_url") and not str(c["narration_url"]).startswith("https://"):
        raise HTTPException(400, "The narration must be an https link to an audio file (MP3, WAV, M4A).")
    if c.get("seconds") not in (None, "") and not 15 <= int(c["seconds"]) <= 90:
        raise HTTPException(400, "Video duration must be 15-90 seconds.")
    pr = c.get("pronunciations") or {}
    if not isinstance(pr, dict) or len(pr) > 200 or any(not isinstance(a, str) or not isinstance(b, str) or len(a) > 40 or len(b) > 60
                                                         for a, b in pr.items()):
        raise HTTPException(400, "Pronunciations: up to 200 word → how to say it pairs (40/60 characters).")


@router.patch(f"{P}/brand")
def patch_brand(body: BrandIn, db: DbSession) -> dict[str, Any]:
    data = body.model_dump(exclude_unset=True)
    if data.get("strategy"):
        _valid_strategy({**(brands.ensure_default(db).strategy or {}), **data["strategy"]})
    return brands.brand_out(brands.update(db, brands.ensure_default(db), data))


# --- TikTok ---------------------------------------------------------------------------------------------------------------


@router.get(f"{P}/tiktok")
def tiktok_get(db: DbSession, runtime: Runtime) -> dict[str, Any]:
    b = brands.ensure_default(db)
    return tiktok.account_out(tiktok.account(db, b.id), runtime.settings)


AdminCookie = Annotated[str | None, Cookie(alias=ADMIN_COOKIE)]


@router.post(f"{P}/tiktok/connect")
def tiktok_connect(db: DbSession, runtime: Runtime, pc_admin: AdminCookie = None) -> dict[str, Any]:
    try:
        return {"url": tiktok.authorize_url(runtime.settings, brands.ensure_default(db).id, pc_admin)}
    except tiktok.TikTokError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get(f"{P}/tiktok/callback", include_in_schema=False)
async def tiktok_callback(db: DbSession, runtime: Runtime, code: str | None = None, state: str | None = None, error: str | None = None,
                          error_description: str | None = None, pc_admin: AdminCookie = None) -> RedirectResponse:
    back = f"{runtime.settings.dashboard_url.rstrip('/')}/brand-manager/tiktok"
    if error or not code or not state:
        tiktok.log.warning("TikTok sign-in refused: error=%s description=%s", error, error_description)
        return RedirectResponse(f"{back}?{urlencode({'error': tiktok.auth_error_message(error, error_description)[:300]})}", status_code=303)
    try:
        await tiktok.finish(db, runtime.settings, code, state, _transport(runtime), session_token=pc_admin)
    except tiktok.TikTokError as exc:
        tiktok.log.warning("TikTok token exchange failed: code=%s", exc.code)
        return RedirectResponse(f"{back}?{urlencode({'error': str(exc)[:300]})}", status_code=303)
    brands.log(db, brands.ensure_default(db).id, "TikTok connected.", commit=True)
    return RedirectResponse(f"{back}?connected=1", status_code=303)


@router.get(f"{P}/tiktok/diagnostics")
async def tiktok_diagnostics(runtime: Runtime) -> dict[str, Any]:
    """Owner-only setup check: where the TikTok client key comes from and whether TikTok accepts the key + secret.
    Never returns the secret."""
    return await tiktok.diagnostics(runtime.settings, _transport(runtime))


@router.post(f"{P}/tiktok/refresh")
async def tiktok_refresh(db: DbSession, runtime: Runtime) -> dict[str, Any]:
    b = brands.ensure_default(db)
    acc = tiktok.account(db, b.id)
    try:
        await tiktok.refresh_profile(db, runtime.settings, acc, _transport(runtime))
        if "video.publish" in acc.scopes:
            await tiktok.creator_info(db, runtime.settings, acc, _transport(runtime))
    except tiktok.TikTokError as exc:
        raise HTTPException(400, str(exc)) from exc
    return tiktok.account_out(acc, runtime.settings)


@router.post(f"{P}/tiktok/creator-info")
async def tiktok_creator_info(db: DbSession, runtime: Runtime) -> dict[str, Any]:
    """Fresh creator info for the posting screen: TikTok requires it each time the screen renders (privacy options,
    interaction settings, max duration, and whether the account can post right now)."""
    acc = tiktok.account(db, brands.ensure_default(db).id)
    if "video.publish" not in acc.scopes:
        raise HTTPException(400, "Direct posting needs the video.publish permission: reconnect TikTok and allow it.")
    try:
        await tiktok.creator_info(db, runtime.settings, acc, _transport(runtime))
    except tiktok.TikTokError as exc:
        raise HTTPException(400, str(exc)) from exc
    return tiktok.account_out(acc, runtime.settings)


@router.get(f"{P}/tiktok/videos")
async def tiktok_videos(db: DbSession, runtime: Runtime) -> dict[str, Any]:
    """The connected account's latest TikTok videos, read live with video.list (nothing stored or estimated)."""
    acc = tiktok.account(db, brands.ensure_default(db).id)
    if acc.status != "connected" or "video.list" not in acc.scopes:
        raise HTTPException(400, "Reading your TikTok videos needs the video.list permission: reconnect TikTok and allow it.")
    try:
        items = await tiktok.list_videos(db, runtime.settings, acc, _transport(runtime), max_pages=1)
    except tiktok.TikTokError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"videos": items, "at": utcnow().isoformat()}


@router.post(f"{P}/tiktok/disconnect")
async def tiktok_disconnect(db: DbSession, runtime: Runtime) -> dict[str, Any]:
    b = brands.ensure_default(db)
    await tiktok.disconnect(db, runtime.settings, b.id, _transport(runtime))
    brands.log(db, b.id, "TikTok disconnected (access revoked).", commit=True)
    return tiktok.account_out(tiktok.account(db, b.id), runtime.settings)


# --- videos ----------------------------------------------------------------------------------------------------------------


@router.get(f"{P}/videos")
def list_videos(db: DbSession, status_: str | None = Query(None, alias="status", max_length=20), page: int = Query(1, ge=1, le=500)) -> dict[str, Any]:
    b = brands.ensure_default(db)
    q = select(BmVideo).where(BmVideo.brand_id == b.id)
    if status_:
        q = q.where(BmVideo.status.in_(status_.split(",")))
    total = db.scalar(q.with_only_columns(func.count(BmVideo.id))) or 0
    rows = db.scalars(q.order_by(BmVideo.id.desc()).offset((page - 1) * 30).limit(30))
    return {"items": [video_out(v) for v in rows], "total": total, "page": page}


@router.get(f"{P}/videos/{{video_id}}")
def get_video(video_id: int, db: DbSession) -> dict[str, Any]:
    v = _video(db, video_id)
    return {**video_out(v, detail=True), "events": brands.events(db, v.brand_id, v.id, 40)}


def _stream(request: Request, store: MediaStore, key: str, media_type: str) -> Response:
    size = store.size(key)
    if size is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found.")
    rng = request.headers.get("range")
    headers = {"Accept-Ranges": "bytes", "Cache-Control": "private, max-age=3600"}
    if request.method == "HEAD":
        return Response(status_code=200, headers={**headers, "Content-Length": str(size)}, media_type=media_type)
    m = re.fullmatch(r"bytes=(\d*)-(\d*)", rng or "")
    if m and (m.group(1) or m.group(2)):
        start = int(m.group(1)) if m.group(1) else max(0, size - int(m.group(2)))
        end = min(int(m.group(2)), size - 1) if m.group(1) and m.group(2) else size - 1
        if start > end:
            return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
        return StreamingResponse(store.stream(key, start, end), status_code=206, media_type=media_type,
                                 headers={**headers, "Content-Range": f"bytes {start}-{end}/{size}", "Content-Length": str(end - start + 1)})
    return StreamingResponse(store.stream(key), media_type=media_type, headers={**headers, "Content-Length": str(size)})


@router.api_route(f"{P}/videos/{{video_id}}/media/{{kind}}", methods=["GET", "HEAD"])
def video_media(video_id: int, kind: Literal["video", "thumb"], request: Request, db: DbSession, runtime: Runtime) -> Response:
    v = _video(db, video_id)
    key = v.video_key if kind == "video" else v.thumb_key
    if not key:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not rendered yet.")
    return _stream(request, MediaStore(runtime.settings), key, "video/mp4" if kind == "video" else "image/jpeg")


@router.api_route(f"{P}/media/{{video_id}}/{{expires}}/{{sig}}/{{name}}", methods=["GET", "HEAD"], include_in_schema=False)
def signed_media(video_id: int, expires: int, sig: str, name: str, request: Request, db: DbSession, runtime: Runtime) -> Response:
    if name not in ("video.mp4", "thumb.jpg") or not verify(runtime.settings, video_id, expires, sig):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found.")
    v = db.get(BmVideo, video_id)
    key = (v.video_key if name == "video.mp4" else v.thumb_key) if v else None
    if not key:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found.")
    return _stream(request, MediaStore(runtime.settings), key, "video/mp4" if name == "video.mp4" else "image/jpeg")


class VideoEdit(StrictInput):
    title: str | None = Field(None, max_length=150)
    description: str | None = Field(None, max_length=2000)
    hashtags: list[str] | None = Field(None, max_length=10)
    slot_at: str | None = None


@router.patch(f"{P}/videos/{{video_id}}")
def edit_video(video_id: int, body: VideoEdit, db: DbSession) -> dict[str, Any]:
    from datetime import datetime

    v = _video(db, video_id)
    if v.status in ("publishing", "sent_to_drafts", "published"):
        raise HTTPException(409, "This video is already on TikTok.")
    s = dict(v.script or {})
    for f in ("title", "description"):
        if getattr(body, f) is not None:
            s[f] = getattr(body, f).strip()
    if body.hashtags is not None:
        s["hashtags"] = list(dict.fromkeys(t for t in (hashtags.clean(h) for h in body.hashtags) if t))[:10]
    v.script = s
    if body.slot_at is not None:
        v.slot_at = datetime.fromisoformat(body.slot_at.replace("Z", "+00:00")) if body.slot_at else None
    brands.log(db, v.brand_id, f"You edited “{v.topic}”.", video_id=v.id)
    db.commit()
    return video_out(v, detail=True)


class ApproveIn(StrictInput):
    # direct posts: TikTok's required posting screen (no defaults: each choice is the owner's)
    privacy_level: str | None = Field(None, max_length=40)
    allow_comment: bool = False
    allow_duet: bool = False
    allow_stitch: bool = False
    disclose: bool = False
    your_brand: bool = False
    branded_content: bool = False
    title: str | None = Field(None, max_length=2200)
    consent: bool = False  # the music usage / branded content declaration was shown and accepted


@router.post(f"{P}/videos/{{video_id}}/approve")
def approve_video(video_id: int, body: ApproveIn, db: DbSession, runtime: Runtime) -> dict[str, Any]:
    v = _video(db, video_id)
    if v.status not in ("needs_review", "ready", "approved"):
        raise HTTPException(409, f"This video can't be approved now ({v.status}).")
    st = brands.strategy(brands.ensure_default(db))
    mode = v.publish_mode or st["publish_mode"]
    if mode == "direct":
        acc = tiktok.account(db, v.brand_id)
        options = (acc.creator_info or {}).get("privacy_level_options") or []
        if not body.consent:
            raise HTTPException(400, "Accept TikTok's Music Usage Confirmation to post.")
        if not body.privacy_level or body.privacy_level not in options:
            raise HTTPException(400, "Choose who can see this post (TikTok requires your choice).")
        if not st.get("tiktok_audited") and body.privacy_level != "SELF_ONLY":
            raise HTTPException(400, "Until TikTok audits the app, direct posts can only be private (Only me).")
        if body.disclose and not (body.your_brand or body.branded_content):
            raise HTTPException(400, "You turned on content disclosure: choose Your brand and/or Branded content.")
        if body.branded_content and body.privacy_level == "SELF_ONLY":
            raise HTTPException(400, "Branded content can't be private on TikTok.")
        info = acc.creator_info or {}
        if info.get("max_video_post_duration_sec") and (v.duration or 0) > float(info["max_video_post_duration_sec"]):
            raise HTTPException(400, f"This video is longer than this TikTok account may post ({info['max_video_post_duration_sec']} s).")
        v.post_settings = {"privacy_level": body.privacy_level, "allow_comment": body.allow_comment and not info.get("comment_disabled"),
                           "allow_duet": body.allow_duet and not info.get("duet_disabled"), "allow_stitch": body.allow_stitch and not info.get("stitch_disabled"),
                           "your_brand": body.disclose and body.your_brand, "branded_content": body.disclose and body.branded_content,
                           "title": (body.title or "").strip() or None, "is_aigc": True, "creator": info.get("creator_nickname")}
    v.status, v.approved_at = "approved", utcnow()
    queue.set_stage(v, "schedule", "done", f"Approved · slot {v.slot_at.isoformat() if v.slot_at else 'next free slot'}")
    brands.log(db, v.brand_id, f"You approved “{v.topic}”.", stage="schedule", video_id=v.id)
    db.commit()
    return video_out(v, detail=True)


class DestinationIn(StrictInput):
    mode: Literal["drafts", "direct"]


@router.put(f"{P}/videos/{{video_id}}/destination")
def set_destination(video_id: int, body: DestinationIn, db: DbSession) -> dict[str, Any]:
    """Send this one video to the TikTok inbox (video.upload) or post it directly (video.publish). Changing it takes back
    an earlier approval: the owner confirms again on the screen for the new destination."""
    v = _video(db, video_id)
    if v.status not in ("needs_review", "ready", "approved"):
        raise HTTPException(409, f"The destination can't change now ({v.status}).")
    if (v.publish_mode or brands.strategy(brands.ensure_default(db))["publish_mode"]) != body.mode:
        v.publish_mode, v.status, v.approved_at, v.post_settings = body.mode, "needs_review", None, None
        queue.set_stage(v, "schedule", "waiting", "Waiting for your approval")
        brands.log(db, v.brand_id, f"“{v.topic}” will go to {'your TikTok drafts' if body.mode == 'drafts' else 'TikTok as a direct post'}.", video_id=v.id)
    else:
        v.publish_mode = body.mode
    db.commit()
    return video_out(v, detail=True)


@router.post(f"{P}/videos/{{video_id}}/publish-status")
async def check_publish_status(video_id: int, db: DbSession, runtime: Runtime) -> dict[str, Any]:
    """Ask TikTok for this upload's status now (the scheduler also asks every minute)."""
    v = _video(db, video_id)
    if not v.publish_id:
        raise HTTPException(409, "This video hasn't been sent to TikTok.")
    try:
        await engine.poll(db, runtime.settings, brands.ensure_default(db), v, _transport(runtime))
    except tiktok.TikTokError as exc:
        raise HTTPException(400, str(exc)) from exc
    return video_out(v, detail=True)


@router.post(f"{P}/videos/{{video_id}}/publish-now")
async def publish_now(video_id: int, db: DbSession, runtime: Runtime) -> dict[str, Any]:
    v = _video(db, video_id)
    a = brands.automation(db, brands.ensure_default(db))
    if a.emergency_stop:
        raise HTTPException(409, "The emergency stop is on.")
    if v.status not in ("ready", "approved"):
        raise HTTPException(409, f"Only a finished video can be sent ({v.status}).")
    b = brands.ensure_default(db)
    try:
        await engine.publish(db, runtime.settings, b, v, _transport(runtime))
    except tiktok.TikTokError as exc:
        raise HTTPException(400, str(exc)) from exc
    return video_out(v, detail=True)


@router.post(f"{P}/videos/{{video_id}}/regenerate")
def regenerate(video_id: int, db: DbSession) -> dict[str, Any]:
    v = _video(db, video_id)
    if v.status in ("publishing", "sent_to_drafts", "published"):
        raise HTTPException(409, "This video is already on TikTok.")
    for job in db.scalars(select(BmJob).where(BmJob.video_id == v.id, BmJob.status.in_(("queued", "claimed")))):
        job.status, job.finished_at, job.error = "cancelled", utcnow(), "Regenerated"
    v.generation += 1
    v.script, v.qc, v.error, v.approved_at, v.post_settings = None, None, None, None, None
    queue.enqueue_render(db, v, manual=True)
    brands.log(db, v.brand_id, f"Regenerating “{v.topic}”.", video_id=v.id)
    db.commit()
    return video_out(v, detail=True)


@router.get(f"{P}/creative/options")
def creative_options(db: DbSession) -> dict[str, Any]:
    """Content Studio v2's selectors, and the brand's current creative defaults."""
    return {"options": defaults.CREATIVE_OPTIONS, "defaults": brands.strategy(brands.ensure_default(db)).get("creative") or {}}


class SceneEditIn(StrictInput):
    screen: str | None = Field(None, max_length=80)
    visual: str | None = Field(None, max_length=24)


class CreativeIn(StrictInput):
    engine: str | None = Field(None, max_length=12)
    style: str | None = Field(None, max_length=16)
    tone: str | None = Field(None, max_length=16)
    narrator: str | None = Field(None, max_length=16)
    intensity: str | None = Field(None, max_length=8)
    music: str | None = Field(None, max_length=16)
    captions: str | None = Field(None, max_length=8)
    preset: str | None = Field(None, max_length=16)
    seconds: int | None = Field(None, ge=15, le=90)
    pronunciations: dict[str, str] | None = None
    scenes: dict[str, SceneEditIn] | None = None
    production: str | None = Field(None, max_length=16)  # v4 production style
    narration: str | None = Field(None, max_length=8)  # v4: ai | upload | none
    narration_url: str | None = Field(None, max_length=500)  # v4: the owner's own recorded narration (https)
    writer: str | None = Field(None, max_length=8)  # old | new (Kiwi hook layer)


class RerenderIn(StrictInput):
    scope: Literal["video", "scene", "voice", "captions", "timeline"] = "video"
    creative: CreativeIn | None = None


def _save_creative(v: BmVideo, body: CreativeIn) -> None:
    data = body.model_dump(exclude_unset=True)
    scenes = data.pop("scenes", None)
    _valid_creative(data)
    cur = dict(v.creative or {})
    cur.update({k: val for k, val in data.items()})
    if scenes is not None:
        n = len((v.script or {}).get("segments") or [])
        edits = dict(cur.get("scenes") or {})
        for idx, ed in scenes.items():
            if not idx.isdigit() or int(idx) >= max(n, 1):
                raise HTTPException(400, f"Scene {idx} doesn't exist.")
            ed = {k: val for k, val in ed.items() if val not in (None, "")}
            if ed.get("visual") and ed["visual"] not in defaults.CREATIVE_OPTIONS["visual"]:
                raise HTTPException(400, f"Unknown visual: {ed['visual']}.")
            if ed.get("screen"):
                from app.brand_manager import safety

                allowed = safety._allowed(v.brief or {})
                bad = [n_ for n_ in safety.numbers(ed["screen"]) if not safety._ok_number(n_, allowed)]
                if bad:
                    raise HTTPException(400, f"Scene {int(idx) + 1}: the number {bad[0]:g} isn't in the research.")
            if ed:
                edits[idx] = ed
            else:
                edits.pop(idx, None)
        cur["scenes"] = edits
    v.creative = cur


@router.put(f"{P}/videos/{{video_id}}/creative")
def put_creative(video_id: int, body: CreativeIn, db: DbSession) -> dict[str, Any]:
    v = _video(db, video_id)
    _save_creative(v, body)
    db.commit()
    return video_out(v, detail=True)


@router.post(f"{P}/videos/{{video_id}}/rerender")
def rerender(video_id: int, body: RerenderIn, db: DbSession) -> dict[str, Any]:
    """Make the video again from its approved script (no new research, no new script): a scene edit, another narrator or
    caption style, or the whole look. The voice is reused from the worker's cache when the narration didn't change."""
    v = _video(db, video_id)
    if v.status in ("publishing", "sent_to_drafts", "published"):
        raise HTTPException(409, "This video is already on TikTok.")
    if not (v.script or {}).get("segments"):
        raise HTTPException(409, "This video has no approved script yet: use Regenerate to write one.")
    if body.creative is not None:
        _save_creative(v, body.creative)
    if body.scope == "timeline" and not (v.timeline or {}).get("video"):
        raise HTTPException(409, "This video has no timeline yet: make it with the editorial renderer first.")
    for job in db.scalars(select(BmJob).where(BmJob.video_id == v.id, BmJob.status.in_(("queued", "claimed")))):
        job.status, job.finished_at, job.error = "cancelled", utcnow(), "Re-rendered"
    v.generation += 1
    v.qc, v.error, v.approved_at, v.post_settings = None, None, None, None
    queue.enqueue_render(db, v, manual=True, mode="timeline" if body.scope == "timeline" else "rerender")
    queue.set_stage(v, "script", "done", "kept the approved script")
    what = {"scene": "a scene", "voice": "the voice", "captions": "the captions", "video": "the video", "timeline": "the edited timeline"}[body.scope]
    brands.log(db, v.brand_id, f"Re-rendering {what} of “{v.topic}” (same script).", video_id=v.id)
    db.commit()
    return video_out(v, detail=True)


# --- Content Studio v4: media library + timeline ------------------------------------------------------------------------
class AssetIn(StrictInput):
    url: str = Field(..., max_length=500)
    kind: Literal["video", "image"] | None = None
    title: str | None = Field(None, max_length=120)
    creator: str | None = Field(None, max_length=80)
    rights_holder: str | None = Field(None, max_length=80)
    license: str = Field(..., max_length=60)
    license_url: str | None = Field(None, max_length=300)
    page: str | None = Field(None, max_length=300)
    attribution: str | None = Field(None, max_length=120)
    shows: str | None = Field(None, max_length=160)
    match: Literal["exact", "related", "generic"] | None = None
    attest: bool = False


class AssetPatchIn(StrictInput):
    status: Literal["ok", "excluded"] | None = None
    match: Literal["exact", "related", "generic"] | None = None


@router.get(f"{P}/videos/{{video_id}}/media")
def get_media(video_id: int, db: DbSession) -> dict[str, Any]:
    v = _video(db, video_id)
    return {"assets": tline.assets_of(v), "updated_at": (v.media or {}).get("updated_at")}


@router.post(f"{P}/videos/{{video_id}}/media")
def add_media(video_id: int, body: AssetIn, db: DbSession) -> dict[str, Any]:
    """Add footage or a photo you hold the rights to (it's used ahead of researched media on the next render)."""
    v = _video(db, video_id)
    rec = tline.add_asset(v, body.model_dump())
    brands.log(db, v.brand_id, f"Added media to “{v.topic}”: {rec['title']} ({rec['license']}).", video_id=v.id)
    db.commit()
    return {"asset": rec, "assets": tline.assets_of(v)}


@router.patch(f"{P}/videos/{{video_id}}/media/{{asset_id}}")
def patch_media(video_id: int, asset_id: str, body: AssetPatchIn, db: DbSession) -> dict[str, Any]:
    v = _video(db, video_id)
    rec = tline.set_asset(v, asset_id, body.model_dump(exclude_unset=True))
    db.commit()
    return {"asset": rec, "assets": tline.assets_of(v)}


class TimelineIn(StrictInput):
    timeline: dict[str, Any]


@router.put(f"{P}/videos/{{video_id}}/timeline")
def put_timeline(video_id: int, body: TimelineIn, db: DbSession) -> dict[str, Any]:
    """Save the owner's edit (shots, graphics, captions, narration, music). Render it with POST /rerender scope=timeline."""
    v = _video(db, video_id)
    if v.status in ("publishing", "sent_to_drafts", "published"):
        raise HTTPException(409, "This video is already on TikTok.")
    v.timeline = tline.validate(v, body.timeline)
    db.commit()
    return video_out(v, detail=True)


@router.delete(f"{P}/videos/{{video_id}}")
def delete_video(video_id: int, db: DbSession, runtime: Runtime) -> dict[str, Any]:
    v = _video(db, video_id)
    if v.status == "publishing":
        raise HTTPException(409, "TikTok is processing this video; wait for it to finish.")
    store = MediaStore(runtime.settings)
    for key in (v.video_key, v.thumb_key):
        if key:
            try:
                store.delete(key)
            except Exception:  # noqa: BLE001 - the row goes either way; an orphan file is harmless
                pass
    db.delete(v)
    db.commit()
    return {"deleted": True}


class CreateIn(StrictInput):
    category: str | None = Field(None, max_length=32)


@router.post(f"{P}/videos")
async def create_video(body: CreateIn, db: DbSession, runtime: Runtime) -> dict[str, Any]:
    """Make one video now (Content Studio › Create): the best fresh topic, rendered by the next free worker."""
    b = brands.ensure_default(db)
    st = brands.strategy(b)
    if body.category:
        if body.category not in defaults.CATEGORIES:
            raise HTTPException(400, "Unknown category.")
        st = {**st, "categories": [body.category]}
    a = brands.automation(db, b)
    topics = await discovery.discover(db, b.id, st, (a.insights or {}).get("category_multipliers"), _transport(runtime),
                                      trend_mult=(a.insights or {}).get("trend_multiplier"))
    if not topics:
        raise HTTPException(409, "No fresh topic with reliable sources right now. Try another category or later.")
    return _make_now(db, b, st, topics[0], "made by hand")


def _make_now(db: Session, b: Any, st: dict[str, Any], t: discovery.Topic, how: str) -> dict[str, Any]:
    v = BmVideo(brand_id=b.id, category=t.category, template=t.template, topic=t.topic[:300], topic_key=t.key, score=t.score, brief=t.brief,
                status="queued", stages={}, slot_at=None, publish_mode=st["publish_mode"], generation=1)
    db.add(v)
    db.flush()
    now = utcnow()
    queue.set_stage(v, "discover", "done", f"score {t.score:.2f} · {how}", now)
    queue.set_stage(v, "research", "done", f"{len(t.brief.get('facts') or [])} facts · {len(t.brief.get('sources') or [])} sources", now)
    queue.enqueue_render(db, v, manual=True)
    brands.log(db, b.id, f"You asked for a video: “{v.topic}”.", stage="discover", video_id=v.id)
    db.commit()
    return video_out(v, detail=True)


# --- trends (app/brand_manager/trends.py) ------------------------------------------------------------------------------------


@router.get(f"{P}/trends")
def get_trends(db: DbSession) -> dict[str, Any]:
    """Today's trend words (headline counts vs the past week, CoinGecko searches), the videos that used each, and what the
    analyzer learned about trend-riding videos."""
    b = brands.ensure_default(db)
    a = brands.automation(db, b)
    now = utcnow()
    used: dict[str, list[dict[str, Any]]] = {}
    for v in db.scalars(select(BmVideo).where(BmVideo.brand_id == b.id, BmVideo.created_at >= now - timedelta(days=30)).order_by(BmVideo.id.desc())):
        for t in ((v.brief or {}).get("trend") or {}).get("terms") or []:
            used.setdefault(t["term"], []).append({"id": v.id, "topic": v.topic, "status": v.status, "views": (v.metrics or {}).get("views")})
    ins = a.insights or {}
    scanned = db.scalar(select(func.max(BmTrendDay.updated_at)))
    return {"trends": [{**t, "videos": used.get(t["term"], [])[:5]} for t in trends.current(db, now, 40)],
            "scanned_at": scanned.isoformat() if scanned else None, "outlets": [n for n, _ in defaults.TREND_FEEDS] + ["CoinGecko searches"],
            "category_on": "finance_trends" in brands.strategy(b)["categories"],
            "learned": {"by_trend": ins.get("by_trend") or [], "multiplier": ins.get("trend_multiplier"), "words": ins.get("trend_words") or []}}


@router.post(f"{P}/trends/refresh")
async def refresh_trends(db: DbSession, runtime: Runtime) -> dict[str, Any]:
    r = await trends.refresh(db, utcnow(), _transport(runtime))
    return {**get_trends(db), "scan": r}


class TrendVideoIn(StrictInput):
    term: str = Field(..., min_length=1, max_length=80)


@router.post(f"{P}/trends/video")
def trend_video(body: TrendVideoIn, db: DbSession) -> dict[str, Any]:
    """Content Studio › Trends › Make a video: one story about this trend word, rendered by the next free worker."""
    b = brands.ensure_default(db)
    now = utcnow()
    t = next((x for x in trends.current(db, now, 200) if x["term"] == body.term), None)
    if t is None:
        raise HTTPException(404, "That trend isn't in today's scan any more.")
    topic = trends.story(t, now)
    if topic is None:
        raise HTTPException(409, f"“{t['label']}” has too few headlines for its own story ({trends.STORY_MIN_DOCS}+ from 2+ outlets needed). "
                                 "It still boosts the topics that mention it.")
    if db.scalar(select(func.count(BmVideo.id)).where(BmVideo.brand_id == b.id, BmVideo.topic_key == topic.key)):
        raise HTTPException(409, "There's already a video about this trend today.")
    return _make_now(db, b, brands.strategy(b), topic, "from a trend")


@router.get(f"{P}/calendar")
def calendar(db: DbSession, days: int = Query(14, ge=1, le=60)) -> dict[str, Any]:
    b = brands.ensure_default(db)
    st = brands.strategy(b)
    now = utcnow()
    rows = db.scalars(select(BmVideo).where(BmVideo.brand_id == b.id, BmVideo.slot_at >= now - timedelta(days=7),
                                            BmVideo.slot_at <= now + timedelta(days=days)).order_by(BmVideo.slot_at))
    return {"timezone": st["timezone"], "post_times": st["post_times"][: int(st["videos_per_day"])],
            "free_slots": [s.isoformat() for s in engine.slots(st, now, 24 * min(days, 3))], "items": [video_out(v) for v in rows]}


@router.get(f"{P}/analytics")
def get_analytics(db: DbSession) -> dict[str, Any]:
    b = brands.ensure_default(db)
    a = brands.automation(db, b)
    since = utcnow() - timedelta(days=60)
    followers = [{"at": m.at.isoformat(), "followers": m.followers} for m in db.scalars(
        select(BmMetric).where(BmMetric.brand_id == b.id, BmMetric.video_id.is_(None), BmMetric.at >= since).order_by(BmMetric.at))]
    vids = [video_out(v) for v in db.scalars(select(BmVideo).where(BmVideo.brand_id == b.id, BmVideo.metrics_at.is_not(None))
                                             .order_by(BmVideo.published_at.desc()).limit(100))]
    return {"insights": a.insights or analytics.analyze(db, b), "followers": followers, "videos": vids,
            "last_collected": a.last_analytics_at.isoformat() if a.last_analytics_at else None}


@router.post(f"{P}/analytics/collect")
async def collect_now(db: DbSession, runtime: Runtime) -> dict[str, Any]:
    b = brands.ensure_default(db)
    a = brands.automation(db, b)
    r = await analytics.collect(db, runtime.settings, b, _transport(runtime))
    a.insights, a.last_analytics_at = analytics.analyze(db, b), utcnow()
    db.commit()
    return {"result": r, "insights": a.insights}


@router.get(f"{P}/events")
def get_events(db: DbSession, limit: int = Query(100, ge=1, le=500)) -> dict[str, Any]:
    return {"events": brands.events(db, brands.ensure_default(db).id, limit=limit)}


# --- workers (registered by the owner) -------------------------------------------------------------------------------------


@router.get(f"{P}/workers")
def list_workers(db: DbSession) -> dict[str, Any]:
    now = utcnow()
    queued = db.scalar(select(func.count(BmJob.id)).where(BmJob.status == "queued")) or 0
    running = db.scalar(select(func.count(BmJob.id)).where(BmJob.status == "claimed")) or 0
    return {"workers": [queue.worker_out(w, now) for w in db.scalars(select(BmWorker).order_by(BmWorker.id))], "queued": queued, "running": running}


class WorkerIn(StrictInput):
    name: str = Field(..., min_length=1, max_length=60)
    cloud: bool = False


@router.post(f"{P}/workers")
def add_worker(body: WorkerIn, db: DbSession, runtime: Runtime) -> dict[str, Any]:
    w, token = queue.add_worker(db, body.name, body.cloud)
    brands.log(db, brands.ensure_default(db).id, f"Worker “{w.name}” registered.", commit=True)
    server = runtime.settings.bm_media_base_url.rstrip("/")
    return {"worker": queue.worker_out(w), "token": token,
            "command": f"python -m pricechat_worker --server {server} --token {token}"}


class WorkerEdit(StrictInput):
    enabled: bool


@router.patch(f"{P}/workers/{{worker_id}}")
def edit_worker(worker_id: int, body: WorkerEdit, db: DbSession) -> dict[str, Any]:
    w = db.get(BmWorker, worker_id)
    if w is None:
        raise HTTPException(404, "Not found.")
    w.enabled = body.enabled
    db.commit()
    return queue.worker_out(w)


@router.delete(f"{P}/workers/{{worker_id}}")
def delete_worker(worker_id: int, db: DbSession) -> dict[str, Any]:
    w = db.get(BmWorker, worker_id)
    if w is None:
        raise HTTPException(404, "Not found.")
    db.delete(w)
    db.commit()
    return {"deleted": True}


# --- the worker protocol (bearer token; outbound HTTPS from the worker only) ------------------------------------------------

Bearer = Annotated[str | None, Header(alias="Authorization")]


def _worker(db: Session, authorization: str | None) -> BmWorker:
    token = (authorization or "").removeprefix("Bearer ").strip()
    w = queue.worker_for(db, token)
    if w is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Unknown or disabled worker token.")
    return w


class ClaimIn(StrictInput):
    info: dict[str, Any] | None = None


@router.post(f"{P}/worker/claim")
def worker_claim(body: ClaimIn, db: DbSession, runtime: Runtime, authorization: Bearer = None) -> Response:
    w = _worker(db, authorization)
    job = queue.claim(db, runtime.settings, w, body.info)
    if job is None:
        return Response(status_code=204)
    return Response(content=json.dumps(job, default=str), media_type="application/json")


class ProgressIn(StrictInput):
    stage: str = Field(..., max_length=20)
    status: Literal["running", "done", "failed", "retrying", "skipped"]
    note: str = Field("", max_length=500)


def _q(exc: queue.QueueError) -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, str(exc))


@router.post(f"{P}/worker/jobs/{{job_id}}/progress")
def worker_progress(job_id: int, body: ProgressIn, db: DbSession, runtime: Runtime, authorization: Bearer = None) -> dict[str, Any]:
    w = _worker(db, authorization)
    try:
        return queue.progress(db, runtime.settings, w, job_id, body.stage, body.status, body.note)
    except queue.QueueError as exc:
        raise _q(exc) from exc


class ScriptIn(StrictInput):
    script: dict[str, Any]


@router.post(f"{P}/worker/jobs/{{job_id}}/script")
def worker_script(job_id: int, body: ScriptIn, db: DbSession, runtime: Runtime, authorization: Bearer = None) -> dict[str, Any]:
    w = _worker(db, authorization)
    try:
        return queue.submit_script(db, runtime.settings, w, job_id, body.script)
    except queue.QueueError as exc:
        raise _q(exc) from exc


@router.post(f"{P}/worker/jobs/{{job_id}}/result")
def worker_result(job_id: int, db: DbSession, runtime: Runtime, video: UploadFile = File(...), thumb: UploadFile | None = File(None),
                  meta: str = Form(...), authorization: Bearer = None) -> dict[str, Any]:
    w = _worker(db, authorization)
    try:
        info = json.loads(meta)
    except ValueError:
        raise HTTPException(400, "meta must be JSON.") from None
    try:
        return queue.complete(db, runtime.settings, w, job_id, video.file, thumb.file if thumb else None, info)
    except queue.QueueError as exc:
        raise _q(exc) from exc


class LibraryIn(StrictInput):
    assets: list[dict[str, Any]] = Field(..., max_length=200)


@router.post(f"{P}/worker/jobs/{{job_id}}/media")
def worker_media(job_id: int, body: LibraryIn, db: DbSession, authorization: Bearer = None) -> dict[str, Any]:
    w = _worker(db, authorization)
    try:
        return queue.library(db, w, job_id, body.assets)
    except queue.QueueError as exc:
        raise _q(exc) from exc


class FailIn(StrictInput):
    error: str = Field(..., max_length=2000)
    retryable: bool = True


@router.post(f"{P}/worker/jobs/{{job_id}}/fail")
def worker_fail(job_id: int, body: FailIn, db: DbSession, authorization: Bearer = None) -> dict[str, Any]:
    w = _worker(db, authorization)
    try:
        return queue.fail(db, w, job_id, body.error, body.retryable)
    except queue.QueueError as exc:
        raise _q(exc) from exc

