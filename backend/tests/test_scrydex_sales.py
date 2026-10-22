"""Verified sales from Scrydex sold listings: only titles matched to the exact card + grade become sales; reprints and
other cards Scrydex attaches to the card are rejected; signed / error listings skipped; no duplicates; never asking prices.
Payloads are TEST FIXTURES shaped like Scrydex's documented listings; the test database only."""

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.pokemon import PokemonCard, PokemonListing, PokemonSale
from app.pokemon import catalog
from tests.test_scrydex import scrydex  # noqa: F401  (fixture)


def _l(i: str, title: str, price: float, sold: str = "2026/07/29", source: str = "ebay", **kw) -> dict:  # type: ignore[type-arg]
    return {"id": i, "source": source, "card_id": "base1-4", "title": title, "variant": "firstEditionShadowlessHolofoil", "company": "PSA", "grade": "10",
            "is_perfect": False, "is_error": False, "is_signed": False, "url": f"https://www.ebay.com/itm/{i}", "price": price, "currency": "USD",
            "sold_at": sold, **kw}  # TEST FIXTURE


LISTINGS = [
    _l("a1", "1999 Pokemon PSA 10 Base Set 1st Edition Charizard Holo", 450000.0),
    _l("a2", "1999 Pokemon Base Set Shadowless 1st Edition Holo Charizard #4 PSA 10 GEM MINT", 456000.0, "2026/04/17", "fanatics"),
    _l("b1", "2021 Pokemon Celebrations Classic Charizard Holo 4/102 PSA 10", 545.0, variant="metal"),  # a reprint Scrydex attached here
    _l("b2", "Pokemon TCG 2010 HGSS Triumphant Drapion Holo Rare 4/102 PSA 10", 8125.0),  # another card entirely
    _l("c1", "Pokemon 1999 Charizard Base Set Unlimited Holo Rare #4 PSA 10 Gem Mint", 37500.0, variant="unlimitedHolofoil"),
    _l("d1", "1999 Pokemon Base Set 1st Edition Charizard PSA 10 AUTO Mitsuhiro Arita", 600000.0, is_signed=True),
]


def api(calls: list[str]) -> httpx.MockTransport:
    def handle(r: httpx.Request) -> httpx.Response:
        calls.append(r.url.path)
        if r.url.path.endswith("/listings"):
            assert r.url.params["company"] == "PSA" and r.url.params["grade"] == "10"
            return httpx.Response(200, json={"data": LISTINGS, "total_count": len(LISTINGS)})
        return httpx.Response(404, json={"message": "not found"})
    return httpx.MockTransport(handle)


def _card(db: Session, name: str, edition: str) -> PokemonCard:
    c = db.scalar(select(PokemonCard).where(PokemonCard.name == name, PokemonCard.edition == edition, PokemonCard.set_name == "Base Set"))
    assert c is not None
    return c


def test_only_exact_matches_become_verified_sales(client: TestClient, db: Session, scrydex: list[str], monkeypatch) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    runtime = client.app.state.runtime  # type: ignore[attr-defined]
    calls: list[str] = []
    monkeypatch.setattr(runtime, "pokemon_transport", api(calls), raising=False)
    catalog.ensure_catalog(db)
    first, unl = _card(db, "Charizard", "1st Edition"), _card(db, "Charizard", "Unlimited")

    out = client.post("/api/pokemon/sources/scrydex/sales-sync", json={"card_ids": [first.id]}).json()
    assert out["status"] == "ok" and out["summary"]["listings"] == 6 and calls == ["/pokemon/v1/cards/base1-4/listings"]
    sales = {s.source_listing_id: s for s in db.scalars(select(PokemonSale))}
    assert {k: (v.card_id, v.grading_company, v.grade, v.sale_price_usd_cents, v.source, v.verified) for k, v in sales.items() if v.card_id == first.id} == {
        "scrydex-a1": (first.id, "PSA", "10", 450_000_00, "ebay", True),
        "scrydex-a2": (first.id, "PSA", "10", 456_000_00, "fanatics", True)}
    assert sales["scrydex-c1"].card_id == unl.id  # the Unlimited sale goes to the Unlimited card, never to the 1st Edition
    listing = {x.listing_id: x for x in db.scalars(select(PokemonListing))}
    assert listing["scrydex-b1"].match_status == "rejected" and listing["scrydex-b2"].match_status == "rejected"
    assert "scrydex-d1" not in listing  # signed: a different product, skipped
    assert all(x.kind == "sold" for x in listing.values())

    again = client.post("/api/pokemon/sources/scrydex/sales-sync", json={"card_ids": [first.id]}).json()
    assert again["summary"]["sales_imported"] == 0 and len(db.scalars(select(PokemonSale)).all()) == len(sales)  # no duplicates

    m = client.get(f"/api/pokemon/market?era=all&ids={first.id}").json()["rows"][0]
    assert m["price_source"] == "sales"  # verified sales now drive this card's price


def test_api_keys_refuse_the_app_id_as_cert_id(client: TestClient) -> None:
    from app.security.credentials import secret_store

    runtime = client.app.state.runtime  # type: ignore[attr-defined]
    secret_store(runtime.settings).set("ebay_client_id", "AVIGADSA-Pricecha-PRD-0123456789-abcdef01")
    r = client.put("/api/settings/api-keys/ebay_client_secret", json={"value": "AVIGADSA-Pricecha-PRD-0123456789-abcdef01"})
    assert r.status_code == 422 and "App ID" in r.json()["detail"]
    ok = client.put("/api/settings/api-keys/ebay_client_secret", json={"value": "PRD-0123456789ab-cdef-0123-4567-89ab"})
    assert ok.status_code == 200


def test_recheck_excludes_sales_the_matcher_no_longer_accepts(db: Session) -> None:
    from datetime import timedelta

    from app.db.base import utcnow
    from app.pokemon import scrydex_sales

    catalog.ensure_catalog(db)
    first = _card(db, "Charizard", "1st Edition")
    for i, title in enumerate(("1999 Pokemon Base Set 1st Edition Charizard Holo #4 PSA 10",  # fine
                               "1999 POKEMON DUTCH BASE SET 1ST EDITION #4/102 CHARIZARD HOLO RARE PSA 10")):  # a Dutch card, not this one
        db.add(PokemonSale(card_id=first.id, grading_company="PSA", grade="10", sale_price_cents=400_000_00, currency="USD", sale_price_usd_cents=400_000_00,
                           sale_date=utcnow() - timedelta(days=5 + i), source="ebay", verified=True, verified_by="source", title=title, fingerprint=f"rc{i}"))
    db.commit()
    out = scrydex_sales.recheck(db)
    assert out["checked"] == 2 and out["excluded"] == 1 and "DUTCH" in out["examples"][0]["title"]
    kept = db.scalars(select(PokemonSale).where(PokemonSale.excluded.is_(False))).all()
    assert [s.fingerprint for s in kept] == ["rc0"] and len(db.scalars(select(PokemonSale)).all()) == 2  # excluded, never deleted


def test_matcher_reads_hash_numbers_ampersands_and_rejects_reverse_holos(db: Session) -> None:
    from app.pokemon import matching

    p = matching.parse("2003 POKEMON EX DRAGON #100 CHARIZARD-HOLO PSA 10")
    assert (p.number_main, p.number) == ("100", "100")
    assert matching.norm("Ruby & Sapphire") == matching.norm("Ruby and Sapphire")
    catalog.ensure_catalog(db)
    cards = db.scalars(select(PokemonCard).where(PokemonCard.pokemon_name == "Charizard")).all()
    m = matching.match("1999 Pokemon Base Set Reverse Holo Charizard #4 PSA 10", cards)
    assert m.status == "rejected" and "reverse" in m.reasons[0]


def test_recheck_resolves_the_review_queue(db: Session) -> None:
    from app.db.base import utcnow
    from app.pokemon import scrydex_sales

    catalog.ensure_catalog(db)
    first = _card(db, "Charizard", "1st Edition")
    # held for review by an older matcher; the current one matches it exactly
    db.add(PokemonListing(provider="ebay", kind="sold", listing_id="scrydex-rv1", title="1999 Pokemon Base Set 1st Edition Holo Charizard #4 PSA 10",
                          price_cents=400_000_00, currency="USD", observed_at=utcnow(), sold_at=utcnow(), card_id=first.id, match_status="review",
                          match_reasons=["confidence 0.65 is below 0.85"]))
    db.add(PokemonListing(provider="ebay", kind="sold", listing_id="scrydex-rv2", title="1999 Pokemon Base Set Reverse Holo Charizard #4 PSA 10",
                          price_cents=500_00, currency="USD", observed_at=utcnow(), sold_at=utcnow(), card_id=first.id, match_status="review", match_reasons=[]))
    db.commit()
    out = scrydex_sales.recheck(db)
    assert (out["review_matched"], out["review_rejected"]) == (1, 1)
    sale = db.scalar(select(PokemonSale).where(PokemonSale.source_listing_id == "scrydex-rv1"))
    assert sale is not None and sale.card_id == first.id and sale.verified and sale.grade == "10"


def test_long_actions_run_in_the_background_and_report_their_result(client: TestClient, db: Session, scrydex: list[str], monkeypatch) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    import time

    runtime = client.app.state.runtime  # type: ignore[attr-defined]
    monkeypatch.setattr(runtime, "pokemon_transport", api([]), raising=False)
    catalog.ensure_catalog(db)
    first = _card(db, "Charizard", "1st Edition")
    started = client.post("/api/pokemon/sources/scrydex/sales-sync?background=true", json={"card_ids": [first.id]}).json()
    assert started["state"] in ("running", "done") and started["name"] == "scrydex-sales"
    for _ in range(100):
        st = client.get("/api/pokemon/tasks/scrydex-sales").json()
        if st["state"] != "running":
            break
        time.sleep(0.05)
    assert st["state"] == "done" and st["result"]["summary"]["sales_imported"] >= 2 and st["finished_at"]
    assert client.get("/api/pokemon/tasks/never-started").json() == {"name": "never-started", "state": "idle"}


def test_matcher_rejects_red_cheeks_pikachu_for_the_ordinary_card(db: Session) -> None:
    from app.pokemon import matching

    pika = PokemonCard(id=9001, name="Pikachu", pokemon_name="Pikachu", set_name="Base Set", set_code="base1", card_number="58/102", year=1999,
                       edition="1st Edition", rarity="Common", language="English", era="vintage", variant="")  # TEST FIXTURE (not stored)
    m = matching.match("1999 Pokemon Base Set 1st Edition Red Cheeks Pikachu #58 PSA 10", [pika])
    assert m.status == "rejected" and "red cheeks" in m.reasons[0]  # a distinct, far rarer printing the catalog doesn't track
    ok = matching.match("1999 Pokemon Base Set 1st Edition Pikachu #58 PSA 10 Gem Mint", [pika])
    assert ok.status == "matched" and ok.card_id == pika.id


def test_matcher_tells_vintage_reverse_foils_apart() -> None:
    from app.pokemon import matching

    common = dict(pokemon_name="Eevee", set_name="Legendary Collection", set_code="base6", card_number="74/110", year=2002, edition="—",
                  rarity="Common", language="English", era="vintage")
    plain = PokemonCard(id=9101, name="Eevee", variant="", **common)  # TEST FIXTURES (not stored)
    rev = PokemonCard(id=9102, name="Eevee", variant="Reverse Holo", **common)
    m = matching.match("2002 Legendary Collection Reverse Foil #74 Eevee BGS 7.5", [plain, rev])
    assert m.card_id == rev.id
    m = matching.match("2002 Pokemon Legendary Collection Eevee 74/110 PSA 9", [plain, rev])
    assert m.card_id == plain.id
