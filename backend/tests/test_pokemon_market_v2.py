"""Market V2 views (CoinGecko-style): sorting with unknowns last, filters, per-row grade lookups, movers, trending, sets,
grades, recent sales, card chart rank + market-cap line, card metadata. Sales here are labelled TEST FIXTURES in the test
database only; nothing is ever invented by the views (unknown stays None)."""

from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.pokemon import PokemonCard
from app.pokemon import catalog, history, service
from app.pokemon.grades import Grade


def _card(db: Session, name: str, edition: str = "1st Edition", set_name: str = "Base Set") -> PokemonCard:
    c = db.scalar(select(PokemonCard).where(PokemonCard.name == name, PokemonCard.set_name == set_name, PokemonCard.edition == edition))
    assert c is not None
    return c


def _sale(cid: int, price: float, days_ago: float, listing: str, grade: str = "10") -> dict:  # type: ignore[type-arg]
    return {"card_id": cid, "grading_company": "PSA", "grade": grade, "price": price, "source": "ebay", "source_listing_id": listing,
            "sale_date": (utcnow() - timedelta(days=days_ago)).isoformat()}  # TEST FIXTURE


def _seed(db: Session) -> tuple[int, int]:
    catalog.ensure_catalog(db)
    zard, blast = _card(db, "Charizard").id, _card(db, "Blastoise").id
    rows = [_sale(zard, 300_000, 40, "z1"), _sale(zard, 310_000, 20, "z2"), _sale(zard, 330_000, 2, "z3"), _sale(zard, 340_000, 0.2, "z4"),
            _sale(blast, 60_000, 40, "b1"), _sale(blast, 55_000, 3, "b2"), _sale(zard, 40_000, 5, "z9", grade="9")]
    assert service.import_sales(db, rows, "ebay", verified=True)["imported"] == 7
    service.store_population(db, zard, Grade("PSA", "10"), 121, "psa", utcnow() - timedelta(days=60))
    service.store_population(db, zard, Grade("PSA", "10"), 125, "psa", utcnow())
    db.commit()
    history.rebuild(db)
    return zard, blast


def test_market_sorts_with_unknowns_last_filters_and_single_rows(client: TestClient, db: Session) -> None:
    zard, blast = _seed(db)
    m = client.get("/api/pokemon/market").json()
    assert m["rows"][0]["card"]["id"] == zard and m["rows"][0]["rank"] == 1  # the only card with a market cap
    assert m["rows"][1]["card"]["id"] == blast and "rank" not in m["rows"][1]  # priced, no population: no cap, no rank
    assert len(m["rows"][0]["sparkline"]["points"]) >= 2 and m["total"] == len([c for c in db.scalars(select(PokemonCard)) if c.era == "vintage"])

    by_price_asc = client.get("/api/pokemon/market?sort=price&dir=asc").json()["rows"]
    assert [r["card"]["id"] for r in by_price_asc[:2]] == [blast, zard] and by_price_asc[2]["market_price"] is None  # "—" stays last
    by_price_desc = client.get("/api/pokemon/market?sort=price").json()["rows"]
    assert [r["card"]["id"] for r in by_price_desc[:2]] == [zard, blast] and by_price_desc[-1]["market_price"] is None

    jungle = client.get("/api/pokemon/market?set=Neo Genesis&edition=1st Edition").json()
    assert jungle["total"] == 1 and jungle["rows"][0]["card"]["name"] == "Lugia"
    nine = client.get(f"/api/pokemon/market?ids={zard}&grade=PSA 9&spark=7&era=all").json()  # exactly what the row grade dropdown sends
    assert nine["total"] == 1 and nine["rows"][0]["label"] == "PSA 9" and nine["rows"][0]["market_price"] == 40_000_00

    hero = m["hero"]
    assert hero["total_market_cap"] == m["rows"][0]["market_price"] * 125 and hero["population_total"] == 125
    assert hero["grade_sales_30d"] >= 1 and hero["volume_30d"] > 0 and hero["sales_records_from"]
    assert not any("1y" in k for k in hero) and "change_1y" not in m["rows"][0]  # 30D and 90D only
    assert hero["market_cap_change_30d"] is not None and hero["market_cap_change_30d_cards"] == 1  # price-driven, over cards with a 30D move
    assert "market_cap_change_24h" not in hero and "volume_24h" not in hero
    assert client.get("/api/pokemon/market?spark=30").status_code == 200  # older clients: ignored, the sparkline is always 1 year
    assert m["rows"][0]["sparkline"]["source"] == "sales" and m["rows"][0]["sparkline"]["from"] < m["rows"][0]["sparkline"]["to"]
    assert "_cap_past" not in m["rows"][0] and m["options"]["grades"][0] == "PSA 10"


def test_movers_trending_sets_grades_and_recent_sales(client: TestClient, db: Session) -> None:
    zard, blast = _seed(db)
    gainers = client.get("/api/pokemon/movers?direction=gainers&period=30d").json()["rows"]
    losers = client.get("/api/pokemon/movers?direction=losers&period=30d").json()["rows"]
    assert [r["card"]["id"] for r in gainers] == [zard] and [r["card"]["id"] for r in losers] == [blast]
    assert [r["card"]["id"] for r in client.get("/api/pokemon/trending").json()["rows"]][:2] == [zard, blast]

    sets = {s["set_name"]: s for s in client.get("/api/pokemon/sets").json()["sets"]}
    assert sets["Base Set"]["rank"] == 1 and sets["Base Set"]["population"] == 125 and sets["Neo Genesis"]["market_cap"] is None

    grades = {g["grade"]: g for g in client.get("/api/pokemon/grades").json()["grades"]}
    assert grades["PSA 10"]["cards_priced"] == 2 and grades["PSA 9"]["cards_priced"] == 1 and grades["RAW NM"]["market_cap"] is None

    recent = client.get("/api/pokemon/sales/recent?limit=3").json()
    assert recent["total"] == 7 and [s["source_listing_id"] for s in recent["sales"]] == ["z4", "z3", "b2"]
    assert client.get("/api/pokemon/sales/recent?grade=PSA 9").json()["total"] == 1


def test_card_chart_has_rank_24h_range_and_a_cap_line_only_where_population_was_known(client: TestClient, db: Session) -> None:
    zard, blast = _seed(db)
    ch = client.get(f"/api/pokemon/cards/{zard}/chart?range=all").json()
    assert ch["rank"] == 1 and ch["summary"]["changes"]["30d"]["value"] is not None and ch["summary"]["changes"]["30d"]["source"] == "sales"
    assert set(ch["summary"]["changes"]) == {"30d", "90d"}
    assert client.get(f"/api/pokemon/cards/{zard}/chart?range=1y").status_code == 422  # no 1-year range
    assert ch["cap_line"] and all(p["population"] in (121, 125) for p in ch["cap_line"])
    assert ch["cap_line"][-1]["v"] == ch["line"][-1]["v"] * ch["cap_line"][-1]["population"]  # the population on record that day
    assert client.get(f"/api/pokemon/cards/{zard}/chart?range=30d").status_code == 200
    b = client.get(f"/api/pokemon/cards/{blast}/chart?range=all").json()
    assert b["cap_line"] == [] and b["rank"] is None  # no population ever: no market cap, no rank
    meta = client.get(f"/api/pokemon/cards/{zard}/meta").json()
    assert meta["meta"] is None and "TCGdex" in meta["reason"]


def test_one_card_at_another_grade_never_builds_the_whole_catalogue(client: TestClient, db: Session) -> None:
    """Memory: the table's per-row grade switch and a card page at another grade compute that card alone. Building every
    card at a second grade (~300 MB at 50k cards) is what ran the production server out of memory."""
    from app.pokemon import history

    zard, _ = _seed(db)
    nine = client.get(f"/api/pokemon/market?ids={zard}&grade=PSA 9&era=all").json()
    assert nine["rows"][0]["label"] == "PSA 9" and nine["rows"][0]["market_price"] == 40_000_00
    assert client.get(f"/api/pokemon/cards/{zard}/chart?range=90d&grade=PSA 9").status_code == 200
    assert not history.is_cached(("base", "PSA 9"))  # no whole-catalogue PSA 9 rows were built
    assert client.get("/api/pokemon/market").json()["rows"][0]["rank"] == 1  # PSA 10 ranking unaffected
