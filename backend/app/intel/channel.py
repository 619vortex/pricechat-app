"""The PC <-> Railway intel channel (doc §4.1): outbound-only, scoped token, leased jobs, HMAC-signed results.

The owner's PC (RTX 5090, Ollama on 127.0.0.1) runs worker/pricechat_worker/intel.py, which only ever calls OUT to /api/intel-worker/*.
Nothing reaches the PC: no tunnel, no open port.

Security properties
- Auth: a pci_ token used ONLY for this channel (never a Brand Manager pcw_ token, never the admin cookie). Railway knows only its
  SHA-256 (INTEL_WORKER_TOKEN_SHA256); the token stays on the PC. Rotating the configured hash retires the old token at once.
- Claim: only while the worker's heartbeat is fresh (<= 60 s); each claimed job gets a 120 s lease and a random single-use nonce.
- Result / fail: HMAC-SHA256 keyed by the raw token over "<job id>\\n<nonce>\\n<canonical JSON>". Verified before any state change;
  the nonce is consumed by a compare-and-swap together with the status change, so a replay (or a second answer) gets 409, and a
  result arriving after its lease is refused (Railway has answered or will answer it itself).
- Content: an analysis must validate against app.intel.schema.ItemAnalysis, an embedding must be exactly intel_embed_dim finite
  numbers. Anything else is refused (422) and the job goes to Railway's fallback; it is never stored as a good result.
- The PC proposes labels; it cannot publish anything (nothing in this module touches News Desk, Trending or X).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from pydantic import ValidationError
from sqlalchemy import case, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.intel import prompt
from app.intel.schema import ItemAnalysis
from app.models.intel import IntelJob, IntelWorker

TOKEN_PREFIX = "pci_"
HEARTBEAT_FRESH = timedelta(seconds=60)
LEASE = timedelta(seconds=120)
MAX_CLAIM = 4
WORKER_NAME = "Owner PC"
KINDS = ("intel.analyze", "intel.embed", "growth.write")  # growth.write: the Growth Engine's Originals writer (app/growth/writer.py)
CHAT_KINDS = ("intel.analyze", "growth.write")  # answered by the PC's chat model with a JSON schema
GROWTH_TEXT_MAX = 1000
PRIORITIES = ("breaking", "slot", "bulk")


class ChannelError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def _settings(settings: Any = None) -> Any:
    if settings is None:
        from app.config.settings import get_settings

        settings = get_settings()
    return settings


def aware(t: datetime | None) -> datetime | None:
    return t if t is None or t.tzinfo else t.replace(tzinfo=timezone.utc)


# --- token -----------------------------------------------------------------------------------------------------------------------


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_token() -> tuple[str, str]:
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    return token, hash_token(token)


def worker_for(db: Session, token: str | None, settings: Any = None) -> IntelWorker | None:
    """The worker this bearer token belongs to, or None (unknown, malformed, rotated out, or switched off)."""
    if not token or not token.startswith(TOKEN_PREFIX) or len(token) > 200:
        return None
    configured = (_settings(settings).intel_worker_token_sha256 or "").strip().lower()
    h = hash_token(token)
    if not configured or not secrets.compare_digest(configured, h):
        return None
    w = db.scalar(select(IntelWorker).where(IntelWorker.token_hash == h))
    if w is None:  # first contact with the configured token: its record (heartbeat, off switch) is made now
        w = IntelWorker(name=WORKER_NAME, token_hash=h, enabled=True, info={})
        db.add(w)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            w = db.scalar(select(IntelWorker).where(IntelWorker.token_hash == h))
    return w if w is not None and w.enabled else None


# --- signatures ------------------------------------------------------------------------------------------------------------------


def canonical(obj: Any) -> bytes:
    """The exact bytes both sides sign (worker/pricechat_worker/intel.py has the same function)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()


def sign(token: str, job_id: int, nonce: str, obj: Any) -> str:
    return hmac.new(token.encode(), f"{job_id}\n{nonce}\n".encode() + canonical(obj), hashlib.sha256).hexdigest()


def verify(token: str, job_id: int, nonce: str, obj: Any, signature: str) -> bool:
    try:
        expected = sign(token, job_id, nonce, obj)
    except (TypeError, ValueError):  # not JSON-serialisable / NaN: can't have been signed honestly
        return False
    return hmac.compare_digest(expected, (signature or "").strip().lower())


# --- heartbeat -------------------------------------------------------------------------------------------------------------------


AVAIL_KEY = "avail"  # IntelWorker.info[AVAIL_KEY]: {"YYYY-MM-DDTHH" (UTC hour): [seconds online, seconds ready]} for Admin's availability
AVAIL_KEEP = timedelta(days=15)  # Admin's window is at most 14 days; older hours are dropped at the next heartbeat


def _hour(t: datetime) -> str:
    return t.strftime("%Y-%m-%dT%H")


def accrue(avail: dict[str, Any] | None, prev_seen: datetime | None, prev_ready: bool, now: datetime) -> dict[str, list[float]]:
    """The availability record after one heartbeat. The interval since the previous heartbeat counts as online when it is no longer than
    HEARTBEAT_FRESH (the PC was routable the whole time), and as ready when the previous heartbeat reported models and not busy (the
    pc_online rule). Split at UTC hour boundaries. One small JSON field on the worker row: no table, no extra write."""
    out: dict[str, list[float]] = {}
    for k, v in (avail or {}).items():
        if isinstance(v, list) and len(v) == 2:
            out[str(k)] = [float(v[0] or 0), float(v[1] or 0)]
    prev = aware(prev_seen)
    if prev is not None and timedelta(0) < now - prev <= HEARTBEAT_FRESH:
        t = prev
        while t < now:
            nxt = min(now, t.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1))
            secs = (nxt - t).total_seconds()
            cell = out.setdefault(_hour(t), [0.0, 0.0])
            cell[0] = round(min(3600.0, cell[0] + secs), 1)
            if prev_ready:
                cell[1] = round(min(3600.0, cell[1] + secs), 1)
            t = nxt
    oldest = _hour(now - AVAIL_KEEP)
    return {k: v for k, v in sorted(out.items()) if k >= oldest}


def heartbeat(db: Session, w: IntelWorker, models: list[str], info: dict[str, Any], now: datetime) -> dict[str, Any]:
    old = w.info or {}
    avail = accrue(old.get(AVAIL_KEY), w.last_seen_at, bool(old.get("models")) and not old.get("busy"), now)
    w.last_seen_at = now
    w.info = {"models": [str(m)[:80] for m in models[:50]], "version": str(info.get("version") or "")[:20],
              "busy": bool(info.get("busy")), "gpu": str(info.get("gpu") or "")[:80], AVAIL_KEY: avail}
    db.commit()
    return {"ok": True, "enabled": bool(_settings().intel_enabled), "lease_seconds": int(LEASE.total_seconds())}


def fresh(w: IntelWorker, now: datetime) -> bool:
    seen = aware(w.last_seen_at)
    return seen is not None and now - seen <= HEARTBEAT_FRESH


def pc_online(db: Session, now: datetime, settings: Any = None) -> bool:
    """A job may be offered to the PC only while this is true (the configured token's worker heartbeated in the last 60 s)."""
    configured = (_settings(settings).intel_worker_token_sha256 or "").strip().lower()
    if not configured:
        return False
    w = db.scalar(select(IntelWorker).where(IntelWorker.token_hash == configured, IntelWorker.enabled.is_(True)))
    if w is None or not fresh(w, now):
        return False
    # The worker keeps heartbeating while its Ollama is down (models=[], busy=true). Such a PC is not online for routing, or every job
    # would wait out its claim window (180 s for a slot) before Railway answered it.
    info = w.info or {}
    return bool(info.get("models")) and not info.get("busy")


# --- claim -----------------------------------------------------------------------------------------------------------------------


def offer(job: IntelJob, nonce: str, settings: Any = None) -> dict[str, Any]:
    """What the PC receives for a claimed job: everything it needs, nothing else (prompt and schema come from Railway, so both sides
    always use the same contract)."""
    out: dict[str, Any] = {"id": job.id, "kind": job.kind, "priority": job.priority, "nonce": nonce,
                           "lease_until": aware(job.lease_until).isoformat() if job.lease_until else None}
    p = job.payload or {}
    if job.kind == "intel.analyze":
        out.update({"system": prompt.SYSTEM, "user": prompt.user_message(str(p.get("title") or ""), str(p.get("summary") or ""),
                                                                          [str(o) for o in p.get("outlets") or []]),
                    "schema": prompt.json_schema(), "prompt_version": prompt.PROMPT_VERSION})
    elif job.kind == "growth.write":  # the prompt was built by app.growth.writer when the job was queued (placeholders, no figures)
        out.update({"system": str(p.get("system") or ""), "user": str(p.get("user") or ""), "schema": p.get("schema") or {},
                    "prompt_version": str(p.get("prompt_version") or "")})
    else:
        out.update({"text": str(p.get("text") or ""), "purpose": p.get("purpose") or "document", "dim": int(_settings(settings).intel_embed_dim)})
    return out


def claim(db: Session, w: IntelWorker, n: int, now: datetime, settings: Any = None) -> list[dict[str, Any]]:
    s = _settings(settings)
    if not s.intel_enabled:
        return []
    if not fresh(w, now):
        raise ChannelError(409, "Send a heartbeat first (claims need a heartbeat in the last 60 s).")
    n = max(1, min(MAX_CLAIM, int(n)))
    rank = case((IntelJob.priority == "breaking", 0), (IntelJob.priority == "slot", 1), else_=2)
    candidates = list(db.scalars(
        select(IntelJob).where(IntelJob.status == "queued", IntelJob.route == "pc")
        .where((IntelJob.offer_until.is_(None)) | (IntelJob.offer_until > now))
        .where((IntelJob.expires_at.is_(None)) | (IntelJob.expires_at > now))
        .order_by(rank, IntelJob.id).limit(n * 3)))
    out: list[dict[str, Any]] = []
    for job in candidates:
        if len(out) >= n:
            break
        nonce = secrets.token_hex(16)
        lease = now + LEASE
        got = db.execute(update(IntelJob).where(IntelJob.id == job.id, IntelJob.status == "queued", IntelJob.route == "pc")
                         .values(status="leased", nonce=nonce, lease_until=lease, worker_id=w.id, attempts=IntelJob.attempts + 1,
                                 claimed_at=now).execution_options(synchronize_session=False))
        db.commit()
        if got.rowcount == 1:  # type: ignore[attr-defined]
            db.refresh(job)
            out.append(offer(job, nonce, s))
    return out


# --- result / fail ---------------------------------------------------------------------------------------------------------------


def validate_output(kind: str, output: Any, settings: Any = None) -> Any:
    """The cleaned output, or ChannelError(422). Never trusts the PC's own validation."""
    if kind == "intel.analyze":
        try:
            return ItemAnalysis.model_validate(output).model_dump()
        except ValidationError as e:
            raise ChannelError(422, f"analysis fails the schema ({e.error_count()} errors)") from e
    if kind == "growth.write":  # only the words: app.growth.writer substitutes the figures and runs the figures gate itself
        if not isinstance(output, dict) or not isinstance(output.get("text"), str) or not output["text"].strip():
            raise ChannelError(422, "growth.write output must be {\"text\": \"...\"}")
        if len(output["text"]) > GROWTH_TEXT_MAX:
            raise ChannelError(422, f"growth.write text longer than {GROWTH_TEXT_MAX} characters")
        return {"text": output["text"]}
    dim = int(_settings(settings).intel_embed_dim)
    if not isinstance(output, list) or len(output) != dim:
        raise ChannelError(422, f"embedding must be a list of {dim} numbers")
    vec: list[float] = []
    for x in output:
        if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(float(x)):
            raise ChannelError(422, "embedding must contain finite numbers only")
        vec.append(float(x))
    return vec


def _lease_job(db: Session, w: IntelWorker, job_id: int, nonce: str, signature: str, signed: Any, token: str, now: datetime) -> IntelJob:
    """The job this worker holds a live lease on under this nonce, after the signature checks out. No state is changed here."""
    if not verify(token, job_id, nonce, signed, signature):
        raise ChannelError(403, "Bad signature.")
    job = db.get(IntelJob, job_id)
    if job is None:
        raise ChannelError(404, "No such job.")
    if job.status != "leased" or job.route != "pc" or job.worker_id != w.id or not job.nonce or not secrets.compare_digest(job.nonce, nonce):
        raise ChannelError(409, "Not this worker's lease, or the nonce was already used.")
    lease = aware(job.lease_until)
    if lease is None or lease <= now:
        raise ChannelError(409, "The lease has expired; Railway answers this job itself.")
    return job


def latency_ms(job: IntelJob, now: datetime) -> int:
    created = aware(job.created_at) or now
    return max(0, int((now - created).total_seconds() * 1000))


def complete(db: Session, w: IntelWorker, job_id: int, nonce: str, signature: str, result: dict[str, Any], token: str, now: datetime,
             settings: Any = None) -> dict[str, Any]:
    job = _lease_job(db, w, job_id, nonce, signature, result, token, now)
    try:
        output = validate_output(job.kind, result.get("output"), settings)
    except ChannelError as e:
        to_fallback(db, job, nonce, f"pc result rejected: {e}", now)
        raise
    stored = {"output": output, "model": str(result.get("model") or "")[:80], "seconds": _num(result.get("seconds"))}
    got = db.execute(update(IntelJob).where(IntelJob.id == job.id, IntelJob.status == "leased", IntelJob.nonce == nonce)
                     .values(status="done", result=stored, result_by="pc", nonce=None, finished_at=now, latency_ms=latency_ms(job, now),
                             error=None).execution_options(synchronize_session=False))
    db.commit()
    if got.rowcount != 1:  # type: ignore[attr-defined]
        raise ChannelError(409, "The job changed while the result arrived.")
    return {"ok": True, "id": job.id}


def fail(db: Session, w: IntelWorker, job_id: int, nonce: str, signature: str, error: str, token: str, now: datetime) -> dict[str, Any]:
    job = _lease_job(db, w, job_id, nonce, signature, {"error": error}, token, now)
    to_fallback(db, job, nonce, f"pc failed: {error}"[:500], now)
    return {"ok": True, "id": job.id}


def to_fallback(db: Session, job: IntelJob, nonce: str | None, reason: str, now: datetime) -> bool:
    """The PC gave up (or answered badly): Railway answers it (route cloud, queued for the router), a shadow job just ends. Nonce consumed."""
    cond = [IntelJob.id == job.id, IntelJob.status == "leased"]
    if nonce is not None:
        cond.append(IntelJob.nonce == nonce)
    values: dict[str, Any] = ({"status": "failed", "finished_at": now} if job.shadow else {"status": "queued", "route": "cloud"})
    got = db.execute(update(IntelJob).where(*cond).values(nonce=None, lease_until=None, error=reason[:500], **values)
                     .execution_options(synchronize_session=False))
    db.commit()
    db.refresh(job)
    return got.rowcount == 1  # type: ignore[attr-defined]


def _num(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(float(v)) else None
