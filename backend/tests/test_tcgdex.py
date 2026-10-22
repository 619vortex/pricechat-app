"""TCGdex: catalog + images only (pricing stripped), cached, no duplicates, existing data never overwritten."""

import asyncio
import json

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config.settings import Settings
from app.models.pokemon import PokemonCard
from app.pokemon import catalog, tcgdex
from app.providers.pokemon import provider
from app.providers.pokemon.tcgdex import TCGdexProvider, strip_pricing

PRICING = {"cardmarket": {"avg": 741.93, "trend": 504.73}, "tcgplayer": {"holofoil": {"marketPrice": 928.32}}}  # TEST FIXTURE
SETS = {
    "en/base1": {"id": "base1", "name": "Base Set", "releaseDate": "1999-01-09", "cardCount": {"official": 102}},
    "en/neo1": {"id": "neo1", "name": "Neo Genesis", "releaseDate": "2000-12-16", "cardCount": {"official": 111}},
    "en/neo4": {"id": "neo4", "name": "Neo Destiny", "releaseDate": "2002-02-28", "cardCount": {"official": 105}},
    "en/base5": {"id": "base5", "name": "Team Rocket", "releaseDate": "2000-04-24", "cardCount": {"official": 82}},
    "en/ecard3": {"id": "ecard3", "name": "Skyridge", "releaseDate": "2003-05-12", "cardCount": {"official": 144}},
    "ja/PMCG1": {"id": "PMCG1", "name": "拡張パック", "releaseDate": "1996-10-20", "cardCount": {"official": 102}},
}


def card(cid: str, name: str, set_id: str, dex: int, first: bool = True, lang: str = "en") -> dict:  # type: ignore[type-arg]
    local = cid.split("-")[1]
    img = None if lang == "ja" else f"https://assets.tcgdex.test/{lang}/{set_id}/{local}"
    return {"id": cid, "localId": local, "name": name, "rarity": "Rare", "image": img, "dexId": [dex], "illustrator": "Mitsuhiro Arita",
            "set": {"id": set_id, "name": SETS[f"{lang}/{set_id}"]["name"]}, "variants": {"firstEdition": first, "holo": True},
            "variants_detailed": [{"type": "holo", "pricing": PRICING}], "pricing": PRICING}


CARDS = {f"en/{c['id']}": c for c in (
    card("base1-1", "Alakazam", "base1", 65), card("base1-2", "Blastoise", "base1", 9), card("base1-4", "Charizard", "base1", 6),
    card("base1-10", "Mewtwo", "base1", 150), card("base1-15", "Venusaur", "base1", 3), card("base5-4", "Dark Charizard", "base5", 6),
    card("neo1-9", "Lugia", "neo1", 249), card("neo4-107", "Shining Charizard", "neo4", 6), card("ecard3-146", "Charizard", "ecard3", 6, False))}
CARDS["ja/PMCG1-021"] = card("PMCG1-021", "リザードン", "PMCG1", 6, False, "ja")
CARDS["ja/PMCG1-060"] = card("PMCG1-060", "ニョロモ", "PMCG1", 60, False, "ja")


def tcgdex_api(calls: list[str]) -> httpx.MockTransport:
    def handle(r: httpx.Request) -> httpx.Response:
        calls.append(str(r.url))
        lang, _, rest = r.url.path.removeprefix("/v2/").partition("/")
        q = dict(r.url.params)
        if rest.startswith("sets/"):
            return httpx.Response(200, json={**SETS[f"{lang}/{rest[5:]}"], "cards": []})
        if rest.startswith("cards/"):
            c = CARDS.get(f"{lang}/{rest[6:]}")
            return httpx.Response(200, json=c) if c else httpx.Response(404, json={"status": 404})
        if rest == "cards":
            sid = q.get("set.id", "").removeprefix("eq:")
            hits = [c for k, c in CARDS.items() if k.startswith(f"{lang}/") and c["set"]["id"] == sid]
            if "dexId" in q:
                hits = [c for c in hits if q["dexId"].removeprefix("eq:") in str(c["dexId"][0])]  # loose, like the real API
            return httpx.Response(200, json=[{k: c[k] for k in ("id", "localId", "name", "image")} for c in hits])
        return httpx.Response(404, json={})
    return httpx.MockTransport(handle)


def settings(tmp_path) -> Settings:  # type: ignore[no-untyped-def]
    return Settings(tcgdex_api_base="https://api.tcgdex.test/v2", tcgdex_cache_dir=tmp_path)


def test_pricing_is_stripped_before_anything_uses_or_caches_it(tmp_path) -> None:  # type: ignore[no-untyped-def]
    calls: list[str] = []
    api = provider("tcgdex", settings(tmp_path), tcgdex_api(calls))
    assert isinstance(api, TCGdexProvider)
    c = asyncio.run(api.card("base1-4"))
    assert "pricing" not in json.dumps(c) and c["variants"]["firstEdition"] is True
    assert "pricing" not in "".join(f.read_text(encoding="utf-8") for f in tmp_path.glob("*.json"))
    asyncio.run(api.card("base1-4"))
    assert len(calls) == 1 and api.cache_hits == 1  # second read from the disk cache
    assert strip_pricing({"a": [{"pricing": 1, "b": 2}]}) == {"a": [{"b": 2}]}


def test_import_links_existing_cards_fills_only_gaps_and_creates_no_duplicates(db: Session, tmp_path) -> None:  # type: ignore[no-untyped-def]
    catalog.ensure_catalog(db)
    kept = db.scalar(select(PokemonCard).where(PokemonCard.name == "Mewtwo"))
    assert kept is not None
    kept.image_url, kept.image_source = "https://example.test/my-own-scan.png", "manual"  # existing data: must survive
    db.commit()
    before = db.scalar(select(func.count(PokemonCard.id)))
    out = asyncio.run(tcgdex.import_vintage(db, settings(tmp_path), limit=100, transport=tcgdex_api([])))

    assert out["error"] is None and out["created"] == 1  # only Alakazam is new
    new = db.scalar(select(PokemonCard).where(PokemonCard.name == "Alakazam"))
    assert new is not None and (new.edition, new.card_number, new.year, new.rarity) == ("1st Edition", "1/102", 1999, "Rare Holo")
    assert new.image_url == "https://assets.tcgdex.test/en/base1/1/high.webp" and new.external_ids == {"tcgdex": "base1-1"}
    assert db.scalar(select(func.count(PokemonCard.id))) == before + 1

    db.expire_all()
    zards = list(db.scalars(select(PokemonCard).where(PokemonCard.set_code == "base1", PokemonCard.name == "Charizard")))
    assert len(zards) == 3 and all(z.external_ids["tcgdex"] == "base1-4" for z in zards)  # every edition row linked, none added
    assert all(z.rarity == "Rare Holo" for z in zards)  # catalog rarity kept, not TCGdex's "Rare"
    mewtwo = db.get(PokemonCard, kept.id)
    assert mewtwo is not None and mewtwo.image_url == "https://example.test/my-own-scan.png" and mewtwo.image_source == "manual"

    jp = db.scalar(select(PokemonCard).where(PokemonCard.language == "Japanese", PokemonCard.pokemon_name == "Charizard"))
    assert jp is not None and jp.external_ids["tcgdex"] == "ja:PMCG1-021" and jp.image_url is None  # exact dex #6, not #60
    assert out["japanese_linked"] == 1
    assert any(s.get("reason", "").startswith("CoroCoro") for s in out["skipped"])  # Pikachu Illustrator: no TCGdex set, said so

    again = asyncio.run(tcgdex.import_vintage(db, settings(tmp_path), limit=100, transport=tcgdex_api([])))
    assert again["created"] == 0 and db.scalar(select(func.count(PokemonCard.id))) == before + 1  # idempotent


def test_import_respects_the_limit(db: Session, tmp_path) -> None:  # type: ignore[no-untyped-def]
    out = asyncio.run(tcgdex.import_vintage(db, settings(tmp_path), limit=3, transport=tcgdex_api([])))
    assert out["tcgdex_cards"] == 3


def test_import_endpoint(client: TestClient, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    runtime = client.app.state.runtime  # type: ignore[attr-defined]
    monkeypatch.setattr(runtime, "pokemon_transport", tcgdex_api([]), raising=False)
    monkeypatch.setattr(runtime.settings, "tcgdex_api_base", "https://api.tcgdex.test/v2")
    monkeypatch.setattr(runtime.settings, "tcgdex_cache_dir", tmp_path)
    r = client.post("/api/pokemon/catalog/import-tcgdex?limit=100").json()
    assert r["created"] == 1 and "pricing" not in json.dumps(r)
    src = {s["key"]: s for s in client.get("/api/pokemon/sources").json()["sources"]}
    assert src["tcgdex"]["connected"] is True and src["tcgdex"]["credentials_present"] is None


def test_trainer_cards_keep_their_name_and_pokemon_lose_only_prefixes() -> None:
    s = SETS["en/base5"]
    trainer = {**card("base5-16", "Rocket's Sneak Attack", "base5", 0), "category": "Trainer", "dexId": None}
    assert tcgdex._new_card(trainer, s).pokemon_name == "Rocket's Sneak Attack"  # type: ignore[union-attr]
    assert tcgdex._new_card({**card("base5-4", "Dark Charizard", "base5", 6), "category": "Pokemon"}, s).pokemon_name == "Charizard"  # type: ignore[union-attr]
    assert tcgdex.pokemon_name("Brock's Onix") == "Onix" and tcgdex.pokemon_name("Light Arcanine") == "Arcanine"
