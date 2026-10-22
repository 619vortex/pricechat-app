"""Physical cards in Price Chat's custody, and the rules their future NFTs must follow (docs/VAULT.md §4).

ARCHITECTURE ONLY: nothing here mints, sells or transfers anything on a chain. It records each physical card's state
and refuses every transition that would let a token exist without a verified card behind it:

- an NFT can be requested only when acquisition is `verified` AND custody is `in_custody`;
- `minted` can only follow `mint_pending` and must carry the chain, contract, token id and tx of a real mint;
- a redemption can't ship while a token exists: it must be `burned` first (one card, one claim);
- every change is written to `vault_events` (who, what, from, to, why).
"""

import secrets
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.members.service import MemberError
from app.models.vault import VaultCard, VaultCardPhoto, VaultConditionRecord, VaultEvent, VaultListing, VaultRedemption

ACQUISITION: dict[str, set[str]] = {
    "purchase_pending": {"purchased", "rejected"},
    "purchased": {"in_transit", "received", "rejected"},
    "in_transit": {"received", "rejected"},
    "received": {"verified", "rejected"},
    "verified": set(),
    "rejected": set(),
}
CUSTODY: dict[str, set[str]] = {
    "none": {"in_custody"},
    "in_custody": {"redemption_pending"},
    "redemption_pending": {"in_custody", "shipped"},
    "shipped": {"delivered"},
    "delivered": set(),
}
NFT: dict[str, set[str]] = {
    "not_eligible": {"eligible"},
    "eligible": {"mint_pending", "not_eligible"},
    "mint_pending": {"minted", "eligible"},
    "minted": {"burned"},
    "burned": set(),
}
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def new_uid() -> str:
    raw = "".join(secrets.choice(_ALPHABET) for _ in range(8))
    return f"PCV-{raw[:4]}-{raw[4:]}"


def _event(db: Session, card: VaultCard, field: str, old: str | None, new: str | None, actor: str, note: str = "") -> None:
    db.add(VaultEvent(card_id=card.id, at=utcnow(), field=field, old=old, new=new, actor=actor[:60], note=note[:2000]))


def card_out(db: Session, c: VaultCard, private: bool = True) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": c.id, "uid": c.uid, "title": c.title, "pokemon": c.pokemon, "set_name": c.set_name, "year": c.year, "language": c.language,
        "acquisition_status": c.acquisition_status, "custody_status": c.custody_status, "nft_status": c.nft_status,
        "nft": {"chain": c.nft_chain, "contract": c.nft_contract, "token_id": c.nft_token_id, "tx": c.mint_tx} if c.nft_status in ("minted", "burned") else None,
        "verified_at": c.verified_at.isoformat() if c.verified_at else None, "listing_id": c.listing_id,
        "photos": [{"side": p.side, "url": p.url} for p in db.scalars(select(VaultCardPhoto).where(VaultCardPhoto.card_id == c.id))],
        "conditions": [{"grade": r.grade, "inspector": r.inspector if private else None, "notes": r.notes, "matches_listing": r.matches_listing,
                        "at": r.created_at.isoformat()} for r in db.scalars(select(VaultConditionRecord).where(VaultConditionRecord.card_id == c.id))],
    }
    if private:
        out.update({"acquisition_cost": c.acquisition_cost_cents, "acquisition_currency": c.acquisition_currency, "acquisition_ref": c.acquisition_ref,
                    "custody_location": c.custody_location, "owner_member_id": c.owner_member_id, "owner_wallet": c.owner_wallet,
                    "request_id": c.request_id,
                    "events": [{"at": e.at.isoformat(), "field": e.field, "old": e.old, "new": e.new, "actor": e.actor, "note": e.note}
                               for e in db.scalars(select(VaultEvent).where(VaultEvent.card_id == c.id).order_by(VaultEvent.id))]})
    return out


def start_acquisition(db: Session, actor: str, *, listing_id: int | None = None, request_id: int | None = None, title: str | None = None,
                      note: str = "") -> VaultCard:
    """Price Chat has started buying a real card: it gets its permanent id. The card is NOT owned or backed yet."""
    listing = db.get(VaultListing, listing_id) if listing_id else None
    if listing_id and listing is None:
        raise MemberError("Not found.")
    name = (title or (listing.title if listing else "")).strip()
    if not name:
        raise MemberError("Give the card a title.")
    uid = new_uid()
    while db.scalar(select(VaultCard.id).where(VaultCard.uid == uid)):
        uid = new_uid()
    c = VaultCard(uid=uid, listing_id=listing.id if listing else None, request_id=request_id, title=name[:300],
                  pokemon=(listing.pokemon_override or listing.pokemon) if listing else None,
                  set_name=(listing.set_override or listing.set_name) if listing else None,
                  year=(listing.year_override or listing.year) if listing else None, language="Japanese",
                  acquisition_status="purchase_pending", custody_status="none", nft_status="not_eligible", version=0)
    db.add(c)
    db.flush()
    _event(db, c, "acquisition", None, "purchase_pending", actor, note or "acquisition started")
    db.commit()
    return c


def _check(table: dict[str, set[str]], old: str, new: str, what: str) -> None:
    if new not in table:
        raise MemberError(f"Unknown {what} status: {new}.")
    if new not in table[old]:
        raise MemberError(f"A card's {what} can't go from {old.replace('_', ' ')} to {new.replace('_', ' ')}.")


def _refresh_nft_eligibility(db: Session, c: VaultCard, actor: str) -> None:
    ok = c.acquisition_status == "verified" and c.custody_status == "in_custody"
    if ok and c.nft_status == "not_eligible":
        _event(db, c, "nft", c.nft_status, "eligible", actor, "verified and in custody")
        c.nft_status = "eligible"
    elif not ok and c.nft_status == "eligible":
        _event(db, c, "nft", c.nft_status, "not_eligible", actor, "no longer verified + in custody")
        c.nft_status = "not_eligible"


def set_status(db: Session, card_id: int, field: str, new: str, actor: str, note: str = "", **extra: Any) -> VaultCard:
    c = db.get(VaultCard, card_id)
    if c is None:
        raise MemberError("Not found.")
    now = utcnow()
    if field == "acquisition":
        _check(ACQUISITION, c.acquisition_status, new, "acquisition")
        if new == "purchased":
            c.acquired_at = now
            for k in ("acquisition_cost_cents", "acquisition_currency", "acquisition_ref"):
                if extra.get(k) is not None:
                    setattr(c, k, extra[k])
        if new == "received":
            c.received_at = now
        if new == "verified":
            if not db.scalar(select(VaultConditionRecord.id).where(VaultConditionRecord.card_id == c.id).limit(1)):
                raise MemberError("Record our own condition inspection before verifying the card.")
            if not db.scalar(select(VaultCardPhoto.id).where(VaultCardPhoto.card_id == c.id, VaultCardPhoto.side == "front").limit(1)):
                raise MemberError("Add our own front photo of the card before verifying it.")
            c.verified_at = now
        _event(db, c, "acquisition", c.acquisition_status, new, actor, note)
        c.acquisition_status = new
    elif field == "custody":
        _check(CUSTODY, c.custody_status, new, "custody")
        if new == "in_custody" and c.acquisition_status != "verified":
            raise MemberError("Only a verified card can be recorded as in custody.")
        if new == "in_custody" and extra.get("custody_location"):
            c.custody_location = str(extra["custody_location"])[:120]
        if new == "shipped" and c.nft_status in ("mint_pending", "minted"):
            raise MemberError("Burn the card's NFT before shipping it: one card, one claim.")
        _event(db, c, "custody", c.custody_status, new, actor, note)
        c.custody_status = new
    elif field == "nft":
        _check(NFT, c.nft_status, new, "NFT")
        if new == "mint_pending" and not (c.acquisition_status == "verified" and c.custody_status == "in_custody"):
            raise MemberError("No NFT before the physical card is verified and in custody.")
        if new == "minted":
            missing = [k for k in ("nft_chain", "nft_contract", "nft_token_id", "mint_tx") if not extra.get(k)]
            if missing:
                raise MemberError("Record the real mint: " + ", ".join(missing) + ".")
            for k in ("nft_chain", "nft_contract", "nft_token_id", "mint_tx"):
                setattr(c, k, str(extra[k])[:80])
        _event(db, c, "nft", c.nft_status, new, actor, note)
        c.nft_status = new
    else:
        raise MemberError("Unknown field.")
    c.version += 1
    _refresh_nft_eligibility(db, c, actor)
    db.commit()
    return c


def add_photo(db: Session, card_id: int, side: str, url: str, actor: str) -> None:
    c = db.get(VaultCard, card_id)
    if c is None:
        raise MemberError("Not found.")
    if not url.startswith("https://"):
        raise MemberError("Photo URLs must be https.")
    db.add(VaultCardPhoto(card_id=c.id, side=side, url=url[:500], taken_at=utcnow()))
    _event(db, c, "note", None, None, actor, f"photo added ({side})")
    db.commit()


def add_condition(db: Session, card_id: int, grade: str, notes: str, matches_listing: bool, actor: str) -> None:
    c = db.get(VaultCard, card_id)
    if c is None:
        raise MemberError("Not found.")
    db.add(VaultConditionRecord(card_id=c.id, inspector=actor[:60], grade=grade, notes=notes[:4000], matches_listing=matches_listing))
    _event(db, c, "note", None, None, actor, f"condition inspected: {grade}" + ("" if matches_listing else " (doesn't match the listing)"))
    db.commit()


def assign_owner(db: Session, card_id: int, member_id: int | None, wallet: str | None, actor: str, note: str = "") -> VaultCard:
    c = db.get(VaultCard, card_id)
    if c is None:
        raise MemberError("Not found.")
    if c.acquisition_status != "verified":
        raise MemberError("A card has no owner on Price Chat until it is verified.")
    _event(db, c, "owner", str(c.owner_member_id) if c.owner_member_id else None, str(member_id) if member_id else None, actor, note)
    c.owner_member_id, c.owner_wallet = member_id, (wallet or None)
    c.version += 1
    db.commit()
    return c


def request_redemption(db: Session, card_id: int, member_id: int, address: dict[str, str]) -> VaultRedemption:
    c = db.get(VaultCard, card_id)
    if c is None or c.owner_member_id != member_id:
        raise MemberError("Not found.")
    if c.custody_status != "in_custody":
        raise MemberError("This card can't be redeemed right now.")
    r = VaultRedemption(card_id=c.id, member_id=member_id, address=address, status="requested")
    db.add(r)
    _event(db, c, "custody", c.custody_status, "redemption_pending", f"member:{member_id}", "redemption requested")
    c.custody_status = "redemption_pending"
    _refresh_nft_eligibility(db, c, "system")
    db.commit()
    return r


def list_cards(db: Session) -> list[dict[str, Any]]:
    return [card_out(db, c) for c in db.scalars(select(VaultCard).order_by(VaultCard.id.desc()).limit(200))]
