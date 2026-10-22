"""Verified sales from Scrydex's SOLD listings (completed eBay / Fanatics Collect / Alt / ... transactions, with sold price,
sold date and the original listing URL): GET /pokemon/v1/cards/{id}/listings (1 credit per page of 100).

Scrydex attaches a sold listing to a card loosely: for 1999 Base Set Charizard #4 it returns 2021 Celebrations reprints and
even other cards numbered 4/102. So nothing is trusted as-is: every title goes through Price Chat's matcher against all
catalog cards of that Pokémon, exactly like eBay data:
  - matched to a card AND a grade -> a VERIFIED sale of that card (verified_by "source"), deduplicated by fingerprint;
  - ambiguous -> REVIEW NEEDED (nothing imported); not this card -> kept as rejected, for audit;
  - signed, error and "perfect" listings are different products and are skipped.
The sale's source is the marketplace Scrydex reports (ebay, fanatics, alt ...). Prices are Scrydex's USD values.
Never asking prices: these are completed sales only.
"""

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.pokemon import PokemonCard, PokemonSyncLog
from app.pokemon import history, service
from app.pokemon.scrydex_sync import scrydex_id
from app.providers.pokemon import Unavailable, provider
from app.providers.pokemon.base import ListingRecord
from app.providers.pokemon.scrydex import ScrydexProvider

LOG_PROVIDER = "scrydex-sales"
GRADES = (("PSA", "10"),)


def _sold_at(v: Any) -> datetime | None:
    try:
        d = datetime.strptime(str(v)[:10].replace("-", "/"), "%Y/%m/%d")
    except ValueError:
        return None
    return d.replace(hour=12, tzinfo=timezone.utc)


def records(rows: list[dict[str, Any]]) -> dict[str, list[ListingRecord]]:
    """Scrydex sold listings -> sold ListingRecords grouped by marketplace (signed / error / perfect left out)."""
    out: dict[str, list[ListingRecord]] = {}
    for r in rows:
        if r.get("is_signed") or r.get("is_error") or r.get("is_perfect"):
            continue
        sold = _sold_at(r.get("sold_at"))
        price = r.get("price")
        if not sold or not isinstance(price, (int, float)) or price <= 0 or not r.get("id") or not r.get("title"):
            continue
        market = str(r.get("source") or "scrydex").lower()[:32]
        out.setdefault(market, []).append(ListingRecord(provider=market, listing_id=f"scrydex-{r['id']}", title=str(r["title"]),
                                                        price_cents=round(float(price) * 100), currency=str(r.get("currency") or "USD")[:3],
                                                        url=r.get("url"), condition=r.get("condition"), sold_at=sold))
    return out


def recheck(db: Session) -> dict[str, Any]:
    """Re-run the matcher on every sale imported from a provider's sold listing (verified_by "source"): a sale it no longer
    attributes to the same card + grade is EXCLUDED (kept for audit, out of prices), never deleted. Then the review queue."""
    from app.models.pokemon import PokemonSale
    from app.pokemon import ebay_cards, matching

    out = {"checked": 0, "excluded": 0, "examples": []}
    touched: set[tuple[int, str, str]] = set()
    by_card: dict[int, list[Any]] = {}
    for s in db.scalars(select(PokemonSale).where(PokemonSale.verified_by == "source", PokemonSale.excluded.is_(False), PokemonSale.title.is_not(None))):
        by_card.setdefault(s.card_id, []).append(s)
    for cid, sales in by_card.items():
        card = db.get(PokemonCard, cid)
        if card is None:
            continue
        cands = ebay_cards.candidates(db, card)
        for s in sales:
            out["checked"] += 1
            m = matching.match(s.title or "", cands)
            if m.status != "matched" or m.card_id != cid or not m.grade or (m.grade.company, m.grade.grade) != (s.grading_company, s.grade):
                s.excluded = True
                out["excluded"] += 1
                touched.add((cid, s.grading_company, s.grade))
                if len(out["examples"]) < 10:
                    out["examples"].append({"sale_id": s.id, "title": s.title, "why": (m.reasons or [m.status])[:2]})
    # the REVIEW NEEDED queue of sold records: re-matched with the current matcher; an exact match becomes a verified sale,
    # a clear non-match is rejected, anything still ambiguous stays in review
    from app.models.pokemon import PokemonListing

    out.update({"review_checked": 0, "review_matched": 0, "review_rejected": 0})
    by_name: dict[str, list[PokemonCard]] = {}
    for x in db.scalars(select(PokemonListing).where(PokemonListing.kind == "sold", PokemonListing.match_status == "review")):
        out["review_checked"] += 1
        hint = db.get(PokemonCard, x.card_id) if x.card_id else None
        if hint is None:
            continue
        cands = by_name.setdefault(hint.pokemon_name, ebay_cards.candidates(db, hint))
        m = matching.match(x.title, cands)
        if m.status == "rejected":
            x.match_status, x.card_id, x.match_reasons = "rejected", None, m.reasons[:12]
            out["review_rejected"] += 1
        elif m.status == "matched" and m.grade:
            x.card_id, x.grading_company, x.grade = m.card_id, m.grade.company, m.grade.grade
            x.match_status, x.match_confidence, x.match_reasons = "matched", m.confidence, m.reasons[:12]
            if service._sale_from_listing(db, x, verified_by="source"):
                out["review_matched"] += 1
                touched.add((x.card_id, x.grading_company, x.grade))
    db.commit()
    if touched:
        history.rebuild(db, touched)
    history.invalidate()
    return out


async def sync(db: Session, settings: Any, card_ids: list[int] | None = None, days: int = 365, grades: tuple[tuple[str, str], ...] = GRADES,
               transport: Any = None, max_requests: int = 200) -> dict[str, Any]:
    """Import verified sold records for the given cards (default: the Top 100 and the largest vintage caps next to it)."""
    from app.pokemon import ebay_cards, top100

    p = provider("scrydex", settings, transport)
    assert isinstance(p, ScrydexProvider)
    p.max_requests = max_requests
    if not p.status()[0]:
        raise Unavailable(p.status()[1], "not_connected")
    ids = card_ids if card_ids is not None else top100.ebay_targets(db, limit=settings.ebay_cards_per_sync)
    log = PokemonSyncLog(provider=LOG_PROVIDER, started_at=utcnow(), status="running", requests=0, cache_hits=0, summary={}, entries=[])
    db.add(log)
    db.commit()
    summary = {"cards": 0, "listings": 0, "sales_imported": 0, "matched": 0, "review": 0, "rejected": 0, "skipped": 0}
    entries: list[dict[str, Any]] = []
    error = None
    try:
        for card in db.scalars(select(PokemonCard).where(PokemonCard.id.in_(ids))):
            sid = scrydex_id(card)
            if not sid:
                entries.append({"card_id": card.id, "status": "skipped", "reason": "no Scrydex card id"})
                summary["skipped"] += 1
                continue
            for company, grade in grades:
                rows: list[dict[str, Any]] = []
                page = 1
                while True:
                    b = await p.get(f"/pokemon/v1/cards/{sid}/listings", {"days": days, "company": company, "grade": grade, "page_size": 100, "page": page})
                    rows += (b.get("data") if isinstance(b, dict) else None) or []
                    total = b.get("total_count") if isinstance(b, dict) else None
                    if not (b.get("data") if isinstance(b, dict) else None) or not isinstance(total, int) or len(rows) >= total:
                        break
                    page += 1
                got = {"seen": 0, "sales": 0, "matched": 0, "review": 0, "rejected": 0}
                for market, items in records(rows).items():
                    r = service.store_observations(db, market, "sold", items, cards=ebay_cards.candidates(db, card))
                    for k in got:
                        got[k] += r.get(k, 0)
                summary["listings"] += len(rows)
                summary["sales_imported"] += got["sales"]
                for k in ("matched", "review", "rejected"):
                    summary[k] += got[k]
                entries.append({"card_id": card.id, "card": f"{card.name} {card.set_name} {card.edition}", "scrydex_id": sid, "grade": f"{company} {grade}",
                                "listings": len(rows), **got})
            summary["cards"] += 1
    except Unavailable as exc:
        error = exc.reason
        db.rollback()
    log = db.get(PokemonSyncLog, log.id)
    assert log is not None
    summary["requests"], summary["cache_hits"] = p.requests, p.cache_hits
    log.finished_at, log.requests, log.cache_hits, log.summary, log.entries, log.error = utcnow(), p.requests, p.cache_hits, summary, entries, error
    log.status = "error" if error and not summary["cards"] else "partial" if error else "ok"
    db.commit()
    history.invalidate()
    service.record_request(db, "scrydex", p.requests, p.info.rate_limit)
    db.commit()
    return {"status": log.status, "summary": summary, "entries": entries, "error": error}
