"""CoinGecko-style market views over the price history engine: the ranked market table (one grade, PSA 10 by default),
sets and grades overviews, recent sales, the card chart payload and series comparisons.

Performance: every view loads its data in a fixed number of batched queries (cards, sales of that grade, valuation points
of that grade, populations, watch counts), never one query per row, and responses are cached until the data changes
(history.cached / history.invalidate) or for at most history.CACHE_TTL seconds.

Honesty: every number comes from stored sales, valuation points and population reports. A market-cap change is computed
only over cards whose past estimate AND past population are both on record; otherwise it is None ("—").
"""

from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, defer

from app.db.base import utcnow
from app.models.pokemon import PokemonCard, PokemonPopulation, PokemonPopulationHistory, PokemonSale, PokemonWatch
from app.pokemon import catalog, history, movement, pricing, provider_prices
from app.pokemon.grades import Grade

DEFAULT_GRADE = Grade("PSA", "10")
MAX_LIMIT = 250
SORTS = ("rank", "name", "price", "change_30d", "change_90d", "volume", "market_cap", "population", "sales")
GRADE_LIST = (Grade("PSA", "10"), Grade("PSA", "9"), Grade("PSA", "8"), Grade("PSA", "7"), Grade("BGS", "10"), Grade("BGS", "9.5"),
              Grade("CGC", "10"), Grade("RAW", "NM"))
CAP_PERIODS = ("30d", "90d")


# SQLite allows ~32k parameters per statement: for (nearly) the whole catalogue, load without an id list (extra rows are ignored)
BIG_IN = 5000


def _ids(card_ids: list[int] | None) -> list[int] | None:
    return None if card_ids is None or len(card_ids) > BIG_IN else card_ids


def _populations(db: Session, card_ids: list[int] | None, g: Grade) -> dict[int, tuple[int, str, datetime]]:
    """Latest reported population per card at this grade (largest count wins when several sources report the same day)."""
    out: dict[int, tuple[int, str, datetime]] = {}
    w = [PokemonPopulation.grading_company == g.company, PokemonPopulation.grade == g.grade]
    if _ids(card_ids) is not None:
        w.append(PokemonPopulation.card_id.in_(card_ids))
    for p in db.scalars(select(PokemonPopulation).where(*w).order_by(PokemonPopulation.as_of)):
        prev = out.get(p.card_id)
        if prev is None or p.as_of > prev[2] or (p.as_of == prev[2] and p.count > prev[0]):
            out[p.card_id] = (p.count, p.source, p.as_of)
    return out


def _population_history(db: Session, card_ids: list[int] | None, g: Grade) -> dict[int, list[tuple[date, int]]]:
    """Every population report per card at this grade, by day (largest count wins per day). One query."""
    by: dict[int, dict[date, int]] = defaultdict(dict)
    w = [PokemonPopulationHistory.grading_company == g.company, PokemonPopulationHistory.grade == g.grade]
    if _ids(card_ids) is not None:
        w.append(PokemonPopulationHistory.card_id.in_(card_ids))
    for r in db.scalars(select(PokemonPopulationHistory).where(*w)):
        if r.count > by[r.card_id].get(r.day, -1):
            by[r.card_id][r.day] = r.count
    return {cid: sorted(days.items()) for cid, days in by.items()}


def _pop_at(hist: list[tuple[date, int]], when: datetime) -> int | None:
    d = when.date()
    known = [c for day_, c in hist if day_ <= d]
    return known[-1] if known else None


def _watchers(db: Session, card_ids: list[int] | None = None) -> dict[int, int]:
    stmt = select(PokemonWatch.card_id, func.count()).group_by(PokemonWatch.card_id)
    if _ids(card_ids) is not None:
        stmt = stmt.where(PokemonWatch.card_id.in_(card_ids))
    return dict(db.execute(stmt).all())  # type: ignore[arg-type]


def _cards(db: Session, era: str | None, q: str, f: dict[str, Any] | None = None, ids: list[int] | None = None) -> list[PokemonCard]:
    f = f or {}
    if q.strip():
        cards, _ = catalog.search(db, q, limit=100_000)
    else:
        cards = list(db.scalars(select(PokemonCard)))

    def ok(c: PokemonCard) -> bool:
        if ids is not None and c.id not in ids:
            return False
        if era and c.era != era:
            return False
        if f.get("set") and str(f["set"]).lower() not in (c.set_name.lower(), (c.set_code or "").lower()):
            return False
        if f.get("year") and c.year != int(f["year"]):
            return False
        if f.get("edition") and c.edition.lower() != str(f["edition"]).lower():
            return False
        if f.get("language") and c.language.lower() != str(f["language"]).lower():
            return False
        return True
    return [c for c in cards if ok(c)]


def market_rows(db: Session, g: Grade, cards: list[PokemonCard], now: datetime, spark_days: int = movement.SPARK_DAYS,
                with_cap_history: bool = False) -> list[dict[str, Any]]:
    """One row per card at grade g. Batched: a fixed number of queries whatever the number of cards.
    Price precedence: verified-sales estimate -> Scrydex valuation -> PriceCharting (backup, no history). Movements and the
    sparkline follow movement.py (30D / 90D / 180D / 1Y, genuine observations only). Market-cap changes are derived from
    the rows' own movements (_cap_change); `with_cap_history` is accepted for older callers."""
    from app.pokemon.service import card_out

    ids = _ids([c.id for c in cards])
    sales = history.load_sales(db, ids, g.company, g.grade)
    points = history.load_points(db, ids, g.company, g.grade)
    pops = _populations(db, ids, g)
    watch = _watchers(db, ids)
    prov = provider_prices.latest(db, ids, g.company, g.grade)
    since = (now - timedelta(days=max(spark_days, max(movement.PERIODS.values())) + movement.MAX_BASE_GAP)).date()
    obs = provider_prices.observations(db, ids, g.company, g.grade, since)
    refs = provider_prices.references(db, ids, g.company, g.grade)
    rows = []
    for c in cards:
        key = (c.id, g.company, g.grade)
        pts = points.get(key, [])
        card_sales = sales.get(key, [])
        st = pricing.compute(card_sales, now)
        pop = pops.get(c.id)
        pv, ref = prov.get(key), refs.get(key)
        price, source, conf, conf_why = st.market_price, ("sales" if st.market_price is not None else None), st.confidence, st.confidence_reasons
        if price is None and pv is not None:
            price, source, conf = pv.market_cents, "scrydex", None
            conf_why = [f"Scrydex market estimate as of {pv.day.isoformat()} (provider valuation, not a verified sale)"]
        if price is None and ref is not None:
            price, source, conf = ref.price_cents, "pricecharting", None
            conf_why = [f"PriceCharting value as of {ref.as_of.isoformat()} (reference price, not a verified sale)"]
        series = obs.get(key, [])
        mv = movement.movements(source, pts, price, pv.trends if pv else None, series, pv.market_cents if pv else None, now)
        spark, spark_why = movement.sparkline(source, pts, price, series, now, spark_days)
        rows.append({
            "card": card_out(c, light=True), "grading_company": g.company, "grade": g.grade, "label": g.label,
            "market_price": price, "confidence": conf, "confidence_reasons": conf_why,
            **{f"change_{k}": v["value"] for k, v in mv.items()},
            "change_sources": {k: v["source"] for k, v in mv.items() if v["source"]},
            "change_reasons": {k: v["reason"] for k, v in mv.items() if v["reason"]},
            "sales_30d": st.sales_30d, "volume_30d": st.volume_30d, "sales_7d": st.sales_7d,
            "last_sale": st.last_sale, "last_sale_at": st.last_sale_at.isoformat() if st.last_sale_at else None,
            "population": pop[0] if pop else None, "population_source": pop[1] if pop else None,
            "population_as_of": pop[2].isoformat() if pop else None,
            "market_cap": pricing.market_cap(price, pop[0] if pop else None, g.graded),
            "watchers": watch.get(c.id, 0),
            "sparkline": spark, "sparkline_note": spark_why,
            "price_source": source, "provider": provider_prices.provider_out(pv),
            "reference": provider_prices.reference_out(ref, None if source == "pricecharting" else price),
        })
    return rows


BASE_TTL = 900.0  # seconds: the whole-catalogue rows per grade; any sync / import invalidates them at once
BASE_REFRESH = 480.0  # the warm-up job (every 600 s) rebuilds rows older than this, well before BASE_TTL: no visitor waits
BASE_STALE = BASE_TTL  # PSA 10 rows past BASE_TTL (warm-up late or off) are still served this long while one background rebuild runs


def base_rows(db: Session, g: Grade, now: datetime | None = None, spark_days: int = movement.SPARK_DAYS, refresh: bool = False) -> list[dict[str, Any]]:
    """Every catalog card's row at grade g (prices, moves, cap history), ranked. Computed once and shared by the market,
    movers, trending, sets, years and grades views; callers filter copies and never mutate these rows.
    refresh (the warm-up job): rebuild rows older than BASE_REFRESH while visitors keep getting the current ones."""
    now = now or utcnow()

    def build() -> list[dict[str, Any]]:
        # a short-lived session of its own: the ~50k card / valuation objects are freed as soon as the rows are built
        # (in the caller's session they would stay in memory until the request ends; a 512 MB server can't afford that)
        with Session(bind=db.get_bind()) as own:
            cards = list(own.scalars(select(PokemonCard).options(defer(PokemonCard.search_text), defer(PokemonCard.external_ids))))
            return _rank(market_rows(own, g, cards, now))

    # memory: keep the homepage grade (PSA 10) plus at most ONE other grade's rows (~200 MB each on 50k cards)
    history.evict(lambda k: isinstance(k, tuple) and len(k) == 2 and k[0] == "base" and k[1] not in (g.label, DEFAULT_GRADE.label))
    keep = _keep_stale()
    if refresh:
        return history.refresh(("base", g.label), build, ttl=BASE_TTL, older_than=BASE_REFRESH, keep_stale=keep)
    # PSA 10 (the homepage): an expired copy is served while it rebuilds in the background (its build has its own session).
    # Other grades are built on demand and evicted soon after: no stale copy kept for them
    return history.cached(("base", g.label), build, ttl=BASE_TTL, stale=BASE_STALE if g == DEFAULT_GRADE and keep else 0.0)


def _keep_stale() -> bool:
    from app.config.settings import get_settings

    return bool(get_settings().pokemon_market_keep_stale)


def warm(db: Session, now: datetime | None = None, settings: Any = None) -> int:
    """The pokemon.warm job (refresh-ahead): rebuild the homepage's shared 15-minute values (PSA 10 rows, the light
    catalogue, X mentions, data quality) before they expire, while visitors keep getting the current ones; then the
    homepage's default view. Returns the number of PSA 10 rows."""
    from app.pokemon import top100

    now = now or utcnow()
    rows = base_rows(db, DEFAULT_GRADE, now, refresh=True)
    catalog.lite(db, refresh=True)
    x_mentions(db, now, refresh=True)
    top100.assess(db, now, settings, refresh=True)
    market(db, DEFAULT_GRADE, None, "", 100, 0, now, settings=settings, view="top100")  # the homepage's default view
    return len(rows)


def row_ok(card: dict[str, Any], era: str | None, f: dict[str, Any]) -> bool:
    """The same filters as _cards, on a row's card."""
    if era and card["era"] != era:
        return False
    if f.get("set") and str(f["set"]).lower() not in (card["set_name"].lower(), (card["set_code"] or "").lower()):
        return False
    if f.get("year") and card["year"] != int(f["year"]):
        return False
    if f.get("edition") and card["edition"].lower() != str(f["edition"]).lower():
        return False
    if f.get("language") and card["language"].lower() != str(f["language"]).lower():
        return False
    return True


SMALL_IDS = 50  # this many cards (or fewer) asked for by id are computed directly, never via the whole catalogue


def rows_for(db: Session, g: Grade, now: datetime, era: str | None = None, q: str = "", f: dict[str, Any] | None = None,
             ids: list[int] | None = None, spark_days: int = movement.SPARK_DAYS) -> list[dict[str, Any]]:
    """The rows of a view, ranked within it. Unfiltered: the cached rows THEMSELVES (already ranked; callers never mutate
    them). Filtered: copies of the matching rows only, re-ranked. A few cards by id (the table's per-row grade switch, a
    card at another grade): computed for those cards alone, so one click never builds 50,000 rows at another grade."""
    f = f or {}
    if ids is not None and len(ids) <= SMALL_IDS and not q.strip():
        cards = [c for c in db.scalars(select(PokemonCard).where(PokemonCard.id.in_(ids or [0]))) if row_ok(_card_dict(c), era, f)]
        rows = market_rows(db, g, cards, now, spark_days)
        for r in rows:
            r.pop("rank", None)  # a rank only means something within the whole catalogue
        return rows
    base = base_rows(db, g, now)
    if era is None and not f and not q.strip() and ids is None:
        return base
    allowed = {c.id for c in _cards(db, era, q, f, ids)} if (q.strip() or ids is not None) else None
    return _rank([dict(r) for r in base if row_ok(r["card"], era, f) and (allowed is None or r["card"]["id"] in allowed)])


def _card_dict(c: PokemonCard) -> dict[str, Any]:
    return {"era": c.era, "set_name": c.set_name, "set_code": c.set_code, "year": c.year, "edition": c.edition, "language": c.language}


def _rank(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Market cap first (CoinGecko order); cards without a cap follow by price; cards with no data keep catalog order."""
    capped = sorted((r for r in rows if r["market_cap"] is not None), key=lambda r: -r["market_cap"])
    priced = sorted((r for r in rows if r["market_cap"] is None and r["market_price"] is not None), key=lambda r: -r["market_price"])
    rest = sorted((r for r in rows if r["market_price"] is None),
                  key=lambda r: (-(r["population"] or 0), r["card"]["year"], r["card"]["set_name"], r["card"]["name"], r["card"]["id"]))
    for i, r in enumerate(capped):
        r["rank"] = i + 1
    return capped + priced + rest


_SORT_VALUE = {
    "price": lambda r: r["market_price"], "change_30d": lambda r: r["change_30d"], "change_90d": lambda r: r["change_90d"],
    "volume": lambda r: r["volume_30d"] or None, "market_cap": lambda r: r["market_cap"],
    "population": lambda r: r["population"], "sales": lambda r: r["sales_30d"] or None,
}


def sort_rows(rows: list[dict[str, Any]], sort: str, desc: bool) -> list[dict[str, Any]]:
    """`rows` arrive in rank order. Unknown values ("—") always sink to the bottom, whichever the direction."""
    if sort == "rank":  # ascending = #1 first (the default); descending = lowest ranked first, unranked still last
        ranked = [r for r in rows if r.get("rank")]
        return (ranked[::-1] + [r for r in rows if not r.get("rank")]) if desc else rows
    if sort == "name":
        return sorted(rows, key=lambda r: (r["card"]["name"].lower(), r["card"]["year"]), reverse=desc)
    get = _SORT_VALUE[sort]
    known = [r for r in rows if get(r) is not None]
    unknown = [r for r in rows if get(r) is None]
    return sorted(known, key=get, reverse=desc) + unknown


def _cap_change(rows: list[dict[str, Any]], period: str) -> float | None:
    """Price-driven market-cap change over `period`: today's cap vs each card's cap at its price at the period start (from
    the card's own movement; population held at today's count), over the cards that HAVE a movement for the period.
    None when none do. _cap_coverage says how many cards that is."""
    cur = past = 0.0
    for r in rows:
        ch = r.get(f"change_{period}")
        if r["market_cap"] is not None and isinstance(ch, (int, float)) and ch > -100:
            cur += r["market_cap"]
            past += r["market_cap"] / (1 + ch / 100)
    return pricing.change(round(cur), round(past)) if past else None


def _cap_coverage(rows: list[dict[str, Any]], period: str) -> int:
    return sum(1 for r in rows if r["market_cap"] is not None and isinstance(r.get(f"change_{period}"), (int, float)))


def filter_options(db: Session) -> dict[str, list[Any]]:
    cards = catalog.lite(db)
    sets: dict[str, tuple[int, str]] = {}
    for c in cards:
        sets[c.set_name] = min(sets.get(c.set_name, (c.year, c.language)), (c.year, c.language))
    return {"sets": [s for s, _ in sorted(sets.items(), key=lambda kv: (kv[1], kv[0]))], "years": sorted({c.year for c in cards}),
            "editions": sorted({c.edition for c in cards if c.edition != "—"}), "languages": sorted({c.language for c in cards}),
            "grades": [g.label for g in GRADE_LIST]}


def _public(r: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in r.items() if not k.startswith("_")}


def market(db: Session, g: Grade = DEFAULT_GRADE, era: str | None = "vintage", q: str = "", limit: int = 100, offset: int = 0,
           now: datetime | None = None, settings: Any = None, sort: str = "rank", desc: bool = False, filters: dict[str, Any] | None = None,
           ids: list[int] | None = None, spark_days: int = movement.SPARK_DAYS, view: str = "all") -> dict[str, Any]:
    """view "top100": only cards that pass every PSA 10 data-quality check (top100.py), vintage, ranked by market cap,
    at most 100. view "all": every card. Each row carries its Data Confidence (quality) either way."""
    now = now or utcnow()
    limit = max(1, min(limit, MAX_LIMIT))
    sort = sort if sort in SORTS else "rank"
    f = {k: v for k, v in (filters or {}).items() if v not in (None, "")}

    def build() -> dict[str, Any]:
        from app.pokemon.service import card_out, sale_out

        ranked = rows_for(db, g, now, era, q, f, ids, spark_days)
        scope = ranked  # every card in the filter: the hero's volumes and counts cover all of them, not just the Top 100
        from app.pokemon import top100

        quality = top100.assess(db, now, settings)
        t100 = top100.top100(db, now, f.get("language"), settings)["summary"]
        if view == "top100":
            ok = top100.eligible_ids(db, now)
            ranked = _rank([dict(r) for r in ranked if r["card"]["id"] in ok and r["card"]["era"] == "vintage"])[: top100.LIMIT]
        rows = sort_rows(ranked, sort, desc)
        page = [{**_public(r), "quality": top100.brief(quality.get(r["card"]["id"]))} for r in rows[offset: offset + limit]]
        priced = [r for r in ranked if r["market_price"] is not None]
        # gainers / losers: only moves that passed validation (a +36,000% Scrydex trend on a thin card is not a gainer)
        with30 = [r for r in priced if r["change_30d"] is not None and "moves" not in (quality.get(r["card"]["id"]) or {}).get("failed", [])]
        card_ids = [r["card"]["id"] for r in scope]
        in_view = set(card_ids)  # sales are few: filtered here, not with a 50k-id IN clause
        recent = []  # newest first, read in small batches until 10 are in view (never every sale ever recorded)
        result = db.execute(select(PokemonSale, PokemonCard).join(PokemonCard, PokemonCard.id == PokemonSale.card_id)
                            .where(PokemonSale.verified.is_(True), PokemonSale.excluded.is_(False))
                            .order_by(PokemonSale.sale_date.desc()).execution_options(yield_per=200))
        try:
            for s, s_card in result:
                if s.card_id in in_view:
                    recent.append(sale_out(s, s_card))
                    if len(recent) == 10:
                        break
        finally:
            result.close()
        by_id = {r["card"]["id"]: r for r in ranked}
        watched = sorted(((cid, n) for cid, n in _watchers(db, card_ids).items() if n > 0), key=lambda x: (-x[1], x[0]))[:5]
        capped = [r for r in ranked if r["market_cap"] is not None]
        with_pop = [r for r in ranked if r["population"] is not None]
        sales_30d = sum(1 for cid in db.scalars(select(PokemonSale.card_id).where(
            PokemonSale.verified.is_(True), PokemonSale.excluded.is_(False), PokemonSale.sale_date >= now - timedelta(days=30))) if cid in in_view)
        first_sale = db.scalar(select(func.min(PokemonSale.sale_date)).where(PokemonSale.verified.is_(True), PokemonSale.excluded.is_(False),
                                                                             PokemonSale.grading_company == g.company, PokemonSale.grade == g.grade))
        first_sale = history.as_utc(first_sale) if first_sale else None
        trending = trend_rank(db, ranked, now)
        return {
            "grade": g.label, "era": era, "query": q, "sort": sort, "desc": desc, "filters": f, "as_of": now.isoformat(), "view": view, "top100": t100,
            "total": len(rows), "offset": offset, "limit": limit, "rows": page,
            "hero": {
                "total_market_cap": sum(r["market_cap"] for r in capped) if capped else None,
                **{f"market_cap_change_{k}": _cap_change(capped, k) for k in CAP_PERIODS},
                **{f"market_cap_change_{k}_cards": _cap_coverage(capped, k) for k in CAP_PERIODS},
                "cards_with_cap": len(capped), "cards_priced": len(priced), "cards_tracked": len(rows),
                # verified trading volume of every card in scope (completed sales only; never asking prices or estimates)
                "volume_30d": sum(r["volume_30d"] for r in scope), "grade_sales_30d": sum(r["sales_30d"] for r in scope),
                "sales_records_from": first_sale.isoformat() if first_sale else None,
                "all_sales_30d": sales_30d,
                "population_total": sum(r["population"] for r in with_pop) if with_pop else None,
                "cards_with_population": len(with_pop),
                "lowest_population": _public(min(with_pop, key=lambda r: r["population"])) if with_pop else None,
            },
            "sections": {
                "trending": [_public(r) for r in trending[:5]],
                "gainers": [_public(r) for r in sorted((r for r in with30 if r["change_30d"] > 0), key=lambda r: -r["change_30d"])[:5]],
                "losers": [_public(r) for r in sorted((r for r in with30 if r["change_30d"] < 0), key=lambda r: r["change_30d"])[:5]],
                "recently_sold": recent,
                "most_watched": [{**_public(by_id[cid]), "watchers": n} if cid in by_id else {"card": card_out(db.get(PokemonCard, cid)), "watchers": n}
                                 for cid, n in watched],
            },
            "options": filter_options(db),
            "providers": provider_summary(db, settings),
            "has_market_data": bool(priced) or bool(with_pop),
        }

    key = ("market", view, g.label, era, q.strip().lower(), limit, offset, sort, desc, tuple(sorted(f.items())), tuple(ids or ()), spark_days,
           now.replace(second=0, microsecond=0).isoformat())
    return history.cached(key, build)


def movers(db: Session, g: Grade, direction: str, period: str = "30d", limit: int = 100, now: datetime | None = None) -> dict[str, Any]:
    """Top gainers / losers at one grade over 30D / 90D. Only cards with a real change for that period."""
    now = now or utcnow()
    field = f"change_{period}"

    def build() -> dict[str, Any]:
        from app.pokemon import top100

        q = top100.assess(db, now)
        rows = [r for r in rows_for(db, g, now) if g != top100.GRADE or "moves" not in (q.get(r["card"]["id"]) or {}).get("failed", [])]
        moved = [r for r in rows if r.get(field) is not None and (r[field] > 0 if direction == "gainers" else r[field] < 0)]
        moved.sort(key=lambda r: r[field], reverse=direction == "gainers")
        return {"grade": g.label, "period": period, "direction": direction, "rows": [_public(r) for r in moved[:limit]],
                "cards_with_change": sum(1 for r in rows if r.get(field) is not None), "as_of": now.isoformat()}

    return history.cached(("movers", g.label, direction, period, limit, now.replace(second=0, microsecond=0).isoformat()), build)


TREND_BASIS = "X mentions of the Pokémon in the last 7 days + size of its 30-day price move (capped at 50%) + verified sales (30 days) and stars"


def x_mentions(db: Session, now: datetime, refresh: bool = False) -> dict[int, int]:
    """X posts of the last 7 days that name each card (the trending score's mentions). refresh: the warm-up job."""
    from app.models.pokemon import PokemonPulsePost
    from app.pokemon import pulse

    def count() -> dict[int, int]:
        cards = [c for c in catalog.lite(db) if pulse.is_pokemon(c)]
        out: dict[int, int] = {}
        for p in db.scalars(select(PokemonPulsePost).where(PokemonPulsePost.created_at_x >= now - timedelta(days=7))):
            for c in pulse.linked_cards(p.text, cards):
                out[c.id] = out.get(c.id, 0) + 1
        return out

    if refresh:
        return history.refresh(("x-mentions",), count, ttl=BASE_TTL, older_than=BASE_REFRESH)
    return history.cached(("x-mentions",), count, ttl=BASE_TTL)


def trend_rank(db: Session, rows: list[dict[str, Any]], now: datetime) -> list[dict[str, Any]]:
    """Score every row, keep the best card per Pokémon. Each kept row gets `trend` = {score, x_mentions, move_30d, why}."""
    import math

    mentions = x_mentions(db, now)
    scored = []
    for r in rows:
        x = mentions.get(r["card"]["id"], 0)
        move = r.get("change_30d")
        mom = min(abs(move), 50.0) if isinstance(move, (int, float)) else 0.0
        score = 3 * math.log1p(x) + mom / 10 + 2 * math.log1p(r.get("sales_30d") or 0) + math.log1p(r.get("watchers") or 0)
        if score <= 0.5:
            continue
        why = [f"𝕏 {x} mention{'s' if x != 1 else ''}"] if x else []
        if move:
            why.append(f"{'▲' if move > 0 else '▼'} {abs(move):.1f}% 30D")
        if r.get("sales_30d"):
            why.append(f"{r['sales_30d']} sales 30D")
        scored.append({**r, "trend": {"score": round(score, 2), "x_mentions": x, "move_30d": move, "why": " · ".join(why)}})
    scored.sort(key=lambda r: -r["trend"]["score"])
    best: dict[str, dict[str, Any]] = {}
    for r in scored:
        best.setdefault(r["card"]["pokemon_name"], r)
    return list(best.values())


def trending(db: Session, g: Grade, limit: int = 50, now: datetime | None = None) -> dict[str, Any]:
    """Trending Pokémon: X mentions + price momentum (+ any verified sales / stars). One card per Pokémon."""
    now = now or utcnow()

    def build() -> dict[str, Any]:
        rows = rows_for(db, g, now)
        hot = trend_rank(db, rows, now)
        return {"grade": g.label, "rows": [_public(r) for r in hot[:limit]], "as_of": now.isoformat(), "basis": TREND_BASIS}

    return history.cached(("trending", g.label, limit, now.replace(second=0, microsecond=0).isoformat()), build)


def sets_overview(db: Session, g: Grade, now: datetime | None = None) -> dict[str, Any]:
    """One row per set (and language): cards tracked, priced, total estimated cap, 30d volume and population at grade g."""
    now = now or utcnow()

    def build() -> dict[str, Any]:
        rows = rows_for(db, g, now)
        groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for r in rows:
            groups[(r["card"]["set_name"], r["card"]["language"])].append(r)
        out = []
        for (name, lang), rs in groups.items():
            capped = [r for r in rs if r["market_cap"] is not None]
            pops = [r["population"] for r in rs if r["population"] is not None]
            cover = next((r["card"] for r in rs if r["card"]["image_url"]), rs[0]["card"])
            out.append({"set_name": name, "set_code": rs[0]["card"]["set_code"], "language": lang, "year": min(r["card"]["year"] for r in rs),
                        "cards": len(rs), "cards_priced": sum(1 for r in rs if r["market_price"] is not None),
                        "market_cap": sum(r["market_cap"] for r in capped) if capped else None,
                        "market_cap_change_30d": _cap_change(capped, "30d"),
                        "volume_30d": sum(r["volume_30d"] for r in rs), "sales_30d": sum(r["sales_30d"] for r in rs),
                        "population": sum(pops) if pops else None, "cover": cover,
                        "top": [_public(r)["card"] for r in rs[:3]]})
        out.sort(key=lambda s: (s["market_cap"] is None, -(s["market_cap"] or 0), s["year"], s["set_name"]))
        for i, s in enumerate(out):
            s["rank"] = i + 1 if s["market_cap"] is not None else None
        return {"grade": g.label, "sets": out, "as_of": now.isoformat()}

    return history.cached(("sets", g.label, now.replace(second=0, microsecond=0).isoformat()), build)


def grades_overview(db: Session, now: datetime | None = None) -> dict[str, Any]:
    """One row per tracked grade: cards priced, total estimated cap, 30d sales / volume, known population.

    Computed with lean per-grade queries instead of 8 x 50k full rows (which do not fit a small server), using the same
    price precedence as the market: verified-sales estimate (engine, for the few cards with sales) -> Scrydex current
    valuation -> PriceCharting exact grade; market cap = price x latest population of that grade."""
    now = now or utcnow()

    def build() -> dict[str, Any]:
        from app.models.pokemon import PokemonProviderValuation

        out = []
        n_cards = db.scalar(select(func.count(PokemonCard.id))) or 0
        for g in GRADE_LIST:
            price: dict[int, int] = {}
            for cid, cents in db.execute(select(PokemonProviderValuation.card_id, PokemonProviderValuation.market_cents).where(
                    PokemonProviderValuation.kind == "current", PokemonProviderValuation.currency == "USD",
                    PokemonProviderValuation.market_cents.is_not(None), PokemonProviderValuation.grading_company == g.company,
                    PokemonProviderValuation.grade == g.grade).order_by(PokemonProviderValuation.day)):
                price[cid] = cents  # latest day wins
            for (cid, comp, gr), ref in provider_prices.references(db, None, g.company, g.grade).items():
                price.setdefault(cid, ref.price_cents)
            sale_ids = set(db.scalars(select(PokemonSale.card_id).where(PokemonSale.verified.is_(True), PokemonSale.excluded.is_(False),
                                                                       PokemonSale.grading_company == g.company, PokemonSale.grade == g.grade).distinct()))
            sales_30d = volume_30d = 0
            if sale_ids:  # the few cards with real sales: the full engine (sales estimate, 30d stats)
                for r in market_rows(db, g, list(db.scalars(select(PokemonCard).where(PokemonCard.id.in_(list(sale_ids))))), now):
                    if r["price_source"] == "sales" and r["market_price"] is not None:
                        price[r["card"]["id"]] = r["market_price"]
                    sales_30d += r["sales_30d"]
                    volume_30d += r["volume_30d"]
            pops = {cid: v[0] for cid, v in _populations(db, None, g).items()}
            caps = {cid: pricing.market_cap(p, pops.get(cid), g.graded) for cid, p in price.items()}
            capped = [c for c in caps.values() if c is not None]
            top_id = max(price, key=lambda cid: price[cid], default=None)
            top = None
            if top_id is not None:
                top_rows = market_rows(db, g, [db.get(PokemonCard, top_id)], now)
                top = _public(top_rows[0]) if top_rows else None
            out.append({"grade": g.label, "company": g.company, "graded": g.graded, "cards_priced": len(price),
                        "market_cap": sum(capped) if capped and g.graded else None,
                        "sales_30d": sales_30d, "volume_30d": volume_30d,
                        "population": sum(pops.values()) if pops else None, "cards_with_population": len(pops),
                        "top_card": top})
        return {"grades": out, "cards_tracked": n_cards, "as_of": now.isoformat()}

    return history.cached(("grades",), build, ttl=BASE_TTL)


def recent_sales(db: Session, limit: int = 50, offset: int = 0, g: Grade | None = None) -> dict[str, Any]:
    """Verified, completed sales, newest first (never asking prices)."""
    from app.pokemon.service import sale_out

    where = [PokemonSale.verified.is_(True), PokemonSale.excluded.is_(False)]
    if g is not None:
        where += [PokemonSale.grading_company == g.company, PokemonSale.grade == g.grade]
    total = db.scalar(select(func.count()).select_from(PokemonSale).where(*where)) or 0
    rows = db.execute(select(PokemonSale, PokemonCard).join(PokemonCard, PokemonCard.id == PokemonSale.card_id).where(*where)
                      .order_by(PokemonSale.sale_date.desc(), PokemonSale.id.desc()).limit(limit).offset(offset)).all()
    return {"sales": [sale_out(s, c) for s, c in rows], "total": total, "limit": limit, "offset": offset, "grade": g.label if g else None}


def provider_summary(db: Session, settings: Any) -> dict[str, Any]:
    """Which sources can feed prices / population right now (for the honest empty state)."""
    if settings is None:
        return {"sales_connected": [], "population_connected": [], "sales_records": 0}
    from app.providers.pokemon import PROVIDERS

    sales_src, pop_src = [], []
    for key, cls in PROVIDERS.items():
        p = cls(settings)
        if key == "manual" or not p.info.credentials:
            continue  # imports are always possible; listed separately
        if p.status()[0]:
            if "get_sales" in p.info.serves:
                sales_src.append(p.info.name)
            if "get_population" in p.info.serves:
                pop_src.append(p.info.name)
    n = db.scalar(select(func.count()).select_from(PokemonSale).where(PokemonSale.verified.is_(True))) or 0
    return {"sales_connected": sales_src, "population_connected": pop_src, "sales_records": n}


# --- card chart --------------------------------------------------------------------------------------------------


def rank_of(db: Session, card: PokemonCard, g: Grade, now: datetime | None = None) -> int | None:
    """The card's market-cap rank at grade g among cards of its era (the homepage ranking). Only for PSA 10 (always
    computed) or a grade whose whole-catalogue rows are already in memory: opening a card at another grade must not
    build 50,000 rows just for a rank pill."""
    if g != DEFAULT_GRADE and not history.is_cached(("base", g.label), BASE_TTL):
        return None
    return era_ranks(db, g, card.era, now or utcnow()).get(card.id)


def era_ranks(db: Session, g: Grade, era: str, now: datetime) -> dict[int, int]:
    """card id -> market-cap rank among the cards of its era (the same ranking as the market's era view), as one lookup
    map: a card page no longer builds a whole market response just for its rank pill."""
    def build() -> dict[int, int]:
        capped = sorted((r for r in base_rows(db, g, now) if (not era or r["card"]["era"] == era) and r["market_cap"] is not None), key=lambda r: -r["market_cap"])
        return {r["card"]["id"]: i + 1 for i, r in enumerate(capped)}

    return history.cached(("ranks", g.label, era), build)


def chart(db: Session, card: PokemonCard, g: Grade, range_key: str, now: datetime | None = None) -> dict[str, Any]:
    """Everything the card page charts for one grade: estimate line (sale days / carried / current), its market-cap line
    (only where a population was on record at that time), sale markers, daily volume, candles only if real sales allow,
    population history, changes with reasons, highs / lows, and the card's rank."""
    now = now or utcnow()
    from app.pokemon.service import sale_out

    def build() -> dict[str, Any]:
        days = history.RANGES.get(range_key, 30)
        since = now - timedelta(days=days) if days else None
        key = (card.id, g.company, g.grade)
        sales = history.load_sales(db, [card.id], g.company, g.grade).get(key, [])
        pts = history.load_points(db, [card.id], g.company, g.grade).get(key, [])
        st = pricing.compute(sales, now)
        pop = _populations(db, [card.id], g).get(card.id)
        pop_hist = _population_history(db, [card.id], g).get(card.id, [])
        ohlc, ohlc_note = history.candles(sales, since, now)
        rows = list(db.scalars(select(PokemonSale).where(PokemonSale.card_id == card.id, PokemonSale.grading_company == g.company,
                                                          PokemonSale.grade == g.grade).order_by(PokemonSale.sale_date)))
        in_range = [s for s in rows if since is None or history.as_utc(s.sale_date) >= since]
        last_verified = next((s for s in reversed(rows) if s.verified and not s.excluded), None)
        # genuine observations only: a "carried" start point would repeat an older estimate at the range start
        line = [p for p in history.series(pts, st.market_price, now, since) if p["kind"] != "carried"]
        if len(line) >= 2 and line[-1]["kind"] == "current" and line[-1]["v"] == line[-2]["v"]:
            line.pop()  # today's estimate is the last sale day's: drawing it again would add a flat tail with no new observation
        pv = provider_prices.latest(db, [card.id], g.company, g.grade).get(key)
        prov_line = provider_prices.line(provider_prices.series(db, [card.id], g.company, g.grade).get(key, []), since)
        ref = provider_prices.references(db, [card.id], g.company, g.grade).get(key)
        price_source = "sales" if st.market_price is not None else ("scrydex" if pv is not None else ("pricecharting" if ref is not None else None))
        shown_price = st.market_price if st.market_price is not None else (pv.market_cents if pv is not None else (ref.price_cents if ref is not None else None))
        cap_line = []
        if g.graded:
            for p in line:
                when = datetime.fromisoformat(p["t"])
                n = _pop_at(pop_hist, when) if pop_hist else None
                if n is None and p.get("kind") == "current" and pop:
                    n = pop[0]  # today's point: the latest population on record
                if n:
                    cap_line.append({"t": p["t"], "v": p["v"] * n, "population": n, "kind": p.get("kind")})
            if not line and prov_line:  # provider valuations x the population on record at each date
                for p in prov_line:
                    n = _pop_at(pop_hist, datetime.fromisoformat(p["t"])) if pop_hist else None
                    if n:
                        cap_line.append({"t": p["t"], "v": p["v"] * n, "population": n, "kind": "provider"})
        return {
            "card_id": card.id, "grade": g.label, "graded": g.graded, "range": range_key, "as_of": now.isoformat(),
            "rank": rank_of(db, card, g, now),
            "summary": {
                "market_price": shown_price, "price_source": price_source, "provider": provider_prices.provider_out(pv),
                "reference": provider_prices.reference_out(ref, None if price_source == "pricecharting" else shown_price),
                "confidence": st.confidence, "confidence_reasons": st.confidence_reasons if price_source == "sales" or price_source is None else [
                    f"Scrydex market estimate as of {pv.day.isoformat()} (provider valuation, not a verified sale)" if price_source == "scrydex" else
                    f"PriceCharting value as of {ref.as_of.isoformat()} (reference price, not a verified sale)"],
                "sales_count": st.sales_count, "window_days": st.window_days,
                "changes": movement.movements(price_source, pts, shown_price, pv.trends if pv else None,
                                              provider_prices.observations(db, [card.id], g.company, g.grade, date(1990, 1, 1)).get(key, []),
                                              pv.market_cents if pv else None, now),
                "population": pop[0] if pop else None, "population_source": pop[1] if pop else None,
                "population_as_of": pop[2].isoformat() if pop else None,
                "market_cap": pricing.market_cap(shown_price, pop[0] if pop else None, g.graded),
                "last_verified_sale": sale_out(last_verified) if last_verified else None,
                "sales_30d": st.sales_30d, "volume_30d": st.volume_30d, "median_30d": st.median_30d,
                **history.extremes(sales, pts),
            },
            "line": line,
            "provider_line": prov_line,
            "cap_line": cap_line,
            "sales": [{"t": history.as_utc(s.sale_date).isoformat(), "v": s.sale_price_usd_cents, "id": s.id, "source": s.source,
                       "verified": s.verified, "excluded": s.excluded, "url": s.url, "sale_type": s.sale_type} for s in in_range],
            "volume": history.volume_buckets(sales, since, now),
            "candles": ohlc, "candles_note": ohlc_note,
            "population_history": history.population_series(db, card.id, g.company, g.grade),
        }

    return history.cached(("chart", card.id, g.label, range_key, now.replace(second=0, microsecond=0).isoformat()), build)


def compare(db: Session, specs: list[tuple[PokemonCard, Grade]], range_key: str, now: datetime | None = None) -> dict[str, Any]:
    """Several card + grade estimate lines on one time axis (PSA 10 vs PSA 9, or two different cards)."""
    from app.pokemon.service import card_out

    now = now or utcnow()
    days = history.RANGES.get(range_key, 365)
    since = now - timedelta(days=days) if days else None
    ids = list({c.id for c, _ in specs})
    sales = history.load_sales(db, ids)
    points = history.load_points(db, ids)
    prov = provider_prices.series(db, ids)
    out = []
    for c, g in specs:
        key = (c.id, g.company, g.grade)
        st = pricing.compute(sales.get(key, []), now)
        line = [p for p in history.series(points.get(key, []), st.market_price, now, since) if p["kind"] != "carried"]
        source = "sales" if line else None
        if not line and prov.get(key):  # no verified-sales history: the provider's published valuations, labelled
            line, source = provider_prices.line(prov[key], since), "scrydex"
        out.append({"card": card_out(c), "grade": g.label, "market_price": st.market_price if st.market_price is not None else
                    (prov[key][-1].market_cents if prov.get(key) else None), "confidence": st.confidence, "source": source,
                    "line": line,
                    "sales": [{"t": s.when.isoformat(), "v": s.price_cents} for s in sales.get(key, [])
                              if s.verified and not s.excluded and (since is None or s.when >= since)]})
    return {"range": range_key, "series": out, "as_of": now.isoformat()}


def grades_with_data(db: Session, card_id: int) -> list[str]:
    keys = set(db.execute(select(PokemonSale.grading_company, PokemonSale.grade).where(PokemonSale.card_id == card_id).distinct()).all())
    keys |= set(db.execute(select(PokemonPopulation.grading_company, PokemonPopulation.grade).where(PokemonPopulation.card_id == card_id).distinct()).all())
    return [g.label for g in sorted((Grade(c, gr) for c, gr in keys), key=lambda g: g.sort_key, reverse=True)]


# --- data quality: every source side by side, per exact grade ------------------------------------------------------------

STALE_DAYS = 3  # a provider value older than this is flagged stale


def quality(db: Session, g: Grade, now: datetime | None = None) -> dict[str, Any]:
    """Per card at grade g: Scrydex vs PriceCharting vs verified sales, the gap, coverage and data age. Nothing is averaged
    or corrected: disagreements are shown for an admin to review."""
    from app.pokemon.service import card_out

    now = now or utcnow()
    cards = list(db.scalars(select(PokemonCard).order_by(PokemonCard.id)))
    ids = [c.id for c in cards]
    sx = provider_prices.latest(db, ids, g.company, g.grade)
    pc = provider_prices.references(db, ids, g.company, g.grade)
    sales = history.load_sales(db, ids, g.company, g.grade)
    pops = _populations(db, ids, g)
    rows = []
    for c in cards:
        key = (c.id, g.company, g.grade)
        a = sx.get(key)
        b = pc.get(key)
        st = pricing.compute(sales.get(key, []), now)
        sxv, pcv = (a.market_cents if a else None), (b.price_cents if b else None)
        gap = round((sxv - pcv) / pcv * 100, 1) if sxv and pcv else None
        ages = {"scrydex": (now.date() - a.day).days if a else None, "pricecharting": (now.date() - b.as_of).days if b else None}
        flags = []
        if sxv and pcv and abs(sxv - pcv) / min(sxv, pcv) > provider_prices.DISAGREE:
            flags.append("sources disagree")
        if not sxv and not pcv and st.market_price is None:
            flags.append("no price")
        elif not (sxv and pcv) and st.market_price is None:
            flags.append("single source")
        if any(v is not None and v > STALE_DAYS for v in ages.values()):
            flags.append("stale")
        if g.graded and not pops.get(c.id):
            flags.append("no population")
        rows.append({"card": card_out(c), "scrydex": sxv, "scrydex_range": [a.low_cents, a.high_cents] if a else None, "pricecharting": pcv,
                     "sales_price": st.market_price, "sales_30d": st.sales_30d, "gap_pct": gap, "age_days": ages,
                     "population": pops[c.id][0] if pops.get(c.id) else None, "flags": flags})
    rows.sort(key=lambda r: (-(abs(r["gap_pct"]) if r["gap_pct"] is not None else -1), r["card"]["name"]))
    both = [r for r in rows if r["gap_pct"] is not None]
    ratios = sorted((r["scrydex"] / r["pricecharting"]) for r in both)
    return {"grade": g.label, "as_of": now.isoformat(), "rows": rows, "summary": {
        "cards": len(rows), "both_sources": len(both), "disagree": sum("sources disagree" in r["flags"] for r in rows),
        "within_10": sum(1 for r in both if abs(r["gap_pct"]) <= 10), "within_25": sum(1 for r in both if abs(r["gap_pct"]) <= 25),
        "median_ratio": round(ratios[len(ratios) // 2], 2) if ratios else None, "no_price": sum("no price" in r["flags"] for r in rows),
        "single_source": sum("single source" in r["flags"] for r in rows), "stale": sum("stale" in r["flags"] for r in rows),
        "no_population": sum("no population" in r["flags"] for r in rows), "stale_after_days": STALE_DAYS}}


def years_overview(db: Session, g: Grade, language: str | None = None, now: datetime | None = None) -> dict[str, Any]:
    """Market cap by release year at grade g: cards, priced, cap (and its 30D / 90D change), population, sets, top cards."""
    now = now or utcnow()

    def build() -> dict[str, Any]:
        rows = rows_for(db, g, now, f={"language": language} if language else {})
        groups: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for r in rows:
            groups[r["card"]["year"]].append(r)
        out = []
        for year, rs in groups.items():
            capped = [r for r in rs if r["market_cap"] is not None]
            priced = [r for r in rs if r["market_price"] is not None]
            pops = [r["population"] for r in rs if r["population"] is not None]
            top = sorted(capped, key=lambda r: -r["market_cap"])[:3] or sorted(priced, key=lambda r: -r["market_price"])[:3]
            langs = sorted({r["card"]["language"] for r in rs})
            out.append({"year": year, "cards": len(rs), "cards_priced": len(priced), "cards_with_cap": len(capped),
                        "market_cap": sum(r["market_cap"] for r in capped) if capped else None,
                        "market_cap_change_30d": _cap_change(capped, "30d"), "market_cap_change_90d": _cap_change(capped, "90d"),
                        "population": sum(pops) if pops else None, "sets": len({r["card"]["set_name"] for r in rs}), "languages": langs,
                        "era": "vintage" if year <= 2003 else "modern", "top": [_public(r) for r in top]})
        out.sort(key=lambda y: -y["year"])
        total = sum(y["market_cap"] or 0 for y in out)
        for y in out:
            y["share"] = round((y["market_cap"] or 0) / total * 100, 2) if total else None
        ranked = sorted((y for y in out if y["market_cap"]), key=lambda y: -y["market_cap"])
        for i, y in enumerate(ranked):
            y["rank"] = i + 1
        return {"grade": g.label, "language": language, "years": out, "total_market_cap": total or None, "as_of": now.isoformat()}

    return history.cached(("years", g.label, language, now.replace(second=0, microsecond=0).isoformat()), build)


def warm_homepage(bind: Any) -> None:
    """Build the market homepage (PSA 10 rows, Top 100 view) ahead of the first visitor, with a session of its own."""
    with Session(bind=bind) as db:
        market(db, DEFAULT_GRADE, None, "", 100, 0, view="top100")
