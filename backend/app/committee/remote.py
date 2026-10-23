"""Committee LLM calls answered by Vortex, the owner's home AI, over the existing worker channel (contract: Committee run).

A call becomes a cm_llm_jobs row ("l_…"). /api/vortex/claim hands it out next to the trade-check jobs as
{"id", "kind": "llm", "system", "prompt", "json_schema", "max_tokens", "temperature", "tier", "lease_seconds"}; Vortex answers
on the same /api/vortex/jobs/{id}/answer route with {"text", "model", "tokens_in", "tokens_out"} or gives up on /fail.
The answer is raw text returned to the committee (which grounds it); it is never sent to Telegram and never sanitised into a
message. A job unanswered within TIMEOUT is expired (committee.llm_expire, or by the waiting call itself) and that role falls
back to its rules text. Polling opens a short session per look; no session is held while waiting.
"""

import asyncio
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.committee import CmLlmJob

LEASE = timedelta(seconds=60)
TIMEOUT = timedelta(seconds=90)
MAX_ANSWER = 32 * 1024
OPEN = ("pending", "claimed")


class Conflict(Exception):
    """The job is answered, failed or expired already (409)."""


def _aware(d: datetime) -> datetime:
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def create(db: Session, *, role: str, tier: str, system: str, prompt: str, schema: dict[str, Any] | None, max_tokens: int,
           temperature: float, run_id: int | None = None) -> CmLlmJob:
    job = CmLlmJob(public_id="l_" + secrets.token_hex(8), run_id=run_id, role=role[:24], tier=tier, status="pending",
                   system=system, prompt=prompt, schema=schema, max_tokens=max_tokens, temperature=temperature, claims=0,
                   created_at=utcnow())
    db.add(job)
    db.commit()
    return job


def claim(db: Session, max_jobs: int, now: datetime | None = None) -> list[dict[str, Any]]:
    """Up to max_jobs pending jobs (or claimed ones whose lease ran out), still within TIMEOUT, leased for LEASE."""
    now = now or utcnow()
    if max_jobs <= 0:
        return []
    q = (select(CmLlmJob).where(CmLlmJob.status.in_(OPEN), CmLlmJob.created_at > now - TIMEOUT,
                                (CmLlmJob.status == "pending") | (CmLlmJob.leased_until < now))
         .order_by(CmLlmJob.id).limit(max_jobs).with_for_update(skip_locked=True))
    out = []
    for j in db.scalars(q).all():
        j.status, j.claims, j.leased_until = "claimed", j.claims + 1, now + LEASE
        out.append({"id": j.public_id, "kind": "llm", "system": j.system, "prompt": j.prompt, "json_schema": j.schema,
                    "max_tokens": j.max_tokens, "temperature": j.temperature, "tier": j.tier,
                    "lease_seconds": int(LEASE.total_seconds())})
    db.commit()
    return out


def find(db: Session, public_id: str) -> CmLlmJob | None:
    return db.scalar(select(CmLlmJob).where(CmLlmJob.public_id == public_id[:24]))


def pending_count(db: Session) -> int:
    return int(db.scalar(select(func.count()).select_from(CmLlmJob).where(CmLlmJob.status.in_(OPEN))) or 0)


def _take(db: Session, job: CmLlmJob, status: str, now: datetime, **values: Any) -> bool:
    res = db.execute(update(CmLlmJob).where(CmLlmJob.id == job.id, CmLlmJob.status.in_(OPEN))
                     .values(status=status, answered_at=now, **values).execution_options(synchronize_session=False))
    db.commit()
    db.refresh(job)
    return bool(res.rowcount)


def answer(db: Session, job: CmLlmJob, text: str, model: str | None = None, tokens_in: int | None = None,
           tokens_out: int | None = None, now: datetime | None = None) -> None:
    now = now or utcnow()
    if job.status not in OPEN:
        raise Conflict("This job is already finished.")
    if now - _aware(job.created_at) > TIMEOUT:
        _take(db, job, "expired", now)
        raise Conflict("Too late: the committee already used its own rules text.")
    text = (text or "").strip()
    if not text or len(text.encode()) > MAX_ANSWER:
        raise ValueError(f"text must be 1 to {MAX_ANSWER} bytes")
    if not _take(db, job, "answered", now, answer=text, model=(model or "vortex")[:64],
                 tokens_in=max(0, int(tokens_in or 0)), tokens_out=max(0, int(tokens_out or 0))):
        raise Conflict("This job is already finished.")


def fail(db: Session, job: CmLlmJob, now: datetime | None = None) -> None:
    if not _take(db, job, "failed", now or utcnow()):
        raise Conflict("This job is already finished.")


def expire(db: Session, now: datetime | None = None) -> int:
    """committee.llm_expire: open jobs older than TIMEOUT are expired (their role already fell back, or will)."""
    now = now or utcnow()
    res = db.execute(update(CmLlmJob).where(CmLlmJob.status.in_(OPEN), CmLlmJob.created_at <= now - TIMEOUT)
                     .values(status="expired", answered_at=now).execution_options(synchronize_session=False))
    db.commit()
    return int(res.rowcount or 0)


class RemoteError(Exception):
    pass


async def wait(public_id: str, timeout: float, poll: float = 0.5) -> dict[str, Any]:
    """Wait for Vortex's answer: {"text", "model", "tokens_in", "tokens_out"}. RemoteError on failure / timeout."""
    from app.db import session as dbs

    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        db = dbs.SessionLocal()
        try:
            job = find(db, public_id)
            if job is None:
                raise RemoteError("job vanished")
            if job.status == "answered":
                return {"text": job.answer or "", "model": job.model or "vortex", "tokens_in": job.tokens_in or 0,
                        "tokens_out": job.tokens_out or 0}
            if job.status in ("failed", "expired"):
                raise RemoteError(f"Vortex {job.status} the call")
            if loop.time() >= deadline:
                _take(db, job, "expired", utcnow())
                raise RemoteError("Vortex timed out")
        finally:
            db.close()
        await asyncio.sleep(poll)
