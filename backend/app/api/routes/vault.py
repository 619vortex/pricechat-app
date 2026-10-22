"""Price Chat Vault API (docs/VAULT.md).

/api/members/vault/*   the storefront is public; a Request to Buy needs the member's Kiwi Passport session
/api/vault/admin/*     owner only (the admin gate protects every non-member /api path): sources, sync, approvals,
                       requests, the seller-provided catalog and the physical-card registry
"""

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import Field

from app.api.deps import DbSession, Runtime
from app.api.routes.members import SessionToken, _current, _fail, _require
from app.api.schemas import StrictInput
from app.members.service import MemberError
from app.models.vault import VaultSource
from app.vault import cards, service
from app.vault.sync import sync_ebay_seller

router = APIRouter(tags=["vault"])
MAX_PRICE = 100_000_000  # dollars: a bound for price inputs (inf / 1e300 would overflow the cents conversion)
Sort = Literal["featured", "newest", "price_asc", "price_desc", "year_asc", "year_desc"]


def _err(exc: Exception) -> HTTPException:
    if str(exc) == "Not found.":
        return HTTPException(status.HTTP_404_NOT_FOUND, "Not found.")
    return _fail(exc)


def _cents(v: float | None) -> int | None:
    return None if v is None else int(round(v * 100))


# --- storefront (public) -------------------------------------------------------------------------------------------


@router.get("/members/vault/home")
def vault_home(db: DbSession, runtime: Runtime) -> dict[str, Any]:
    service.ensure_sources(db, runtime.settings)
    return service.home(db, runtime.settings)


@router.get("/members/vault/listings")
def vault_browse(db: DbSession, runtime: Runtime, q: str = Query("", max_length=80), pokemon: str | None = Query(None, max_length=60),
                 set_name: str | None = Query(None, alias="set", max_length=80), year: int | None = Query(None, ge=1990, le=2100),
                 min_price: float | None = Query(None, ge=0, le=MAX_PRICE), max_price: float | None = Query(None, ge=0, le=MAX_PRICE), sort: Sort = "featured",
                 sold: bool = False, page: int = Query(1, ge=1, le=500)) -> dict[str, Any]:
    return service.browse(db, runtime.settings, q=q, pokemon=pokemon, set_name=set_name, year=year, min_price=_cents(min_price),
                          max_price=_cents(max_price), sort=sort, include_ended=sold, page=page)


@router.get("/members/vault/listings/{listing_id}")
def vault_listing(listing_id: int, db: DbSession, runtime: Runtime, pc_session: SessionToken = None) -> dict[str, Any]:
    try:
        return service.detail(db, runtime.settings, listing_id, _current(db, pc_session))
    except MemberError as exc:
        raise _err(exc) from exc


class RequestIn(StrictInput):
    contact: str = Field("", max_length=120)
    note: str = Field("", max_length=1000)


@router.post("/members/vault/listings/{listing_id}/request")
def vault_request(listing_id: int, body: RequestIn, db: DbSession, runtime: Runtime, pc_session: SessionToken = None) -> dict[str, Any]:
    member = _require(db, pc_session)
    try:
        return service.create_request(db, runtime.settings, member, listing_id, body.contact, body.note)
    except MemberError as exc:
        raise _err(exc) from exc


@router.get("/members/vault/my/requests")
def vault_my_requests(db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    return {"requests": service.my_requests(db, _require(db, pc_session))}


@router.post("/members/vault/requests/{request_id}/withdraw")
def vault_withdraw(request_id: int, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    member = _require(db, pc_session)
    try:
        return service.withdraw_request(db, member, request_id)
    except MemberError as exc:
        raise _err(exc) from exc


# --- admin -----------------------------------------------------------------------------------------------------------


@router.get("/vault/admin/overview")
def admin_overview(db: DbSession, runtime: Runtime) -> dict[str, Any]:
    return service.admin_overview(db, runtime.settings)


@router.post("/vault/admin/sources/{source_id}/sync")
async def admin_sync(source_id: int, db: DbSession, runtime: Runtime) -> dict[str, Any]:
    src = db.get(VaultSource, source_id)
    if src is None or src.kind != "ebay_seller":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found.")
    return await sync_ebay_seller(db, runtime.settings, src, transport=getattr(runtime, "pokemon_transport", None))


class SourceIn(StrictInput):
    enabled: bool


@router.post("/vault/admin/sources/{source_id}")
def admin_source(source_id: int, body: SourceIn, db: DbSession) -> dict[str, Any]:
    try:
        service.set_source(db, source_id, body.enabled)
    except MemberError as exc:
        raise _err(exc) from exc
    return {"ok": True}


@router.get("/vault/admin/listings")
def admin_listings(db: DbSession, runtime: Runtime, review: Literal["pending", "approved", "hidden"] | None = None, eligible: bool | None = None,
                   availability: Literal["available", "sold_out", "ended", "unknown"] | None = None, q: str = Query("", max_length=80),
                   source_id: int | None = None, page: int = Query(1, ge=1, le=1000)) -> dict[str, Any]:
    return service.admin_listings(db, runtime.settings, review=review, eligible=eligible, availability=availability, q=q, source_id=source_id, page=page)


class ListingEdit(StrictInput):
    review: Literal["pending", "approved", "hidden"] | None = None
    featured: bool | None = None
    pokemon_override: str | None = Field(None, max_length=60)
    set_override: str | None = Field(None, max_length=80)
    year_override: int | None = Field(None, ge=1996, le=2100)


@router.patch("/vault/admin/listings/{listing_id}")
def admin_edit(listing_id: int, body: ListingEdit, db: DbSession, runtime: Runtime) -> dict[str, Any]:
    try:
        return service.admin_update(db, runtime.settings, listing_id, body.model_dump(exclude_unset=True))
    except MemberError as exc:
        raise _err(exc) from exc


class BulkIn(StrictInput):
    ids: list[int] = Field(..., min_length=1, max_length=200)
    review: Literal["pending", "approved", "hidden"]


@router.post("/vault/admin/listings/bulk")
def admin_bulk(body: BulkIn, db: DbSession, runtime: Runtime) -> dict[str, Any]:
    return service.admin_bulk(db, runtime.settings, body.ids, body.review)


@router.get("/vault/admin/requests")
def admin_requests(db: DbSession, status_: Literal["new", "contacted", "closed", "declined", "withdrawn"] | None = Query(None, alias="status")) -> dict[str, Any]:
    return {"requests": service.admin_requests(db, status_)}


class RequestEdit(StrictInput):
    status: Literal["new", "contacted", "closed", "declined"] | None = None
    admin_note: str | None = Field(None, max_length=2000)


@router.patch("/vault/admin/requests/{request_id}")
def admin_request_edit(request_id: int, body: RequestEdit, db: DbSession) -> dict[str, Any]:
    try:
        return service.admin_request_update(db, request_id, body.status, body.admin_note)
    except MemberError as exc:
        raise _err(exc) from exc


class CatalogSourceIn(StrictInput):
    key: str = Field(..., min_length=2, max_length=64)
    name: str = Field(..., min_length=2, max_length=80)
    url: str | None = Field(None, max_length=300)


@router.post("/vault/admin/catalogs")
def admin_add_catalog(body: CatalogSourceIn, db: DbSession) -> dict[str, Any]:
    try:
        s = service.add_catalog_source(db, body.key, body.name, body.url)
    except MemberError as exc:
        raise _err(exc) from exc
    return {"id": s.id}


class CatalogItemIn(StrictInput):
    sku: str = Field(..., min_length=1, max_length=80)
    title: str = Field(..., min_length=3, max_length=300)
    price: float | None = Field(None, ge=0)
    currency: str = Field("USD", min_length=3, max_length=3)
    images: list[str] = Field(default_factory=list, max_length=12)
    condition: str | None = Field(None, max_length=80)
    condition_detail: str | None = Field(None, max_length=2000)
    url: str | None = Field(None, max_length=500)
    availability: Literal["available", "sold_out", "ended"] = "available"
    aspects: dict[str, str] | None = None


@router.post("/vault/admin/catalogs/{source_id}/items")
def admin_catalog_item(source_id: int, body: CatalogItemIn, db: DbSession, runtime: Runtime) -> dict[str, Any]:
    data = body.model_dump()
    data["price_cents"] = _cents(body.price)
    try:
        return service.upsert_catalog_item(db, runtime.settings, source_id, data)
    except MemberError as exc:
        raise _err(exc) from exc


# --- physical-card registry (architecture for the future card-backed NFTs; nothing is minted here) --------------------


@router.get("/vault/admin/cards")
def admin_cards(db: DbSession) -> dict[str, Any]:
    return {"cards": cards.list_cards(db)}


class CardIn(StrictInput):
    listing_id: int | None = None
    request_id: int | None = None
    title: str | None = Field(None, max_length=300)
    note: str = Field("", max_length=2000)


@router.post("/vault/admin/cards")
def admin_card_start(body: CardIn, db: DbSession) -> dict[str, Any]:
    try:
        c = cards.start_acquisition(db, "owner", listing_id=body.listing_id, request_id=body.request_id, title=body.title, note=body.note)
    except MemberError as exc:
        raise _err(exc) from exc
    return cards.card_out(db, c)


class CardStatusIn(StrictInput):
    field: Literal["acquisition", "custody", "nft"]
    status: str = Field(..., max_length=20)
    note: str = Field("", max_length=2000)
    acquisition_cost: float | None = Field(None, ge=0, le=MAX_PRICE)
    acquisition_currency: str | None = Field(None, min_length=3, max_length=3)
    acquisition_ref: str | None = Field(None, max_length=80)
    custody_location: str | None = Field(None, max_length=120)
    nft_chain: str | None = Field(None, max_length=16)
    nft_contract: str | None = Field(None, max_length=64)
    nft_token_id: str | None = Field(None, max_length=80)
    mint_tx: str | None = Field(None, max_length=80)


@router.post("/vault/admin/cards/{card_id}/status")
def admin_card_status(card_id: int, body: CardStatusIn, db: DbSession) -> dict[str, Any]:
    extra = body.model_dump(exclude={"field", "status", "note", "acquisition_cost"})
    extra["acquisition_cost_cents"] = _cents(body.acquisition_cost)
    try:
        c = cards.set_status(db, card_id, body.field, body.status, "owner", body.note, **extra)
    except MemberError as exc:
        raise _err(exc) from exc
    return cards.card_out(db, c)


class CardPhotoIn(StrictInput):
    side: Literal["front", "back", "detail"]
    url: str = Field(..., max_length=500)


@router.post("/vault/admin/cards/{card_id}/photos")
def admin_card_photo(card_id: int, body: CardPhotoIn, db: DbSession) -> dict[str, Any]:
    try:
        cards.add_photo(db, card_id, body.side, body.url, "owner")
    except MemberError as exc:
        raise _err(exc) from exc
    return {"ok": True}


class ConditionIn(StrictInput):
    grade: Literal["NM", "LP", "MP", "HP", "DMG"]
    notes: str = Field("", max_length=4000)
    matches_listing: bool = True


@router.post("/vault/admin/cards/{card_id}/condition")
def admin_card_condition(card_id: int, body: ConditionIn, db: DbSession) -> dict[str, Any]:
    try:
        cards.add_condition(db, card_id, body.grade, body.notes, body.matches_listing, "owner")
    except MemberError as exc:
        raise _err(exc) from exc
    return {"ok": True}
