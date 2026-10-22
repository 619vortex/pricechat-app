"""Daily usage counters (model calls by tier/task, Telegram requests, ...) for compute/cost visibility.

`bump` only adds to an in-memory tally, so it never opens a database write inside a running job
(holding a write while awaiting a model would block every other writer). `flush` saves the tally in
its own short transaction; the scheduler calls it after each job and readers call it before reading.
"""

import threading
from collections import defaultdict
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import utcnow
from app.models.social_intel import UsageCounter

_lock = threading.Lock()
_pending: dict[tuple[str, str, str], list[float]] = defaultdict(lambda: [0, 0.0])


def today() -> str:
    return utcnow().strftime("%Y-%m-%d")


def bump(kind: str, key: str, count: int = 1, seconds: float = 0.0) -> None:
    with _lock:
        item = _pending[(today(), kind, key[:64])]
        item[0] += count
        item[1] += seconds


def flush(session_factory: sessionmaker[Session]) -> None:
    with _lock:
        items = dict(_pending)
        _pending.clear()
    if not items:
        return
    try:
        with session_factory() as db:
            for (day, kind, key), (count, seconds) in items.items():
                row = db.scalar(select(UsageCounter).where(UsageCounter.day == day, UsageCounter.kind == kind, UsageCounter.key == key))
                if row is None:
                    row = UsageCounter(day=day, kind=kind, key=key, count=0, seconds=0.0)
                    db.add(row)
                row.count = (row.count or 0) + int(count)
                row.seconds = round((row.seconds or 0.0) + float(seconds), 2)
            db.commit()
    except Exception:  # noqa: BLE001 - put them back; counters must never break real work
        with _lock:
            for k, (count, seconds) in items.items():
                _pending[k][0] += count
                _pending[k][1] += seconds


def summary(db: Session, days: int = 1) -> dict[str, dict[str, float]]:
    """{kind: {key: count}} plus '<kind>:seconds' totals over the last `days` days (UTC), including unsaved tallies."""
    since = (utcnow() - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    out: dict[str, dict[str, float]] = {}
    rows = db.execute(select(UsageCounter.kind, UsageCounter.key, func.sum(UsageCounter.count), func.sum(UsageCounter.seconds))
                      .where(UsageCounter.day >= since).group_by(UsageCounter.kind, UsageCounter.key))
    for kind, key, n, secs in rows:
        out.setdefault(kind, {})[key] = int(n or 0)
        out.setdefault(f"{kind}:seconds", {})[key] = round(float(secs or 0), 1)
    with _lock:
        for (day, kind, key), (count, seconds) in _pending.items():
            if day >= since:
                out.setdefault(kind, {})[key] = out.get(kind, {}).get(key, 0) + int(count)
                out.setdefault(f"{kind}:seconds", {})[key] = round(out.get(f"{kind}:seconds", {}).get(key, 0) + float(seconds), 1)
    return out


def reset_for_tests() -> None:
    with _lock:
        _pending.clear()
