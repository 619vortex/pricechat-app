"""Pokémon Phase 2: real-data ingestion. All sale rows below are TEST FIXTURES in the throwaway test database: they exercise
the pipeline and are never real market data. Covers: title / CSV matching, the CSV importer (preview, review, duplicates,
impossible values, currency), price engine v2 + confidence + provenance, eBay (listings never become sales; sold data only
with Marketplace Insights), PriceCharting reference prices, provider isolation, the end-to-end pipeline."""

import asyncio
from datetime import timedelta
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.settings import Settings
from app.db.base import utcnow
from app.models.pokemon import PokemonCard, PokemonListing, PokemonPriceSnapshot, PokemonReferencePrice, PokemonSale
from app.pokemon import catalog, importer, matching, pricing, service, sync
from app.pokemon.fx import FxRates
from app.pokemon.grades import Grade


def cards(db: Session) -> list[PokemonCard]:
    catalog.ensure_catalog(db)
    return list(db.scalars(select(PokemonCard)))


def find(db: Session, name: str, edition: str, set_name: str = "Base Set") -> PokemonCard:
    return next(c for c in cards(db) if c.name == name and c.edition == edition and c.set_name == set_name)


def day(n: float) -> str:
    return (utcnow() - timedelta(days=n)).date().isoformat()


# --- 5. title matching ---------------------------------------------------------------------------------------------


def test_the_example_title_resolves_to_exactly_one_card_and_grade(db: Session) -> None:
    m = matching.match("1999 Pokemon Base Set Charizard 4/102 1st Edition PSA 10", cards(db))
    c = db.get(PokemonCard, m.card_id)
    assert m.status == "matched" and m.confidence >= matching.AUTO_MATCH
    assert (c.year, c.set_name, c.name, c.card_number, c.language, c.edition, c.holo_type) == (1999, "Base Set", "Charizard", "4/102", "English",
                                                                                              "1st Edition", "Holo")
    assert m.grade == Grade("PSA", "10")


@pytest.mark.parametrize("title,status,why", [
    ("Pokemon Base Set Charizard 4/102 Holo PSA 9", "review", "edition not stated"),  # 1st / Shadowless / Unlimited all exist
    ("1999 Base Set Charizard 4/102 Unlimited Holo PSA 8", "matched", "Unlimited"),
    ("Charizard 4/102 Shadowless Base Set PSA 7", "matched", "Shadowless"),
    ("Charizard Celebrations Classic Collection 4/102 PSA 10", "rejected", "celebrations"),  # a reprint with the same number
    ("Pokemon Base Set 2 Charizard 4/130 PSA 9", "rejected", "base set 2"),
    ("Team Rocket Dark Charizard 4/82 1st Edition PSA 9", "matched", "Dark Charizard"),
    ("Japanese Base Set Charizard 4/102 1st Edition PSA 10", "rejected", "Japanese"),  # no English card is Japanese
    ("Pokemon Japanese Base Set Charizard No Rarity Holo PSA 9", "matched", "No Rarity"),
    ("Pokemon Japanese Base Set Charizard Holo PSA 9", "review", "No Rarity' not confirmed"),
    ("1999 Base Set Charizard 4/102 1st Edition SGC 10", "review", "SGC"),
    ("1999 Base Set Charizard 4/102 1st Edition", "review", "no grade"),
    ("Charizard proxy custom card 4/102", "rejected", "proxy"),
])
def test_uncertain_titles_never_auto_import(db: Session, title: str, status: str, why: str) -> None:
    m = matching.match(title, cards(db))
    assert m.status == status, (m.status, m.reasons)
    assert why.lower() in " ".join(m.reasons + [db.get(PokemonCard, m.card_id).name if m.card_id else ""]).lower() or \
        why.lower() in (db.get(PokemonCard, m.card_id).edition.lower() if m.card_id else "") or \
        why.lower() in (db.get(PokemonCard, m.card_id).variant.lower() if m.card_id else "")


def test_raw_is_never_mixed_with_graded() -> None:
    assert matching.parse("Base Set Charizard 4/102 Unlimited ungraded near mint").grade == Grade("RAW", "NM")
    assert matching.parse("Base Set Charizard 4/102 Unlimited ungraded").grade is None  # condition unknown: review
    assert matching.parse("Charizard PSA GEM MINT 10").grade == Grade("PSA", "10")
    assert matching.parse("Charizard BGS 9.5 Gem Mint").grade == Grade("BGS", "9.5")


# --- 3. CSV importer -----------------------------------------------------------------------------------------------

HEADER = "card,set,year,card_number,language,edition,variant,grading_company,grade,cert_number,sale_price,currency,sale_date,source,source_listing_id,url,auction_house\n"


def fx_transport(rate: float = 1.25) -> httpx.MockTransport:
    def handle(r: httpx.Request) -> httpx.Response:
        assert r.url.params["to"] == "USD"
        return httpx.Response(200, json={"amount": 1.0, "base": r.url.params["from"], "date": r.url.path.strip("/"), "rates": {"USD": rate}})
    return httpx.MockTransport(handle)


def test_csv_preview_matches_validates_and_writes_nothing(db: Session) -> None:
    cards(db)
    text = HEADER + "\n".join([
        f"Charizard,Base Set,1999,4/102,English,1st Edition,,PSA,10,111,\"$300,000\",,{day(3)},goldin,G-1,https://goldin.co/1,Goldin",  # TEST FIXTURE
        f"Charizard,Base Set,1999,4/102,English,,,PSA,9,,40000,USD,{day(5)},ebay,E-2,,",  # no edition -> review
        f"Charizard,Base Set,1999,4/102,English,1st Edition,,PSA,10,111,300000,USD,{day(3)},goldin,G-1,,",  # duplicate of row 1
        f"Charizard,Base Set,1999,4/102,English,1st Edition,,PSA,11,,1000,USD,{day(3)},ebay,E-4,,",  # impossible grade
        f"Charizard,Base Set,1999,4/102,English,1st Edition,,PSA,10,,0.5,USD,{day(3)},ebay,E-5,,",  # impossible price
        "Charizard,Base Set,1999,4/102,English,1st Edition,,PSA,10,,1000,USD,06/10/2026,ebay,E-6,,",  # ambiguous date
        f"Blastoise,Base Set,1999,2/102,English,Unlimited,,BGS,9.5,,1000,GBP,{day(10)},ebay,E-7,,",  # GBP -> USD at ECB rate
        f"Charizard,Celebrations,2021,4/102,English,,,PSA,10,,500,USD,{day(2)},ebay,E-8,,",  # reprint
    ])
    pv = importer.preview(db, text, FxRates("https://fx.test", fx_transport(1.25)))
    st = {r["row"]: r for r in pv["rows"]}
    assert [st[i]["status"] for i in range(1, 9)] == ["ready", "review", "duplicate", "error", "error", "error", "ready", "error"]
    assert st[1]["price"] == 300_000_00 and st[1]["currency"] == "USD" and st[1]["card"] == "1999 Base Set Charizard 4/102 1st Edition"
    assert sorted(c["label"] for c in st[2]["candidates"]) == ["1999 Base Set Charizard 4/102 1st Edition", "1999 Base Set Charizard 4/102 Shadowless",
                                                               "1999 Base Set Charizard 4/102 Unlimited"]  # the admin picks one
    assert "PSA grades run 1 to 10" in st[4]["errors"][0] and "outside $1" in st[5]["errors"][0] and "use YYYY-MM-DD" in st[6]["errors"][0]
    assert st[7]["price_usd"] == 1250_00 and st[7]["fx_rate"] == 1.25 and st[7]["fx_source"] == "ecb"
    assert "not an original single card" in st[8]["errors"][0]
    assert pv["counts"] == {"ready": 2, "review": 1, "duplicate": 1, "error": 4}
    assert db.scalar(select(PokemonSale.id)) is None  # a preview never writes


def test_csv_commit_imports_only_confirmed_rows_and_review_needs_a_choice(db: Session) -> None:
    unl = find(db, "Charizard", "Unlimited")
    text = HEADER + "\n".join([
        f"Charizard,Base Set,1999,4/102,English,1st Edition,,PSA,10,,300000,USD,{day(3)},goldin,G-1,,",  # TEST FIXTURE
        f"Charizard,Base Set,1999,4/102,English,,,PSA,9,,4000,USD,{day(5)},ebay,E-2,,",
        f"Charizard,Base Set,1999,4/102,English,,,PSA,8,,1500,USD,{day(6)},ebay,E-3,,",
    ])
    fx = FxRates("https://fx.test", fx_transport())
    out = importer.commit(db, text, fx, {1: None, 2: unl.id, 3: None}, verified=True)
    assert (out["imported"], out["skipped"], out["failed"]) == (2, 0, 1)  # row 3 is "review" with no decision
    sales = list(db.scalars(select(PokemonSale).order_by(PokemonSale.id)))
    assert [(s.card_id, s.grade, s.verified, s.verified_by) for s in sales] == [(find(db, "Charizard", "1st Edition").id, "10", True, "manual"),
                                                                              (unl.id, "9", True, "manual")]
    assert sales[1].match_confidence is None  # chosen by the admin, not matched
    again = importer.commit(db, text, fx, {1: None}, verified=True)
    assert (again["imported"], again["skipped"]) == (0, 3)  # duplicate + not selected: history is never overwritten


# --- 9-11. price engine v2 ------------------------------------------------------------------------------------------


def pts(*rows: tuple[int, float], verified: bool = True, ref: bool = True) -> list[pricing.SalePoint]:
    now = utcnow()
    return [pricing.SalePoint(c, now - timedelta(days=d), sale_id=i + 1, verified=verified, source="ebay", has_reference=ref)
            for i, (c, d) in enumerate(rows)]


def test_step_weights_window_and_unverified_sales() -> None:
    assert [pricing.weight_for(d) for d in (0, 7, 8, 30, 31, 90, 91)] == [1.0, 1.0, 0.8, 0.8, 0.5, 0.5, 0.25]
    st, prov = pricing.evaluate(pts((100_00, 2), (110_00, 20), (120_00, 60)) + pts((999_00, 1), verified=False), utcnow())
    assert st.market_price == 110_00 and st.window_days == 90 and st.sales_count == 3
    # 1.0 and 0.8 are both capped at a third of the total weight (2.3 / 3): no single sale dominates
    assert [r["weight"] for r in prov["included"]] == [pytest.approx(0.7667, abs=1e-3), pytest.approx(0.7667, abs=1e-3), 0.5]
    assert [r["reason"] for r in prov["excluded"]] == ["not verified"]


def test_outliers_are_marked_not_hidden() -> None:
    st, prov = pricing.evaluate(pts((100_00, 1), (102_00, 3), (98_00, 5), (101_00, 9), (99_00, 12), (5_00, 2)), utcnow())
    assert st.market_price in (100_00, 101_00) and st.sales_count == 5
    assert any(r["price"] == 5_00 and r["reason"].startswith("outlier") for r in prov["excluded"])


def test_confidence_levels() -> None:
    now = utcnow()
    high = pricing.compute(pts(*[(1000_00 + i * 10_00, i * 3) for i in range(10)]), now)
    assert high.confidence == "HIGH"
    assert pricing.compute(pts((1000_00, 3), (1100_00, 10), (1050_00, 40)), now).confidence == "MEDIUM"
    assert pricing.compute(pts((1000_00, 3)), now).confidence == "LOW"
    assert pricing.compute(pts((1000_00, 200), (1000_00, 250), (1000_00, 300)), now).confidence == "LOW"  # stale
    no_refs = pricing.compute(pts(*[(1000_00 + i * 10_00, i * 3) for i in range(10)], ref=False), now)
    assert no_refs.confidence == "MEDIUM" and "no source reference" in " ".join(no_refs.confidence_reasons)
    assert pricing.compute([], now).confidence is None


def test_volume_and_30d_stats() -> None:
    st = pricing.compute(pts((100_00, 1), (200_00, 6), (300_00, 20), (400_00, 60)), utcnow())
    assert (st.sales_7d, st.volume_7d, st.sales_30d, st.volume_30d) == (2, 300_00, 3, 600_00)
    assert (st.median_30d, st.average_30d, st.high_30d, st.low_30d, st.sales_90d) == (200_00, 200_00, 300_00, 100_00, 4)


# --- 4. eBay ---------------------------------------------------------------------------------------------------------


def ebay_transport(grant_insights: bool, items: list[dict], sold: list[dict]) -> tuple[httpx.MockTransport, list[str]]:
    seen: list[str] = []

    def handle(r: httpx.Request) -> httpx.Response:
        seen.append(r.url.path)
        if r.url.path.endswith("/oauth2/token"):
            scope = parse_qs(r.content.decode())["scope"][0]
            if scope.endswith("buy.marketplace.insights") and not grant_insights:
                return httpx.Response(400, json={"error": "invalid_scope"})
            return httpx.Response(200, json={"access_token": f"tok-{scope[-8:]}", "expires_in": 7200})
        if r.url.path.endswith("/item_summary/search"):
            assert r.headers["X-EBAY-C-MARKETPLACE-ID"] == "EBAY_US" and r.url.params["category_ids"] == "183454"
            return httpx.Response(200, json={"itemSummaries": items})
        if r.url.path.endswith("/item_sales/search"):
            return httpx.Response(200, json={"itemSales": sold})
        return httpx.Response(404, json={})
    return httpx.MockTransport(handle), seen


EBAY = Settings(ebay_client_id=SecretStr("cid"), ebay_client_secret=SecretStr("sec"), ebay_api_base="https://ebay.test", fx_api_base="https://fx.test")


def listing(i: str, title: str, price: str) -> dict:
    return {"itemId": i, "title": title, "price": {"value": price, "currency": "USD"}, "condition": "Graded", "seller": {"username": "s"},
            "buyingOptions": ["FIXED_PRICE"], "itemWebUrl": f"https://ebay.test/itm/{i}", "image": {"imageUrl": "https://img.test/x.jpg"}}


def test_ebay_active_listings_are_stored_as_listings_never_sales(db: Session) -> None:
    from app.providers.pokemon.ebay import EbayProvider

    EbayProvider._tokens.clear()
    EbayProvider.sold_access.clear()
    cards(db)
    transport, seen = ebay_transport(False, [listing("L1", "1999 Pokemon Base Set Charizard 4/102 1st Edition PSA 10", "350000.00"),  # TEST FIXTURE
                                             listing("L2", "Charizard 4/102 Base Set PSA 9", "4500.00")], [])
    out = asyncio.run(sync.sync_ebay(db, EBAY, transport))
    assert out["sold"] == "SOLD DATA UNAVAILABLE FROM CURRENT EBAY API ACCESS (Marketplace Insights is not granted to these keys)"
    rows = {x.listing_id: x for x in db.scalars(select(PokemonListing))}
    assert rows["L1"].kind == "active" and rows["L1"].match_status == "matched" and rows["L1"].card_id == find(db, "Charizard", "1st Edition").id
    assert rows["L2"].match_status == "review"  # no edition: REVIEW NEEDED
    assert db.scalar(select(PokemonSale.id)) is None  # asking prices are never sales
    src = {s["key"]: s for s in service.sources(db, EBAY)}["ebay"]
    assert src["credentials_present"] is True and src["requests_today"] > 0 and src["last_request_at"]
    assert "SOLD DATA UNAVAILABLE" in " ".join(src["notes"])


def test_ebay_sold_items_become_sales_only_when_granted_and_matched(db: Session) -> None:
    from app.providers.pokemon.ebay import EbayProvider

    EbayProvider._tokens.clear()
    EbayProvider.sold_access.clear()
    cards(db)
    when = (utcnow() - timedelta(days=4)).isoformat().replace("+00:00", "Z")
    sold = [{"itemId": "S1", "title": "1999 Pokemon Base Set Charizard 4/102 1st Edition PSA 10", "lastSoldPrice": {"value": "320000.00", "currency": "USD"},
             "lastSoldDate": when, "itemWebUrl": "https://ebay.test/itm/S1", "buyingOptions": ["AUCTION"]},  # TEST FIXTURE
            {"itemId": "S2", "title": "Pokemon Charizard Base Set Holo PSA 10", "lastSoldPrice": {"value": "9000.00", "currency": "USD"}, "lastSoldDate": when}]
    transport, _ = ebay_transport(True, [], sold)
    out = asyncio.run(sync.sync_ebay(db, EBAY, transport))
    assert out["sold"]["sales"] == 1
    s = db.scalar(select(PokemonSale))
    assert (s.source, s.source_listing_id, s.verified, s.verified_by, s.sale_type) == ("ebay", "S1", True, "source", "auction")
    queue = service.review_queue(db)
    assert [q["listing_id"] for q in queue] == ["S2"]
    unl = find(db, "Charizard", "Unlimited")
    service.resolve_review(db, queue[0]["id"], unl.id, "PSA 10")  # an admin decides: it becomes a verified sale
    assert db.scalar(select(PokemonSale).where(PokemonSale.source_listing_id == "S2")).verified_by == "manual"


# --- 7. PriceCharting --------------------------------------------------------------------------------------------------


def test_pricecharting_is_reference_only_and_keeps_its_grade_labels(db: Session) -> None:
    c = find(db, "Charizard", "Unlimited")
    c.external_ids = {"pricecharting": "6910"}
    db.commit()

    def handle(r: httpx.Request) -> httpx.Response:
        assert r.url.params["t"] == "pc-key"
        if r.url.path == "/api/product":
            return httpx.Response(200, json={"status": "success", "id": "6910", "product-name": "Charizard #4", "console-name": "Pokemon Base Set",
                                             "loose-price": 45000, "graded-price": 210000, "manual-only-price": 1250000})  # TEST FIXTURE
        return httpx.Response(200, json={"products": []})
    s = Settings(pricecharting_api_key=SecretStr("pc-key"), pricecharting_api_base="https://pc.test")
    out = asyncio.run(sync.sync_pricecharting(db, s, httpx.MockTransport(handle)))
    assert out["priced"] == 1
    refs = {r.provider_grade: (r.grading_company, r.grade, r.price_cents) for r in db.scalars(select(PokemonReferencePrice))}
    assert refs == {"Ungraded": (None, None, 45000), "Grade 9": (None, None, 210000), "PSA 10": ("PSA", "10", 1250000)}  # "Grade 9" isn't PSA 9
    v = service.card_detail(db, c)["valuations"][3]
    # no verified sales and no Scrydex value: the PriceCharting PSA 10 value is shown, LABELLED, and never becomes a sale
    assert db.scalar(select(PokemonSale.id)) is None and (v["label"], v["market_price"], v["price_source"]) == ("PSA 10", 1250000, "pricecharting")
    assert service.card_detail(db, c)["reference_prices"][0]["provider"] == "pricecharting"


def test_unavailable_providers_never_stop_the_platform(db: Session) -> None:
    out = asyncio.run(sync.sync_all(db, Settings()))
    assert out == {"ebay": {"skipped": "Not connected: add EBAY_CLIENT_ID, EBAY_CLIENT_SECRET."},
                   "pricecharting": {"skipped": "Not connected: add PRICECHARTING_API_KEY."},
                   "gemrate": {"skipped": "Not connected: add GEMRATE_API_KEY."},
                   "psa": {"skipped": "Not connected: add PSA_API_KEY."}}


# --- 12, 16, 17, 19, 22. end-to-end pipeline -----------------------------------------------------------------------------


def test_pipeline_sale_to_table_card_chart_and_provenance(client: TestClient, db: Session) -> None:
    """TEST FIXTURE rows (labelled, test DB only): CSV -> match -> stored sales -> engine -> snapshot -> table -> card page -> chart."""
    csv = HEADER + "\n".join([
        f"Charizard,Base Set,1999,4/102,English,1st Edition,,PSA,10,,{p},USD,{day(d)},goldin,TEST-{i},https://example.test/{i},Goldin"
        for i, (p, d) in enumerate([(300000, 2), (310000, 9), (295000, 20), (305000, 40)])])
    pv = client.post("/api/pokemon/import/csv/preview", json={"csv": csv}).json()
    assert pv["counts"]["ready"] == 4
    done = client.post("/api/pokemon/import/csv/commit", json={"csv": csv, "accept": [{"row": r["row"]} for r in pv["rows"]], "verified": True}).json()
    assert done["imported"] == 4
    cid = pv["rows"][0]["card_id"]
    client.post("/api/pokemon/import/population", json={"rows": [{"card_id": cid, "grading_company": "PSA", "grade": "10", "count": 121}]})
    detail = client.get(f"/api/pokemon/cards/{cid}").json()
    head = detail["headline"]
    assert head["label"] == "PSA 10" and head["market_price"] == 300_000_00 and head["confidence"] == "MEDIUM"
    # 30 days ago the estimate rested on the 40-day-old $305,000 sale; three verified sales since: a defensible move
    assert head["market_cap"] == 300_000_00 * 121 and head["change_30d"] == -1.64
    assert head["change_90d"] is None and head["change_reasons"]["90d"] == "no estimate 90 days ago"
    assert [s["source"] for s in detail["recent_sales"]] == ["goldin"] * 4 and detail["recent_sales"][0]["verified_by"] == "manual"
    prov = client.get(f"/api/pokemon/cards/{cid}/provenance", params={"grade": "PSA 10"}).json()
    assert len(prov["included"]) == 4 and prov["algorithm"].startswith("price-v2") and prov["population"]["count"] == 121
    assert client.post("/api/pokemon/snapshot").json() == {"snapshots": 1}
    snap = db.scalar(select(PokemonPriceSnapshot))
    assert (snap.market_price_cents, snap.confidence, snap.population) == (300_000_00, "MEDIUM", 121)
    row = client.get("/api/pokemon/overview").json()["top"][0]
    assert (row["card"]["id"], row["label"], row["market_price"], row["rank"]) == (cid, "PSA 10", 300_000_00, 1)
    assert client.get("/api/pokemon/overview").json()["sections"]["gainers"] == []  # one snapshot: no rankings from sparse data
    hist = client.get(f"/api/pokemon/cards/{cid}/history", params={"grade": "PSA 10", "range": "3m"}).json()
    assert len(hist["sales"]) == 4 and len(hist["line"]) == 1


def test_headline_prefers_psa_10_and_says_so(db: Session) -> None:
    c = find(db, "Blastoise", "1st Edition")
    service.import_sales(db, [{"card_id": c.id, "grading_company": "PSA", "grade": "9", "price": 5000, "sale_date": day(2), "source_listing_id": "a"},
                              {"card_id": c.id, "grading_company": "PSA", "grade": "10", "price": 40000, "sale_date": day(3), "source_listing_id": "b"}],
                         verified=True)  # TEST FIXTURE
    row = next(r for r in service.overview(db)["top"] if r["card"]["id"] == c.id)
    assert row["label"] == "PSA 10" and row["market_price"] == 40_000_00


def test_review_queue_api(client: TestClient, db: Session) -> None:
    db.add(PokemonListing(provider="ebay", kind="active", listing_id="R1", title="Charizard Base Set PSA 9", price_cents=1, currency="USD",
                          observed_at=utcnow(), match_status="review", match_reasons=["edition not stated"]))
    db.commit()
    items = client.get("/api/pokemon/review").json()["items"]
    assert [i["listing_id"] for i in items] == ["R1"]
    assert client.post(f"/api/pokemon/review/{items[0]['id']}", json={"reject": True}).json()["match_status"] == "rejected"
    assert client.get("/api/pokemon/match", params={"title": "1999 Base Set Charizard 4/102 1st Edition PSA 10"}).json()["status"] == "matched"


# --- the dashboard reads exactly these fields (frontend pages/pokemon/api.ts) ----------------------------------------------

ROW_FIELDS = {"card", "grading_company", "grade", "label", "market_price", "confidence", "change_30d", "change_90d", "population",
              "market_cap", "sales_30d", "volume_30d", "last_sale", "last_sale_at"}
VALUATION_FIELDS = {"grading_company", "grade", "label", "graded", "market_price", "confidence", "change_30d", "change_90d", "stats",
                    "population", "population_source", "population_as_of", "market_cap", "sales_7d", "volume_7d", "sales_30d", "volume_30d"}


def test_api_field_names_match_what_the_dashboard_reads(client: TestClient, db: Session) -> None:
    """TEST FIXTURE sale (test DB only): with data present, every field the table and card page render is filled, not '—'."""
    c = find(db, "Charizard", "1st Edition")
    service.import_sales(db, [{"card_id": c.id, "grading_company": "PSA", "grade": "10", "price": 300000, "sale_date": day(10),
                               "source": "goldin", "source_listing_id": "T-1", "url": "https://example.test/T-1"}], verified=True)
    service.import_population(db, [{"card_id": c.id, "grading_company": "PSA", "grade": "10", "count": 121}])
    service.snapshot(db, utcnow() - timedelta(days=8))  # an earlier observation, so 7d / 30d moves can exist
    ov = client.get("/api/pokemon/overview").json()
    row = next(r for r in ov["top"] if r["card"]["id"] == c.id)
    assert ROW_FIELDS <= set(row)
    assert (row["market_price"], row["last_sale"], row["sales_30d"], row["volume_30d"], row["population"], row["market_cap"]) == \
        (300_000_00, 300_000_00, 1, 300_000_00, 121, 300_000_00 * 121)
    # one sale 10 days ago: no estimate 30 days ago -> no move (not 0%, not a guess)
    assert row["change_30d"] is None and row["change_reasons"]["30d"] == "no estimate 30 days ago"
    assert ov["hero"]["sales_tracked"] == 1 and ov["hero"]["total_market_cap"] == 300_000_00 * 121
    d = client.get(f"/api/pokemon/cards/{c.id}").json()
    assert VALUATION_FIELDS <= set(d["headline"]) and d["headline"]["stats"]["last_sale"] == 300_000_00
    assert {"last_sale", "last_sale_at", "sales_30d", "volume_30d", "confidence", "confidence_reasons"} <= set(d["headline"]["stats"])


def test_test_connection_reports_the_truth(client: TestClient) -> None:
    r = client.post("/api/pokemon/sources/ebay/test").json()
    assert r == {"ok": False, "message": "Not connected: add EBAY_CLIENT_ID, EBAY_CLIENT_SECRET."}
    assert client.post("/api/pokemon/sources/manual/test").json()["ok"] is True
    assert client.post("/api/pokemon/sources/nope/test").status_code == 404


def test_partial_success_counts_its_records(db: Session) -> None:
    service.record_source(db, "pokemontcg", 16, "The Pokémon TCG API answered HTTP 500.")
    st = {s["key"]: s for s in service.sources(db, Settings())}["pokemontcg"]
    assert st["records_imported"] == 16 and st["last_success_at"] and st["last_error"]
