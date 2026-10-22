"""Marketplace administration (owner only: every /api/market/admin route sits behind the admin login). Every action is
audited; ownership- or money-changing actions also need a typed reason and an explicit confirmation of the target."""

from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config.settings import Settings
from app.db.base import utcnow
from app.market import compliance
from app.market.service import (
    Conflict, _cas, _cas_item, _event, _ledger, _photos, audit, listing_out, order_out, publish, redemption_out, seller_of, stats,
)
from app.members.service import MemberError
from app.models.market import (
    MarketAudit, MarketDispute, MarketItem, MarketLedgerEntry, MarketListing, MarketOffer, MarketOrder, MarketPayment, MarketRedemption,
    MarketReport, MarketSeller, MarketTransfer,
)
from app.models.members import Member
from app.models.pokemon import PokemonCard
from app.pokemon.grades import GradeError, normalise


def require_confirm(reason: str | None, confirm: str | None, expected: str) -> None:
    """Stronger authorisation for financial / ownership changes: a written reason and the target typed back."""
    if not reason or len(reason.strip()) < 8:
        raise MemberError("Give a reason (at least 8 characters): it goes into the audit log.")
    if (confirm or "").strip().upper() != expected.upper():
        raise MemberError(f"Type {expected} to confirm.")


def overview(db: Session, settings: Settings) -> dict[str, Any]:
    def count(model: Any, *where: Any) -> int:
        return db.scalar(select(func.count()).select_from(model).where(*where)) or 0

    by_status = dict(db.execute(select(MarketListing.status, func.count()).group_by(MarketListing.status)).all())
    orders = dict(db.execute(select(MarketOrder.status, func.count()).group_by(MarketOrder.status)).all())
    fees_real = db.scalar(select(func.coalesce(func.sum(MarketLedgerEntry.amount_cents), 0)).where(
        MarketLedgerEntry.kind == "platform_fee", MarketLedgerEntry.simulated.is_(False))) or 0
    fees_sim = db.scalar(select(func.coalesce(func.sum(MarketLedgerEntry.amount_cents), 0)).where(
        MarketLedgerEntry.kind == "platform_fee", MarketLedgerEntry.simulated.is_(True))) or 0
    return {
        "mode": compliance.mode(settings), "requested_mode": settings.market_mode, "gates": compliance.gates(settings),
        "listings": by_status, "orders": orders, "stats": stats(db),
        "revenue": {"platform_fees": fees_real, "simulated_platform_fees": fees_sim, "currency": "USDT"},
        "inventory": {"vaulted": count(MarketItem, MarketItem.custody_status == "vaulted", MarketItem.is_sandbox.is_(False)),
                      "awaiting_custody": count(MarketItem, MarketItem.custody_status.in_(("self_custody", "submitted", "received")),
                                                MarketItem.verification_status == "verified"),
                      "sandbox": count(MarketItem, MarketItem.is_sandbox.is_(True))},
        "open_reports": count(MarketReport, MarketReport.status == "open"), "open_disputes": count(MarketDispute, MarketDispute.status == "open"),
        "refunds_pending": orders.get("refund_pending", 0),
        "redemptions_open": count(MarketRedemption, MarketRedemption.status.in_(("requested", "approved", "released", "shipped"))),
        "pending_sellers": count(MarketSeller, MarketSeller.status == "pending"),
        "fees": {"seller_bps": settings.market_seller_fee_bps, "buyer_bps": settings.market_buyer_fee_bps},
        "vault_partners": compliance.vault_partners(settings),
    }


def queue(db: Session, status: str) -> list[dict[str, Any]]:
    from app.market.service import _base, _rows

    rows = _rows(db, _base().where(MarketListing.status == status).order_by(MarketListing.id.desc()).limit(200))
    ph = _photos(db, [i.id for _, i, _ in rows])
    out = []
    for l, i, c in rows:
        d = listing_out(db, l, i, c, photos=ph.get(i.id, []), seller=db.get(Member, l.seller_id))
        d.update({"cert": i.cert_number, "ownership_evidence": l.ownership_evidence, "flags": l.flags, "review_note": l.review_note,
                  "item": {"id": i.id, "custody": i.custody_status, "vault_partner": i.vault_partner, "vault_inventory_id": i.vault_inventory_id,
                           "storage_location": i.storage_location, "verification": i.verification_status}})
        out.append(d)
    return out


def verify_listing(db: Session, listing_id: int, approve: bool, note: str) -> MarketListing:
    """Identity + certificate review (photos, label vs catalogue, evidence). A cert number alone never proves authenticity:
    approval moves the card to 'awaiting custody' — it goes live only after a vault partner confirms the physical slab."""
    l = db.get(MarketListing, listing_id)
    if l is None:
        raise MemberError("Not found.")
    item = db.get(MarketItem, l.item_id)
    assert item is not None
    if approve:
        _cas(db, MarketListing, l, ("awaiting_verification",), status="awaiting_custody", review_note=note[:2000])
        item.verification_status, item.verified_at = "verified", utcnow()
        _event(db, l, "admin", "verified", "awaiting_verification", "awaiting_custody")
        if item.custody_status == "vaulted":  # already in the vault (e.g. re-listed after a purchase)
            _cas(db, MarketListing, l, ("awaiting_custody",), status="verified")
            _event(db, l, "vault", "custody_confirmed", "awaiting_custody", "verified")
    else:
        if len(note.strip()) < 8:
            raise MemberError("Tell the seller why (at least 8 characters).")
        _cas(db, MarketListing, l, ("awaiting_verification", "awaiting_custody", "verified"), status="rejected", review_note=note[:2000], closed_at=utcnow())
        if item.verification_status != "verified" or item.custody_status != "vaulted":
            item.verification_status = "rejected"
        _event(db, l, "admin", "rejected", None, "rejected")
    audit(db, "admin", "listing_verified" if approve else "listing_rejected", f"listing:{l.id}", note=note[:500])
    db.commit()
    return l


CUSTODY_STEPS = {"submitted": ("self_custody",), "received": ("submitted", "self_custody"), "vaulted": ("received",)}


def custody(db: Session, settings: Settings, item_id: int, to: str, *, partner: str | None = None, inventory_id: str | None = None,
            location: str | None = None, insurance_confirmed: bool = False, insurance_note: str = "", reason: str | None = None,
            confirm: str | None = None) -> MarketItem:
    """Vault intake, as reported by an approved vault partner. 'vaulted' (custody confirmed) needs the partner's unique
    inventory id + location, an approved partner, a reason and a typed confirmation — it changes what can be sold."""
    item = db.get(MarketItem, item_id)
    if item is None or to not in CUSTODY_STEPS:
        raise MemberError("Not found.")
    approved = compliance.vault_partners(settings) + ([compliance.SANDBOX_VAULT] if item.is_sandbox else [])
    if to in ("received", "vaulted"):
        if not partner or partner not in approved:
            raise MemberError("Custody can only be recorded for an approved vault partner. None is contracted yet: "
                              "Price Chat itself has no vault." if not compliance.vault_partners(settings) else "Choose an approved vault partner.")
    if to == "vaulted":
        require_confirm(reason, confirm, f"ITEM-{item.id}")
        if not inventory_id or not location:
            raise MemberError("Record the vault's unique inventory id and storage location.")
        if db.scalar(select(MarketItem.id).where(MarketItem.vault_inventory_id == inventory_id, MarketItem.id != item.id)):
            raise Conflict("That vault inventory id belongs to another item.")
    values: dict[str, Any] = {"custody_status": to}
    if partner:
        values["vault_partner"] = partner
    if to == "vaulted":
        values.update(vault_inventory_id=inventory_id[:64], storage_location=location[:120], custody_confirmed_at=utcnow(),  # type: ignore[index]
                      insurance_status="confirmed" if insurance_confirmed else "none", insurance_note=insurance_note[:2000] if insurance_confirmed else "")
    _cas_item(db, item, CUSTODY_STEPS[to], **values)
    if to == "vaulted":
        for l in db.scalars(select(MarketListing).where(MarketListing.item_id == item.id, MarketListing.status == "awaiting_custody")):
            _cas(db, MarketListing, l, ("awaiting_custody",), status="verified")
            _event(db, l, "vault", "custody_confirmed", "awaiting_custody", "verified")
    audit(db, "admin", f"custody_{to}", f"item:{item.id}", partner=partner, inventory_id=inventory_id, reason=reason)
    db.commit()
    return item


def set_seller(db: Session, username: str, *, status: str | None = None, level: int | None = None, sandbox: bool | None = None,
               country: str | None = None, note: str = "") -> MarketSeller:
    m = db.scalar(select(Member).where(Member.username_key == username.strip().lower()))
    if m is None:
        raise MemberError("No member with that username.")
    s = seller_of(db, m)
    if status is not None:
        if status not in ("unverified", "pending", "verified", "suspended"):
            raise MemberError("Unknown status.")
        s.status = status
    if level is not None:
        s.level = max(0, min(2, level))
    if sandbox is not None:
        s.sandbox = sandbox
    if country is not None:
        s.country = country.strip().upper()[:2] or None
    if note:
        s.notes = (s.notes + f"\n{utcnow().date()}: {note}")[-4000:]
    audit(db, "admin", "seller_updated", f"member:{m.id}", status=status, level=level, sandbox=sandbox, country=country, note=note[:200])
    db.commit()
    return s


def sellers(db: Session) -> list[dict[str, Any]]:
    rows = db.execute(select(MarketSeller, Member).join(Member, Member.id == MarketSeller.member_id).order_by(MarketSeller.id.desc()).limit(300)).all()
    return [{"username": m.username, "status": s.status, "level": s.level, "sandbox": s.sandbox, "country": s.country, "notes": s.notes} for s, m in rows]


def sandbox_listing(db: Session, settings: Settings, *, username: str, card_id: int, company: str, grade: str, price_cents: int) -> MarketListing:
    """Simulation inventory: clearly marked SANDBOX, catalogue image only, a synthetic cert, held by the sandbox vault.
    Never shown to the public and never counted in volume."""
    import secrets

    m = db.scalar(select(Member).where(Member.username_key == username.strip().lower()))
    card = db.get(PokemonCard, card_id)
    if m is None or card is None:
        raise MemberError("Unknown member or card.")
    try:
        g = normalise(company, grade)
    except GradeError as exc:
        raise MemberError(str(exc)) from exc
    s = seller_of(db, m)
    s.sandbox = True
    cert = "SBX" + secrets.token_hex(4).upper()
    now = utcnow()
    item = MarketItem(card_id=card.id, grading_company=g.company, grade=g.grade, cert_number=cert, cert_key=f"SBX:{cert}", owner_member_id=m.id,
                      verification_status="verified", verified_at=now, custody_status="vaulted", vault_partner=compliance.SANDBOX_VAULT,
                      vault_inventory_id=f"SBX-{cert}", storage_location="sandbox", custody_confirmed_at=now, is_sandbox=True)
    db.add(item)
    db.flush()
    db.add(MarketTransfer(item_id=item.id, to_member_id=m.id, kind="intake", at=now))
    l = MarketListing(item_id=item.id, seller_id=m.id, status="verified", price_cents=price_cents, is_sandbox=True,
                      ownership_evidence="SANDBOX: simulation inventory", seller_note="Sandbox listing for testing the trading simulation.")
    db.add(l)
    db.flush()
    _event(db, l, "admin", "sandbox_created", None, "verified")
    audit(db, "admin", "sandbox_listing", f"listing:{l.id}", member=m.id, card=card.id)
    db.commit()
    return publish(db, settings, None, l.id)


def orders(db: Session, status: str | None = None) -> list[dict[str, Any]]:
    q = select(MarketOrder).order_by(MarketOrder.id.desc()).limit(300)
    if status:
        q = q.where(MarketOrder.status == status)
    return [order_out(db, o, admin=True) for o in db.scalars(q)]


def refund(db: Session, order_id: int, reason: str | None, confirm: str | None) -> MarketOrder:
    """Records that the payment partner returned the buyer's funds (simulated for sandbox orders)."""
    o = db.get(MarketOrder, order_id)
    if o is None:
        raise MemberError("Not found.")
    require_confirm(reason, confirm, f"ORDER-{o.ref}")
    if o.status != "refund_pending":
        raise Conflict("Only orders awaiting a refund can be refunded.")
    due = db.scalar(select(func.coalesce(func.sum(MarketLedgerEntry.amount_cents), 0)).where(
        MarketLedgerEntry.order_id == o.id, MarketLedgerEntry.kind == "refund_due")) or 0
    _ledger(db, f"order:{o.id}:refunded", o, "refunded", due, o.buyer_id)
    _cas(db, MarketOrder, o, ("refund_pending",), status="refunded", status_note=f"Refunded: {reason}"[:500])
    audit(db, "admin", "order_refunded", f"order:{o.id}", amount=due, reason=reason)
    db.commit()
    return o


def retry_settlement(db: Session, settings: Settings, order_id: int, reason: str | None, confirm: str | None) -> MarketOrder:
    from app.market.service import settle

    o = db.get(MarketOrder, order_id)
    if o is None:
        raise MemberError("Not found.")
    require_confirm(reason, confirm, f"ORDER-{o.ref}")
    audit(db, "admin", "settlement_retry", f"order:{o.id}", reason=reason)
    db.commit()
    return settle(db, settings, o.id)


def payments_list(db: Session) -> list[dict[str, Any]]:
    return [{"id": p.id, "order_id": p.order_id, "network": p.network, "tx": p.tx_ref, "status": p.status, "problem": p.problem,
             "amount_units": p.amount_units, "simulated": p.simulated, "verified_at": p.verified_at.isoformat() if p.verified_at else None}
            for p in db.scalars(select(MarketPayment).order_by(MarketPayment.id.desc()).limit(300))]


def redemptions(db: Session) -> list[dict[str, Any]]:
    return [redemption_out(r, with_address=True) for r in db.scalars(select(MarketRedemption).order_by(MarketRedemption.id.desc()).limit(300))]


def disputes(db: Session) -> list[dict[str, Any]]:
    return [{"id": d.id, "order_id": d.order_id, "redemption_id": d.redemption_id, "opened_by": d.opened_by, "reason": d.reason, "status": d.status,
             "resolution": d.resolution, "at": d.created_at.isoformat() if d.created_at else None}
            for d in db.scalars(select(MarketDispute).order_by(MarketDispute.id.desc()).limit(300))]


def resolve_dispute(db: Session, dispute_id: int, status: str, resolution: str) -> MarketDispute:
    d = db.get(MarketDispute, dispute_id)
    if d is None or status not in ("resolved", "rejected"):
        raise MemberError("Not found.")
    if len(resolution.strip()) < 8:
        raise MemberError("Describe the resolution.")
    d.status, d.resolution = status, resolution.strip()[:4000]
    audit(db, "admin", f"dispute_{status}", f"dispute:{d.id}", resolution=resolution[:500])
    db.commit()
    return d


def reports(db: Session) -> list[dict[str, Any]]:
    return [{"id": r.id, "listing_id": r.listing_id, "reason": r.reason, "auto": r.auto, "status": r.status,
             "at": r.created_at.isoformat() if r.created_at else None}
            for r in db.scalars(select(MarketReport).order_by(MarketReport.id.desc()).limit(300))]


def decide_report(db: Session, report_id: int, status: str) -> MarketReport:
    r = db.get(MarketReport, report_id)
    if r is None or status not in ("dismissed", "actioned"):
        raise MemberError("Not found.")
    r.status = status
    audit(db, "admin", f"report_{status}", f"report:{r.id}", listing=r.listing_id)
    db.commit()
    return r


def audit_log(db: Session, limit: int = 300) -> list[dict[str, Any]]:
    return [{"at": a.at.isoformat(), "actor": a.actor, "action": a.action, "target": a.target, "data": a.data}
            for a in db.scalars(select(MarketAudit).order_by(MarketAudit.id.desc()).limit(limit))]


def reconcile(db: Session) -> dict[str, Any]:
    """Consistency checks (run on demand; any finding is a problem to investigate)."""
    problems: list[str] = []
    for o in db.scalars(select(MarketOrder).where(MarketOrder.status == "completed")):
        kinds = set(db.scalars(select(MarketLedgerEntry.kind).where(MarketLedgerEntry.order_id == o.id)))
        if not {"buyer_paid", "platform_fee", "seller_payable"} <= kinds:
            problems.append(f"order {o.ref}: completed without full ledger entries")
        item = db.get(MarketItem, o.item_id)
        last = db.scalar(select(MarketTransfer).where(MarketTransfer.item_id == o.item_id).order_by(MarketTransfer.id.desc()))
        if item and last and last.kind == "sale" and last.to_member_id != item.owner_member_id:
            problems.append(f"item {item.id}: owner differs from the last transfer")
    stuck = db.scalars(select(MarketOrder).where(MarketOrder.status.in_(("paid", "settling")), MarketOrder.updated_at < utcnow() - timedelta(minutes=10))).all()
    problems += [f"order {o.ref}: stuck in {o.status}" for o in stuck]
    dup = db.execute(select(MarketItem.cert_key, func.count()).group_by(MarketItem.cert_key).having(func.count() > 1)).all()
    problems += [f"duplicate cert {k}" for k, _ in dup]
    reserved_no_order = db.scalars(select(MarketListing).where(MarketListing.status == "reserved")).all()
    for l in reserved_no_order:
        if not db.scalar(select(MarketOrder.id).where(MarketOrder.listing_id == l.id, MarketOrder.status.in_(("awaiting_payment", "paid", "settling")))):
            problems.append(f"listing {l.id}: reserved without a live order")
    return {"ok": not problems, "problems": problems, "checked_at": utcnow().isoformat()}
