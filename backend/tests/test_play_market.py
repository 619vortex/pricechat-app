"""Play Market (virtual: Play USDT is a test currency). TEST FIXTURES only: a tiny fake Base Set with fake prices."""

from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.members import packs, service
from app.members.catalog import ensure_catalog
from app.models.members import Member, MemberBinderCard, PlayLedger, PlayWallet
from app.models.pokemon import PokemonCard, PokemonProviderValuation


def _cards(db: Session) -> list[int]:
    """TEST FIXTURE: three Unlimited Base Set cards; the first two have ungraded prices ($10, $4), the third has none."""
    packs._pools.clear()
    ids = []
    for i, usd in enumerate((10, 4, None)):
        c = PokemonCard(identity_key=f"test-play-{i}", name=f"PlayMon{i}", pokemon_name="Mon", set_name="Base Set", card_number=f"{i + 1}/102",
                        year=1999, language="English", edition="Unlimited", variant="", rarity="Common", image_url=f"https://img.test/{i}.png", era="vintage")
        db.add(c)
        db.flush()
        ids.append(c.id)
        if usd:
            for cond in ("NM", "LP", "MP"):
                db.add(PokemonProviderValuation(provider="scrydex", card_id=c.id, provider_card_id="x", provider_variant="v", grading_company="RAW",
                                                grade=cond, day=utcnow().date(), kind="current", market_cents=usd * 100, currency="USD", fetched_at=utcnow()))
    db.commit()
    return ids


def _member(db: Session, name: str) -> tuple[Member, str]:
    ensure_catalog(db)
    m = service.create_member(db)
    m.username, m.username_key = name, name
    m.created_at = utcnow() - timedelta(days=5)
    db.commit()
    return m, service.new_session(db, m, 30)


def _give(db: Session, m: Member, card_id: int, count: int) -> None:
    db.add(MemberBinderCard(member_id=m.id, card_id=card_id, count=count, tier="common", pack_key="base", first_at=utcnow()))
    db.commit()


def _balance(db: Session, m: Member) -> int:
    db.expire_all()
    return db.scalar(select(PlayWallet.balance_cents).where(PlayWallet.member_id == m.id)) or 0


def _count(db: Session, m: Member, card_id: int) -> int:
    db.expire_all()
    return db.scalar(select(MemberBinderCard.count).where(MemberBinderCard.member_id == m.id, MemberBinderCard.card_id == card_id)) or 0


def test_instant_sell_list_buy_cancel_with_play_usdt(client: TestClient, db: Session) -> None:
    a, b, unpriced = _cards(db)
    ash, ash_tok = _member(db, "ash")
    misty, misty_tok = _member(db, "misty")
    _give(db, ash, a, 2)
    _give(db, ash, unpriced, 1)

    client.cookies.set("pc_session", ash_tok)
    over = client.get("/api/members/play").json()
    assert over["currency"] == "Play USDT" and "not real USDT" in over["note"] and over["balance_cents"] == 1000000  # the 10,000 starter
    assert client.get("/api/members/play").json()["balance_cents"] == 1000000  # once only
    # instant sell at the market estimate
    r = client.post("/api/members/play/sell", json={"card_id": a}).json()
    assert r["price_cents"] > 0 and r["balance_cents"] == 1000000 + r["price_cents"] and _count(db, ash, a) == 1
    earned = r["balance_cents"]  # welcome + the sale
    # an unpriced card can't be sold instantly, but it can be listed at the member's own price
    assert client.post("/api/members/play/sell", json={"card_id": unpriced}).status_code == 409
    assert client.post("/api/members/play/list", json={"card_id": unpriced}).status_code == 409  # no price, no estimate
    lst = client.post("/api/members/play/list", json={"card_id": unpriced, "price_cents": 250}).json()
    assert lst["price_cents"] == 250 and _count(db, ash, unpriced) == 0  # the copy left the binder while listed
    assert client.get("/api/members/packs/binder").json()["unique"] == 1  # only the card still held shows
    # one-tap listing at the estimate
    auto = client.post("/api/members/play/list", json={"card_id": a}).json()
    assert r["price_cents"] == 950 and r["to"] == "market"  # an instant sale: 5% below the $10 market price
    assert auto["price_cents"] == 1000 and _count(db, ash, a) == 0  # a listing defaults to the market price
    assert client.post("/api/members/play/sell", json={"card_id": a}).status_code == 409  # none left to sell
    assert client.post(f"/api/members/play/listings/{lst['listing_id']}/buy").status_code == 409  # not your own

    # misty: a listing too expensive for her balance can't be bought; nothing moves
    client.cookies.set("pc_session", misty_tok)
    assert len(client.get("/api/members/play").json()["market"]) == 2  # her starter arrives here
    pricey = client.post("/api/members/play/list", json={"card_id": b, "price_cents": 999999}).status_code
    assert pricey == 409  # not hers to list
    _give(db, misty, b, 1)
    sold = client.post("/api/members/play/sell", json={"card_id": b}).json()  # $4 → 400 Play USDT cents
    assert sold["balance_cents"] == 1000000 + sold["price_cents"]
    bought = client.post(f"/api/members/play/listings/{lst['listing_id']}/buy").json()
    assert bought["price_cents"] == 250 and _balance(db, misty) == 1000000 + sold["price_cents"] - 250 and _count(db, misty, unpriced) == 1
    assert client.post(f"/api/members/play/listings/{lst['listing_id']}/buy").status_code == 409  # sold once only
    assert _balance(db, ash) == earned + 250  # the seller is paid

    # cancel puts the card back
    client.cookies.set("pc_session", ash_tok)
    assert client.post(f"/api/members/play/listings/{auto['listing_id']}/cancel").json() == {"ok": True}
    assert _count(db, ash, a) == 1
    assert client.post(f"/api/members/play/listings/{auto['listing_id']}/cancel").status_code == 409
    # every change is in the ledger, and the ledger adds up to the balance
    for m in (ash, misty):
        total = db.scalar(select(func.sum(PlayLedger.delta_cents)).where(PlayLedger.member_id == m.id)) or 0
        assert total == _balance(db, m)
    client.cookies.clear()
    assert client.get("/api/members/play").status_code == 401


def test_auto_sell_duplicates_is_opt_in(client: TestClient, db: Session, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    ids = _cards(db)
    m, tok = _member(db, "brock")
    client.cookies.set("pc_session", tok)
    monkeypatch.setattr(packs, "COOLDOWN", timedelta(0))
    monkeypatch.setattr(packs, "draw", lambda db, key, bonus=False: ("Unlimited", [(ids[0], "common")] * 5))  # TEST: five copies of one card
    d = client.post("/api/members/packs/base/open").json()
    assert all(c["auto_sold"] is None for c in d["cards"]) and _count(db, m, ids[0]) == 5  # off: nothing sold
    assert client.post("/api/members/play/auto-sell", json={"on": True}).json() == {"auto_sell_dupes": True}
    assert client.get("/api/members/packs/binder").json()["auto_sell_dupes"] is True
    d = client.post("/api/members/packs/base/open").json()
    assert all(c["auto_sold"] for c in d["cards"])  # every copy was a duplicate: all sold
    assert _count(db, m, ids[0]) == 5 and _balance(db, m) == sum(c["auto_sold"] for c in d["cards"])
    kinds = set(db.scalars(select(PlayLedger.kind).where(PlayLedger.member_id == m.id)))
    assert kinds == {"auto_sell"}


def test_shop_search_and_instant_buy_with_the_welcome_play_usdt(client: TestClient, db: Session) -> None:
    a, b, unpriced = _cards(db)
    m, tok = _member(db, "gary")
    client.cookies.set("pc_session", tok)
    found = client.get("/api/members/play/shop?q=PlayMon").json()["cards"]
    assert [c["id"] for c in found] == [a, b] and found[0]["price_cents"] > 0  # the unpriced card isn't offered
    assert client.get("/api/members/play/shop?q=P").json()["cards"] == []  # at least 2 letters
    r = client.post("/api/members/play/buy-card", json={"card_id": a}).json()
    assert r["balance_cents"] == 1000000 - r["price_cents"] and _count(db, m, a) == 1  # paid from the starter
    assert client.post("/api/members/play/buy-card", json={"card_id": unpriced}).status_code == 409
    while _balance(db, m) >= r["price_cents"]:
        assert client.post("/api/members/play/buy-card", json={"card_id": a}).status_code == 200
    assert client.post("/api/members/play/buy-card", json={"card_id": a}).status_code == 409  # never below zero
    total = db.scalar(select(func.sum(PlayLedger.delta_cents)).where(PlayLedger.member_id == m.id))
    assert total == _balance(db, m) >= 0
    client.cookies.clear()
    assert client.get("/api/members/play/shop?q=PlayMon").status_code == 401



def test_members_from_the_small_starter_are_topped_up_once(client: TestClient, db: Session) -> None:
    from app.members import play_market

    m, tok = _member(db, "old_timer")
    play_market._credit(db, m, 100_00, "welcome", "welcome", None, "the old 100 starter", utcnow())  # TEST: an earlier member
    db.commit()
    client.cookies.set("pc_session", tok)
    assert client.get("/api/members/play").json()["balance_cents"] == 1_000_000
    assert client.get("/api/members/play").json()["balance_cents"] == 1_000_000  # once



def test_bids_hold_funds_fill_against_sales_and_listings_and_release_on_cancel(client: TestClient, db: Session) -> None:
    a, b, unpriced = _cards(db)  # a: $10 market, b: $4
    buyer, btok = _member(db, "buyer")
    seller, stok = _member(db, "seller")
    _give(db, seller, a, 3)
    client.cookies.set("pc_session", btok)
    start = client.get("/api/members/play").json()["balance_cents"]
    q = client.get(f"/api/members/play/quote/{a}").json()
    assert q["market_cents"] == 1000 and q["sell_cents"] == 950 and q["best_bid_cents"] is None
    # a bid below what an instant sale pays never wins over the house...
    low = client.post("/api/members/play/bid", json={"card_id": a, "price_cents": 900}).json()
    assert low["filled"] is False and _balance(db, buyer) == start - 900  # held
    # ...a better one does
    high = client.post("/api/members/play/bid", json={"card_id": a, "price_cents": 1200}).json()
    assert _balance(db, buyer) == start - 2100
    assert client.get("/api/members/play/shop?q=PlayMon0").json()["cards"][0]["best_bid_cents"] == 1200
    client.cookies.set("pc_session", stok)
    sold = client.post("/api/members/play/sell", json={"card_id": a}).json()
    assert sold["to"] == "bid" and sold["price_cents"] == 1200 and _count(db, buyer, a) == 1 and _count(db, seller, a) == 2
    sold2 = client.post("/api/members/play/sell", json={"card_id": a}).json()
    assert sold2["to"] == "market" and sold2["price_cents"] == 950  # the $9 bid pays less than the house
    # a listing at or below a standing bid fills it at once, at the bid
    filled = client.post("/api/members/play/list", json={"card_id": a, "price_cents": 800}).json()
    assert filled["filled_bid"] is True and filled["price_cents"] == 900 and _count(db, buyer, a) == 2 and _count(db, seller, a) == 0
    # a bid at or above an open listing buys it at once, at the listing's price
    _give(db, seller, b, 1)
    lst = client.post("/api/members/play/list", json={"card_id": b, "price_cents": 300}).json()
    client.cookies.set("pc_session", btok)
    before = _balance(db, buyer)
    got = client.post("/api/members/play/bid", json={"card_id": b, "price_cents": 500}).json()
    assert got["filled"] is True and got["price_cents"] == 300 and _balance(db, buyer) == before - 300 and lst["listing_id"]
    # cancel releases the hold; a bid can't be bigger than the balance
    mine = client.post("/api/members/play/bid", json={"card_id": unpriced, "price_cents": 100}).json()
    before = _balance(db, buyer)
    assert client.post(f"/api/members/play/bids/{mine['bid_id']}/cancel").json()["balance_cents"] == before + 100
    assert client.post(f"/api/members/play/bids/{mine['bid_id']}/cancel").status_code == 409
    assert client.post("/api/members/play/bid", json={"card_id": a, "price_cents": _balance(db, buyer) + 1}).status_code == 409
    assert client.get("/api/members/play").json()["bids"] == []
    for m in (buyer, seller):  # every Play USDT change is in the ledger
        assert (db.scalar(select(func.sum(PlayLedger.delta_cents)).where(PlayLedger.member_id == m.id)) or 0) == _balance(db, m)



def test_sell_all_dumps_every_priced_card_at_ten_percent_below(client: TestClient, db: Session) -> None:
    a, b, unpriced = _cards(db)
    m, tok = _member(db, "paperhands")
    _give(db, m, a, 2)
    _give(db, m, b, 1)
    _give(db, m, unpriced, 1)
    client.cookies.set("pc_session", tok)
    start = client.get("/api/members/play").json()["balance_cents"]
    d = client.post("/api/members/play/sell-all").json()
    assert d["cards"] == 2 and d["copies"] == 3 and d["total_cents"] == 2 * 900 + 360  # $10 → $9, $4 → $3.60
    assert d["balance_cents"] == start + d["total_cents"]
    assert _count(db, m, a) == 0 and _count(db, m, b) == 0 and _count(db, m, unpriced) == 1  # unpriced stays
    assert client.post("/api/members/play/sell-all").status_code == 409  # nothing priced left
