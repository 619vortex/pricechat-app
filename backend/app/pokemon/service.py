"""Pokémon Vintage Market Cap: valuations, market cap, dashboard sections, imports, snapshots, watchlist.

Rules: every number comes from stored records (sales, populations, Price Chat's own snapshots). Unknown stays None
("—"). Grades are never combined. Market cap is always an ESTIMATE (price x known population of that grade).
"""

import hashlib
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.pokemon import (
    PokemonCard, PokemonListing, PokemonPopulation, PokemonPriceSnapshot, PokemonReferencePrice, PokemonSale, PokemonSourceState, PokemonWatch,
)
from app.pokemon import history as price_history
from app.pokemon import pricing
from app.pokemon.catalog import search_text
from app.pokemon.grades import HEADLINE, Grade, GradeError, normalise

MIN_SNAPSHOT_GAP = timedelta(minutes=50)
LOW_POP_MAX = 50  # "low pop gems": graded copies at that grade at most this many
# Reference rates for sales imported in another currency (USD per unit). Shown with the import; editable in code only.
USD_RATES = {"USD": 1.0}


# --- card output ---------------------------------------------------------------------------------------------------


def _picture(c: PokemonCard) -> str | None:
    """The card's picture for its own print (an Unlimited card without the 1st Edition stamp of the shared scan)."""
    from app.members.packs import picture

    return picture(c)


def card_out(c: PokemonCard, light: bool = False) -> dict[str, Any]:
    """light=True (market lists): skips the PSA link fields, so external_ids is never loaded for 50k rows."""
    ids = {} if light else (c.external_ids or {})
    return {"id": c.id, "name": c.name, "pokemon_name": c.pokemon_name, "set_name": c.set_name, "set_code": c.set_code,
            "card_number": c.card_number, "year": c.year, "language": c.language, "edition": c.edition, "rarity": c.rarity,
            "variant": c.variant, "holo_type": c.holo_type, "era": c.era, "is_promo": c.is_promo, "image_url": _picture(c),
            "image_source": c.image_source, "release_date": c.release_date.isoformat() if c.release_date else None,
            "psa_spec": ids.get("psa_spec"), "psa_certs": ids.get("psa_certs", [])}


# --- per card + grade ------------------------------------------------------------------------------------------------


def _sales(db: Session, card_ids: list[int] | None = None) -> dict[tuple[int, str, str], list[pricing.SalePoint]]:
    stmt = select(PokemonSale.id, PokemonSale.card_id, PokemonSale.grading_company, PokemonSale.grade, PokemonSale.sale_price_usd_cents,
                  PokemonSale.sale_date, PokemonSale.verified, PokemonSale.excluded, PokemonSale.source, PokemonSale.source_listing_id,
                  PokemonSale.url)
    if card_ids is not None:
        stmt = stmt.where(PokemonSale.card_id.in_(card_ids))
    out: dict[tuple[int, str, str], list[pricing.SalePoint]] = defaultdict(list)
    for sid, cid, comp, grade, cents, when, verified, excluded, source, listing, url in db.execute(stmt):
        out[(cid, comp, grade)].append(pricing.SalePoint(
            cents, when if when.tzinfo else when.replace(tzinfo=timezone.utc), sale_id=sid, verified=bool(verified), excluded=bool(excluded),
            source=source, has_reference=bool(listing or url)))
    return out


def _populations(db: Session, card_ids: list[int] | None = None) -> dict[tuple[int, str, str], tuple[int, str, datetime]]:
    """The most recent reported population per card + grade: (count, source, as of)."""
    stmt = select(PokemonPopulation)
    if card_ids is not None:
        stmt = stmt.where(PokemonPopulation.card_id.in_(card_ids))
    out: dict[tuple[int, str, str], tuple[int, str, datetime]] = {}
    for p in db.scalars(stmt.order_by(PokemonPopulation.as_of)):
        out[(p.card_id, p.grading_company, p.grade)] = (p.count, p.source, p.as_of)
    return out


def _past_prices(db: Session, now: datetime, days: float, card_ids: list[int] | None = None) -> dict[tuple[int, str, str], PokemonPriceSnapshot]:
    """The latest snapshot per card + grade taken at or before now - days."""
    cutoff = now - timedelta(days=days)
    stmt = select(PokemonPriceSnapshot).where(PokemonPriceSnapshot.taken_at <= cutoff)
    if card_ids is not None:
        stmt = stmt.where(PokemonPriceSnapshot.card_id.in_(card_ids))
    out: dict[tuple[int, str, str], PokemonPriceSnapshot] = {}
    for s in db.scalars(stmt.order_by(PokemonPriceSnapshot.taken_at)):
        out[(s.card_id, s.grading_company, s.grade)] = s
    return out


MOVES = (("30d", 30), ("90d", 90))


def moves(points: list[Any], price: int | None, now: datetime) -> dict[str, Any]:
    """change_<period> (None unless defensible: an estimate at the period start AND a verified sale since) + the reasons."""
    out: dict[str, Any] = {}
    why: dict[str, str] = {}
    for label, d in MOVES:
        c = price_history.period_change(points, price, now, d)
        out[f"change_{label}"] = c["value"]
        if c["reason"]:
            why[label] = c["reason"]
    out["change_reasons"] = why
    return out


def valuations(db: Session, card_ids: list[int] | None = None, now: datetime | None = None) -> dict[tuple[int, str, str], dict[str, Any]]:
    """Every card + grade that has any data (sales or population): engine stats, confidence, moves, population, estimated cap.
    Moves compare today's estimate with the price history engine's estimate at the start of the period, and only when a
    verified sale happened since (history.period_change); otherwise None, with the reason."""
    now = now or utcnow()
    wanted = None if card_ids is None else set(card_ids)
    card_ids = None if card_ids is not None and len(card_ids) > 5000 else card_ids  # SQLite parameter limit: load all, filter below
    sales, pops = _sales(db, card_ids), _populations(db, card_ids)
    points = price_history.load_points(db, card_ids)
    from app.pokemon import movement, provider_prices

    prov = provider_prices.latest(db, card_ids)
    refs = provider_prices.references(db, card_ids)
    obs = provider_prices.observations(db, card_ids, None, None, (now - timedelta(days=90 + movement.MAX_BASE_GAP)).date())
    out: dict[tuple[int, str, str], dict[str, Any]] = {}
    for key in set(sales) | set(pops) | set(prov) | set(refs):
        if wanted is not None and key[0] not in wanted:
            continue
        cid, comp, grade = key
        g = Grade(comp, grade)
        st = pricing.compute(sales.get(key, []), now)
        pop = pops.get(key)
        price = st.market_price
        out[key] = {
            "card_id": cid, "grading_company": comp, "grade": grade, "label": g.label, "graded": g.graded,
            "market_price": price, "confidence": st.confidence, **moves(points.get(key, []), price, now), "stats": st.as_dict(),
            "population": pop[0] if pop else None, "population_source": pop[1] if pop else None,
            "population_as_of": pop[2].isoformat() if pop else None,
            "market_cap": pricing.market_cap(price, pop[0] if pop else None, g.graded),
            "sales_7d": st.sales_7d, "volume_7d": st.volume_7d, "sales_30d": st.sales_30d, "volume_30d": st.volume_30d, "sort": g.sort_key,
            "price_source": "sales" if price is not None else None, "provider": provider_prices.provider_out(prov.get(key)),
        }
        pv, ref = prov.get(key), refs.get(key)
        val = out[key]
        if price is None and pv is not None:  # labelled provider valuation
            val["market_price"], val["price_source"], val["confidence"] = pv.market_cents, "scrydex", None
            val["market_cap"] = pricing.market_cap(pv.market_cents, pop[0] if pop else None, g.graded)
        elif price is None and ref is not None:  # backup only: PriceCharting's value where Scrydex has none (no history)
            val["market_price"], val["price_source"], val["confidence"] = ref.price_cents, "pricecharting", None
            val["market_cap"] = pricing.market_cap(ref.price_cents, pop[0] if pop else None, g.graded)
        # movements: the same sources and rules as the market table (movement.py)
        mv = movement.movements(val["price_source"], points.get(key, []), val["market_price"], pv.trends if pv else None,
                                obs.get(key, []), pv.market_cents if pv else None, now)
        for k, m in mv.items():
            val[f"change_{k}"] = m["value"]
        val["change_sources"] = {k: m["source"] for k, m in mv.items() if m["source"]}
        val["change_reasons"] = {k: m["reason"] for k, m in mv.items() if m["reason"]}
        val["reference"] = provider_prices.reference_out(ref, None if val["price_source"] == "pricecharting" else val["market_price"])
    return out


def _empty_valuation(card_id: int, g: Grade) -> dict[str, Any]:
    return {"card_id": card_id, "grading_company": g.company, "grade": g.grade, "label": g.label, "graded": g.graded, "market_price": None,
            "confidence": None, "change_30d": None, "change_90d": None,
            "change_reasons": {}, "change_sources": {}, "stats": pricing.Stats().as_dict(), "population": None,
            "population_source": None, "population_as_of": None, "market_cap": None, "sales_7d": 0, "volume_7d": 0, "sales_30d": 0,
            "volume_30d": 0, "sort": g.sort_key}


# --- filters ------------------------------------------------------------------------------------------------------


def _filtered_cards(db: Session, f: dict[str, Any]) -> list[PokemonCard]:
    cards = list(db.scalars(select(PokemonCard)))
    def ok(c: PokemonCard) -> bool:
        if f.get("era") and c.era != f["era"]:
            return False
        if f.get("language") and c.language.lower() != str(f["language"]).lower():
            return False
        if f.get("edition") and c.edition.lower() != str(f["edition"]).lower():
            return False
        if f.get("promo") and not c.is_promo:
            return False
        if f.get("year") and c.year != int(f["year"]):
            return False
        if f.get("set") and c.set_name.lower() != str(f["set"]).lower():
            return False
        if f.get("pokemon") and c.pokemon_name.lower() != str(f["pokemon"]).lower():
            return False
        return True
    return [c for c in cards if ok(c)]


def _grade_ok(v: dict[str, Any], f: dict[str, Any]) -> bool:
    if f.get("company") and v["grading_company"] != str(f["company"]).upper():
        return False
    if f.get("grade"):
        try:
            want = normalise(*str(f["grade"]).split(None, 1)) if " " in str(f["grade"]) else None
        except GradeError:
            want = None
        if want is None or (v["grading_company"], v["grade"]) != (want.company, want.grade):
            return False
    return True


def filter_options(db: Session) -> dict[str, list[Any]]:
    from app.pokemon.catalog import lite

    cards = lite(db)
    return {"years": sorted({c.year for c in cards}), "sets": sorted({c.set_name for c in cards}),
            "pokemon": sorted({c.pokemon_name for c in cards}), "languages": sorted({c.language for c in cards}),
            "editions": sorted({c.edition for c in cards if c.edition != "—"})}


# --- dashboard -------------------------------------------------------------------------------------------------------


def _row(card: PokemonCard, v: dict[str, Any]) -> dict[str, Any]:
    return {"card": card_out(card), **{k: v[k] for k in ("grading_company", "grade", "label", "market_price", "confidence", "change_30d", "change_90d",
                                                          "change_sources", "change_reasons", "population", "market_cap", "sales_7d",
                                                          "volume_7d", "sales_30d", "volume_30d")},
            "last_sale": v["stats"]["last_sale"], "last_sale_at": v["stats"]["last_sale_at"]}


HEADLINE_ORDER = (("PSA", "10"), ("PSA", "9"), ("BGS", "9.5"), ("CGC", "10"), ("PSA", "8"), ("RAW", "NM"))


def headline(vals: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The card's default market: PSA 10 when it has a market price, otherwise the best-documented grade. Always labelled."""
    priced = [v for v in vals if v["market_price"] is not None]
    for comp, grade in HEADLINE_ORDER:
        hit = next((v for v in priced if (v["grading_company"], v["grade"]) == (comp, grade)), None)
        if hit:
            return hit
    if priced:
        return max(priced, key=lambda v: (v["stats"]["sales_count"], v["market_price"]))
    with_pop = [v for v in vals if v["population"] is not None]
    return next((v for comp, grade in HEADLINE_ORDER for v in with_pop if (v["grading_company"], v["grade"]) == (comp, grade)),
                with_pop[0] if with_pop else None)


def overview(db: Session, f: dict[str, Any] | None = None, now: datetime | None = None) -> dict[str, Any]:
    f, now = f or {}, now or utcnow()
    cards = {c.id: c for c in _filtered_cards(db, f)}
    vals = [v for v in valuations(db, list(cards), now).values() if _grade_ok(v, f)]
    rows = [_row(cards[v["card_id"]], v) for v in vals]  # every card + grade (sections, totals)
    per_card: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for v in vals:
        per_card[v["card_id"]].append(v)
    heads = [_row(cards[cid], h) for cid, vs in per_card.items() if (h := headline(vs)) is not None]
    by_cap_all = sorted((r for r in rows if r["market_cap"] is not None), key=lambda r: -r["market_cap"])
    by_cap = sorted((r for r in heads if r["market_cap"] is not None), key=lambda r: -r["market_cap"])
    for i, r in enumerate(by_cap):
        r["rank"] = i + 1
    rest = sorted((r for r in heads if r["market_cap"] is None), key=lambda r: (-(r["market_price"] or 0), r["card"]["year"]))
    covered = set(per_card)
    no_data = [{"card": card_out(c), "grading_company": None, "grade": None, "label": None, "market_price": None, "confidence": None,
                "change_30d": None, "change_90d": None, "change_sources": {}, "change_reasons": {}, "population": None, "market_cap": None, "sales_7d": 0, "volume_7d": 0, "sales_30d": 0,
                "volume_30d": 0, "last_sale": None, "last_sale_at": None} for c in sorted(cards.values(), key=lambda c: (c.year, c.set_name, c.name))
               if c.id not in covered and not (f.get("grade") or f.get("company"))]
    priced = [r for r in rows if r["market_price"] is not None]
    total_cap = sum(r["market_cap"] for r in by_cap_all) if by_cap_all else None

    def cap_change(days: int) -> float | None:
        past = _past_prices(db, now, days, list(cards))
        cur = past_total = 0
        for r in by_cap_all:
            s = past.get((r["card"]["id"], r["grading_company"], r["grade"]))
            if s is not None and s.market_cap_cents:
                cur += r["market_cap"]
                past_total += s.market_cap_cents
        return pricing.change(cur, past_total) if past_total else None  # only cards known at both times

    day_ago = now - timedelta(days=1)
    all_sales = [s for s in db.scalars(select(PokemonSale).where(PokemonSale.excluded.is_(False)).order_by(PokemonSale.sale_date.desc()))
                 if s.card_id in cards]  # sales are few: filtered here, not with a catalogue-sized IN clause
    vol_24h = sum(s.sale_price_usd_cents for s in all_sales if price_history.as_utc(s.sale_date) >= day_ago)
    new_sales = [sale_out(s, cards[s.card_id]) for s in all_sales[:10]]
    with_30d = [r for r in priced if r["change_30d"] is not None]
    graded_pop = [r for r in rows if r["grading_company"] not in (None, "RAW") and r["population"] is not None and r["population"] <= LOW_POP_MAX]
    return {
        "hero": {"total_market_cap": total_cap, "change_24h": cap_change(1) if by_cap_all else None, "change_7d": cap_change(7) if by_cap_all else None,
                 "cards_tracked": len(cards), "sales_tracked": len(all_sales),
                 "volume_24h": int(vol_24h or 0)},
        "top": by_cap + rest + no_data,
        "sections": {
            "gainers": sorted((r for r in with_30d if r["change_30d"] > 0), key=lambda r: -r["change_30d"])[:5],
            "losers": sorted((r for r in with_30d if r["change_30d"] < 0), key=lambda r: r["change_30d"])[:5],
            "most_traded": sorted((r for r in rows if r["sales_30d"] > 0), key=lambda r: -r["sales_30d"])[:5],
            "most_valuable": sorted(priced, key=lambda r: -r["market_price"])[:5],
            "new_sales": new_sales,
            "low_pop": sorted(graded_pop, key=lambda r: r["population"])[:5],
        },
        "filters": filter_options(db),
        "has_market_data": bool(rows),
    }


def card_detail(db: Session, card: PokemonCard, now: datetime | None = None) -> dict[str, Any]:
    now = now or utcnow()
    vals = valuations(db, [card.id], now)
    by_grade = {(v["grading_company"], v["grade"]): v for v in vals.values()}
    headline_cards = [by_grade.get(g, _empty_valuation(card.id, Grade(*g))) for g in HEADLINE]
    others = sorted((v for k, v in by_grade.items() if k not in HEADLINE), key=lambda v: v["sort"])
    pops = [{"grading_company": k[1], "grade": k[2], "count": v[0], "source": v[1], "as_of": v[2].isoformat()}
            for k, v in sorted(_populations(db, [card.id]).items(), key=lambda kv: Grade(kv[0][1], kv[0][2]).sort_key, reverse=True)]
    by_company: dict[str, int] = defaultdict(int)
    for p in pops:
        by_company[p["grading_company"]] += p["count"]
    sales = [sale_out(s, card) for s in db.scalars(select(PokemonSale).where(PokemonSale.card_id == card.id)
                                                      .order_by(PokemonSale.sale_date.desc()).limit(50))]
    listings = [listing_out(x) for x in db.scalars(select(PokemonListing).where(
        PokemonListing.card_id == card.id, PokemonListing.kind == "active", PokemonListing.match_status == "matched")
        .order_by(PokemonListing.observed_at.desc()).limit(10))]
    refs = [reference_out(x) for x in db.scalars(select(PokemonReferencePrice).where(PokemonReferencePrice.card_id == card.id)
                                                  .order_by(PokemonReferencePrice.as_of.desc()).limit(12))]
    caps = [v["market_cap"] for v in vals.values() if v["market_cap"] is not None]
    head = headline(list(vals.values()))
    from app.pokemon.market import grades_with_data

    return {"card": card_out(card), "headline": head, "valuations": headline_cards, "other_grades": others, "population": pops,
            "grades_with_data": grades_with_data(db, card.id),
            "population_totals": dict(by_company), "recent_sales": sales, "active_listings": listings, "reference_prices": refs,
            "total_market_cap": sum(caps) if caps else None, "watched": is_watched(db, card.id)}


RANGES = {"7d": 7, "30d": 30, "3m": 91, "1y": 365, "all": None}


def history(db: Session, card: PokemonCard, g: Grade, range_key: str, now: datetime | None = None) -> dict[str, Any]:
    """Chart data: Price Chat's own snapshots (the line) and the individual sales (dots) for one grade."""
    now = now or utcnow()
    days = RANGES.get(range_key, 30)
    since = now - timedelta(days=days) if days else None
    snaps = select(PokemonPriceSnapshot).where(PokemonPriceSnapshot.card_id == card.id, PokemonPriceSnapshot.grading_company == g.company,
                                               PokemonPriceSnapshot.grade == g.grade)
    sales = select(PokemonSale).where(PokemonSale.card_id == card.id, PokemonSale.grading_company == g.company, PokemonSale.grade == g.grade)
    if since is not None:
        snaps = snaps.where(PokemonPriceSnapshot.taken_at >= since)
        sales = sales.where(PokemonSale.sale_date >= since)
    return {"grade": g.label, "range": range_key,
            "line": [{"t": s.taken_at.isoformat(), "v": s.market_price_cents} for s in db.scalars(snaps.order_by(PokemonPriceSnapshot.taken_at))
                     if s.market_price_cents is not None],
            "sales": [{"t": s.sale_date.isoformat(), "v": s.sale_price_usd_cents, "source": s.source, "excluded": s.excluded}
                      for s in db.scalars(sales.order_by(PokemonSale.sale_date))]}


# --- sales & population import --------------------------------------------------------------------------------------


def sale_out(s: PokemonSale, card: PokemonCard | None = None) -> dict[str, Any]:
    return {"id": s.id, "card_id": s.card_id, "card": card_out(card) if card else None, "grading_company": s.grading_company,
            "grade": s.grade, "label": Grade(s.grading_company, s.grade).label, "price": s.sale_price_cents, "currency": s.currency,
            "price_usd": s.sale_price_usd_cents, "fx_rate": s.fx_rate, "fx_source": s.fx_source, "sale_date": s.sale_date.isoformat(),
            "source": s.source, "source_listing_id": s.source_listing_id, "auction_house": s.auction_house, "sale_type": s.sale_type,
            "url": s.url, "verified": s.verified, "verified_by": s.verified_by, "excluded": s.excluded, "cert_number": s.cert_number,
            "title": s.title, "match_confidence": s.match_confidence}


def provenance(db: Session, card: PokemonCard, g: Grade, now: datetime | None = None) -> dict[str, Any]:
    """WHY IS THIS PRICE $X? Every sale of this card + grade: used (with its weight) or left out (with the reason)."""
    now = now or utcnow()
    pts = _sales(db, [card.id]).get((card.id, g.company, g.grade), [])
    st, prov = pricing.evaluate(pts, now)
    by_id = {s.id: s for s in db.scalars(select(PokemonSale).where(PokemonSale.card_id == card.id, PokemonSale.grading_company == g.company,
                                                                    PokemonSale.grade == g.grade))}
    for part in ("included", "excluded"):
        for r in prov[part]:
            sale = by_id.get(r["sale_id"])
            if sale is not None:
                r.update(url=sale.url, source_listing_id=sale.source_listing_id, verified_by=sale.verified_by, currency=sale.currency,
                         original_price=sale.sale_price_cents, auction_house=sale.auction_house)
    pop = _populations(db, [card.id]).get((card.id, g.company, g.grade))
    return {"card": card_out(card), "grade": g.label, "market_price": st.market_price, "stats": st.as_dict(), **prov,
            "latest_sale": st.last_sale, "latest_sale_at": st.last_sale_at.isoformat() if st.last_sale_at else None,
            "population": {"count": pop[0], "source": pop[1], "as_of": pop[2].isoformat()} if pop else None,
            "market_cap": pricing.market_cap(st.market_price, pop[0] if pop else None, g.graded),
            "computed_at": now.isoformat()}


def fingerprint(source: str, listing_id: str | None, card_id: int, g: Grade, price_cents: int, when: datetime) -> str:
    """Same source + listing id = same sale. Without a listing id: same card, grade, price, day and source."""
    basis = f"{source}|{listing_id}" if listing_id else f"{source}|{card_id}|{g.label}|{price_cents}|{when:%Y-%m-%d}"
    return hashlib.sha256(basis.lower().encode()).hexdigest()


def _parse_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        d = value
    else:
        s = str(value or "").strip()
        d = datetime.fromisoformat(s[:-1] + "+00:00" if s.endswith("Z") else s)
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def import_sales(db: Session, rows: list[dict[str, Any]], source: str = "manual", verified: bool = False) -> dict[str, Any]:
    """Validate, normalise and store sale rows. Duplicates (same listing, or same fingerprint) are skipped, never doubled."""
    out: dict[str, Any] = {"imported": 0, "duplicates": 0, "errors": []}
    seen: set[str] = set()
    touched: set[tuple[int, str, str]] = set()
    for i, r in enumerate(rows):
        try:
            card = db.get(PokemonCard, int(r["card_id"]))
            if card is None:
                raise ValueError("unknown card_id")
            g = normalise(r.get("grading_company") or "RAW", r.get("grade"))
            price = round(float(r["price"]) * 100)
            if price <= 0:
                raise ValueError("price must be above 0")
            currency = str(r.get("currency") or "USD").upper()
            if currency not in USD_RATES:
                raise ValueError(f"no USD rate for {currency}: import USD prices")
            when = _parse_dt(r["sale_date"])
            if when > utcnow() + timedelta(days=1):
                raise ValueError("sale date is in the future")
            src = str(r.get("source") or source).strip().lower()[:32]
            listing = str(r["source_listing_id"]).strip()[:128] if r.get("source_listing_id") else None
        except (KeyError, ValueError, TypeError, GradeError) as exc:
            out["errors"].append({"row": i + 1, "error": str(exc)})
            continue
        fp = fingerprint(src, listing, card.id, g, price, when)
        if fp in seen or db.scalar(select(PokemonSale.id).where(PokemonSale.fingerprint == fp)):
            out["duplicates"] += 1
            continue
        seen.add(fp)
        db.add(PokemonSale(card_id=card.id, grading_company=g.company, grade=g.grade, cert_number=(str(r["cert_number"])[:32] if r.get("cert_number") else None),
                           sale_price_cents=price, currency=currency, sale_price_usd_cents=round(price * USD_RATES[currency]), sale_date=when,
                           source=src, source_listing_id=listing, auction_house=(str(r.get("auction_house"))[:64] if r.get("auction_house") else None),
                           url=(str(r.get("url"))[:512] if r.get("url") else None), sale_type=(str(r.get("sale_type"))[:24] if r.get("sale_type") else None),
                           verified=bool(r.get("verified", verified)), verified_by="manual" if r.get("verified", verified) else None,
                           fx_rate=round(USD_RATES[currency], 6), fx_source="usd" if currency == "USD" else "admin", fingerprint=fp))
        touched.add((card.id, g.company, g.grade))
        out["imported"] += 1
    _record_source(db, "manual", out["imported"], None)
    db.commit()
    price_history.rebuild(db, touched)
    return out


def import_population(db: Session, rows: list[dict[str, Any]], source: str = "manual") -> dict[str, Any]:
    """Population counts per card + grade from a source the owner may use. A newer report replaces the older one."""
    out: dict[str, Any] = {"imported": 0, "errors": []}
    for i, r in enumerate(rows):
        try:
            card = db.get(PokemonCard, int(r["card_id"]))
            if card is None:
                raise ValueError("unknown card_id")
            g = normalise(r["grading_company"], r["grade"])
            if not g.graded:
                raise ValueError("population is for graded cards only")
            count = int(r["count"])
            if count < 0:
                raise ValueError("count can't be negative")
            as_of = _parse_dt(r.get("as_of") or utcnow())
            src = str(r.get("source") or source).strip().lower()[:32]
        except (KeyError, ValueError, TypeError, GradeError) as exc:
            out["errors"].append({"row": i + 1, "error": str(exc)})
            continue
        store_population(db, card.id, g, count, src, as_of)
        out["imported"] += 1
    _record_source(db, "manual", out["imported"], None)
    db.commit()
    price_history.invalidate()
    return out


def store_population(db: Session, card_id: int, g: Grade, count: int, source: str, as_of: datetime) -> None:
    """The latest report per source (pokemon_populations) + every report kept over time (population history). No commit."""
    row = db.scalar(select(PokemonPopulation).where(PokemonPopulation.card_id == card_id, PokemonPopulation.grading_company == g.company,
                                                    PokemonPopulation.grade == g.grade, PokemonPopulation.source == source))
    if row is None:
        db.add(PokemonPopulation(card_id=card_id, grading_company=g.company, grade=g.grade, count=count, source=source, as_of=as_of))
        db.flush()  # the session doesn't autoflush: a second report in the same batch must find this row
    elif as_of >= (row.as_of if row.as_of.tzinfo else row.as_of.replace(tzinfo=timezone.utc)):
        row.count, row.as_of = count, as_of
    price_history.record_population(db, card_id, g.company, g.grade, count, source, as_of)


def set_excluded(db: Session, sale_id: int, excluded: bool) -> PokemonSale | None:
    s = db.get(PokemonSale, sale_id)
    if s is not None:
        s.excluded = excluded
        db.commit()
        price_history.rebuild(db, {(s.card_id, s.grading_company, s.grade)})
    return s


# --- snapshots (Price Chat's own history) ------------------------------------------------------------------------------


def snapshot(db: Session, now: datetime | None = None) -> int:
    """Record today's estimate for every card + grade with data. At most one snapshot per combination per ~hour."""
    now = now or utcnow()
    # the newest snapshot per combination only (the table keeps every day's history: never load all of it)
    latest = {(cid, comp, grade): at for cid, comp, grade, at in db.execute(
        select(PokemonPriceSnapshot.card_id, PokemonPriceSnapshot.grading_company, PokemonPriceSnapshot.grade, func.max(PokemonPriceSnapshot.taken_at))
        .group_by(PokemonPriceSnapshot.card_id, PokemonPriceSnapshot.grading_company, PokemonPriceSnapshot.grade))}
    n = 0
    own = [cid for cid, x in db.execute(select(PokemonCard.id, PokemonCard.external_ids)) if (x or {}).get("source") != "scrydex-catalog"]
    for key, v in valuations(db, own, now).items():
        last = latest.get(key)
        if last is not None and now - (last if last.tzinfo else last.replace(tzinfo=timezone.utc)) < MIN_SNAPSHOT_GAP:
            continue
        db.add(PokemonPriceSnapshot(card_id=key[0], grading_company=key[1], grade=key[2], market_price_cents=v["market_price"],
                                    population=v["population"], market_cap_cents=v["market_cap"], sales_count=v["sales_30d"],
                                    sales_volume_cents=v["volume_30d"], confidence=v["confidence"], taken_at=now))
        n += 1
    db.commit()
    return n


# --- watchlist -------------------------------------------------------------------------------------------------------


def is_watched(db: Session, card_id: int, owner: str = "admin") -> bool:
    return db.scalar(select(PokemonWatch.id).where(PokemonWatch.owner == owner, PokemonWatch.card_id == card_id)) is not None


def watch(db: Session, card_id: int, on: bool, owner: str = "admin") -> bool:
    row = db.scalar(select(PokemonWatch).where(PokemonWatch.owner == owner, PokemonWatch.card_id == card_id))
    if on and row is None:
        db.add(PokemonWatch(owner=owner, card_id=card_id))
    elif not on and row is not None:
        db.delete(row)
    db.commit()
    price_history.invalidate()
    return on


def watchlist(db: Session, owner: str = "admin", now: datetime | None = None) -> list[dict[str, Any]]:
    ids = list(db.scalars(select(PokemonWatch.card_id).where(PokemonWatch.owner == owner).order_by(PokemonWatch.created_at)))
    vals = valuations(db, ids, now)
    out = []
    for cid in ids:
        card = db.get(PokemonCard, cid)
        if card is None:
            continue
        mine = [v for v in vals.values() if v["card_id"] == cid]
        best = headline(mine)  # the card's default market (PSA 10 first), always labelled
        last = db.scalar(select(PokemonSale).where(PokemonSale.card_id == cid, PokemonSale.excluded.is_(False))
                         .order_by(PokemonSale.sale_date.desc()).limit(1))
        caps = [v["market_cap"] for v in mine if v["market_cap"] is not None]
        out.append({"card": card_out(card), "headline": _row(card, best) if best else None,
                    "latest_sale": sale_out(last) if last else None, "total_market_cap": sum(caps) if caps else None})
    return out


# --- sources ------------------------------------------------------------------------------------------------------


def _state(db: Session, key: str) -> PokemonSourceState:
    st = db.scalar(select(PokemonSourceState).where(PokemonSourceState.key == key))
    if st is None:
        st = PokemonSourceState(key=key, records_imported=0, requests_today=0)
        db.add(st)
        db.flush()
    return st


def _record_source(db: Session, key: str, imported: int, error: str | None) -> None:
    st = _state(db, key)
    now = utcnow()
    st.last_sync_at = now
    if error:
        st.last_error = error[:500]
    if imported or not error:
        st.last_success_at = now
        st.records_imported += imported
    if not error:
        st.last_error = None


def record_source(db: Session, key: str, imported: int, error: str | None) -> None:
    _record_source(db, key, imported, error)
    db.commit()


def record_request(db: Session, key: str, n: int = 1, rate_limit: str | None = None) -> None:
    """Count calls to a provider (shown on Data Sources; daily counts reset at 00:00 UTC)."""
    st = _state(db, key)
    now = utcnow()
    today = now.strftime("%Y-%m-%d")
    if st.requests_day != today:
        st.requests_day, st.requests_today = today, 0
    st.requests_today += n
    st.last_request_at = now
    if rate_limit:
        st.rate_limit = rate_limit[:128]


def sources(db: Session, settings: Any) -> list[dict[str, Any]]:
    from app.providers.pokemon import PROVIDERS
    from app.security.credentials import env_name

    states = {s.key: s for s in db.scalars(select(PokemonSourceState))}
    out = []
    for key, cls in PROVIDERS.items():
        p = cls(settings)
        connected, why = p.status()
        st = states.get(key)
        missing = [env_name(c) for c in p.missing_credentials()] if p.info.credentials else []
        out.append({"key": key, "name": p.info.name, "connected": connected, "reason": why, "serves": list(p.info.serves),
                    "access": p.info.access, "licence": p.info.licence, "credentials": [env_name(c) for c in p.info.credentials],
                    "missing": missing, "credentials_present": (not missing) if p.info.credentials else None,
                    "rate_limit": (st.rate_limit if st and st.rate_limit else p.info.rate_limit) or None,
                    "notes": list(p.notes()),
                    "last_request_at": st.last_request_at.isoformat() if st and st.last_request_at else None,
                    "requests_today": st.requests_today if st and st.requests_day == utcnow().strftime("%Y-%m-%d") else 0,
                    "last_sync_at": st.last_sync_at.isoformat() if st and st.last_sync_at else None,
                    "last_success_at": st.last_success_at.isoformat() if st and st.last_success_at else None,
                    "records_imported": st.records_imported if st else 0, "last_error": st.last_error if st else None})
    return out


# --- marketplace observations: active listings (asking prices) and provider-reported sold items --------------------------


def listing_out(x: PokemonListing) -> dict[str, Any]:
    return {"id": x.id, "provider": x.provider, "kind": x.kind, "listing_id": x.listing_id, "title": x.title, "price": x.price_cents,
            "currency": x.currency, "condition": x.condition, "seller": x.seller, "listing_type": x.listing_type, "url": x.url,
            "image_url": x.image_url, "observed_at": x.observed_at.isoformat(), "sold_at": x.sold_at.isoformat() if x.sold_at else None,
            "card_id": x.card_id, "grade": Grade(x.grading_company, x.grade).label if x.grading_company and x.grade else None,
            "match_status": x.match_status, "match_confidence": x.match_confidence, "match_reasons": x.match_reasons}


def reference_out(x: PokemonReferencePrice) -> dict[str, Any]:
    return {"provider": x.provider, "provider_grade": x.provider_grade, "grade": Grade(x.grading_company, x.grade).label if x.grading_company and x.grade else None,
            "price": x.price_cents, "currency": x.currency, "as_of": x.as_of.isoformat()}


def store_observations(db: Session, provider: str, kind: str, items: list[Any], fx: Any = None, cards: list[PokemonCard] | None = None) -> dict[str, int]:
    """Store listings / sold items from a provider. Each title goes through the matcher:
    matched -> active listings are kept as listings (NEVER sales); sold items become verified sales (source-reported);
    review -> REVIEW NEEDED queue (nothing imported); rejected -> kept for audit, ignored."""
    from app.pokemon import matching

    if cards is None:  # callers searching for one card pass that Pokémon's cards: matching 50k cards per title is wasteful
        cards = list(db.scalars(select(PokemonCard)))
    out = {"seen": 0, "new": 0, "matched": 0, "review": 0, "rejected": 0, "sales": 0}
    touched: set[tuple[int, str, str]] = set()
    for it in items:
        out["seen"] += 1
        row = db.scalar(select(PokemonListing).where(PokemonListing.provider == provider, PokemonListing.kind == kind,
                                                     PokemonListing.listing_id == it.listing_id))
        if row is not None:
            if kind == "active":
                row.price_cents, row.observed_at = it.price_cents, utcnow()  # an asking price can change; a sale never does
            continue
        m = matching.match(it.title, cards)
        row = PokemonListing(provider=provider, kind=kind, listing_id=it.listing_id, title=it.title[:1000], price_cents=it.price_cents,
                             currency=it.currency, condition=getattr(it, "condition", None), seller=getattr(it, "seller", None),
                             listing_type=getattr(it, "listing_type", None), url=it.url, image_url=getattr(it, "image_url", None),
                             observed_at=utcnow(), sold_at=getattr(it, "sold_at", None), card_id=m.card_id if m.status != "rejected" else None,
                             grading_company=m.grade.company if m.grade else None, grade=m.grade.grade if m.grade else None,
                             match_status=m.status, match_confidence=m.confidence, match_reasons=m.reasons[:12])
        db.add(row)
        out["new"] += 1
        out[m.status] += 1
        if kind == "sold" and m.status == "matched" and m.grade and row.sold_at and _sale_from_listing(db, row, verified_by="source", fx=fx):
            out["sales"] += 1
            touched.add((row.card_id, row.grading_company, row.grade))
    db.commit()
    if touched:
        price_history.rebuild(db, touched)  # new real sales: the history and today's estimates update automatically
    return out


def _sale_from_listing(db: Session, x: PokemonListing, verified_by: str, fx: Any = None) -> bool:
    """A provider-reported SOLD item (matched, or resolved by an admin) becomes one sale. Active listings never do."""
    if x.kind != "sold" or not (x.card_id and x.grading_company and x.grade and x.sold_at):
        return False
    rate: float | None = 1.0
    if x.currency != "USD":
        from app.pokemon.fx import FxError

        try:
            rate = fx.usd_per_unit(x.currency, x.sold_at.date())[0] if fx is not None else None
        except FxError:
            rate = None
    if rate is None:
        x.match_reasons = [*(x.match_reasons or []), f"no USD rate for {x.currency}"]
        x.match_status = "review"
        return False
    g = Grade(x.grading_company, x.grade)
    fp = fingerprint(x.provider, x.listing_id, x.card_id, g, x.price_cents, x.sold_at)
    if db.scalar(select(PokemonSale.id).where(PokemonSale.fingerprint == fp)):
        x.match_status = "imported"
        return False
    db.add(PokemonSale(card_id=x.card_id, grading_company=g.company, grade=g.grade, sale_price_cents=x.price_cents, currency=x.currency,
                       sale_price_usd_cents=round(x.price_cents * rate), fx_rate=rate, fx_source="usd" if x.currency == "USD" else "ecb",
                       sale_date=x.sold_at, source=x.provider, source_listing_id=x.listing_id, url=x.url,
                       sale_type=x.listing_type, verified=True, verified_by=verified_by, fingerprint=fp, title=x.title,
                       match_confidence=x.match_confidence))
    x.match_status = "imported"
    return True


def review_queue(db: Session, limit: int = 200) -> list[dict[str, Any]]:
    """REVIEW NEEDED: observations whose card or grade couldn't be matched safely."""
    rows = db.scalars(select(PokemonListing).where(PokemonListing.match_status == "review").order_by(PokemonListing.observed_at.desc()).limit(limit))
    return [listing_out(x) for x in rows]


def resolve_review(db: Session, listing_id: int, card_id: int | None, grade_label: str | None, reject: bool = False) -> dict[str, Any]:
    """An admin decides: the right card + grade (a sold item then becomes a verified sale), or reject."""
    from app.pokemon.grades import parse_label

    x = db.get(PokemonListing, listing_id)
    if x is None or x.match_status != "review":
        raise ValueError("Nothing to review there.")
    if reject:
        x.match_status = "rejected"
        x.match_reasons = [*(x.match_reasons or []), "rejected by an admin"]
        db.commit()
        return listing_out(x)
    if card_id is None or db.get(PokemonCard, card_id) is None or not grade_label:
        raise ValueError("Choose the card and the grade.")
    g = parse_label(grade_label)
    x.card_id, x.grading_company, x.grade = card_id, g.company, g.grade
    x.match_status, x.match_reasons = "matched", [*(x.match_reasons or []), "resolved by an admin"]
    added = x.kind == "sold" and _sale_from_listing(db, x, verified_by="manual")
    db.commit()
    if added:
        price_history.rebuild(db, {(x.card_id, x.grading_company, x.grade)})
    return listing_out(x)


def store_reference_prices(db: Session, provider: str, card_id: int, product_id: str | None, prices: list[tuple[str, Grade | None, int]]) -> int:
    """Reference prices (e.g. PriceCharting). Kept with the provider's own grade label; one row per day; never priced as sales."""
    today = utcnow().date()
    n = 0
    for label, g, cents in prices:
        row = db.scalar(select(PokemonReferencePrice).where(PokemonReferencePrice.provider == provider, PokemonReferencePrice.card_id == card_id,
                                                            PokemonReferencePrice.provider_grade == label, PokemonReferencePrice.as_of == today))
        if row is None:
            row = PokemonReferencePrice(provider=provider, card_id=card_id, provider_grade=label, as_of=today, currency="USD", price_cents=cents)
            db.add(row)
            n += 1
        row.price_cents, row.provider_product_id = cents, product_id
        row.grading_company, row.grade = (g.company, g.grade) if g else (None, None)
    db.commit()
    return n


# --- catalog enrichment (Pokémon TCG API: identity + images only) ----------------------------------------------------------


async def fetch_images(db: Session, settings: Any, transport: Any = None, force: bool = False) -> dict[str, Any]:
    """Fill official card images from the Pokémon TCG API for cards with a set id and number. Never prices."""
    from app.providers.pokemon import Unavailable, provider

    api = provider("pokemontcg", settings, transport)
    out: dict[str, Any] = {"updated": 0, "not_found": [], "skipped": 0, "error": None}
    for card in db.scalars(select(PokemonCard).order_by(PokemonCard.id)):
        if not card.set_code or not card.card_number or (card.image_url and not force):
            out["skipped"] += 1
            continue
        number = card.card_number.split("/")[0]
        # the API's ids are "<set id>-<number>": one direct lookup, checked against set and number; search as fallback
        try:
            hits = [await api.get_card(f"{card.set_code}-{number}")]
        except Unavailable as exc:
            if exc.code in ("rate_limited", "unreachable"):
                out["error"] = exc.reason
                break  # the service is limiting or away: stop, try again later
            try:
                hits = await api.search_cards(f"set.id:{card.set_code} number:{number}")
            except Unavailable as exc2:
                if exc2.code in ("rate_limited", "unreachable"):
                    out["error"] = exc2.reason
                    break
                out["error"] = exc2.reason  # noted; this card is just "not found" this time
                hits = []
        hit = next((h for h in hits if h.set_code == card.set_code and (h.number or "").split("/")[0] == number), None)
        if hit is None or not hit.image_url:
            out["not_found"].append(card.id)
            continue
        card.image_url, card.image_source = hit.image_url, "pokemontcg"
        card.external_ids = {**(card.external_ids or {}), "pokemontcg": hit.provider_id}
        card.search_text = search_text(card)
        out["updated"] += 1
    _record_source(db, "pokemontcg", out["updated"], out["error"])
    db.commit()
    price_history.invalidate()
    return out
