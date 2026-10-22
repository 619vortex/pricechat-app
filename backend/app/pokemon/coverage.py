"""Data coverage and the verified-sales audit (admin only).

coverage(): for the PSA 10 market (all cards and vintage), how many cards have a current price, a 30D / 90D / 1Y movement,
real chart points and verified sales, and, for each missing movement, the reason (as the table itself states it).

sales_audit(): every verified, non-excluded sale of the last N days re-checked: completed-sale provenance, card identity
(the title re-run through the matcher), edition, language, grade, price / currency / FX, and duplicates. It only reports;
nothing is changed.
"""

import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.pokemon import PokemonCard, PokemonProviderValuation, PokemonSale, PokemonValuationPoint
from app.pokemon.grades import Grade

GRADE = Grade("PSA", "10")
PERIODS = ("30d", "90d")
OUTLIER = 3.0  # a sale below a third or above 3x the median of the same card + grade is flagged for review


def _chart_days(db: Session, now: datetime) -> dict[int, int]:
    """Distinct days with a genuine PSA 10 observation in the last 90 days per card: Scrydex valuations (history + daily
    current) and days with a verified-sales estimate."""
    since = (now - timedelta(days=90)).date()
    days: dict[int, set[Any]] = defaultdict(set)
    for cid, day in db.execute(select(PokemonProviderValuation.card_id, PokemonProviderValuation.day).where(
            PokemonProviderValuation.provider == "scrydex", PokemonProviderValuation.grading_company == GRADE.company,
            PokemonProviderValuation.grade == GRADE.grade, PokemonProviderValuation.market_cents.is_not(None),
            PokemonProviderValuation.day >= since)):
        days[cid].add(day)
    for cid, day in db.execute(select(PokemonValuationPoint.card_id, PokemonValuationPoint.day).where(
            PokemonValuationPoint.grading_company == GRADE.company, PokemonValuationPoint.grade == GRADE.grade,
            PokemonValuationPoint.day >= since)):
        days[cid].add(day)
    return {cid: len(d) for cid, d in days.items()}


def _block(rows: list[dict[str, Any]], chart_days: dict[int, int], sale_cards: set[int]) -> dict[str, Any]:
    priced = [r for r in rows if r["market_price"] is not None]
    out: dict[str, Any] = {"cards": len(rows), "with_current_price": len(priced),
                           "price_source": dict(Counter(r["price_source"] or "none" for r in priced))}
    for k in PERIODS:
        have = [r for r in priced if r.get(f"change_{k}") is not None]
        out[f"with_{k}_movement"] = len(have)
        out[f"{k}_movement_source"] = dict(Counter((r.get("change_sources") or {}).get(k) or r.get("change_source") or r["price_source"] or "?" for r in have))
        out[f"missing_{k}_by_reason"] = dict(Counter((r.get("change_reasons") or {}).get(k) or "no reason recorded"
                                                     for r in priced if r.get(f"change_{k}") is None).most_common(8))
    out["with_real_chart_points"] = sum(1 for r in priced if chart_days.get(r["card"]["id"], 0) >= 2)
    out["chart_points_missing_by_reason"] = dict(Counter(
        ("only today's valuation (no history fetched)" if chart_days.get(r["card"]["id"], 0) == 1 else "no observation in the last 90 days")
        for r in priced if chart_days.get(r["card"]["id"], 0) < 2))
    out["with_verified_sales"] = sum(1 for r in rows if r["card"]["id"] in sale_cards)
    out["no_price_by_reason"] = {"no Scrydex, PriceCharting or sales value at PSA 10": len(rows) - len(priced)}
    return out


def coverage(db: Session, now: datetime | None = None) -> dict[str, Any]:
    from app.pokemon import market

    now = now or utcnow()
    rows = market.base_rows(db, GRADE, now)
    chart_days = _chart_days(db, now)
    sale_cards = set(db.scalars(select(PokemonSale.card_id).where(PokemonSale.verified.is_(True), PokemonSale.excluded.is_(False),
                                                                  PokemonSale.grading_company == GRADE.company, PokemonSale.grade == GRADE.grade).distinct()))
    first_hist = db.scalar(select(PokemonProviderValuation.day).where(PokemonProviderValuation.kind == "history").order_by(PokemonProviderValuation.day).limit(1))
    last_cur = db.scalar(select(PokemonProviderValuation.day).where(PokemonProviderValuation.kind == "current").order_by(PokemonProviderValuation.day.desc()).limit(1))
    first_sale = db.scalar(select(PokemonSale.sale_date).where(PokemonSale.verified.is_(True), PokemonSale.excluded.is_(False)).order_by(PokemonSale.sale_date).limit(1))
    return {"grade": GRADE.label, "as_of": now.isoformat(),
            "all_cards": _block(rows, chart_days, sale_cards),
            "vintage": _block([r for r in rows if r["card"]["era"] == "vintage"], chart_days, sale_cards),
            "sources": {"scrydex_history_from": first_hist.isoformat() if first_hist else None,
                        "scrydex_latest_valuation_day": last_cur.isoformat() if last_cur else None,
                        "earliest_verified_sale": first_sale.isoformat() if first_sale else None,
                        "scrydex_history_cards_psa10": len(set(db.scalars(select(PokemonProviderValuation.card_id).where(
                            PokemonProviderValuation.kind == "history", PokemonProviderValuation.grading_company == GRADE.company,
                            PokemonProviderValuation.grade == GRADE.grade).distinct())))}}


def sales_audit(db: Session, days: int = 30, now: datetime | None = None) -> dict[str, Any]:
    from app.pokemon import ebay_cards, matching

    now = now or utcnow()
    sales = list(db.scalars(select(PokemonSale).where(PokemonSale.verified.is_(True), PokemonSale.excluded.is_(False),
                                                      PokemonSale.sale_date >= now - timedelta(days=days))))
    cards = {c.id: c for c in db.scalars(select(PokemonCard).where(PokemonCard.id.in_({s.card_id for s in sales} or {0})))}
    issues: dict[str, list[dict[str, Any]]] = defaultdict(list)
    cands: dict[int, list[PokemonCard]] = {}

    def note(kind: str, s: PokemonSale, why: str) -> None:
        issues[kind].append({"sale_id": s.id, "card_id": s.card_id, "title": s.title, "price_usd": s.sale_price_usd_cents,
                             "date": s.sale_date.date().isoformat(), "source": s.source, "why": why})

    by_key: dict[tuple[int, str, str], list[int]] = defaultdict(list)
    for cid, comp, gr, cents in db.execute(select(PokemonSale.card_id, PokemonSale.grading_company, PokemonSale.grade, PokemonSale.sale_price_usd_cents)
                                           .where(PokemonSale.verified.is_(True), PokemonSale.excluded.is_(False))):
        by_key[(cid, comp, gr)].append(cents)
    seen_listing: dict[tuple[str, str], int] = {}
    seen_same: dict[tuple[int, str, str, int, Any], int] = {}
    for s in sales:
        c = cards.get(s.card_id)
        if s.verified_by not in ("source", "manual"):
            note("provenance", s, f"verified_by={s.verified_by!r}: not reported sold by the source nor confirmed by the admin")
        if (s.sale_type or "").lower() in ("listing", "active", "asking"):
            note("not_a_sale", s, f"sale_type={s.sale_type}")
        if s.currency != "USD" and not s.fx_rate:
            note("currency", s, f"{s.currency} price without an FX rate")
        if s.sale_price_usd_cents <= 0:
            note("price", s, "non-positive price")
        if s.source_listing_id:
            k = (s.source, s.source_listing_id or "")
            if k in seen_listing:
                note("duplicate", s, f"same {s.source} listing as sale {seen_listing[k]}")
            else:
                seen_listing[k] = s.id
        k2 = (s.card_id, s.grading_company, s.grade, s.sale_price_usd_cents, s.sale_date.date())
        if k2 in seen_same:  # the same transaction reported twice (e.g. by eBay and by an aggregator), whatever the listing ids
            note("possible_duplicate", s, f"same card, grade, price and day as sale {seen_same[k2]}")
        seen_same.setdefault(k2, s.id)
        peers = by_key.get((s.card_id, s.grading_company, s.grade), [])
        if len(peers) >= 5:
            med = statistics.median(peers)
            if med and not (med / OUTLIER <= s.sale_price_usd_cents <= med * OUTLIER):
                note("outlier", s, f"${s.sale_price_usd_cents / 100:,.0f} vs a median of ${med / 100:,.0f} over {len(peers)} sales of this card + grade")
        if c is None:
            note("identity", s, "card missing")
            continue
        if not s.title:
            continue  # manual / structured imports: confirmed by the admin
        pool = cands.setdefault(c.id, ebay_cards.candidates(db, c))
        m = matching.match(s.title, pool)
        p = matching.parse(s.title)
        if m.card_id != c.id or m.status != "matched":
            note("identity", s, f"matcher now says {m.status} -> card {m.card_id}: {'; '.join(m.reasons[:2])}")
        if m.grade is None or (m.grade.company, m.grade.grade) != (s.grading_company, s.grade):
            note("grade", s, f"title grade {m.grade.label if m.grade else 'none'} vs stored {s.grading_company} {s.grade}")
        if p.edition and c.edition != "—" and p.edition != c.edition:
            note("edition", s, f"title says {p.edition}, card is {c.edition}")
        if p.language and p.language != c.language:
            note("language", s, f"title says {p.language}, card is {c.language}")
    psa10 = [s for s in sales if (s.grading_company, s.grade) == (GRADE.company, GRADE.grade)]
    flagged = {x["sale_id"] for v in issues.values() for x in v}
    return {"days": days, "as_of": now.isoformat(), "sales": len(sales), "volume_usd_cents": sum(s.sale_price_usd_cents for s in sales),
            "psa10_sales": len(psa10), "psa10_volume_usd_cents": sum(s.sale_price_usd_cents for s in psa10),
            "by_source": dict(Counter(s.source for s in sales)), "by_grade": dict(Counter(f"{s.grading_company} {s.grade}" for s in sales).most_common(10)),
            "by_sale_type": dict(Counter(s.sale_type or "unknown" for s in sales)), "by_verified_by": dict(Counter(s.verified_by or "?" for s in sales)),
            "by_currency": dict(Counter(s.currency for s in sales)),
            "flagged_sales": len(flagged), "flagged_volume_usd_cents": sum(s.sale_price_usd_cents for s in sales if s.id in flagged),
            "issues": {k: {"count": len(v), "examples": v[:8]} for k, v in issues.items()}}
