"""The Kalshi bot's connection and reports. Read-only on both sides: the bot (C:\\AI\\kalshi-bot) places no orders before its
Phase 7, and nothing here can send it a command — Price Chat only stores and shows what the bot reports."""

import hashlib
import json
import secrets
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.kalshi import KalshiBot, KalshiReport

TOKEN_PREFIX = "pck_"
KINDS = ("status", "discovery", "activity")
MAX_PAYLOAD = 256 * 1024
KEEP_PER_KIND = 300  # ~3 days of 15-minute sweeps; ~5 hours of 1-minute trading cycles
STALE_AFTER = timedelta(minutes=45)  # three missed 15-minute sweeps


class KalshiError(Exception):
    def __init__(self, status: int, msg: str) -> None:
        super().__init__(msg)
        self.status = status


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _bot(db: Session) -> KalshiBot:
    row = db.get(KalshiBot, 1)
    if row is None:
        row = KalshiBot(id=1)
        db.add(row)
        db.flush()
    return row


def new_token(db: Session) -> str:
    """A new bot token (shown once; only its hash is kept). Replaces the old one: the old bot stops reporting."""
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    row = _bot(db)
    row.token_hash, row.token_created_at = _hash(token), utcnow()
    db.commit()
    return token


def disconnect(db: Session) -> None:
    row = _bot(db)
    row.token_hash = None
    db.commit()


def authorize(db: Session, authorization: str | None) -> KalshiBot:
    token = (authorization or "").removeprefix("Bearer ").strip()
    row = db.get(KalshiBot, 1)
    if not token.startswith(TOKEN_PREFIX) or row is None or not row.token_hash or not secrets.compare_digest(row.token_hash, _hash(token)):
        raise KalshiError(401, "Unknown Kalshi bot token.")
    return row


def save_report(db: Session, bot: KalshiBot, kind: str, payload: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
    if kind not in KINDS:
        raise KalshiError(400, f"Unknown report kind: {kind}.")
    size = len(json.dumps(payload, default=str))
    if size > MAX_PAYLOAD:
        raise KalshiError(413, f"Report too large ({size} bytes).")
    now = utcnow()
    env = str(meta.get("env") or payload.get("env") or "")[:16]
    db.add(KalshiReport(received_at=now, kind=kind, env=env, payload=payload))
    bot.last_seen_at = now
    bot.last_bot = {k: str(meta.get(k) or "")[:40] for k in ("version", "env", "host")}
    db.flush()
    old = db.scalars(select(KalshiReport.id).where(KalshiReport.kind == kind).order_by(KalshiReport.id.desc()).offset(KEEP_PER_KIND)).all()
    if old:
        db.execute(delete(KalshiReport).where(KalshiReport.id.in_(old)))
    db.commit()
    return {"ok": True, "received_at": now.isoformat()}


def _latest(db: Session, kind: str) -> KalshiReport | None:
    return db.scalar(select(KalshiReport).where(KalshiReport.kind == kind).order_by(KalshiReport.id.desc()).limit(1))


def overview(db: Session, now: datetime | None = None) -> dict[str, Any]:
    now = now or utcnow()
    row = db.get(KalshiBot, 1)
    status, disc = _latest(db, "status"), _latest(db, "discovery")
    history = db.scalars(select(KalshiReport).where(KalshiReport.kind == "discovery").order_by(KalshiReport.id.desc()).limit(48)).all()
    seen = row.last_seen_at if row else None
    return {
        "connected": bool(row and row.token_hash),
        "token_created_at": row.token_created_at.isoformat() if row and row.token_created_at else None,
        "last_seen_at": seen.isoformat() if seen else None,
        "stale": bool(seen is None or now - seen > STALE_AFTER),
        "bot": row.last_bot if row else None,
        "status": {"received_at": status.received_at.isoformat(), **status.payload} if status else None,
        "discovery": {"received_at": disc.received_at.isoformat(), **disc.payload} if disc else None,
        "history": [{"received_at": r.received_at.isoformat(), "markets": r.payload.get("markets"), "two_sided": r.payload.get("two_sided"),
                     "with_volume_24h": r.payload.get("with_volume_24h"), "elapsed_s": r.payload.get("elapsed_s"),
                     "quote_warnings": r.payload.get("quote_warnings"), "rate_limited": r.payload.get("rate_limited")}
                    for r in reversed(history)],
        "reports": db.scalar(select(func.count(KalshiReport.id))) or 0,
    }


def public_watch(db: Session) -> dict[str, Any] | None:
    """What Earn › Kalshi may show anyone from the bot's reports: market counts, categories and the fee curve. Never the
    owner's balance, positions or account details."""
    disc, status = _latest(db, "discovery"), _latest(db, "status")
    if disc is None and status is None:
        return None
    d, st = (disc.payload if disc else {}), (status.payload if status else {})
    return {
        "received_at": (disc or status).received_at.isoformat(),  # type: ignore[union-attr]
        "markets": d.get("markets"), "events": d.get("events"), "two_sided": d.get("two_sided"), "with_volume_24h": d.get("with_volume_24h"),
        "categories": dict(list((d.get("categories") or {}).items())[:12]),
        "fee_curve": st.get("fee_curve") or [], "fee_gate_min_cents": st.get("fee_gate_min_cents"),
        "phases": st.get("phases") or [],
    }


ACTIVITY_LIVE_FOR = timedelta(minutes=5)  # the bot runs a cycle a minute; older than this = it's not running
PRIVATE_STAGES = ("account",)  # step lines that carry the owner's balance


def activity(db: Session, owner: bool, now: datetime | None = None, history: int = 60) -> dict[str, Any] | None:
    """The trading loop's latest cycle (its steps = what the bot did and why) and a short history for the pulse chart.
    Everyone sees the scan, the closest baskets and the gates; only the owner sees equity, orders and trades."""
    now = now or utcnow()
    rows = db.scalars(select(KalshiReport).where(KalshiReport.kind == "activity").order_by(KalshiReport.id.desc()).limit(history)).all()
    if not rows:
        return None
    last = rows[0]
    p = dict(last.payload)
    if not owner:
        p = {k: v for k, v in p.items() if k not in ("equity_usd", "held_usd", "deployable_usd", "trades", "recent_trades", "calls")}
        p["steps"] = [s for s in p.get("steps") or [] if s.get("stage") not in PRIVATE_STAGES]
    return {
        "received_at": last.received_at.isoformat(), "live": now - last.received_at <= ACTIVITY_LIVE_FOR, **p,
        "history": [{"ts": r.received_at.isoformat(), "mode": r.payload.get("mode"), "elapsed_s": r.payload.get("elapsed_s"),
                     "over_one_dollar": (r.payload.get("counts") or {}).get("over_one_dollar"),
                     "best_edge_cents": ((r.payload.get("closest") or [{}])[0] or {}).get("edge_cents"),
                     "trades": len(r.payload.get("trades") or []) if owner else None, "error": bool(r.payload.get("error"))}
                    for r in reversed(rows)],
    }
