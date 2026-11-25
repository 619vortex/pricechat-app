"""The AI router (doc §4.1): who answers an intel job. PC worker (leased) -> on timeout / expired lease / PC offline -> Claude (capped)
-> over the cap or no key -> rules (result_by 'rules', no analysis: the caller keeps today's rule gates). Never silent: every job row
records who answered and why.

Policy
- The PC is offered a job only while its heartbeat is fresh (<= 60 s). Bulk jobs wait for it anyway (backfill), for up to 6 h.
- breaking: cloud-first (intel_breaking_cloud_first). If the PC is online it gets a shadow copy; its answer is only stored for comparison.
  With cloud-first off, the PC has 20 s to claim it.
- slot: the PC has 180 s to claim it; bulk: no claim deadline, but the job expires after 6 h.
- growth.write (Growth Engine Originals writer): queued "bulk" by the daily generation (templates stand meanwhile) and "slot" by an owner's
  Regenerate; Railway's fallback is Claude (same cap) then rules (the template text stays).
- A claim holds a 120 s lease. An expired lease goes back to the queue once (if the claim window is still open and the PC is online),
  otherwise Railway answers it.

OFF by default: with settings.intel_enabled False, enqueue() returns None (nothing written), run() returns None and tick() does nothing.
Nothing here publishes or changes what News Desk / Trending publish.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.intel import anthropic_client, channel
from app.intel.channel import KINDS, PRIORITIES, aware
from app.models.intel import IntelJob

CLAIM_WAIT: dict[str, timedelta | None] = {"breaking": timedelta(seconds=20), "slot": timedelta(seconds=180), "bulk": None}
BULK_TTL = timedelta(hours=6)
SHADOW_TTL = timedelta(minutes=10)
CLOUD_TAKE = timedelta(seconds=90)  # Railway's own hold on a job while it calls the cloud (TIMEOUT 45 s inside)
TICK_MAX_CLOUD = 20  # cloud calls per tick at most (the rest wait for the next tick)


@dataclass(frozen=True, slots=True)
class Outcome:
    job_id: int
    result_by: str | None  # pc | cloud | rules; None = still pending (bulk)
    output: Any  # ItemAnalysis dict / vector, or None (rules: no analysis, use today's rules)
    latency_ms: int | None
    reason: str | None = None


def _settings(settings: Any = None) -> Any:
    return channel._settings(settings)


def enabled(settings: Any = None) -> bool:
    return bool(_settings(settings).intel_enabled)


def _check(kind: str, payload: dict[str, Any], priority: str) -> None:
    if kind not in KINDS:
        raise ValueError(f"unknown intel job kind {kind!r}")
    if priority not in PRIORITIES:
        raise ValueError(f"unknown priority {priority!r}")
    field = "title" if kind == "intel.analyze" else "text"
    if not isinstance(payload.get(field), str) or not payload[field].strip():
        raise ValueError(f"{kind} needs a non-empty {field}")
    if kind == "growth.write" and (not str(payload.get("user") or "").strip() or not isinstance(payload.get("schema"), dict)):
        raise ValueError("growth.write needs its prompt (user) and schema (app.growth.writer.build)")


def enqueue(db: Session, kind: str, payload: dict[str, Any], priority: str = "slot", *, key: str | None = None, now: datetime | None = None,
            settings: Any = None) -> IntelJob | None:
    s = _settings(settings)
    if not s.intel_enabled:
        return None
    _check(kind, payload, priority)
    now = now or utcnow()
    online = channel.pc_online(db, now, s)
    common = {"kind": kind, "priority": priority, "key": (key or None) and key[:120], "payload": payload, "status": "queued", "attempts": 0,
              "max_attempts": 2}
    if priority == "breaking" and s.intel_breaking_cloud_first:
        job = IntelJob(route="cloud", shadow=False, **common)
        if online:
            db.add(IntelJob(route="pc", shadow=True, offer_until=now + SHADOW_TTL, expires_at=now + SHADOW_TTL, **common))
    elif priority == "bulk":
        job = IntelJob(route="pc", shadow=False, offer_until=None, expires_at=now + BULK_TTL, **common)
    elif online:
        wait = CLAIM_WAIT[priority]
        assert wait is not None
        job = IntelJob(route="pc", shadow=False, offer_until=now + wait, expires_at=now + wait + channel.LEASE * 2, **common)
    else:
        job = IntelJob(route="cloud", shadow=False, **common)
    db.add(job)
    db.commit()
    return job


def _take(db: Session, job: IntelJob, now: datetime) -> bool:
    """Railway takes the job to answer it itself (compare-and-swap: only one of tick / a waiting run() wins, a PC lease must be over)."""
    got = db.execute(update(IntelJob).where(IntelJob.id == job.id, IntelJob.status.in_(("queued", "leased")))
                     .where(or_(IntelJob.status == "queued", IntelJob.lease_until <= now))
                     .values(status="leased", route="cloud", nonce=None, worker_id=None, lease_until=now + CLOUD_TAKE)
                     .execution_options(synchronize_session=False))
    db.commit()
    db.refresh(job)
    return got.rowcount == 1  # type: ignore[attr-defined]


def rules_fallback(job: IntelJob) -> None:
    """The deterministic fallback: no analysis. Callers read result_by 'rules' and keep using today's rule gates."""
    job.result = None
    job.result_by = "rules"


async def fallback(db: Session, job: IntelJob, now: datetime | None = None, *, settings: Any = None,
                   transport: httpx.AsyncBaseTransport | None = None) -> Outcome | None:
    """Railway answers the job: Claude (analyze only, key present, under the cap), else rules. Shadow jobs just expire.
    None when another process already took it."""
    s = _settings(settings)
    now = now or utcnow()
    if job.shadow:
        db.execute(update(IntelJob).where(IntelJob.id == job.id, IntelJob.status.in_(("queued", "leased")))
                   .where(or_(IntelJob.status == "queued", IntelJob.lease_until <= now))
                   .values(status="expired", nonce=None, finished_at=now).execution_options(synchronize_session=False))
        db.commit()
        return None
    if not _take(db, job, now):
        return None
    reason = job.error
    if job.kind == "intel.analyze":
        p = job.payload or {}
        try:
            res = await anthropic_client.analyze(db, str(p.get("title") or ""), str(p.get("summary") or ""),
                                                 [str(o) for o in p.get("outlets") or []], settings=s, transport=transport)
            job.result = {"output": res.analysis.model_dump(), "model": res.model, "cost_usd": round(res.cost_usd, 6)}
            job.result_by = "cloud"
        except anthropic_client.CloudUnavailable as e:
            rules_fallback(job)
            reason = f"cloud: {e.reason}" + (f"; {reason}" if reason else "")
    elif job.kind == "growth.write":  # the Originals writer: Claude under the same cap, else rules (the draft keeps its template text)
        p = job.payload or {}
        try:
            out, model, cost = await anthropic_client.write_json(db, str(p.get("system") or ""), str(p.get("user") or ""), p.get("schema") or {},
                                                                 purpose="growth.write", settings=s, transport=transport)
            job.result = {"output": channel.validate_output("growth.write", out, s), "model": model, "cost_usd": round(cost, 6)}
            job.result_by = "cloud"
        except (anthropic_client.CloudUnavailable, channel.ChannelError) as e:
            rules_fallback(job)
            reason = f"cloud: {getattr(e, 'reason', str(e))}" + (f"; {reason}" if reason else "")
    else:
        rules_fallback(job)  # Phase 0 has no Railway embeddings yet (Phase 1: nomic on CPU)
        reason = "no cloud embeddings" + (f"; {reason}" if reason else "")
    done = utcnow()
    job.status, job.nonce, job.lease_until, job.finished_at = "done", None, None, done
    job.latency_ms = channel.latency_ms(job, done)
    job.error = (reason or None) and reason[:500]
    db.commit()
    return outcome(job)


def outcome(job: IntelJob) -> Outcome:
    if job.status != "done":
        return Outcome(job.id, None, None, None, job.error)
    return Outcome(job.id, job.result_by, (job.result or {}).get("output"), job.latency_ms, job.error)


def _needs_railway(job: IntelJob, now: datetime, online: bool) -> bool:
    """Should Railway answer this PC-routed job now?"""
    if job.status == "queued" and job.route == "cloud":
        return True
    offer, expires, lease = aware(job.offer_until), aware(job.expires_at), aware(job.lease_until)
    if expires is not None and expires <= now:
        return True
    if job.status == "queued":
        return (offer is not None and offer <= now) or (not online and job.priority != "bulk" and not job.shadow)
    if job.status == "leased":
        return lease is not None and lease <= now
    return False


def _requeue(db: Session, job: IntelJob, now: datetime) -> bool:
    """An expired PC lease gets one more chance while the claim window is open and the PC is still there."""
    got = db.execute(update(IntelJob).where(IntelJob.id == job.id, IntelJob.status == "leased", IntelJob.route == "pc",
                                            IntelJob.lease_until <= now)
                     .values(status="queued", nonce=None, lease_until=None, worker_id=None, error="pc lease expired")
                     .execution_options(synchronize_session=False))
    db.commit()
    db.refresh(job)
    return got.rowcount == 1  # type: ignore[attr-defined]


async def run(db: Session, kind: str, payload: dict[str, Any], priority: str = "slot", *, key: str | None = None, settings: Any = None,
              transport: httpx.AsyncBaseTransport | None = None, poll_seconds: float = 1.0) -> Outcome | None:
    """Enqueue and wait for the answer under the policy above. None when intel is off (the caller uses today's rules). Bulk jobs return
    at once with result_by None (pending): their answer lands on the job row."""
    s = _settings(settings)
    job = enqueue(db, kind, payload, priority, key=key, settings=s)
    if job is None:
        return None
    if job.route == "cloud":
        return await fallback(db, job, settings=s, transport=transport) or outcome(job)
    if priority == "bulk":
        return outcome(job)
    give_up = time.monotonic() + (CLAIM_WAIT[priority] or timedelta(0)).total_seconds() + channel.LEASE.total_seconds() * 2 + 30
    while time.monotonic() < give_up:
        db.refresh(job)
        if job.status in ("done", "failed", "expired"):
            return outcome(job)
        now = utcnow()
        if _needs_railway(job, now, channel.pc_online(db, now, s)):
            got = await fallback(db, job, now, settings=s, transport=transport)
            if got is not None:
                return got
        await asyncio.sleep(poll_seconds)
    db.refresh(job)
    return outcome(job)


async def tick(db: Session, now: datetime | None = None, settings: Any = None, transport: httpx.AsyncBaseTransport | None = None) -> dict[str, Any]:
    """The scheduler's pass (job intel.tick, registered only when intel_enabled): expire leases, fall back what the PC didn't answer."""
    s = _settings(settings)
    if not s.intel_enabled:
        return {"skipped": "intel disabled"}
    now = now or utcnow()
    online = channel.pc_online(db, now, s)
    counts = {"requeued": 0, "cloud": 0, "rules": 0, "shadow_expired": 0, "deferred": 0}
    live = list(db.scalars(select(IntelJob).where(IntelJob.status.in_(("queued", "leased"))).order_by(IntelJob.id).limit(500)))
    calls = 0
    for job in live:
        if not _needs_railway(job, now, online):
            continue
        offer = aware(job.offer_until)
        if (job.status == "leased" and job.route == "pc" and not job.shadow and online and job.attempts < job.max_attempts
                and (offer is None or offer > now) and (aware(job.expires_at) is None or aware(job.expires_at) > now)):  # type: ignore[operator]
            counts["requeued"] += _requeue(db, job, now)
            continue
        if job.shadow:
            await fallback(db, job, now, settings=s, transport=transport)
            counts["shadow_expired"] += 1
            continue
        if calls >= TICK_MAX_CLOUD:
            counts["deferred"] += 1
            continue
        calls += 1
        got = await fallback(db, job, now, settings=s, transport=transport)
        if got is not None:
            counts["cloud" if got.result_by == "cloud" else "rules"] += 1
    return counts


def status(db: Session, settings: Any = None, now: datetime | None = None) -> dict[str, Any]:
    """Admin's view: on/off, the PC, today's spend against the cap, and the last day's jobs by outcome."""
    from sqlalchemy import func

    from app.intel import ledger
    from app.models.intel import IntelWorker

    s = _settings(settings)
    now = now or utcnow()
    since = now - timedelta(days=1)
    rows = db.execute(select(IntelJob.status, IntelJob.result_by, func.count()).where(IntelJob.created_at >= since)
                      .group_by(IntelJob.status, IntelJob.result_by)).all()
    workers = [{"name": w.name, "enabled": w.enabled, "online": channel.fresh(w, now),
                "last_seen_at": aware(w.last_seen_at).isoformat() if w.last_seen_at else None, "models": (w.info or {}).get("models", [])}
               for w in db.scalars(select(IntelWorker))]
    return {"enabled": bool(s.intel_enabled), "token_configured": bool((s.intel_worker_token_sha256 or "").strip()),
            "pc_online": channel.pc_online(db, now, s), "cloud_model": s.intel_cloud_model, "ledger": ledger.status(db, s, now),
            "workers": workers, "jobs_24h": [{"status": st, "result_by": rb, "count": n} for st, rb, n in rows]}
