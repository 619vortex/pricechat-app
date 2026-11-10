"""News Desk picture backgrounds from the owner's GPU PC (owner, 2026-11-10: "build the premium image engine around my local GPU").

The PC runs the Price Chat AI worker (worker/pricechat_worker), which already polls this server over outbound HTTPS with its Brand
Manager token; nothing on the PC is reachable from the internet. For a candidate story the desk queues one job: a scene prompt chosen
from an art-direction library by the story's desk and subject. Prompts never name a real person, never ask for text or numbers, and
never depict a real event as if photographed: the model paints mood, the backend draws every headline, figure, chart and credit itself
(app/newsdesk/editorial_layouts). A result is checked here again (decodes, 16:9, sharp, exposed, not a repeat of a recent background)
before any post may use it. No result by the job's deadline, or the PC offline: the post uses the desk's own drawn visuals.

Leases, retries and idempotency follow app/brand_manager/queue.py: a claim is a compare-and-swap with a lease, an expired lease goes
back to the queue (or fails after max_attempts), and one event + art direction has exactly one job.
"""

import hashlib
import io
import random
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from PIL import Image, ImageFilter, ImageStat
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.brand_manager import BmWorker
from app.models.newsdesk import NdEvent, NdRenderJob

LEASE = timedelta(minutes=6)  # Qwen-Image loads (~40 s) and renders (~60 s) inside one lease
KEEP = timedelta(days=7)  # finished images kept this long (Admin previews, repeat checks), then dropped from the row
ONLINE = timedelta(minutes=3)
SIZE = (1536, 864)  # 16:9, multiple of 16
MIN_WIDTH = 1280
REPEAT_DISTANCE = 10  # dHash bits (of 64): closer than this to a recent background is a repeat
NEGATIVE = ("text, letters, words, numbers, digits, watermark, logo, caption, signature, chart, graph, ticker, user interface, "
            "screenshot, flag text, blurry, lowres, deformed, extra limbs, cartoon, anime, illustration, cgi look")
STYLE = ("editorial documentary photography, natural colour, filmic contrast, prime lens, shallow depth of field, premium magazine still, "
         "no people's faces in focus, no signage, no lettering, plain unmarked surfaces")

# art direction: scenes by subject. A scene is a place or an object, never a named person or a claimed event.
SCENES: dict[str, list[str]] = {
    "oil": ["a crude oil tanker crossing a hazy strait at dusk, telephoto", "an offshore oil platform at night, flare glowing, calm dark sea",
            "a refinery skyline at blue hour, steam, sodium lights"],
    "gas": ["an LNG carrier moored at a terminal at dawn, cold mist", "gas pipeline valves in a winter field, frost, low sun"],
    "gold": ["stacked gold bullion bars in a bank vault, hard rim light, black background", "a jeweller's scale with gold grains, macro, warm light"],
    "silver": ["stacked silver bars in a vault, cool rim light, black background", "molten silver poured in a foundry, sparks, dark"],
    "copper": ["copper cathode sheets in a warehouse, warm industrial light", "an open-pit copper mine at sunrise, terraced walls"],
    # no coins: real coins carry inscriptions, and the model paints fake ones (the text check rejected them in the 2026-11-10 benchmark)
    "bitcoin": ["a dark server hall of bitcoin mining rigs, rows of machines with blue LEDs, long perspective, haze",
                "a container of mining machines in a snowy field at night, warm light from its open doors"],
    "crypto": ["a dim data centre aisle, cold blue light, fibre cables", "a glowing fibre-optic cable bundle in darkness, macro, shallow depth of field"],
    "hack": ["a dark server room with a single red warning light, haze", "a broken padlock on a steel server rack, macro, cold light"],
    "stocks": ["a trading floor at night, rows of glowing screens out of focus, one silhouette", "a stock exchange facade at dawn, columns, long shadows"],
    "tech": ["a semiconductor wafer under clean-room light, macro, iridescent", "a glass-walled tech campus at night, reflections, rain"],
    "macro": ["a neoclassical central bank facade at dawn, stone columns, cold light", "stacks of banknotes under a desk lamp, dark office, macro"],
    "rates": ["a central bank boardroom, empty leather chairs, morning light", "a bond trader's desk at dawn, city skyline beyond the glass"],
    "war": ["a grey naval destroyer on a stormy sea, spray, desaturated", "a border checkpoint at night under floodlights, empty road"],
    "aircraft": ["military transport aircraft on an airbase runway at dusk, heat haze", "fighter jets parked in a row on a misty airbase apron at dawn"],
    "navy": ["a grey naval destroyer on a stormy sea, spray, desaturated", "a naval fleet on the horizon at dusk, silhouettes"],
    "diplomacy": ["an empty negotiation table with national-colour chairs, soft window light", "a conference hall corridor with flags out of focus"],
    "shipping": ["container ships queued off a port at sunset, haze", "a container terminal at night, cranes lit, long exposure"],
    "politics": ["a capitol dome at blue hour, empty plaza", "a government press room with an empty podium, soft light"],
    "world": ["a city skyline at night from above, lights, haze", "a globe-lit newsroom out of focus, screens glowing, night"],
}
KEYWORDS: list[tuple[str, str]] = [
    ("oil", r"\b(oil|crude|brent|wti|opec|refiner\w*|barrel)"), ("gas", r"\b(lng|natural gas|gas prices|pipeline)"),
    ("gold", r"\bgold\b"), ("silver", r"\bsilver\b"), ("copper", r"\bcopper\b"),
    ("hack", r"\b(hack\w*|exploit\w*|drain\w*|stolen|breach\w*)"), ("bitcoin", r"\b(bitcoin|btc|miner\w*)"),
    ("crypto", r"\b(crypto\w*|ethereum|ether|xrp|solana|stablecoin\w*|token\w*|defi|exchange)"),
    ("rates", r"\b(rate cut|rate hike|interest rates?|yields?|bonds?|treasur\w*)"), ("macro", r"\b(fed|ecb|central bank|inflation|cpi|gdp|jobs|payrolls)"),
    ("aircraft", r"\b(aircraft|jets?|planes?|air ?base|airbase|air force|bombers?)"), ("navy", r"\b(navy|naval|warships?|destroyers?|carrier|fleet)"),
    ("war", r"\b(war|missile\w*|strike\w*|troops|military|navy|drone\w*|attack\w*|invasion)"), ("shipping", r"\b(shipping|tanker\w*|strait|port|freight)"),
    ("diplomacy", r"\b(talks|ceasefire|peace|summit|sanction\w*|treaty)"), ("tech", r"\b(nvidia|chip\w*|semiconductor\w*|ai\b|openai|anthropic|apple|microsoft)"),
    ("stocks", r"\b(stocks?|shares?|nasdaq|s&p|dow|ipo|earnings)"), ("politics", r"\b(congress|senate|election\w*|impeach\w*|white house|parliament)"),
]
LIGHT = ["golden hour light", "blue hour light", "overcast soft light", "hard side light", "night, practical lights", "dawn mist"]
GRADE = ["muted teal and amber grade", "cool steel grade", "warm low-contrast grade", "deep shadows, crisp highlights", "desaturated natural grade"]


# which model renders what (benchmark on the RTX 5090, 2026-11-10): Qwen-Image-2512 is the best picture at ~57 s, so it paints the
# scheduled slots, rendered ahead of time; FLUX.2 klein 4B takes ~2 s, so breaking news never waits on Qwen.
MODEL_SLOT, MODEL_BREAKING = "qwen", "klein"
PRERENDER_BEFORE = timedelta(minutes=25)  # a slot's likely stories get their backgrounds this long before it opens
PRERENDER_TOP = 3
MAX_JOBS_PER_EVENT = 3  # first try plus two fresh art directions  # the best few waiting candidates
WAIT_SLOT = timedelta(minutes=6)  # the longest a released post waits for its background, then it goes with the desk's own visual
WAIT_BREAKING = timedelta(seconds=150)
FORMATS_WITH_BACKGROUND = ("cinematic",)


def enabled(state: dict[str, Any] | None) -> bool:
    """Admin switch: News Desk state.gpu_render (on unless turned off)."""
    return (state or {}).get("gpu_render") is not False


def online(db: Session, now: datetime) -> bool:
    return any(w.last_seen_at and now - _aware(w.last_seen_at) < ONLINE and (w.info or {}).get("newsdesk")
               for w in db.scalars(select(BmWorker).where(BmWorker.enabled.is_(True))))


def _aware(t: datetime) -> datetime:
    from datetime import timezone

    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def wants_background(ev: Any) -> bool:
    """Charts and market checks are their numbers; every other story may carry a painted background."""
    return getattr(ev, "kind", "") not in ("recap", "market") and len((getattr(ev, "facts", None) or {}).get("points") or []) < 10


def _breaking(ev: Any) -> bool:
    from app.newsdesk import brief

    return getattr(ev, "slot", "") in ("extra", "breaking") or getattr(ev, "label", "") in (brief.BREAKING, brief.ALERT)


def prerender(db: Session, now: datetime, upcoming: datetime | None, candidates: list[NdEvent]) -> int:
    """Before a slot opens, the likely stories get Qwen backgrounds, so the post doesn't wait at release."""
    if upcoming is None or not timedelta(0) <= _aware(upcoming) - now <= PRERENDER_BEFORE or not online(db, now):
        return 0
    n = 0
    for ev in [e for e in candidates if wants_background(e)][:PRERENDER_TOP]:
        if db.scalar(select(NdRenderJob.id).where(NdRenderJob.event_id == ev.id).limit(1)) is None:
            enqueue(db, ev, now, priority=0, deadline=_aware(upcoming) + WAIT_SLOT, model=MODEL_SLOT)
            n += 1
    return n


def on_release(db: Session, ev: NdEvent, now: datetime) -> NdRenderJob | None:
    """A released story with no background coming: one now (klein for breaking, Qwen for a slot), if the PC is there to paint it."""
    if not wants_background(ev) or not online(db, now):
        return None
    jobs = list(db.scalars(select(NdRenderJob).where(NdRenderJob.event_id == ev.id)))
    if any(j.status == "done" and j.image for j in jobs):
        return None
    breaking = _breaking(ev)
    wait = WAIT_BREAKING if breaking else WAIT_SLOT
    live = [j for j in jobs if j.status in ("queued", "claimed")]
    if live:  # rendering ahead already: it may take up to the post's wait from now
        for j in live:
            j.deadline = max(_aware(j.deadline), now + wait) if j.deadline else now + wait
            if breaking and j.status == "queued" and (j.payload or {}).get("model") != MODEL_BREAKING:
                j.payload, j.priority = {**(j.payload or {}), "model": MODEL_BREAKING}, 10
        return live[0]
    return enqueue(db, ev, now, priority=10 if breaking else 5, deadline=now + wait, model=MODEL_BREAKING if breaking else MODEL_SLOT, attempt=len(jobs))


def waiting(db: Session, ev: Any, now: datetime) -> bool:
    """True while this released post should hold for its background: a job is queued or rendering, inside its deadline, the PC online."""
    if not wants_background(ev) or (getattr(ev, "facts", None) or {}).get("visual"):
        return False
    live = list(db.scalars(select(NdRenderJob).where(NdRenderJob.event_id == ev.id, NdRenderJob.status.in_(("queued", "claimed")))))
    if not live:
        return False
    if not online(db, now):
        return False
    return any(j.deadline is None or _aware(j.deadline) > now for j in live)


@dataclass
class Direction:
    scene_key: str
    scene: str
    light: str
    grade: str
    seed: int

    @property
    def prompt(self) -> str:
        # a scene that names its own time or light keeps it: "at night" never meets "golden hour light"
        timed = re.search(r"\b(dusk|dawn|night|blue hour|sunset|sunrise|morning|golden|lamp|lights?|lit|neon|flare)\b", self.scene)
        return ", ".join(x for x in (self.scene, "" if timed else self.light, self.grade, STYLE) if x)

    @property
    def key(self) -> str:
        return hashlib.sha1(f"{self.scene}|{self.light}|{self.grade}|{self.seed}".encode()).hexdigest()[:12]


def subject_of(ev: Any) -> str:
    text = f"{getattr(ev, 'title', '') or ''} {getattr(ev, 'headline', '') or ''}".lower()
    for key, rx in KEYWORDS:
        if re.search(rx, text):
            return key
    return {"crypto": "crypto", "stocks": "stocks", "commodities": "oil", "politics": "politics", "economics": "macro"}.get(getattr(ev, "category", "") or "", "world")


def direction(ev: Any, recent_scenes: list[str] | None = None, attempt: int = 0) -> Direction:
    """The art direction for an event: a scene for its subject, a light and a grade, varied against the last backgrounds."""
    key = subject_of(ev)
    rnd = random.Random(f"{getattr(ev, 'id', 0)}:{attempt}")
    used = recent_scenes or []
    options = sorted(SCENES[key], key=lambda s: (used.count(s), rnd.random()))
    return Direction(key, options[0], rnd.choice(LIGHT), rnd.choice(GRADE), rnd.randrange(1, 2**31))


def _recent_done(db: Session, n: int = 30) -> list[NdRenderJob]:
    return list(db.scalars(select(NdRenderJob).where(NdRenderJob.status == "done").order_by(NdRenderJob.id.desc()).limit(n)))


def enqueue(db: Session, ev: NdEvent, now: datetime, *, priority: int = 0, deadline: datetime | None = None, attempt: int = 0,
            model: str = MODEL_SLOT) -> NdRenderJob:
    recent = [str((j.payload or {}).get("scene") or "") for j in _recent_done(db, 12)]
    d = direction(ev, recent, attempt)
    key = f"nd:{ev.id}:{attempt}"
    job = db.scalar(select(NdRenderJob).where(NdRenderJob.idempotency_key == key))
    if job is not None:
        return job
    job = NdRenderJob(event_id=ev.id, idempotency_key=key, status="queued", priority=priority, attempts=0, max_attempts=3, run_after=now,
                      deadline=deadline, result={},
                      payload={"prompt": d.prompt, "negative": NEGATIVE, "width": SIZE[0], "height": SIZE[1], "seed": d.seed, "model": model,
                               "scene_key": d.scene_key, "scene": d.scene, "light": d.light, "grade": d.grade})
    db.add(job)
    db.flush()
    return job


def expire(db: Session, now: datetime) -> int:
    n = 0
    for job in db.scalars(select(NdRenderJob).where(NdRenderJob.status == "claimed", NdRenderJob.lease_until < now)):  # noqa: SIM118
        if job.attempts >= job.max_attempts:
            job.status, job.error, job.finished_at = "failed", "the GPU worker stopped responding too many times", now
        else:
            job.status, job.worker_id, job.run_after = "queued", None, now
        n += 1
    for job in db.scalars(select(NdRenderJob).where(NdRenderJob.status == "queued", NdRenderJob.deadline.is_not(None), NdRenderJob.deadline < now)):
        job.status, job.error, job.finished_at = "cancelled", "past its deadline: the post went with the desk's own visual", now
        n += 1
    for job in db.scalars(select(NdRenderJob).where(NdRenderJob.image.is_not(None), NdRenderJob.finished_at < now - KEEP)):
        job.image = None
    return n


def claim(db: Session, worker: BmWorker, info: dict[str, Any] | None, now: datetime | None = None) -> dict[str, Any] | None:
    now = now or utcnow()
    worker.last_seen_at = now
    if info:
        worker.info = {**(worker.info or {}), "newsdesk": info}
    expire(db, now)
    db.flush()
    for job in db.scalars(select(NdRenderJob).where(NdRenderJob.status == "queued", NdRenderJob.run_after <= now)
                          .order_by(NdRenderJob.priority.desc(), NdRenderJob.id).limit(5)):
        got = db.execute(update(NdRenderJob).where(NdRenderJob.id == job.id, NdRenderJob.status == "queued")
                         .values(status="claimed", worker_id=worker.id, lease_until=now + LEASE, claimed_at=now, attempts=NdRenderJob.attempts + 1))
        if got.rowcount == 1:
            db.commit()
            db.refresh(job)
            return {"id": job.id, "event_id": job.event_id, "attempt": job.attempts, **(job.payload or {})}
    db.commit()
    return None


class RenderError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def _own(db: Session, job_id: int, worker: BmWorker) -> NdRenderJob:
    job = db.get(NdRenderJob, job_id)
    if job is None:
        raise RenderError(404, "No such job.")
    if job.status != "claimed" or job.worker_id != worker.id:
        raise RenderError(409, "This job isn't claimed by this worker (its lease may have expired).")
    return job


def dhash(img: Image.Image) -> int:
    g = img.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
    px = list(g.getdata())
    bits = 0
    for row in range(8):
        for col in range(8):
            bits = (bits << 1) | (px[row * 9 + col] > px[row * 9 + col + 1])
    return bits


def quality(img: Image.Image, recent_hashes: list[int]) -> tuple[list[str], dict[str, Any]]:
    """Problems that make a background unusable, and the measurements."""
    w, h = img.size
    grey = img.convert("L")
    st = ImageStat.Stat(grey)
    mean, spread = st.mean[0], st.stddev[0]
    edges = ImageStat.Stat(grey.resize((w // 2, h // 2)).filter(ImageFilter.FIND_EDGES)).mean[0]
    hsh = dhash(img)
    nearest = min((bin(hsh ^ r).count("1") for r in recent_hashes), default=64)
    problems = []
    if w < MIN_WIDTH:
        problems.append(f"too small ({w}x{h})")
    if abs(w / h - 16 / 9) > 0.02:
        problems.append(f"not 16:9 ({w}x{h})")
    if not 18 <= mean <= 225:
        problems.append(f"badly exposed (mean {mean:.0f})")
    if spread < 22:
        problems.append(f"flat, no contrast (spread {spread:.0f})")
    if edges < 4.0:
        problems.append(f"soft or empty (detail {edges:.1f})")
    if nearest < REPEAT_DISTANCE:
        problems.append(f"too close to a recent background ({nearest} bits)")
    return problems, {"mean": round(mean, 1), "spread": round(spread, 1), "detail": round(edges, 2), "nearest": nearest, "dhash": f"{hsh:016x}"}


def complete(db: Session, job_id: int, worker: BmWorker, data: bytes, result: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    now = now or utcnow()
    job = _own(db, job_id, worker)
    worker.last_seen_at = now
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
        img = img.convert("RGB")
    except Exception as e:  # noqa: BLE001 - anything that isn't a picture
        raise RenderError(400, f"Not a readable image: {type(e).__name__}") from e
    hashes = [int(str((j.result or {}).get("quality", {}).get("dhash") or "0"), 16) for j in _recent_done(db) if j.id != job.id]
    problems, measured = quality(img, [x for x in hashes if x])
    info = {k: v for k, v in (result or {}).items() if k in ("model", "seconds", "steps", "gpu", "vram_gb", "seed", "ocr", "tries")}
    if problems:
        tries = len(list(db.scalars(select(NdRenderJob.id).where(NdRenderJob.event_id == job.event_id))))
        retry = tries < MAX_JOBS_PER_EVENT and (job.deadline is None or _aware(job.deadline) > now)
        job.result = {**info, "quality": measured, "rejected": problems}
        job.status, job.error, job.finished_at = "failed", "rejected: " + "; ".join(problems), now
        if retry:  # a fresh art direction and seed, once more
            nxt = enqueue(db, db.get(NdEvent, job.event_id) or NdEvent(id=job.event_id, title="", headline=""), now, priority=job.priority,
                          deadline=job.deadline, attempt=tries, model=(job.payload or {}).get("model") or MODEL_SLOT)
            db.commit()
            return {"ok": False, "rejected": problems, "retry_job": nxt.id}
        db.commit()
        return {"ok": False, "rejected": problems}
    out = io.BytesIO()
    img.resize(SIZE, Image.Resampling.LANCZOS).save(out, "JPEG", quality=92, optimize=True)
    job.image, job.result = out.getvalue(), {**info, "quality": measured}
    job.status, job.finished_at, job.error = "done", now, None
    db.commit()
    return {"ok": True}


def fail(db: Session, job_id: int, worker: BmWorker, error: str, now: datetime | None = None) -> dict[str, Any]:
    now = now or utcnow()
    job = _own(db, job_id, worker)
    worker.last_seen_at = now
    job.error = (error or "unknown")[:500]
    if job.attempts >= job.max_attempts:
        job.status, job.finished_at = "failed", now
    else:
        job.status, job.worker_id, job.run_after = "queued", None, now + timedelta(seconds=20 * 2 ** job.attempts)
    db.commit()
    return {"status": job.status}


def background_for(db: Session, event_id: int) -> tuple[Image.Image, dict[str, Any]] | None:
    """The newest accepted background for an event, or None (the post then uses the desk's own visuals)."""
    job = db.scalar(select(NdRenderJob).where(NdRenderJob.event_id == event_id, NdRenderJob.status == "done", NdRenderJob.image.is_not(None))
                    .order_by(NdRenderJob.id.desc()))
    if job is None or not job.image:
        return None
    return Image.open(io.BytesIO(job.image)).convert("RGB"), {"job": job.id, **(job.result or {}), "scene": (job.payload or {}).get("scene")}


def health(db: Session, now: datetime | None = None) -> dict[str, Any]:
    """Admin: is the GPU PC online, what's queued, how renders went in the last 24 hours."""
    now = now or utcnow()
    workers = [w for w in db.scalars(select(BmWorker).where(BmWorker.enabled.is_(True))) if (w.info or {}).get("newsdesk")]
    day = list(db.scalars(select(NdRenderJob).where(NdRenderJob.created_at >= now - timedelta(hours=24))))
    done = [j for j in day if j.status == "done"]
    secs = sorted(float((j.result or {}).get("seconds") or 0) for j in done)
    counts: dict[str, int] = {}
    for j in day:
        counts[j.status] = counts.get(j.status, 0) + 1
    return {
        "workers": [{"id": w.id, "name": w.name, "online": bool(w.last_seen_at and now - w.last_seen_at < ONLINE),
                     "last_seen_at": w.last_seen_at.isoformat() if w.last_seen_at else None, "info": (w.info or {}).get("newsdesk") or {}} for w in workers],
        "online": any(w.last_seen_at and now - w.last_seen_at < ONLINE for w in workers),
        "queued": sum(1 for j in db.scalars(select(NdRenderJob).where(NdRenderJob.status.in_(("queued", "claimed"))))),
        "last_24h": counts,
        "success_rate": round(len(done) / max(1, sum(1 for j in day if j.status in ("done", "failed"))), 3),
        "avg_seconds": round(sum(secs) / len(secs), 1) if secs else None,
        "p95_seconds": secs[min(len(secs) - 1, int(len(secs) * 0.95))] if secs else None,
        "cost_usd": 0.0,  # local GPU: electricity only
        "models": {"slot": MODEL_SLOT, "breaking": MODEL_BREAKING},
        "recent": [{"id": j.id, "event": j.event_id, "status": j.status, "scene": (j.payload or {}).get("scene"), "model": (j.result or {}).get("model"),
                    "seconds": (j.result or {}).get("seconds"), "quality": (j.result or {}).get("quality"), "error": j.error,
                    "at": (j.finished_at or j.created_at).isoformat() if (j.finished_at or j.created_at) else None}
                   for j in sorted(day, key=lambda j: -j.id)[:20]],
    }
