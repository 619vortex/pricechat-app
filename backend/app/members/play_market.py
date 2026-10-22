"""Play Market (virtual, just for fun): sell and buy Pack Giveaway cards for Play USDT.

Play USDT is a TEST currency with no value. It is NOT Tether's USDT: it can't be bought, deposited, withdrawn,
transferred out or exchanged for anything real. It only comes from selling virtual giveaway cards and only buys virtual
giveaway cards from other members. Packs stay free; Play USDT never buys packs. Nothing here touches the real Card
Marketplace (app/market), its payments or its live-trading gates.

  buy            any card at its market price (the Pokémon Portfolio's ungraded estimate; a rarer print's "at least"
                 floor counts), from the house, instantly.
  instant sell   one copy from your binder, sold at once at 5% below the market price (SELL_PCT), or to the best
                 member bid when that pays more. Unpriced cards can't be sold instantly: list them instead.
  bid            a standing offer for a card: the amount is held from your wallet; it fills when someone sells or lists
                 that card at or below it (or at once, if a listing already is). Cancel to release the hold.
  list           one copy leaves your binder and goes on the Play Market at a price you set (default: the estimate);
                 cancel puts it back.
  buy            another member's listing, paid from your Play USDT; the card lands in your binder.
  auto-sell      opt-in: an extra copy of a card you already have is sold instantly as you pull it.

Integrity: balances move only through _credit (a compare-and-swap UPDATE that never lets a balance go below zero) and
every change is an append-only PlayLedger row, unique per (member, kind, ref). Listings change state by compare-and-swap
on (status, version). Binder copies move by guarded UPDATEs. One DB transaction per action.
"""

import secrets
from datetime import datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.members import Member, MemberBinderCard, PlayBid, PlayLedger, PlayListing, PlayWallet
from app.models.pokemon import PokemonCard
from app.members.packs import picture

CURRENCY = "Play USDT"
WELCOME_CENTS = 10_000_00  # every member starts with 10,000 Play USDT (fake), so buying and selling works from the first visit
SHOP_RESULTS = 24
SELL_PCT = 95  # an instant sale pays 5% below the market price (buying instantly pays the market price)
MAX_OPEN_BIDS = 50
MAX_PRICE_CENTS = 10_000_000_00  # 10M Play USDT: a sanity cap, not a value
MAX_OPEN_LISTINGS = 50
NOTE = ("Play USDT is a test currency for fun: not real USDT, no value, can't be bought, deposited or withdrawn, "
        "and only buys virtual giveaway cards.")


class PlayError(ValueError):
    """A refused action, with a plain-language message for the member."""


def wallet(db: Session, member: Member) -> PlayWallet:
    w = db.scalar(select(PlayWallet).where(PlayWallet.member_id == member.id))
    if w is None:
        w = PlayWallet(member_id=member.id, balance_cents=0, version=0, updated_at=utcnow())
        db.add(w)
        db.flush()
    return w


def welcome(db: Session, member: Member) -> None:
    """The starter balance (fake money): every member gets WELCOME_CENTS in total, once. Members who got the earlier,
    smaller starter are topped up to it. Idempotent: welcome ledger rows are unique per member and ref."""
    from sqlalchemy import func

    got = db.scalar(select(func.coalesce(func.sum(PlayLedger.delta_cents), 0)).where(PlayLedger.member_id == member.id,
                                                                                    PlayLedger.kind == "welcome")) or 0
    if got < WELCOME_CENTS:
        _credit(db, member, WELCOME_CENTS - int(got), "welcome", f"starter:{WELCOME_CENTS}", None,
                f"Starter balance: {WELCOME_CENTS // 100:,} Play USDT (test currency)", utcnow())


def _credit(db: Session, member: Member, delta: int, kind: str, ref: str, card_id: int | None, note: str, now: datetime) -> int:
    """Move Play USDT (delta may be negative). Refuses to go below zero. Returns the new balance."""
    wallet(db, member)
    res = db.execute(update(PlayWallet).where(PlayWallet.member_id == member.id, PlayWallet.balance_cents + delta >= 0)
                     .values(balance_cents=PlayWallet.balance_cents + delta, version=PlayWallet.version + 1, updated_at=now))
    if res.rowcount != 1:
        raise PlayError(f"Not enough {CURRENCY}.")
    db.add(PlayLedger(member_id=member.id, delta_cents=delta, kind=kind, ref=ref[:64], card_id=card_id, note=note[:200], at=now))
    db.flush()
    return int(db.scalar(select(PlayWallet.balance_cents).where(PlayWallet.member_id == member.id)) or 0)


def _take_copy(db: Session, member: Member, card_id: int) -> MemberBinderCard:
    """Take one copy of a card out of the member's binder (refused if they have none)."""
    b = db.scalar(select(MemberBinderCard).where(MemberBinderCard.member_id == member.id, MemberBinderCard.card_id == card_id))
    if b is None:
        raise PlayError("That card isn't in your binder.")
    res = db.execute(update(MemberBinderCard).where(MemberBinderCard.id == b.id, MemberBinderCard.count >= 1)
                     .values(count=MemberBinderCard.count - 1))
    if res.rowcount != 1:
        raise PlayError("That card isn't in your binder.")
    db.flush()
    db.refresh(b)
    return b


def _sync(db: Session, *members: Member | None) -> None:
    """Members who show their pulls in their portfolio: keep those virtual holdings in step with the binder."""
    from app.portfolio.service import sync_giveaway

    for m in members:
        if m is not None and m.giveaway_in_portfolio:
            sync_giveaway(db, m)


def _give_copy(db: Session, member_id: int, card_id: int, tier: str, pack_key: str, now: datetime) -> None:
    res = db.execute(update(MemberBinderCard).where(MemberBinderCard.member_id == member_id, MemberBinderCard.card_id == card_id)
                     .values(count=MemberBinderCard.count + 1))
    if res.rowcount != 1:
        db.add(MemberBinderCard(member_id=member_id, card_id=card_id, count=1, tier=tier, first_at=now, pack_key=pack_key))
    db.flush()


def estimate(db: Session, card_id: int) -> int | None:
    from app.members.packs import values_for

    card = db.get(PokemonCard, card_id)
    if card is None:
        return None
    return values_for(db, [card]).get(card_id, (None, False))[0]


def sell_price(market: int | None) -> int | None:
    return market * SELL_PCT // 100 if market else None


def _best_bid(db: Session, card_id: int, at_least: int, not_member: int) -> PlayBid | None:
    return db.scalar(select(PlayBid).where(PlayBid.card_id == card_id, PlayBid.status == "open", PlayBid.price_cents >= at_least,
                                           PlayBid.bidder_id != not_member).order_by(PlayBid.price_cents.desc(), PlayBid.created_at).limit(1))


def _fill_bid(db: Session, bid: PlayBid, seller: Member, now: datetime) -> None:
    """The seller's copy (already taken from their binder) goes to the bidder; the held amount goes to the seller."""
    from app.members.packs import tier_of

    res = db.execute(update(PlayBid).where(PlayBid.id == bid.id, PlayBid.status == "open", PlayBid.version == bid.version)
                     .values(status="filled", version=PlayBid.version + 1, seller_id=seller.id, closed_at=now))
    if res.rowcount != 1:
        raise PlayError("That bid has just changed. Try again.")
    card = db.get(PokemonCard, bid.card_id)
    _credit(db, seller, bid.price_cents, "bid_sale", f"bid:{bid.id}", bid.card_id, f"Sold {card.name if card else 'a card'} to a bid", now)
    _give_copy(db, bid.bidder_id, bid.card_id, tier_of(card.rarity, card.edition, card.variant) if card else "common", "bid", now)
    _sync(db, db.get(Member, bid.bidder_id))


def sell_now(db: Session, member: Member, card_id: int, kind: str = "instant_sell", ref: str | None = None,
             now: datetime | None = None, commit: bool = True) -> dict[str, Any]:
    """Sell one copy instantly: to the best member bid when it pays more, else to the house at 5% below market."""
    now = now or utcnow()
    house = sell_price(estimate(db, card_id))
    bid = _best_bid(db, card_id, (house or 0) + 1, member.id)
    if bid is not None:
        _take_copy(db, member, card_id)
        _fill_bid(db, bid, member, now)
        balance = int(db.scalar(select(PlayWallet.balance_cents).where(PlayWallet.member_id == member.id)) or 0)
        if commit:
            _sync(db, member)
            db.commit()
        return {"ok": True, "card_id": card_id, "price_cents": bid.price_cents, "balance_cents": balance, "to": "bid"}
    price = house
    if not price:
        raise PlayError("This card has no market price yet, so it can't be sold instantly. List it with your own price instead.")
    _take_copy(db, member, card_id)
    name = db.scalar(select(PokemonCard.name).where(PokemonCard.id == card_id)) or "card"
    balance = _credit(db, member, price, kind, ref or f"{card_id}:{secrets.token_hex(6)}", card_id, f"Sold {name}", now)
    if commit:
        _sync(db, member)
        db.commit()
    return {"ok": True, "card_id": card_id, "price_cents": price, "balance_cents": balance, "to": "market"}


def list_card(db: Session, member: Member, card_id: int, price_cents: int | None = None, now: datetime | None = None) -> dict[str, Any]:
    """Put one copy on the Play Market (default price: its market estimate)."""
    now = now or utcnow()
    price = price_cents if price_cents is not None else estimate(db, card_id)
    if not price or price < 1:
        raise PlayError("Set a price: this card has no market estimate yet.")
    if price > MAX_PRICE_CENTS:
        raise PlayError("That price is too high.")
    open_n = len(db.scalars(select(PlayListing.id).where(PlayListing.seller_id == member.id, PlayListing.status == "open")).all())
    if open_n >= MAX_OPEN_LISTINGS:
        raise PlayError(f"You can have up to {MAX_OPEN_LISTINGS} cards listed at once.")
    bid = _best_bid(db, card_id, price, member.id)
    if bid is not None:  # someone already bids this much or more: sold at once, at their bid
        _take_copy(db, member, card_id)
        _fill_bid(db, bid, member, now)
        _sync(db, member)
        db.commit()
        return {"ok": True, "listing_id": None, "price_cents": bid.price_cents, "filled_bid": True}
    b = _take_copy(db, member, card_id)
    row = PlayListing(seller_id=member.id, card_id=card_id, tier=b.tier, pack_key=b.pack_key, price_cents=int(price), status="open",
                      version=0, created_at=now)
    db.add(row)
    _sync(db, member)
    db.commit()
    return {"ok": True, "listing_id": row.id, "price_cents": row.price_cents}


def _close(db: Session, listing: PlayListing, status: str, now: datetime, buyer_id: int | None = None) -> None:
    res = db.execute(update(PlayListing).where(PlayListing.id == listing.id, PlayListing.status == "open", PlayListing.version == listing.version)
                     .values(status=status, version=PlayListing.version + 1, closed_at=now, buyer_id=buyer_id))
    if res.rowcount != 1:
        db.rollback()
        raise PlayError("That listing has just changed. Refresh and try again.")


def cancel(db: Session, member: Member, listing_id: int, now: datetime | None = None) -> dict[str, Any]:
    now = now or utcnow()
    row = db.get(PlayListing, listing_id)
    if row is None or row.seller_id != member.id or row.status != "open":
        raise PlayError("That listing isn't open.")
    _close(db, row, "cancelled", now)
    _give_copy(db, member.id, row.card_id, row.tier, row.pack_key, now)
    _sync(db, member)
    db.commit()
    return {"ok": True}


def buy(db: Session, member: Member, listing_id: int, now: datetime | None = None) -> dict[str, Any]:
    now = now or utcnow()
    row = db.get(PlayListing, listing_id)
    if row is None or row.status != "open":
        raise PlayError("That card has already been sold.")
    if row.seller_id == member.id:
        raise PlayError("That's your own listing.")
    seller = db.get(Member, row.seller_id)
    name = db.scalar(select(PokemonCard.name).where(PokemonCard.id == row.card_id)) or "card"
    try:
        _close(db, row, "sold", now, buyer_id=member.id)
        balance = _credit(db, member, -row.price_cents, "purchase", f"listing:{row.id}", row.card_id, f"Bought {name}", now)
        if seller is not None:
            _credit(db, seller, row.price_cents, "sale", f"listing:{row.id}", row.card_id, f"Sold {name}", now)
        _give_copy(db, member.id, row.card_id, row.tier, row.pack_key, now)
        _sync(db, member, seller)
    except PlayError:
        db.rollback()
        raise
    db.commit()
    return {"ok": True, "card_id": row.card_id, "price_cents": row.price_cents, "balance_cents": balance}


def shop(db: Session, q: str) -> list[dict[str, Any]]:
    """Cards anyone can buy instantly from the Play Market (the house), at their market estimate. Only cards with an
    estimate are offered; nothing else about the card changes (it's a virtual copy in your binder)."""
    from app.members.packs import tier_of, values_for

    q = (q or "").strip()[:60]
    if len(q) < 2:
        return []
    cards = list(db.scalars(select(PokemonCard).where(PokemonCard.language == "English", PokemonCard.image_url.is_not(None),
                                                      PokemonCard.name.ilike(f"%{q}%")).order_by(PokemonCard.year, PokemonCard.set_name)
                            .limit(SHOP_RESULTS * 3)))
    vals = values_for(db, cards)
    bids = _best_bids(db, [c.id for c in cards])
    out = []
    for c in cards:
        cents, floor = vals.get(c.id, (None, False))
        if cents:
            out.append({"id": c.id, "name": c.name, "set": c.set_name, "number": c.card_number, "year": c.year, "edition": c.edition,
                        "image": picture(c), "tier": tier_of(c.rarity, c.edition, c.variant), "price_cents": cents, "value_floor": floor,
                        "sell_cents": sell_price(cents), "best_bid_cents": bids.get(c.id)})
        if len(out) >= SHOP_RESULTS:
            break
    return out


def buy_card(db: Session, member: Member, card_id: int, now: datetime | None = None) -> dict[str, Any]:
    """Buy one virtual copy of a card instantly from the house, at its market estimate, with Play USDT."""
    from app.members.packs import tier_of

    now = now or utcnow()
    card = db.get(PokemonCard, card_id)
    price = estimate(db, card_id)
    if card is None or not price:
        raise PlayError("This card has no market estimate, so it can't be bought instantly.")
    welcome(db, member)
    try:
        balance = _credit(db, member, -price, "shop_buy", f"{card_id}:{secrets.token_hex(6)}", card_id, f"Bought {card.name}", now)
    except PlayError:
        db.rollback()
        raise
    _give_copy(db, member.id, card_id, tier_of(card.rarity, card.edition, card.variant), "shop", now)
    _sync(db, member)
    db.commit()
    return {"ok": True, "card_id": card_id, "price_cents": price, "balance_cents": balance}


def place_bid(db: Session, member: Member, card_id: int, price_cents: int, now: datetime | None = None) -> dict[str, Any]:
    """Bid for a card. A member listing at or below it is bought at once (at its own, lower price); otherwise the bid
    stands and the amount is held from the wallet until it fills or is cancelled."""
    now = now or utcnow()
    card = db.get(PokemonCard, card_id)
    if card is None:
        raise PlayError("Unknown card.")
    if not 1 <= price_cents <= MAX_PRICE_CENTS:
        raise PlayError("Set a bid between 0.01 and 10,000,000 Play USDT.")
    welcome(db, member)
    cheapest = db.scalar(select(PlayListing).where(PlayListing.card_id == card_id, PlayListing.status == "open", PlayListing.seller_id != member.id,
                                                   PlayListing.price_cents <= price_cents).order_by(PlayListing.price_cents, PlayListing.created_at).limit(1))
    if cheapest is not None:
        out = buy(db, member, cheapest.id, now)
        return {**out, "filled": True}
    n = len(db.scalars(select(PlayBid.id).where(PlayBid.bidder_id == member.id, PlayBid.status == "open")).all())
    if n >= MAX_OPEN_BIDS:
        raise PlayError(f"You can have up to {MAX_OPEN_BIDS} open bids.")
    bid = PlayBid(bidder_id=member.id, card_id=card_id, price_cents=int(price_cents), status="open", version=0, created_at=now)
    db.add(bid)
    db.flush()
    try:
        balance = _credit(db, member, -bid.price_cents, "bid_hold", f"bid:{bid.id}", card_id, f"Bid held for {card.name}", now)
    except PlayError:
        db.rollback()
        raise
    db.commit()
    return {"ok": True, "bid_id": bid.id, "price_cents": bid.price_cents, "balance_cents": balance, "filled": False}


def cancel_bid(db: Session, member: Member, bid_id: int, now: datetime | None = None) -> dict[str, Any]:
    now = now or utcnow()
    bid = db.get(PlayBid, bid_id)
    if bid is None or bid.bidder_id != member.id or bid.status != "open":
        raise PlayError("That bid isn't open.")
    res = db.execute(update(PlayBid).where(PlayBid.id == bid.id, PlayBid.status == "open", PlayBid.version == bid.version)
                     .values(status="cancelled", version=PlayBid.version + 1, closed_at=now))
    if res.rowcount != 1:
        db.rollback()
        raise PlayError("That bid has just changed. Refresh and try again.")
    balance = _credit(db, member, bid.price_cents, "bid_refund", f"bid:{bid.id}", bid.card_id, "Bid cancelled: amount released", now)
    db.commit()
    return {"ok": True, "balance_cents": balance}


def _best_bids(db: Session, card_ids: list[int]) -> dict[int, int]:
    from sqlalchemy import func

    if not card_ids:
        return {}
    return dict(db.execute(select(PlayBid.card_id, func.max(PlayBid.price_cents)).where(PlayBid.card_id.in_(card_ids), PlayBid.status == "open")
                           .group_by(PlayBid.card_id)).all())


def quote(db: Session, member: Member, card_id: int) -> dict[str, Any]:
    """Everything one card's trade sheet needs: market price, instant-sale price, best bid, cheapest listing, mine."""
    card = db.get(PokemonCard, card_id)
    if card is None:
        raise PlayError("Unknown card.")
    market = estimate(db, card_id)
    best = _best_bids(db, [card_id]).get(card_id)
    cheapest = db.scalar(select(PlayListing.price_cents).where(PlayListing.card_id == card_id, PlayListing.status == "open",
                                                               PlayListing.seller_id != member.id).order_by(PlayListing.price_cents).limit(1))
    mine = db.scalars(select(PlayBid).where(PlayBid.bidder_id == member.id, PlayBid.card_id == card_id, PlayBid.status == "open")).all()
    owned = db.scalar(select(MemberBinderCard.count).where(MemberBinderCard.member_id == member.id, MemberBinderCard.card_id == card_id)) or 0
    return {"card_id": card_id, "market_cents": market, "sell_cents": max(sell_price(market) or 0, best or 0) or None,
            "best_bid_cents": best, "cheapest_listing_cents": cheapest, "owned": owned,
            "my_bids": [{"id": b.id, "price_cents": b.price_cents} for b in mine]}


DUMP_PCT = 90  # "Sell all": everything priced goes at once, 10% below market (paper hands cost extra)


def dump_all(db: Session, member: Member, now: datetime | None = None) -> dict[str, Any]:
    """Sell every priced card in the binder at once, at 10% below market. Unpriced cards stay. One ledger row per card."""
    from app.members.packs import values_for

    now = now or utcnow()
    rows = db.execute(select(MemberBinderCard, PokemonCard).join(PokemonCard, PokemonCard.id == MemberBinderCard.card_id)
                      .where(MemberBinderCard.member_id == member.id, MemberBinderCard.count > 0)).all()
    vals = values_for(db, [c for _, c in rows])
    tag = secrets.token_hex(5)
    total = copies = cards = 0
    for b, c in rows:
        market = vals.get(c.id, (None, False))[0]
        if not market:
            continue
        n = b.count
        res = db.execute(update(MemberBinderCard).where(MemberBinderCard.id == b.id, MemberBinderCard.count == n).values(count=0))
        if res.rowcount != 1:
            continue  # changed meanwhile: left alone
        price = market * DUMP_PCT // 100 * n
        _credit(db, member, price, "dump", f"{tag}:{c.id}", c.id, f"Sell all: {n} × {c.name}", now)
        total, copies, cards = total + price, copies + n, cards + 1
    if not cards:
        db.rollback()
        raise PlayError("Nothing to sell: no priced cards in your binder.")
    balance = int(db.scalar(select(PlayWallet.balance_cents).where(PlayWallet.member_id == member.id)) or 0)
    _sync(db, member)
    db.commit()
    return {"ok": True, "cards": cards, "copies": copies, "total_cents": total, "balance_cents": balance}


def set_auto_sell(db: Session, member: Member, on: bool) -> bool:
    member.auto_sell_dupes = bool(on)
    db.commit()
    return member.auto_sell_dupes


def _listing_out(r: PlayListing, c: PokemonCard, sellers: dict[int, str], me: int, est: int | None = None) -> dict[str, Any]:
    return {"id": r.id, "card": {"id": c.id, "name": c.name, "set": c.set_name, "number": c.card_number, "year": c.year, "edition": c.edition,
                                 "image": picture(c), "tier": r.tier},
            "price_cents": r.price_cents, "estimate_cents": est,  # the market estimate, to show the price against it
            "seller": sellers.get(r.seller_id, "a member"), "mine": r.seller_id == me, "listed_at": r.created_at.isoformat()}


def overview(db: Session, member: Member) -> dict[str, Any]:
    """Wallet, the open market (newest first), the member's own listings and recent Play USDT activity."""
    welcome(db, member)
    w = wallet(db, member)
    db.refresh(w)
    rows = db.execute(select(PlayListing, PokemonCard).join(PokemonCard, PokemonCard.id == PlayListing.card_id)
                      .where(PlayListing.status == "open").order_by(PlayListing.created_at.desc()).limit(120)).all()
    ids = {r.seller_id for r, _ in rows}
    sellers = dict(db.execute(select(Member.id, Member.username).where(Member.id.in_(ids))).all()) if ids else {}
    from app.members.packs import values_for

    ests = values_for(db, list({c.id: c for _, c in rows}.values())) if rows else {}
    listings = [_listing_out(r, c, sellers, member.id, ests.get(c.id, (None, False))[0]) for r, c in rows]
    ledger = db.scalars(select(PlayLedger).where(PlayLedger.member_id == member.id).order_by(PlayLedger.at.desc(), PlayLedger.id.desc()).limit(40)).all()
    my_bids = db.execute(select(PlayBid, PokemonCard).join(PokemonCard, PokemonCard.id == PlayBid.card_id)
                         .where(PlayBid.bidder_id == member.id, PlayBid.status == "open").order_by(PlayBid.created_at.desc())).all()
    db.commit()
    return {"currency": CURRENCY, "note": NOTE, "balance_cents": w.balance_cents, "auto_sell_dupes": bool(member.auto_sell_dupes),
            "market": [x for x in listings if not x["mine"]], "mine": [x for x in listings if x["mine"]], "sell_pct": SELL_PCT,
            "bids": [{"id": b.id, "price_cents": b.price_cents, "created_at": b.created_at.isoformat(),
                      "card": {"id": c.id, "name": c.name, "set": c.set_name, "year": c.year, "edition": c.edition, "image": picture(c)}}
                     for b, c in my_bids],
            "activity": [{"delta_cents": e.delta_cents, "kind": e.kind, "note": e.note, "at": e.at.isoformat()} for e in ledger]}
