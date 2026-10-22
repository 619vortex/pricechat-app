"""PSA Public API: official endpoints only, certificate facts kept apart from card-wide population, structured catalog
matching by PSA's fields, caching, retries and plain reasons (incl. "account not approved")."""

import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.settings import Settings
from app.models.pokemon import PokemonCard, PokemonPopulation
from app.pokemon import catalog
from app.pokemon.psa import match_cert
from app.providers.pokemon import Unavailable, provider
from app.providers.pokemon.psa import PSAProvider, pop_grade

BASE = "https://api.psa.test/publicapi"
SETTINGS = Settings(psa_api_token=SecretStr("tok"), psa_api_base=BASE)  # TEST FIXTURE token

CERT = {"IsValidRequest": True, "ServerMessage": "Request successful",
        "PSACert": {"CertNumber": "11112222", "SpecID": 544027, "Year": "1999", "Brand": "POKEMON GAME", "CardNumber": "4",
                    "Subject": "CHARIZARD-HOLO", "Variety": "1ST EDITION", "CardGrade": "GEM MT 10", "TotalPopulation": 121,
                    "PopulationHigher": 0}}  # TEST FIXTURE (shape from PSA's swagger PublicPSACert)
POP = {"SpecID": 544027, "Description": "1999 POKEMON GAME 4 CHARIZARD-HOLO 1ST EDITION",
       "PSAPop": {"Total": 3600, "Auth": 2, "Grade10": 121, "Grade9": 900, "Grade9Q": 4, "Grade8_5": 15, "Grade8": 1100, "Grade1": 30}}  # TEST FIXTURE


@pytest.fixture(autouse=True)
def _no_cache() -> None:
    PSAProvider._cache.clear()
    PSAProvider.backoff = 0.0


def psa_api(calls: list[str], status: int = 200, body: object = None):  # type: ignore[no-untyped-def]
    def handle(r: httpx.Request) -> httpx.Response:
        calls.append(r.url.path)
        assert r.headers["authorization"] == "bearer tok"
        if status != 200:
            return httpx.Response(status, json=body)
        if "/cert/GetByCertNumber/" in r.url.path:
            return httpx.Response(200, json=CERT)
        if "/pop/GetPSASpecPopulation/544027" in r.url.path:
            return httpx.Response(200, json=POP)
        return httpx.Response(200, json={"IsValidRequest": True, "ServerMessage": "No data found"})
    return httpx.MockTransport(handle)


def test_pop_fields_map_to_plain_grades_only() -> None:
    assert [pop_grade(f) for f in ("Grade10", "Grade8_5", "Grade9Q", "Auth", "Total")] == ["10", "8.5", None, None, None]


def test_spec_population_is_card_wide_and_cached() -> None:
    calls: list[str] = []
    p = provider("psa", SETTINGS, psa_api(calls))
    out = asyncio.run(p.get_spec_population(544027))  # type: ignore[attr-defined]
    assert out["scope"] == "card" and out["total"] == 3600 and out["qualified"] == {"Grade9Q": 4}
    assert {r.grade: r.count for r in out["records"]} == {"10": 121, "9": 900, "8.5": 15, "8": 1100, "1": 30}
    asyncio.run(p.get_spec_population("544027"))  # type: ignore[attr-defined]
    assert calls == ["/publicapi/pop/GetPSASpecPopulation/544027"]  # second call served from the cache
    cert = asyncio.run(p.get_cert("1111-2222"))  # type: ignore[attr-defined]
    assert cert["scope"] == "certificate" and cert["spec_id"] == 544027 and cert["total_population"] == 121


@pytest.mark.parametrize(("status", "body", "code"), [
    (403, {"Message": "Access to this API is limited to approved customers."}, "not_approved"),
    (401, {"Message": "Unauthorized"}, "auth"),
    (429, "API calls quota exceeded! maximum admitted 100 per Day.", "rate_limited"),
    (500, {"Message": "An error has occurred."}, "unreachable"),
])
def test_failures_have_plain_reasons_and_outages_are_retried(status: int, body: object, code: str) -> None:
    calls: list[str] = []
    with pytest.raises(Unavailable) as exc:
        asyncio.run(provider("psa", SETTINGS, psa_api(calls, status, body)).get_cert("1"))  # type: ignore[attr-defined]
    assert exc.value.code == code
    assert len(calls) == (3 if status >= 500 else 1)  # only outages are retried; a refusal is final


def test_certs_match_the_exact_catalog_edition_or_wait_for_review(db: Session) -> None:
    catalog.ensure_catalog(db)
    cards = list(db.scalars(select(PokemonCard)))
    by = {(c.name, c.edition): c.id for c in cards if c.set_name == "Base Set"}
    base = {"year": "1999", "brand": "POKEMON GAME", "card_number": "4", "subject": "CHARIZARD-HOLO"}
    first = match_cert({**base, "variety": "1ST EDITION"}, cards)
    assert first.status == "matched" and first.card_id == by[("Charizard", "1st Edition")]
    assert match_cert({**base, "variety": "SHADOWLESS"}, cards).card_id == by[("Charizard", "Shadowless")]
    assert match_cert(base, cards).card_id == by[("Charizard", "Unlimited")]  # no edition on the label = unlimited print
    assert match_cert({**base, "card_number": "2", "variety": "1ST EDITION"}, cards).status == "not_in_catalog"
    eevee = {"year": "2000", "brand": "POKEMON JAPANESE PROMO", "card_number": "133", "subject": "EEVEE-HOLO"}
    assert match_cert(eevee, cards).status == "not_in_catalog"


def test_link_endpoint_links_the_spec_and_stores_population(client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.security.credentials import secret_store

    catalog.ensure_catalog(db)
    calls: list[str] = []
    runtime = client.app.state.runtime  # type: ignore[attr-defined]
    monkeypatch.setattr(runtime, "pokemon_transport", psa_api(calls), raising=False)
    monkeypatch.setattr(runtime.settings, "psa_api_base", BASE)
    secret_store(runtime.settings).set("psa_api_token", "tok")
    first = db.scalar(select(PokemonCard.id).where(PokemonCard.name == "Charizard", PokemonCard.edition == "1st Edition"))
    unlimited = db.scalar(select(PokemonCard.id).where(PokemonCard.name == "Charizard", PokemonCard.edition == "Unlimited"))

    wrong = client.post("/api/pokemon/psa/link", json={"cert_number": "11112222", "card_id": unlimited, "confirm": True}).json()
    assert wrong["linked"] is False and wrong["match"]["status"] == "mismatch"  # a contradicting field is never overridden

    r = client.post("/api/pokemon/psa/link", json={"cert_number": "11112222"}).json()
    assert r["linked"] is True and r["card_id"] == first and r["cert"]["scope"] == "certificate"
    assert r["population"]["grades_stored"] == 5 and r["population"]["qualified"] == {"Grade9Q": 4}
    db.expire_all()
    pops = {(p.grade, p.count) for p in db.scalars(select(PokemonPopulation).where(PokemonPopulation.card_id == first))}
    assert ("10", 121) in pops and all(p.source == "psa" for p in db.scalars(select(PokemonPopulation)))
    card = client.get(f"/api/pokemon/cards/{first}").json()["card"]
    assert card["psa_spec"] == "544027" and card["psa_certs"] == ["11112222"]
    src = {s["key"]: s for s in client.get("/api/pokemon/sources").json()["sources"]}["psa"]
    assert src["requests_today"] >= 2 and src["last_error"] is None
