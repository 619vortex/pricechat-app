"""Price history engine: verified sales -> valuation points -> chart series, period changes, highs / lows.

A valuation point is the price engine (pricing.py) run over the sales known at the end of a day ON WHICH A VERIFIED SALE
HAPPENED. Days without a sale get no point: the series carries the last defensible estimate forward and every element says
which kind it is ("sale_day" = re-estimated because of a real sale, "carried" = the previous estimate held, "current" =
today's estimate from the same engine). Nothing here invents a transaction, a daily move or an OHLC candle.

Period change (7D / 30D / 90D / 1Y) = current estimate vs the estimate in force at the start of the period, ONLY when
  - an estimate existed at the start of the period (a point on or before it), and
  - at least one verified sale happened during the period (otherwise the change is "no new sales", not 0% or a guess).
Candles are offered only when the range holds enough real sales: >= CANDLE_MIN_WEEKS weeks with >= CANDLE_MIN_SALES each.

Points are derived data, rebuilt per card + grade whenever that series' sales change (imports, syncs, reviews, exclusions).
"""

import threading
import time as _time
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Iterable

from sqlalchemy import delete, func, select, tuple_
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.pokemon import PokemonPopulationHistory, PokemonSale, PokemonValuationPoint
from app.pokemon import pricing

Key = tuple[int, str, str]  # card id, grading company, grade

PERIODS = {"30d": 30, "90d": 90}
RANGES = {"24h": 1, "7d": 7, "30d": 30, "90d": 90, "3m": 91, "180d": 180, "1y": 365, "all": None}
CANDLE_MIN_WEEKS = 6
CANDLE_MIN_SALES = 3


def as_utc(d: datetime) -> datetime:
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


# --- cache (chart / market responses); bumped on every data change --------------------------------------------------

_lock = threading.Lock()
_generation = 0
_cache: dict[Any, tuple[int, float, Any]] = {}
CACHE_TTL = 60.0  # seconds: "now"-relative values (ages, changes) stay fresh
CACHE_MAX = 512


def invalidate() -> None:
    global _generation
    with _lock:
        _generation += 1
        _cache.clear()


def evict(match: Any) -> int:
    """Drop cached entries whose key matches (frees memory held by large cached values)."""
    with _lock:
        gone = [k for k in _cache if match(k)]
        for k in gone:
            _cache.pop(k, None)
    if gone:
        release_memory()
    return len(gone)


def release_memory() -> None:
    """Collect garbage and give freed heap pages back to the operating system. CPython keeps freed memory for reuse, so
    after a 200-400 MB build the process would otherwise stay at its peak (and creep towards the container limit)."""
    import gc
    import sys

    gc.collect()
    if sys.platform.startswith("linux"):
        try:
            import ctypes

            ctypes.CDLL("libc.so.6").malloc_trim(0)
        except (OSError, AttributeError):
            pass


def is_cached(key: Any, ttl: float = CACHE_TTL) -> bool:
    return _fresh(key, ttl)[0]


_building: dict[Any, threading.Lock] = {}  # one build per key at a time
_heavy = threading.Lock()  # whole-catalogue builds ("base" rows) never run side by side: each needs ~200-400 MB


def _fresh(key: Any, ttl: float) -> tuple[bool, Any]:
    with _lock:
        hit = _cache.get(key)
        if hit and hit[0] == _generation and _time.monotonic() - hit[1] < ttl:
            return True, hit[2]
    return False, None


def cached(key: Any, build: Any, ttl: float = CACHE_TTL) -> Any:
    """Cached value for `key`, built at most once at a time: concurrent callers wait for the first build and reuse it
    (several page requests after a sync must not each build the 50k-card rows at once: that runs a small server out of
    memory)."""
    ok, value = _fresh(key, ttl)
    if ok:
        return value
    with _lock:
        lock = _building.setdefault(key, threading.Lock())
    heavy = isinstance(key, tuple) and bool(key) and key[0] == "base"
    with lock:
        ok, value = _fresh(key, ttl)  # built by the caller we waited for
        if ok:
            return value
        with _lock:
            gen = _generation
            if heavy:
                _cache.pop(key, None)  # the stale copy goes BEFORE the rebuild: never two whole catalogues in memory
        now = _time.monotonic()
        if heavy:
            with _heavy:
                release_memory()
                value = build()
            release_memory()  # the build's temporary objects (ORM cards, loaded rows) back to the OS
        else:
            value = build()
        with _lock:
            if gen == _generation:
                if len(_cache) >= CACHE_MAX:
                    _cache.pop(next(iter(_cache)))
                _cache[key] = (gen, now, value)
    return value


# --- loading ------------------------------------------------------------------------------------------------------


def load_sales(db: Session, card_ids: Iterable[int] | None = None, company: str | None = None,
               grade: str | None = None) -> dict[Key, list[pricing.SalePoint]]:
    stmt = select(PokemonSale.id, PokemonSale.card_id, PokemonSale.grading_company, PokemonSale.grade, PokemonSale.sale_price_usd_cents,
                  PokemonSale.sale_date, PokemonSale.verified, PokemonSale.excluded, PokemonSale.source, PokemonSale.source_listing_id,
                  PokemonSale.url)
    ids = None if card_ids is None else list(card_ids)
    if ids is not None and len(ids) <= 5000:  # SQLite parameter cap: a catalogue-sized list loads all
        stmt = stmt.where(PokemonSale.card_id.in_(ids))
    if company is not None and grade is not None:
        stmt = stmt.where(PokemonSale.grading_company == company, PokemonSale.grade == grade)
    out: dict[Key, list[pricing.SalePoint]] = defaultdict(list)
    for sid, cid, comp, gr, cents, when, verified, excluded, source, listing, url in db.execute(stmt):
        out[(cid, comp, gr)].append(pricing.SalePoint(cents, as_utc(when), sale_id=sid, verified=bool(verified), excluded=bool(excluded),
                                                       source=source, has_reference=bool(listing or url)))
    for v in out.values():
        v.sort(key=lambda s: s.when)
    return out


def load_points(db: Session, card_ids: Iterable[int] | None = None, company: str | None = None,
                grade: str | None = None) -> dict[Key, list[PokemonValuationPoint]]:
    stmt = select(PokemonValuationPoint)
    ids = None if card_ids is None else list(card_ids)
    if ids is not None and len(ids) <= 5000:
        stmt = stmt.where(PokemonValuationPoint.card_id.in_(ids))
    if company is not None and grade is not None:
        stmt = stmt.where(PokemonValuationPoint.grading_company == company, PokemonValuationPoint.grade == grade)
    out: dict[Key, list[PokemonValuationPoint]] = defaultdict(list)
    for p in db.scalars(stmt.order_by(PokemonValuationPoint.day)):
        out[(p.card_id, p.grading_company, p.grade)].append(p)
    return out


# --- building ------------------------------------------------------------------------------------------------------


def is_eligible(s: pricing.SalePoint) -> bool:
    return s.verified and not s.excluded


def compute_points(sales: list[pricing.SalePoint], now: datetime) -> list[dict[str, Any]]:
    """One point per UTC day with >= 1 eligible sale: the engine run at the end of that day over the sales known by then."""
    eligible = [s for s in sales if is_eligible(s) and s.when <= now]
    by_day: dict[date, list[pricing.SalePoint]] = defaultdict(list)
    for s in eligible:
        by_day[s.when.astimezone(timezone.utc).date()].append(s)
    out = []
    for d in sorted(by_day):
        end = min(datetime.combine(d, time.max, timezone.utc), now)
        known = [s for s in sales if s.when <= end]  # unverified / excluded ones too: the engine lists and ignores them
        st = pricing.compute(known, end)
        if st.market_price is None:
            continue
        out.append({"day": d, "market_price_cents": st.market_price, "confidence": st.confidence, "sales_used": st.sales_count,
                    "sales_on_day": len(by_day[d]), "volume_on_day_cents": sum(s.price_cents for s in by_day[d])})
    return out


def rebuild(db: Session, keys: Iterable[Key] | None = None, now: datetime | None = None, commit: bool = True) -> int:
    """Recompute the valuation points of these series (all series when keys is None). Returns the number of points."""
    now = now or utcnow()
    if keys is None:
        db.execute(delete(PokemonValuationPoint))
        sales = load_sales(db)
        targets = list(sales)
    else:
        targets = sorted(set(keys))
        if not targets:
            return 0
        for cid, comp, gr in targets:
            db.execute(delete(PokemonValuationPoint).where(PokemonValuationPoint.card_id == cid, PokemonValuationPoint.grading_company == comp,
                                                           PokemonValuationPoint.grade == gr))
        sales = load_sales(db, {k[0] for k in targets})
    n = 0
    for key in targets:
        for p in compute_points(sales.get(key, []), now):
            db.add(PokemonValuationPoint(card_id=key[0], grading_company=key[1], grade=key[2], algorithm=pricing.ALGORITHM[:160], **p))
            n += 1
    if commit:
        db.commit()
    invalidate()
    return n


def ensure_built(db: Session) -> None:
    """Derived data: if sales exist but no point was ever built (fresh migration), build every series once."""
    if db.scalar(select(func.count()).select_from(PokemonValuationPoint)):
        return
    if db.scalar(select(func.count()).select_from(PokemonSale).where(PokemonSale.verified.is_(True), PokemonSale.excluded.is_(False))):
        rebuild(db)


def sale_keys(db: Session, sale_ids: Iterable[int]) -> set[Key]:
    ids = list(sale_ids)
    if not ids:
        return set()
    return {(c, co, g) for c, co, g in db.execute(select(PokemonSale.card_id, PokemonSale.grading_company, PokemonSale.grade)
                                                  .where(PokemonSale.id.in_(ids)))}


# --- reading -------------------------------------------------------------------------------------------------------


def _ts(d: date) -> datetime:
    return datetime.combine(d, time.min, timezone.utc)


def estimate_at(points: list[PokemonValuationPoint], when: datetime) -> PokemonValuationPoint | None:
    """The estimate in force at `when`: the last point whose day ended on or before it (carry-forward)."""
    best = None
    for p in points:
        if datetime.combine(p.day, time.max, timezone.utc) <= when:
            best = p
        else:
            break
    return best


def period_change(points: list[PokemonValuationPoint], current: int | None, now: datetime, days: int) -> dict[str, Any]:
    """{"value": % or None, "reason": why None, "base": the estimate compared against}."""
    start = now - timedelta(days=days)
    if current is None:
        return {"value": None, "reason": "no current estimate", "base": None, "base_day": None}
    base = estimate_at(points, start)
    if base is None:
        return {"value": None, "reason": f"no estimate {'24 hours' if days == 1 else f'{days} days'} ago", "base": None, "base_day": None}
    if not any(p.day > base.day for p in points):  # every later point is a day with a verified sale inside the period
        return {"value": None, "reason": f"no verified sales in {'24 hours' if days == 1 else f'{days} days'}", "base": base.market_price_cents,
                "base_day": base.day.isoformat()}
    return {"value": pricing.change(current, base.market_price_cents), "reason": None, "base": base.market_price_cents,
            "base_day": base.day.isoformat()}


def changes(points: list[PokemonValuationPoint], current: int | None, now: datetime) -> dict[str, dict[str, Any]]:
    return {k: period_change(points, current, now, d) for k, d in PERIODS.items()}


def series(points: list[PokemonValuationPoint], current: int | None, now: datetime, since: datetime | None) -> list[dict[str, Any]]:
    """Chart line: [carried start] + sale-day estimates in range + [today's estimate]. `kind` says what each point is.
    The range starts at UTC midnight so every point sits on a day boundary (one point per day, in order)."""
    out: list[dict[str, Any]] = []
    start = _ts(since.date()) if since is not None else None
    inside = [p for p in points if (start is None or _ts(p.day) >= start) and _ts(p.day) <= now]
    if start is not None:
        base = estimate_at(points, start)
        if base is not None and not (inside and _ts(inside[0].day) == start):
            out.append({"t": start.isoformat(), "v": base.market_price_cents, "kind": "carried", "confidence": base.confidence,
                        "from_day": base.day.isoformat()})
    for p in inside:
        out.append({"t": _ts(p.day).isoformat(), "v": p.market_price_cents, "kind": "sale_day", "confidence": p.confidence,
                    "sales": p.sales_on_day, "volume": p.volume_on_day_cents, "sales_used": p.sales_used})
    if current is not None and out and out[-1]["t"][:10] != now.date().isoformat():
        out.append({"t": _ts(now.date()).isoformat(), "v": current, "kind": "current"})
    return out


def sparkline(points: list[PokemonValuationPoint], current: int | None, now: datetime, days: int = 30) -> dict[str, Any] | None:
    """Compact 30-day series for table rows: [[unix seconds, cents], ...]; `carried_only` when no sale fell in the window."""
    s = series(points, current, now, now - timedelta(days=days))
    if len(s) < 2:
        return None
    return {"points": [[int(datetime.fromisoformat(p["t"]).timestamp()), p["v"]] for p in s],
            "carried_only": not any(p["kind"] == "sale_day" for p in s)}


def extremes(sales: list[pricing.SalePoint], points: list[PokemonValuationPoint]) -> dict[str, Any]:
    """Historical highest / lowest OBSERVED (verified, not excluded) sale, and highest / lowest estimate."""
    ok = [s for s in sales if is_eligible(s)]
    hi = max(ok, key=lambda s: (s.price_cents, s.when), default=None)
    lo = min(ok, key=lambda s: (s.price_cents, s.when), default=None)
    phi = max(points, key=lambda p: p.market_price_cents, default=None)
    plo = min(points, key=lambda p: p.market_price_cents, default=None)
    sale = lambda s: {"price": s.price_cents, "at": s.when.isoformat(), "sale_id": s.sale_id, "source": s.source} if s else None  # noqa: E731
    point = lambda p: {"price": p.market_price_cents, "day": p.day.isoformat()} if p else None  # noqa: E731
    return {"highest_sale": sale(hi), "lowest_sale": sale(lo), "highest_estimate": point(phi), "lowest_estimate": point(plo)}


def volume_buckets(sales: list[pricing.SalePoint], since: datetime | None, now: datetime) -> list[dict[str, Any]]:
    """Daily verified trading volume (count + USD) on days that had sales. Empty days are simply absent."""
    by_day: dict[date, list[int]] = defaultdict(list)
    for s in sales:
        if is_eligible(s) and (since is None or s.when >= since) and s.when <= now:
            by_day[s.when.astimezone(timezone.utc).date()].append(s.price_cents)
    return [{"t": _ts(d).isoformat(), "count": len(v), "usd": sum(v)} for d, v in sorted(by_day.items())]


def candles(sales: list[pricing.SalePoint], since: datetime | None, now: datetime) -> tuple[list[dict[str, Any]] | None, str]:
    """Weekly OHLC from real sales only, and only when there are enough of them. (candles or None, why)."""
    weeks: dict[date, list[pricing.SalePoint]] = defaultdict(list)
    for s in sales:
        if is_eligible(s) and (since is None or s.when >= since) and s.when <= now:
            d = s.when.astimezone(timezone.utc).date()
            weeks[d - timedelta(days=d.weekday())].append(s)
    full = {w: v for w, v in weeks.items() if len(v) >= CANDLE_MIN_SALES}
    if len(full) < CANDLE_MIN_WEEKS:
        return None, (f"Candles need at least {CANDLE_MIN_WEEKS} weeks with {CANDLE_MIN_SALES}+ verified sales each in this range "
                      f"(found {len(full)}).")
    out = []
    for w in sorted(full):
        v = sorted(full[w], key=lambda s: s.when)
        p = [s.price_cents for s in v]
        out.append({"t": _ts(w).isoformat(), "open": p[0], "high": max(p), "low": min(p), "close": p[-1], "sales": len(p)})
    return out, f"Weekly candles from real sales; weeks with fewer than {CANDLE_MIN_SALES} sales are left out, not filled."


def population_series(db: Session, card_id: int, company: str, grade: str) -> list[dict[str, Any]]:
    rows = db.scalars(select(PokemonPopulationHistory).where(
        PokemonPopulationHistory.card_id == card_id, PokemonPopulationHistory.grading_company == company,
        PokemonPopulationHistory.grade == grade).order_by(PokemonPopulationHistory.day))
    by_day: dict[date, tuple[int, str]] = {}
    for r in rows:  # one count per day; with several sources the larger report (the most complete) wins
        if r.day not in by_day or r.count > by_day[r.day][0]:
            by_day[r.day] = (r.count, r.source)
    return [{"t": _ts(d).isoformat(), "count": c, "source": s} for d, (c, s) in sorted(by_day.items())]


def record_population(db: Session, card_id: int, company: str, grade: str, count: int, source: str, as_of: datetime) -> None:
    """Keep every population report (one per card + grade + source + day). The caller commits."""
    d = as_utc(as_of).date()
    row = db.scalar(select(PokemonPopulationHistory).where(
        PokemonPopulationHistory.card_id == card_id, PokemonPopulationHistory.grading_company == company,
        PokemonPopulationHistory.grade == grade, PokemonPopulationHistory.source == source, PokemonPopulationHistory.day == d))
    if row is None:
        db.add(PokemonPopulationHistory(card_id=card_id, grading_company=company, grade=grade, count=count, source=source, day=d))
        db.flush()
    else:
        row.count = count


def latest_points(db: Session, keys: Iterable[Key]) -> dict[Key, PokemonValuationPoint]:
    ks = list(keys)
    if not ks:
        return {}
    sub = (select(PokemonValuationPoint.card_id, PokemonValuationPoint.grading_company, PokemonValuationPoint.grade,
                  func.max(PokemonValuationPoint.day).label("d"))
           .where(tuple_(PokemonValuationPoint.card_id, PokemonValuationPoint.grading_company, PokemonValuationPoint.grade).in_(ks))
           .group_by(PokemonValuationPoint.card_id, PokemonValuationPoint.grading_company, PokemonValuationPoint.grade).subquery())
    stmt = select(PokemonValuationPoint).join(sub, (PokemonValuationPoint.card_id == sub.c.card_id)
                                              & (PokemonValuationPoint.grading_company == sub.c.grading_company)
                                              & (PokemonValuationPoint.grade == sub.c.grade) & (PokemonValuationPoint.day == sub.c.d))
    return {(p.card_id, p.grading_company, p.grade): p for p in db.scalars(stmt)}
