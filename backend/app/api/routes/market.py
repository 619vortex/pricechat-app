"""Card Marketplace API (docs/MARKETPLACE.md).

/api/members/market/*: browsing is public; selling, buying, offers, watchlist and vault need the member's Kiwi Passport
session. /api/market/admin/*: owner only (the admin gate protects every non-member /api path).
Real-money settlement is disabled: the server runs the trading simulation until every launch gate passes.
"""

from typing import Any, Literal

from fastapi import APIRouter, File, Form, Header, HTTPException, Query, UploadFile, status
from fastapi.responses import Response
from pydantic import Field
from sqlalchemy import select

from app.api.deps import DbSession, Runtime
from app.api.routes.members import SessionToken, _current, _fail, _require
from app.api.schemas import StrictInput
from app.market import admin, compliance, payments, raw, service, wallets
from app.market.service import Conflict
from app.members.service import MemberError
from app.models.market import MarketItem, MarketListing, MarketPhoto
from app.portfolio import photos

router = APIRouter(tags=["market"])


def _err(exc: Exception) -> HTTPException:
    if isinstance(exc, Conflict):
        return HTTPException(status.HTTP_409_CONFLICT, str(exc))
    if str(exc) == "Not found.":
        return HTTPException(status.HTTP_404_NOT_FOUND, "Not found.")
    return _fail(exc)


MAX_PRICE = 100_000_000  # USDT: bounds the browse filters (inf / 1e300 overflowed the cents conversion: a 500)


def _usdt(v: float | None) -> int | None:
    return None if v is None else int(round(v * 100))


# --- browsing (public) ---------------------------------------------------------------------------------------------------


@router.get("/members/market/status")
def market_status(db: DbSession, runtime: Runtime, pc_session: SessionToken = None) -> dict[str, Any]:
    s = runtime.settings
    member = _current(db, pc_session)
    return {"mode": compliance.mode(s), "sandbox": service.sandbox_enabled(db, member), "signed_in": member is not None,
            "networks": [{"id": k, "label": v["label"], "token": v["token"], "decimals": v["decimals"], "confirmations": v["confirmations"],
                          "fee_estimate": v["fee_estimate"], "wallets": v["wallets"], "testnet": bool(v.get("testnet")), "chain_id": v.get("chain_id")}
                         for k, v in payments.enabled(s).items()],
            "fees": service.fees(None, 10_000, s), "photos": photos.Storage(s).available,
            "notice": "Live USDT settlement is not enabled. Browsing, selling drafts, verification and watchlists work; buying and offers run "
                      "as a clearly marked simulation on sandbox listings only."}


@router.get("/members/market/home")
def market_home(db: DbSession, runtime: Runtime, pc_session: SessionToken = None) -> dict[str, Any]:
    return service.home(db, _current(db, pc_session), runtime.settings)


@router.get("/members/market/listings")
def browse(db: DbSession, q: str = Query("", max_length=80), set_name: str | None = Query(None, max_length=80), year: int | None = None,
           language: str | None = Query(None, max_length=16), edition: str | None = Query(None, max_length=24), number: str | None = Query(None, max_length=12),
           company: Literal["PSA", "BGS", "CGC"] | None = None, grade: str | None = Query(None, max_length=8),
           min_price: float | None = Query(None, ge=0, le=MAX_PRICE), max_price: float | None = Query(None, ge=0, le=MAX_PRICE), min_discount: float | None = Query(None, ge=0, le=100),
           vaulted: bool = False, kind: Literal["graded", "raw"] | None = None, status_: Literal["active", "sold"] = Query("active", alias="status"),
           sort: Literal["recent", "price_asc", "price_desc", "value", "discount", "sold"] = "recent", page: int = Query(1, ge=1, le=500),
           pc_session: SessionToken = None) -> dict[str, Any]:
    return service.browse(db, _current(db, pc_session), q=q, set_name=set_name, year=year, language=language, edition=edition, number=number,
                          company=company, grade=grade, min_price=_usdt(min_price), max_price=_usdt(max_price), min_discount=min_discount,
                          vaulted=vaulted, kind=kind, status=status_, sort=sort, page=page)


@router.get("/members/market/listings/{listing_id}")
def listing(listing_id: int, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    try:
        return service.listing_detail(db, listing_id, _current(db, pc_session))
    except MemberError as exc:
        raise _err(exc) from exc


# --- wallets (prove by signature; public address only) ---------------------------------------------------------------


@router.get("/members/market/wallets")
def my_wallets(db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    return {"wallets": wallets.mine(db, _require(db, pc_session))}


class WalletChallengeIn(StrictInput):
    address: str = Field(min_length=42, max_length=42)


@router.post("/members/market/wallets/challenge")
def wallet_challenge(payload: WalletChallengeIn, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    try:
        return wallets.challenge(db, _require(db, pc_session), payload.address)
    except MemberError as exc:
        raise _err(exc) from exc


class WalletVerifyIn(StrictInput):
    address: str = Field(min_length=42, max_length=42)
    signature: str = Field(min_length=130, max_length=132)
    wallet: str = Field("", max_length=40)


@router.post("/members/market/wallets/verify")
def wallet_verify(payload: WalletVerifyIn, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    member = _require(db, pc_session)
    try:
        wallets.verify(db, member, payload.address, payload.signature, payload.wallet)
        return {"wallets": wallets.mine(db, member)}
    except MemberError as exc:
        raise _err(exc) from exc


@router.post("/members/market/wallets/{wallet_id}/payout")
def wallet_payout(wallet_id: int, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    try:
        return {"wallets": wallets.set_payout(db, _require(db, pc_session), wallet_id)}
    except MemberError as exc:
        raise _err(exc) from exc


@router.delete("/members/market/wallets/{wallet_id}")
def wallet_remove(wallet_id: int, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    try:
        return {"wallets": wallets.remove(db, _require(db, pc_session), wallet_id)}
    except MemberError as exc:
        raise _err(exc) from exc


class VoteIn(StrictInput):
    estimate: int = Field(ge=1, le=10)
    note: str = Field("", max_length=280)


@router.post("/members/market/listings/{listing_id}/condition-vote")
def condition_vote(listing_id: int, payload: VoteIn, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    """A member's own 1-10 grade for a RAW card (one each, changeable; not the seller)."""
    try:
        return raw.vote(db, _require(db, pc_session), listing_id, payload.estimate, payload.note)
    except MemberError as exc:
        raise _err(exc) from exc


@router.get("/members/market/listings/{listing_id}/comments")
def listing_comments(listing_id: int, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    try:
        return {"comments": raw.comments(db, listing_id, _current(db, pc_session))}
    except MemberError as exc:
        raise _err(exc) from exc


class CommentIn(StrictInput):
    body: str = Field(min_length=2, max_length=1000)
    parent_id: int | None = None


@router.post("/members/market/listings/{listing_id}/comments", status_code=status.HTTP_201_CREATED)
def add_listing_comment(listing_id: int, payload: CommentIn, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    try:
        return {"comments": raw.add_comment(db, _require(db, pc_session), listing_id, payload.body, payload.parent_id)}
    except MemberError as exc:
        raise _err(exc) from exc


@router.delete("/members/market/comments/{comment_id}")
def delete_listing_comment(comment_id: int, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    try:
        raw.delete_comment(db, _require(db, pc_session), comment_id)
        return {"ok": True}
    except MemberError as exc:
        raise _err(exc) from exc


@router.get("/members/market/photos/{photo_id}")
def photo(photo_id: int, db: DbSession, runtime: Runtime, size: Literal["thumb", "full"] = "thumb", pc_session: SessionToken = None) -> Response:
    """A slab photo: public once its listing is public (active / reserved / sold); otherwise only for the seller."""
    p = db.get(MarketPhoto, photo_id)
    if p is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found.")
    viewer = _current(db, pc_session)
    item = db.get(MarketItem, p.item_id)
    listings = db.scalars(select(MarketListing).where(MarketListing.item_id == p.item_id)).all()
    public = any(l.status in service.PUBLIC_STATUSES and (not l.is_sandbox or service.sandbox_enabled(db, viewer)) for l in listings)
    if not public and not (viewer and item and (viewer.id == item.owner_member_id or any(l.seller_id == viewer.id for l in listings))):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found.")
    data = photos.Storage(runtime.settings).get(p.thumb_key if size == "thumb" else p.key)
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff"})


# --- selling -------------------------------------------------------------------------------------------------------------


@router.get("/members/market/sell/holdings")
def sellable(db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    return {"holdings": service.eligible_holdings(db, _require(db, pc_session))}


@router.get("/members/market/seller")
def seller_status(db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    return service.seller_out(db, _require(db, pc_session))


class VerificationIn(StrictInput):
    country: str = Field(min_length=2, max_length=2)
    about: str = Field(min_length=20, max_length=1000)


@router.post("/members/market/seller/verification")
def seller_verification(payload: VerificationIn, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    try:
        return service.request_verification(db, _require(db, pc_session), payload.country, payload.about)
    except MemberError as exc:
        raise _err(exc) from exc


class ListingIn(StrictInput):
    holding_id: int
    grading_company: Literal["PSA", "BGS", "CGC", "RAW"]  # RAW: condition comes from the card's AI scan, no cert
    grade: str = Field("", max_length=8)
    cert_number: str = Field("", max_length=40)
    price: float | None = Field(None, ge=1, le=1_000_000)  # USDT
    ownership_evidence: str = Field("", max_length=4000)
    note: str = Field("", max_length=500)


@router.post("/members/market/listings", status_code=status.HTTP_201_CREATED)
def create_listing(payload: ListingIn, db: DbSession, runtime: Runtime, pc_session: SessionToken = None) -> dict[str, Any]:
    member = _require(db, pc_session)
    try:
        l = service.create_listing(db, runtime.settings, member, holding_id=payload.holding_id, company=payload.grading_company, grade=payload.grade,
                                   cert=payload.cert_number, price_cents=_usdt(payload.price), evidence=payload.ownership_evidence, note=payload.note)
        return service.listing_detail(db, l.id, member)
    except MemberError as exc:
        raise _err(exc) from exc


class ListingUpdate(StrictInput):
    price: float | None = Field(None, ge=1, le=1_000_000)
    ownership_evidence: str | None = Field(None, max_length=4000)
    note: str | None = Field(None, max_length=500)


@router.patch("/members/market/listings/{listing_id}")
def update_listing(listing_id: int, payload: ListingUpdate, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    member = _require(db, pc_session)
    try:
        service.update_listing(db, member, listing_id, price_cents=_usdt(payload.price), evidence=payload.ownership_evidence, note=payload.note)
        return service.listing_detail(db, listing_id, member)
    except MemberError as exc:
        raise _err(exc) from exc


@router.post("/members/market/listings/{listing_id}/photos", status_code=status.HTTP_201_CREATED)
async def listing_photo(listing_id: int, db: DbSession, runtime: Runtime, side: Literal["front", "back", "label"] = Form(...),
                        file: UploadFile = File(...), pc_session: SessionToken = None) -> dict[str, Any]:
    member = _require(db, pc_session)
    data = await file.read(photos.MAX_UPLOAD + 1)
    if len(data) > photos.MAX_UPLOAD:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "Photos up to 15 MB.")
    try:
        p = service.add_photo(db, runtime.settings, member, listing_id, side, data)
    except MemberError as exc:
        raise _err(exc) from exc
    return {"id": p.id, "side": p.side}


@router.post("/members/market/listings/{listing_id}/submit")
def submit_listing(listing_id: int, db: DbSession, runtime: Runtime, pc_session: SessionToken = None) -> dict[str, Any]:
    member = _require(db, pc_session)
    try:
        service.submit(db, runtime.settings, member, listing_id)
        return service.listing_detail(db, listing_id, member)
    except MemberError as exc:
        raise _err(exc) from exc


@router.post("/members/market/listings/{listing_id}/publish")
def publish_listing(listing_id: int, db: DbSession, runtime: Runtime, pc_session: SessionToken = None) -> dict[str, Any]:
    member = _require(db, pc_session)
    try:
        service.publish(db, runtime.settings, member, listing_id)
        return service.listing_detail(db, listing_id, member)
    except MemberError as exc:
        raise _err(exc) from exc


@router.post("/members/market/listings/{listing_id}/cancel")
def cancel_listing(listing_id: int, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    member = _require(db, pc_session)
    try:
        service.cancel_listing(db, member, listing_id)
        return service.listing_detail(db, listing_id, member)
    except MemberError as exc:
        raise _err(exc) from exc


@router.get("/members/market/my/listings")
def my_listings(db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    return {"listings": service.my_listings(db, _require(db, pc_session))}


# --- watchlist, reports ----------------------------------------------------------------------------------------------------


class WatchIn(StrictInput):
    on: bool = True


@router.post("/members/market/listings/{listing_id}/watch")
def watch(listing_id: int, payload: WatchIn, db: DbSession, pc_session: SessionToken = None) -> dict[str, bool]:
    member = _require(db, pc_session)
    try:
        return {"watching": service.watch(db, member, listing_id, payload.on)}
    except MemberError as exc:
        raise _err(exc) from exc


@router.get("/members/market/my/watchlist")
def my_watchlist(db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    return {"listings": service.watchlist(db, _require(db, pc_session))}


class ReportIn(StrictInput):
    reason: str = Field(min_length=3, max_length=1000)


@router.post("/members/market/listings/{listing_id}/report")
def report_listing(listing_id: int, payload: ReportIn, db: DbSession, pc_session: SessionToken = None) -> dict[str, bool]:
    member = _require(db, pc_session)
    try:
        service.report(db, member, listing_id, payload.reason)
    except MemberError as exc:
        raise _err(exc) from exc
    return {"reported": True}


# --- buying, offers, orders (simulation until live gates pass) -------------------------------------------------------------


class BuyIn(StrictInput):
    network: Literal["tron", "solana", "ethereum", "sepolia"]


@router.post("/members/market/listings/{listing_id}/buy", status_code=status.HTTP_201_CREATED)
def buy(listing_id: int, payload: BuyIn, db: DbSession, runtime: Runtime, idempotency_key: str = Header(..., min_length=8, max_length=60),
        pc_session: SessionToken = None) -> dict[str, Any]:
    member = _require(db, pc_session)
    try:
        o = service.buy_now(db, runtime.settings, member, listing_id, payload.network, idempotency_key)
        return service.order_out(db, o, member)
    except MemberError as exc:
        raise _err(exc) from exc


class OfferIn(StrictInput):
    amount: float = Field(ge=1, le=1_000_000)


@router.post("/members/market/listings/{listing_id}/offers", status_code=status.HTTP_201_CREATED)
def offer(listing_id: int, payload: OfferIn, db: DbSession, runtime: Runtime, pc_session: SessionToken = None) -> dict[str, Any]:
    member = _require(db, pc_session)
    try:
        return service.offer_out(service.make_offer(db, runtime.settings, member, listing_id, _usdt(payload.amount) or 0))
    except MemberError as exc:
        raise _err(exc) from exc


@router.post("/members/market/offers/{offer_id}/cancel")
def cancel_offer(offer_id: int, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    member = _require(db, pc_session)
    try:
        return service.offer_out(service.cancel_offer(db, member, offer_id))
    except MemberError as exc:
        raise _err(exc) from exc


class DecideIn(StrictInput):
    accept: bool
    network: Literal["tron", "solana", "ethereum", "sepolia"] = "tron"


@router.post("/members/market/offers/{offer_id}/decide")
def decide_offer(offer_id: int, payload: DecideIn, db: DbSession, runtime: Runtime, pc_session: SessionToken = None) -> dict[str, Any]:
    member = _require(db, pc_session)
    try:
        return service.offer_out(service.decide_offer(db, runtime.settings, member, offer_id, payload.accept, payload.network))
    except MemberError as exc:
        raise _err(exc) from exc


@router.get("/members/market/my/orders")
def my_orders(db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    return service.my_orders(db, _require(db, pc_session))


@router.get("/members/market/orders/{order_id}")
def order(order_id: int, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    from app.models.market import MarketOrder

    member = _require(db, pc_session)
    o = db.get(MarketOrder, order_id)
    if o is None or member.id not in (o.buyer_id, o.seller_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found.")
    return service.order_out(db, o, member)


class SimPayIn(StrictInput):
    outcome: Literal["exact", "underpaid", "wrong_network", "wrong_token", "wrong_address", "unconfirmed"] = "exact"


@router.post("/members/market/orders/{order_id}/simulate-payment")
def simulate_payment(order_id: int, payload: SimPayIn, db: DbSession, runtime: Runtime, pc_session: SessionToken = None) -> dict[str, Any]:
    """SANDBOX ONLY: a synthetic USDT transfer, then the same independent verification + settlement a real one gets."""
    member = _require(db, pc_session)
    try:
        o = service.simulate_payment(db, runtime.settings, member, order_id, payload.outcome)
        return service.order_out(db, o, member)
    except MemberError as exc:
        raise _err(exc) from exc


class TxIn(StrictInput):
    tx: str = Field(min_length=66, max_length=66)


@router.post("/members/market/orders/{order_id}/tx")
def submit_tx(order_id: int, payload: TxIn, db: DbSession, runtime: Runtime, pc_session: SessionToken = None) -> dict[str, Any]:
    """TESTNET: the transaction the buyer's wallet sent. The server checks it on-chain itself (token, recipient, exact
    amount, sender = the buyer's verified wallet, confirmations, time); the buyer's word alone settles nothing."""
    member = _require(db, pc_session)
    try:
        o = service.submit_tx(db, runtime.settings, member, order_id, payload.tx)
        return service.order_out(db, o, member)
    except MemberError as exc:
        raise _err(exc) from exc


@router.post("/members/market/orders/{order_id}/cancel")
def cancel_order(order_id: int, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    member = _require(db, pc_session)
    try:
        return service.order_out(db, service.cancel_order(db, member, order_id), member)
    except MemberError as exc:
        raise _err(exc) from exc


class DisputeIn(StrictInput):
    reason: str = Field(min_length=20, max_length=4000)


@router.post("/members/market/orders/{order_id}/dispute", status_code=status.HTTP_201_CREATED)
def dispute(order_id: int, payload: DisputeIn, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    member = _require(db, pc_session)
    try:
        d = service.open_dispute(db, member, order_id, payload.reason)
    except MemberError as exc:
        raise _err(exc) from exc
    return {"id": d.id, "status": d.status}


# --- vault & redemption ------------------------------------------------------------------------------------------------------


@router.get("/members/market/my/vault")
def my_vault(db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    return {"items": service.my_vault(db, _require(db, pc_session))}


@router.get("/members/market/redemption-quote")
def redemption_quote(db: DbSession, item_id: int, country: str = Query(min_length=2, max_length=2), pc_session: SessionToken = None) -> dict[str, Any]:
    member = _require(db, pc_session)
    item = db.get(MarketItem, item_id)
    if item is None or item.owner_member_id != member.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found.")
    est = service.item_estimate(db, item).get("value")
    return {"fees": service.redemption_fees(country, est), "currency": "USDT", "note": "Estimates until a vault/shipping partner quotes."}


class AddressIn(StrictInput):
    name: str = Field(min_length=2, max_length=120)
    line1: str = Field(min_length=2, max_length=120)
    line2: str = Field("", max_length=120)
    city: str = Field(min_length=1, max_length=120)
    region: str = Field("", max_length=120)
    postcode: str = Field(min_length=2, max_length=20)
    country: str = Field(min_length=2, max_length=2)
    phone: str = Field("", max_length=32)


class RedeemIn(StrictInput):
    item_id: int
    address: AddressIn


@router.post("/members/market/redemptions", status_code=status.HTTP_201_CREATED)
def redeem(payload: RedeemIn, db: DbSession, runtime: Runtime, pc_session: SessionToken = None) -> dict[str, Any]:
    member = _require(db, pc_session)
    try:
        r = service.request_redemption(db, runtime.settings, member, payload.item_id, payload.address.model_dump())
    except MemberError as exc:
        raise _err(exc) from exc
    return service.redemption_out(r)


@router.post("/members/market/redemptions/{redemption_id}/cancel")
def cancel_redeem(redemption_id: int, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    member = _require(db, pc_session)
    try:
        return service.redemption_out(service.cancel_redemption(db, member, redemption_id))
    except MemberError as exc:
        raise _err(exc) from exc


# --- admin (owner only) -------------------------------------------------------------------------------------------------------


def _adm(fn: Any) -> Any:
    try:
        return fn()
    except MemberError as exc:
        raise _err(exc) from exc


@router.get("/market/admin/overview")
def admin_overview(db: DbSession, runtime: Runtime) -> dict[str, Any]:
    return admin.overview(db, runtime.settings)


@router.get("/market/admin/listings")
def admin_listings(db: DbSession, status_: str = Query("awaiting_verification", alias="status", max_length=24)) -> dict[str, Any]:
    return {"listings": admin.queue(db, status_)}


@router.get("/market/admin/photos/{photo_id}")
def admin_photo(photo_id: int, db: DbSession, runtime: Runtime, size: Literal["thumb", "full"] = "full") -> Response:
    p = db.get(MarketPhoto, photo_id)
    if p is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found.")
    return Response(photos.Storage(runtime.settings).get(p.thumb_key if size == "thumb" else p.key), media_type="image/jpeg",
                    headers={"Cache-Control": "private, no-store"})


class VerifyIn(StrictInput):
    approve: bool
    note: str = Field("", max_length=2000)


@router.post("/market/admin/listings/{listing_id}/verify")
def admin_verify(listing_id: int, payload: VerifyIn, db: DbSession) -> dict[str, Any]:
    l = _adm(lambda: admin.verify_listing(db, listing_id, payload.approve, payload.note))
    return {"id": l.id, "status": l.status}


class AdminCancelIn(StrictInput):
    reason: str = Field(min_length=8, max_length=1000)


@router.post("/market/admin/listings/{listing_id}/cancel")
def admin_cancel(listing_id: int, payload: AdminCancelIn, db: DbSession) -> dict[str, Any]:
    l = _adm(lambda: service.cancel_listing(db, None, listing_id, actor="admin", reason=payload.reason))
    return {"id": l.id, "status": l.status}


@router.post("/market/admin/listings/{listing_id}/publish")
def admin_publish(listing_id: int, db: DbSession, runtime: Runtime) -> dict[str, Any]:
    l = _adm(lambda: service.publish(db, runtime.settings, None, listing_id))
    return {"id": l.id, "status": l.status}


class CustodyIn(StrictInput):
    to: Literal["submitted", "received", "vaulted"]
    partner: str | None = Field(None, max_length=48)
    inventory_id: str | None = Field(None, max_length=64)
    location: str | None = Field(None, max_length=120)
    insurance_confirmed: bool = False
    insurance_note: str = Field("", max_length=2000)
    reason: str | None = Field(None, max_length=1000)
    confirm: str | None = Field(None, max_length=40)


@router.post("/market/admin/items/{item_id}/custody")
def admin_custody(item_id: int, payload: CustodyIn, db: DbSession, runtime: Runtime) -> dict[str, Any]:
    i = _adm(lambda: admin.custody(db, runtime.settings, item_id, payload.to, partner=payload.partner, inventory_id=payload.inventory_id,
                                   location=payload.location, insurance_confirmed=payload.insurance_confirmed, insurance_note=payload.insurance_note,
                                   reason=payload.reason, confirm=payload.confirm))
    return {"id": i.id, "custody": i.custody_status}


class SellerIn(StrictInput):
    username: str = Field(min_length=2, max_length=32)
    status: Literal["unverified", "pending", "verified", "suspended"] | None = None
    level: int | None = Field(None, ge=0, le=2)
    sandbox: bool | None = None
    country: str | None = Field(None, max_length=2)
    note: str = Field("", max_length=500)


@router.get("/market/admin/sellers")
def admin_sellers(db: DbSession) -> dict[str, Any]:
    return {"sellers": admin.sellers(db)}


@router.post("/market/admin/sellers")
def admin_seller(payload: SellerIn, db: DbSession) -> dict[str, Any]:
    s = _adm(lambda: admin.set_seller(db, payload.username, status=payload.status, level=payload.level, sandbox=payload.sandbox,
                                      country=payload.country, note=payload.note))
    return {"status": s.status, "level": s.level, "sandbox": s.sandbox}


class SandboxIn(StrictInput):
    username: str = Field(min_length=2, max_length=32)
    card_id: int
    grading_company: Literal["PSA", "BGS", "CGC"]
    grade: str = Field(max_length=8)
    price: float = Field(ge=1, le=1_000_000)


@router.post("/market/admin/sandbox/listings", status_code=status.HTTP_201_CREATED)
def admin_sandbox(payload: SandboxIn, db: DbSession, runtime: Runtime) -> dict[str, Any]:
    l = _adm(lambda: admin.sandbox_listing(db, runtime.settings, username=payload.username, card_id=payload.card_id, company=payload.grading_company,
                                           grade=payload.grade, price_cents=_usdt(payload.price) or 0))
    return {"id": l.id, "status": l.status, "sandbox": True}


@router.get("/market/admin/orders")
def admin_orders(db: DbSession, status_: str | None = Query(None, alias="status", max_length=16)) -> dict[str, Any]:
    return {"orders": admin.orders(db, status_)}


class ConfirmIn(StrictInput):
    reason: str = Field(min_length=8, max_length=1000)
    confirm: str = Field(max_length=40)


@router.post("/market/admin/orders/{order_id}/refund")
def admin_refund(order_id: int, payload: ConfirmIn, db: DbSession) -> dict[str, Any]:
    o = _adm(lambda: admin.refund(db, order_id, payload.reason, payload.confirm))
    return {"id": o.id, "status": o.status}


@router.post("/market/admin/orders/{order_id}/settle")
def admin_settle(order_id: int, payload: ConfirmIn, db: DbSession, runtime: Runtime) -> dict[str, Any]:
    o = _adm(lambda: admin.retry_settlement(db, runtime.settings, order_id, payload.reason, payload.confirm))
    return {"id": o.id, "status": o.status}


@router.get("/market/admin/payments")
def admin_payments(db: DbSession) -> dict[str, Any]:
    return {"payments": admin.payments_list(db)}


@router.get("/market/admin/redemptions")
def admin_redemptions(db: DbSession) -> dict[str, Any]:
    return {"redemptions": admin.redemptions(db)}


class RedemptionStepIn(StrictInput):
    to: Literal["approved", "released", "shipped", "delivered", "returned", "lost", "cancelled"]
    carrier: str | None = Field(None, max_length=32)
    tracking: str | None = Field(None, max_length=64)
    note: str = Field("", max_length=1000)
    reason: str | None = Field(None, max_length=1000)
    confirm: str | None = Field(None, max_length=40)


@router.post("/market/admin/redemptions/{redemption_id}")
def admin_redemption(redemption_id: int, payload: RedemptionStepIn, db: DbSession) -> dict[str, Any]:
    def go() -> Any:
        if payload.to in ("released", "delivered"):  # irreversible custody steps
            admin.require_confirm(payload.reason, payload.confirm, f"REDEMPTION-{redemption_id}")
        return service.advance_redemption(db, redemption_id, payload.to, carrier=payload.carrier, tracking=payload.tracking, note=payload.note)

    r = _adm(go)
    return service.redemption_out(r)


@router.get("/market/admin/disputes")
def admin_disputes(db: DbSession) -> dict[str, Any]:
    return {"disputes": admin.disputes(db)}


class ResolveIn(StrictInput):
    status: Literal["resolved", "rejected"]
    resolution: str = Field(min_length=8, max_length=4000)


@router.post("/market/admin/disputes/{dispute_id}")
def admin_dispute(dispute_id: int, payload: ResolveIn, db: DbSession) -> dict[str, Any]:
    d = _adm(lambda: admin.resolve_dispute(db, dispute_id, payload.status, payload.resolution))
    return {"id": d.id, "status": d.status}


@router.get("/market/admin/reports")
def admin_reports(db: DbSession) -> dict[str, Any]:
    return {"reports": admin.reports(db)}


class ReportDecision(StrictInput):
    status: Literal["dismissed", "actioned"]


@router.post("/market/admin/reports/{report_id}")
def admin_report(report_id: int, payload: ReportDecision, db: DbSession) -> dict[str, Any]:
    r = _adm(lambda: admin.decide_report(db, report_id, payload.status))
    return {"id": r.id, "status": r.status}


@router.get("/market/admin/audit")
def admin_audit(db: DbSession, limit: int = Query(300, ge=1, le=1000)) -> dict[str, Any]:
    return {"audit": admin.audit_log(db, limit)}


@router.get("/market/admin/reconcile")
def admin_reconcile(db: DbSession) -> dict[str, Any]:
    return admin.reconcile(db)
