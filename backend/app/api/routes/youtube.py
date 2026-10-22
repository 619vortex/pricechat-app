"""YouTube (read-only): connection, channel selection, sync. Never uploads or edits anything."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from pydantic import StringConstraints

from app.api.deps import DbSession, Runtime
from app.api.schemas import StrictInput
from app.models.routing import ScheduledJob
from app.services.activity import ActivityLogger
from app.youtube import service as yt
from app.youtube.client import YouTubeAuthError, YouTubeError, YouTubeNotConfigured, YouTubeQuotaError

router = APIRouter(prefix="/youtube", tags=["youtube"])


def _fail(exc: Exception) -> HTTPException:
    if isinstance(exc, YouTubeNotConfigured):
        return HTTPException(status.HTTP_409_CONFLICT, str(exc))
    if isinstance(exc, YouTubeAuthError):
        return HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc))
    if isinstance(exc, YouTubeQuotaError):
        return HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, str(exc))
    if isinstance(exc, YouTubeError):
        return HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc))
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))


def _status(db, runtime):  # type: ignore[no-untyped-def]
    row = db.get(ScheduledJob, "youtube.sync")
    nxt = row.next_run_at if row is not None and row.enabled and runtime.scheduler is not None and runtime.scheduler._loop_task is not None else None
    return yt.status(db, runtime.settings, runtime.youtube, nxt)


@router.get("")
def get_status(db: DbSession, runtime: Runtime) -> dict[str, object]:
    out = _status(db, runtime)
    from sqlalchemy import select

    from app.models.content_items import ContentItem
    from app.models.enums import Platform

    channel = (out.get("channel") or {}).get("id")
    rows = db.scalars(select(ContentItem).where(ContentItem.platform == Platform.YOUTUBE, ContentItem.channel_id == channel)
                      .order_by(ContentItem.published_at.desc()).limit(12)) if channel else []
    out["recent"] = [yt.item_out(i) for i in rows]
    return out


class ApiKeyIn(StrictInput):
    channel: Annotated[str, StringConstraints(min_length=2, max_length=200)]
    api_key: Annotated[str, StringConstraints(max_length=200)] | None = None  # omit to keep the saved key


@router.put("/api-key")
async def connect_api_key(payload: ApiKeyIn, db: DbSession, runtime: Runtime) -> dict[str, object]:
    try:
        await yt.connect_api_key(db, runtime.settings, runtime.youtube, payload.channel, payload.api_key)
    except (YouTubeError, ValueError) as exc:
        raise _fail(exc) from exc
    return _status(db, runtime)


class OAuthClientIn(StrictInput):
    client_id: Annotated[str, StringConstraints(min_length=10, max_length=300)]
    client_secret: Annotated[str, StringConstraints(max_length=300)] | None = None


@router.put("/oauth-client")
def save_oauth_client(payload: OAuthClientIn, db: DbSession, runtime: Runtime) -> dict[str, object]:
    try:
        yt.save_oauth_client(db, runtime.settings, payload.client_id, payload.client_secret)
    except ValueError as exc:
        raise _fail(exc) from exc
    return _status(db, runtime)


@router.post("/oauth/start")
def oauth_start(runtime: Runtime) -> dict[str, str]:
    """Returns Google's consent-screen URL; the dashboard opens it. Read-only scope."""
    try:
        return {"url": yt.start_oauth(runtime.settings, runtime.youtube)}
    except ValueError as exc:
        raise _fail(exc) from exc


@router.get("/oauth/callback", include_in_schema=False)
async def oauth_callback(db: DbSession, runtime: Runtime, code: str | None = None, state: str | None = None,
                         error: str | None = None) -> RedirectResponse:
    """Google redirects the browser here after consent; we send it back to the dashboard."""
    back = f"{runtime.settings.dashboard_url.rstrip('/')}/youtube"
    if error or not code or not state:
        ActivityLogger(db).log("youtube_sync_failed", f"Google sign-in was not completed ({error or 'no code'})", actor="dashboard")
        return RedirectResponse(f"{back}?oauth=cancelled", status_code=303)
    try:
        await yt.finish_oauth(db, runtime.settings, runtime.youtube, code, state)
    except YouTubeError as exc:
        ActivityLogger(db).log("youtube_sync_failed", f"Google sign-in failed: {exc}", actor="dashboard")
        return RedirectResponse(f"{back}?oauth=failed", status_code=303)
    return RedirectResponse(f"{back}?oauth=ok", status_code=303)


class ChannelIn(StrictInput):
    channel_id: Annotated[str, StringConstraints(pattern=r"^UC[\w-]{22}$")]


@router.post("/channel")
async def choose_channel(payload: ChannelIn, db: DbSession, runtime: Runtime) -> dict[str, object]:
    try:
        await yt.select_channel(db, runtime.settings, runtime.youtube, payload.channel_id)
    except (YouTubeError, ValueError) as exc:
        raise _fail(exc) from exc
    return _status(db, runtime)


@router.post("/test")
async def test(db: DbSession, runtime: Runtime) -> dict[str, object]:
    try:
        await yt.test_connection(db, runtime.settings, runtime.youtube)
    except (YouTubeError, ValueError) as exc:
        raise _fail(exc) from exc
    return _status(db, runtime)


@router.post("/sync")
async def sync_now(db: DbSession, runtime: Runtime) -> dict[str, object]:
    """Sync now (same as the scheduled job). The website reads stored content, so it never waits for this."""
    try:
        report = await yt.sync(db, runtime.settings, runtime.youtube)
    except (YouTubeError, ValueError) as exc:
        ActivityLogger(db).log("youtube_sync_failed", f"YouTube sync failed: {exc}", actor="dashboard")
        raise _fail(exc) from exc
    return {"result": report.as_dict(), **_status(db, runtime)}


@router.delete("")
def disconnect(db: DbSession, runtime: Runtime) -> dict[str, object]:
    yt.disconnect(db, runtime.settings, runtime.youtube)
    return _status(db, runtime)


@router.get("/videos")
def videos(db: DbSession, runtime: Runtime, limit: int = Query(50, ge=1, le=500)) -> list[dict[str, object]]:
    from sqlalchemy import select

    from app.models.content_items import ContentItem
    from app.models.enums import Platform

    rows = db.scalars(select(ContentItem).where(ContentItem.platform == Platform.YOUTUBE).order_by(ContentItem.published_at.desc()).limit(limit))
    return [yt.item_out(i) for i in rows]
