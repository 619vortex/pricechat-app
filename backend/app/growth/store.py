"""Market Memory storage: idempotent upserts into mm_series (one value per series per UTC day) and reads back as plain series."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.market_memory import MmSeries

BATCH = 500  # rows per statement / commit: small batches keep locks short on the production database


def _insert(db: Session) -> Any:
    name = db.get_bind().dialect.name
    if name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    elif name == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    else:  # pragma: no cover - production is Postgres, local/tests SQLite
        return None
    return insert


def upsert(db: Session, series: str, points: Iterable[tuple[date, float]], source: str, *, now: datetime | None = None) -> int:
    """Write (day, value) points for one series; an existing day is overwritten (the latest fetch wins). Non-finite values are skipped.
    Returns the number of points written. Commits every BATCH rows."""
    import math

    now = now or utcnow()
    rows = [{"series": series[:80], "day": d, "value": float(v), "source": source[:30], "fetched_at": now}
            for d, v in points if v is not None and math.isfinite(float(v))]
    if not rows:
        return 0
    dedup: dict[date, dict[str, Any]] = {}
    for r in rows:
        dedup[r["day"]] = r
    rows = sorted(dedup.values(), key=lambda r: r["day"])
    insert = _insert(db)
    for i in range(0, len(rows), BATCH):
        chunk = rows[i:i + BATCH]
        if insert is not None:
            stmt = insert(MmSeries).values(chunk)
            stmt = stmt.on_conflict_do_update(index_elements=["series", "day"],
                                              set_={"value": stmt.excluded.value, "source": stmt.excluded.source,
                                                    "fetched_at": stmt.excluded.fetched_at})
            db.execute(stmt)
        else:  # pragma: no cover
            for r in chunk:
                got = db.scalar(select(MmSeries).where(MmSeries.series == r["series"], MmSeries.day == r["day"]))
                if got is None:
                    db.add(MmSeries(**r))
                else:
                    got.value, got.source, got.fetched_at = r["value"], r["source"], r["fetched_at"]
        db.commit()
    return len(rows)


def last_day(db: Session, series: str) -> date | None:
    return db.scalar(select(func.max(MmSeries.day)).where(MmSeries.series == series))


def first_day(db: Session, series: str) -> date | None:
    return db.scalar(select(func.min(MmSeries.day)).where(MmSeries.series == series))


def load(db: Session, series: str) -> list[tuple[date, float]]:
    return [(d, float(v)) for d, v in db.execute(select(MmSeries.day, MmSeries.value).where(MmSeries.series == series).order_by(MmSeries.day))]


def load_prefix(db: Session, prefix: str) -> dict[str, list[tuple[date, float]]]:
    """Every series whose key starts with prefix (e.g. 'px:'), each oldest first."""
    out: dict[str, list[tuple[date, float]]] = {}
    for s, d, v in db.execute(select(MmSeries.series, MmSeries.day, MmSeries.value).where(MmSeries.series.like(prefix + "%"))
                              .order_by(MmSeries.series, MmSeries.day)):
        out.setdefault(s, []).append((d, float(v)))
    return out


def freshness(db: Session) -> list[dict[str, Any]]:
    """Per series family: how many series, rows and the newest day (Admin's Market Memory freshness)."""
    rows = db.execute(select(MmSeries.series, func.count(), func.min(MmSeries.day), func.max(MmSeries.day), func.max(MmSeries.source))
                      .group_by(MmSeries.series)).all()
    fam: dict[str, dict[str, Any]] = {}
    for s, n, lo, hi, src in rows:
        k = s.split(":")[0] + (":" + s.split(":")[-1] if s.startswith(("px:", "liq:")) else "")
        f = fam.setdefault(k, {"family": k, "series": 0, "rows": 0, "first": lo, "last": hi, "source": src})
        f["series"] += 1
        f["rows"] += int(n)
        f["first"] = min(f["first"], lo)
        f["last"] = max(f["last"], hi)
    return [{**f, "first": f["first"].isoformat(), "last": f["last"].isoformat()} for f in sorted(fam.values(), key=lambda f: f["family"])]
