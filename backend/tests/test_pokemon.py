"""Pokémon Vintage Market Cap: catalog identity, grades, the price engine, estimated market cap, imports (dedupe),
snapshots and moves, search, watchlist, provider honesty (unavailable, never faked)."""

import asyncio
import json
from datetime import timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.settings import Settings
from app.db.base import utcnow
from app.models.pokemon import PokemonCard, PokemonPriceSnapshot, PokemonSale
from app.pokemon import catalog, pricing, service
from app.pokemon.grades import Grade, GradeError, normalise, parse_label
from app.providers.pokemon import PROVIDERS, Unavailable, provider
from pydantic import SecretStr


def card(db: Session, name: str, edition: str) -> PokemonCard:
    catalog.ensure_catalog(db)
    c = db.scalar(select(PokemonCard).where(PokemonCard.name == name, PokemonCard.set_name == "Base Set", PokemonCard.edition == edition))
    assert c is not None
    return c


def sale(card_id: int, grade: str, price: float, days_ago: float, listing: str | None = None, company: str = "PSA", **kw):  # type: ignore[no-untyped-def]
    return {"card_id": card_id, "grading_company": company, "grade": grade, "price": price,
            "sale_date": (utcnow() - timedelta(days=days_ago)).isoformat(), "source": kw.pop("source", "ebay"), "source_listing_id": listing, **kw}


# --- grades & engine ----------------------------------------------------------------------------------------------


def test_grades_are_normalised_and_never_merged() -> None:
    assert normalise("psa", 10).label == "PSA 10"
    assert normalise("Beckett", "9.5").label == "BGS 9.5"
    assert normalise("CGC", "Pristine 10").label == "CGC 10"
    assert normalise("raw", "near mint").label == "RAW NM"
    assert parse_label("BGS 10") == Grade("BGS", "10") and Grade("PSA", "10") != Grade("BGS", "10")
    for bad in (("PSA", "11"), ("PSA", "9.3"), ("SGC", "10"), ("RAW", "mint-ish")):
        with pytest.raises(GradeError):
            normalise(*bad)


def test_market_price_is_a_recency_weighted_median_not_the_last_sale() -> None:
    now = utcnow()
    pts = [pricing.SalePoint(c, now - timedelta(days=d)) for c, d in ((10_000_00, 200), (10_500_00, 150), (11_000_00, 60), (20_000_00, 2))]
    st = pricing.compute(pts, now)
    assert st.last_sale == 20_000_00  # the odd latest sale doesn't set the price
    assert st.market_price == 11_000_00 and st.window_days == 365  # only 2 sales in 90 days: the 365-day window, older weigh 0.25
    assert (st.sales_7d, st.sales_30d, st.sales_90d, st.median_90d, st.median_30d) == (1, 1, 2, 15_500_00, 20_000_00)
    assert pricing.compute([], now).market_price is None  # no sales: no price, never a guess
    old = pricing.compute([pricing.SalePoint(5_000_00, now - timedelta(days=400))], now)
    assert old.market_price is None and old.last_sale == 5_000_00  # outside every window: last sale shown, no current price


def test_estimated_market_cap_needs_both_numbers_and_a_graded_card() -> None:
    assert pricing.market_cap(25_000_00, 125, True) == 3_125_000_00  # $25,000 x 125 = $3,125,000
    assert pricing.market_cap(None, 125, True) is None and pricing.market_cap(25_000_00, None, True) is None
    assert pricing.market_cap(500_00, 1000, graded=False) is None  # raw cards have no population


# --- catalog & search ----------------------------------------------------------------------------------------------


def test_first_edition_and_unlimited_are_different_assets(db: Session) -> None:
    assert catalog.ensure_catalog(db) == len(catalog.SEED)
    assert catalog.ensure_catalog(db) == 0  # idempotent
    first, unl = card(db, "Charizard", "1st Edition"), card(db, "Charizard", "Unlimited")
    assert first.id != unl.id and first.identity_key != unl.identity_key
    assert (first.card_number, first.year, first.language) == ("4/102", 1999, "English")
    assert all(c.image_url is None for c in db.scalars(select(PokemonCard)))  # no invented images


@pytest.mark.parametrize("q,expect,grade", [
    ("Charizard 1st edition PSA 10", ["Charizard|Base Set|1st Edition", "Dark Charizard|Team Rocket|1st Edition",
                                      "Shining Charizard|Neo Destiny|1st Edition"], "PSA 10"),
    ("Japanese Lugia Neo", ["Lugia|Japanese Neo Genesis (Gold, Silver, to a New World...)|—"], None),
    ("Pikachu Illustrator", ["Pikachu Illustrator|CoroCoro Comic Promo|—"], None),
    ("Base Set Blastoise", ["Blastoise|Base Set|1st Edition", "Blastoise|Base Set|Unlimited"], None),
    ("4/102 unlimited", ["Charizard|Base Set|Unlimited"], None),
])
def test_search_matches_name_set_number_year_edition_language(db: Session, q: str, expect: list[str], grade: str | None) -> None:
    catalog.ensure_catalog(db)
    cards, g = catalog.search(db, q)
    assert sorted(f"{c.name}|{c.set_name}|{c.edition}" for c in cards) == sorted(expect)
    assert (g.label if g else None) == grade


# --- imports, valuations, market cap ---------------------------------------------------------------------------------


def test_sales_import_validates_and_refuses_duplicates(db: Session) -> None:
    c = card(db, "Charizard", "1st Edition")
    rows = [sale(c.id, "10", 300_000, 3, "eb-1"), sale(c.id, "10", 300_000, 3, "eb-1"),  # same listing twice
            sale(c.id, "10", 290_000, 5, None, source="goldin"), sale(c.id, "10", 290_000, 5, None, source="goldin"),  # same fingerprint
            sale(c.id, "11", 1, 1), {**sale(c.id, "10", 1, 1), "card_id": 99999}, sale(c.id, "10", 100, -5)]
    out = service.import_sales(db, rows)
    assert out["imported"] == 2 and out["duplicates"] == 2 and [e["row"] for e in out["errors"]] == [5, 6, 7]
    assert service.import_sales(db, [sale(c.id, "10", 300_000, 3, "eb-1")])["duplicates"] == 1  # also across imports
    assert all(not s.verified for s in db.scalars(select(PokemonSale)))  # imported = unverified unless stated


def test_valuations_per_grade_and_estimated_market_cap(db: Session) -> None:
    c = card(db, "Charizard", "1st Edition")
    service.import_sales(db, [sale(c.id, "10", 300_000, 2, "a"), sale(c.id, "10", 310_000, 10, "b"), sale(c.id, "9", 40_000, 4, "c"),
                              sale(c.id, "NM", 9_000, 6, "d", company="RAW")], verified=True)
    service.import_population(db, [{"card_id": c.id, "grading_company": "PSA", "grade": "10", "count": 121},
                                   {"card_id": c.id, "grading_company": "PSA", "grade": "9", "count": 1500}])
    d = service.card_detail(db, c)
    by = {v["label"]: v for v in d["valuations"]}
    assert list(by) == ["RAW NM", "PSA 8", "PSA 9", "PSA 10"]
    assert by["PSA 10"]["market_price"] == 300_000_00 and by["PSA 10"]["market_cap"] == 300_000_00 * 121
    assert by["PSA 9"]["market_cap"] == 40_000_00 * 1500
    assert by["RAW NM"]["market_price"] == 9_000_00 and by["RAW NM"]["market_cap"] is None and by["RAW NM"]["population"] is None
    assert by["PSA 8"]["market_price"] is None and by["PSA 8"]["population"] is None  # no data: "—", never invented
    # price history engine: the oldest sale is 10 days old, so no 30D / 90D move exists yet ("—" with the reason, never 0%)
    assert by["PSA 10"]["change_30d"] is None and by["PSA 10"]["change_reasons"]["30d"] == "no estimate 30 days ago"
    assert by["PSA 9"]["change_90d"] is None and by["PSA 9"]["change_reasons"]["90d"] == "no estimate 90 days ago"
    assert "change_7d" not in by["PSA 10"] and "change_24h" not in by["PSA 10"]  # vintage cards: no crypto-style periods
    assert d["population_totals"] == {"PSA": 1621} and d["total_market_cap"] == 300_000_00 * 121 + 40_000_00 * 1500
    assert [s["label"] for s in d["recent_sales"]] == ["PSA 10", "PSA 9", "RAW NM", "PSA 10"]


def test_excluded_sales_are_kept_but_never_priced(db: Session) -> None:
    c = card(db, "Blastoise", "1st Edition")
    service.import_sales(db, [sale(c.id, "10", 50_000, 2, "x1"), sale(c.id, "10", 5, 1, "shill")], verified=True)
    shill = db.scalar(select(PokemonSale).where(PokemonSale.source_listing_id == "shill"))
    service.set_excluded(db, shill.id, True)
    v = {x["label"]: x for x in service.card_detail(db, c)["valuations"]}["PSA 10"]
    assert v["market_price"] == 50_000_00 and v["stats"]["sales_count"] == 1


def test_snapshots_build_history_and_moves(db: Session) -> None:
    c = card(db, "Venusaur", "1st Edition")
    service.import_sales(db, [sale(c.id, "10", 20_000, 20, "v1")], verified=True)
    service.import_population(db, [{"card_id": c.id, "grading_company": "PSA", "grade": "10", "count": 100}])
    past = utcnow() - timedelta(days=8)
    assert service.snapshot(db, past) == 1
    service.import_sales(db, [sale(c.id, "10", 30_000, 1, "v2"), sale(c.id, "10", 30_000, 0.5, "v3")], verified=True)
    assert service.snapshot(db) == 1 and service.snapshot(db) == 0  # at most one per ~hour
    v = {x["label"]: x for x in service.card_detail(db, c)["valuations"]}["PSA 10"]
    assert v["market_price"] == 30_000_00 and v["change_30d"] is None and v["change_reasons"]["30d"] == "no estimate 30 days ago"
    h = service.history(db, c, Grade("PSA", "10"), "30d")
    assert [p["v"] for p in h["line"]] == [20_000_00, 30_000_00] and len(h["sales"]) == 3
    ov = service.overview(db)
    assert ov["hero"]["total_market_cap"] == 30_000_00 * 100 and ov["hero"]["change_7d"] == 50.0
    assert ov["sections"]["gainers"] == [] and ov["sections"]["losers"] == []  # gainers / losers are 30D moves: none exists yet
    assert db.scalar(select(PokemonPriceSnapshot.market_cap_cents).order_by(PokemonPriceSnapshot.taken_at)) == 20_000_00 * 100


def test_overview_without_data_lists_the_catalog_with_no_numbers(db: Session) -> None:
    catalog.ensure_catalog(db)
    ov = service.overview(db)
    assert ov["has_market_data"] is False
    assert ov["hero"] == {"total_market_cap": None, "change_24h": None, "change_7d": None, "cards_tracked": len(catalog.SEED),
                          "sales_tracked": 0, "volume_24h": 0}
    assert len(ov["top"]) == len(catalog.SEED) and all(r["market_price"] is None and r["market_cap"] is None for r in ov["top"])
    assert all(not v for v in ov["sections"].values())


def test_overview_filters_and_low_pop(db: Session) -> None:
    jp = next(c for c in db.scalars(select(PokemonCard)) if c.name == "Pikachu Illustrator") if catalog.ensure_catalog(db) or True else None
    service.import_population(db, [{"card_id": jp.id, "grading_company": "PSA", "grade": "10", "count": 1}])
    ov = service.overview(db, {"language": "Japanese"})
    assert {r["card"]["language"] for r in ov["top"]} == {"Japanese"}
    assert ov["sections"]["low_pop"][0]["card"]["id"] == jp.id and ov["sections"]["low_pop"][0]["market_cap"] is None  # no price: no cap
    assert service.overview(db, {"grade": "PSA 9"})["top"] == []


def test_watchlist(db: Session) -> None:
    c = card(db, "Charizard", "Unlimited")
    service.watch(db, c.id, True)
    service.watch(db, c.id, True)
    wl = service.watchlist(db)
    assert [w["card"]["id"] for w in wl] == [c.id] and wl[0]["headline"] is None and wl[0]["latest_sale"] is None
    service.watch(db, c.id, False)
    assert service.watchlist(db) == []


# --- providers: honest about access -----------------------------------------------------------------------------------


def test_providers_report_access_honestly_and_never_return_data_without_it(db: Session) -> None:
    s = Settings()
    st = {r["key"]: r for r in service.sources(db, s)}
    assert set(st) == {"psa", "gemrate", "ebay", "pricecharting", "heritage", "goldin", "fanatics", "tcgplayer", "beckett", "pokemontcg", "tcgdex", "scrydex", "manual"}
    assert not any(st[k]["connected"] for k in ("heritage", "goldin", "fanatics"))  # export-only houses: never "connected"
    assert st["tcgplayer"]["connected"] is False and "new API applications" in st["tcgplayer"]["reason"]
    assert st["beckett"]["connected"] is False and st["psa"]["missing"] == ["PSA_API_KEY"]
    assert st["pokemontcg"]["connected"] and st["manual"]["connected"]
    assert st["psa"]["serves"] == ["cert", "get_population"]  # PSA swagger: cert lookup + GetPSASpecPopulation; no prices or sales
    for key in ("ebay", "pricecharting", "tcgplayer", "beckett"):
        with pytest.raises(Unavailable):
            asyncio.run(provider(key, s).get_sales("x"))
    with pytest.raises(Unavailable):
        asyncio.run(provider("psa", s).get_population("x"))
    assert set(PROVIDERS) == set(st)


def test_psa_cert_lookup_uses_the_official_endpoint() -> None:
    seen: list[str] = []

    def handle(r: httpx.Request) -> httpx.Response:
        seen.append(f"{r.url.path} {r.headers.get('authorization')}")
        return httpx.Response(200, json={"IsValidRequest": True, "ServerMessage": "Request successful",
                                         "PSACert": {"CertNumber": "12345678", "Year": "1999", "Brand": "POKEMON GAME", "CardNumber": "4",
                                                     "Subject": "CHARIZARD-HOLO", "Variety": "1ST EDITION", "CardGrade": "GEM MT 10",
                                                     "TotalPopulation": 121}})
    s = Settings(psa_api_token=SecretStr("tok"), psa_api_base="https://api.psa.test/publicapi")
    out = asyncio.run(provider("psa", s, httpx.MockTransport(handle)).get_cert("1234-5678"))  # type: ignore[attr-defined]
    assert seen == ["/publicapi/cert/GetByCertNumber/12345678 bearer tok"]
    assert out["subject"] == "CHARIZARD-HOLO" and out["grade"] == "GEM MT 10" and out["total_population"] == 121
    with pytest.raises(Unavailable) as exc:
        asyncio.run(provider("psa", Settings()).get_cert("1"))  # type: ignore[attr-defined]
    assert exc.value.code == "not_connected"


def test_images_come_from_the_pokemon_tcg_api_and_never_prices(db: Session) -> None:
    catalog.ensure_catalog(db)
    calls: list[str] = []

    charizard = {"id": "base1-4", "name": "Charizard", "number": "4", "rarity": "Rare Holo",
                 "set": {"id": "base1", "name": "Base", "printedTotal": 102, "releaseDate": "1999/01/09"},
                 "images": {"large": "https://images.pokemontcg.io/base1/4_hires.png"},
                 "tcgplayer": {"prices": {"holofoil": {"market": 999.99}}}}

    def handle(r: httpx.Request) -> httpx.Response:
        calls.append(r.url.path + ("?" + r.url.params.get("q", "") if r.url.params.get("q") else ""))
        if r.url.path.endswith("/cards/base1-4"):
            return httpx.Response(200, json={"data": charizard})  # direct lookup by "<set>-<number>"
        if r.url.path.endswith("/cards"):
            return httpx.Response(500, json={})  # the search endpoint can be down: lookups don't depend on it
        return httpx.Response(404, json={"error": "not found"})
    s = Settings(pokemontcg_api_base="https://api.tcg.test/v2")
    out = asyncio.run(service.fetch_images(db, s, httpx.MockTransport(handle)))
    # the three Base Set Charizard editions share the artwork; a failing search fallback only marks that card "not found"
    assert out["updated"] == 3 and out["error"] == "The Pokémon TCG API answered HTTP 500."
    with_codes = [c.id for c in db.scalars(select(PokemonCard).where(PokemonCard.set_code.is_not(None))) if c.set_code != "base1" or c.card_number != "4/102"]
    assert sorted(out["not_found"]) == sorted(with_codes)  # the run went through every card
    db.expire_all()
    imgs = {c.edition: c.image_url for c in db.scalars(select(PokemonCard).where(PokemonCard.set_code == "base1", PokemonCard.card_number == "4/102"))}
    assert set(imgs.values()) == {"https://images.pokemontcg.io/base1/4_hires.png"}
    assert db.scalar(select(PokemonSale.id)) is None  # its embedded marketplace prices are never imported
    assert "/v2/cards/base1-4" in calls  # direct lookup first
    assert not any(c.image_url for c in db.scalars(select(PokemonCard).where(PokemonCard.language == "Japanese")))


# --- API --------------------------------------------------------------------------------------------------------------


def test_api_routes(client: TestClient) -> None:
    ov = client.get("/api/pokemon/overview").json()
    assert ov["hero"]["cards_tracked"] == len(catalog.SEED) and ov["hero"]["total_market_cap"] is None
    found = client.get("/api/pokemon/cards", params={"q": "Charizard 1st edition PSA 10"}).json()
    assert found["grade"] == "PSA 10" and {r["card"]["edition"] for r in found["results"]} == {"1st Edition"}
    assert found["results"][0]["card"]["set_name"] == "Base Set"  # the exact name ranks first
    cid = found["results"][0]["card"]["id"]
    assert found["results"][0]["market_price"] is None
    imp = client.post("/api/pokemon/import/sales", json={"rows": [{"card_id": cid, "grading_company": "PSA", "grade": "10", "price": 250000,
                                                                  "sale_date": utcnow().isoformat(), "source": "goldin", "source_listing_id": "g-1"}],
                                                         "verified": True})
    assert imp.json()["imported"] == 1
    detail = client.get(f"/api/pokemon/cards/{cid}").json()
    assert {v["label"]: v["market_price"] for v in detail["valuations"]}["PSA 10"] == 250_000_00
    assert client.get(f"/api/pokemon/cards/{cid}/history", params={"grade": "PSA 10", "range": "all"}).json()["sales"][0]["v"] == 250_000_00
    assert client.get(f"/api/pokemon/cards/{cid}/history", params={"grade": "PSA 11"}).status_code == 400
    assert client.put(f"/api/pokemon/watchlist/{cid}").json() == {"watched": True}
    assert [w["card"]["id"] for w in client.get("/api/pokemon/watchlist").json()["cards"]] == [cid]
    src = client.get("/api/pokemon/sources").json()["sources"]
    allowed = {"key", "name", "connected", "reason", "serves", "access", "licence", "credentials", "missing", "credentials_present", "rate_limit",
               "notes", "last_request_at", "requests_today", "last_sync_at", "last_success_at", "records_imported", "last_error"}
    assert all(set(r) == allowed for r in src)  # status and credential NAMES only, never values
    assert json.loads(json.dumps(src))[0]["credentials"] == ["PSA_API_KEY"]
    assert client.get("/api/pokemon/cards/99999").status_code == 404
