"""Pokémon Vintage Market Cap API (owner dashboard; protected by the admin login like every non-public /api route)."""

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select
from pydantic import Field

from app.api.deps import DbSession, Runtime
from app.api.schemas import StrictInput
from app.models.pokemon import PokemonCard
from app.pokemon import catalog, service
from app.pokemon.grades import GradeError, parse_label

router = APIRouter(tags=["pokemon"])


def _card(db: DbSession, card_id: int) -> PokemonCard:
    catalog.ensure_catalog(db)
    card = db.get(PokemonCard, card_id)
    if card is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Card not found")
    return card


@router.get("/pokemon/overview")
def overview(db: DbSession, era: Literal["vintage", "modern"] | None = None, language: str | None = None, edition: str | None = None,
             promo: bool = False, year: int | None = None, set: str | None = None, pokemon: str | None = None,  # noqa: A002
             grade: str | None = None, company: Literal["PSA", "BGS", "CGC", "RAW"] | None = None) -> dict[str, Any]:
    catalog.ensure_catalog(db)
    return service.overview(db, {"era": era, "language": language, "edition": edition, "promo": promo, "year": year, "set": set,
                                 "pokemon": pokemon, "grade": grade, "company": company})


@router.get("/pokemon/cards")
def search_cards(db: DbSession, q: str = Query("", max_length=120), limit: int = Query(50, ge=1, le=200)) -> dict[str, Any]:
    """'Charizard 1st edition PSA 10': words match the card; a grade in the query narrows the valuations shown."""
    catalog.ensure_catalog(db)
    cards, grade = catalog.search(db, q, limit)
    vals = service.valuations(db, [c.id for c in cards])
    rows = []
    for c in cards:
        mine = [v for v in vals.values() if v["card_id"] == c.id and (grade is None or (v["grading_company"], v["grade"]) == (grade.company, grade.grade))]
        best = max(mine, key=lambda v: (v["market_cap"] or 0, v["market_price"] or 0), default=None)
        rows.append({"card": service.card_out(c), "grade": grade.label if grade else (best["label"] if best else None),
                     "market_price": best["market_price"] if best else None, "market_cap": best["market_cap"] if best else None,
                     "change_30d": best["change_30d"] if best else None})
    return {"query": q, "grade": grade.label if grade else None, "results": rows}


@router.get("/pokemon/cards/{card_id}")
def card(card_id: int, db: DbSession) -> dict[str, Any]:
    return service.card_detail(db, _card(db, card_id))


@router.get("/pokemon/cards/{card_id}/history")
def card_history(card_id: int, db: DbSession, grade: str = "PSA 10",
                 range: Literal["7d", "30d", "3m", "1y", "all"] = "30d") -> dict[str, Any]:  # noqa: A002
    try:
        g = parse_label(grade)
    except GradeError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return service.history(db, _card(db, card_id), g, range)


# --- market (CoinGecko-style) + price history engine --------------------------------------------------------------


def _grade(label: str) -> Any:
    try:
        return parse_label(label)
    except GradeError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.get("/pokemon/market")
def market_table(db: DbSession, runtime: Runtime, grade: str = Query("PSA 10", max_length=16),
                 era: Literal["vintage", "modern", "all"] = "vintage", q: str = Query("", max_length=120),
                 limit: int = Query(100, ge=1, le=250), offset: int = Query(0, ge=0),
                 sort: Literal["rank", "name", "price", "change_30d", "change_90d", "volume", "market_cap", "population", "sales"] = "rank",
                 dir: Literal["asc", "desc"] | None = None,  # noqa: A002
                 set: str | None = Query(None, max_length=128), year: int | None = None,  # noqa: A002
                 edition: str | None = Query(None, max_length=32), language: str | None = Query(None, max_length=16),
                 ids: str | None = Query(None, max_length=2000), spark: int = Query(90), view: Literal["all", "top100"] = "all") -> dict[str, Any]:
    """Ranked market at ONE grade (PSA 10 by default): sortable (unknown values always last), filterable, paged; every row
    carries its sparkline (the last 90 days of genuine observations). `ids=1,2,3` returns just those cards (e.g. one row at another grade)."""
    from app.pokemon import history, market

    catalog.ensure_catalog(db)
    history.ensure_built(db)
    id_list = None
    if ids:
        try:
            id_list = [int(x) for x in ids.split(",") if x.strip()]
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "ids: comma-separated card ids") from exc
    spark = 90  # every row's sparkline is its last 90 days of genuine observations (older clients may still send 7 / 30)
    desc = (dir or ("asc" if sort in ("rank", "name") else "desc")) == "desc"
    return market.market(db, _grade(grade), None if era == "all" or id_list else era, q, limit, offset, settings=runtime.settings,
                         sort=sort, desc=desc, filters={"set": set, "year": year, "edition": edition, "language": language},
                         ids=id_list, spark_days=spark, view=view)

@router.get("/pokemon/top100")
def top100_view(db: DbSession, runtime: Runtime, language: str | None = Query(None, max_length=16)) -> dict[str, Any]:
    """The Top 100: eligible vintage PSA 10 records ranked by market cap, with how many qualified and why others did not."""
    from app.pokemon import top100

    return top100.top100(db, None, language, runtime.settings)


@router.get("/pokemon/coverage")
def data_coverage(db: DbSession) -> dict[str, Any]:
    """PSA 10 data coverage: prices, 30D / 90D / 1Y movements, real chart points, verified sales, and what is missing why."""
    from app.pokemon import coverage

    return coverage.coverage(db)


@router.get("/pokemon/sales/audit")
def sales_audit(db: DbSession, days: int = Query(30, ge=1, le=730)) -> dict[str, Any]:
    """Re-checks every verified sale of the last `days` (identity, edition, language, grade, currency, duplicates). Report only."""
    from app.pokemon import coverage

    return coverage.sales_audit(db, days)


@router.get("/pokemon/top100/quality")
def top100_quality(db: DbSession, runtime: Runtime, language: str | None = Query(None, max_length=16), reason: str | None = Query(None, max_length=16),
                   limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0)) -> dict[str, Any]:
    """Data Quality: the vintage PSA 10 records that do NOT qualify (largest market cap / price first), each with its
    failed checks; counts per reason; recent entries / exits of the Top 100."""
    from sqlalchemy import select as sel

    from app.models.pokemon import PokemonCard, PokemonTop100Event
    from app.pokemon import market as mk
    from app.pokemon import top100
    from app.pokemon.service import card_out

    q = top100.assess(db, None, runtime.settings)
    t = top100.top100(db, None, language, runtime.settings)
    scope = [r for r in mk.base_rows(db, top100.GRADE) if r["card"]["era"] == "vintage" and (not language or r["card"]["language"] == language)]
    rows = [r for r in scope if not q[r["card"]["id"]]["eligible"] and (not reason or reason in q[r["card"]["id"]]["failed"])]
    rows.sort(key=lambda r: (-(r["market_cap"] or 0), -(r["market_price"] or 0), r["card"]["id"]))
    events = []
    for e, c in db.execute(sel(PokemonTop100Event, PokemonCard).join(PokemonCard, PokemonCard.id == PokemonTop100Event.card_id)
                           .order_by(PokemonTop100Event.at.desc(), PokemonTop100Event.id.desc()).limit(30)).all():
        events.append({"at": e.at.isoformat(), "kind": e.kind, "rank": e.rank, "reasons": e.reasons, "card": card_out(c)})
    missing = {k: sum(1 for r in scope if k in q[r["card"]["id"]]["failed"]) for k in ("price", "population", "history")}
    return {"summary": t["summary"], "missing": missing, "total": len(rows), "offset": offset, "limit": limit,
            "rows": [{**mk._public(r), "quality": q[r["card"]["id"]]} for r in rows[offset: offset + limit]], "events": events}


@router.post("/pokemon/top100/improve")
async def top100_improve(db: DbSession, runtime: Runtime, max_requests: int = Query(300, ge=1, le=3000), background: bool = False) -> dict[str, Any]:
    """Fetch Scrydex PSA 10 history for the largest near-miss cards (Scrydex credits, capped), then record the Top 100."""
    from app.pokemon import tasks, top100
    from app.providers.pokemon import Unavailable

    async def work(s: Any) -> dict[str, Any]:
        out = await top100.improve(s, runtime.settings, max_requests, getattr(runtime, "pokemon_transport", None))
        out["recorded"] = top100.record(s)
        return out

    if background:
        return tasks.start("top100-improve", work, db.get_bind())
    try:
        return await work(db)
    except Unavailable as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, exc.reason) from exc


@router.get("/pokemon/cards/{card_id}/quality")
def card_quality(card_id: int, db: DbSession, runtime: Runtime) -> dict[str, Any]:
    """This card's PSA 10 data-quality checks, Top 100 status, and how its market cap is computed (auditable)."""
    from app.models.pokemon import PokemonCard
    from app.pokemon import market as mk
    from app.pokemon import top100

    card = db.get(PokemonCard, card_id)
    if card is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown card")
    q = top100.assess(db, None, runtime.settings).get(card_id)
    row = next((r for r in mk.base_rows(db, top100.GRADE) if r["card"]["id"] == card_id), None)
    t = top100.top100(db, None, None, runtime.settings)
    rank = next((r["rank"] for r in t["rows"] if r["card"]["id"] == card_id), None)
    cap = None
    if row and row["market_price"] is not None:
        cap = {"price": row["market_price"], "price_source": row["price_source"],
               "price_as_of": (row.get("provider") or {}).get("as_of") if row["price_source"] == "scrydex" else row.get("last_sale_at"),
               "population": row["population"], "population_source": row["population_source"], "population_as_of": row["population_as_of"],
               "market_cap": row["market_cap"]}
    return {"card_id": card_id, "grade": top100.GRADE.label, "era": card.era, "quality": q, "top100_rank": rank,
            "in_scope": card.era == "vintage", "market_cap": cap, "min_history": t["summary"]["min_history"],
            "eligible_count": t["summary"]["eligible"]}


@router.get("/pokemon/cards/{card_id}/ebay")
async def card_ebay(card_id: int, db: DbSession, runtime: Runtime, grade: str = Query("PSA 10", max_length=16), refresh: bool = False) -> dict[str, Any]:
    """Live eBay listings (Browse API, asking prices: never sales) matched to this exact card + grade; sold-data access."""
    from app.models.pokemon import PokemonCard
    from app.pokemon import ebay_cards

    card = db.get(PokemonCard, card_id)
    if card is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown card")
    return await ebay_cards.card_listings(db, runtime.settings, card, _grade(grade), refresh, getattr(runtime, "pokemon_transport", None))


@router.get("/pokemon/movers")
def movers(db: DbSession, direction: Literal["gainers", "losers"] = "gainers", grade: str = Query("PSA 10", max_length=16),
           period: Literal["30d", "90d"] = "30d", limit: int = Query(100, ge=1, le=250)) -> dict[str, Any]:
    from app.pokemon import history, market

    catalog.ensure_catalog(db)
    history.ensure_built(db)
    return market.movers(db, _grade(grade), direction, period, limit)


@router.get("/pokemon/trending")
def trending(db: DbSession, grade: str = Query("PSA 10", max_length=16), limit: int = Query(50, ge=1, le=250)) -> dict[str, Any]:
    from app.pokemon import history, market

    catalog.ensure_catalog(db)
    history.ensure_built(db)
    return market.trending(db, _grade(grade), limit)


@router.get("/pokemon/sets")
def sets_overview(db: DbSession, grade: str = Query("PSA 10", max_length=16)) -> dict[str, Any]:
    from app.pokemon import history, market

    catalog.ensure_catalog(db)
    history.ensure_built(db)
    return market.sets_overview(db, _grade(grade))


@router.get("/pokemon/grades")
def grades_overview(db: DbSession) -> dict[str, Any]:
    from app.pokemon import history, market

    catalog.ensure_catalog(db)
    history.ensure_built(db)
    return market.grades_overview(db)


@router.get("/pokemon/quality")
def data_quality(db: DbSession, grade: str = Query("PSA 10", max_length=16), flag: str | None = Query(None, max_length=32),
                 limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0)) -> dict[str, Any]:
    """Every source side by side for one exact grade: gaps, single-source and missing prices, stale data, missing population.
    The summary covers every card; the rows are paged (and optionally only one flag)."""
    from app.pokemon import market

    catalog.ensure_catalog(db)
    out = market.quality(db, _grade(grade))
    rows = [r for r in out["rows"] if not flag or flag in r["flags"]]
    return {**out, "rows": rows[offset: offset + limit], "total": len(rows), "offset": offset, "limit": limit}


@router.get("/pokemon/sales/recent")
def recent_sales(db: DbSession, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
                 grade: str | None = Query(None, max_length=16)) -> dict[str, Any]:
    """Verified completed sales, newest first. Never asking prices."""
    from app.pokemon import market

    return market.recent_sales(db, limit, offset, _grade(grade) if grade else None)


@router.get("/pokemon/cards/{card_id}/meta")
async def card_meta(card_id: int, db: DbSession, runtime: Runtime) -> dict[str, Any]:
    """Catalog facts about the card from TCGdex (cached): illustrator, HP, types, attacks, description. Never prices."""
    from app.providers.pokemon import Unavailable, provider

    card = _card(db, card_id)
    tid = (card.external_ids or {}).get("tcgdex")
    if not tid:
        return {"source": None, "meta": None, "reason": "Not linked to TCGdex yet (Data sources › TCGdex › Import)."}
    lang, _, cid = str(tid).rpartition(":")
    try:
        c = await provider("tcgdex", runtime.settings, getattr(runtime, "pokemon_transport", None)).card(cid, lang or "en")  # type: ignore[attr-defined]
    except Unavailable as exc:
        return {"source": "tcgdex", "meta": None, "reason": exc.reason}
    keep = ("id", "name", "illustrator", "rarity", "hp", "types", "stage", "evolveFrom", "description", "dexId", "attacks", "abilities",
            "weaknesses", "resistances", "retreat", "variants", "category", "trainerType", "effect", "localId")
    meta = {k: c[k] for k in keep if c.get(k) not in (None, "", [], {})}
    meta["set"] = {k: (c.get("set") or {}).get(k) for k in ("id", "name")}
    return {"source": "tcgdex", "language": "Japanese" if lang == "ja" else "English", "meta": meta,
            "url": f"https://tcgdex.dev/cards/{cid}" if lang != "ja" else None}


@router.get("/pokemon/cards/{card_id}/chart")
def card_chart(card_id: int, db: DbSession, grade: str = Query("PSA 10", max_length=16),
               range: Literal["30d", "90d", "all"] = "90d") -> dict[str, Any]:  # noqa: A002
    """Estimate line, market-cap line (where population was on record), sale markers, daily volume, candles (only with
    enough real sales), population history, period changes with reasons, highest / lowest observed sales, rank."""
    from app.pokemon import history, market

    card = _card(db, card_id)
    history.ensure_built(db)
    return market.chart(db, card, _grade(grade), range)


@router.get("/pokemon/compare")
def compare(db: DbSession, series: list[str] = Query(min_length=1, max_length=4),
            range: Literal["30d", "90d", "all"] = "90d") -> dict[str, Any]:  # noqa: A002
    """series=<card id>:<grade> (repeat): PSA 10 vs PSA 9 of one card, or two different cards."""
    from app.pokemon import history, market

    specs = []
    for item in series:
        cid, _, label = item.partition(":")
        if not cid.strip().isdigit():
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Use series=<card id>:<grade>, e.g. 1:PSA 10")
        specs.append((_card(db, int(cid)), _grade(label or "PSA 10")))
    history.ensure_built(db)
    return market.compare(db, specs, range)


@router.post("/pokemon/history/rebuild")
def rebuild_history(db: DbSession) -> dict[str, int]:
    """Recompute every valuation point from the stored sales (normally automatic after each import / sync)."""
    from app.pokemon import history

    return {"points": history.rebuild(db)}


# --- watchlist ---------------------------------------------------------------------------------------------------


@router.get("/pokemon/watchlist")
def get_watchlist(db: DbSession) -> dict[str, Any]:
    catalog.ensure_catalog(db)
    return {"cards": service.watchlist(db)}


@router.put("/pokemon/watchlist/{card_id}")
def star(card_id: int, db: DbSession) -> dict[str, bool]:
    _card(db, card_id)
    return {"watched": service.watch(db, card_id, True)}


@router.delete("/pokemon/watchlist/{card_id}")
def unstar(card_id: int, db: DbSession) -> dict[str, bool]:
    _card(db, card_id)
    return {"watched": service.watch(db, card_id, False)}


# --- data sources ---------------------------------------------------------------------------------------------------


@router.get("/pokemon/sources")
def data_sources(db: DbSession, runtime: Runtime) -> dict[str, Any]:
    return {"sources": service.sources(db, runtime.settings)}


@router.post("/pokemon/catalog/fetch-images")
async def fetch_images(db: DbSession, runtime: Runtime, force: bool = False) -> dict[str, Any]:
    """Official card images from the Pokémon TCG API (catalog only; never prices)."""
    catalog.ensure_catalog(db)
    return await service.fetch_images(db, runtime.settings, getattr(runtime, "pokemon_transport", None), force)


@router.post("/pokemon/catalog/import-tcgdex")
async def import_tcgdex(db: DbSession, runtime: Runtime, limit: int = Query(100, ge=1, le=500)) -> dict[str, Any]:
    """Vintage cards (1996-2003) from TCGdex: identity, metadata, images. Links existing cards, never duplicates or
    overwrites, never imports TCGdex pricing."""
    from app.pokemon import tcgdex

    return await tcgdex.import_vintage(db, runtime.settings, limit, getattr(runtime, "pokemon_transport", None))


@router.get("/pokemon/psa/cert/{cert_number}")
async def psa_cert(cert_number: str, runtime: Runtime) -> dict[str, Any]:
    """Verify a PSA cert with PSA's official Public API (needs PSA_API_TOKEN)."""
    from app.providers.pokemon import Unavailable, provider

    try:
        return {"cert": await provider("psa", runtime.settings, getattr(runtime, "pokemon_transport", None)).get_cert(cert_number)}  # type: ignore[attr-defined]
    except Unavailable as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, exc.reason) from exc


class PsaLink(StrictInput):
    cert_number: str = Field(min_length=1, max_length=20)
    card_id: int | None = None  # the card page's card; without it the cert is matched against the whole catalog
    confirm: bool = False  # the owner confirms a link that isn't certain (never overrides a contradicting field)


@router.post("/pokemon/psa/link")
async def psa_link(payload: PsaLink, db: DbSession, runtime: Runtime) -> dict[str, Any]:
    """Look up a PSA cert, match it to a catalog card by PSA's fields, link the card to its PSA SpecID and pull the
    card-wide PSA population. Certificate facts are returned separately from population."""
    from app.pokemon import psa
    from app.providers.pokemon import Unavailable

    catalog.ensure_catalog(db)
    if payload.card_id is not None:
        _card(db, payload.card_id)
    try:
        return await psa.link_cert(db, runtime.settings, payload.cert_number, payload.card_id, payload.confirm,
                                   getattr(runtime, "pokemon_transport", None))
    except Unavailable as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, exc.reason) from exc


@router.post("/pokemon/cards/{card_id}/psa/refresh")
async def psa_refresh(card_id: int, db: DbSession, runtime: Runtime) -> dict[str, Any]:
    """Refresh one linked card's card-wide PSA population now."""
    from app.pokemon import history, psa
    from app.providers.pokemon import Unavailable

    card = _card(db, card_id)
    p = psa._psa(runtime.settings, getattr(runtime, "pokemon_transport", None))
    try:
        out = await psa.refresh_population(db, p, card)
    except Unavailable as exc:
        service.record_source(db, "psa", 0, exc.reason)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, exc.reason) from exc
    finally:
        service.record_request(db, "psa", p.requests, p.info.rate_limit)
        db.commit()
    history.invalidate()
    service.record_source(db, "psa", out["grades_stored"], None)
    return out


class SaleRow(StrictInput):
    card_id: int
    grading_company: str = Field("RAW", max_length=10)
    grade: str = Field("NM", max_length=24)
    price: float = Field(gt=0, lt=100_000_000)
    currency: str = Field("USD", min_length=3, max_length=3)
    sale_date: str = Field(min_length=10, max_length=40)
    source: str | None = Field(None, max_length=32)
    source_listing_id: str | None = Field(None, max_length=128)
    auction_house: str | None = Field(None, max_length=64)
    url: str | None = Field(None, max_length=512)
    sale_type: str | None = Field(None, max_length=24)
    cert_number: str | None = Field(None, max_length=32)
    verified: bool | None = None


class SalesImport(StrictInput):
    rows: list[SaleRow] = Field(min_length=1, max_length=5000)
    source: str = Field("manual", max_length=32)
    verified: bool = False


class PopulationRow(StrictInput):
    card_id: int
    grading_company: Literal["PSA", "BGS", "CGC"]
    grade: str = Field(max_length=8)
    count: int = Field(ge=0)
    as_of: str | None = Field(None, max_length=40)
    source: str | None = Field(None, max_length=32)


class PopulationImport(StrictInput):
    rows: list[PopulationRow] = Field(min_length=1, max_length=5000)
    source: str = Field("manual", max_length=32)


@router.post("/pokemon/import/sales")
def import_sales(payload: SalesImport, db: DbSession) -> dict[str, Any]:
    """Sales you have the right to use. Validated, normalised, deduplicated; unverified unless marked verified."""
    catalog.ensure_catalog(db)
    rows = [{k: v for k, v in r.model_dump().items() if v is not None} for r in payload.rows]
    return service.import_sales(db, rows, payload.source, payload.verified)


@router.post("/pokemon/import/population")
def import_population(payload: PopulationImport, db: DbSession) -> dict[str, Any]:
    catalog.ensure_catalog(db)
    return service.import_population(db, [{k: v for k, v in r.model_dump().items() if v is not None} for r in payload.rows], payload.source)


class Exclude(StrictInput):
    excluded: bool


@router.patch("/pokemon/sales/{sale_id}")
def exclude_sale(sale_id: int, payload: Exclude, db: DbSession) -> dict[str, Any]:
    """Mark a sale as an outlier / shill: kept for audit, never priced."""
    s = service.set_excluded(db, sale_id, payload.excluded)
    if s is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Sale not found")
    return service.sale_out(s)


@router.post("/pokemon/snapshot")
def take_snapshot(db: DbSession) -> dict[str, int]:
    return {"snapshots": service.snapshot(db)}


class ProviderCredentials(StrictInput):
    values: dict[str, str] = Field(min_length=1, max_length=4)


@router.put("/pokemon/sources/{key}/credentials")
def save_credentials(key: str, payload: ProviderCredentials, db: DbSession, runtime: Runtime) -> dict[str, Any]:
    """Store a provider's own credentials, encrypted (never returned). Only names that provider declares are accepted."""
    from app.providers.pokemon import PROVIDERS
    from app.security.credentials import secret_store

    cls = PROVIDERS.get(key)
    if cls is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown data source")
    allowed = set(cls.info.credentials)
    names = {k.strip().lower(): v.strip() for k, v in payload.values.items()}
    if not allowed or not set(names) <= allowed or not all(names.values()):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"{cls.info.name} takes: {', '.join(c.upper() for c in allowed) or 'no credentials'}")
    store = secret_store(runtime.settings)
    for name, value in names.items():
        store.set(name, value)
    return {"sources": service.sources(db, runtime.settings)}


@router.delete("/pokemon/sources/{key}/credentials")
def forget_credentials(key: str, db: DbSession, runtime: Runtime) -> dict[str, Any]:
    from app.providers.pokemon import PROVIDERS
    from app.security.credentials import secret_store

    cls = PROVIDERS.get(key)
    if cls is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown data source")
    store = secret_store(runtime.settings)
    for name in cls.info.credentials:
        store.delete(name)
    return {"sources": service.sources(db, runtime.settings)}


# --- Phase 2: CSV importer, provenance, review queue, syncs ---------------------------------------------------------


CsvPreset = Literal["heritage", "goldin", "fanatics", "pwcc", "ebay"]


class CsvPreview(StrictInput):
    csv: str = Field(min_length=1, max_length=2_000_000)
    preset: CsvPreset | None = None  # auction-house export: fills source / auction house


class CsvAccept(StrictInput):
    row: int = Field(ge=1)
    card_id: int | None = None  # the admin's choice for a "review" row (or to correct a match)


class CsvCommit(StrictInput):
    csv: str = Field(min_length=1, max_length=2_000_000)
    accept: list[CsvAccept] = Field(min_length=1, max_length=5000)
    verified: bool = True  # the admin confirms these are real, checked sales ("manual verified")
    preset: CsvPreset | None = None


def _fx(runtime: Any):  # type: ignore[no-untyped-def]
    from app.pokemon.fx import FxRates

    return FxRates(runtime.settings.fx_api_base, getattr(runtime, "pokemon_transport", None))


@router.post("/pokemon/import/csv/preview")
def csv_preview(payload: CsvPreview, db: DbSession, runtime: Runtime) -> dict[str, Any]:
    """Read a sales CSV and show what WOULD happen per row (card match, normalised values, ready / review / duplicate / error).
    Nothing is written."""
    from app.pokemon import importer

    catalog.ensure_catalog(db)
    try:
        return importer.preview(db, payload.csv, _fx(runtime), None, payload.preset)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.post("/pokemon/import/csv/commit")
def csv_commit(payload: CsvCommit, db: DbSession, runtime: Runtime) -> dict[str, Any]:
    """Import the confirmed rows. Every row is checked again here; duplicates and errors are never imported."""
    from app.pokemon import importer

    catalog.ensure_catalog(db)
    try:
        return importer.commit(db, payload.csv, _fx(runtime), {a.row: a.card_id for a in payload.accept}, payload.verified, payload.preset)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.get("/pokemon/cards/{card_id}/provenance")
def card_provenance(card_id: int, db: DbSession, grade: str = "PSA 10") -> dict[str, Any]:
    """WHY IS THIS PRICE $X? Sales used (with weights) and left out (with reasons), algorithm version, confidence."""
    try:
        g = parse_label(grade)
    except GradeError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return service.provenance(db, _card(db, card_id), g)


@router.get("/pokemon/review")
def review(db: DbSession) -> dict[str, Any]:
    """REVIEW NEEDED: marketplace items whose card or grade couldn't be matched safely (nothing imported)."""
    catalog.ensure_catalog(db)
    return {"items": service.review_queue(db)}


class ReviewDecision(StrictInput):
    card_id: int | None = None
    grade: str | None = Field(None, max_length=16)
    reject: bool = False


@router.post("/pokemon/review/{listing_id}")
def resolve(listing_id: int, payload: ReviewDecision, db: DbSession) -> dict[str, Any]:
    try:
        return service.resolve_review(db, listing_id, payload.card_id, payload.grade, payload.reject)
    except (ValueError, GradeError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.post("/pokemon/sources/{key}/test")
async def test_source(key: str, db: DbSession, runtime: Runtime) -> dict[str, Any]:
    """TEST CONNECTION: one small real request to the provider (read-only)."""
    from app.pokemon import sync
    from app.providers.pokemon import Unavailable

    try:
        return await sync.test_connection(db, runtime.settings, key, getattr(runtime, "pokemon_transport", None))
    except Unavailable as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, exc.reason) from exc


class ScrydexSync(StrictInput):
    card_ids: list[int] | None = Field(None, max_length=500)  # None: every English catalog card with a set code
    history: bool = True  # also the provider's daily price history (1 request per card variant)
    max_requests: int | None = Field(None, ge=1, le=5000)  # hard cap for this run (1 request = 1 credit)
    history_grades: list[Literal["PSA 10", "PSA 9", "PSA 8", "PSA 7"]] = Field(default_factory=lambda: ["PSA 10"], max_length=4)


# --- Market Pulse (public X posts; context only, never prices) ------------------------------------------------------


@router.get("/pokemon/pulse")
def pulse_feed(db: DbSession, runtime: Runtime, tab: Literal["latest", "popular", "auctions", "vintage"] = "latest",
               limit: int = Query(30, ge=1, le=100)) -> dict[str, Any]:
    from app.pokemon import pulse

    return {"tab": tab, "posts": pulse.feed(db, tab, limit), "status": pulse.status(db, runtime.settings, runtime.x)}


@router.post("/pokemon/pulse/refresh")
async def pulse_refresh(db: DbSession, runtime: Runtime, older: bool = False) -> dict[str, Any]:
    """ONE budget-checked X recent search (manual, with a cooldown). `older=true` loads earlier posts instead of newer ones."""
    from app.pokemon import pulse
    from app.security.credentials import x_credentials
    from app.social.x_service import make_x_client

    if not x_credentials(runtime.settings).app_only:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "X is not connected (no bearer token).")
    client = make_x_client(db, runtime.settings, runtime.x)
    try:
        result = await pulse.refresh(db, client, force=older, older=older)
    finally:
        await client.aclose()
    return {**result, "status": pulse.status(db, runtime.settings, runtime.x)}


class PulseConfig(StrictInput):
    enabled: bool | None = None  # recurring refresh (every ~15 min, budget permitting)
    trusted_accounts: list[str] | None = Field(None, max_length=50)


@router.put("/pokemon/pulse/config")
def pulse_config(payload: PulseConfig, db: DbSession, runtime: Runtime) -> dict[str, Any]:
    from app.pokemon import pulse

    cfg = pulse.config(db)
    if payload.trusted_accounts is not None:
        handles = [h for h in (pulse.clean_handle(x) for x in payload.trusted_accounts) if h]
        bad = [x for x in payload.trusted_accounts if x.strip() and not pulse.clean_handle(x)]
        if bad:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Not X usernames: {', '.join(bad[:5])}")
        cfg.trusted_accounts = list(dict.fromkeys(handles))
        cfg.since_id = None  # the query changed: the next refresh starts fresh
    if payload.enabled is not None:
        cfg.enabled = payload.enabled
    db.commit()
    return pulse.status(db, runtime.settings, runtime.x)


@router.get("/pokemon/years")
def years(db: DbSession, grade: str = Query("PSA 10", max_length=16), language: str | None = Query(None, max_length=16)) -> dict[str, Any]:
    """Market cap by release year (all languages, or one)."""
    from app.pokemon import market as mk

    return mk.years_overview(db, _grade(grade), language)


class CatalogSync(StrictInput):
    max_sets: int = Field(40, ge=1, le=500)
    max_requests: int = Field(150, ge=1, le=800)
    set_ids: list[str] | None = None
    languages: list[str] = ["EN", "JA"]


class SalesSync(StrictInput):
    card_ids: list[int] | None = None
    days: int = Field(365, ge=1, le=365)
    max_requests: int = Field(200, ge=1, le=1000)


@router.post("/pokemon/sources/scrydex/sales-sync")
async def scrydex_sales_sync(payload: SalesSync, db: DbSession, runtime: Runtime, background: bool = False) -> dict[str, Any]:
    """Verified sales from Scrydex's sold listings (completed transactions), each title matched to the exact card + grade."""
    from app.pokemon import scrydex_sales, tasks
    from app.providers.pokemon import Unavailable

    async def work(s: Any) -> dict[str, Any]:
        return await scrydex_sales.sync(s, runtime.settings, payload.card_ids, payload.days, transport=getattr(runtime, "pokemon_transport", None),
                                        max_requests=payload.max_requests)

    if background:
        return tasks.start("scrydex-sales", work, db.get_bind())
    try:
        return await work(db)
    except Unavailable as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, exc.reason) from exc


@router.post("/pokemon/sales/recheck")
async def sales_recheck(db: DbSession, background: bool = False) -> dict[str, Any]:
    """Re-match every provider-imported sale (ones the matcher no longer accepts are excluded, never deleted) and the review
    queue of sold records."""
    from app.pokemon import scrydex_sales, tasks

    if background:
        return tasks.start("sales-recheck", scrydex_sales.recheck, db.get_bind(), blocking=True)
    return scrydex_sales.recheck(db)


@router.get("/pokemon/tasks/{name}")
def task_status(name: str) -> dict[str, Any]:
    """A background admin task: idle | running | done (with its result) | error."""
    from app.pokemon import tasks

    return tasks.status(name)


@router.post("/pokemon/sources/scrydex/catalog-sync")
async def scrydex_catalog_sync(payload: CatalogSync, db: DbSession, runtime: Runtime, background: bool = False) -> dict[str, Any]:
    """Import / refresh the whole catalogue from Scrydex, set by set (sets refreshed in the last 7 days are skipped)."""
    from app.pokemon import bulk, tasks
    from app.providers.pokemon import Unavailable

    async def work(s: Any) -> dict[str, Any]:
        return await bulk.sync(s, runtime.settings, payload.max_sets, tuple(x.upper() for x in payload.languages), payload.set_ids,
                               getattr(runtime, "pokemon_transport", None), payload.max_requests)

    if background:
        return tasks.start("scrydex-catalog", work, db.get_bind())
    try:
        return await work(db)
    except Unavailable as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, exc.reason) from exc


@router.get("/pokemon/fx")
def fx_rate(runtime: Runtime) -> dict[str, Any]:
    """USD -> JPY display rate with its date and fetch time (ECB reference rate). Prices themselves stay in USD."""
    from app.pokemon import fx

    return {"usd_jpy": fx.latest_usd_jpy(runtime.settings, getattr(runtime, "pokemon_transport", None))}


class JapaneseMap(StrictInput):
    card_ids: list[int] | None = None
    max_requests: int = Field(10, ge=1, le=50)


@router.post("/pokemon/sources/scrydex/map-japanese")
async def scrydex_map_japanese(payload: JapaneseMap, db: DbSession, runtime: Runtime) -> dict[str, Any]:
    """Link Japanese catalog cards to their Scrydex Japanese card (exact English name in the matching Japanese set). No prices."""
    from app.pokemon import japanese
    from app.providers.pokemon import Unavailable

    catalog.ensure_catalog(db)
    try:
        return await japanese.map_scrydex(db, runtime.settings, payload.card_ids, getattr(runtime, "pokemon_transport", None), payload.max_requests)
    except Unavailable as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, exc.reason) from exc


@router.post("/pokemon/sources/scrydex/sync")
async def scrydex_sync(payload: ScrydexSync, db: DbSession, runtime: Runtime, background: bool = False) -> dict[str, Any]:
    """Sync Scrydex market valuations, PSA population and price history for exact card variants. Returns the run log."""
    from app.pokemon import scrydex_sync as sx
    from app.pokemon import tasks
    from app.providers.pokemon import Unavailable

    catalog.ensure_catalog(db)
    grades = tuple((g.split()[0], g.split()[1]) for g in payload.history_grades)

    async def work(s: Any) -> dict[str, Any]:
        return await sx.sync(s, runtime.settings, payload.card_ids, payload.history, getattr(runtime, "pokemon_transport", None), payload.max_requests,
                             history_grades=grades)

    if background:
        return tasks.start("scrydex-sync", work, db.get_bind())
    try:
        return await work(db)
    except Unavailable as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, exc.reason) from exc


@router.get("/pokemon/sync-logs")
def sync_logs(db: DbSession, provider: str = Query("scrydex", max_length=32), limit: int = Query(10, ge=1, le=100)) -> dict[str, Any]:
    from app.models.pokemon import PokemonSyncLog
    from app.pokemon.scrydex_sync import log_out

    rows = db.scalars(select(PokemonSyncLog).where(PokemonSyncLog.provider == provider).order_by(PokemonSyncLog.id.desc()).limit(limit))
    return {"logs": [log_out(r) for r in rows]}


@router.post("/pokemon/sources/{key}/sync")
async def sync_source(key: Literal["ebay", "pricecharting", "gemrate", "psa"], db: DbSession, runtime: Runtime, background: bool = False) -> dict[str, Any]:
    from app.pokemon import psa, sync, tasks
    from app.providers.pokemon import Unavailable

    catalog.ensure_catalog(db)
    fn = {"ebay": sync.sync_ebay, "pricecharting": sync.sync_pricecharting, "gemrate": sync.sync_gemrate, "psa": psa.sync_psa}[key]

    async def work(s: Any) -> dict[str, Any]:
        return await fn(s, runtime.settings, getattr(runtime, "pokemon_transport", None))

    if background:
        return tasks.start(f"source-{key}", work, db.get_bind())
    try:
        return await work(db)
    except Unavailable as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, exc.reason) from exc


class ExternalId(StrictInput):
    provider: Literal["gemrate", "pricecharting", "pokemontcg"]
    id: str | None = Field(None, max_length=64)  # empty / null removes the link


@router.put("/pokemon/cards/{card_id}/external-id")
def set_external_id(card_id: int, payload: ExternalId, db: DbSession) -> dict[str, Any]:
    """Link a catalog card to a provider's id by hand (when automatic matching wasn't certain)."""
    from app.pokemon import history

    card = _card(db, card_id)
    ids = dict(card.external_ids or {})
    if payload.id and payload.id.strip():
        ids[payload.provider] = payload.id.strip()
    else:
        ids.pop(payload.provider, None)
    card.external_ids = ids
    db.commit()
    history.invalidate()
    return {"card_id": card.id, "external_ids": ids}


class PopulationCsv(StrictInput):
    csv: str = Field(min_length=1, max_length=2_000_000)
    source: str = Field("psa", max_length=32)  # psa | cgc | beckett | manual ... (who reported the counts)


@router.post("/pokemon/import/population/csv")
def import_population_csv(payload: PopulationCsv, db: DbSession) -> dict[str, Any]:
    """Population report rows (e.g. copied from the PSA Pop Report): card_id or card/set/number, grading_company, grade,
    count, as_of. A row whose card isn't matched with certainty is refused (give card_id)."""
    from app.pokemon import importer

    catalog.ensure_catalog(db)
    try:
        return importer.import_population_csv(db, payload.csv, payload.source)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.get("/pokemon/match")
def explain_match(db: DbSession, title: str = Query(min_length=3, max_length=300)) -> dict[str, Any]:
    """How a title would be matched (for checking the matcher). Read-only."""
    from app.models.pokemon import PokemonCard as Card
    from app.pokemon import matching
    from app.pokemon.importer import card_label

    catalog.ensure_catalog(db)
    cards = list(db.scalars(select(Card)))
    by_id = {c.id: c for c in cards}
    m = matching.match(title, cards)
    return {"title": title, "status": m.status, "card_id": m.card_id, "card": card_label(by_id[m.card_id]) if m.card_id else None,
            "confidence": m.confidence, "grade": m.grade.label if m.grade else None, "reasons": m.reasons,
            "candidates": [{"card_id": cid, "card": card_label(by_id[cid]), "confidence": sc} for cid, sc in m.candidates]}
