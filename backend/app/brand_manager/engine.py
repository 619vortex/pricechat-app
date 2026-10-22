"""START AUTO / STOP AUTO and the scheduler tick that runs the whole pipeline (docs/BRAND_MANAGER.md).

tick (every minute, on the backend; light work only — no AI, no rendering):
  1. checks the brand can still publish; if not, status BLOCKED with the exact reason (never a silent stop);
  2. returns timed-out worker jobs to the queue;
  3. fills the next 24 h of posting slots: discover + research topics, create videos, queue render jobs for workers;
  4. publishes videos whose slot has come, through the authorized TikTok flow only (drafts: inbox upload;
     direct: only with the owner's approved choices from TikTok's posting screen);
  5. polls TikTok for each upload's real status (published only when TikTok says PUBLISH_COMPLETE);
  6. every 6 h collects analytics and re-runs the performance analyzer.
"""

import asyncio
import logging
import time
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.brand_manager import brands, discovery, queue, tiktok
from app.brand_manager.store import MediaStore, media_url
from app.db.base import utcnow
from app.models.brand_manager import BmAutomation, BmBrand, BmEvent, BmJob, BmVideo, BmWorker

logger = logging.getLogger(__name__)
LIVE = ("queued", "rendering", "needs_review", "ready", "approved", "publishing", "sent_to_drafts", "published")
UNPUBLISHED = ("queued", "rendering", "needs_review", "ready", "approved")
POLL_SECONDS = 50  # the tick has 170 s: polling, publishing and discovery share it
DISCOVERY_SECONDS = 90
CAP_MESSAGE = "Daily posting cap reached; the rest waits for tomorrow."
DAILY_POST_CAP = 10  # TikTok caps posts per creator (~15/day across all apps); stay well under it


# --- checks --------------------------------------------------------------------------------------------------------------


def checks(db: Session, settings: Any, b: BmBrand, now: datetime | None = None) -> dict[str, list[str]]:
    """{blocking: [...], warnings: [...]}: the exact reasons START AUTO can't run (or what to watch)."""
    now = now or utcnow()
    st = brands.strategy(b)
    a = brands.automation(db, b)
    acc = tiktok.account(db, b.id)
    blocking: list[str] = []
    warnings: list[str] = []
    if a.emergency_stop:
        blocking.append("The emergency stop is on. Release it in Settings › Safety to start again.")
    if not tiktok.app_configured(settings):
        blocking.append("The TikTok app isn't set up: add its Client key and Client secret on Settings › API keys.")
    elif acc.status != "connected":
        blocking.append("TikTok isn't connected" + (f" ({acc.last_error})" if acc.last_error else "") + ". Connect it on the TikTok tab.")
    else:
        need = "video.upload" if st["publish_mode"] == "drafts" else "video.publish"
        if need not in acc.scopes:
            blocking.append(f"TikTok didn't grant the {need} permission. Reconnect TikTok and allow it (the app needs the Content Posting API).")
        if acc.refresh_expires_at and acc.refresh_expires_at < now + timedelta(days=3):
            warnings.append("TikTok's sign-in expires within 3 days: reconnect TikTok soon.")
        if "video.list" not in acc.scopes:
            warnings.append("Analytics need the video.list permission (reconnect TikTok to grant it).")
    if st["publish_mode"] == "drafts" and a.upload_consent_at is None:
        blocking.append("Confirm that Brand Manager may upload finished videos to your TikTok drafts (the checkbox next to START AUTO).")
    if not MediaStore(settings).available:
        blocking.append("Video storage isn't set up (the S3 bucket: PORTFOLIO_BUCKET…).")
    if not st.get("categories"):
        blocking.append("Pick at least one content category in Settings › Content.")
    if not st.get("post_times"):
        blocking.append("Add at least one posting time in Settings › Publishing.")
    try:
        ZoneInfo(st["timezone"])
    except Exception:  # noqa: BLE001
        blocking.append(f"Unknown time zone “{st['timezone']}” in Settings › General.")
    workers = list(db.scalars(select(BmWorker).where(BmWorker.enabled.is_(True))))
    if not workers:
        warnings.append("No AI worker is registered yet: videos will wait in the queue. Add the DGX Spark in Settings › AI.")
    elif not any(w.last_seen_at and now - w.last_seen_at < timedelta(minutes=3) for w in workers):
        warnings.append("No AI worker is online right now: videos wait safely in the queue until one connects.")
    if st["publish_mode"] == "direct" and not st.get("tiktok_audited"):
        warnings.append("TikTok hasn't audited this app yet, so direct posts can only be private (only you can see them).")
    if st["publish_mode"] == "direct":
        warnings.append("Direct posting: TikTok requires your approval of each post (Content Studio › Approve).")
    return {"blocking": blocking, "warnings": warnings}


def automation_out(db: Session, settings: Any, b: BmBrand) -> dict[str, Any]:
    a = brands.automation(db, b)
    c = checks(db, settings, b)
    return {"running": a.running, "status": a.status, "reason": a.reason, "started_at": a.started_at.isoformat() if a.started_at else None,
            "last_tick_at": a.last_tick_at.isoformat() if a.last_tick_at else None, "emergency_stop": a.emergency_stop,
            "upload_consent": a.upload_consent_at is not None, **c}


def start(db: Session, settings: Any, b: BmBrand, consent: bool = False) -> dict[str, Any]:
    a = brands.automation(db, b)
    if consent and a.upload_consent_at is None:
        a.upload_consent_at = utcnow()
        brands.log(db, b.id, "You allowed Brand Manager to upload finished videos to your TikTok drafts.")
    db.commit()
    c = checks(db, settings, b)
    if c["blocking"]:
        a.status, a.reason = ("blocked" if a.running else "stopped"), c["blocking"][0]
        db.commit()
        return {"started": False, **automation_out(db, settings, b)}
    a.running, a.status, a.reason, a.started_at = True, "running", "", utcnow()
    a.last_discovery_at = None  # discover right away
    brands.log(db, b.id, "AUTO started.")
    db.commit()
    return {"started": True, **automation_out(db, settings, b)}


def stop(db: Session, settings: Any, b: BmBrand) -> dict[str, Any]:
    a = brands.automation(db, b)
    a.running, a.status, a.reason, a.stopped_at = False, "stopped", "", utcnow()
    brands.log(db, b.id, "AUTO stopped: no new videos start and nothing new is published. Videos already rendering finish.")
    db.commit()
    return automation_out(db, settings, b)


def emergency(db: Session, settings: Any, b: BmBrand, on: bool) -> dict[str, Any]:
    a = brands.automation(db, b)
    if on:
        a.running, a.status, a.emergency_stop, a.stopped_at = False, "stopped", True, utcnow()
        a.reason = "Emergency stop."
        n = 0
        for job in db.scalars(select(BmJob).join(BmVideo, BmVideo.id == BmJob.video_id).where(BmVideo.brand_id == b.id, BmJob.status == "queued")):
            job.status, job.finished_at, job.error = "cancelled", utcnow(), "Emergency stop"
            n += 1
        for v in db.scalars(select(BmVideo).where(BmVideo.brand_id == b.id, BmVideo.status.in_(("queued", "ready", "approved")))):
            if v.status == "queued":
                v.status = "cancelled"
            v.slot_at = None if v.status != "cancelled" else v.slot_at
        brands.log(db, b.id, f"EMERGENCY STOP: {n} queued jobs cancelled, nothing will be published. Rendering in progress is told to stop.", level="warn")
    else:
        a.emergency_stop, a.reason = False, ""
        brands.log(db, b.id, "Emergency stop released. Press START AUTO to run again.")
    db.commit()
    return automation_out(db, settings, b)


# --- planning --------------------------------------------------------------------------------------------------------------


def slots(st: dict[str, Any], now: datetime, hours: int = 24) -> list[datetime]:
    """The posting times in the next `hours`, in UTC."""
    tz = ZoneInfo(st["timezone"])
    local_now = now.astimezone(tz)
    times = sorted(st.get("post_times") or [])[: max(1, int(st.get("videos_per_day", 3)))]
    out = []
    for d in range(0, 3):
        day = (local_now + timedelta(days=d)).date()
        for t in times:
            hh, mm = (int(x) for x in t.split(":"))
            at = datetime(day.year, day.month, day.day, hh, mm, tzinfo=tz).astimezone(ZoneInfo("UTC"))
            if now < at <= now + timedelta(hours=hours):
                out.append(at)
    return out


async def plan(db: Session, settings: Any, b: BmBrand, a: BmAutomation, now: datetime, transport: Any = None) -> int:
    st = brands.strategy(b)
    upcoming = slots(st, now)
    taken = {v.slot_at for v in db.scalars(select(BmVideo).where(BmVideo.brand_id == b.id, BmVideo.status.in_(LIVE), BmVideo.slot_at >= now))}
    free = [s for s in upcoming if s not in taken]
    # finished videos without a slot (after an emergency stop, or made by hand) take the next free slots first
    for v in db.scalars(select(BmVideo).where(BmVideo.brand_id == b.id, BmVideo.status.in_(("ready", "approved", "needs_review")),
                                              BmVideo.slot_at.is_(None)).order_by(BmVideo.id)).all():
        if not free:
            break
        v.slot_at = free.pop(0)
    if not free:
        db.commit()
        return 0
    if a.last_discovery_at and now - a.last_discovery_at < timedelta(minutes=30):
        return 0
    a.last_discovery_at = now
    db.commit()  # a discovery cut off by the job timeout still waits 30 minutes, instead of starving publishing every tick
    perf = (a.insights or {}).get("category_multipliers") or {}
    try:
        topics = await asyncio.wait_for(discovery.discover(db, b.id, st, perf, transport, now,
                                                           trend_mult=(a.insights or {}).get("trend_multiplier")), DISCOVERY_SECONDS)
    except Exception as exc:  # noqa: BLE001 - a source being down must not stop the brand
        logger.exception("discovery")
        brands.log(db, b.id, f"Topic discovery failed ({type(exc).__name__}); trying again in 30 minutes.", level="warn", stage="discover")
        db.commit()
        return 0
    made = 0
    used_cats: dict[str, int] = {}
    for slot in free:
        # variety: prefer a category not already used in this batch
        pick = next((t for t in topics if used_cats.get(t.category, 0) == 0), None) or (topics[0] if topics else None)
        if pick is None:
            brands.log(db, b.id, "No new topic with reliable sources right now; checking again in 30 minutes.", level="warn", stage="discover")
            break
        topics.remove(pick)
        used_cats[pick.category] = used_cats.get(pick.category, 0) + 1
        v = BmVideo(brand_id=b.id, category=pick.category, template=pick.template, topic=pick.topic[:300], topic_key=pick.key, score=pick.score,
                    brief=pick.brief, status="queued", stages={}, slot_at=slot, publish_mode=st["publish_mode"], generation=1)
        db.add(v)
        db.flush()
        queue.set_stage(v, "discover", "done", f"score {pick.score:.2f} · {pick.category}", now)
        queue.set_stage(v, "research", "done", f"{len(pick.brief.get('facts') or [])} facts · {len(pick.brief.get('sources') or [])} sources", now)
        queue.enqueue_render(db, v)
        brands.log(db, b.id, f"New video planned for {slot.astimezone(ZoneInfo(st['timezone'])):%a %H:%M}: “{v.topic}”.", stage="discover", video_id=v.id)
        made += 1
    db.commit()
    return made


# --- publishing ------------------------------------------------------------------------------------------------------------


def _published_today(db: Session, brand_id: int, now: datetime) -> int:
    """Videos sent to TikTok in the last 24 h, from the publish log (updated_at moves whenever analytics touch an old video)."""
    return db.scalar(select(func.count(BmEvent.id)).where(BmEvent.brand_id == brand_id, BmEvent.stage == "publish",
                                                         BmEvent.message.like("Sent “%"), BmEvent.at >= now - timedelta(hours=24))) or 0


async def publish(db: Session, settings: Any, b: BmBrand, v: BmVideo, transport: Any = None) -> None:
    """Send one ready video to TikTok through the authorized flow. Raises TikTokError with TikTok's reason."""
    st = brands.strategy(b)
    acc = tiktok.account(db, b.id)
    url = media_url(settings, v.id)
    mode = v.publish_mode or st["publish_mode"]
    prev = v.status
    # claim it: only one sender (the tick or Publish now) gets it, and a send cut off midway is never repeated blindly
    won = db.execute(update(BmVideo).where(BmVideo.id == v.id, BmVideo.status.in_(("ready", "approved")), BmVideo.publish_id.is_(None))
                     .values(status="publishing", publish_status="SENDING", updated_at=utcnow())).rowcount
    db.commit()
    if not won:
        raise tiktok.TikTokError("This video is already being sent to TikTok.", "already_sending")
    try:
        await _send(db, settings, st, acc, v, url, mode, transport)
    except Exception:  # TikTok said no before taking it: back to where it was (a cancelled send stays SENDING, see tick)
        v.status, v.publish_status = prev, None
        db.commit()
        raise
    v.status, v.publish_mode, v.publish_status = "publishing", mode, "PROCESSING"
    queue.set_stage(v, "publish", "running", "Sent to TikTok; waiting for TikTok to process it.")
    brands.log(db, b.id, f"Sent “{v.topic}” to TikTok ({'your drafts' if mode == 'drafts' else 'direct post'}).", stage="publish", video_id=v.id)
    db.commit()


async def _send(db: Session, settings: Any, st: dict[str, Any], acc: Any, v: BmVideo, url: str, mode: str, transport: Any) -> None:
    if mode == "direct":
        post = v.post_settings or {}
        if not v.approved_at or not post.get("privacy_level"):
            raise tiktok.TikTokError("Direct posts need your approval with TikTok's posting choices (Content Studio › Approve).", "needs_approval")
        info = await tiktok.creator_info(db, settings, acc, transport)  # fresh, as TikTok requires
        if post["privacy_level"] not in (info.get("privacy_level_options") or []):
            raise tiktok.TikTokError("The chosen privacy setting isn't available on this TikTok account any more. Approve again.", "privacy")
        if not st.get("tiktok_audited") and post["privacy_level"] != "SELF_ONLY":
            raise tiktok.TikTokError("Until TikTok audits the app, direct posts can only be private (Only me).", "unaudited")
        if info.get("max_video_post_duration_sec") and (v.duration or 0) > float(info["max_video_post_duration_sec"]):
            raise tiktok.TikTokError("The video is longer than this account may post.", "duration")
        v.publish_id = await tiktok.direct_post(db, settings, acc, url, {**post, "title": post.get("title") or _caption(v)}, transport)
    else:
        v.publish_id = await tiktok.upload_to_inbox(db, settings, acc, url, transport)


def _caption(v: BmVideo) -> str:
    s = v.script or {}
    tags = " ".join(s.get("hashtags") or [])
    return f"{s.get('title') or v.topic}\n{s.get('description') or ''}\n{tags}".strip()[:2200]


async def poll(db: Session, settings: Any, b: BmBrand, v: BmVideo, transport: Any = None) -> None:
    acc = tiktok.account(db, b.id)
    data = await tiktok.publish_status(db, settings, acc, v.publish_id or "", transport)
    status = str(data.get("status") or "")
    v.publish_status = status
    if status == "SEND_TO_USER_INBOX":
        if v.status != "sent_to_drafts":
            v.status = "sent_to_drafts"
            queue.set_stage(v, "publish", "done", "In your TikTok inbox: open TikTok to finish posting.")
            brands.log(db, b.id, f"“{v.topic}” is in your TikTok inbox. Open the TikTok app to post it.", stage="publish", video_id=v.id)
    elif status == "PUBLISH_COMPLETE":
        ids = data.get("publicaly_available_post_id") or data.get("publicly_available_post_id") or []
        v.post_id = str(ids[0]) if ids else v.post_id
        if v.status != "published":
            v.status, v.published_at = "published", utcnow()
            private = (v.post_settings or {}).get("privacy_level") == "SELF_ONLY"
            queue.set_stage(v, "publish", "done", "Published on TikTok" + (" (private: only you can see it)" if private else ""))
            brands.log(db, b.id, f"TikTok confirmed “{v.topic}” is posted{' (private)' if private else ''}.", stage="publish", video_id=v.id)
    elif status == "FAILED":
        v.status, v.error = "failed", f"TikTok: {data.get('fail_reason') or 'failed'}"
        queue.set_stage(v, "publish", "failed", v.error)
        brands.log(db, b.id, f"TikTok rejected “{v.topic}”: {data.get('fail_reason') or 'no reason given'}.", level="error", stage="publish", video_id=v.id)
    db.commit()


# --- the tick --------------------------------------------------------------------------------------------------------------


async def tick(db: Session, settings: Any, transport: Any = None, now: datetime | None = None) -> dict[str, Any]:
    from app.brand_manager import analytics

    now = now or utcnow()
    out: dict[str, Any] = {"expired_leases": queue.expire_leases(db, now)}
    db.commit()
    brands.ensure_default(db)
    for b in db.scalars(select(BmBrand)).all():
        a = brands.automation(db, b)
        a.last_tick_at = now
        r: dict[str, Any] = {}
        # a send cut off midway (job timeout, restart) may or may not have reached TikTok: the owner checks, nothing re-sends it
        for v in db.scalars(select(BmVideo).where(BmVideo.brand_id == b.id, BmVideo.status == "publishing", BmVideo.publish_id.is_(None),
                                                  BmVideo.updated_at < now - timedelta(minutes=15))).all():
            v.status, v.error = "failed", "Sending to TikTok was interrupted, so it may or may not have arrived. Check TikTok before sending it again."
            queue.set_stage(v, "publish", "failed", v.error)
            brands.log(db, b.id, f"“{v.topic}”: {v.error}", level="error", stage="publish", video_id=v.id)
        db.commit()
        # TikTok statuses are polled even when stopped: an upload already sent must reach its real end state
        if tiktok.account(db, b.id).status == "connected":
            deadline = time.monotonic() + POLL_SECONDS
            for v in db.scalars(select(BmVideo).where(BmVideo.brand_id == b.id, BmVideo.status.in_(("publishing", "sent_to_drafts")),
                                                      BmVideo.publish_id.is_not(None), BmVideo.updated_at >= now - timedelta(days=3))).all()[:10]:
                if time.monotonic() > deadline:
                    break
                try:
                    await poll(db, settings, b, v, transport)
                except tiktok.TikTokError as exc:
                    if tiktok.account(db, b.id).status != "connected":
                        break  # sign-in expired: the account shows it once, not one warning per video per minute
                    if v.error != str(exc):
                        v.error = str(exc)
                        brands.log(db, b.id, f"Couldn't check “{v.topic}” on TikTok: {exc}", level="warn", stage="publish", video_id=v.id)
                    db.commit()
        if not a.running:
            db.commit()
            out[b.name] = {"running": False}
            continue
        c = checks(db, settings, b, now)
        if c["blocking"]:
            if a.status != "blocked" or a.reason != c["blocking"][0]:
                brands.log(db, b.id, f"AUTO is blocked: {c['blocking'][0]}", level="error")
            a.status, a.reason = "blocked", c["blocking"][0]
            db.commit()
            out[b.name] = {"blocked": a.reason}
            continue
        if a.status != "running":
            brands.log(db, b.id, "AUTO is running again.")
        a.status, a.reason = "running", ""
        try:
            r["published"] = 0
            due = db.scalars(select(BmVideo).where(BmVideo.brand_id == b.id, BmVideo.status.in_(("ready", "approved")),
                                                   BmVideo.slot_at <= now).order_by(BmVideo.slot_at)).all()
            for v in due[:2]:
                if _published_today(db, b.id, now) >= DAILY_POST_CAP:
                    if not db.scalar(select(BmEvent.id).where(BmEvent.brand_id == b.id, BmEvent.message == CAP_MESSAGE,
                                                              BmEvent.at >= now - timedelta(hours=24)).limit(1)):
                        brands.log(db, b.id, CAP_MESSAGE, level="warn", stage="publish")
                    break
                try:
                    await publish(db, settings, b, v, transport)
                    r["published"] += 1
                except tiktok.TikTokError as exc:
                    if exc.code in ("needs_approval", "already_sending"):
                        continue
                    v.error = str(exc)
                    queue.set_stage(v, "publish", "failed", str(exc))
                    if exc.code in ("unreachable", "rate_limit_exceeded", "spam_risk_too_many_posts"):
                        v.slot_at = now + timedelta(minutes=30)
                        brands.log(db, b.id, f"TikTok couldn't take “{v.topic}” now ({exc}); trying again in 30 minutes.", level="warn", stage="publish", video_id=v.id)
                    else:
                        v.status = "failed"
                        brands.log(db, b.id, f"Publishing “{v.topic}” failed: {exc}", level="error", stage="publish", video_id=v.id)
                    db.commit()
            # publish before planning: discovery is the slow part and must never hold back a video that's due
            r["planned"] = await plan(db, settings, b, a, now, transport)
            if a.last_analytics_at is None or now - a.last_analytics_at > timedelta(hours=6):
                r["analytics"] = await analytics.collect(db, settings, b, transport, now)
                a.last_analytics_at = now
                a.insights = analytics.analyze(db, b, now)
            db.commit()
        except Exception as exc:  # noqa: BLE001 - recorded and shown, and the next tick tries again
            logger.exception("brand manager tick")
            db.rollback()
            a = brands.automation(db, b)
            a.status, a.reason = "error", f"{type(exc).__name__}: {exc}"[:500]
            brands.log(db, b.id, f"Something went wrong ({a.reason}); retrying next minute.", level="error")
            db.commit()
        out[b.name] = r
    return out
