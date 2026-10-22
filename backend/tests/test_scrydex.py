"""Scrydex: credentials from the key store, exact variant matching, provider valuations kept apart from sales and labelled,
PSA population per grade, market cap only with population, request cap, cache, per-card sync log. Payloads below are
TEST FIXTURES shaped like Scrydex's documented responses; the test database only."""

import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config.settings import Settings
from app.models.pokemon import PokemonCard, PokemonProviderValuation, PokemonSale
from app.pokemon import catalog
from app.providers.pokemon.scrydex import ScrydexProvider, match_variant

BASE = "https://api.scrydex.test"


def graded(company: str, grade: str, market: float, **kw) -> dict:  # type: ignore[type-arg]
    return {"type": "graded", "company": company, "grade": grade, "is_perfect": False, "is_signed": False, "is_error": False,
            "low": market * 0.9, "mid": market, "high": market * 1.1, "market": market, "currency": "USD",
            "trends": {"days_1": {"price_change": 0, "percent_change": 0.0}, "days_7": {"price_change": 10, "percent_change": 2.5},
                       "days_30": {"price_change": -5, "percent_change": -1.2}}, **kw}  # TEST FIXTURE


def pop(n10: int, n9: int) -> list[dict]:  # type: ignore[type-arg]
    return [{"company": "PSA", "total": n10 + n9 + 50, "grade_total": n10 + n9, "grades": [{"grade": "10", "count": n10}, {"grade": "9", "count": n9}]}]


CARDS = {  # TEST FIXTURES
    "base1-4": {"id": "base1-4", "name": "Charizard", "number": "4", "expansion": {"id": "base1", "name": "Base"}, "language_code": "EN", "variants": [
        {"name": "unlimitedHolofoil", "prices": [graded("PSA", "10", 12_000.0)], "pop_reports": pop(900, 4000)},
        {"name": "firstEditionShadowlessHolofoil", "prices": [graded("PSA", "10", 400_000.0), graded("PSA", "9", 60_000.0), graded("PSA", "10", 1.0, is_signed=True),
                                                              {"type": "raw", "condition": "NM", "market": 9000.0, "currency": "USD"}],
         "pop_reports": pop(121, 1500)},
        {"name": "unlimitedShadowlessHolofoil", "prices": [graded("PSA", "10", 50_000.0)], "pop_reports": []}]},
    "base1-2": {"id": "base1-2", "name": "Blastoise", "number": "2", "expansion": {"id": "base1"}, "variants": [
        {"name": "firstEditionShadowlessHolofoil", "prices": [graded("PSA", "10", 60_000.0)], "pop_reports": []},
        {"name": "unlimitedHolofoil", "prices": [], "pop_reports": []}]},
    "neo1-9": {"id": "neo1-9", "name": "Lugia", "number": "9", "expansion": {"id": "neo1"}, "variants": [
        {"name": "firstEditionHolofoil", "prices": [graded("PSA", "10", 150_000.0)], "pop_reports": pop(45, 300)},
        {"name": "unlimitedHolofoil", "prices": [graded("PSA", "10", 9_000.0)], "pop_reports": pop(300, 2000)}]},
}
HISTORY = {"data": [{"date": "2026/09/01", "prices": [{"variant": "firstEditionShadowlessHolofoil", "type": "graded", "company": "PSA", "grade": "10",
                                                       "low": 380_000.0, "market": 390_000.0, "currency": "USD"}]},
                    {"date": "2026/09/15", "prices": [{"variant": "firstEditionShadowlessHolofoil", "type": "graded", "company": "PSA", "grade": "10",
                                                       "low": 385_000.0, "market": 395_000.0, "currency": "USD"},
                                                      {"variant": "unlimitedHolofoil", "type": "graded", "company": "PSA", "grade": "10", "market": 1.0, "currency": "USD"}]}]}


def api(calls: list[str], status: int = 200) -> httpx.MockTransport:
    def handle(r: httpx.Request) -> httpx.Response:
        calls.append(r.url.path)
        assert r.headers["x-api-key"] == "key-1" and r.headers["x-team-id"] == "team-1"
        if status != 200:
            return httpx.Response(status, json={"error": {"message": "Invalid credentials", "code": "INVALID_CREDENTIALS"}})
        if r.url.path == "/account/v1/usage":
            return httpx.Response(200, json={"data": {"total_credits": 50_000, "remaining_credits": 49_000}})
        if r.url.path.endswith("/price_history"):
            return httpx.Response(200, json=HISTORY)
        cid = r.url.path.rsplit("/", 1)[-1]
        if r.url.path.startswith("/pokemon/v1/cards/") and cid in CARDS:
            assert r.url.params["include"] == "prices,pop_reports"
            return httpx.Response(200, json={"data": CARDS[cid]})
        return httpx.Response(404, json={"message": "not found"})
    return httpx.MockTransport(handle)


@pytest.fixture
def scrydex(client: TestClient, tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    from app.security.credentials import secret_store

    runtime = client.app.state.runtime  # type: ignore[attr-defined]
    calls: list[str] = []
    monkeypatch.setattr(runtime, "pokemon_transport", api(calls), raising=False)
    monkeypatch.setattr(runtime.settings, "scrydex_api_base", BASE)
    monkeypatch.setattr(runtime.settings, "scrydex_cache_dir", tmp_path)
    monkeypatch.setattr(ScrydexProvider, "backoff", 0.0)
    store = secret_store(runtime.settings)
    store.set("scrydex_api_key", "key-1")
    store.set("scrydex_team_id", "team-1")
    return calls


def _id(db: Session, name: str, edition: str, set_name: str = "Base Set") -> int:
    cid = db.scalar(select(PokemonCard.id).where(PokemonCard.name == name, PokemonCard.edition == edition, PokemonCard.set_name == set_name))
    assert cid is not None
    return cid


def test_variant_matching_is_exact_or_nothing() -> None:
    vs = CARDS["base1-4"]["variants"]
    assert match_variant(vs, "1st Edition", "base1", "Holo")[0]["name"] == "firstEditionShadowlessHolofoil"
    assert match_variant(vs, "Shadowless", "base1", "Holo")[0]["name"] == "unlimitedShadowlessHolofoil"
    assert match_variant(vs, "Unlimited", "base1", "Holo")[0]["name"] == "unlimitedHolofoil"
    assert match_variant([{"name": "normal"}], "Unlimited", "pop5", "Gold Star")[0]["name"] == "normal"  # gold star: its only printing
    assert match_variant([{"name": "normal"}, {"name": "firstEditionNormal"}], "Unlimited", "x", "Holo")[0] is None  # editions exist: no shortcut
    assert match_variant([{"name": "normal"}, {"name": "reverseHolofoil"}, {"name": "holofoil"}], "Unlimited", "x", "Holo")[0]["name"] == "holofoil"
    real = [{"name": n} for n in ("firstEditionShadowlessHolofoil", "jumbo", "metal", "unlimitedFourthPrintHolofoil", "unlimitedHolofoil", "unlimitedShadowlessHolofoil")]
    assert match_variant(real, "Unlimited", "base1", "Holo")[0]["name"] == "unlimitedHolofoil"  # the real base1-4 list: special prints excluded
    assert match_variant(real, "1st Edition", "base1", "Holo")[0]["name"] == "firstEditionShadowlessHolofoil"
    v, why = match_variant([{"name": "firstEditionHolofoil"}, {"name": "firstEditionShadowlessHolofoil"}], "1st Edition", "base1", "Holo")
    assert v is None and why.startswith("ambiguous")
    assert match_variant([{"name": "unlimitedHolofoil"}], "1st Edition", "neo1", "Holo")[0] is None


def test_three_card_sync_stores_labelled_valuations_population_and_history(client: TestClient, db: Session, scrydex: list[str]) -> None:
    catalog.ensure_catalog(db)
    ids = [_id(db, "Charizard", "1st Edition"), _id(db, "Blastoise", "1st Edition"), _id(db, "Lugia", "1st Edition", "Neo Genesis")]
    r = client.post("/api/pokemon/sources/scrydex/sync", json={"card_ids": ids, "history": True})
    assert r.status_code == 200, r.text
    log = r.json()
    assert "key-1" not in r.text and "team-1" not in r.text  # credentials never returned
    assert log["status"] == "ok" and log["summary"]["matched_variants"] == 3 and log["summary"]["unmatched"] == 0
    assert log["summary"]["usage_before"]["remaining_credits"] == 49_000
    # 1 usage + 3 cards + 3 PSA 10 histories = 7 requests (1 credit each)
    assert log["requests"] == 7 and sorted(scrydex).count("/account/v1/usage") == 1
    zard = next(e for e in log["entries"] if e["scrydex_id"] == "base1-4")
    assert zard["variant"] == "firstEditionShadowlessHolofoil" and zard["prices"]["PSA 10"]["market"] == 400_000.0
    assert zard["population"] == {"PSA 10": 121, "PSA 9": 1500} and zard["history_points"] == 2

    assert db.scalar(select(func.count(PokemonSale.id))) == 0  # provider valuations are never sales
    rows = {(v.card_id, v.grade, v.kind): v for v in db.scalars(select(PokemonProviderValuation))}
    assert rows[(ids[0], "10", "current")].market_cents == 400_000_00 and rows[(ids[0], "10", "current")].provider_variant == "firstEditionShadowlessHolofoil"
    assert not any(v.market_cents == 1_00 for v in rows.values())  # signed card / other variant's history never mixed in

    m = client.get("/api/pokemon/market?era=all").json()
    by = {row["card"]["id"]: row for row in m["rows"]}
    z, b, lug = by[ids[0]], by[ids[1]], by[ids[2]]
    assert (z["market_price"], z["price_source"], z["provider"]["name"]) == (400_000_00, "scrydex", "Scrydex")
    assert z["market_cap"] == 400_000_00 * 121 and z["population"] == 121 and z["change_30d"] == -1.2
    assert z["change_sources"]["30d"] == "scrydex" and z["change_90d"] is None and "no 90 days trend" in z["change_reasons"]["90d"]
    assert b["market_price"] == 60_000_00 and b["population"] is None and b["market_cap"] is None  # no PSA population: no cap
    assert lug["market_cap"] == 150_000_00 * 45 and z["rank"] == 1 and lug["rank"] == 2
    ch = client.get(f"/api/pokemon/cards/{ids[0]}/chart?range=all").json()
    assert ch["summary"]["price_source"] == "scrydex" and ch["summary"]["market_price"] == 400_000_00 and ch["line"] == []
    assert [p["v"] for p in ch["provider_line"]] == [390_000_00, 395_000_00, 400_000_00]
    cmp = client.get(f"/api/pokemon/compare?series={ids[0]}:PSA 10&series={ids[0]}:PSA 9&series={ids[0]}:PSA 8&range=all").json()["series"]
    assert [(s["grade"], s["source"], len(s["line"])) for s in cmp] == [("PSA 10", "scrydex", 3), ("PSA 9", "scrydex", 1), ("PSA 8", None, 0)]
    logs = client.get("/api/pokemon/sync-logs?provider=scrydex").json()["logs"]
    assert logs[0]["id"] == log["id"]

    again = client.post("/api/pokemon/sources/scrydex/sync", json={"card_ids": ids, "history": True}).json()
    assert again["requests"] == 1 and again["cache_hits"] == 6  # only the usage check is live; cards + history from cache


def test_request_cap_and_auth_errors_are_reported(client: TestClient, db: Session, scrydex: list[str], monkeypatch) -> None:  # type: ignore[no-untyped-def]
    catalog.ensure_catalog(db)
    ids = [_id(db, "Charizard", "1st Edition"), _id(db, "Blastoise", "1st Edition"), _id(db, "Lugia", "1st Edition", "Neo Genesis")]
    capped = client.post("/api/pokemon/sources/scrydex/sync", json={"card_ids": ids, "history": False, "max_requests": 2}).json()
    assert capped["requests"] == 2 and "request cap" in capped["error"] and capped["status"] == "partial"
    runtime = client.app.state.runtime  # type: ignore[attr-defined]
    monkeypatch.setattr(runtime, "pokemon_transport", api([], status=401))
    import shutil

    shutil.rmtree(runtime.settings.scrydex_cache_dir, ignore_errors=True)
    r = client.post("/api/pokemon/sources/scrydex/sync", json={"card_ids": ids})
    assert r.status_code == 200 and r.json()["status"] == "error" and "HTTP 401: INVALID_CREDENTIALS Invalid credentials" in r.json()["error"]


def test_without_credentials_nothing_is_requested(client: TestClient) -> None:
    r = client.post("/api/pokemon/sources/scrydex/sync", json={"card_ids": [1]})
    assert r.status_code == 400 and "SCRYDEX_API_KEY" in r.json()["detail"]
    test = client.post("/api/pokemon/sources/scrydex/test").json()
    assert test["ok"] is False and "SCRYDEX_API_KEY" in test["message"]
    assert asyncio.iscoroutinefunction(ScrydexProvider.card)


def test_data_quality_puts_sources_side_by_side(client: TestClient, db: Session, scrydex: list[str]) -> None:
    from app.models.pokemon import PokemonReferencePrice
    from app.db.base import utcnow

    catalog.ensure_catalog(db)
    ids = [_id(db, "Charizard", "1st Edition"), _id(db, "Blastoise", "1st Edition"), _id(db, "Lugia", "1st Edition", "Neo Genesis")]
    client.post("/api/pokemon/sources/scrydex/sync", json={"card_ids": ids, "history": False})
    db.add(PokemonReferencePrice(provider="pricecharting", card_id=ids[0], provider_grade="PSA 10", grading_company="PSA", grade="10",
                                 price_cents=300_000_00, currency="USD", as_of=utcnow().date()))  # TEST FIXTURE
    db.add(PokemonReferencePrice(provider="pricecharting", card_id=ids[1], provider_grade="PSA 10", grading_company="PSA", grade="10",
                                 price_cents=58_000_00, currency="USD", as_of=utcnow().date()))  # TEST FIXTURE
    db.commit()
    q = client.get("/api/pokemon/quality?grade=PSA 10").json()
    by = {r["card"]["id"]: r for r in q["rows"]}
    assert by[ids[0]]["gap_pct"] == round((400_000_00 - 300_000_00) / 300_000_00 * 100, 1) and "sources disagree" in by[ids[0]]["flags"]
    assert "sources disagree" not in by[ids[1]]["flags"] and "no population" in by[ids[1]]["flags"]  # 60k vs 58k: agree
    assert "single source" in by[ids[2]]["flags"] and q["rows"][0]["card"]["id"] == ids[0]  # worst gap first
    assert q["summary"]["both_sources"] == 2 and q["summary"]["disagree"] == 1


def test_scrydex_is_the_main_price_and_pricecharting_only_a_backup(client: TestClient, db: Session, scrydex: list[str]) -> None:
    from app.db.base import utcnow
    from app.models.pokemon import PokemonReferencePrice

    catalog.ensure_catalog(db)
    zard, blast = _id(db, "Charizard", "1st Edition"), _id(db, "Blastoise", "1st Edition")
    client.post("/api/pokemon/sources/scrydex/sync", json={"card_ids": [zard], "history": True})
    for cid, cents in ((zard, 330_000_00), (blast, 58_000_00)):  # TEST FIXTURES: PriceCharting values
        db.add(PokemonReferencePrice(provider="pricecharting", card_id=cid, provider_grade="PSA 10", grading_company="PSA", grade="10",
                                     price_cents=cents, currency="USD", as_of=utcnow().date()))
    db.commit()
    rows = {r["card"]["id"]: r for r in client.get("/api/pokemon/market?era=all").json()["rows"]}
    assert (rows[zard]["price_source"], rows[zard]["market_price"]) == ("scrydex", 400_000_00)  # Scrydex wins where it has a value
    assert rows[zard]["reference"]["price"] == 330_000_00
    assert (rows[blast]["price_source"], rows[blast]["market_price"]) == ("pricecharting", 58_000_00)  # backup: no Scrydex value
    assert rows[blast]["change_30d"] is None and rows[blast]["sparkline"] is None

    ch = client.get(f"/api/pokemon/cards/{zard}/chart?range=all").json()
    assert ch["summary"]["price_source"] == "scrydex" and len(ch["provider_line"]) >= 2  # the Scrydex history chart is back


# --- Japanese vintage ---------------------------------------------------------------------------------------------------

JA_CARDS = {  # TEST FIXTURES shaped like Scrydex's Japanese cards
    "base1_ja-21": {"id": "base1_ja-21", "name": "リザードン", "number": "6", "printed_number": "No.006", "language_code": "JA",
                    "translation": {"en": {"name": "Charizard"}}, "expansion": {"id": "base1_ja", "language_code": "JA"}, "variants": [
        {"name": "holofoil", "prices": [graded("PSA", "10", 5_000.0), graded("PSA", "9", 1_200.0)], "pop_reports": []},
        {"name": "noRaritySymbolHolofoil", "prices": [graded("PSA", "10", 3_000.0), graded("PSA", "9", 25_000.0), graded("PSA", "8", 9_000.0)],
         "pop_reports": []}]},
    "neo1_ja-72": {"id": "neo1_ja-72", "name": "ルギア", "number": "249", "language_code": "JA", "translation": {"en": {"name": "Lugia"}},
                   "expansion": {"id": "neo1_ja", "language_code": "JA"}, "variants": [
        {"name": "holofoil", "prices": [graded("PSA", "10", 7_000.0)], "pop_reports": pop(10, 50)}]},
    "neo1_ja-9": {"id": "neo1_ja-9", "name": "Lugia", "number": "9", "language_code": "EN", "translation": {"en": {"name": "Lugia"}},
                  "expansion": {"id": "neo1_ja"}, "variants": [{"name": "holofoil", "prices": [graded("PSA", "10", 1.0)], "pop_reports": []}]},
}


def ja_api(calls: list[str]) -> httpx.MockTransport:
    def handle(r: httpx.Request) -> httpx.Response:
        calls.append(r.url.path)
        if r.url.path == "/account/v1/usage":
            return httpx.Response(200, json={"data": {"credits_remaining": 49_000}})
        if r.url.path.startswith("/pokemon/v1/expansions/") and r.url.path.endswith("/cards"):
            exp = r.url.path.split("/")[4]
            rows = [c for c in JA_CARDS.values() if c["expansion"]["id"] == exp and c["language_code"] == "JA"]
            rows += [{"id": "base1_ja-14", "name": "ヒトカゲ", "language_code": "JA", "translation": {"en": {"name": "Charmander"}}}] if exp == "base1_ja" else []
            return httpx.Response(200, json={"data": rows, "total_count": len(rows)})
        cid = r.url.path.rsplit("/", 1)[-1]
        if r.url.path.startswith("/pokemon/v1/cards/") and cid in JA_CARDS:
            return httpx.Response(200, json={"data": JA_CARDS[cid]})
        return httpx.Response(404, json={"message": "not found"})
    return httpx.MockTransport(handle)


def test_no_rarity_is_its_own_variant() -> None:
    vs = JA_CARDS["base1_ja-21"]["variants"]
    assert match_variant(vs, "—", None, "Holo", "No Rarity")[0]["name"] == "noRaritySymbolHolofoil"
    assert match_variant(vs, "—", None, "Holo", "")[0]["name"] == "holofoil"  # a regular card never takes the No Rarity print
    assert match_variant([vs[0]], "—", None, "Holo", "No Rarity")[0] is None


def test_japanese_cards_map_only_to_japanese_scrydex_cards(client: TestClient, db: Session, scrydex: list[str], monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.pokemon.scrydex_sync import scrydex_id

    runtime = client.app.state.runtime  # type: ignore[attr-defined]
    calls: list[str] = []
    monkeypatch.setattr(runtime, "pokemon_transport", ja_api(calls), raising=False)
    catalog.ensure_catalog(db)
    ja = {c.name: c for c in db.scalars(select(PokemonCard).where(PokemonCard.language == "Japanese"))}
    eng_lugia = _id(db, "Lugia", "1st Edition", "Neo Genesis")
    assert scrydex_id(ja["Charizard"]) is None  # not derived like English ids

    out = client.post("/api/pokemon/sources/scrydex/map-japanese", json={}).json()
    linked = {x["card"]: x["scrydex_id"] for x in out["linked"]}
    assert linked == {"Charizard": "base1_ja-21", "Lugia": "neo1_ja-72"}
    assert any(x["card"] == "Pikachu Illustrator" for x in out["unmatched"])  # no miscp_ja card in the fixtures: not guessed

    log = client.post("/api/pokemon/sources/scrydex/sync", json={"card_ids": [ja["Charizard"].id, ja["Lugia"].id], "history": False}).json()
    by = {e["card_id"]: e for e in log["entries"]}
    zard = by[ja["Charizard"].id]
    assert zard["status"] == "matched" and zard["variant"] == "noRaritySymbolHolofoil"
    assert "PSA 10" not in zard["prices"] and zard["prices"]["PSA 9"]["market"] == 25_000.0  # PSA 10 below PSA 9: not used
    assert any("PSA 10" in n for n in zard["not_used"])
    rows = db.scalars(select(PokemonProviderValuation).where(PokemonProviderValuation.card_id == ja["Charizard"].id)).all()
    assert sorted((r.grade, r.market_cents) for r in rows) == [("8", 9_000_00), ("9", 25_000_00)]
    assert by[ja["Lugia"].id]["prices"]["PSA 10"]["market"] == 7_000.0
    # nothing reached the English Lugia
    assert not db.scalars(select(PokemonProviderValuation).where(PokemonProviderValuation.card_id == eng_lugia)).all()

    # a Japanese card pointing at a card Scrydex reports as English is refused
    lugia = db.get(PokemonCard, ja["Lugia"].id)
    lugia.external_ids = {**lugia.external_ids, "scrydex": "neo1_ja-9"}
    db.commit()
    log = client.post("/api/pokemon/sources/scrydex/sync", json={"card_ids": [lugia.id], "history": False}).json()
    assert log["entries"][0]["status"] == "unmatched" and "language" in log["entries"][0]["reason"]

    jp = client.get("/api/pokemon/market?era=all&grade=PSA 9&language=Japanese").json()["rows"]
    assert {r["card"]["language"] for r in jp} == {"Japanese"}
    en = client.get("/api/pokemon/market?era=all&grade=PSA 9&language=English").json()["rows"]
    assert all(r["card"]["language"] == "English" for r in en)


def test_pricecharting_never_crosses_languages(db: Session) -> None:
    from app.pokemon.pricecharting_sync import console_for

    catalog.ensure_catalog(db)
    ja = db.scalars(select(PokemonCard).where(PokemonCard.language == "Japanese")).first()
    en = db.get(PokemonCard, _id(db, "Charizard", "1st Edition"))
    assert "japanese" in console_for(ja) and "japanese" not in console_for(en)
    ja.set_name = "Base Set"  # a Japanese card with an English-looking set name: no PriceCharting set at all
    assert console_for(ja) is None
    db.rollback()


def test_usd_jpy_display_rate_is_dated_and_cached(client: TestClient, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    runtime = client.app.state.runtime  # type: ignore[attr-defined]
    calls: list[str] = []

    def handle(r: httpx.Request) -> httpx.Response:
        calls.append(str(r.url))
        return httpx.Response(200, json={"amount": 1.0, "base": "USD", "date": "2026-10-07", "rates": {"JPY": 149.25}})  # TEST FIXTURE

    monkeypatch.setattr(runtime, "pokemon_transport", httpx.MockTransport(handle), raising=False)
    monkeypatch.setattr(runtime.settings, "tcgdex_cache_dir", tmp_path / "tcgdex")
    r = client.get("/api/pokemon/fx").json()["usd_jpy"]
    assert (r["rate"], r["rate_date"], r["stale"]) == (149.25, "2026-10-07", False) and "European Central Bank" in r["source"] and r["fetched_at"]
    client.get("/api/pokemon/fx")
    assert len(calls) == 1 and "from=USD" in calls[0] and "to=JPY" in calls[0]  # second call served from the cache
