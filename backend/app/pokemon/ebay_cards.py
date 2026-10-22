"""eBay for ONE card + grade (card detail page, Top 100 refresh): live Browse API listings, matched to the exact catalog card.

- Active listings are ASKING prices: stored as listings, shown as such, never used as sales or prices.
- Sold items come only from Marketplace Insights (Limited Release). Without that grant eBay refuses the scope and the
  answer says so (SOLD DATA UNAVAILABLE ...); asking prices are never put in its place.
- A title is matched against the catalog cards of the same Pokémon (all sets, editions, languages), so a 1st Edition /
  Unlimited / Japanese / other-grade listing is attributed to the right card or rejected, never to this one by default.
- One search per card + grade per hour at most (eBay's default limit is 5,000 calls a day).
"""

import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.pokemon import PokemonCard, PokemonListing
from app.pokemon import service
from app.pokemon.grades import Grade
from app.providers.pokemon import Unavailable, provider

FRESH_SECONDS = 3600
_fetched: dict[tuple[int, str], float] = {}  # (card id, grade label) -> monotonic time of the last live search


def candidates(db: Session, card: PokemonCard) -> list[PokemonCard]:
    return list(db.scalars(select(PokemonCard).where(PokemonCard.pokemon_name == card.pokemon_name)))


def stored(db: Session, card: PokemonCard, g: Grade, since: Any = None) -> list[dict[str, Any]]:
    q = select(PokemonListing).where(PokemonListing.card_id == card.id, PokemonListing.kind == "active", PokemonListing.match_status == "matched",
                                     PokemonListing.grading_company == g.company, PokemonListing.grade == g.grade)
    if since is not None:
        q = q.where(PokemonListing.observed_at >= since)
    return [service.listing_out(x) for x in db.scalars(q.order_by(PokemonListing.price_cents).limit(24))]


async def card_listings(db: Session, settings: Any, card: PokemonCard, g: Grade, refresh: bool = False, transport: Any = None) -> dict[str, Any]:
    from app.providers.pokemon.ebay import EbayProvider, query_for

    ebay = provider("ebay", settings, transport)
    assert isinstance(ebay, EbayProvider)
    q = query_for(card, g.label)
    out: dict[str, Any] = {"connected": ebay.status()[0], "query": q, "grade": g.label, "listings": [], "fetched": False,
                           "sold": None, "message": None, "counts": None}
    if not out["connected"]:
        out["message"] = ebay.status()[1]
        return out
    key = (card.id, g.label)
    last = _fetched.get(key)
    if refresh or last is None or time.monotonic() - last > FRESH_SECONDS:
        try:
            items = await ebay.get_listing_data(q)
            started = utcnow()
            counts = service.store_observations(db, "ebay", "active", items, cards=candidates(db, card))
            _fetched[key] = time.monotonic()
            out["fetched"], out["counts"] = True, counts
            # this search's results that ARE this card + grade (others went to their own card, to review, or were rejected)
            out["listings"] = [x for x in stored(db, card, g, since=started.replace(microsecond=0)) if x]
            try:
                sold = await ebay.get_sales(q)
                got = service.store_observations(db, "ebay", "sold", sold, cards=candidates(db, card))
                out["sold"] = {"access": True, "seen": got.get("seen", 0), "verified_sales": got.get("sales", 0)}
            except Unavailable as exc:
                if exc.code != "no_access":
                    raise
                out["sold"] = {"access": False, "message": exc.reason}
        except Unavailable as exc:
            out["message"] = exc.reason
        service.record_request(db, "ebay", ebay.requests, ebay.info.rate_limit)
        db.commit()
    if not out["listings"]:
        out["listings"] = stored(db, card, g)
    access = EbayProvider.sold_access.get(ebay._creds()[0])
    if out["sold"] is None and access is not None:
        out["sold"] = {"access": access, "message": None if access else "SOLD DATA UNAVAILABLE FROM CURRENT EBAY API ACCESS (Marketplace Insights is not granted to these keys)"}
    return out
