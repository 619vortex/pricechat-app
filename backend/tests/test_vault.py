"""Price Chat Vault. TEST FIXTURES only: a fake eBay Browse API (httpx.MockTransport) with made-up item ids and prices."""

from datetime import timedelta
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.members.service import MemberError
from app.models.vault import VaultEvent, VaultListing, VaultSource
from app.vault import cards
from app.vault.classify import classify, seller_grade
from tests.test_portfolio import _member

SELLER = "mayumicardshop"


# --- classification ----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("title,ok,reason,pokemon,set_name,year", [
    ("[NM] Gengar Pokemon Card Japanese No.094 Vintage Holo LW7", True, None, "Gengar", None, None),
    ("[NM] Lugia Pokemon Card Japanese Neo Genesis Holo", True, None, "Lugia", "Neo Genesis (Gold, Silver, to a New World...)", 2000),
    ("[LP] Dark Charizard Pokemon Card Japanese Rocket Gang Vintage Holo", True, None, "Dark Charizard", "Team Rocket (Rocket Gang)", 1997),
    ("[NM] Rocket's Zapdos Pokemon Card Japanese Gym 2 Vintage", True, None, "Rocket's Zapdos", "Gym Challenge (Challenge from the Darkness)", 1999),
    ("[NM] Mew Pokemon Card Japanese No.151 Vintage Holo", True, None, "Mew", None, None),  # No.151 isn't the modern "151" set
    ("[EX] Charizard Pokemon Card Japanese No Rarity Symbol 1996 Base Holo", True, None, "Charizard", "Base Set (Expansion Pack)", 1996),
    ("[NM] Mr. Mime Pokemon Japanese Jungle Vintage", True, None, "Mr. Mime", "Jungle", 1997),
    ("[PSA 5] Charizard No Rarity Symbol Pokemon Japanese No.006 1996 Vintage Holo", False, "graded slab", None, None, None),
    ("[ARS 10] Umbreon Pokemon Card Japanese No.197 2000 Neo 2 Vintage Holo", False, "graded slab", None, None, None),
    ("[Sealed] MEGA 30th Celebration Booster Box M6a Japanese Pokemon Card w/shrink", False, "not a single card", None, None, None),
    ("[NM] Pikachu ex SAR Pokemon Card Japanese 2023 sv2a", False, "modern (2023)", None, None, None),
    ("[NM] Umbreon VMAX Pokemon Card Japanese s6a Holo", False, "modern marker", None, None, None),
    ("Charizard Base Set Holo Pokemon English WOTC", False, "English", None, None, None),
    ("[NM] Monkey D. Luffy One Piece Card Japanese Vintage", False, "not Pokémon", None, None, None),
    ("[NM] Pikachu Pokemon Card Holo", False, "language not stated", None, None, None),
    ("[NM] Pikachu Pokemon Card Japanese Holo", False, "no vintage set or year", None, None, None),
    ("Pokemon Card Japanese Vintage Lot of 50 Holo", False, "not a single card", None, None, None),
])
def test_vintage_japanese_classification(title: str, ok: bool, reason: str | None, pokemon: str | None, set_name: str | None, year: int | None) -> None:
    v = classify(title)
    assert v.eligible is ok, (title, v.reason)
    if reason:
        assert reason in (v.reason or "")
    else:
        assert v.reason is None and v.pokemon == pokemon and v.set_name == set_name and v.year == year and v.language == "Japanese"


def test_item_specifics_beat_the_title() -> None:
    # the title doesn't say the language, eBay's Language specific does
    assert classify("[NM] Blastoise Pokemon Card Vintage Holo", {"Language": "Japanese", "Year Manufactured": "1996"}).eligible
    assert classify("[NM] Blastoise Pokemon Card Japanese Vintage", {"Language": "English"}).reason == "English, not Japanese"
    late = classify("[NM] Blastoise Pokemon Card Japanese Vintage", {"Language": "Japanese", "Year Manufactured": "2004"})
    assert not late.eligible and late.reason == "modern (2004)"
    # an admin's year correction wins
    assert classify("[NM] Pikachu Pokemon Card Japanese Holo", year_override=1998).eligible
    assert seller_grade("[NM] Gengar Pokemon Card Japanese") == "NM" and seller_grade("Gengar") is None


# --- fake eBay -----------------------------------------------------------------------------------------------------------


def _summary(i: str, title: str, price: str, seller: str = SELLER) -> dict:
    return {"itemId": f"v1|{i}|0", "legacyItemId": i, "title": title, "price": {"value": price, "currency": "USD"}, "condition": "Ungraded",
            "seller": {"username": seller, "feedbackPercentage": "100.0", "feedbackScore": 12345}, "buyingOptions": ["FIXED_PRICE"],
            "itemWebUrl": f"https://www.ebay.test/itm/{i}", "image": {"imageUrl": f"https://i.ebayimg.test/images/g/{i}/s-l225.jpg"},
            "shippingOptions": [{"shippingCost": {"value": "25.00", "currency": "USD"}}]}


class FakeEbay:
    """The seller's live store: `items` is what search returns, `details` what getItem returns (missing = 404)."""

    def __init__(self) -> None:
        self.items: list[dict] = []
        self.details: dict[str, dict] = {}
        self.searches: list[dict[str, str]] = []
        self.gets: list[str] = []

    def transport(self) -> httpx.MockTransport:
        def handle(r: httpx.Request) -> httpx.Response:
            if r.url.path.endswith("/oauth2/token"):
                assert parse_qs(r.content.decode())["scope"][0] == "https://api.ebay.com/oauth/api_scope"
                return httpx.Response(200, json={"access_token": "tok", "expires_in": 7200})
            if r.url.path.endswith("/item_summary/search"):
                params = dict(r.url.params)
                self.searches.append(params)
                off, lim = int(params["offset"]), int(params["limit"])
                return httpx.Response(200, json={"total": len(self.items), "itemSummaries": self.items[off: off + lim]})
            if "/buy/browse/v1/item/" in r.url.path:
                iid = r.url.path.rsplit("/", 1)[1]
                self.gets.append(iid)
                if iid in self.details:
                    return httpx.Response(200, json=self.details[iid])
                return httpx.Response(404, json={"errors": [{"errorId": 11001}]})
            return httpx.Response(404, json={})
        return httpx.MockTransport(handle)


@pytest.fixture
def ebay(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> FakeEbay:
    from app.providers.pokemon.ebay import EbayProvider
    from app.security.credentials import secret_store

    runtime = client.app.state.runtime  # type: ignore[attr-defined]
    fake = FakeEbay()
    monkeypatch.setattr(runtime, "pokemon_transport", fake.transport(), raising=False)
    monkeypatch.setattr(runtime.settings, "ebay_api_base", "https://ebay.test")
    store = secret_store(runtime.settings)
    store.set("ebay_client_id", "cid-test")
    store.set("ebay_client_secret", "SECRET-NEVER-SHOWN")
    EbayProvider._tokens.clear()
    return fake


def _sync(client: TestClient) -> dict:
    src = client.get("/api/vault/admin/overview").json()["sources"][0]
    return client.post(f"/api/vault/admin/sources/{src['id']}/sync").json()


def _detail(i: str, title: str, price: str, status: str = "IN_STOCK") -> dict:
    return {**_summary(i, title, price), "localizedAspects": [{"name": "Language", "value": "Japanese"}, {"name": "Set", "value": "Neo Genesis"}],
            "conditionDescriptors": [{"name": "Card Condition", "values": [{"content": "Near mint or better"}]}],
            "additionalImages": [{"imageUrl": f"https://i.ebayimg.test/images/g/{i}/b/s-l225.jpg"}],
            "estimatedAvailabilities": [{"estimatedAvailabilityStatus": status}]}


# --- sync, seller filtering, approval, storefront -------------------------------------------------------------------------


def test_sync_reads_only_the_seller_and_only_approved_vintage_cards_go_public(client: TestClient, db: Session, ebay: FakeEbay) -> None:
    ebay.items = [_summary("101", "[NM] Lugia Pokemon Card Japanese Neo Genesis Holo", "899.99"),
                  _summary("102", "[NM] Pikachu ex SAR Pokemon Card Japanese 2023 sv2a", "120.00"),           # modern
                  _summary("103", "[Sealed] Japanese Pokemon Booster Box Neo Genesis", "9999.00"),            # sealed
                  _summary("104", "[NM] Typhlosion Pokemon Card Japanese Neo Genesis Holo", "300.00", seller="someone-else")]  # another seller
    ebay.details = {"v1|101|0": _detail("101", "[NM] Lugia Pokemon Card Japanese Neo Genesis Holo", "899.99")}
    out = _sync(client)
    assert out["status"] == "ok" and out["seen"] == 3 and out["other_seller"] == 1 and out["eligible"] == 1
    search = ebay.searches[0]
    assert search["filter"] == "sellers:{mayumicardshop},buyingOptions:{FIXED_PRICE}"
    assert search["aspect_filter"] == "categoryId:183454,Language:{Japanese}" and search["category_ids"] == "183454"
    assert ebay.gets == ["v1|101|0"]  # item specifics only for listings that could be in the store
    assert db.scalar(select(VaultListing.id).where(VaultListing.external_id == "v1|104|0")) is None

    # nothing is public until an admin approves it
    assert client.get("/api/members/vault/listings").json()["total"] == 0
    pending = client.get("/api/vault/admin/listings?review=pending&eligible=true").json()["items"]
    assert [p["title"] for p in pending] == ["[NM] Lugia Pokemon Card Japanese Neo Genesis Holo"]
    modern = db.scalar(select(VaultListing).where(VaultListing.external_id == "v1|102|0"))
    r = client.patch(f"/api/vault/admin/listings/{modern.id}", json={"review": "approved"})
    assert r.status_code == 400 and "vintage Japanese" in r.json()["detail"]
    assert client.patch(f"/api/vault/admin/listings/{pending[0]['id']}", json={"review": "approved"}).json()["review"] == "approved"

    page = client.get("/api/members/vault/listings").json()
    assert page["total"] == 1
    card = page["items"][0]
    assert card["price"] == 89999 and card["currency"] == "USD" and card["shipping"] == 2500  # the seller's price, exactly
    assert card["pokemon"] == "Lugia" and card["year"] == 2000 and card["seller_grade"] == "NM" and card["condition"] == "Ungraded"
    assert card["source"] == {"kind": "ebay_seller", "name": "Mayumi Card Shop", "username": SELLER, "url": "https://www.ebay.com/str/mayumicardshop", "platform": "eBay"}
    assert card["image"].endswith("/s-l1600.jpg") and card["availability"] == "available" and card["can_request"] is True
    detail = client.get(f"/api/members/vault/listings/{card['id']}").json()
    assert detail["condition_detail"] == "Card Condition: Near mint or better" and len(detail["images"]) == 2
    assert detail["specifics"]["Set"] == "Neo Genesis" and detail["url"] == "https://www.ebay.test/itm/101"
    home = client.get("/api/members/vault/home").json()
    assert home["count"] == 1 and home["facets"]["pokemon"] == [["Lugia", 1]] and home["facets"]["years"] == [[2000, 1]]
    # filters
    assert client.get("/api/members/vault/listings?q=lugia").json()["total"] == 1
    assert client.get("/api/members/vault/listings?max_price=500").json()["total"] == 0
    assert client.get("/api/members/vault/listings?year=1999").json()["total"] == 0
    # admin hides it again
    client.patch(f"/api/vault/admin/listings/{card['id']}", json={"review": "hidden"})
    assert client.get(f"/api/members/vault/listings/{card['id']}").status_code == 404


def test_sold_out_ended_and_stale_listings(client: TestClient, db: Session, ebay: FakeEbay) -> None:
    titles = {"201": "[NM] Lugia Pokemon Card Japanese Neo Genesis Holo", "202": "[NM] Ho-Oh Pokemon Card Japanese Neo Revelation Holo",
              "203": "[NM] Celebi Pokemon Card Japanese Neo Destiny Holo"}
    ebay.items = [_summary(i, t, "100.00") for i, t in titles.items()]
    ebay.details = {f"v1|{i}|0": _detail(i, t, "100.00") for i, t in titles.items()}
    _sync(client)
    ids = {r.external_id: r.id for r in db.scalars(select(VaultListing))}
    client.post("/api/vault/admin/listings/bulk", json={"ids": [ids["v1|201|0"], ids["v1|202|0"]], "review": "approved"})
    assert client.get("/api/members/vault/listings").json()["total"] == 2

    # 201 sells out, 202 is removed from eBay, 203 (never approved) disappears from search: all leave the search results
    ebay.items = []
    ebay.details = {"v1|201|0": _detail("201", titles["201"], "100.00", status="OUT_OF_STOCK")}
    out = _sync(client)
    assert out["refreshed"] == 1 and out["sold_out"] == 1 and out["ended"] == 2
    db.expire_all()
    state = {r.external_id: r.availability for r in db.scalars(select(VaultListing))}
    assert state == {"v1|201|0": "sold_out", "v1|202|0": "ended", "v1|203|0": "ended"}
    assert client.get("/api/members/vault/listings").json()["total"] == 0  # the store shows what can be bought
    sold = client.get("/api/members/vault/listings?sold=true").json()["items"]
    assert {s["availability"] for s in sold} == {"sold_out", "ended"} and not any(s["can_request"] for s in sold)

    # a listing eBay hasn't confirmed for longer than the freshness limit: price kept, flagged, no requests
    ebay.items = [_summary("201", titles["201"], "100.00")]
    _sync(client)
    row = db.get(VaultListing, ids["v1|201|0"])
    db.refresh(row)
    row.checked_at = utcnow() - timedelta(hours=7)
    db.commit()
    card = client.get(f"/api/members/vault/listings/{row.id}").json()
    assert card["stale"] is True and card["can_request"] is False and card["price"] == 10000


def test_sync_holds_no_write_lock_while_asking_ebay(client: TestClient, db: Session, ebay: FakeEbay, monkeypatch: pytest.MonkeyPatch) -> None:
    """While the per-listing eBay calls run, another connection can still write (the search's rows are already saved)."""
    import sqlite3

    path = db.get_bind().url.database
    can_write: list[bool] = []
    handle = ebay.transport().handler  # type: ignore[attr-defined]

    def checking(r: httpx.Request) -> httpx.Response:
        if "/buy/browse/v1/item/" in r.url.path:
            other = sqlite3.connect(path, timeout=0)
            try:
                other.execute("BEGIN IMMEDIATE")
                other.rollback()
                can_write.append(True)
            except sqlite3.OperationalError:
                can_write.append(False)
            finally:
                other.close()
        return handle(r)

    monkeypatch.setattr(client.app.state.runtime, "pokemon_transport", httpx.MockTransport(checking), raising=False)  # type: ignore[attr-defined]
    ebay.items = [_summary("301", "[NM] Lugia Pokemon Card Japanese Neo Genesis Holo", "100.00")]
    ebay.details = {"v1|301|0": _detail("301", "[NM] Lugia Pokemon Card Japanese Neo Genesis Holo", "100.00")}
    assert _sync(client)["details"] == 1
    assert can_write == [True]


def test_not_connected_and_bad_username_are_reported(client: TestClient, db: Session) -> None:
    out = _sync(client)
    assert out["status"] == "not_connected" and "eBay isn't connected" in out["message"]
    overview = client.get("/api/vault/admin/overview").json()
    assert overview["ebay"]["connected"] is False and overview["sources"][0]["last_status"] == "not_connected"


def test_empty_store_says_check_the_username(client: TestClient, ebay: FakeEbay) -> None:
    out = _sync(client)
    assert out["status"] == "ok" and "Check the eBay username" in out["message"]


# --- Request to Buy ------------------------------------------------------------------------------------------------------


def test_request_to_buy_is_a_request_not_an_order(client: TestClient, db: Session, ebay: FakeEbay) -> None:
    ebay.items = [_summary("301", "[NM] Lugia Pokemon Card Japanese Neo Genesis Holo", "899.99")]
    ebay.details = {"v1|301|0": _detail("301", ebay.items[0]["title"], "899.99")}
    _sync(client)
    lid = db.scalar(select(VaultListing.id))
    client.patch(f"/api/vault/admin/listings/{lid}", json={"review": "approved"})

    assert client.post(f"/api/members/vault/listings/{lid}/request", json={"contact": "@ash"}).status_code == 401
    _member(db, "ash", client)
    r = client.post(f"/api/members/vault/listings/{lid}/request", json={"contact": "@ash_tg", "note": "Is the holo clean?"})
    assert r.status_code == 200 and r.json()["status"] == "new" and r.json()["price"] == 89999
    again = client.post(f"/api/members/vault/listings/{lid}/request", json={"contact": "@ash_tg"})
    assert again.status_code == 400 and "already asked" in again.json()["detail"]
    assert client.get(f"/api/members/vault/listings/{lid}").json()["my_request"]["status"] == "new"
    # the listing itself is untouched: still available, still the seller's price, not reserved
    row = db.get(VaultListing, lid)
    db.refresh(row)
    assert row.availability == "available" and row.price_cents == 89999

    admin = client.get("/api/vault/admin/requests").json()["requests"]
    assert admin[0]["member"]["username"] == "ash" and admin[0]["note"] == "Is the holo clean?"
    client.patch(f"/api/vault/admin/requests/{admin[0]['id']}", json={"status": "contacted", "admin_note": "messaged on TG"})
    mine = client.get("/api/members/vault/my/requests").json()["requests"]
    assert mine[0]["status"] == "contacted" and mine[0]["listing"]["title"].startswith("[NM] Lugia")
    assert client.post(f"/api/members/vault/requests/{mine[0]['id']}/withdraw").json()["status"] == "withdrawn"


# --- physical cards: no NFT before verified acquisition ------------------------------------------------------------------


def test_no_nft_before_the_physical_card_is_verified_and_in_custody(client: TestClient, db: Session) -> None:
    c = cards.start_acquisition(db, "owner", title="[NM] Lugia Pokemon Card Japanese Neo Genesis Holo")
    assert c.uid.startswith("PCV-") and c.acquisition_status == "purchase_pending" and c.nft_status == "not_eligible"
    with pytest.raises(MemberError, match="from not eligible to mint pending"):
        cards.set_status(db, c.id, "nft", "mint_pending", "owner")
    with pytest.raises(MemberError, match="verified card"):
        cards.set_status(db, c.id, "custody", "in_custody", "owner")
    cards.set_status(db, c.id, "acquisition", "purchased", "owner", acquisition_cost_cents=89999, acquisition_currency="USD", acquisition_ref="eBay order 1")
    cards.set_status(db, c.id, "acquisition", "received", "owner")
    with pytest.raises(MemberError, match="condition inspection"):
        cards.set_status(db, c.id, "acquisition", "verified", "owner")
    cards.add_condition(db, c.id, "NM", "clean holo, light edge wear", True, "owner")
    with pytest.raises(MemberError, match="front photo"):
        cards.set_status(db, c.id, "acquisition", "verified", "owner")
    cards.add_photo(db, c.id, "front", "https://cdn.test/pcv/front.jpg", "owner")
    cards.set_status(db, c.id, "acquisition", "verified", "owner")
    assert c.nft_status == "not_eligible"  # verified but not yet in custody
    cards.set_status(db, c.id, "custody", "in_custody", "owner", custody_location="Vault A / Box 3")
    assert c.nft_status == "eligible"
    cards.set_status(db, c.id, "nft", "mint_pending", "owner")
    with pytest.raises(MemberError, match="Record the real mint"):
        cards.set_status(db, c.id, "nft", "minted", "owner")
    cards.set_status(db, c.id, "nft", "minted", "owner", nft_chain="base", nft_contract="0xabc", nft_token_id="1", mint_tx="0xdef")
    # redemption: the token must be burned before the card ships
    m = _member(db, "misty")
    cards.assign_owner(db, c.id, m.id, None, "owner")
    cards.request_redemption(db, c.id, m.id, {"country": "JP"})
    with pytest.raises(MemberError, match="Burn the card's NFT"):
        cards.set_status(db, c.id, "custody", "shipped", "owner")
    cards.set_status(db, c.id, "nft", "burned", "owner")
    cards.set_status(db, c.id, "custody", "shipped", "owner")
    trail = [(e.field, e.new) for e in db.scalars(select(VaultEvent).where(VaultEvent.card_id == c.id).order_by(VaultEvent.id))]
    assert ("nft", "minted") in trail and trail[-1] == ("custody", "shipped") and ("nft", "burned") in trail


# --- the Paper Exchange is retired --------------------------------------------------------------------------------------


def test_paper_exchange_is_retired(client: TestClient, db: Session) -> None:
    r = client.get("/api/members/exchange/markets")
    assert r.status_code == 410 and "/pokemon" in r.json()["detail"]
    assert client.get("/api/members/exchange/perps").status_code == 410


def test_absurd_price_filters_are_refused_not_a_server_error(client: TestClient) -> None:
    for q in ("min_price=inf", "max_price=1e300", "min_price=nan"):
        assert client.get(f"/api/members/vault/listings?{q}").status_code == 422, q
