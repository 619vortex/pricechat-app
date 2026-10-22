"""Open-interest history: Hyperliquid publishes only the CURRENT open interest, so every 15 minutes Price Chat records it
(with mark and funding) for the most traded markets. Charts and the weekly summary read it back; nothing is ever filled in
between snapshots. BTC and ETH also have the Kiwi Brain's own 15-minute market observations, used when they go back further."""

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.tradingos import KbEvent, TsOiSnapshot
from app.tradingos.market import MarketData

TOP = 30  # the most traded markets (24h volume)
KEEP = timedelta(days=35)


def _aware(d: datetime) -> datetime:
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def record(db: Session, md: MarketData, now: datetime | None = None) -> dict[str, Any]:
    """One snapshot of the top markets (one Hyperliquid call), then retention."""
    now = now or utcnow()
    _, ms = md.markets()
    top = sorted((m for m in ms.values() if m.mark > 0), key=lambda m: -m.day_volume)[:TOP]
    for m in top:
        db.add(TsOiSnapshot(at=now, coin=m.coin.upper(), mark=m.mark, oi_usd=m.oi_usd, funding=m.funding))
    pruned = db.execute(delete(TsOiSnapshot).where(TsOiSnapshot.at < now - KEEP)).rowcount or 0
    db.commit()
    return {"recorded": len(top), "pruned": int(pruned)}


def history(db: Session, coin: str, since: datetime) -> list[tuple[float, float]]:
    """(unix seconds, open interest USD), oldest first: the recorder's snapshots, plus the Kiwi Brain's observations for
    times before the recorder started (BTC / ETH)."""
    coin = coin.upper()
    rows = [(_aware(r.at).timestamp(), r.oi_usd) for r in db.scalars(
        select(TsOiSnapshot).where(TsOiSnapshot.coin == coin, TsOiSnapshot.at >= since).order_by(TsOiSnapshot.at))]
    first = rows[0][0] if rows else float("inf")
    older = []
    for e in db.scalars(select(KbEvent).where(KbEvent.kind == "market", KbEvent.coin == coin, KbEvent.at >= since).order_by(KbEvent.at)):
        v = ((e.data or {}).get("market") or {}).get("open_interest_usd")
        t = _aware(e.at).timestamp()
        if v is not None and t < first - 60:
            older.append((t, float(v)))
    return older + rows


def change(db: Session, coin: str, days: int = 7, now: datetime | None = None) -> tuple[float, float, float] | None:
    """(OI then, OI now, % change) over `days`, only when the history really reaches back that far (within 6 hours)."""
    now = now or utcnow()
    h = history(db, coin, now - timedelta(days=days, hours=6))
    if len(h) < 2 or h[0][0] > (now - timedelta(days=days)).timestamp() + 6 * 3600:
        return None
    then, last = h[0][1], h[-1][1]
    return (then, last, (last - then) / then * 100) if then else None
