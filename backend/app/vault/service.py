"""Price Chat Vault: sources, the public storefront, Requests to Buy and admin curation (docs/VAULT.md).

Public = eligible (vintage Japanese, per the classifier) AND approved by an admin AND from an enabled source. What the
public sees is the source's own data: price, photos, condition text and availability are copied, never invented, and
every listing names its seller and links to the original listing.
"""

import math
import re
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.members.service import MemberError
from app.models.members import Member
from app.models.vault import VaultListing, VaultRequest, VaultSource
from app.vault import classify
from app.vault.sync import large_image, reclassify, stale

PER_PAGE = 24
REQUESTS_PER_DAY = 10
OPEN_REQUEST = ("new", "contacted")
SORTS = ("featured", "newest", "price_asc", "price_desc", "year_asc", "year_desc")


def configured_sellers(settings: Any) -> list[tuple[str, str]]:
    out = []
    for part in (settings.vault_ebay_sellers or "").split(","):
        if not part.strip():
            continue
        key, _, name = part.strip().partition("|")
        out.append((key.strip(), (name or key).strip()))
    return out


def ensure_sources(db: Session, settings: Any) -> list[VaultSource]:
    """The configured eBay sellers exist as sources (added once; an admin can disable one, it is never re-enabled here)."""
    have = {(s.kind, s.key): s for s in db.scalars(select(VaultSource))}
    changed = False
    for key, name in configured_sellers(settings):
        if ("ebay_seller", key) not in have:
            src = VaultSource(kind="ebay_seller", key=key[:64], name=name[:80], url=f"https://www.ebay.com/str/{key}", enabled=True,
                              last_status="never", last_message="", calls_today=0)
            db.add(src)
            have[("ebay_seller", key)] = src
            changed = True
    if changed:
        db.commit()
    return sorted(have.values(), key=lambda s: s.id)


def _public_q(now: datetime, include_ended: bool = False):  # type: ignore[no-untyped-def]
    q = (select(VaultListing).join(VaultSource, VaultSource.id == VaultListing.source_id)
         .where(VaultSource.enabled.is_(True), VaultListing.eligible.is_(True), VaultListing.review == "approved"))
    if include_ended:
        return q.where(or_(VaultListing.availability == "available", VaultListing.ended_at >= now - timedelta(days=14)))
    return q.where(VaultListing.availability == "available")


def _source_out(s: VaultSource) -> dict[str, Any]:
    return {"kind": s.kind, "name": s.name, "username": s.key if s.kind == "ebay_seller" else None, "url": s.url,
            "platform": "eBay" if s.kind == "ebay_seller" else "Seller catalog"}


def listing_out(r: VaultListing, src: VaultSource, settings: Any, detail: bool = False, now: datetime | None = None) -> dict[str, Any]:
    now = now or utcnow()
    is_stale = src.kind == "ebay_seller" and stale(r, settings.vault_max_age_hours, now)  # eBay's freshness rule
    imgs = [large_image(u) for u in (r.images or [])]
    out: dict[str, Any] = {
        "id": r.id, "title": r.title,
        "pokemon": r.pokemon_override or r.pokemon, "set_name": r.set_override or r.set_name, "year": r.year_override or r.year,
        "language": r.language or "Japanese",
        "condition": r.condition,  # the source's own condition ("Ungraded")
        "seller_grade": classify.seller_grade(r.title),  # the seller's tag in the title ([NM])
        "price": r.price_cents, "currency": r.currency, "shipping": r.shipping_cents,
        "availability": r.availability, "image": imgs[0] if imgs else None,
        "checked_at": r.checked_at.isoformat() if r.checked_at else None, "stale": is_stale,
        "featured": r.featured, "source": _source_out(src), "url": r.url,
        "can_request": bool(settings.vault_requests_enabled and r.availability == "available" and not is_stale and r.price_cents is not None),
    }
    if detail:
        out.update({"images": imgs, "condition_detail": r.condition_detail,
                    "seller": {"username": r.seller_username, "feedback_pct": r.seller_feedback_pct, "feedback_score": r.seller_feedback_score},
                    "specifics": {k: v for k, v in (r.aspects or {}).items() if k in ("Set", "Card Name", "Character", "Language", "Year Manufactured",
                                                                                      "Card Number", "Rarity", "Finish", "Card Condition", "Graded",
                                                                                      "Manufacturer", "Country/Region of Manufacture")},
                    "first_seen_at": r.first_seen_at.isoformat(), "ended_at": r.ended_at.isoformat() if r.ended_at else None,
                    "max_age_hours": settings.vault_max_age_hours})
    return out


def home(db: Session, settings: Any) -> dict[str, Any]:
    now = utcnow()
    rows = db.execute(_public_q(now).with_only_columns(VaultListing.pokemon, VaultListing.pokemon_override, VaultListing.set_name,
                                                       VaultListing.set_override, VaultListing.year, VaultListing.year_override,
                                                       VaultListing.price_cents)).all()
    pokemon: dict[str, int] = {}
    sets: dict[str, int] = {}
    years: dict[int, int] = {}
    prices = []
    for p, po, s, so, y, yo, price in rows:
        if po or p:
            pokemon[po or p] = pokemon.get(po or p, 0) + 1
        if so or s:
            sets[so or s] = sets.get(so or s, 0) + 1
        if yo or y:
            years[yo or y] = years.get(yo or y, 0) + 1
        if price is not None:
            prices.append(price)
    srcs = [s for s in db.scalars(select(VaultSource).where(VaultSource.enabled.is_(True)).order_by(VaultSource.id))]
    return {
        "count": len(rows),
        "facets": {"pokemon": sorted(pokemon.items(), key=lambda kv: (-kv[1], kv[0]))[:200],
                   "sets": sorted(sets.items(), key=lambda kv: kv[0]), "years": sorted(years.items()),
                   "price_min": min(prices) if prices else None, "price_max": max(prices) if prices else None},
        "sources": [{**_source_out(s), "checked_at": s.last_ok_at.isoformat() if s.last_ok_at else None} for s in srcs],
        "requests_enabled": settings.vault_requests_enabled, "max_age_hours": settings.vault_max_age_hours,
    }


def browse(db: Session, settings: Any, *, q: str = "", pokemon: str | None = None, set_name: str | None = None, year: int | None = None,
           min_price: int | None = None, max_price: int | None = None, sort: str = "featured", include_ended: bool = False,
           page: int = 1) -> dict[str, Any]:
    now = utcnow()
    query = _public_q(now, include_ended)
    pk = func.coalesce(VaultListing.pokemon_override, VaultListing.pokemon)
    st = func.coalesce(VaultListing.set_override, VaultListing.set_name)
    yr = func.coalesce(VaultListing.year_override, VaultListing.year)
    for word in re.findall(r"[\w'.-]+", q.lower())[:6]:
        like = f"%{word}%"
        query = query.where(or_(func.lower(VaultListing.title).like(like), func.lower(pk).like(like), func.lower(st).like(like)))
    if pokemon:
        query = query.where(pk == pokemon)
    if set_name:
        query = query.where(st == set_name)
    if year:
        query = query.where(yr == year)
    if min_price is not None:
        query = query.where(VaultListing.price_cents >= min_price)
    if max_price is not None:
        query = query.where(VaultListing.price_cents <= max_price)
    total = db.scalar(query.with_only_columns(func.count(VaultListing.id))) or 0
    available_first = (VaultListing.availability != "available")
    order = {
        "featured": (available_first, VaultListing.featured.desc(), VaultListing.first_seen_at.desc(), VaultListing.id.desc()),
        "newest": (available_first, VaultListing.first_seen_at.desc(), VaultListing.id.desc()),
        "price_asc": (available_first, VaultListing.price_cents.is_(None), VaultListing.price_cents.asc(), VaultListing.id),
        "price_desc": (available_first, VaultListing.price_cents.is_(None), VaultListing.price_cents.desc(), VaultListing.id),
        "year_asc": (available_first, yr.is_(None), yr.asc(), VaultListing.id),
        "year_desc": (available_first, yr.is_(None), yr.desc(), VaultListing.id),
    }[sort if sort in SORTS else "featured"]
    rows = db.execute(query.add_columns(VaultSource).order_by(*order).offset((page - 1) * PER_PAGE).limit(PER_PAGE)).all()
    return {"items": [listing_out(r, s, settings, now=now) for r, s in rows], "total": total, "page": page, "per_page": PER_PAGE,
            "pages": max(1, math.ceil(total / PER_PAGE))}


def _get_public(db: Session, listing_id: int) -> tuple[VaultListing, VaultSource]:
    row = db.execute(select(VaultListing, VaultSource).join(VaultSource, VaultSource.id == VaultListing.source_id)
                     .where(VaultListing.id == listing_id, VaultSource.enabled.is_(True), VaultListing.eligible.is_(True),
                            VaultListing.review == "approved")).first()
    if row is None:
        raise MemberError("Not found.")
    return row[0], row[1]


def detail(db: Session, settings: Any, listing_id: int, member: Member | None) -> dict[str, Any]:
    r, s = _get_public(db, listing_id)
    out = listing_out(r, s, settings, detail=True)
    mine = None
    if member is not None:
        req = db.scalar(select(VaultRequest).where(VaultRequest.listing_id == r.id, VaultRequest.member_id == member.id,
                                                   VaultRequest.status.in_(OPEN_REQUEST)).limit(1))
        mine = request_out(req, None) if req else None
    out["my_request"] = mine
    return out


# --- Requests to Buy -------------------------------------------------------------------------------------------------


def request_out(r: VaultRequest, listing: VaultListing | None, member: Member | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"id": r.id, "listing_id": r.listing_id, "status": r.status, "contact": r.contact, "note": r.note,
                           "price": r.price_cents, "currency": r.currency, "created_at": r.created_at.isoformat()}
    if listing is not None:
        out["listing"] = {"title": listing.title, "image": large_image(listing.images[0]) if listing.images else None,
                          "availability": listing.availability, "url": listing.url, "price": listing.price_cents, "currency": listing.currency}
    if member is not None:  # the admin's view: who asked, and our private note
        out["member"] = {"username": member.username, "public_id": member.public_id}
        out["admin_note"] = r.admin_note
    return out


def create_request(db: Session, settings: Any, member: Member, listing_id: int, contact: str, note: str) -> dict[str, Any]:
    if not settings.vault_requests_enabled:
        raise MemberError("Requests to Buy are paused right now.")
    r, src = _get_public(db, listing_id)
    if r.availability != "available":
        raise MemberError("This card is no longer available from the seller.")
    if (src.kind == "ebay_seller" and stale(r, settings.vault_max_age_hours)) or r.price_cents is None:
        raise MemberError("We're re-checking this listing with the seller. Try again in a little while.")
    if db.scalar(select(VaultRequest.id).where(VaultRequest.listing_id == r.id, VaultRequest.member_id == member.id,
                                               VaultRequest.status.in_(OPEN_REQUEST)).limit(1)):
        raise MemberError("You already asked for this card. We'll be in touch.")
    since = utcnow() - timedelta(days=1)
    if (db.scalar(select(func.count(VaultRequest.id)).where(VaultRequest.member_id == member.id, VaultRequest.created_at >= since)) or 0) >= REQUESTS_PER_DAY:
        raise MemberError("That's the most requests for one day. Try again tomorrow.")
    contact, note = contact.strip()[:120], note.strip()[:1000]
    req = VaultRequest(listing_id=r.id, member_id=member.id, contact=contact, note=note, price_cents=r.price_cents, currency=r.currency,
                       status="new", admin_note="")
    db.add(req)
    db.commit()
    return request_out(req, r)


def my_requests(db: Session, member: Member) -> list[dict[str, Any]]:
    rows = db.execute(select(VaultRequest, VaultListing).join(VaultListing, VaultListing.id == VaultRequest.listing_id)
                      .where(VaultRequest.member_id == member.id).order_by(VaultRequest.id.desc()).limit(100)).all()
    return [request_out(q, lst) for q, lst in rows]


def withdraw_request(db: Session, member: Member, request_id: int) -> dict[str, Any]:
    req = db.get(VaultRequest, request_id)
    if req is None or req.member_id != member.id:
        raise MemberError("Not found.")
    if req.status in OPEN_REQUEST:
        req.status = "withdrawn"
        db.commit()
    return request_out(req, db.get(VaultListing, req.listing_id))


# --- admin -----------------------------------------------------------------------------------------------------------


def admin_overview(db: Session, settings: Any) -> dict[str, Any]:
    from app.providers.pokemon import provider

    srcs = ensure_sources(db, settings)
    connected, why = provider("ebay", settings).status()
    by: dict[tuple[int, str, bool], int] = {}
    for sid, review, eligible, n in db.execute(select(VaultListing.source_id, VaultListing.review, VaultListing.eligible, func.count(VaultListing.id))
                                               .where(VaultListing.availability == "available")
                                               .group_by(VaultListing.source_id, VaultListing.review, VaultListing.eligible)):
        by[(sid, review, bool(eligible))] = n
    out = []
    for s in srcs:
        c = {"pending": by.get((s.id, "pending", True), 0), "approved": by.get((s.id, "approved", True), 0),
             "hidden": by.get((s.id, "hidden", True), 0), "excluded": sum(v for (sid, _, el), v in by.items() if sid == s.id and not el)}
        out.append({"id": s.id, "kind": s.kind, "key": s.key, "name": s.name, "url": s.url, "enabled": s.enabled,
                    "last_sync_at": s.last_sync_at.isoformat() if s.last_sync_at else None, "last_ok_at": s.last_ok_at.isoformat() if s.last_ok_at else None,
                    "last_status": s.last_status, "last_message": s.last_message, "last_counts": s.last_counts,
                    "calls_today": s.calls_today if s.calls_day == utcnow().date().isoformat() else 0, "counts": c})
    new_requests = db.scalar(select(func.count(VaultRequest.id)).where(VaultRequest.status == "new")) or 0
    return {"ebay": {"connected": connected, "message": why}, "sources": out, "new_requests": new_requests,
            "settings": {"sync_hours": round(settings.vault_sync_seconds / 3600, 2), "max_age_hours": settings.vault_max_age_hours,
                         "daily_calls": settings.vault_ebay_daily_calls, "include_graded": settings.vault_include_graded,
                         "requests_enabled": settings.vault_requests_enabled}}


def admin_listing_out(r: VaultListing, settings: Any) -> dict[str, Any]:
    v = classify.classify(r.title, r.aspects, include_graded=settings.vault_include_graded, year_override=r.year_override, set_override=r.set_override)
    return {"id": r.id, "source_id": r.source_id, "external_id": r.external_id, "title": r.title, "url": r.url,
            "image": large_image(r.images[0]) if r.images else None, "price": r.price_cents, "currency": r.currency,
            "availability": r.availability, "checked_at": r.checked_at.isoformat() if r.checked_at else None,
            "eligible": r.eligible, "exclude_reason": r.exclude_reason, "evidence": v.evidence,
            "pokemon": r.pokemon, "set_name": r.set_name, "year": r.year, "language": r.language, "graded": r.graded,
            "pokemon_override": r.pokemon_override, "set_override": r.set_override, "year_override": r.year_override,
            "review": r.review, "featured": r.featured, "condition": r.condition, "condition_detail": r.condition_detail,
            "seller_grade": classify.seller_grade(r.title), "details": r.details_at is not None}


def admin_listings(db: Session, settings: Any, *, review: str | None, eligible: bool | None, availability: str | None, q: str,
                   source_id: int | None, page: int) -> dict[str, Any]:
    query = select(VaultListing)
    if review:
        query = query.where(VaultListing.review == review)
    if eligible is not None:
        query = query.where(VaultListing.eligible.is_(eligible))
    if availability:
        query = query.where(VaultListing.availability == availability)
    if source_id:
        query = query.where(VaultListing.source_id == source_id)
    for word in re.findall(r"[\w'.-]+", q.lower())[:6]:
        query = query.where(func.lower(VaultListing.title).like(f"%{word}%"))
    total = db.scalar(query.with_only_columns(func.count(VaultListing.id))) or 0
    rows = db.scalars(query.order_by(VaultListing.first_seen_at.desc(), VaultListing.id.desc()).offset((page - 1) * 50).limit(50))
    return {"items": [admin_listing_out(r, settings) for r in rows], "total": total, "page": page, "pages": max(1, math.ceil(total / 50))}


def admin_update(db: Session, settings: Any, listing_id: int, changes: dict[str, Any]) -> dict[str, Any]:
    r = db.get(VaultListing, listing_id)
    if r is None:
        raise MemberError("Not found.")
    for f in ("pokemon_override", "set_override", "year_override"):
        if f in changes:
            v = changes[f]
            setattr(r, f, (v.strip() or None) if isinstance(v, str) else v)
    if "featured" in changes and changes["featured"] is not None:
        r.featured = bool(changes["featured"])
    reclassify(r, settings.vault_include_graded)
    review = changes.get("review")
    if review:
        if review == "approved" and not r.eligible:
            raise MemberError(f"Only vintage Japanese cards can be approved (this one: {r.exclude_reason}). Correct the set or year first if the classifier is wrong.")
        r.review, r.reviewed_at = review, utcnow()
    db.commit()
    return admin_listing_out(r, settings)


def admin_bulk(db: Session, settings: Any, ids: list[int], review: str) -> dict[str, Any]:
    done, skipped = 0, 0
    for r in db.scalars(select(VaultListing).where(VaultListing.id.in_(ids[:200]))):
        if review == "approved" and not r.eligible:
            skipped += 1
            continue
        r.review, r.reviewed_at = review, utcnow()
        done += 1
    db.commit()
    return {"updated": done, "skipped": skipped}


def admin_requests(db: Session, status: str | None) -> list[dict[str, Any]]:
    q = select(VaultRequest, VaultListing, Member).join(VaultListing, VaultListing.id == VaultRequest.listing_id).join(Member, Member.id == VaultRequest.member_id)
    if status:
        q = q.where(VaultRequest.status == status)
    return [request_out(r, lst, m) for r, lst, m in db.execute(q.order_by(VaultRequest.id.desc()).limit(200)).all()]


def admin_request_update(db: Session, request_id: int, status: str | None, admin_note: str | None) -> dict[str, Any]:
    r = db.get(VaultRequest, request_id)
    if r is None:
        raise MemberError("Not found.")
    if status:
        r.status = status
    if admin_note is not None:
        r.admin_note = admin_note.strip()[:2000]
    db.commit()
    return request_out(r, db.get(VaultListing, r.listing_id), db.get(Member, r.member_id))


def set_source(db: Session, source_id: int, enabled: bool) -> None:
    s = db.get(VaultSource, source_id)
    if s is None:
        raise MemberError("Not found.")
    s.enabled = enabled
    db.commit()


# --- seller-provided catalog (the fallback when a platform's API terms don't cover our use) ---------------------------


def add_catalog_source(db: Session, key: str, name: str, url: str | None) -> VaultSource:
    key = re.sub(r"[^a-z0-9_-]", "", key.lower())[:64]
    if not key:
        raise MemberError("Give the seller a short key (letters, numbers, - or _).")
    if db.scalar(select(VaultSource.id).where(VaultSource.kind == "seller_catalog", VaultSource.key == key)):
        raise MemberError("That seller catalog already exists.")
    s = VaultSource(kind="seller_catalog", key=key, name=name.strip()[:80] or key, url=(url or None), enabled=True, last_status="ok",
                    last_message="Seller-provided catalog: items are entered from the seller's own data and licensed photos.", calls_today=0)
    db.add(s)
    db.commit()
    return s


def upsert_catalog_item(db: Session, settings: Any, source_id: int, data: dict[str, Any]) -> dict[str, Any]:
    s = db.get(VaultSource, source_id)
    if s is None or s.kind != "seller_catalog":
        raise MemberError("Items can only be added to a seller catalog.")
    sku = str(data["sku"]).strip()[:80]
    r = db.scalar(select(VaultListing).where(VaultListing.source_id == s.id, VaultListing.external_id == sku))
    now = utcnow()
    if r is None:
        r = VaultListing(source_id=s.id, external_id=sku, title="", images=[], first_seen_at=now, review="pending")
        db.add(r)
    r.title = data["title"].strip()[:300]
    r.price_cents, r.currency = data.get("price_cents"), (data.get("currency") or "USD")[:3]
    r.images = [u for u in data.get("images") or [] if u.startswith("https://")][:12]
    r.condition, r.condition_detail = data.get("condition") or "Ungraded", data.get("condition_detail")
    r.url, r.availability = data.get("url"), data.get("availability") or "available"
    r.aspects = data.get("aspects") or None
    r.seller_username, r.checked_at = s.name, now
    r.ended_at = None if r.availability == "available" else (r.ended_at or now)
    reclassify(r, settings.vault_include_graded)
    db.commit()
    return admin_listing_out(r, settings)
