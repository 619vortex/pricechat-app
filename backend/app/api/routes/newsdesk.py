"""Admin › X › News Desk. Owner only (admin_guard: nothing here is on the public list)."""

from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import Field

from app.api.deps import DbSession, Runtime
from app.api.schemas import StrictInput
from app.db.base import utcnow
from app.models.newsdesk import NdPost
from app.newsdesk import engine, sources
from app.newsdesk import telegram as telegram_lane
from app.services.agent_service import get_agent_config
from app.api.routes.newsdesk_editorial import router as editorial_router  # noqa: E402,I001

router = APIRouter(prefix="/x/newsdesk", tags=["newsdesk"])
router.include_router(editorial_router)  # story history, preview queue, the owner's block (app/api/routes/newsdesk_editorial)


@router.get("/render/health")
def render_health(db: DbSession) -> dict[str, Any]:
    """The GPU PC's News Desk pictures: worker online, queue, success rate, speed, recent jobs (app/newsdesk/render_queue)."""
    from app.newsdesk import render_queue

    return {**render_queue.health(db), "enabled": render_queue.enabled(engine.settings_row(db).state or {})}


class RenderInput(StrictInput):
    enabled: bool


@router.put("/render")
def render_switch(payload: RenderInput, db: DbSession) -> dict[str, Any]:
    """Admin: GPU backgrounds on or off. Off: no jobs are queued and posts use the desk's own drawn visuals at once."""
    from app.newsdesk import render_queue

    s = engine.settings_row(db)
    s.state = {**(s.state or {}), "gpu_render": payload.enabled}
    db.commit()
    return {**render_queue.health(db), "enabled": payload.enabled}


@router.get("/render/jobs/{job_id}/image")
def render_image(job_id: int, db: DbSession) -> dict[str, Any]:
    """A finished background (text-free, as the GPU made it), for the Admin panel."""
    from app.models.newsdesk import NdRenderJob

    job = db.get(NdRenderJob, job_id)
    if job is None or not job.image:
        raise HTTPException(404, "No image for this job (not finished, or older than 7 days).")
    import base64

    return {"id": job.id, "image": "data:image/jpeg;base64," + base64.b64encode(job.image).decode(), "scene": (job.payload or {}).get("scene"),
            "prompt": (job.payload or {}).get("prompt"), "result": job.result or {}}


@router.get("")
def overview(db: DbSession, runtime: Runtime) -> dict[str, Any]:
    return engine.overview(db, runtime.settings)


class SettingsInput(StrictInput):
    automation: bool | None = None
    mode: Literal["dry_run", "live"] | None = None
    kill_switch: bool | None = None
    market_pause: bool | None = None  # no desk-measured market post (moves, data posts, market checks, liquidations) released or delivered
    resume: bool | None = None
    target_per_day: int | None = Field(None, ge=1, le=48)
    max_breaking_per_day: int | None = Field(None, ge=0, le=24)
    breaking_gap_minutes: int | None = Field(None, ge=5, le=720)
    sensitivity: Literal["low", "medium", "high"] | None = None
    categories: dict[str, bool] | None = None
    feeds: list[dict[str, Any]] | None = None
    watchlist: list[dict[str, Any]] | None = None


def _feeds_ok(feeds: list[dict[str, Any]]) -> None:
    for f in feeds:
        if not str(f.get("url", "")).startswith("https://") or not str(f.get("name", "")).strip():
            raise HTTPException(400, "Every feed needs a name and an https:// address.")


def _watch_ok(items: list[dict[str, Any]]) -> None:
    for w in items:
        if not w.get("symbol") or not w.get("name") or w.get("group") not in sources.THRESHOLDS or w.get("category") not in sources.CATEGORIES:
            raise HTTPException(400, "Every ticker needs a symbol, a name, a group and a category.")


@router.put("/settings")
def update(payload: SettingsInput, db: DbSession, runtime: Runtime) -> dict[str, Any]:
    s = engine.settings_row(db)
    data = payload.model_dump(exclude_none=True)
    if "feeds" in data:
        _feeds_ok(data["feeds"])
    if "watchlist" in data:
        _watch_ok(data["watchlist"])
    if data.pop("resume", False):
        s.paused_until, s.pause_reason = None, None
    if "market_pause" in data:
        s.state = {**(s.state or {}), "market_pause": bool(data.pop("market_pause"))}
    if "categories" in data:
        data["categories"] = {c: bool(data["categories"].get(c, False)) for c in sources.CATEGORIES}
    for k, v in data.items():
        setattr(s, k, v)
    db.commit()
    return engine.overview(db, runtime.settings)


@router.get("/telegram")
def telegram_overview(db: DbSession) -> dict[str, Any]:
    return telegram_lane.overview(db)


class TelegramInput(StrictInput):
    enabled: bool | None = None
    max_per_day: int | None = Field(None, ge=1, le=48)
    gap_minutes: int | None = Field(None, ge=5, le=720)
    resume: bool | None = None


@router.put("/telegram")
def telegram_update(payload: TelegramInput, db: DbSession) -> dict[str, Any]:
    s = telegram_lane.settings_row(db)
    data = payload.model_dump(exclude_none=True)
    if data.pop("resume", False):
        s.state = {k: v for k, v in (s.state or {}).items() if k not in ("paused_until", "problem")}
    for k, v in data.items():
        setattr(s, k, v)
    db.commit()
    return telegram_lane.overview(db)


class NetworkInput(StrictInput):
    x_links: bool | None = None  # source links in X posts ($0.20 a post with a link, $0.015 without)
    emergency: bool | None = None  # stop Telegram and X at once until turned off
    data_posts: bool | None = None  # Viral Engine 2.0: data posts and the daily MARKETS GONE MAD
    experiments: bool | None = None  # Viral Engine 2.0: about one story in five tries its runner-up factual hook
    post_style: Literal["wire", "clean", "classic"] | None = None  # wire: short factual headlines, earned tags, real charts or text only; clean: picture first; classic: the older layout


@router.get("/network")
def network_overview(db: DbSession) -> dict[str, Any]:
    from app.newsdesk import network

    return network.state(db)


@router.put("/network")
def network_update(payload: NetworkInput, db: DbSession) -> dict[str, Any]:
    """Price Chat News switches: links on X, and the emergency pause for both platforms."""
    from app.newsdesk import network

    return network.configure(db, **payload.model_dump(exclude_none=True))


@router.get("/viral")
def viral_overview(db: DbSession) -> dict[str, Any]:
    """Viral Engine 2.0 in Admin: the hooks chosen for recent stories (with the four that lost and why), what the audience engaged with
    per hook, format, question and links, the experiments, and today's data-post candidates (from the closes already fetched)."""
    from app.newsdesk import viral

    return viral.overview(db, utcnow())


class TextInput(StrictInput):
    text: str = Field(min_length=1, max_length=280)


def _post(db: DbSession, post_id: int) -> NdPost:
    post = db.get(NdPost, post_id)
    if post is None:
        raise HTTPException(404, "No such post.")
    return post


@router.put("/posts/{post_id}")
def edit(post_id: int, payload: TextInput, db: DbSession) -> dict[str, Any]:
    post = _post(db, post_id)
    if post.status != "queued":
        raise HTTPException(409, "Only a queued post can be edited.")
    result = engine.edit_post(db, post, payload.text)
    if not result.ok:
        raise HTTPException(400, result.reason)
    db.commit()
    return engine.post_out(post)


@router.post("/posts")
def manual(payload: TextInput, db: DbSession) -> dict[str, Any]:
    post, result = engine.new_manual(db, payload.text)
    if post is None:
        raise HTTPException(400, result.reason)
    db.commit()
    return engine.post_out(post)


@router.post("/posts/{post_id}/reject")
def reject(post_id: int, db: DbSession) -> dict[str, Any]:
    post = _post(db, post_id)
    if post.status != "queued":
        raise HTTPException(409, "Only a queued post can be rejected.")
    post.status = "rejected"
    db.commit()
    return engine.post_out(post)


@router.post("/posts/{post_id}/publish")
async def publish_now(post_id: int, db: DbSession, runtime: Runtime) -> dict[str, Any]:
    """Override: this post goes next, now, whatever the spacing says. Every other safety still applies."""
    post = _post(db, post_id)
    if post.status != "queued":
        raise HTTPException(409, "Only a queued post can be published.")
    s = engine.settings_row(db)
    db.commit()
    if s.kill_switch:
        raise HTTPException(409, "The emergency stop is on.")
    await engine.publish(db, s, post, runtime.settings, runtime.x, get_agent_config(db).id, utcnow())
    db.refresh(post)
    return engine.post_out(post)


class CorrectionInput(StrictInput):
    action: Literal["tg_edit", "tg_reply", "tg_delete", "x_delete", "x_reply"]
    text: str | None = Field(None, max_length=8000)


class CorrectionConfirm(CorrectionInput):
    confirm_token: str = Field(min_length=1, max_length=200)


def _event(db: DbSession, event_id: int) -> Any:
    from app.models.newsdesk import NdEvent

    ev = db.get(NdEvent, event_id)
    if ev is None:
        raise HTTPException(404, "No such event.")
    return ev


@router.post("/events/{event_id}/corrections/preview")
async def correction_preview(event_id: int, payload: CorrectionInput, db: DbSession) -> dict[str, Any]:
    """Step one of a correction (owner, 2026-11-21, after Telegram post 204): exactly what would be edited, replied or deleted, and a token
    to confirm it with (10 minutes, once). Sends nothing."""
    from app.newsdesk import corrections

    return await corrections.preview(db, _event(db, event_id), payload.action, payload.text)


@router.post("/events/{event_id}/corrections")
async def correction_confirm(event_id: int, payload: CorrectionConfirm, db: DbSession, runtime: Runtime) -> dict[str, Any]:
    """Step two: do it, only with the preview's token. Every attempt is in the audit log, done or not."""
    from app.newsdesk import corrections

    ev = _event(db, event_id)
    try:
        return await corrections.execute(db, ev, payload.action, payload.text, payload.confirm_token, settings=runtime.settings, x_runtime=runtime.x)
    except corrections.CorrectionError as e:
        raise HTTPException(e.status, str(e)) from None


@router.get("/corrections")
def corrections_log(db: DbSession) -> list[dict[str, Any]]:
    """Every correction attempt, newest first."""
    from app.newsdesk import corrections

    return corrections.audit_list(db)


@router.get("/events/{event_id}/corrections")
def event_corrections(event_id: int, db: DbSession) -> list[dict[str, Any]]:
    from app.newsdesk import corrections

    return corrections.audit_list(db, event_id)


@router.get("/test-post")
async def test_post_preview(db: DbSession, event_id: int | None = None) -> dict[str, Any]:
    """What one test post to X would carry (text and picture) for the latest event Telegram delivered, or the one named. Sends nothing."""
    preview = await engine.test_post_preview(db, event_id, utcnow())
    if preview is None:
        raise HTTPException(404, "No event Telegram has delivered yet.")
    return preview


class TestPostInput(StrictInput):
    event_id: int
    confirm: Literal[True]


@router.post("/test-post")
async def send_test_post(payload: TestPostInput, db: DbSession, runtime: Runtime) -> dict[str, Any]:
    """The owner's one real test post to X, even in dry run. Every other safety (emergency stop, X kill switch, outbound switch) still applies."""
    s = engine.settings_row(db)
    db.commit()
    if s.kill_switch:
        raise HTTPException(409, "The emergency stop is on.")
    try:
        post = await engine.send_test_post(db, s, runtime.settings, runtime.x, get_agent_config(db).id, payload.event_id, utcnow())
    except ValueError as e:
        raise HTTPException(409, str(e)) from None
    return engine.post_out(post)
