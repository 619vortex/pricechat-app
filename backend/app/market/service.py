"""Card Marketplace logic. Every state change is a compare-and-swap (status + version) inside one transaction, backed by
partial unique indexes, so two buyers can never both reserve a card and a retried request never creates a second order.

Settlement (`settle`) moves nothing until it has verified: (1) the payment, (2) the exact item, (3) the seller's right
to transfer it, (4) the buyer's entitlement, (5) the mode's conditions. It is idempotent: ledger entries are keyed.
"""

import secrets
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config.settings import Settings
from app.db.base import utcnow
from app.market import compliance, payments, raw
from app.members.service import MemberError
from app.models.market import (
    OPEN_LISTING, MarketAudit, MarketDispute, MarketItem, MarketLedgerEntry, MarketListing, MarketListingEvent, MarketOffer, MarketOrder,
    MarketPayment, MarketPhoto, MarketRedemption, MarketReport, MarketSeller, MarketTransfer, MarketWatch,
)
from app.models.members import Member
from app.models.pokemon import PokemonCard
from app.models.portfolio import PokemonHolding, PokemonPortfolio
from app.pokemon.grades import GradeError, normalise

PUBLIC_STATUSES = ("active", "reserved", "sold")
SELLER_EDITABLE = ("draft", "awaiting_verification", "awaiting_custody", "verified", "active")
LISTINGS_PER_DAY = 20
OFFERS_PER_DAY = 30
REPORTS_PER_DAY = 10
MIN_PRICE, MAX_PRICE = 100, 100_000_000  # 1 USDT .. 1,000,000 USDT (cents)
SHIPPING = {"AE": 2500, "US": 4500, "GB": 4500, "EU": 4500, "JP": 4500}  # USDT cents, insured courier (estimates until a partner quotes)
SHIPPING_OTHER = 6500
HANDLING = 1000
EU = {"AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IE", "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT",
      "RO", "SK", "SI", "ES", "SE"}


class Conflict(MemberError):
    """The thing changed under us (sold, reserved, expired...): HTTP 409."""


# --- helpers -------------------------------------------------------------------------------------------------------------


def audit(db: Session, actor: str, action: str, target: str, **data: Any) -> None:
    db.add(MarketAudit(at=utcnow(), actor=actor, action=action, target=target, data=data))


def _event(db: Session, listing: MarketListing, actor: str, kind: str, frm: str | None, to: str | None, price: int | None = None) -> None:
    db.add(MarketListingEvent(listing_id=listing.id, at=utcnow(), actor=actor, kind=kind, from_status=frm, to_status=to,
                              price_cents=price if price is not None else listing.price_cents))


def _cas(db: Session, model: Any, row: Any, expect: tuple[str, ...], **values: Any) -> None:
    """Move `row` out of one of `expect` statuses atomically, or raise Conflict (someone else got there first)."""
    db.flush()
    res = db.execute(update(model).where(model.id == row.id, model.status.in_(expect), model.version == row.version)
                     .values(version=row.version + 1, **values).execution_options(synchronize_session=False))
    if res.rowcount != 1:  # type: ignore[attr-defined]
        db.rollback()
        raise Conflict("This changed a moment ago. Refresh and try again.")
    db.refresh(row)


def _cas_item(db: Session, item: MarketItem, expect: tuple[str, ...], **values: Any) -> None:
    db.flush()
    res = db.execute(update(MarketItem).where(MarketItem.id == item.id, MarketItem.custody_status.in_(expect), MarketItem.version == item.version)
                     .values(version=item.version + 1, **values).execution_options(synchronize_session=False))
    if res.rowcount != 1:  # type: ignore[attr-defined]
        db.rollback()
        raise Conflict("This card's custody changed a moment ago. Refresh and try again.")
    db.refresh(item)


def mask_cert(cert: str) -> str:
    return cert if len(cert) <= 4 else "•" * (len(cert) - 4) + cert[-4:]


def seller_of(db: Session, member: Member) -> MarketSeller:
    s = db.scalar(select(MarketSeller).where(MarketSeller.member_id == member.id))
    if s is None:
        s = MarketSeller(member_id=member.id)
        db.add(s)
        db.flush()
    return s


def seller_out(db: Session, member: Member) -> dict[str, Any]:
    s = db.scalar(select(MarketSeller).where(MarketSeller.member_id == member.id))
    return {"status": s.status if s else "unverified", "level": s.level if s else 0, "country": s.country if s else None,
            "sandbox": bool(s and s.sandbox), "can_list": bool(s and (s.status == "verified" or s.sandbox))}


def request_verification(db: Session, member: Member, country: str, about: str) -> dict[str, Any]:
    """Only verified sellers list. A member asks; the owner reviews (and, once a KYC provider is connected, it checks)."""
    s = seller_of(db, member)
    if s.status in ("verified", "pending"):
        return seller_out(db, member)
    if s.status == "suspended":
        raise MemberError("Your seller account is suspended. Contact support.")
    country = country.strip().upper()
    if len(country) != 2 or not country.isalpha():
        raise MemberError("Pick your country.")
    if len(about.strip()) < 20:
        raise MemberError("Tell us a little about your collection and where your cards come from (20+ characters).")
    s.status, s.country = "pending", country
    s.notes = (s.notes + f"\n{utcnow().date()}: verification requested: {about.strip()[:1000]}")[-4000:]
    audit(db, f"member:{member.id}", "seller_verification_requested", f"member:{member.id}", country=country)
    db.commit()
    return seller_out(db, member)


def estimate(db: Session, card_id: int, company: str, grade: str) -> dict[str, Any]:
    from app.portfolio import valuation

    h = SimpleNamespace(card_id=card_id, kind="graded", grading_company=company, grade=grade, condition_low=None, condition_high=None)
    return valuation.Pricer(db, [h]).value(h).as_dict()


def item_estimate(db: Session, item: MarketItem) -> dict[str, Any]:
    """The market estimate for this exact item: its official grade, or (RAW) its AI condition range."""
    if item.grading_company != "RAW":
        return estimate(db, item.card_id, item.grading_company, item.grade)
    from app.portfolio import valuation

    h = SimpleNamespace(card_id=item.card_id, kind="raw", grading_company=None, grade=None,
                        condition_low=item.condition_low, condition_high=item.condition_high)
    return valuation.Pricer(db, [h]).value(h).as_dict()


def _today_count(db: Session, model: Any, col: Any, member_id: int) -> int:
    return db.scalar(select(func.count()).select_from(model).where(col == member_id, model.created_at >= utcnow() - timedelta(days=1))) or 0


def _visible(viewer_sandbox: bool) -> Any:
    return MarketListing.is_sandbox.is_(False) if not viewer_sandbox else or_(MarketListing.is_sandbox.is_(False), MarketListing.is_sandbox.is_(True))


def sandbox_enabled(db: Session, member: Member | None) -> bool:
    if member is None:
        return False
    s = db.scalar(select(MarketSeller).where(MarketSeller.member_id == member.id))
    return bool(s and s.sandbox)


# --- presentation --------------------------------------------------------------------------------------------------------


def _photos(db: Session, item_ids: list[int]) -> dict[int, list[MarketPhoto]]:
    out: dict[int, list[MarketPhoto]] = {}
    for p in db.scalars(select(MarketPhoto).where(MarketPhoto.item_id.in_(item_ids or [0])).order_by(MarketPhoto.id)):
        out.setdefault(p.item_id, []).append(p)
    return out


CUSTODY_LABEL = {
    "self_custody": "With the seller (not vaulted)", "submitted": "On its way to the vault", "received": "Received by the vault, inspection pending",
    "vaulted": "Vaulted", "release_requested": "Redemption requested (locked)", "released": "Released from the vault", "redeemed": "Redeemed (shipped to owner)",
}


def listing_out(db: Session, l: MarketListing, item: MarketItem, card: PokemonCard, *, viewer: Member | None = None, detail: bool = False,
                photos: list[MarketPhoto] | None = None, est: dict[str, Any] | None = None, seller: Member | None = None) -> dict[str, Any]:
    from app.portfolio.service import card_brief

    mine = viewer is not None and viewer.id == l.seller_id
    est = est if est is not None else item_estimate(db, item)
    value = est.get("value")
    diff = round((l.price_cents - value) / value * 100, 1) if value and l.price_cents else None
    out: dict[str, Any] = {
        "id": l.id, "status": l.status, "sandbox": l.is_sandbox, "card": card_brief(card),
        "grading_company": item.grading_company, "grade": item.grade, "cert": item.cert_number if mine else mask_cert(item.cert_number),
        "price": l.price_cents, "currency": "USDT", "listed_at": l.listed_at.isoformat() if l.listed_at else None,
        "sold_at": l.sold_at.isoformat() if l.sold_at else None,
        "estimate": {"value": value, "low": est.get("low"), "high": est.get("high"), "source": est.get("source"), "confidence": est.get("confidence"),
                     "note": est.get("note"), "as_of": est.get("as_of"), "currency": "USD"},
        "diff_pct": diff, "uncertain": est.get("confidence") in (None, "low") or value is None,
        "custody": {"status": item.custody_status, "label": CUSTODY_LABEL.get(item.custody_status, item.custody_status),
                    "vaulted": item.custody_status == "vaulted", "partner": item.vault_partner if item.custody_status == "vaulted" else None,
                    "insurance": item.insurance_status, "insurance_note": item.insurance_note if item.insurance_status == "confirmed" else ""},
        "verified": item.verification_status == "verified",
        "photos": [{"id": p.id, "side": p.side, "source": p.source} for p in (photos or [])],
        "mine": mine,
        "kind": "raw" if item.grading_company == "RAW" else "graded",
    }
    if item.grading_company == "RAW":
        out["cert"] = None
        out["condition"] = {"label": item.grade, "low": item.condition_low, "high": item.condition_high,
                            "ai": raw.report_out(item.condition_report) if detail else None,
                            "community": raw.community(db, l.id, viewer) if detail else None}
    if seller is not None:
        s = db.scalar(select(MarketSeller).where(MarketSeller.member_id == seller.id))
        out["seller"] = {"username": seller.username, "verified": bool(s and s.status == "verified"), "level": s.level if s else 0,
                         "since": seller.created_at.isoformat() if seller.created_at else None}
    if detail:
        events = db.scalars(select(MarketListingEvent).where(MarketListingEvent.listing_id == l.id).order_by(MarketListingEvent.at)).all()
        out["history"] = [{"at": e.at.isoformat(), "kind": e.kind, "status": e.to_status, "price": e.price_cents} for e in events
                          if mine or e.kind in ("listed", "price", "reserved", "released", "sold", "cancelled")]
        out["fees"] = fees(db, l.price_cents or 0, settings=None)
        if mine:
            out.update({"ownership_evidence": l.ownership_evidence, "review_note": l.review_note, "flags": l.flags, "seller_note": l.seller_note})
        else:
            out["seller_note"] = l.seller_note
    return out


def fees(db: Session | None, price: int, settings: Settings | None) -> dict[str, Any]:
    from app.config.settings import get_settings

    s = settings or get_settings()
    buyer = price * s.market_buyer_fee_bps // 10_000
    seller = price * s.market_seller_fee_bps // 10_000
    return {"price": price, "buyer_fee": buyer, "buyer_fee_bps": s.market_buyer_fee_bps, "buyer_total": price + buyer,
            "seller_fee": seller, "seller_fee_bps": s.market_seller_fee_bps, "seller_proceeds": price - seller,
            "network_fees": {k: v["fee_estimate"] for k, v in payments.NETWORKS.items()}, "currency": "USDT"}


def _rows(db: Session, q: Any) -> list[tuple[MarketListing, MarketItem, PokemonCard]]:
    return [tuple(r) for r in db.execute(q).all()]  # type: ignore[misc]


def _base() -> Any:
    return (select(MarketListing, MarketItem, PokemonCard).join(MarketItem, MarketItem.id == MarketListing.item_id)
            .join(PokemonCard, PokemonCard.id == MarketItem.card_id))


def _out_many(db: Session, rows: list[tuple[MarketListing, MarketItem, PokemonCard]], viewer: Member | None) -> list[dict[str, Any]]:
    ph = _photos(db, [i.id for _, i, _ in rows])
    return [listing_out(db, l, i, c, viewer=viewer, photos=ph.get(i.id, [])) for l, i, c in rows]


SORTS = {"price_asc": MarketListing.price_cents.asc(), "price_desc": MarketListing.price_cents.desc(), "recent": MarketListing.listed_at.desc(),
         "sold": MarketListing.sold_at.desc()}


def browse(db: Session, viewer: Member | None, *, q: str = "", set_name: str | None = None, year: int | None = None, language: str | None = None,
           edition: str | None = None, number: str | None = None, company: str | None = None, grade: str | None = None,
           min_price: int | None = None, max_price: int | None = None, min_discount: float | None = None, vaulted: bool = False, kind: str | None = None,
           status: str = "active", sort: str = "recent", page: int = 1, per_page: int = 24) -> dict[str, Any]:
    sb = sandbox_enabled(db, viewer)
    qy = _base().where(_visible(sb), MarketListing.status == ("sold" if status == "sold" else "active"))
    for word in q.split()[:6]:
        like = f"%{word}%"
        qy = qy.where(or_(PokemonCard.name.ilike(like), PokemonCard.set_name.ilike(like), PokemonCard.card_number.ilike(like)))
    if set_name:
        qy = qy.where(PokemonCard.set_name == set_name)
    if year:
        qy = qy.where(PokemonCard.year == year)
    if language:
        qy = qy.where(PokemonCard.language == language)
    if edition:
        qy = qy.where(PokemonCard.edition == edition)
    if number:
        qy = qy.where(PokemonCard.card_number.ilike(f"{number}%"))
    if kind == "raw":
        qy = qy.where(MarketItem.grading_company == "RAW")
    elif kind == "graded":
        qy = qy.where(MarketItem.grading_company != "RAW")
    if company:
        qy = qy.where(MarketItem.grading_company == company)
    if grade:
        qy = qy.where(MarketItem.grade == grade)
    if min_price is not None:
        qy = qy.where(MarketListing.price_cents >= min_price)
    if max_price is not None:
        qy = qy.where(MarketListing.price_cents <= max_price)
    if vaulted:
        qy = qy.where(MarketItem.custody_status == "vaulted")
    if sort in SORTS:
        qy = qy.order_by(SORTS[sort], MarketListing.id.desc())
    else:
        qy = qy.order_by(MarketListing.listed_at.desc())
    total = db.scalar(select(func.count()).select_from(qy.order_by(None).subquery())) or 0
    if sort in ("value", "discount") or min_discount is not None:
        # value-based sorts need the estimate per row: bounded to the first 500 matches (indexed filters run first)
        rows = _rows(db, qy.limit(500))
        items = _out_many(db, rows, viewer)
        if min_discount is not None:
            items = [x for x in items if x["diff_pct"] is not None and -x["diff_pct"] >= min_discount]
        if sort == "value":
            items.sort(key=lambda x: -(x["estimate"]["value"] or 0))
        elif sort == "discount":
            items.sort(key=lambda x: x["diff_pct"] if x["diff_pct"] is not None else 1e9)
        total = len(items) if min_discount is not None else total
        items = items[(page - 1) * per_page: page * per_page]
    else:
        items = _out_many(db, _rows(db, qy.offset((page - 1) * per_page).limit(per_page)), viewer)
    return {"items": items, "total": total, "page": page, "per_page": per_page, "sandbox": sb}


def home(db: Session, viewer: Member | None, settings: Settings) -> dict[str, Any]:
    sb = sandbox_enabled(db, viewer)
    active = _base().where(_visible(sb), MarketListing.status == "active")
    newest = _out_many(db, _rows(db, active.order_by(MarketListing.listed_at.desc()).limit(8)), viewer)
    cheapest = _out_many(db, _rows(db, active.order_by(MarketListing.price_cents.asc()).limit(8)), viewer)
    pool = _out_many(db, _rows(db, active.order_by(MarketListing.price_cents.desc()).limit(60)), viewer)
    valuable = sorted(pool, key=lambda x: -(x["estimate"]["value"] or 0))[:8]
    featured = [x for x in sorted(pool, key=lambda x: (x["diff_pct"] if x["diff_pct"] is not None else 1e9)) if x["custody"]["vaulted"]][:4]
    sold = _out_many(db, _rows(db, _base().where(_visible(sb), MarketListing.status == "sold").order_by(MarketListing.sold_at.desc()).limit(8)), viewer)
    names = [r[0] for r in db.execute(select(PokemonCard.pokemon_name, func.count()).join(MarketItem, MarketItem.card_id == PokemonCard.id)
                                      .join(MarketListing, MarketListing.item_id == MarketItem.id)
                                      .where(_visible(sb), MarketListing.status.in_(("active", "sold")))
                                      .group_by(PokemonCard.pokemon_name).order_by(func.count().desc()).limit(8))]
    return {"mode": compliance.mode(settings), "sandbox": sb, "featured": featured, "newest": newest, "cheapest": cheapest, "valuable": valuable,
            "sold": sold, "trending": names, "stats": stats(db),
            "vaulted_inventory": db.scalar(select(func.count()).select_from(MarketItem).where(MarketItem.custody_status == "vaulted",
                                                                                              MarketItem.is_sandbox.is_(False))) or 0}


def stats(db: Session) -> dict[str, Any]:
    """Volume = completed, real (non-sandbox) sales between two different members. Never transfers, cancellations or listings."""
    q = select(func.count(), func.coalesce(func.sum(MarketOrder.price_cents), 0)).where(
        MarketOrder.status == "completed", MarketOrder.is_sandbox.is_(False), MarketOrder.buyer_id != MarketOrder.seller_id)
    n, vol = db.execute(q).one()
    n30, vol30 = db.execute(q.where(MarketOrder.completed_at >= utcnow() - timedelta(days=30))).one()
    active = db.scalar(select(func.count()).select_from(MarketListing).where(MarketListing.status == "active", MarketListing.is_sandbox.is_(False))) or 0
    return {"sales": n, "volume": vol, "sales_30d": n30, "volume_30d": vol30, "active_listings": active, "currency": "USDT"}


def listing_detail(db: Session, listing_id: int, viewer: Member | None, admin: bool = False) -> dict[str, Any]:
    row = db.execute(_base().where(MarketListing.id == listing_id)).first()
    if row is None:
        raise MemberError("Not found.")
    l, item, card = row
    mine = viewer is not None and viewer.id == l.seller_id
    if not admin and not mine:
        if l.status not in PUBLIC_STATUSES or (l.is_sandbox and not sandbox_enabled(db, viewer)):
            raise MemberError("Not found.")
    seller = db.get(Member, l.seller_id)
    out = listing_out(db, l, item, card, viewer=viewer, detail=True, photos=_photos(db, [item.id]).get(item.id, []), seller=seller)
    out["comparables"] = comparables(db, card.id, item.grading_company, item.grade)
    if viewer is not None:
        out["watching"] = db.scalar(select(MarketWatch.id).where(MarketWatch.member_id == viewer.id, MarketWatch.listing_id == l.id)) is not None
        offers = db.scalars(select(MarketOffer).where(MarketOffer.listing_id == l.id, or_(MarketOffer.buyer_id == viewer.id, MarketOffer.status == "pending"))
                            .order_by(MarketOffer.id.desc())).all()
        out["offers"] = [offer_out(o) for o in offers if mine or o.buyer_id == viewer.id]
    return out


def comparables(db: Session, card_id: int, company: str, grade: str) -> dict[str, Any]:
    """Completed, verified sales of the same printing and exact grade (market data), plus this marketplace's own sales."""
    from app.models.pokemon import PokemonSale

    sales = db.scalars(select(PokemonSale).where(PokemonSale.card_id == card_id, PokemonSale.grading_company == company, PokemonSale.grade == grade,
                                                 PokemonSale.verified.is_(True), PokemonSale.excluded.is_(False))
                       .order_by(PokemonSale.sale_date.desc()).limit(8)).all()
    own = db.execute(select(MarketOrder.price_cents, MarketOrder.completed_at).join(MarketItem, MarketItem.id == MarketOrder.item_id).where(
        MarketItem.card_id == card_id, MarketItem.grading_company == company, MarketItem.grade == grade, MarketOrder.status == "completed",
        MarketOrder.is_sandbox.is_(False)).order_by(MarketOrder.completed_at.desc()).limit(8)).all()
    return {"verified_sales": [{"price": s.sale_price_usd_cents, "currency": "USD", "at": s.sale_date.isoformat(), "source": s.auction_house or s.source}
                               for s in sales],
            "marketplace_sales": [{"price": p, "at": at.isoformat() if at else None} for p, at in own]}


# --- selling ------------------------------------------------------------------------------------------------------------


def create_listing(db: Session, settings: Settings, member: Member, *, holding_id: int, company: str, grade: str, cert: str, price_cents: int | None,
                   evidence: str, note: str = "") -> MarketListing:
    """Step 1-6 of selling: a DRAFT from one of the member's holdings: graded (company + grade + cert) or RAW (condition
    from the AI scan of its photos). Only verified sellers (or sandbox testers) can sell. Nothing is public until verified and live."""
    if compliance.mode(settings) == "off":
        raise MemberError("The marketplace is closed right now.")
    if member.status != "active":
        raise MemberError("Your account can't sell right now.")
    if _today_count(db, MarketListing, MarketListing.seller_id, member.id) >= LISTINGS_PER_DAY:
        raise MemberError(f"Up to {LISTINGS_PER_DAY} new listings a day.")
    s = seller_of(db, member)
    if s.status == "suspended":
        raise MemberError("Your seller account is suspended. Contact support.")
    if s.status != "verified" and not s.sandbox:
        raise MemberError("Only verified sellers can list cards. Request seller verification first."
                          if s.status == "unverified" else "Your seller verification is being reviewed. You can list once it's approved.")
    if not compliance.country_allowed(settings, s.country):
        raise MemberError("The marketplace isn't available in your country.")
    p = db.scalar(select(PokemonPortfolio).where(PokemonPortfolio.member_id == member.id))
    h = db.get(PokemonHolding, holding_id)
    if h is None or p is None or h.portfolio_id != p.id or h.removed_at is not None:
        raise MemberError("Pick a card from your portfolio.")
    if price_cents is not None and not MIN_PRICE <= price_cents <= MAX_PRICE:
        raise MemberError("Asking price must be between 1 and 1,000,000 USDT.")
    if h.kind == "raw":
        report = h.condition_report or {}
        if h.condition_source != "ai" or h.condition_low is None or h.condition_high is None or not report.get("enough_photos", True):
            raise MemberError("Scan this card's condition first: add sharp front and back photos to it in your portfolio and tap ✨ Estimate condition.")
        g = SimpleNamespace(company="RAW", grade=raw.label_for(h.condition_low, h.condition_high))
        cert, key = "", f"RAW:H{h.id}"  # a raw card has no cert: one item per portfolio card
    else:
        try:
            g = normalise(company, grade)
        except GradeError as exc:
            raise MemberError(str(exc)) from exc
        cert = "".join(ch for ch in cert if ch.isalnum())[:32]
        if len(cert) < 6:
            raise MemberError("Enter the slab's certification number (as printed on the label).")
        key = f"{g.company}:{cert}"
    item = db.scalar(select(MarketItem).where(MarketItem.cert_key == key))
    if item is not None:
        if item.owner_member_id != member.id:
            audit(db, f"member:{member.id}", "duplicate_cert_attempt", f"item:{item.id}", cert_key=key)
            db.commit()
            raise MemberError("This certification number is already registered on the marketplace by another account. "
                              "If it's your card, contact support with proof of ownership.")
        if db.scalar(select(MarketListing.id).where(MarketListing.item_id == item.id, MarketListing.status.in_(OPEN_LISTING))):
            raise MemberError("This card already has an open listing.")
        if item.custody_status in ("release_requested", "released", "redeemed"):
            raise MemberError("This card has been redeemed or is being released from the vault.")
    else:
        item = MarketItem(card_id=h.card_id, grading_company=g.company, grade=g.grade, cert_number=cert, cert_key=key, owner_member_id=member.id,
                          origin_holding_id=h.id)
        db.add(item)
        db.flush()
        db.add(MarketTransfer(item_id=item.id, from_member_id=None, to_member_id=member.id, kind="intake", at=utcnow()))
    if g.company == "RAW":  # always the latest AI estimate of this card's photos
        item.grade, item.condition_low, item.condition_high = g.grade, h.condition_low, h.condition_high
        item.condition_report = raw.report_out(h.condition_report)
    l = MarketListing(item_id=item.id, seller_id=member.id, status="draft", price_cents=price_cents, ownership_evidence=evidence.strip()[:4000],
                      seller_note=note.strip()[:500])
    db.add(l)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise Conflict("This card already has an open listing.") from exc
    _event(db, l, "seller", "draft", None, "draft")
    audit(db, f"member:{member.id}", "listing_draft", f"listing:{l.id}", holding=h.id, cert_key=key)
    db.commit()
    return l


def _own_listing(db: Session, member: Member, listing_id: int) -> tuple[MarketListing, MarketItem]:
    l = db.get(MarketListing, listing_id)
    if l is None or l.seller_id != member.id:
        raise MemberError("Not found.")
    item = db.get(MarketItem, l.item_id)
    assert item is not None
    return l, item


def add_photo(db: Session, settings: Settings, member: Member | None, listing_id: int, side: str, data: bytes, source: str = "seller") -> MarketPhoto:
    from app.portfolio import photos

    l = db.get(MarketListing, listing_id)
    if l is None or (member is not None and l.seller_id != member.id):
        raise MemberError("Not found.")
    if member is not None and l.status not in ("draft", "awaiting_verification"):
        raise MemberError("Photos can be changed while the listing is a draft or awaiting verification.")
    item = db.get(MarketItem, l.item_id)
    assert item is not None
    if side not in ("front", "back", "label", "vault"):
        raise MemberError("Photo side must be front, back or label.")
    if (db.scalar(select(func.count()).select_from(MarketPhoto).where(MarketPhoto.item_id == item.id)) or 0) >= 12:
        raise MemberError("Up to 12 photos per card.")
    try:
        full, thumb, w, h, sha = photos.process(data)
    except photos.PhotoError as exc:
        raise MemberError(str(exc)) from exc
    store = photos.Storage(settings)
    if not store.available:
        raise MemberError("Photo storage isn't configured on this server.")
    token = secrets.token_urlsafe(18)
    key, tkey = f"market/{item.id}/{token}.jpg", f"market/{item.id}/{token}_t.jpg"
    store.put(key, full)
    store.put(tkey, thumb)
    reused = db.scalar(select(MarketPhoto.item_id).where(MarketPhoto.sha256 == sha, MarketPhoto.item_id != item.id).limit(1))
    if reused is not None:
        _flag(db, l, f"photo reused from another card (item {reused})")
    if member is not None:  # a seller's new photo replaces their previous one of that side
        for old in db.scalars(select(MarketPhoto).where(MarketPhoto.item_id == item.id, MarketPhoto.side == side, MarketPhoto.source == "seller")):
            store.delete(old.key)
            store.delete(old.thumb_key)
            db.delete(old)
    p = MarketPhoto(item_id=item.id, side=side, source=source, key=key, thumb_key=tkey, width=w, height=h, sha256=sha)
    db.add(p)
    db.commit()
    return p


def _flag(db: Session, l: MarketListing, reason: str) -> None:
    if reason not in (l.flags or []):
        l.flags = [*(l.flags or []), reason]
        db.add(MarketReport(listing_id=l.id, reporter_id=None, reason=reason, auto=True))


def update_listing(db: Session, member: Member, listing_id: int, *, price_cents: int | None = None, evidence: str | None = None,
                   note: str | None = None) -> MarketListing:
    l, _ = _own_listing(db, member, listing_id)
    if l.status not in SELLER_EDITABLE:
        raise MemberError("This listing can't be changed now.")
    if price_cents is not None:
        if not MIN_PRICE <= price_cents <= MAX_PRICE:
            raise MemberError("Asking price must be between 1 and 1,000,000 USDT.")
        if price_cents != l.price_cents:
            _cas(db, MarketListing, l, SELLER_EDITABLE, price_cents=price_cents)
            _event(db, l, "seller", "price", l.status, l.status, price_cents)
    if evidence is not None and l.status in ("draft", "awaiting_verification"):
        l.ownership_evidence = evidence.strip()[:4000]
    if note is not None:
        l.seller_note = note.strip()[:500]
    db.commit()
    return l


def submit(db: Session, settings: Settings, member: Member, listing_id: int) -> MarketListing:
    """Step 8: send the draft for verification (photos of front, back and label + ownership evidence + price required)."""
    l, item = _own_listing(db, member, listing_id)
    sides = set(db.scalars(select(MarketPhoto.side).where(MarketPhoto.item_id == item.id)))
    is_raw = item.grading_company == "RAW"
    missing = [s for s in (("front", "back") if is_raw else ("front", "back", "label")) if s not in sides]
    if missing:
        raise MemberError(f"Add photos of the {'card' if is_raw else 'slab'}'s {', '.join(missing)} first.")
    if not l.price_cents:
        raise MemberError("Set an asking price in USDT.")
    if len(l.ownership_evidence.strip()) < 20:
        raise MemberError("Describe your ownership evidence (where/when you bought it, receipts you can provide).")
    est = item_estimate(db, item).get("value")
    if est and l.price_cents < est * 0.5:
        _flag(db, l, "asking price under half the market estimate")
    if l.price_cents >= settings.market_high_value_cents:
        _flag(db, l, "high value: enhanced seller verification required")
    _cas(db, MarketListing, l, ("draft",), status="awaiting_verification", submitted_at=utcnow())
    item.verification_status = "pending"
    _event(db, l, "seller", "submitted", "draft", "awaiting_verification")
    audit(db, f"member:{member.id}", "listing_submitted", f"listing:{l.id}")
    db.commit()
    return l


def publish(db: Session, settings: Settings, member: Member | None, listing_id: int) -> MarketListing:
    """verified -> active: only verified inventory in confirmed custody, from a seller allowed to sell at this price."""
    l = db.get(MarketListing, listing_id)
    if l is None or (member is not None and l.seller_id != member.id):
        raise MemberError("Not found.")
    item = db.get(MarketItem, l.item_id)
    assert item is not None
    if item.verification_status != "verified" or item.custody_status != "vaulted":
        raise MemberError("Only verified cards in confirmed vault custody can go live.")
    seller = db.scalar(select(MarketSeller).where(MarketSeller.member_id == l.seller_id))
    if seller is None or seller.status == "suspended":
        raise MemberError("This seller can't sell right now.")
    if not l.is_sandbox and (l.price_cents or 0) >= settings.market_high_value_cents and seller.level < 2:
        raise MemberError("High-value listings need enhanced seller verification first.")
    _cas(db, MarketListing, l, ("verified",), status="active", listed_at=utcnow())
    _event(db, l, "seller" if member else "admin", "listed", "verified", "active")
    db.commit()
    return l


def cancel_listing(db: Session, member: Member | None, listing_id: int, actor: str = "seller", reason: str = "") -> MarketListing:
    l = db.get(MarketListing, listing_id)
    if l is None or (member is not None and l.seller_id != member.id):
        raise MemberError("Not found.")
    if l.status in ("reserved", "sold"):
        raise Conflict("A buyer has reserved or bought this card: it can't be cancelled now.")
    before = l.status
    _cas(db, MarketListing, l, SELLER_EDITABLE, status="cancelled", closed_at=utcnow(), review_note=reason or l.review_note)
    for o in db.scalars(select(MarketOffer).where(MarketOffer.listing_id == l.id, MarketOffer.status == "pending")):
        o.status, o.decided_at = "cancelled", utcnow()
    _event(db, l, actor, "cancelled", before, "cancelled")
    audit(db, actor if actor != "seller" else f"member:{l.seller_id}", "listing_cancelled", f"listing:{l.id}", reason=reason)
    db.commit()
    return l


def my_listings(db: Session, member: Member) -> list[dict[str, Any]]:
    rows = _rows(db, _base().where(MarketListing.seller_id == member.id).order_by(MarketListing.id.desc()).limit(200))
    ph = _photos(db, [i.id for _, i, _ in rows])
    out = []
    for l, i, c in rows:
        d = listing_out(db, l, i, c, viewer=member, photos=ph.get(i.id, []))
        d.update({"review_note": l.review_note, "flags": l.flags})
        out.append(d)
    return out


def eligible_holdings(db: Session, member: Member) -> list[dict[str, Any]]:
    """Graded and raw holdings that could be listed (a portfolio entry alone never creates a listing). A raw card is
    ready once the AI has estimated its condition from front + back photos."""
    from app.portfolio.service import card_brief

    p = db.scalar(select(PokemonPortfolio).where(PokemonPortfolio.member_id == member.id))
    if p is None:
        return []
    listed = set(db.scalars(select(MarketItem.origin_holding_id).join(MarketListing, MarketListing.item_id == MarketItem.id)
                            .where(MarketListing.status.in_(OPEN_LISTING), MarketItem.owner_member_id == member.id)))
    rows = db.execute(select(PokemonHolding, PokemonCard).join(PokemonCard, PokemonCard.id == PokemonHolding.card_id).where(
        PokemonHolding.portfolio_id == p.id, PokemonHolding.removed_at.is_(None), PokemonHolding.kind.in_(("graded", "raw")))).all()
    out = []
    for h, c in rows:
        d = {"holding_id": h.id, "card": card_brief(c), "kind": h.kind, "grading_company": h.grading_company, "grade": h.grade,
             "cert": h.cert_number, "value": h.value_cents, "listed": h.id in listed, "verification": h.verification}
        if h.kind == "raw":
            scanned = h.condition_source == "ai" and h.condition_low is not None and h.condition_high is not None
            d.update({"grading_company": "RAW", "grade": raw.label_for(h.condition_low, h.condition_high) if scanned else None,
                      "condition": {"scanned": scanned, "low": h.condition_low, "high": h.condition_high}})
        out.append(d)
    return out


# --- watchlist ----------------------------------------------------------------------------------------------------------


def watch(db: Session, member: Member, listing_id: int, on: bool) -> bool:
    listing_detail(db, listing_id, member)  # visibility check
    w = db.scalar(select(MarketWatch).where(MarketWatch.member_id == member.id, MarketWatch.listing_id == listing_id))
    if on and w is None:
        db.add(MarketWatch(member_id=member.id, listing_id=listing_id))
    elif not on and w is not None:
        db.delete(w)
    db.commit()
    return on


def watchlist(db: Session, member: Member) -> list[dict[str, Any]]:
    rows = _rows(db, _base().join(MarketWatch, MarketWatch.listing_id == MarketListing.id).where(MarketWatch.member_id == member.id)
                 .order_by(MarketWatch.id.desc()).limit(200))
    return _out_many(db, rows, member)


# --- trading (simulation until every live gate passes) -------------------------------------------------------------------


def _can_buy(db: Session, settings: Settings, member: Member, l: MarketListing) -> None:
    mode = compliance.mode(settings)
    if mode == "off":
        raise MemberError("The marketplace is closed right now.")
    if member.status != "active":
        raise MemberError("Your account can't buy right now.")
    s = seller_of(db, member)
    if s.status == "suspended":
        raise MemberError("Your marketplace account is suspended.")
    if not compliance.country_allowed(settings, s.country):
        raise MemberError("The marketplace isn't available in your country.")
    if mode == "simulation" and not (l.is_sandbox and s.sandbox):
        raise MemberError("Buying opens when live settlement launches. Today only sandbox listings can be traded, in simulation.")
    if l.seller_id == member.id:
        raise MemberError("That's your own listing.")


def _new_order(db: Session, settings: Settings, member: Member, l: MarketListing, price: int, network: str, idem: str,
               offer_id: int | None = None) -> MarketOrder:
    nets = payments.enabled(settings)
    if network not in nets:
        raise MemberError("Choose a supported network: " + ", ".join(str(v["label"]) for v in nets.values()))
    if network == payments.TESTNET:
        if not l.is_sandbox:
            raise MemberError("The testnet is only for sandbox listings: test tokens can't buy a real card.")
        from app.market import wallets

        if not wallets.mine(db, member):
            raise MemberError("Connect and verify a wallet first (Marketplace → 👛 Wallet): testnet payments must come from it.")
    f = fees(db, price, settings)
    ref = secrets.token_hex(5).upper()
    provider = provider_for(settings, network)
    o = MarketOrder(ref=ref, idempotency_key=idem, listing_id=l.id, item_id=l.item_id, buyer_id=member.id, seller_id=l.seller_id, offer_id=offer_id,
                    price_cents=price, buyer_fee_cents=f["buyer_fee"], seller_fee_cents=f["seller_fee"], total_cents=f["buyer_total"],
                    proceeds_cents=f["seller_proceeds"], network=network, pay_to=provider.deposit_address(network, ref),
                    amount_units=payments.order_units(f["buyer_total"], network, ref), status="awaiting_payment",
                    expires_at=utcnow() + timedelta(minutes=settings.market_order_minutes), is_sandbox=l.is_sandbox)
    db.add(o)
    db.flush()
    return o


def buy_now(db: Session, settings: Settings, member: Member, listing_id: int, network: str, client_key: str) -> MarketOrder:
    idem = f"{member.id}:{client_key[:60]}"
    existing = db.scalar(select(MarketOrder).where(MarketOrder.idempotency_key == idem))
    if existing is not None:  # the same click retried: the same order, never a second one
        return existing
    l = db.get(MarketListing, listing_id)
    if l is None:
        raise MemberError("Not found.")
    _can_buy(db, settings, member, l)
    if l.status != "active":
        raise Conflict("This card isn't available any more.")
    _cas(db, MarketListing, l, ("active",), status="reserved")
    try:
        o = _new_order(db, settings, member, l, l.price_cents or 0, network, idem)
    except IntegrityError as exc:
        db.rollback()
        raise Conflict("Someone else just reserved this card.") from exc
    _event(db, l, "buyer", "reserved", "active", "reserved")
    audit(db, f"member:{member.id}", "order_created", f"order:{o.id}", listing=l.id, price=o.price_cents, network=network, sandbox=o.is_sandbox)
    db.commit()
    return o


def make_offer(db: Session, settings: Settings, member: Member, listing_id: int, amount_cents: int) -> MarketOffer:
    l = db.get(MarketListing, listing_id)
    if l is None:
        raise MemberError("Not found.")
    _can_buy(db, settings, member, l)
    if l.status != "active":
        raise Conflict("This card isn't available for offers.")
    if not MIN_PRICE <= amount_cents < (l.price_cents or 0):
        raise MemberError("An offer must be below the asking price (use Buy Now to pay the asking price).")
    if _today_count(db, MarketOffer, MarketOffer.buyer_id, member.id) >= OFFERS_PER_DAY:
        raise MemberError(f"Up to {OFFERS_PER_DAY} offers a day.")
    for old in db.scalars(select(MarketOffer).where(MarketOffer.listing_id == l.id, MarketOffer.buyer_id == member.id, MarketOffer.status == "pending")):
        old.status, old.decided_at = "cancelled", utcnow()  # one live offer per buyer per card
    o = MarketOffer(listing_id=l.id, buyer_id=member.id, amount_cents=amount_cents, expires_at=utcnow() + timedelta(hours=settings.market_offer_hours))
    db.add(o)
    audit(db, f"member:{member.id}", "offer_made", f"listing:{l.id}", amount=amount_cents)
    db.commit()
    return o


def offer_out(o: MarketOffer) -> dict[str, Any]:
    return {"id": o.id, "listing_id": o.listing_id, "amount": o.amount_cents, "status": o.status, "expires_at": o.expires_at.isoformat(),
            "order_id": o.order_id}


def cancel_offer(db: Session, member: Member, offer_id: int) -> MarketOffer:
    o = db.get(MarketOffer, offer_id)
    if o is None or o.buyer_id != member.id:
        raise MemberError("Not found.")
    if o.status != "pending":
        raise Conflict("This offer is no longer pending.")
    o.status, o.decided_at = "cancelled", utcnow()
    db.commit()
    return o


def decide_offer(db: Session, settings: Settings, member: Member, offer_id: int, accept: bool, network: str = "tron") -> MarketOffer:
    o = db.get(MarketOffer, offer_id)
    l = db.get(MarketListing, o.listing_id) if o else None
    if o is None or l is None or l.seller_id != member.id:
        raise MemberError("Not found.")
    if o.status != "pending":
        raise Conflict("This offer is no longer pending.")
    if o.expires_at <= utcnow():
        o.status, o.decided_at = "expired", utcnow()
        db.commit()
        raise Conflict("This offer has expired.")
    if not accept:
        o.status, o.decided_at = "rejected", utcnow()
        db.commit()
        return o
    buyer = db.get(Member, o.buyer_id)
    assert buyer is not None
    _can_buy(db, settings, buyer, l)
    if l.status != "active":
        raise Conflict("This card isn't available any more.")
    _cas(db, MarketListing, l, ("active",), status="reserved")
    order = _new_order(db, settings, buyer, l, o.amount_cents, network, f"offer:{o.id}", offer_id=o.id)
    o.status, o.decided_at, o.order_id = "accepted", utcnow(), order.id
    _event(db, l, "seller", "reserved", "active", "reserved", o.amount_cents)
    audit(db, f"member:{member.id}", "offer_accepted", f"offer:{o.id}", order=order.id)
    db.commit()
    return o


def order_out(db: Session, o: MarketOrder, viewer: Member | None = None, admin: bool = False) -> dict[str, Any]:
    from app.portfolio.service import card_brief

    item = db.get(MarketItem, o.item_id)
    card = db.get(PokemonCard, item.card_id) if item else None
    net = payments.NETWORKS[o.network]
    pays = db.scalars(select(MarketPayment).where(MarketPayment.order_id == o.id).order_by(MarketPayment.id)).all()
    role = "admin" if admin else "buyer" if viewer and viewer.id == o.buyer_id else "seller"
    out = {"id": o.id, "ref": o.ref, "status": o.status, "note": o.status_note, "role": role, "sandbox": o.is_sandbox, "listing_id": o.listing_id,
           "card": card_brief(card) if card else None, "grading_company": item.grading_company if item else None, "grade": item.grade if item else None,
           "price": o.price_cents, "buyer_fee": o.buyer_fee_cents, "seller_fee": o.seller_fee_cents, "total": o.total_cents, "proceeds": o.proceeds_cents,
           "currency": "USDT", "network": o.network, "network_label": net["label"], "token": net["token"], "pay_to": o.pay_to,
           "amount_units": o.amount_units, "decimals": net["decimals"], "confirmations_needed": net["confirmations"],
           "testnet": bool(net.get("testnet")), "chain_id": net.get("chain_id"),
           "expires_at": o.expires_at.isoformat(), "paid_at": o.paid_at.isoformat() if o.paid_at else None,
           "completed_at": o.completed_at.isoformat() if o.completed_at else None, "created_at": o.created_at.isoformat() if o.created_at else None,
           "payments": [{"tx": p.tx_ref, "status": p.status, "problem": p.problem, "amount_units": p.amount_units, "confirmations": p.confirmations,
                         "simulated": p.simulated, "explorer": None if p.simulated else str(net["explorer"]).format(tx=p.tx_ref)} for p in pays]}
    if role == "seller":
        out.pop("pay_to")
    return out


_PROVIDERS: dict[str, Any] = {}


def provider_for(settings: Settings, network: str | None = None) -> Any:
    """Simulated payments, or (testnet orders) read-only checks against the Sepolia chain. A live provider is wired here
    only once a regulated partner is contracted (compliance gates)."""
    if compliance.mode(settings) == "live":
        raise MemberError("Live settlement has no payment partner integration yet.")
    if network == payments.TESTNET:
        receive = settings.market_testnet_receive
        if not receive:
            raise MemberError("Testnet payments aren't switched on.")
        from app.market import evm

        if not evm.is_address(receive):
            raise MemberError("The testnet receiving wallet isn't a valid address (MARKET_TESTNET_RECEIVE).")
        key = f"evm:{settings.market_testnet_rpc}:{receive.lower()}"
        if "testnet" in _PROVIDERS:  # tests inject a provider here
            return _PROVIDERS["testnet"]
        return _PROVIDERS.setdefault(key, payments.EvmRpcProvider(settings.market_testnet_rpc, receive))
    return _PROVIDERS.setdefault("sim", payments.SimulatedProvider())


def simulate_payment(db: Session, settings: Settings, member: Member, order_id: int, outcome: str = "exact") -> MarketOrder:
    """Simulation: 'broadcast' a synthetic transfer for this order (exact, underpaid, wrong_network, wrong_token, wrong_address,
    unconfirmed), then run the SAME independent verification a live payment would get."""
    o = db.get(MarketOrder, order_id)
    if o is None or o.buyer_id != member.id:
        raise MemberError("Not found.")
    if not o.is_sandbox or compliance.mode(settings) != "simulation":
        raise MemberError("Simulated payments are only for sandbox orders.")
    net = payments.NETWORKS[o.network]
    amount = int(o.amount_units) - (1 if outcome == "underpaid" else 0)
    t = payments.Transfer(network="ethereum" if outcome == "wrong_network" and o.network != "ethereum" else ("tron" if outcome == "wrong_network" else o.network),
                          tx_ref=payments.new_tx_ref(), token="FAKE" if outcome == "wrong_token" else str(net["token"]), from_address="SIM-BUYER-WALLET",
                          to_address="SIM-OTHER" if outcome == "wrong_address" else o.pay_to, amount_units=str(amount),
                          confirmations=0 if outcome == "unconfirmed" else int(net["confirmations"]), at=utcnow())  # type: ignore[call-overload]
    provider = provider_for(settings)
    provider.broadcast(t)
    return confirm_payment(db, settings, o.id, t.network, t.tx_ref)


def submit_tx(db: Session, settings: Settings, member: Member, order_id: int, tx_ref: str) -> MarketOrder:
    """TESTNET: the buyer's wallet sent the payment; the server checks that transaction on-chain itself."""
    o = db.get(MarketOrder, order_id)
    if o is None or o.buyer_id != member.id:
        raise MemberError("Not found.")
    if o.network != payments.TESTNET:
        raise MemberError("Only testnet orders are paid from a connected wallet today.")
    if not payments.re_tx(tx_ref):
        raise MemberError("That isn't a transaction hash (0x followed by 64 characters).")
    return confirm_payment(db, settings, o.id, o.network, tx_ref)


def recheck_pending(db: Session, settings: Settings, limit: int = 20) -> int:
    """TESTNET: look again at payments waiting for confirmations (scheduled; also when the buyer's page asks)."""
    if not settings.market_testnet_receive:
        return 0
    n = 0
    since = utcnow() - timedelta(hours=6)
    for p in db.scalars(select(MarketPayment).where(MarketPayment.network == payments.TESTNET, MarketPayment.status == "pending",
                                                    MarketPayment.created_at >= since).order_by(MarketPayment.id).limit(limit)).all():
        try:
            confirm_payment(db, settings, p.order_id, p.network, p.tx_ref)
            n += 1
        except MemberError:
            db.rollback()
    return n


def confirm_payment(db: Session, settings: Settings, order_id: int, network: str, tx_ref: str) -> MarketOrder:
    """Independent verification of a transaction for an order. The buyer's word is never enough: the provider is asked."""
    o = db.get(MarketOrder, order_id)
    if o is None:
        raise MemberError("Not found.")
    testnet = network == payments.TESTNET
    if testnet:
        tx_ref = tx_ref.lower()
    pay = db.scalar(select(MarketPayment).where(MarketPayment.network == network, MarketPayment.tx_ref == tx_ref))
    if pay is not None and not (pay.status == "pending" and pay.order_id == o.id):
        raise Conflict("This transaction has already been used.")  # replay protection (a pending check of THIS order may re-run)
    provider = provider_for(settings, network)
    try:
        t = provider.fetch(network, tx_ref)
    except Exception as exc:  # noqa: BLE001 - the chain node is down or slow: nothing is recorded, try again
        raise MemberError("Couldn't reach the test network to check that transaction. Try again in a minute.") from exc
    problem = payments.verify(t, network=o.network, pay_to=o.pay_to, amount_units=o.amount_units, created_at=o.created_at)
    if testnet and problem is None and t is not None:
        from app.models.market import MarketWallet

        mine = {a for a in db.scalars(select(MarketWallet.address).where(MarketWallet.member_id == o.buyer_id))}
        if t.from_address not in mine:
            problem = "sent from a wallet that isn't verified on the buyer's account"
        elif int(t.amount_units) != int(o.amount_units):
            problem = "not the exact amount for this order"  # the sub-cent tail identifies the order on the shared test wallet
    waiting = problem == "not enough confirmations yet" or (testnet and problem == "transaction not found by the provider")
    fields = dict(token=t.token if t else "", from_address=t.from_address if t else None, to_address=t.to_address if t else "",
                  amount_units=t.amount_units if t else "0", confirmations=t.confirmations if t else 0,
                  status="pending" if waiting else "rejected" if problem else "confirmed", problem=problem or "", verified_at=utcnow())
    if pay is None:
        pay = MarketPayment(order_id=o.id, network=network, tx_ref=tx_ref, simulated=provider.simulated, **fields)
        db.add(pay)
    else:
        for k, v in fields.items():
            setattr(pay, k, v)
    if problem:
        audit(db, "system", "payment_rejected" if pay.status == "rejected" else "payment_pending", f"order:{o.id}", problem=problem, tx=tx_ref)
        db.commit()
        return o
    if o.status != "awaiting_payment":  # a valid payment for an expired/cancelled order: money must go back
        _ledger(db, f"order:{o.id}:late:{tx_ref[:40]}", o, "refund_due", int(t.amount_units) // 10 ** 4, o.buyer_id)  # type: ignore[union-attr]
        o.status_note = "Payment arrived after the order closed: refund due."
        if o.status in ("expired", "cancelled"):
            o.status = "refund_pending"
        audit(db, "system", "late_payment", f"order:{o.id}", tx=tx_ref)
        db.commit()
        return o
    _cas(db, MarketOrder, o, ("awaiting_payment",), status="paid", paid_at=utcnow())
    _ledger(db, f"order:{o.id}:buyer_paid", o, "buyer_paid", o.total_cents, o.buyer_id)
    over = int(t.amount_units) - int(o.amount_units)  # type: ignore[union-attr]
    if over > 0:
        _ledger(db, f"order:{o.id}:overpaid", o, "refund_due", over // 10 ** 4, o.buyer_id)
    audit(db, "system", "payment_confirmed", f"order:{o.id}", tx=tx_ref, simulated=provider.simulated)
    db.commit()
    settle(db, settings, o.id)
    db.refresh(o)
    return o


def _ledger(db: Session, key: str, o: MarketOrder, kind: str, cents: int, member_id: int | None) -> None:
    if db.scalar(select(MarketLedgerEntry.id).where(MarketLedgerEntry.key == key)) is None:
        db.add(MarketLedgerEntry(key=key, order_id=o.id, kind=kind, amount_cents=cents, member_id=member_id, at=utcnow(), simulated=o.is_sandbox))


def settle(db: Session, settings: Settings, order_id: int) -> MarketOrder:
    """Idempotent. Transfers title and books the seller's proceeds only after all five checks pass."""
    o = db.get(MarketOrder, order_id)
    if o is None:
        raise MemberError("Not found.")
    if o.status == "completed":
        return o
    l = db.get(MarketListing, o.listing_id)
    item = db.get(MarketItem, o.item_id)
    buyer = db.get(Member, o.buyer_id)
    pay = db.scalar(select(MarketPayment).where(MarketPayment.order_id == o.id, MarketPayment.status == "confirmed"))
    problems = []
    if o.status != "paid" or pay is None:
        problems.append("payment not confirmed")
    if l is None or item is None or l.item_id != o.item_id or l.status != "reserved":
        problems.append("listing / item mismatch")
    if item is not None and (item.owner_member_id != o.seller_id or item.custody_status != "vaulted" or item.verification_status != "verified"):
        problems.append("seller can't transfer this item (ownership or custody changed)")
    bs = db.scalar(select(MarketSeller).where(MarketSeller.member_id == o.buyer_id))
    if buyer is None or buyer.status != "active" or (bs and bs.status == "suspended") or not compliance.country_allowed(settings, bs.country if bs else None):
        problems.append("buyer not eligible")
    mode = compliance.mode(settings)
    if mode == "off" or (mode == "simulation" and not o.is_sandbox):
        problems.append("settlement conditions not met for this mode")
    if problems:
        if o.status == "paid":
            _cas(db, MarketOrder, o, ("paid",), status="refund_pending", status_note="Settlement blocked: " + "; ".join(problems))
            _ledger(db, f"order:{o.id}:refund_due", o, "refund_due", o.total_cents, o.buyer_id)
            audit(db, "system", "settlement_failed", f"order:{o.id}", problems=problems)
            if l is not None and l.status == "reserved":
                _cas(db, MarketListing, l, ("reserved",), status="active")
                _event(db, l, "system", "released", "reserved", "active")
            db.commit()
        return o
    assert l is not None and item is not None and buyer is not None
    _cas(db, MarketOrder, o, ("paid",), status="settling")
    now = utcnow()
    _cas_item(db, item, ("vaulted",), owner_member_id=o.buyer_id)
    db.add(MarketTransfer(item_id=item.id, from_member_id=o.seller_id, to_member_id=o.buyer_id, order_id=o.id, kind="sale", at=now))
    _ledger(db, f"order:{o.id}:platform_fee", o, "platform_fee", o.seller_fee_cents + o.buyer_fee_cents, None)
    _ledger(db, f"order:{o.id}:seller_payable", o, "seller_payable", o.proceeds_cents, o.seller_id)
    _cas(db, MarketListing, l, ("reserved",), status="sold", sold_at=now, closed_at=now)
    _event(db, l, "system", "sold", "reserved", "sold", o.price_cents)
    for off in db.scalars(select(MarketOffer).where(MarketOffer.listing_id == l.id, MarketOffer.status == "pending")):
        off.status, off.decided_at = "expired", now
    _cas(db, MarketOrder, o, ("settling",), status="completed", completed_at=now)
    if o.is_sandbox:  # simulated payout: in live mode the payment partner releases escrow and reports back
        _ledger(db, f"order:{o.id}:seller_paid", o, "seller_paid", o.proceeds_cents, o.seller_id)
    audit(db, "system", "order_settled", f"order:{o.id}", item=item.id, buyer=o.buyer_id, seller=o.seller_id, sandbox=o.is_sandbox)
    db.commit()  # title + money are settled atomically; the portfolios (a view of ownership, not the record) follow
    try:
        _sync_portfolios(db, o, item, buyer)
        db.commit()
    except Exception as exc:  # noqa: BLE001 - the sale stands; the portfolio sync is retried from the audit trail
        db.rollback()
        audit(db, "system", "portfolio_sync_failed", f"order:{o.id}", error=str(exc)[:200])
        db.commit()
    return o


def _sync_portfolios(db: Session, o: MarketOrder, item: MarketItem, buyer: Member) -> None:
    """The seller's holding leaves their portfolio; the buyer gains the exact card + grade at the price actually paid."""
    from app.portfolio import service as pf

    seller = db.get(Member, o.seller_id)
    if item.origin_holding_id and seller is not None:
        h = db.get(PokemonHolding, item.origin_holding_id)
        p = db.scalar(select(PokemonPortfolio).where(PokemonPortfolio.member_id == seller.id))
        if h is not None and p is not None and h.portfolio_id == p.id and h.removed_at is None:
            if h.quantity > 1:
                pf.update_holding(db, seller, h.id, quantity=h.quantity - 1)
            else:
                pf.remove_holding(db, seller, h.id)
    pf.get_portfolio(db, buyer)
    nh = pf.add_holding(db, buyer, item.card_id, "graded", company=item.grading_company, grade=item.grade, cert_number=item.cert_number,
                        purchase_price_cents=o.price_cents, purchase_date=utcnow().date(), notes=f"Bought on the Price Chat marketplace (order {o.ref})",
                        is_public=False, source="market")
    nh.verification = "sandbox" if o.is_sandbox else "vaulted"
    nh.source = "market"
    item.origin_holding_id = nh.id


def cancel_order(db: Session, member: Member, order_id: int) -> MarketOrder:
    o = db.get(MarketOrder, order_id)
    if o is None or o.buyer_id != member.id:
        raise MemberError("Not found.")
    _cas(db, MarketOrder, o, ("awaiting_payment",), status="cancelled", status_note="Cancelled by the buyer before payment.")
    _release_listing(db, o)
    db.commit()
    return o


def _release_listing(db: Session, o: MarketOrder) -> None:
    l = db.get(MarketListing, o.listing_id)
    if l is not None and l.status == "reserved":
        _cas(db, MarketListing, l, ("reserved",), status="active")
        _event(db, l, "system", "released", "reserved", "active")


def expire(db: Session, now: datetime | None = None) -> dict[str, int]:
    """Scheduled: unpaid reservations past their time go back on sale; expired offers close."""
    now = now or utcnow()
    n = 0
    paying = select(MarketPayment.order_id).where(MarketPayment.status == "pending")  # a payment on its way, waiting for confirmations
    for o in db.scalars(select(MarketOrder).where(MarketOrder.status == "awaiting_payment", MarketOrder.expires_at <= now,
                                                  MarketOrder.id.not_in(paying))).all():
        try:
            _cas(db, MarketOrder, o, ("awaiting_payment",), status="expired", status_note="Not paid in time.")
            _release_listing(db, o)
            db.commit()
            n += 1
        except Conflict:
            continue
    offers = db.execute(update(MarketOffer).where(MarketOffer.status == "pending", MarketOffer.expires_at <= now)
                        .values(status="expired", decided_at=now).execution_options(synchronize_session=False)).rowcount  # type: ignore[attr-defined]
    db.commit()
    return {"orders_expired": n, "offers_expired": offers or 0}


def my_orders(db: Session, member: Member) -> dict[str, Any]:
    orders = db.scalars(select(MarketOrder).where(or_(MarketOrder.buyer_id == member.id, MarketOrder.seller_id == member.id))
                        .order_by(MarketOrder.id.desc()).limit(200)).all()
    made = db.scalars(select(MarketOffer).where(MarketOffer.buyer_id == member.id).order_by(MarketOffer.id.desc()).limit(100)).all()
    received = db.execute(select(MarketOffer, MarketListing).join(MarketListing, MarketListing.id == MarketOffer.listing_id)
                          .where(MarketListing.seller_id == member.id).order_by(MarketOffer.id.desc()).limit(100)).all()
    return {"orders": [order_out(db, o, member) for o in orders], "offers_made": [offer_out(o) for o in made],
            "offers_received": [{**offer_out(o), "listing_price": l.price_cents} for o, l in received]}


# --- vault & redemption ----------------------------------------------------------------------------------------------------


def my_vault(db: Session, member: Member) -> list[dict[str, Any]]:
    from app.portfolio.service import card_brief

    rows = db.execute(select(MarketItem, PokemonCard).join(PokemonCard, PokemonCard.id == MarketItem.card_id)
                      .where(MarketItem.owner_member_id == member.id).order_by(MarketItem.id.desc())).all()
    reds = {r.item_id: r for r in db.scalars(select(MarketRedemption).where(MarketRedemption.member_id == member.id).order_by(MarketRedemption.id))}
    return [{"item_id": i.id, "card": card_brief(c), "grading_company": i.grading_company, "grade": i.grade, "cert": i.cert_number,
             "custody": i.custody_status, "custody_label": CUSTODY_LABEL.get(i.custody_status, i.custody_status), "sandbox": i.is_sandbox,
             "vault_partner": i.vault_partner, "insurance": i.insurance_status, "insurance_note": i.insurance_note,
             "redemption": redemption_out(reds[i.id]) if i.id in reds else None,
             "can_redeem": i.custody_status == "vaulted" and i.verification_status == "verified"} for i, c in rows]


def redemption_fees(country: str, value_cents: int | None) -> dict[str, int]:
    c = country.upper()
    ship = SHIPPING.get("EU" if c in EU else c, SHIPPING_OTHER)
    insurance = max(500, (value_cents or 0) // 100)  # ~1 % of the card's estimate, min 5 USDT (until the partner quotes)
    return {"shipping": ship, "handling": HANDLING, "insurance": insurance, "total": ship + HANDLING + insurance}


def request_redemption(db: Session, settings: Settings, member: Member, item_id: int, address: dict[str, str]) -> MarketRedemption:
    from app.security.secrets import protect

    item = db.get(MarketItem, item_id)
    if item is None or item.owner_member_id != member.id:
        raise MemberError("Not found.")
    if item.custody_status != "vaulted" or item.verification_status != "verified":
        raise MemberError("Only verified cards held in the vault can be redeemed.")
    if db.scalar(select(MarketListing.id).where(MarketListing.item_id == item.id, MarketListing.status.in_(OPEN_LISTING))):
        raise MemberError("Cancel this card's listing first (a reserved card can't be redeemed).")
    country = (address.get("country") or "").strip().upper()[:2]
    if len(country) != 2 or not compliance.country_allowed(settings, country):
        raise MemberError("We can't ship to that country.")
    for k in ("name", "line1", "city", "postcode"):
        if not (address.get(k) or "").strip():
            raise MemberError("Enter the full shipping address.")
    clean = {k: (address.get(k) or "").strip()[:120] for k in ("name", "line1", "line2", "city", "region", "postcode", "country", "phone")}
    est = item_estimate(db, item).get("value")
    f = redemption_fees(country, est)
    _cas_item(db, item, ("vaulted",), custody_status="release_requested")  # locked: can't be listed, sold or transferred
    import json

    r = MarketRedemption(item_id=item.id, member_id=member.id, address_enc=protect(json.dumps(clean).encode()).decode("latin-1"), country=country,
                         fees=f, total_cents=f["total"])
    db.add(r)
    audit(db, f"member:{member.id}", "redemption_requested", f"item:{item.id}", total=f["total"])
    db.commit()
    return r


def redemption_out(r: MarketRedemption, with_address: bool = False) -> dict[str, Any]:
    out = {"id": r.id, "item_id": r.item_id, "status": r.status, "country": r.country, "fees": r.fees, "total": r.total_cents, "carrier": r.carrier,
           "tracking": r.tracking, "note": r.note, "created_at": r.created_at.isoformat() if r.created_at else None}
    if with_address:
        import json

        from app.security.secrets import unprotect

        try:
            out["address"] = json.loads(unprotect(r.address_enc.encode("latin-1")))
        except Exception:  # noqa: BLE001
            out["address"] = None
    return out


def cancel_redemption(db: Session, member: Member, redemption_id: int) -> MarketRedemption:
    r = db.get(MarketRedemption, redemption_id)
    if r is None or r.member_id != member.id:
        raise MemberError("Not found.")
    if r.status != "requested":
        raise Conflict("The vault has already started releasing this card.")
    item = db.get(MarketItem, r.item_id)
    assert item is not None
    r.status = "cancelled"
    _cas_item(db, item, ("release_requested",), custody_status="vaulted")
    db.commit()
    return r


REDEMPTION_FLOW = {"approved": ("requested",), "released": ("approved",), "shipped": ("released",), "delivered": ("shipped",),
                   "returned": ("shipped",), "lost": ("shipped",), "cancelled": ("requested", "approved")}


def advance_redemption(db: Session, redemption_id: int, to: str, *, carrier: str | None = None, tracking: str | None = None, note: str = "") -> MarketRedemption:
    """Admin / vault partner. The digital claim is retired (item 'redeemed') only once the card is delivered."""
    r = db.get(MarketRedemption, redemption_id)
    if r is None or to not in REDEMPTION_FLOW:
        raise MemberError("Not found.")
    if r.status not in REDEMPTION_FLOW[to]:
        raise Conflict(f"A {r.status} redemption can't become {to}.")
    item = db.get(MarketItem, r.item_id)
    assert item is not None
    now = utcnow()
    if to == "released":
        _cas_item(db, item, ("release_requested",), custody_status="released")
    elif to == "shipped":
        if not tracking:
            raise MemberError("Add the carrier tracking number.")
        r.carrier, r.tracking = (carrier or "")[:32], tracking[:64]
    elif to == "delivered":
        _cas_item(db, item, ("released",), custody_status="redeemed", redeemed_at=now)
        db.add(MarketTransfer(item_id=item.id, from_member_id=r.member_id, to_member_id=None, kind="redeemed", at=now))
        h = db.get(PokemonHolding, item.origin_holding_id) if item.origin_holding_id else None
        if h is not None:
            h.verification = "redeemed"
    elif to == "returned":
        _cas_item(db, item, ("released",), custody_status="received")  # back at the vault: re-inspected before it's vaulted again
    elif to == "lost":
        db.add(MarketDispute(redemption_id=r.id, opened_by=r.member_id, reason="Shipment reported lost: insurance claim with the carrier/vault partner."))
    elif to == "cancelled":
        _cas_item(db, item, ("release_requested",), custody_status="vaulted")
    r.status = to
    r.note = (note or r.note)[:1000]
    audit(db, "admin", f"redemption_{to}", f"redemption:{r.id}", item=item.id)
    db.commit()
    return r


# --- trust -------------------------------------------------------------------------------------------------------------------


def report(db: Session, member: Member, listing_id: int, reason: str) -> None:
    listing_detail(db, listing_id, member)
    if _today_count(db, MarketReport, MarketReport.reporter_id, member.id) >= REPORTS_PER_DAY:
        raise MemberError("Thanks: you've sent several reports today already.")
    db.add(MarketReport(listing_id=listing_id, reporter_id=member.id, reason=reason.strip()[:1000] or "suspicious"))
    db.commit()


def open_dispute(db: Session, member: Member, order_id: int, reason: str) -> MarketDispute:
    o = db.get(MarketOrder, order_id)
    if o is None or member.id not in (o.buyer_id, o.seller_id):
        raise MemberError("Not found.")
    if len(reason.strip()) < 20:
        raise MemberError("Describe the problem (at least 20 characters).")
    d = MarketDispute(order_id=o.id, opened_by=member.id, reason=reason.strip()[:4000])
    db.add(d)
    audit(db, f"member:{member.id}", "dispute_opened", f"order:{o.id}")
    db.commit()
    return d
