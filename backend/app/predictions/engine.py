"""Price Chat Predictions: the once-a-minute tick (jobs.py "predictions.tick", registered only when PREDICTIONS_MODE isn't off).

1. Open today's daily call at 13:30 UTC (and Monday's weekly call), with the reference close read from Hyperliquid, and post it.
2. Keep each open post's count current (an edit, at most once a minute), send opt-in reminders an hour before the lock.
3. Lock calls at their lock time and reveal the split on the post.
4. Settle locked calls from two matching reads of the closed daily candle (settle.py); edit the post with the result.
5. Write the XP of settled votes that belong to a Passport (service.credit).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.predictions import PredQuestion
from app.predictions import rules, service, settle
from app.predictions import telegram as tg

log = logging.getLogger(__name__)


def schedule(db: Session, now: datetime, reader: settle.Reader = settle.read_daily) -> list[PredQuestion]:
    """Create the calls that should be open now and don't exist yet (idempotent by slot)."""
    made: list[PredQuestion] = []
    today = now.date()
    for asset in rules.ASSETS:
        plans = []
        if service.at(today, rules.DAILY_OPEN) <= now < service.at(today, rules.DAILY_LOCK):
            plans.append(("daily", service.daily_slot(asset, today), today))
        monday = today - timedelta(days=today.weekday())
        lock = service.at(monday + timedelta(days=rules.WEEKLY_LOCK_WEEKDAY), rules.WEEKLY_LOCK)
        if service.at(monday, rules.WEEKLY_OPEN) <= now < lock:
            plans.append(("weekly", service.weekly_slot(asset, monday), monday))
        for kind, slot, day in plans:
            if db.scalar(select(PredQuestion.id).where(PredQuestion.slot == slot)) is not None:
                continue
            ref = settle.reference_close(asset, day - timedelta(days=1), reader)
            if ref is None:
                log.info("predictions: reference close for %s not readable yet; retrying next tick", slot)
                continue
            q = service.make_daily(asset, day, ref) if kind == "daily" else service.make_weekly(asset, day, ref)
            service._audit(q, "system", "opened", now, ref=ref)
            db.add(q)
            try:
                db.commit()
            except IntegrityError:  # another process made it first
                db.rollback()
                continue
            made.append(q)
    return made


async def tick(db: Session, settings: Any, client: Any | None, now: datetime, reader: settle.Reader = settle.read_daily) -> dict[str, Any]:
    out: dict[str, Any] = {"mode": tg.mode(settings)}
    if out["mode"] == "off":
        return out
    service.ensure_catalog(db)
    made = await asyncio.to_thread(schedule, db, now, reader)
    out["opened"] = [q.slot for q in made]
    posted = 0
    for q in db.scalars(select(PredQuestion).where(PredQuestion.status == "open", PredQuestion.tg_message_id.is_(None))):
        if client is not None and await tg.announce(db, client, settings, q, now):
            posted += 1
    out["posted"] = posted
    locked = 0
    for q in list(db.scalars(select(PredQuestion).where(PredQuestion.status == "open"))):
        if now >= service._aware(q.locks_at):  # type: ignore[operator]
            q.status = "locked"
            service._audit(q, "system", "locked", now, votes=sum(service.tallies(db, q).values()))
            db.commit()
            locked += 1
            if client is not None:
                await tg.refresh(db, client, q, now, force=True)
        elif client is not None:
            await tg.refresh(db, client, q, now)
            await tg.remind(db, client, q, now)
    out["locked"] = locked
    settled = []
    for q in settle.due_to_settle(db):
        state = await asyncio.to_thread(settle.try_settle, db, q, now, reader)
        if state in ("settled", "held"):
            settled.append((q.id, state))
            if client is not None:
                await tg.refresh(db, client, q, now, force=True)
                if state == "settled":
                    await tg.personal_results(db, client, q)
    out["settled"] = settled
    out["credit"] = await asyncio.to_thread(service.credit, db, now)
    return out
