"""The real-card reference price ("the index") for each market. PAPER TRADING never changes it: it only reads real data.

Sources (both already in Price Chat, nothing invented):
  1. Verified completed sales of the exact card + grade (pokemon_sales; Scrydex sold listings, verified=True,
     excluded=False), the last 365 days.
  2. Scrydex's market estimate for the exact card + grade (pokemon_provider_valuations, PSA 10), with its date.

Method:
  - Outliers: sales more than 2x away from the median of the year's sales (e.g. a misidentified $330 "PSA 10 1st
    Edition Charizard") are rejected and listed for manual review; they never move the index.
  - Sales index: the median of the clean sales of the last 180 days (the last 5, if more).
  - Combined with Scrydex: the average when both exist and agree within 35%; when they disagree more, the sales index
    if it has 3+ clean recent sales, else the market is "insufficient" (paused) pending review.
  - Confidence: high (5+ clean sales in 90 days and sources within 15%), medium (2+ clean sales in 180 days and sources
    within 35%), low (one source only, fresh).
  - Staleness: no clean sale in 120 days AND Scrydex older than 14 days -> "stale" (the simulated market maker stops
    quoting; members can still trade with each other). Nothing fresh at all -> "insufficient" (market halted).
  - Damping: a new value more than 15% away from the previous index moves it at most 15% per update and is flagged
    for review; repeated evidence keeps moving it, one suspicious observation can't move it drastically.
Every evaluation is stored (ExOracleSnapshot) with its provenance: the sales used and rejected, the Scrydex value and day.
"""

import statistics
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.exchange import ExMarket, ExOracleSnapshot
from app.models.pokemon import PokemonProviderValuation, PokemonSale
from app.pokemon.history import as_utc

LOOKBACK_DAYS = 365
RECENT_DAYS = 180
OUTLIER_FACTOR = 2.0
AGREE_PCT = 35
STRONG_AGREE_PCT = 15
MAX_STEP_PCT = 15
SALE_STALE_DAYS = 120
SCRYDEX_STALE_DAYS = 14
SCRYDEX_DEAD_DAYS = 45


def _pct(a: int, b: int) -> float:
    return abs(a - b) / max(1, min(a, b)) * 100


def evaluate(db: Session, m: ExMarket, now: datetime | None = None) -> dict[str, Any]:
    """The candidate reference for one market, with confidence, status and provenance (no writes)."""
    now = now or utcnow()
    since = now - timedelta(days=LOOKBACK_DAYS)
    rows = db.execute(select(PokemonSale.id, PokemonSale.sale_price_usd_cents, PokemonSale.sale_date, PokemonSale.source).where(
        PokemonSale.card_id == m.card_id, PokemonSale.grading_company == m.grading_company, PokemonSale.grade == m.grade,
        PokemonSale.verified.is_(True), PokemonSale.excluded.is_(False), PokemonSale.sale_date >= since)
        .order_by(PokemonSale.sale_date.desc())).all()
    sales = [(sid, int(cents), as_utc(when), src) for sid, cents, when, src in rows if cents and cents > 0]
    prov: dict[str, Any] = {"sales_considered": len(sales), "used": [], "rejected": [], "scrydex": None, "method": None}
    clean = []
    if sales:
        med = statistics.median(c for _, c, _, _ in sales)
        for sid, c, when, src in sales:
            row = {"sale_id": sid, "usd": c / 100, "date": when.date().isoformat(), "source": src}
            if c > med * OUTLIER_FACTOR or c < med / OUTLIER_FACTOR:
                prov["rejected"].append({**row, "why": f"more than {OUTLIER_FACTOR:g}x from the year's median ${med / 100:,.0f}"})
            else:
                clean.append((sid, c, when))
    recent = [x for x in clean if x[2] >= now - timedelta(days=RECENT_DAYS)][:5]
    prov["used"] = [{"sale_id": s, "usd": c / 100, "date": w.date().isoformat()} for s, c, w in recent]
    sales_idx = int(statistics.median(c for _, c, _ in recent)) if recent else None
    n90 = sum(1 for _, _, w in clean if w >= now - timedelta(days=90))
    last_clean = clean[0][2] if clean else None

    pv = db.scalar(select(PokemonProviderValuation).where(PokemonProviderValuation.card_id == m.card_id,
                                                          PokemonProviderValuation.grading_company == m.grading_company,
                                                          PokemonProviderValuation.grade == m.grade)
                   .order_by(PokemonProviderValuation.day.desc()).limit(1))
    scry = pv.market_cents if pv and pv.market_cents else None
    scry_age = (now.date() - pv.day).days if pv else None
    if pv:
        prov["scrydex"] = {"usd": (scry or 0) / 100, "day": pv.day.isoformat(), "age_days": scry_age}
    scry_ok = scry is not None and scry_age is not None and scry_age <= SCRYDEX_DEAD_DAYS

    review = bool(prov["rejected"])
    value, confidence = None, None
    if sales_idx and scry_ok:
        gap = _pct(sales_idx, scry)  # type: ignore[arg-type]
        prov["sources_gap_pct"] = round(gap, 1)
        if gap <= AGREE_PCT:
            value, prov["method"] = (sales_idx + scry) // 2, "average of the sales index and Scrydex"  # type: ignore[operator]
            confidence = "high" if n90 >= 5 and gap <= STRONG_AGREE_PCT else "medium" if len(recent) >= 2 else "low"
        elif len(recent) >= 3:
            value, prov["method"], confidence, review = sales_idx, "sales index (Scrydex disagrees)", "low", True
        else:
            prov["method"], review = "sources disagree, too few sales", True
    elif sales_idx:
        value, prov["method"], confidence = sales_idx, "sales index only", "medium" if len(recent) >= 3 else "low"
    elif scry_ok:
        value, prov["method"], confidence = scry, "Scrydex estimate only", "low"

    sale_fresh = last_clean is not None and last_clean >= now - timedelta(days=SALE_STALE_DAYS)
    scry_fresh = scry_age is not None and scry_age <= SCRYDEX_STALE_DAYS
    if value is None:
        status = "insufficient"
    elif sale_fresh or scry_fresh:
        status = "ok"
    else:
        status = "stale"
    return {"value": value, "confidence": confidence if value else None, "status": status, "review": review, "provenance": prov}


def refresh(db: Session, m: ExMarket, now: datetime | None = None) -> ExOracleSnapshot:
    """Evaluate, damp against the previous index, store the snapshot and update the market. The caller commits."""
    now = now or utcnow()
    ev = evaluate(db, m, now)
    raw = ev["value"]
    prev_card = m.ref_contract_cents * m.multiplier_den if m.ref_contract_cents else None
    card = raw
    review = ev["review"]
    if raw and prev_card:
        hi, lo = prev_card * (100 + MAX_STEP_PCT) // 100, prev_card * (100 - MAX_STEP_PCT) // 100
        if raw > hi or raw < lo:
            card = min(hi, max(lo, raw))
            review = True
            ev["provenance"]["damped"] = f"moved at most {MAX_STEP_PCT}% from ${prev_card / 100:,.0f} (raw ${raw / 100:,.0f})"
    contract = card // m.multiplier_den if card else None
    snap = ExOracleSnapshot(market_id=m.id, at=now, card_cents=card, contract_cents=contract, raw_cents=raw, status=ev["status"],
                            confidence=ev["confidence"], review=review, provenance=ev["provenance"])
    db.add(snap)
    m.ref_status, m.ref_confidence, m.ref_at = ev["status"], ev["confidence"], now
    if contract:
        m.ref_contract_cents = contract
    return snap


def refresh_all(db: Session, now: datetime | None = None) -> dict[str, Any]:
    out = {}
    for m in db.scalars(select(ExMarket)):
        s = refresh(db, m, now)
        out[m.symbol] = {"status": s.status, "confidence": s.confidence, "contract": s.contract_cents, "review": s.review}
    db.commit()
    return out
