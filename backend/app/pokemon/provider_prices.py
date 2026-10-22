"""Provider market valuations (Scrydex): read side.

Precedence everywhere: Price Chat's own verified-sales engine first; a provider valuation is used as the market price ONLY
when the engine has none for that card + grade, and it is always labelled (price_source = "scrydex", with the provider's
as-of date). Changes then come from the provider's own published trends, labelled as such. Nothing is interpolated.
"""

from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.pokemon import PokemonProviderValuation

Key = tuple[int, str, str]
PROVIDER_NAMES = {"scrydex": "Scrydex", "pricecharting": "PriceCharting"}
TREND_KEYS = {"24h": "days_1", "7d": "days_7", "30d": "days_30", "90d": "days_90"}


BIG_IN = 5000  # SQLite allows ~32k parameters per statement

def _where(card_ids: Iterable[int] | None, company: str | None, grade: str | None) -> list[Any]:
    w: list[Any] = [PokemonProviderValuation.currency == "USD", PokemonProviderValuation.market_cents.is_not(None)]
    ids = None if card_ids is None else list(card_ids)
    if ids is not None and len(ids) <= BIG_IN:  # SQLite caps parameters: a catalogue-sized list loads all (callers look up their keys)
        w.append(PokemonProviderValuation.card_id.in_(ids))
    if company:
        w.append(PokemonProviderValuation.grading_company == company)
    if grade:
        w.append(PokemonProviderValuation.grade == grade)
    return w


def latest(db: Session, card_ids: Iterable[int] | None = None, company: str | None = None, grade: str | None = None) -> dict[Key, PokemonProviderValuation]:
    """The most recent CURRENT valuation per card + grade (one query)."""
    out: dict[Key, PokemonProviderValuation] = {}
    for r in db.scalars(select(PokemonProviderValuation).where(PokemonProviderValuation.kind == "current", *_where(card_ids, company, grade))
                        .order_by(PokemonProviderValuation.day)):
        out[(r.card_id, r.grading_company, r.grade)] = r
    return out


def series(db: Session, card_ids: Iterable[int] | None = None, company: str | None = None, grade: str | None = None,
           since: date | None = None) -> dict[Key, list[PokemonProviderValuation]]:
    """Every daily valuation (history + current) per card + grade, oldest first (one query); only from `since` when given."""
    out: dict[Key, list[PokemonProviderValuation]] = defaultdict(list)
    w = _where(card_ids, company, grade) + ([PokemonProviderValuation.day >= since] if since else [])
    for r in db.scalars(select(PokemonProviderValuation).where(*w).order_by(PokemonProviderValuation.day)):
        out[(r.card_id, r.grading_company, r.grade)].append(r)
    return out


def observations(db: Session, card_ids: Iterable[int] | None, company: str | None, grade: str | None, since: date) -> dict[Key, list[tuple[date, int]]]:
    """Scrydex valuations (provider history + the daily current values Price Chat stored) per card + grade since `since`:
    plain (day, cents) tuples, one per day (the current valuation wins over a history point of the same day), oldest
    first. Lean on purpose: the whole catalogue's year of observations is loaded at once."""
    w = _where(card_ids, company, grade) + [PokemonProviderValuation.provider == "scrydex", PokemonProviderValuation.day >= since]
    by: dict[Key, dict[date, tuple[int, int]]] = defaultdict(dict)
    for cid, comp, gr, day, cents, kind in db.execute(select(
            PokemonProviderValuation.card_id, PokemonProviderValuation.grading_company, PokemonProviderValuation.grade,
            PokemonProviderValuation.day, PokemonProviderValuation.market_cents, PokemonProviderValuation.kind).where(*w)):
        rank = 1 if kind == "current" else 0
        prev = by[(cid, comp, gr)].get(day)
        if prev is None or rank >= prev[0]:
            by[(cid, comp, gr)][day] = (rank, cents)
    return {k: [(d, v[1]) for d, v in sorted(days.items())] for k, days in by.items()}


def ts(d: date) -> str:
    return datetime.combine(d, time.min, timezone.utc).isoformat()


def provider_out(r: PokemonProviderValuation | None) -> dict[str, Any] | None:
    if r is None:
        return None
    return {"provider": r.provider, "name": PROVIDER_NAMES.get(r.provider, r.provider), "market": r.market_cents, "low": r.low_cents,
            "mid": r.mid_cents, "high": r.high_cents, "currency": r.currency, "as_of": r.day.isoformat(), "fetched_at": r.fetched_at.isoformat(),
            "variant": r.provider_variant, "provider_card_id": r.provider_card_id,
            "trends": {k: (r.trends or {}).get(v, {}).get("percent_change") for k, v in TREND_KEYS.items()}}


def trend(r: PokemonProviderValuation | None, period: str) -> float | None:
    if r is None or period not in TREND_KEYS:
        return None
    v = (r.trends or {}).get(TREND_KEYS[period], {}).get("percent_change")
    return round(float(v), 2) if isinstance(v, (int, float)) else None


def line(points: list[PokemonProviderValuation], since: datetime | None, baseline: bool = False) -> list[dict[str, Any]]:
    """The provider's observations since `since`. baseline=True also keeps the last observation BEFORE the window (at its
    real date): the value in effect when the window opens, so a sparkline's start matches the period's change."""
    inside = [{"t": ts(p.day), "v": p.market_cents, "kind": p.kind} for p in points
              if p.market_cents is not None and (since is None or datetime.combine(p.day, time.max, timezone.utc) >= since)]
    if baseline and since is not None:
        before = [p for p in points if p.market_cents is not None and datetime.combine(p.day, time.max, timezone.utc) < since]
        if before:
            inside.insert(0, {"t": ts(before[-1].day), "v": before[-1].market_cents, "kind": before[-1].kind})
    return inside


# --- PriceCharting reference values (exact grades only: PSA 10, BGS 10, CGC 10) ---------------------------------------

DISAGREE = 0.25  # flag when two sources differ by more than 25% of the lower value


def references(db: Session, card_ids: Iterable[int] | None = None, company: str | None = None, grade: str | None = None,
               provider: str = "pricecharting") -> dict[Key, Any]:
    """Latest reference price per card + EXACT grade (rows with an exact grade only; "Grade 9" labels are excluded)."""
    from app.models.pokemon import PokemonReferencePrice

    w: list[Any] = [PokemonReferencePrice.provider == provider, PokemonReferencePrice.grading_company.is_not(None), PokemonReferencePrice.currency == "USD"]
    ids = None if card_ids is None else list(card_ids)
    if ids is not None and len(ids) <= BIG_IN:
        w.append(PokemonReferencePrice.card_id.in_(ids))
    if company:
        w.append(PokemonReferencePrice.grading_company == company)
    if grade:
        w.append(PokemonReferencePrice.grade == grade)
    out: dict[Key, Any] = {}
    for r in db.scalars(select(PokemonReferencePrice).where(*w).order_by(PokemonReferencePrice.as_of)):
        out[(r.card_id, r.grading_company, r.grade)] = r
    return out


def reference_out(r: Any, shown: int | None) -> dict[str, Any] | None:
    if r is None:
        return None
    diff = None
    if shown and r.price_cents:
        diff = round((shown - r.price_cents) / r.price_cents * 100, 1)
    low = min(shown or 0, r.price_cents) if shown else None
    disagree = bool(shown and low and abs(shown - r.price_cents) / low > DISAGREE)
    return {"provider": r.provider, "name": PROVIDER_NAMES.get(r.provider, r.provider), "price": r.price_cents, "as_of": r.as_of.isoformat(),
            "product_id": r.provider_product_id, "diff_pct": diff, "disagree": disagree}


# --- PriceCharting as the main source: its daily values as recorded by Price Chat -------------------------------------
# PriceCharting's API has no history. Every sync stores that day's value (pokemon_reference_prices, one row per day), so the
# moves, sparklines and charts of PriceCharting-priced rows are built from those recorded days only; before enough days
# exist they are None ("—"), never borrowed from another source.

PERIOD_DAYS = {"24h": 1, "7d": 7, "30d": 30, "90d": 90, "1y": 365}


def reference_series(db: Session, card_ids: Iterable[int] | None = None, company: str | None = None, grade: str | None = None,
                     provider: str = "pricecharting") -> dict[Key, list[Any]]:
    from app.models.pokemon import PokemonReferencePrice

    w: list[Any] = [PokemonReferencePrice.provider == provider, PokemonReferencePrice.grading_company.is_not(None), PokemonReferencePrice.currency == "USD"]
    ids = None if card_ids is None else list(card_ids)
    if ids is not None and len(ids) <= BIG_IN:
        w.append(PokemonReferencePrice.card_id.in_(ids))
    if company:
        w.append(PokemonReferencePrice.grading_company == company)
    if grade:
        w.append(PokemonReferencePrice.grade == grade)
    out: dict[Key, list[Any]] = defaultdict(list)
    for r in db.scalars(select(PokemonReferencePrice).where(*w).order_by(PokemonReferencePrice.as_of)):
        out[(r.card_id, r.grading_company, r.grade)].append(r)
    return out


def value_on(series: list[Any], when: datetime) -> Any:
    best = None
    for r in series:
        if r.as_of <= when.date():
            best = r
        else:
            break
    return best


def recorded_changes(series: list[Any], now: datetime) -> dict[str, dict[str, Any]]:
    """% change of the latest recorded value vs the value recorded at the start of each period (or why there is none)."""
    out: dict[str, dict[str, Any]] = {}
    if not series:
        return {k: {"value": None, "reason": "no PriceCharting value"} for k in PERIOD_DAYS}
    cur = series[-1]
    for k, days in PERIOD_DAYS.items():
        base = value_on(series, now - timedelta(days=days))
        if base is None or base.as_of >= cur.as_of:
            out[k] = {"value": None, "reason": f"PriceCharting history recorded since {series[0].as_of.isoformat()}"}
        else:
            out[k] = {"value": round((cur.price_cents - base.price_cents) / base.price_cents * 100, 2) if base.price_cents else None,
                      "reason": None, "base": base.price_cents, "base_day": base.as_of.isoformat()}
    return out


def reference_line(series: list[Any], since: datetime | None) -> list[dict[str, Any]]:
    return [{"t": ts(r.as_of), "v": r.price_cents, "kind": "pricecharting"} for r in series
            if since is None or datetime.combine(r.as_of, time.max, timezone.utc) >= since]
