"""Market Pulse: one combined, budget-checked X search; spam / gaming filtered; duplicates collapsed; tabs; cooldown; the
X budget guard is respected; posts never become sales. X payloads are TEST FIXTURES."""

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.pokemon import PokemonPulsePost, PokemonSale
from app.pokemon import pulse

USERS = [{"id": "u1", "username": "VintageSlabs", "name": "Vintage Slabs", "profile_image_url": "https://pbs.test/a.jpg", "verified": False},
         {"id": "u2", "username": "spammer", "name": "Free Stuff"}, {"id": "u3", "username": "gamer", "name": "Gamer"}]  # TEST FIXTURES


def tweet(i: str, text: str, author: str = "u1", likes: int = 0, media: bool = False) -> dict:  # type: ignore[type-arg]
    t = {"id": i, "text": text, "author_id": author, "created_at": f"2026-10-08T0{i[-1]}:00:00.000Z", "lang": "en",
         "public_metrics": {"like_count": likes, "retweet_count": 1, "reply_count": 0, "quote_count": 0}}
    if media:
        t["attachments"] = {"media_keys": ["m1"]}
    return t


PAGE = {"data": [
    tweet("101", "Base Set 1st Edition Charizard PSA 10 just sold for $420,000 at Goldin auction", likes=500, media=True),
    tweet("102", "GIVEAWAY! RT & follow to win a PSA 10 Charizard", author="u2"),
    tweet("103", "Shiny hunt in Pokemon Scarlet tonight, raid battle with friends", author="u3"),
    tweet("104", "Neo Genesis Lugia 1st Edition PSA 9 population report update: graded copies rising", likes=20),
    tweet("105", "Base Set 1st Edition Charizard PSA 10 just sold for $420,000 at Goldin auction!!", likes=3),  # near-duplicate of 101
], "includes": {"users": USERS, "media": [{"media_key": "m1", "type": "photo", "url": "https://pbs.test/card.jpg"}]},
    "meta": {"newest_id": "105", "result_count": 5}}


def x_api(calls: list[httpx.Request], page: dict | None = None) -> httpx.MockTransport:  # type: ignore[type-arg]
    def handle(r: httpx.Request) -> httpx.Response:
        calls.append(r)
        return httpx.Response(200, json=page if page is not None else PAGE)
    return httpx.MockTransport(handle)


def connect(client: TestClient, monkeypatch, calls: list[httpx.Request], page: dict | None = None) -> None:  # type: ignore[no-untyped-def,type-arg]
    from app.security.credentials import secret_store

    runtime = client.app.state.runtime  # type: ignore[attr-defined]
    secret_store(runtime.settings).set("x_bearer_token", "bearer-test")
    monkeypatch.setattr(runtime.x, "transport", x_api(calls, page))
    monkeypatch.setattr(runtime.settings, "x_api_base", "https://api.x.test")


def test_query_is_one_combined_search_within_limits() -> None:
    q = pulse.build_query(["@Goldin", "bad handle!", "heritage"])
    assert q.count(" OR ") >= 5 and "from:Goldin" in q and "from:heritage" in q and "bad handle" not in q and len(q) <= 512
    assert "-is:retweet" in q and "-giveaway" in q


def test_classification_keeps_market_posts_and_drops_spam_and_games() -> None:
    assert pulse.classify("Charizard PSA 10 sold at Heritage auction")[0:2] == (True, ["auctions", "grading", "sale"])
    assert pulse.classify("Win a PSA 10! Giveaway, RT & follow")[3] == "spam / giveaway"
    assert pulse.classify("Pokemon GO community day shiny hunt")[3] == "video game, not the card market"
    assert pulse.classify("Good morning everyone")[0] is False and pulse.classify("Good morning everyone", trusted=True)[0] is True
    assert "vintage" in pulse.classify("1999 Base Set Blastoise graded")[1]
    # real posts seen in the first live search (crypto / pack-app promotions) are dropped; real market talk is kept
    assert pulse.classify("Introducing PokeIndex. The FIRST launchpad that backs your coin with Pokémon cards. Real PSA 10 slabs.")[3] == "crypto / web3 promotion"
    assert pulse.classify("$CERT — FIRST 10 PACKS. Mew V CGC 10 Pristine $35, PSA 10 $50")[3] == "crypto / web3 promotion"
    assert pulse.classify("The Lucky Fragment Pack is live. Pull PSA-graded Pokémon cards for $1")[3] == "pack-opening / app promotion"
    assert pulse.classify("Play the game. Get REAL Pokémon cards. Pulled a PSA 10 Mewtwo worth $925!")[3] == "pack-opening / app promotion"
    assert pulse.classify("The 25 modern cards I'd consign right now, with real PSA 10 comps for every one")[0] is True
    assert pulse.classify("Charizard PSA 10 sold for $5,000 at auction")[0] is True  # a dollar amount is not a cashtag


def test_refresh_stores_filters_dedupes_and_respects_since_id(client: TestClient, db: Session, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls: list[httpx.Request] = []
    connect(client, monkeypatch, calls)
    r = client.post("/api/pokemon/pulse/refresh").json()
    assert r["ok"] is True and r["kept"] == 2 and r["filtered"] == 2 and r["duplicates"] == 1
    assert r["estimated_cost_usd"] == round(5 * 0.005 + 3 * 0.010, 4) and len(calls) == 1
    req = calls[0]
    assert req.url.path == "/2/tweets/search/recent" and req.headers["authorization"] == "Bearer bearer-test"
    assert req.url.params["max_results"] == "10" and "profile_image_url" in req.url.params["user.fields"]
    assert db.scalar(select(func.count(PokemonSale.id))) == 0  # posts are never sales

    feed = client.get("/api/pokemon/pulse?tab=latest").json()
    assert [p["id"] for p in feed["posts"]] == ["104", "101"]
    top = next(p for p in feed["posts"] if p["id"] == "101")
    assert top["url"] == "https://x.com/VintageSlabs/status/101" and top["media"] == "https://pbs.test/card.jpg" and top["avatar"]
    assert [p["id"] for p in client.get("/api/pokemon/pulse?tab=popular").json()["posts"]] == ["101", "104"]
    assert [p["id"] for p in client.get("/api/pokemon/pulse?tab=auctions").json()["posts"]] == ["101"]
    assert {p["id"] for p in client.get("/api/pokemon/pulse?tab=vintage").json()["posts"]} == {"101", "104"}

    again = client.post("/api/pokemon/pulse/refresh").json()
    assert again["ok"] is False and "try again" in again["error"] and len(calls) == 1  # cooldown: nothing spent
    cfg = pulse.config(db)
    db.refresh(cfg)
    assert cfg.since_id == "105"


def test_budget_guard_blocks_the_search_and_says_so(client: TestClient, db: Session, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls: list[httpx.Request] = []
    connect(client, monkeypatch, calls)
    runtime = client.app.state.runtime  # type: ignore[attr-defined]
    monkeypatch.setattr(runtime.settings, "x_monthly_budget_usd", 0.0)
    r = client.post("/api/pokemon/pulse/refresh").json()
    assert r["ok"] is False and "budget" in r["error"].lower() and calls == []  # refused before any request
    st = client.get("/api/pokemon/pulse").json()["status"]
    assert "X budget reached" in st["error"] and st["enabled"] is False and st["cooldown_seconds"] == 0 and st["last_refresh_at"] is None


def test_config_trusted_accounts_and_auto_refresh_switch(client: TestClient) -> None:
    st = client.put("/api/pokemon/pulse/config", json={"trusted_accounts": ["@Goldin", "heritage"], "enabled": True}).json()
    assert st["trusted_accounts"] == ["Goldin", "heritage"] and st["enabled"] is True
    assert client.put("/api/pokemon/pulse/config", json={"trusted_accounts": ["not a handle!!"]}).status_code == 400


def test_posts_link_to_catalog_cards_and_drive_trending(client: TestClient, db: Session, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.db.base import utcnow
    from app.models.pokemon import PokemonCard
    from app.pokemon import catalog

    catalog.ensure_catalog(db)
    cards = list(db.scalars(select(PokemonCard)))
    first = pulse.linked_cards("Base Set 1st Edition Charizard PSA 10 sold at Goldin", cards)
    assert {(c.name, c.set_name, c.edition) for c in first} == {("Charizard", "Base Set", "1st Edition")}
    assert {c.pokemon_name for c in pulse.linked_cards("Lugia PSA 9 pop report", cards)} == {"Lugia"}
    assert pulse.linked_cards("Graded my collection today", cards) == []
    for i in range(3):  # TEST FIXTURES: three recent posts about Lugia
        db.add(PokemonPulsePost(post_id=f"9{i}", author_id="a", username="u", display_name="u", text=f"Neo Genesis Lugia PSA 10 auction {i}",
                                created_at_x=utcnow(), url="https://x.com/u/status/1", categories=["auctions"], relevance=1.0, text_key=f"k{i}",
                                fetched_at=utcnow()))
    db.commit()
    feed = client.get("/api/pokemon/pulse").json()["posts"]
    assert feed[0]["cards"] and feed[0]["cards"][0]["name"] == "Lugia"
    rows = client.get("/api/pokemon/trending").json()["rows"]
    assert rows[0]["card"]["pokemon_name"] == "Lugia" and rows[0]["trend"]["x_mentions"] == 3 and "𝕏 3 mentions" in rows[0]["trend"]["why"]
    assert len({r["card"]["pokemon_name"] for r in rows}) == len(rows)  # one card per Pokémon


def test_chips_are_one_card_per_pokemon_and_game_ads_are_dropped(db: Session) -> None:
    from app.models.pokemon import PokemonCard
    from app.pokemon import catalog

    catalog.ensure_catalog(db)
    cards = list(db.scalars(select(PokemonCard)))
    chips = pulse.card_chips("Trading my PSA 10 Mewtwo for a sealed case of Charizard", cards)
    assert [c.pokemon_name for c in chips] == ["Mewtwo", "Charizard"] and all(c.edition == "1st Edition" for c in chips)
    assert pulse.classify("In Route 151, you ARE the Pokémon. Explore an open-world Kanto")[3] == "video game, not the card market"


def test_game_ads_are_dropped_even_when_they_mention_psa() -> None:
    assert pulse.classify("In Route 151, you ARE the Pokémon. Explore an open-world Kanto. Win PSA 10 cards in-game!")[0] is False
