"""Top 100 data quality: eligibility checks (price, population, history, identity, freshness, moves, spikes, sales
consistency), ranking by market cap only, no padding, snapshots + enter / leave events; eBay per-card listings (asking
prices only, sold-data permission, exact matching, secret never returned). TEST FIXTURES only, test database only."""

import asyncio
from datetime import timedelta
from urllib.parse import parse_qs

import httpx
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.settings import Settings
from app.db.base import utcnow
from app.models.pokemon import (PokemonCard, PokemonListing, PokemonPopulation, PokemonProviderValuation, PokemonSale, PokemonTop100Event,
                                PokemonTop100Snapshot)
from app.pokemon import catalog, history, top100


def _card(db: Session, name: str, edition: str = "1st Edition", set_name: str = "Base Set") -> PokemonCard:
    c = db.scalar(select(PokemonCard).where(PokemonCard.name == name, PokemonCard.edition == edition, PokemonCard.set_name == set_name))
    assert c is not None
    return c


def _record(db: Session, c: PokemonCard, prices: list[tuple[int, int]], pop: int | None, linked: bool = True, trends: dict | None = None) -> None:
    """TEST FIXTURE: Scrydex PSA 10 valuations `days ago -> cents` (the newest is today's current one) and a population."""
    now = utcnow()
    if linked:
        c.external_ids = {**(c.external_ids or {}), "scrydex": f"{c.set_code}-x{c.id}", "scrydex_variant": "firstEditionHolofoil"}
    for days, cents in prices:
        db.add(PokemonProviderValuation(provider="scrydex", card_id=c.id, provider_card_id="x", provider_variant="v", grading_company="PSA", grade="10",
                                        day=(now - timedelta(days=days)).date(), kind="current" if days == 0 else "history", market_cents=cents,
                                        currency="USD", fetched_at=now, trends=trends if days == 0 else None))
    if pop:
        db.add(PokemonPopulation(card_id=c.id, grading_company="PSA", grade="10", count=pop, source="scrydex", as_of=now))
    db.commit()


def _steady(n: int, cents: int) -> list[tuple[int, int]]:
    return [(d * 7, cents + d * 100) for d in range(n)]  # n weekly points, gently moving, newest today


def test_eligibility_rules_rank_by_market_cap_only_and_never_pad(client: TestClient, db: Session) -> None:
    catalog.ensure_catalog(db)
    zard, blast, venu = _card(db, "Charizard"), _card(db, "Blastoise"), _card(db, "Venusaur")
    lugia = _card(db, "Lugia", "1st Edition", "Neo Genesis")
    _record(db, zard, _steady(10, 400_000_00), pop=120)  # eligible, cap 48.0M
    _record(db, blast, _steady(10, 60_000_00), pop=900)  # eligible, cap 54.0M: ranks ABOVE Charizard (market cap only)
    _record(db, venu, _steady(10, 30_000_00), pop=None)  # no population
    _record(db, lugia, _steady(3, 150_000_00), pop=45)  # too little history
    unl = _card(db, "Charizard", "Unlimited")
    _record(db, unl, [(0, 12_000_00), (7, 3_000_00), *_steady(10, 3_000_00)[2:]], pop=900)  # x4 jump a week ago: spike
    shadow = _card(db, "Charizard", "Shadowless")
    _record(db, shadow, _steady(10, 50_000_00), pop=200, trends={"days_30": {"percent_change": 150.0}})  # unverified +150% 30D
    # only two observations in our database, but Scrydex publishes fresh 30-day and 90-day trends: eligible, cap 10.0M
    mew = _card(db, "Mewtwo")
    _record(db, mew, [(0, 20_000_00), (40, 18_000_00)], pop=500,
            trends={"days_30": {"percent_change": 4.0}, "days_90": {"percent_change": 11.1}, "days_180": {"percent_change": 20.0}})
    history.invalidate()

    t = client.get("/api/pokemon/top100").json()
    assert [r["card"]["id"] for r in t["rows"]] == [blast.id, zard.id, mew.id] and [r["rank"] for r in t["rows"]] == [1, 2, 3]
    s = t["summary"]
    assert (s["eligible"], s["shown"], s["limit"]) == (3, 3, 100)  # fewer than 100: shown as is, never padded
    assert s["excluded_by_reason"]["population"] >= 1 and s["excluded_by_reason"]["history"] >= 1
    assert s["excluded_by_reason"]["spikes"] == 1 and s["excluded_by_reason"]["moves"] == 2  # the x4 jump is also an unverified +300% 30D move

    q = {cid: client.get(f"/api/pokemon/cards/{cid}/quality").json() for cid in (zard.id, venu.id, lugia.id, unl.id, shadow.id, mew.id)}
    assert q[zard.id]["top100_rank"] == 2 and q[zard.id]["quality"]["eligible"]
    assert q[zard.id]["market_cap"]["market_cap"] == q[zard.id]["market_cap"]["price"] * 120  # auditable: price x population
    assert q[venu.id]["quality"]["failed"] == ["population"]
    assert q[lugia.id]["quality"]["failed"] == ["history"] and "needs 8" in q[lugia.id]["quality"]["reasons"][0]
    assert q[unl.id]["quality"]["failed"] == ["moves", "spikes"]
    assert q[shadow.id]["quality"]["failed"] == ["moves"] and "+150% 30d" in q[shadow.id]["quality"]["reasons"][0]
    assert q[mew.id]["quality"]["eligible"] and "30D and 90D movements from scrydex" in next(
        c["detail"] for c in q[mew.id]["quality"]["checks"] if c["code"] == "history")

    # the market table's Top 100 view = the same eligible cards; every row carries its confidence; excluded cards stay in "all"
    m = client.get("/api/pokemon/market?era=all&view=top100").json()
    assert [r["card"]["id"] for r in m["rows"]] == [blast.id, zard.id, mew.id] and m["rows"][0]["quality"]["eligible"] is True
    everything = client.get("/api/pokemon/market?era=all&view=all&limit=250").json()
    assert venu.id in {r["card"]["id"] for r in everything["rows"]} and m["top100"]["eligible"] == 3
    # Japanese only: no Japanese record qualifies (no PSA 10 population on record) -> an empty, honest Top 100
    assert client.get("/api/pokemon/top100?language=Japanese").json()["summary"]["eligible"] == 0
    # Data Quality: excluded records with reasons, filterable by reason
    dq = client.get("/api/pokemon/top100/quality?reason=spikes").json()
    assert [r["card"]["id"] for r in dq["rows"]] == [unl.id] and dq["missing"]["population"] >= 1


def test_old_spikes_are_noted_not_disqualifying(db: Session) -> None:
    catalog.ensure_catalog(db)
    zard = _card(db, "Charizard")
    _record(db, zard, [(0, 400_000_00), (30, 400_000_00), (60, 400_000_00), (120, 410_000_00), (127, 100_000_00), *[(140 + 7 * i, 100_000_00) for i in range(6)]], pop=120)
    history.invalidate()
    q = top100.assess(db)[zard.id]
    assert q["eligible"] and "1 older jump" in next(c["detail"] for c in q["checks"] if c["code"] == "spikes")


def test_sales_disagreement_excludes(db: Session) -> None:
    catalog.ensure_catalog(db)
    zard = _card(db, "Charizard")
    _record(db, zard, _steady(10, 400_000_00), pop=120)
    for i in range(3):  # TEST FIXTURES: three verified sales far below Scrydex
        db.add(PokemonSale(card_id=zard.id, grading_company="PSA", grade="10", sale_price_cents=150_000_00, currency="USD", sale_price_usd_cents=150_000_00,
                           sale_date=utcnow() - timedelta(days=10 + i), source="goldin", verified=True, fingerprint=f"fp{i}"))
    db.commit()
    history.rebuild(db, {(zard.id, "PSA", "10")})
    history.invalidate()
    q = top100.assess(db)[zard.id]
    assert "consistency" in q["failed"] and "off by 167% of the median of 3 verified sales" in " ".join(q["reasons"])


def test_snapshots_record_entries_and_exits(db: Session) -> None:
    catalog.ensure_catalog(db)
    zard, blast = _card(db, "Charizard"), _card(db, "Blastoise")
    _record(db, zard, _steady(10, 400_000_00), pop=120)
    history.invalidate()
    first = top100.record(db, utcnow() - timedelta(days=1))
    assert (first["members"], first["entered"], first["left"]) == (1, 1, 0)
    db.query(PokemonPopulation).filter(PokemonPopulation.card_id == zard.id).delete()  # Charizard loses its population
    _record(db, blast, _steady(10, 60_000_00), pop=900)
    history.invalidate()
    second = top100.record(db)
    assert (second["members"], second["entered"], second["left"]) == (1, 1, 1)
    ev = {(e.card_id, e.kind): e for e in db.scalars(select(PokemonTop100Event))}
    assert ev[(zard.id, "left")].reasons == ["no PSA 10 population on record"] and (blast.id, "entered") in ev
    assert {s.day for s in db.scalars(select(PokemonTop100Snapshot))} == {utcnow().date() - timedelta(days=1), utcnow().date()}


# --- eBay ---------------------------------------------------------------------------------------------------------------


def _ebay(grant: bool, items: list[dict], calls: list[str]) -> httpx.MockTransport:
    def handle(r: httpx.Request) -> httpx.Response:
        calls.append(r.url.path)
        if r.url.path.endswith("/oauth2/token"):
            assert r.headers["Authorization"].startswith("Basic ")
            scope = parse_qs(r.content.decode())["scope"][0]
            if scope.endswith("buy.marketplace.insights") and not grant:
                return httpx.Response(400, json={"error": "invalid_scope"})
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 7200})
        if r.url.path.endswith("/item_summary/search"):
            return httpx.Response(200, json={"itemSummaries": items})
        if r.url.path.endswith("/item_sales/search"):
            return httpx.Response(200, json={"itemSales": []})
        return httpx.Response(404, json={})
    return httpx.MockTransport(handle)


def _item(i: str, title: str, price: str) -> dict:
    return {"itemId": i, "title": title, "price": {"value": price, "currency": "USD"}, "condition": "Graded", "seller": {"username": "slabking"},
            "buyingOptions": ["FIXED_PRICE"], "itemWebUrl": f"https://ebay.test/itm/{i}", "image": {"imageUrl": "https://img.test/x.jpg"}}  # TEST FIXTURE


def test_card_ebay_listings_are_exact_and_asking_prices_only(client: TestClient, db: Session, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.pokemon import ebay_cards
    from app.providers.pokemon.ebay import EbayProvider
    from app.security.credentials import secret_store

    runtime = client.app.state.runtime  # type: ignore[attr-defined]
    calls: list[str] = []
    items = [_item("A1", "1999 Pokemon Base Set Charizard 4/102 1st Edition Holo PSA 10 GEM MINT", "399999.00"),
             _item("A2", "1999 Pokemon Base Set Charizard 4/102 Unlimited Holo PSA 10", "12500.00"),  # another card: not this one
             _item("A3", "1999 Pokemon Japanese Base Set Charizard Holo PSA 10", "9000.00"),  # Japanese: never the English card
             _item("A4", "Charizard 4/102 Base Set 1st Edition PSA 9", "40000.00")]  # other grade
    monkeypatch.setattr(runtime, "pokemon_transport", _ebay(False, items, calls), raising=False)
    monkeypatch.setattr(runtime.settings, "ebay_api_base", "https://ebay.test")
    store = secret_store(runtime.settings)
    store.set("ebay_client_id", "cid-test")
    store.set("ebay_client_secret", "SECRET-NEVER-SHOWN")
    EbayProvider._tokens.clear()
    EbayProvider.sold_access.clear()
    ebay_cards._fetched.clear()
    catalog.ensure_catalog(db)
    zard = _card(db, "Charizard")

    r = client.get(f"/api/pokemon/cards/{zard.id}/ebay?grade=PSA 10")
    body = r.json()
    assert "SECRET-NEVER-SHOWN" not in r.text
    assert body["connected"] and body["fetched"] and [x["listing_id"] for x in body["listings"]] == ["A1"]
    assert body["listings"][0]["price"] == 399_999_00 and body["listings"][0]["seller"] == "slabking" and body["listings"][0]["url"].endswith("/A1")
    assert body["sold"] == {"access": False, "message": "SOLD DATA UNAVAILABLE FROM CURRENT EBAY API ACCESS (Marketplace Insights is not granted to these keys)"}
    rows = {x.listing_id: x for x in db.scalars(select(PokemonListing))}
    assert rows["A2"].card_id != zard.id and rows["A3"].card_id != zard.id and rows["A4"].card_id in (zard.id, None)
    assert rows["A4"].grade != "10" or rows["A4"].match_status != "matched"
    assert db.scalar(select(PokemonSale.id)) is None  # asking prices never become sales

    n = len(calls)
    client.get(f"/api/pokemon/cards/{zard.id}/ebay?grade=PSA 10")  # within the hour: served from what is stored
    assert len(calls) == n

    keys = client.get("/api/settings/api-keys").json()["keys"]
    assert "SECRET-NEVER-SHOWN" not in str(keys) and next(k for k in keys if k["name"] == "ebay_client_secret")["test_key"] == "ebay"
    assert any(k["name"] == "ebay_dev_id" for k in keys)


def test_ebay_test_connection_reports_sold_access(client: TestClient, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.providers.pokemon.ebay import EbayProvider
    from app.security.credentials import secret_store

    runtime = client.app.state.runtime  # type: ignore[attr-defined]
    for grant, expect in ((False, "SOLD DATA UNAVAILABLE"), (True, "Marketplace Insights granted")):
        EbayProvider._tokens.clear()
        EbayProvider.sold_access.clear()
        monkeypatch.setattr(runtime, "pokemon_transport", _ebay(grant, [_item("A1", "Charizard PSA 10", "1.00")], []), raising=False)
        monkeypatch.setattr(runtime.settings, "ebay_api_base", "https://ebay.test")
        store = secret_store(runtime.settings)
        store.set("ebay_client_id", "cid-test")
        store.set("ebay_client_secret", "SECRET-NEVER-SHOWN")
        r = client.post("/api/pokemon/sources/ebay/test")
        assert r.json()["ok"] and expect in r.json()["message"] and "SECRET" not in r.text


def test_settings_scope(db: Session) -> None:
    s = Settings(ebay_client_id=SecretStr("x"))
    assert s.ebay_cards_per_sync == 150 and s.pokemon_top100_min_history == 8
    assert asyncio.iscoroutinefunction(top100.improve)


def test_chart_gaps_target_eligible_cards_without_history_once(db: Session) -> None:
    catalog.ensure_catalog(db)
    zard, mew = _card(db, "Charizard"), _card(db, "Mewtwo")
    _record(db, zard, _steady(10, 400_000_00), pop=120)  # 10 observations: a real chart already
    _record(db, mew, [(0, 20_000_00)], pop=500, trends={"days_30": {"percent_change": 4.0}, "days_90": {"percent_change": 11.1}})
    history.invalidate()
    assert top100.chart_gaps(db) == [mew.id]  # eligible through Scrydex trends, but its chart has one observation
    top100.mark_history_checked(db, [mew.id], utcnow())
    history.invalidate()
    assert top100.chart_gaps(db) == []  # fetched recently: not paid for again
