"""Whole-catalogue import from Scrydex (set listings, 100 cards per credit): every card, English and Japanese kept apart,
existing cards never duplicated, provider prices for the market's grades only, weekly refresh, market cap by year.
Payloads are TEST FIXTURES shaped like Scrydex's documented responses; the test database only."""

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.pokemon import PokemonCard, PokemonPopulation, PokemonProviderValuation
from app.pokemon import catalog
from app.pokemon.bulk import base_pokemon, printings
from tests.test_scrydex import graded, pop, scrydex  # noqa: F401  (fixture)

EXPANSIONS = [  # TEST FIXTURES
    {"id": "base1", "name": "Base", "language_code": "EN", "release_date": "1999/01/09", "total": 102, "printed_total": 102},
    {"id": "sv3pt5", "name": "151", "language_code": "EN", "release_date": "2023/09/22", "total": 207, "printed_total": 165},
    {"id": "sv2a_ja", "name": "ポケモンカード151", "language_code": "JA", "release_date": "2023/06/16", "total": 2,
     "translation": {"en": {"name": "Pokémon Card 151"}}},
    {"id": "tcgp-A1", "name": "Genetic Apex", "language_code": "EN", "release_date": "2024/10/30", "total": 286, "is_online_only": True},
]
SET_CARDS = {  # TEST FIXTURES
    "base1": [{"id": "base1-4", "name": "Charizard", "number": "4", "supertype": "Pokémon", "language_code": "EN", "variants": [
        {"name": "firstEditionShadowlessHolofoil", "prices": [graded("PSA", "10", 1.0)], "pop_reports": []}]}],
    "sv3pt5": [
        {"id": "sv3pt5-199", "name": "Charizard ex", "number": "199", "supertype": "Pokémon", "language_code": "EN",
         "images": [{"type": "front", "large": "https://images.scrydex.test/sv3pt5-199/large"}], "variants": [
             {"name": "holofoil", "prices": [graded("PSA", "10", 1_200.0), graded("PSA", "9", 300.0), graded("PSA", "6", 50.0),
                                             graded("PSA", "10", 9_999.0, is_signed=True),
                                             {"type": "raw", "condition": "NM", "market": 40.0, "currency": "USD"},
                                             {"type": "raw", "condition": "DM", "market": 9.0, "currency": "USD"}], "pop_reports": pop(4_000, 9_000)},
             {"name": "reverseHolofoil", "prices": [graded("PSA", "10", 80.0)]}]},
        {"id": "sv3pt5-1", "name": "Bulbasaur", "number": "1", "supertype": "Pokémon", "language_code": "EN",
         "variants": [{"name": "normal", "prices": []}, {"name": "reverseHolofoil", "prices": []}]},
        {"id": "sv3pt5-166", "name": "Switch", "number": "166", "supertype": "Trainer", "language_code": "EN", "variants": [{"name": "normal"}]}],
    "sv2a_ja": [{"id": "sv2a_ja-201", "name": "リザードンex", "number": "201", "supertype": "Pokémon", "language_code": "JA",
                 "translation": {"en": {"name": "Charizard ex"}}, "variants": [{"name": "holofoil", "prices": [graded("PSA", "10", 700.0)]}]},
                {"id": "sv3pt5-999", "name": "Mew", "number": "999", "language_code": "EN", "variants": []}],  # an English card in a Japanese set: never imported
}


def api(calls: list[str]) -> httpx.MockTransport:
    def handle(r: httpx.Request) -> httpx.Response:
        calls.append(r.url.path)
        if r.url.path == "/account/v1/usage":
            return httpx.Response(200, json={"data": {"credits_remaining": 49_000}})
        if r.url.path == "/pokemon/v1/expansions":
            return httpx.Response(200, json={"data": EXPANSIONS, "total_count": len(EXPANSIONS)})
        if r.url.path.startswith("/pokemon/v1/expansions/") and r.url.path.endswith("/cards"):
            assert r.url.params["include"] == "prices,pop_reports"
            rows = SET_CARDS.get(r.url.path.split("/")[4], [])
            return httpx.Response(200, json={"data": rows, "total_count": len(rows)})
        return httpx.Response(404, json={"message": "not found"})
    return httpx.MockTransport(handle)


def test_vintage_reverse_holos_are_their_own_printing() -> None:
    lc = printings([{"name": "normal"}, {"name": "reverseHolofoil"}], vintage=True)  # Legendary Collection (2002)
    assert [(e, lab, h) for _, e, lab, h in lc] == [("—", "", "Non-Holo"), ("—", "Reverse Holo", "Reverse Holo")]
    modern = printings([{"name": "normal"}, {"name": "reverseHolofoil"}])  # modern sets: reverse holos stay out
    assert [lab for _, _, lab, _ in modern] == [""]


def test_names_and_printings() -> None:
    assert [base_pokemon(n, "Pokémon") for n in ("Charizard ex", "M Charizard-EX", "Radiant Charizard", "Blaine's Charizard", "Pikachu VMAX")] == ["Charizard"] * 4 + ["Pikachu"]
    assert base_pokemon("Professor's Research", "Trainer") == "Professor's Research"
    ps = printings([{"name": "firstEditionHolofoil"}, {"name": "unlimitedHolofoil"}, {"name": "reverseHolofoil"}, {"name": "firstEditionStamped"}])
    assert [(v["name"], e) for v, e, _, _ in ps] == [("firstEditionHolofoil", "1st Edition"), ("unlimitedHolofoil", "Unlimited")]
    both = printings([{"name": "normal"}, {"name": "holofoil"}])
    assert [(e, lab) for _, e, lab, _ in both] == [("—", "Non-Holo"), ("—", "Holo")]  # told apart, never merged


def test_catalogue_import(client: TestClient, db: Session, scrydex: list[str], monkeypatch) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    runtime = client.app.state.runtime  # type: ignore[attr-defined]
    calls: list[str] = []
    monkeypatch.setattr(runtime, "pokemon_transport", api(calls), raising=False)
    catalog.ensure_catalog(db)
    before = db.scalar(select(PokemonCard).where(PokemonCard.name == "Charizard", PokemonCard.edition == "1st Edition", PokemonCard.set_name == "Base Set"))
    n_before = len(db.scalars(select(PokemonCard)).all())

    out = client.post("/api/pokemon/sources/scrydex/catalog-sync", json={}).json()
    assert out["status"] == "ok" and [e["expansion"] for e in out["entries"]] == ["base1", "sv2a_ja", "sv3pt5"]  # oldest first, online-only skipped
    s = out["summary"]
    assert s["cards_created"] == 4 and s["requests"] == 5  # usage + expansions + 3 set pages

    cards = {c.name: c for c in db.scalars(select(PokemonCard).where(PokemonCard.set_code.in_(["sv3pt5", "sv2a_ja"])))}
    assert set(cards) == {"Charizard ex", "Bulbasaur", "Switch"}  # one row per card name here; Japanese one checked below
    zard = cards["Charizard ex"]
    assert (zard.pokemon_name, zard.set_name, zard.card_number, zard.year, zard.era, zard.language) == ("Charizard", "151", "199/165", 2023, "modern", "English")
    assert zard.image_url == "https://images.scrydex.test/sv3pt5-199/large" and zard.external_ids["source"] == "scrydex-catalog"
    vals = {(v.grading_company, v.grade): v.market_cents for v in db.scalars(select(PokemonProviderValuation).where(PokemonProviderValuation.card_id == zard.id))}
    # every grade and the raw (ungraded) conditions, for portfolio holdings; signed and reverse-holo prices ignored
    assert vals == {("PSA", "10"): 1_200_00, ("PSA", "9"): 300_00, ("PSA", "6"): 50_00, ("RAW", "NM"): 40_00, ("RAW", "DMG"): 9_00}
    assert {(p.grade, p.count) for p in db.scalars(select(PokemonPopulation).where(PokemonPopulation.card_id == zard.id))} == {("10", 4_000), ("9", 9_000)}

    ja = db.scalars(select(PokemonCard).where(PokemonCard.set_code == "sv2a_ja")).all()
    assert [(c.name, c.language, c.set_name, c.external_ids["name_ja"]) for c in ja] == [("Charizard ex", "Japanese", "Japanese Pokémon Card 151", "リザードンex")]
    assert db.scalar(select(PokemonCard).where(PokemonCard.name == "Mew")) is None  # English card inside a Japanese set: skipped

    # the hand-curated Base Set Charizard was recognised, not duplicated or repriced
    assert len(db.scalars(select(PokemonCard).where(PokemonCard.set_code == "base1", PokemonCard.name == "Charizard")).all()) == \
        len([c for c in db.scalars(select(PokemonCard)) if c.set_code == "base1" and c.name == "Charizard" and c.id <= max(before.id, n_before)])
    assert not db.scalars(select(PokemonProviderValuation).where(PokemonProviderValuation.card_id == before.id)).all()

    # within 7 days every set is up to date: nothing is fetched again
    calls.clear()
    again = client.post("/api/pokemon/sources/scrydex/catalog-sync", json={}).json()
    assert again["summary"]["sets"] == 0 and not any(c.endswith("/cards") for c in calls)

    # market: Japanese / English kept apart; the market cap by year
    jp = client.get("/api/pokemon/market?era=all&language=Japanese&limit=250").json()["rows"]
    assert all(r["card"]["language"] == "Japanese" for r in jp) and any(r["card"]["name"] == "Charizard ex" for r in jp)
    years = client.get("/api/pokemon/years?grade=PSA 10").json()
    y2023 = next(y for y in years["years"] if y["year"] == 2023)
    assert y2023["market_cap"] == 1_200_00 * 4_000 and y2023["languages"] == ["English", "Japanese"] and y2023["top"][0]["card"]["name"] == "Charizard ex"
    assert [y["year"] for y in years["years"]] == sorted((y["year"] for y in years["years"]), reverse=True)
    only_ja = client.get("/api/pokemon/years?grade=PSA 10&language=Japanese").json()
    assert {y["year"] for y in only_ja["years"]} <= {1996, 1998, 2000, 2023}

    # Pulse never links trainer cards
    from app.pokemon.pulse import linked_cards
    assert linked_cards("Switch it up with a Charizard ex pull", db.scalars(select(PokemonCard)).all())
    assert all(c.name != "Switch" for c in linked_cards("Switch it up with a Charizard ex pull", db.scalars(select(PokemonCard)).all()))


def test_same_name_promos_without_numbers_stay_apart(client: TestClient, db: Session, scrydex: list[str], monkeypatch) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    runtime = client.app.state.runtime  # type: ignore[attr-defined]
    calls: list[str] = []
    monkeypatch.setattr(runtime, "pokemon_transport", api(calls), raising=False)
    SET_CARDS["miscp_ja"] = [{"id": f"miscp_ja-{i}", "name": "ピカチュウ", "language_code": "JA", "translation": {"en": {"name": "Pikachu"}},
                              "variants": [{"name": "holofoil", "prices": []}]} for i in (1, 4)] + [
        {"id": "base1_ja-21", "name": "リザードン", "number": "6", "printed_number": "No.006", "language_code": "JA", "translation": {"en": {"name": "Charizard"}},
         "variants": [{"name": "holofoil"}]}]
    EXPANSIONS.append({"id": "miscp_ja", "name": "Unnumbered Promos", "language_code": "JA", "release_date": "1996/01/01", "total": 3,
                       "translation": {"en": {"name": "Unnumbered Promos"}}})
    try:
        catalog.ensure_catalog(db)
        client.post("/api/pokemon/sources/scrydex/catalog-sync", json={"set_ids": ["miscp_ja"]})
        rows = db.scalars(select(PokemonCard).where(PokemonCard.set_code == "miscp_ja")).all()
        assert sorted(c.name for c in rows) == ["Charizard", "Pikachu", "Pikachu"]
        assert {c.set_name for c in rows} == {"Japanese Unnumbered Promos"}  # not the catalog's "CoroCoro Comic Promo"
        assert next(c for c in rows if c.name == "Charizard").card_number == "No.006"  # printed, not the Pokédex number over a set size
    finally:
        EXPANSIONS.pop()
        SET_CARDS.pop("miscp_ja")
