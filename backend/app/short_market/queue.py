"""Short Market's render queue. A short is its own job: an AI worker (registered in Brand Manager › Settings › AI, same
token) claims it with a lease, reports progress (which extends the lease) and uploads the finished MP4. An expired
lease puts the short back in the queue, or fails it after MAX_ATTEMPTS. Nothing is published: the owner downloads it.
"""

import hashlib
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.brand_manager import brands, safety
from app.brand_manager.queue import QueueError
from app.db.base import utcnow
from app.models.brand_manager import BmWorker
from app.models.short_market import SmShort
from app.short_market.defaults import BANNED, CATEGORIES, MAX_ATTEMPTS, STYLES, WORKER_STAGES

MIN_SCENES, MAX_SCENES = 2, 12
MAX_SCENE_WORDS = 30  # one scene is one breath: longer and the picture overstays


def set_stage(s: SmShort, stage: str, status: str, note: str = "", now: datetime | None = None) -> None:
    st = dict(s.stages or {})
    st[stage] = {"status": status, "at": (now or utcnow()).isoformat(), "note": note[:500]}
    s.stages = st


def enqueue(s: SmShort, now: datetime | None = None) -> None:
    s.status, s.error, s.attempts, s.run_after, s.lease_until, s.worker_id = "queued", None, 0, None, None, None
    for stage in WORKER_STAGES + ("quality",):
        set_stage(s, stage, "waiting", "", now)


def expire_leases(db: Session, now: datetime) -> int:
    n = 0
    for s in db.scalars(select(SmShort).where(SmShort.status == "rendering", SmShort.lease_until < now)):
        if s.attempts >= MAX_ATTEMPTS:
            s.status, s.error = "failed", "The worker stopped responding too many times while making this short."
        else:
            s.status, s.worker_id, s.run_after = "queued", None, now
        n += 1
    return n


def _payload(db: Session, s: SmShort, lease: int) -> dict[str, Any]:
    b = brands.ensure_default(db)
    cat = CATEGORIES.get(s.category, {})
    return {"kind": "short", "job_id": s.id, "generation": s.generation, "category": s.category, "topic": s.topic, "brief": s.brief,
            "angle": cat.get("angle", ""), "style": {"key": s.style, **STYLES.get(s.style, STYLES["cartoon3d"])}, "voice": s.voice,
            "seconds": s.seconds, "quality": s.quality, "brand": {"name": b.name, "language": b.language, "personality": b.personality, "audience": b.audience},
            "lease_seconds": lease}


def claim(db: Session, settings: Any, w: BmWorker, info: dict[str, Any] | None, now: datetime | None = None) -> dict[str, Any] | None:
    now = now or utcnow()
    w.last_seen_at = now
    if info:
        w.info = {k: info[k] for k in list(info)[:20]}
    expire_leases(db, now)
    db.commit()
    for s in db.scalars(select(SmShort).where(SmShort.status == "queued", (SmShort.run_after.is_(None)) | (SmShort.run_after <= now))
                        .order_by(SmShort.id).limit(10)):
        lease = now + timedelta(seconds=settings.bm_lease_seconds)
        won = db.execute(update(SmShort).where(SmShort.id == s.id, SmShort.status == "queued")
                         .values(status="rendering", worker_id=w.id, lease_until=lease, attempts=SmShort.attempts + 1)).rowcount
        if not won:
            db.rollback()
            continue  # another worker got it first
        db.commit()
        db.refresh(s)
        return _payload(db, s, settings.bm_lease_seconds)
    return None


def _own(db: Session, w: BmWorker, short_id: int) -> SmShort:
    s = db.get(SmShort, short_id)
    if s is None or s.worker_id != w.id or s.status != "rendering":
        raise QueueError("This short isn't yours (it may have timed out, been remade or deleted).")
    return s


def progress(db: Session, settings: Any, w: BmWorker, short_id: int, stage: str, status: str, note: str = "") -> dict[str, Any]:
    now = utcnow()
    s = _own(db, w, short_id)
    w.last_seen_at = now
    s.lease_until = now + timedelta(seconds=settings.bm_lease_seconds)
    if stage in WORKER_STAGES:
        set_stage(s, stage, status, note, now)
    db.commit()
    return {"ok": True, "cancel": False}


def check(script: dict[str, Any], brief: dict[str, Any], seconds: int, avoid: list[str] | None = None) -> list[str]:
    """Brand Manager's script checks (no invented numbers, no financial claims, sources, length) plus the story's shape."""
    lo, hi = (max(8, seconds - 8), seconds + 8) if script.get("engine") != "template" else (8, 70)  # the facts-only fallback is as long as the facts
    strategy = {"min_seconds": lo, "max_seconds": hi, "banned_topics": list(BANNED), "require_sources": True, "hashtags": {"max": 6}}
    problems = safety.check(script, brief, strategy, avoid)
    segs = [g for g in script.get("segments") or [] if isinstance(g, dict)]
    if not MIN_SCENES <= len(segs) <= MAX_SCENES:
        problems.append(f"The story needs {MIN_SCENES}-{MAX_SCENES} scenes; this one has {len(segs)}.")
    for n, g in enumerate(segs, start=1):
        if len(str(g.get("text") or "").split()) > MAX_SCENE_WORDS:
            problems.append(f"Scene {n} is too long to say over one picture: keep each scene under {MAX_SCENE_WORDS} words.")
        if len(str(g.get("visual") or "").split()) < 4:
            problems.append(f"Scene {n} has no picture: describe what the 3D scene shows.")
    return problems


def submit_script(db: Session, settings: Any, w: BmWorker, short_id: int, script: dict[str, Any]) -> dict[str, Any]:
    s = _own(db, w, short_id)
    b = brands.ensure_default(db)
    problems = check(script, s.brief, s.seconds, b.avoid)
    s.lease_until = utcnow() + timedelta(seconds=settings.bm_lease_seconds)
    if problems:
        set_stage(s, "script", "retrying", problems[0])
        db.commit()
        return {"ok": False, "problems": problems}
    clean = {k: script.get(k) for k in ("hook", "voiceover", "title", "description", "hashtags", "storyboard", "engine", "character")}
    clean["segments"] = [{"text": str(g.get("text") or "").strip(), "screen": str(g.get("screen") or "").strip()[:60],
                          "visual": str(g.get("visual") or "").strip()[:600]} for g in script.get("segments") or [] if isinstance(g, dict)]
    clean["hashtags"] = [t if str(t).startswith("#") else f"#{t}" for t in (str(h).strip().replace(" ", "") for h in clean.get("hashtags") or []) if t][:6]
    desc = str(clean.get("description") or "").strip()
    if CATEGORIES.get(s.category, {}).get("disclaimer") and "not financial advice" not in desc.lower():
        desc = f"{desc} Not financial advice.".strip()
    clean["description"] = desc[:2000]
    clean["sources"] = s.brief.get("sources") or []
    s.script = clean
    set_stage(s, "script", "done", f"{len(clean['segments'])} scenes · {len(str(clean.get('voiceover') or '').split())} words · {clean.get('engine') or 'llm'}")
    db.commit()
    return {"ok": True, "script": clean}


def fail(db: Session, w: BmWorker, short_id: int, error: str, retryable: bool = True) -> dict[str, Any]:
    now = utcnow()
    s = _own(db, w, short_id)
    if retryable and s.attempts < MAX_ATTEMPTS:
        s.status, s.worker_id, s.error = "queued", None, error[:2000]
        s.run_after = now + timedelta(minutes=2 ** s.attempts)
    else:
        s.status, s.error, s.lease_until = "failed", error[:2000], None
        for stage in WORKER_STAGES:
            if (s.stages or {}).get(stage, {}).get("status") in ("running", "retrying"):
                set_stage(s, stage, "failed", error[:300], now)
    db.commit()
    return {"ok": True, "status": s.status}


def complete(db: Session, settings: Any, w: BmWorker, short_id: int, video: Any, thumb: Any | None, meta: dict[str, Any]) -> dict[str, Any]:
    """The worker's finished video: checked (format, size, length, voice, captions, checksum), then streamed to storage.
    `video` / `thumb` are file objects (FastAPI spools uploads to disk, so memory stays flat)."""
    from app.brand_manager.store import MediaStore

    now = utcnow()
    s = _own(db, w, short_id)
    if s.script is None:
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
    dur = float(meta.get("duration") or 0)
    if (int(meta.get("width") or 0), int(meta.get("height") or 0)) != (1080, 1920):
        problems.append(f"Wrong size {meta.get('width')}×{meta.get('height')} (needs 1080×1920).")
    if not 8 <= dur <= 120:
        problems.append(f"Length {dur:.1f}s is outside 8-120s.")
    if not meta.get("has_audio"):
        problems.append("The video has no voice track.")
    if not meta.get("subtitles"):
        problems.append("The video has no captions.")
    if problems:
        set_stage(s, "quality", "failed", problems[0], now)
        db.commit()
        return fail(db, w, short_id, "Final check: " + "; ".join(problems), retryable=True)
    store = MediaStore(settings)
    base = f"short-market/{s.id}-g{s.generation}"
    store.put_file(f"{base}.mp4", video, "video/mp4")
    if thumb is not None:
        store.put_file(f"{base}.jpg", thumb, "image/jpeg")
    for old in (s.video_key, s.thumb_key):  # the take this remake replaces
        if old and not old.startswith(base + "."):
            try:
                store.delete(old)
            except Exception:  # noqa: BLE001 - an orphan file is harmless
                pass
    s.video_key, s.thumb_key = f"{base}.mp4", f"{base}.jpg" if thumb is not None else None
    s.duration, s.size_bytes, s.checksum = dur, size, digest.hexdigest()
    s.render_info = {"worker": w.name, **{k: meta.get(k) for k in ("tts", "llm", "seconds", "renderer", "scenes", "ai_scenes", "fps")}}
    for stage in WORKER_STAGES:
        if (s.stages or {}).get(stage, {}).get("status") != "done":
            set_stage(s, stage, "done", "", now)
    set_stage(s, "quality", "done", f"{dur:.1f}s · 1080×1920 · {size / 1e6:.1f} MB", now)
    s.status, s.error, s.lease_until, s.downloaded_at = "ready", None, None, None
    db.commit()
    return {"ok": True, "status": s.status}
