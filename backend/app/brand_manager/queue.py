"""The durable job queue between the backend and the AI workers (DGX Spark, optional cloud fallback).

A worker polls `claim` (outbound HTTPS only: the Spark exposes nothing). A claim is a compare-and-swap on the job row
with a lease; progress reports extend it; an expired lease puts the job back in the queue (or fails it after
`max_attempts`). Retries back off exponentially. Each video generation has exactly one render job (idempotency key),
so a retried request or a restarted server never renders or uploads a video twice.
"""

import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.brand_manager import brands, defaults, factcard, hashtags
from app.db.base import utcnow
from app.models.brand_manager import BmAutomation, BmBrand, BmEvent, BmJob, BmVideo, BmWorker

WORKER_STAGES = ("script", "voice", "visuals", "assemble", "subtitles")


class QueueError(Exception):
    pass


# --- workers ---------------------------------------------------------------------------------------------------------------


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def add_worker(db: Session, name: str, cloud: bool = False) -> tuple[BmWorker, str]:
    token = "pcw_" + secrets.token_urlsafe(32)
    w = BmWorker(name=name.strip()[:60] or "worker", token_hash=_hash(token), enabled=True, cloud=cloud)
    db.add(w)
    db.commit()
    return w, token


def worker_for(db: Session, token: str | None) -> BmWorker | None:
    if not token or not token.startswith("pcw_"):
        return None
    h = _hash(token)
    w = db.scalar(select(BmWorker).where(BmWorker.token_hash == h))
    if w is None:
        from app.config.settings import get_settings

        # A worker set up from the server's side: only the SHA-256 of its token is configured (BM_WORKER_BOOTSTRAP_SHA256),
        # the token itself stays on the worker machine. Registered on its first contact; deleting it in Settings › AI is final.
        boot = (get_settings().bm_worker_bootstrap_sha256 or "").strip().lower()
        if boot and secrets.compare_digest(boot, h) and not db.scalar(select(BmEvent.id).where(BmEvent.message == BOOTSTRAP_LOG)):
            w = BmWorker(name=BOOTSTRAP_NAME, token_hash=h, enabled=True, cloud=False)
            db.add(w)
            brands.log(db, brands.ensure_default(db).id, BOOTSTRAP_LOG)
            db.commit()
    return w if w is not None and w.enabled else None


BOOTSTRAP_NAME = "This PC (until the DGX Spark)"
BOOTSTRAP_LOG = "Worker “This PC (until the DGX Spark)” registered itself with its configured token (once)."


def worker_out(w: BmWorker, now: datetime | None = None) -> dict[str, Any]:
    now = now or utcnow()
    online = w.last_seen_at is not None and now - w.last_seen_at < timedelta(minutes=3)
    return {"id": w.id, "name": w.name, "enabled": w.enabled, "cloud": w.cloud, "online": online,
            "last_seen_at": w.last_seen_at.isoformat() if w.last_seen_at else None, "info": w.info or {}}


# --- stages --------------------------------------------------------------------------------------------------------------


def set_stage(v: BmVideo, stage: str, status: str, note: str = "", now: datetime | None = None) -> None:
    st = dict(v.stages or {})
    st[stage] = {"status": status, "at": (now or utcnow()).isoformat(), "note": note[:500]}
    v.stages = st


# --- jobs ----------------------------------------------------------------------------------------------------------------


def enqueue_render(db: Session, v: BmVideo, manual: bool = False, mode: str = "full") -> BmJob:
    """mode "full": research brief -> new script -> video. "rerender": keep the approved script, make the video again with
    the video's (new) creative settings and scene edits (Content Studio's regenerate scene / voice / captions). "timeline":
    keep the script and render the owner's edited timeline as it is (Content Studio v4's editor)."""
    key = f"render:{v.id}:{v.generation}"
    job = db.scalar(select(BmJob).where(BmJob.idempotency_key == key))
    if job is not None:
        return job
    job = BmJob(video_id=v.id, kind="render", idempotency_key=key, status="queued", attempts=0, max_attempts=3,
                payload={"manual": manual, **({"mode": mode} if mode in ("rerender", "timeline") else {})})
    db.add(job)
    v.status = "queued"
    for s in WORKER_STAGES:
        set_stage(v, s, "waiting")
    return job


def expire_leases(db: Session, now: datetime) -> int:
    n = 0
    for job in db.scalars(select(BmJob).where(BmJob.status == "claimed", BmJob.lease_until < now)):
        v = db.get(BmVideo, job.video_id)
        if job.attempts >= job.max_attempts:
            job.status, job.error, job.finished_at = "failed", "The worker stopped responding (lease expired) too many times.", now
            if v:
                v.status, v.error = "failed", job.error
                brands.log(db, v.brand_id, f"Rendering “{v.topic}” failed: the worker stopped responding.", level="error", video_id=v.id)
        else:
            job.status, job.worker_id, job.run_after = "queued", None, now
            if v:
                v.status = "queued"
                brands.log(db, v.brand_id, f"The worker stopped responding while rendering “{v.topic}”; it's back in the queue.", level="warn", video_id=v.id)
        n += 1
    return n


def _payload(db: Session, job: BmJob, v: BmVideo, b: BmBrand, a: BmAutomation | None) -> dict[str, Any]:
    st = brands.strategy(b)
    tpl = defaults.TEMPLATES[v.template]
    insights = (a.insights or {}) if a else {}
    return {
        "job_id": job.id, "video_id": v.id, "generation": v.generation, "category": v.category, "template": v.template, "topic": v.topic,
        "brief": factcard.attach(v.brief or {}), "brand": {"name": b.name, "website": b.website, "personality": b.personality, "audience": b.audience,
                                    "language": b.language, "cta": b.cta, "colors": b.colors, "logo_url": b.logo_url,
                                    "examples": [e["text"] for e in b.examples if e.get("approved")][:5], "avoid": b.avoid},
        "strategy": {k: st[k] for k in ("min_seconds", "max_seconds", "voice", "llm", "ollama_model", "quality", "watermark", "outro_cta",
                                        "music", "hashtags", "ai_disclosure", "voice_name", "voice_speed")},
        "suggested_hashtags": hashtags.build([], st, v.category, v.brief or {}),
        "template_style": {**{"label": tpl["label"], "scenes": tpl["scenes"]}, **(st["templates"].get(v.template) or {})},
        "learned": {"top_hooks": insights.get("top_hooks", [])[:3], "best_length": insights.get("best_length")},
        # the live trend words this story contains (they are in its facts, so the writer may say them)
        "trends": [t["label"] for t in ((v.brief or {}).get("trend") or {}).get("terms") or []][:3],
        "lease_seconds": None,
        "creative": creative_of(st, v),
        **({"mode": "rerender", "script": v.script} if (job.payload or {}).get("mode") == "rerender" and v.script else {}),
        **({"mode": "timeline", "script": v.script, "timeline": v.timeline}
           if (job.payload or {}).get("mode") == "timeline" and v.script and (v.timeline or {}).get("video") else {}),
        "media_library": list((v.media or {}).get("assets") or []),  # v4: what was found before + what the owner added/excluded
        "history": history(db, b.id, v.id),  # recent hooks and visual formats: the writer's duplicate / format-repeat gates
    }


def history(db: Session, brand_id: int, exclude: int | None = None, n: int = 50) -> list[dict[str, Any]]:
    """The brand's most recent finished videos, newest first: their hook and visual format (no other content)."""
    rows = db.scalars(select(BmVideo).where(BmVideo.brand_id == brand_id, BmVideo.script.is_not(None), BmVideo.id != (exclude or 0))
                      .order_by(BmVideo.created_at.desc()).limit(n))
    return [{"hook": (r.script or {}).get("hook") or "", "visual_format": (r.script or {}).get("visual_format") or "",
             "at": r.created_at.isoformat() if r.created_at else None} for r in rows if (r.script or {}).get("hook")]


def creative_of(st: dict[str, Any], v: BmVideo) -> dict[str, Any]:
    """The brand's creative defaults with this video's own choices (and scene edits) on top."""
    base = dict(st.get("creative") or {})
    own = dict(v.creative or {})
    out = {**base, **{k: val for k, val in own.items() if k not in ("pronunciations", "scenes")}}
    out["pronunciations"] = {**(base.get("pronunciations") or {}), **(own.get("pronunciations") or {})}
    out["scenes"] = own.get("scenes") or {}
    return out


def length_limits(st: dict[str, Any], v: BmVideo | None = None) -> dict[str, Any]:
    """min/max seconds: the brand's, or around the video's own target length when Content Studio set one."""
    secs = (creative_of(st, v) if v is not None else (st.get("creative") or {})).get("seconds")
    if secs:
        return {**st, "min_seconds": max(10, int(secs) - 5), "max_seconds": int(secs) + 5}
    return st


def claim(db: Session, settings: Any, w: BmWorker, info: dict[str, Any] | None, now: datetime | None = None) -> dict[str, Any] | None:
    now = now or utcnow()
    w.last_seen_at = now
    if info:
        w.info = {k: info[k] for k in list(info)[:20]}
    expire_leases(db, now)
    db.commit()
    autos = {a.brand_id: a for a in db.scalars(select(BmAutomation))}
    stopped = [b for b, a in autos.items() if a.emergency_stop]
    # STOP AUTO leaves its planned jobs queued (they wait for START): scan past them, so a manual video made meanwhile
    # (no slot, so sorted last) is still found behind a long stopped queue
    candidates = db.execute(select(BmJob, BmVideo).join(BmVideo, BmVideo.id == BmJob.video_id)
                            .where(BmJob.status == "queued", (BmJob.run_after.is_(None)) | (BmJob.run_after <= now),
                                   BmVideo.brand_id.not_in(stopped))
                            .order_by(BmVideo.slot_at.is_(None), BmVideo.slot_at, BmJob.id).limit(500)).all()
    for job, v in candidates:
        a = autos.get(v.brand_id)
        if not (a is not None and a.running) and not (job.payload or {}).get("manual"):
            continue  # STOP AUTO: nothing new starts (a manual Regenerate still runs)
        lease = now + timedelta(seconds=settings.bm_lease_seconds)
        won = db.execute(update(BmJob).where(BmJob.id == job.id, BmJob.status == "queued")
                         .values(status="claimed", worker_id=w.id, lease_until=lease, attempts=BmJob.attempts + 1)).rowcount
        if not won:
            db.rollback()
            continue  # another worker got it first
        v.status = "rendering"
        brands.log(db, v.brand_id, f"{w.name} started “{v.topic}”.", stage="script", video_id=v.id)
        db.commit()
        db.refresh(job)
        b = db.get(BmBrand, v.brand_id)
        assert b is not None
        p = _payload(db, job, v, b, a)
        p["lease_seconds"] = settings.bm_lease_seconds
        return p
    return None


def _own(db: Session, w: BmWorker, job_id: int) -> tuple[BmJob, BmVideo]:
    job = db.get(BmJob, job_id)
    if job is None or job.worker_id != w.id or job.status != "claimed":
        raise QueueError("This job isn't yours (it may have timed out and gone to another worker).")
    v = db.get(BmVideo, job.video_id)
    assert v is not None
    return job, v


def progress(db: Session, settings: Any, w: BmWorker, job_id: int, stage: str, status: str, note: str = "") -> dict[str, Any]:
    now = utcnow()
    job, v = _own(db, w, job_id)
    w.last_seen_at = now
    job.lease_until = now + timedelta(seconds=settings.bm_lease_seconds)
    if stage in WORKER_STAGES:
        set_stage(v, stage, status, note, now)
        if status == "failed":
            brands.log(db, v.brand_id, f"{dict((s[0], s[1]) for s in defaults.STAGES)[stage]} failed: {note}", level="warn", stage=stage, video_id=v.id)
    a = db.scalar(select(BmAutomation).where(BmAutomation.brand_id == v.brand_id))
    cancel = bool(a and a.emergency_stop) or v.status == "cancelled"
    db.commit()
    return {"ok": True, "cancel": cancel}


def submit_script(db: Session, settings: Any, w: BmWorker, job_id: int, script: dict[str, Any]) -> dict[str, Any]:
    from app.brand_manager import safety

    job, v = _own(db, w, job_id)
    b = db.get(BmBrand, v.brand_id)
    assert b is not None
    st = length_limits(brands.strategy(b), v)
    problems = safety.check(script, v.brief, st, b.avoid)
    job.lease_until = utcnow() + timedelta(seconds=settings.bm_lease_seconds)
    if problems:
        set_stage(v, "script", "retrying", problems[0])
        db.commit()
        return {"ok": False, "problems": problems}
    clean = {k: script.get(k) for k in ("hook", "voiceover", "title", "description", "hashtags", "storyboard", "sources", "engine", "segments")}
    clean["hashtags"] = hashtags.build(clean.get("hashtags"), st, v.category, v.brief or {})
    disclaimer = safety.finance_disclaimer(v.category)
    desc = str(clean.get("description") or "").strip()
    if disclaimer and disclaimer.lower() not in desc.lower():
        desc = f"{desc} {disclaimer}".strip()
    if st.get("ai_disclosure") and "ai" not in desc.lower():
        desc = f"{desc} (AI-assisted video.)".strip()
    clean["description"] = desc[:2000]
    clean["sources"] = v.brief.get("sources") or clean.get("sources") or []
    v.script = clean
    set_stage(v, "script", "done", f"{len(str(clean.get('voiceover') or '').split())} words · {clean.get('engine') or 'llm'}")
    db.commit()
    return {"ok": True, "script": clean}


def store_library(v: BmVideo, assets: list[dict[str, Any]]) -> None:
    """The worker's media research merged into the video's library: the owner's own additions and exclusions win."""
    cur = {a["id"]: a for a in (v.media or {}).get("assets") or [] if isinstance(a, dict) and a.get("id")}
    for a in assets[:200]:
        if not isinstance(a, dict) or not a.get("id"):
            continue
        old = cur.get(a["id"])
        if old and (old.get("origin") == "owner" or old.get("status") == "excluded"):
            continue
        cur[a["id"]] = {**a, "origin": a.get("origin") or "research"}
    v.media = {"assets": list(cur.values()), "updated_at": utcnow().isoformat()}


def library(db: Session, w: BmWorker, job_id: int, assets: list[dict[str, Any]]) -> dict[str, Any]:
    """The worker reports what its media research found (before the edit), so the owner sees it even if the story is sent
    to editorial review."""
    job, v = _own(db, w, job_id)
    store_library(v, assets)
    set_stage(v, "visuals", "running", f"{sum(1 for a in assets if a.get('status') == 'ok')} real assets found")
    db.commit()
    return {"ok": True, "assets": len((v.media or {}).get("assets") or [])}


def fail(db: Session, w: BmWorker, job_id: int, error: str, retryable: bool = True) -> dict[str, Any]:
    now = utcnow()
    job, v = _own(db, w, job_id)
    job.error = error[:2000]
    if retryable and job.attempts < job.max_attempts:
        job.status, job.worker_id = "queued", None
        job.run_after = now + timedelta(minutes=2 ** job.attempts)
        v.status = "queued"
        brands.log(db, v.brand_id, f"Rendering “{v.topic}” hit a problem ({error[:200]}); retrying in {2 ** job.attempts} min.", level="warn", video_id=v.id)
    else:
        job.status, job.finished_at = "failed", now
        v.status, v.error = "failed", error[:2000]
        brands.log(db, v.brand_id, f"Rendering “{v.topic}” failed: {error[:300]}", level="error", video_id=v.id)
    db.commit()
    return {"ok": True, "status": job.status}


MAX_THUMB_BYTES = 5 * 1024 * 1024


def _num(value: Any) -> float:
    """A number from the worker's report; anything else counts as 0 (and fails the check that needs it)."""
    try:
        out = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    return out if out == out and abs(out) != float("inf") else 0.0


def _is_thumb(f: Any) -> bool:
    head = f.read(3)
    f.seek(0, 2)
    size = f.tell()
    f.seek(0)
    return head[:2] == b"\xff\xd8" and 0 < size <= MAX_THUMB_BYTES


def complete(db: Session, settings: Any, w: BmWorker, job_id: int, video: Any, thumb: Any | None, meta: dict[str, Any]) -> dict[str, Any]:
    """The worker's finished video: streamed to object storage, checked (format, size, length, narration, checksum), then
    scheduled. `video` / `thumb` are file objects (FastAPI spools uploads to disk, so memory stays flat)."""
    from app.brand_manager.store import MediaStore

    now = utcnow()
    job, v = _own(db, w, job_id)
    b = db.get(BmBrand, v.brand_id)
    assert b is not None
    st = length_limits(brands.strategy(b), v)
    if v.script is None:
        raise QueueError("Submit the script before the video.")
    problems: list[str] = []
    head = video.read(12)
    video.seek(0, 2)
    size = video.tell()
    video.seek(0)
    if head[4:8] != b"ftyp":
        problems.append("The file isn't an MP4 video.")
    if size > settings.bm_max_video_mb * 1024 * 1024:
        problems.append(f"The video is larger than {settings.bm_max_video_mb} MB.")
    digest = hashlib.sha256()
    while chunk := video.read(1 << 20):
        digest.update(chunk)
    video.seek(0)
    if meta.get("sha256") and meta["sha256"] != digest.hexdigest():
        problems.append("The upload was corrupted (checksum mismatch).")
    dur = _num(meta.get("duration"))
    if (int(_num(meta.get("width"))), int(_num(meta.get("height")))) != (1080, 1920):
        problems.append(f"Wrong size {meta.get('width')}×{meta.get('height')} (needs 1080×1920).")
    if not (float(st["min_seconds"]) - 5 <= dur <= float(st["max_seconds"]) + 10):
        problems.append(f"Length {dur:.1f}s is outside {st['min_seconds']}-{st['max_seconds']}s.")
    if not meta.get("has_audio"):
        problems.append("The video has no narration track.")
    if not meta.get("subtitles"):
        problems.append("The video has no subtitles.")
    qc = {"size_bytes": size, "duration": dur, "width": meta.get("width"), "height": meta.get("height"), "fps": meta.get("fps"),
          "has_audio": bool(meta.get("has_audio")), "subtitles": bool(meta.get("subtitles")), "problems": problems, "checked_at": now.isoformat()}
    v.qc = qc
    if problems:
        set_stage(v, "quality", "failed", problems[0])
        db.commit()
        return fail(db, w, job_id, "Quality check: " + "; ".join(problems), retryable=True)
    store = MediaStore(settings)
    base = f"{v.brand_id}/{v.id}-g{v.generation}"
    if thumb is not None and not _is_thumb(thumb):
        thumb = None  # not a JPEG, or too big: the video is still good, it just has no poster
    store.put_file(f"{base}.mp4", video, "video/mp4")
    if thumb is not None:
        store.put_file(f"{base}.jpg", thumb, "image/jpeg")
    old = [k for k in (v.video_key, v.thumb_key) if k and k not in (f"{base}.mp4", f"{base}.jpg")]
    v.video_key, v.thumb_key = f"{base}.mp4", f"{base}.jpg" if thumb is not None else None
    for key in old:  # the previous render's files (Regenerate / re-render): nothing points at them any more
        try:
            store.delete(key)
        except Exception:  # noqa: BLE001 - an orphan file costs storage, not correctness
            pass
    v.duration, v.size_bytes, v.checksum = dur, size, digest.hexdigest()
    v.render_info = {"worker": w.name, **{k: meta.get(k) for k in ("tts", "llm", "seconds", "renderer", "cost_usd", "creative", "storyboard",
                                                                     "segments", "checks", "warnings", "timings", "narration_match",
                                                                     "authenticity", "gate", "shots", "photo_credits")
                                         if k in meta}}
    if isinstance(meta.get("media_library"), list):
        store_library(v, meta["media_library"])
    if isinstance(meta.get("timeline"), dict) and meta["timeline"].get("video"):
        v.timeline = meta["timeline"]
    for s in WORKER_STAGES:
        if (v.stages or {}).get(s, {}).get("status") != "done":
            set_stage(v, s, "done", "", now)
    set_stage(v, "quality", "done", f"{dur:.1f}s · 1080×1920 · {size / 1e6:.1f} MB", now)
    review = st.get("approval_required") or st.get("publish_mode") == "direct"
    v.status, v.error = ("needs_review" if review else "ready"), None
    when = v.slot_at.isoformat() if v.slot_at else "the next free slot"
    set_stage(v, "schedule", "done" if not review else "waiting", f"{'Waiting for your approval · ' if review else ''}slot {when}", now)
    job.status, job.finished_at, job.lease_until = "done", now, None
    brands.log(db, v.brand_id, f"Video ready: “{v.topic}” ({dur:.0f}s).{' Waiting for your approval in Content Studio.' if review else ''}",
               stage="quality", video_id=v.id)
    db.commit()
    return {"ok": True, "status": v.status}
