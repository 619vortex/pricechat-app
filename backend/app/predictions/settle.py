"""Price Chat Predictions: verified settlement from Hyperliquid's daily candle (licence-cleared: app/growth/sources.py "hyperliquid").

Rules (shown to members in the Rules pop-up and on the Passport):
- The settlement price is the close of the Hyperliquid perpetual's daily candle for the call's day (00:00-24:00 UTC).
- The candle is read once it has closed (from 2 minutes after), then read again at least 10 minutes later. The call settles only when
  two reads match exactly; a mismatch keeps reading.
- Tie: a daily close exactly equal to the reference close settles as "tie": nobody is right or wrong, the participation XP still pays and
  streaks are unaffected. A weekly range boundary belongs to the range above it.
- Missing data: no matching reads 6 hours after the close (an outage, a missing candle) puts the call on hold; the owner resolves it by
  hand with a logged reason, or cancels it.
- Cancellation (owner): the call is void, nobody gets XP for it, and XP already written is reversed in the ledger.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.predictions import PredQuestion
from app.predictions import rules, service

log = logging.getLogger(__name__)
HL_API = "https://api.hyperliquid.xyz/info"
READ_AFTER = timedelta(minutes=2)

Reader = Callable[[str, date], "dict[str, Any] | None"]


def read_daily(coin: str, day: date) -> dict[str, Any] | None:
    """The closed daily candle of `coin` for `day` from Hyperliquid, or None (not closed yet, missing, or the API failed)."""
    start = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)
    try:
        r = httpx.post(HL_API, json={"type": "candleSnapshot", "req": {"coin": coin, "interval": "1d", "startTime": int(start.timestamp() * 1000),
                                                                      "endTime": int((start + timedelta(days=1)).timestamp() * 1000) - 1}},
                       timeout=15.0, headers={"Content-Type": "application/json"})
        r.raise_for_status()
        rows = r.json() or []
    except (httpx.HTTPError, ValueError) as e:
        log.warning("predictions: Hyperliquid candle read failed: %s", type(e).__name__)
        return None
    for k in rows:
        try:
            t, close_t, c, n = int(k["t"]), int(k["T"]), float(k["c"]), int(k.get("n") or 0)
        except (KeyError, TypeError, ValueError):
            continue
        if t == int(start.timestamp() * 1000) and n > 0 and c > 0:
            if close_t / 1000 > utcnow().timestamp():
                return None  # still open
            return {"coin": coin, "day": day.isoformat(), "open_time": t, "close_time": close_t, "close": c, "trades": n}
    return None


def reference_close(coin: str, day: date, reader: Reader = read_daily) -> float | None:
    c = reader(coin, day)
    return float(c["close"]) if c else None


def try_settle(db: Session, q: PredQuestion, now: datetime, reader: Reader = read_daily) -> str:
    """One settlement attempt for a locked call: "waiting" | "read" | "settled" | "held"."""
    if now < service._aware(q.settles_at) + READ_AFTER:  # type: ignore[operator]
        return "waiting"
    ev = dict(q.evidence or {})
    reads = list(ev.get("reads") or [])
    c = reader(q.asset, q.settle_day)
    if c is not None:
        reads.append({"at": now.isoformat(), "close": c["close"], "close_time": c["close_time"], "trades": c["trades"]})
        reads = reads[-10:]
    ev.update(reads=reads, source="Hyperliquid candleSnapshot 1d", coin=q.asset, candle_day=q.settle_day.isoformat())
    q.evidence = ev
    match = _matching_pair(reads)
    if match is not None:
        service.finalize(db, q, match, now, how="verified")
        return "settled"
    if now >= service._aware(q.settles_at) + timedelta(hours=rules.HOLD_AFTER_HOURS):  # type: ignore[operator]
        q.status = "held"
        service._audit(q, "system", "held", now, reason="no two matching reads within 6 hours of the close")
        db.commit()
        from app.services.activity import ActivityLogger

        try:
            from app.models.enums import ActivityLevel

            ActivityLogger(db).log("predictions_held", f"Prediction #{q.id} ({q.title}) is on hold: its close couldn't be verified. "
                                   "Resolve or cancel it in Admin › Community › Predictions.", level=ActivityLevel.WARNING)
        except Exception:  # noqa: BLE001 - the hold itself is what matters
            db.rollback()
        return "held"
    db.commit()
    return "read"


def _matching_pair(reads: list[dict[str, Any]]) -> float | None:
    """Two reads at least SECOND_READ_AFTER_MIN apart with exactly the same close: that close."""
    gap = timedelta(minutes=rules.SECOND_READ_AFTER_MIN)
    for i in range(len(reads)):
        for j in range(i + 1, len(reads)):
            a, b = reads[i], reads[j]
            if a["close"] == b["close"] and datetime.fromisoformat(b["at"]) - datetime.fromisoformat(a["at"]) >= gap:
                return float(b["close"])
    return None


def due_to_settle(db: Session) -> list[PredQuestion]:
    return list(db.scalars(select(PredQuestion).where(PredQuestion.status == "locked").order_by(PredQuestion.settles_at)))
