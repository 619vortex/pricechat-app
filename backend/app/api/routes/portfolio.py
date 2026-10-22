"""Pokémon Portfolio API. Under /api/members/ (the member area): the member's own Kiwi Passport session (pc_session) is
the only identity. Every write is the signed-in member's own portfolio; a private portfolio, its holdings, photos and
values are indistinguishable from "not found" to anyone else."""

from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import Response
from pydantic import Field
from sqlalchemy import func, select

from app.api.deps import DbSession, Runtime
from app.api.routes.members import SessionToken, _current, _fail, _require
from app.api.schemas import StrictInput
from app.db.base import utcnow
from app.members.service import MemberError
from app.models.portfolio import PokemonHolding, PokemonHoldingPhoto, PokemonPortfolio, PokemonPortfolioComment, PokemonPortfolioReport
from app.portfolio import community, condition, photos, prices, scan, service

router = APIRouter(tags=["portfolio"])
MAX_PHOTOS = 6  # per card
PHOTOS_PER_DAY = 200  # per member


def _err(exc: Exception) -> HTTPException:
    msg = str(exc)
    if "doesn't exist" in msg or msg == "Not found." or "isn't in your portfolio" in msg:
        return HTTPException(status.HTTP_404_NOT_FOUND, msg)
    return _fail(exc)


async def _read(upload: UploadFile) -> bytes:
    data = await upload.read(photos.MAX_UPLOAD + 1)
    if len(data) > photos.MAX_UPLOAD:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, f"Photos up to {photos.MAX_UPLOAD // (1024 * 1024)} MB.")
    return data


@router.get("/members/portfolio/status")
def portfolio_status(runtime: Runtime) -> dict[str, Any]:
    """What this server can do right now (the page hides what isn't set up)."""
    return {"scanner": scan.configured(runtime.settings), "condition": condition.configured(runtime.settings),
            "photos": photos.Storage(runtime.settings).available, "scans_per_day": runtime.settings.portfolio_scans_per_day}


@router.get("/members/portfolio")
def my_portfolio(db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    return service.dashboard(db, _require(db, pc_session))


class Settings_(StrictInput):
    visibility: Literal["private", "public"] | None = None
    show_value: bool | None = None
    show_purchase: bool | None = None
    comments_enabled: bool | None = None
    bio: str | None = Field(None, max_length=280)
    featured: list[int] | None = Field(None, max_length=6)


@router.patch("/members/portfolio")
def portfolio_settings(payload: Settings_, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    member = _require(db, pc_session)
    try:
        p = service.update_settings(db, member, **payload.model_dump(exclude_unset=True))
    except MemberError as exc:
        raise _err(exc) from exc
    return service.settings_out(p, member)


@router.get("/members/portfolio/search")
def search_cards(db: DbSession, q: str = Query("", max_length=120), limit: int = Query(30, ge=1, le=60), pc_session: SessionToken = None) -> dict[str, Any]:
    """Catalogue search for adding a card: every exact printing, with reference prices for orientation."""
    _require(db, pc_session)
    from app.pokemon import catalog

    if len(q.strip()) < 2:
        return {"query": q, "results": []}
    cards, _ = catalog.search(db, q, limit)
    prices = scan._prices(db, [c.id for c in cards])
    return {"query": q, "results": [scan.candidate_out(c, prices.get(c.id, {}), False, None) for c in cards]}


@router.get("/members/portfolio/quote")
async def quote(db: DbSession, runtime: Runtime, card_id: int, kind: Literal["raw", "graded"], company: Literal["PSA", "BGS", "CGC"] | None = None,
                grade: str | None = Query(None, max_length=8), pc_session: SessionToken = None) -> dict[str, Any]:
    """What a card would be valued at, before it's added (same engine as the portfolio; nothing is saved). When the stored
    data has no price for this grade / condition, the card's current provider prices are fetched first (bounded)."""
    member = _require(db, pc_session)
    from types import SimpleNamespace

    from app.portfolio import valuation

    try:
        comp, gr = service._clean_grade(kind, company, grade) if kind == "graded" else (None, None)
    except MemberError as exc:
        raise _err(exc) from exc
    h = SimpleNamespace(card_id=card_id, kind=kind, grading_company=comp, grade=gr, condition_low=None, condition_high=None)
    v = valuation.Pricer(db, [h]).value(h)
    if v.reason in prices.NEEDS_FETCH and await prices.ensure(db, runtime.settings, card_id, member, getattr(runtime, "pokemon_transport", None)) == "fetched":
        v = valuation.Pricer(db, [h]).value(h)
    return v.as_dict()


class HoldingIn(StrictInput):
    card_id: int
    kind: Literal["raw", "graded"]
    grading_company: Literal["PSA", "BGS", "CGC"] | None = None
    grade: str | None = Field(None, max_length=8)
    quantity: int = Field(1, ge=1, le=service.MAX_QUANTITY)
    cert_number: str | None = Field(None, max_length=32)
    condition_low: float | None = Field(None, ge=1, le=10)
    condition_high: float | None = Field(None, ge=1, le=10)
    purchase_price: int | None = Field(None, ge=0, le=10_000_000_00)  # cents
    purchase_date: date | None = None
    notes: str = Field("", max_length=500)
    is_public: bool = True
    source: Literal["manual", "scan"] = "manual"


@router.post("/members/portfolio/holdings", status_code=status.HTTP_201_CREATED)
async def add_holding(payload: HoldingIn, db: DbSession, runtime: Runtime, pc_session: SessionToken = None) -> dict[str, Any]:
    member = _require(db, pc_session)
    try:
        h = service.add_holding(db, member, payload.card_id, payload.kind, company=payload.grading_company, grade=payload.grade,
                                quantity=payload.quantity, cert_number=payload.cert_number, condition_low=payload.condition_low,
                                condition_high=payload.condition_high, purchase_price_cents=payload.purchase_price,
                                purchase_date=payload.purchase_date, notes=payload.notes, is_public=payload.is_public, source=payload.source)
    except MemberError as exc:
        raise _err(exc) from exc
    await _fetch_if_unpriced(db, runtime, h, member)
    return service.holding_detail(db, h.id, member)


async def _fetch_if_unpriced(db: Any, runtime: Any, h: PokemonHolding, member: Any) -> None:
    """No exact price stored for this holding: fetch its card's current provider prices once (bounded) and value it again."""
    if (h.value_reason or "") not in prices.NEEDS_FETCH:
        return
    out = await prices.ensure(db, runtime.settings, h.card_id, member, getattr(runtime, "pokemon_transport", None))
    if out == "fetched":
        service.revalue_holding(db, h)
    elif out == "error" and h.value_cents is None:
        h.value_reason, h.value_note = "provider_error", "Price unavailable right now: the price provider didn't answer. It's retried automatically."
        db.commit()


class HoldingUpdate(StrictInput):
    card_id: int | None = None  # Edit Identification: the same card, re-identified (photos and history are kept)
    kind: Literal["raw", "graded"] | None = None
    grading_company: Literal["PSA", "BGS", "CGC"] | None = None
    grade: str | None = Field(None, max_length=8)
    quantity: int | None = Field(None, ge=1, le=service.MAX_QUANTITY)
    cert_number: str | None = Field(None, max_length=32)
    condition_low: float | None = Field(None, ge=1, le=10)
    condition_high: float | None = Field(None, ge=1, le=10)
    purchase_price: int | None = Field(None, ge=0, le=10_000_000_00)
    purchase_date: date | None = None
    notes: str | None = Field(None, max_length=500)
    is_public: bool | None = None
    community_review: bool | None = None


@router.patch("/members/portfolio/holdings/{holding_id}")
async def update_holding(holding_id: int, payload: HoldingUpdate, db: DbSession, runtime: Runtime, pc_session: SessionToken = None) -> dict[str, Any]:
    member = _require(db, pc_session)
    changes = payload.model_dump(exclude_unset=True)
    if "grading_company" in changes:
        changes["company"] = changes.pop("grading_company")
    if "purchase_price" in changes:
        changes["purchase_price_cents"] = changes.pop("purchase_price")
    try:
        h = service.update_holding(db, member, holding_id, **changes)
    except MemberError as exc:
        raise _err(exc) from exc
    if {"card_id", "kind", "grading_company", "grade", "condition_low", "condition_high"} & set(payload.model_dump(exclude_unset=True)):
        await _fetch_if_unpriced(db, runtime, h, member)
    return service.holding_detail(db, h.id, member)


@router.delete("/members/portfolio/holdings/{holding_id}")
def remove_holding(holding_id: int, db: DbSession, pc_session: SessionToken = None) -> dict[str, bool]:
    try:
        service.remove_holding(db, _require(db, pc_session), holding_id)
    except MemberError as exc:
        raise _err(exc) from exc
    return {"removed": True}


@router.post("/members/portfolio/holdings/{holding_id}/duplicate", status_code=status.HTTP_201_CREATED)
def duplicate_holding(holding_id: int, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    member = _require(db, pc_session)
    try:
        h = service.duplicate_holding(db, member, holding_id)
    except MemberError as exc:
        raise _err(exc) from exc
    return service.holding_detail(db, h.id, member)


@router.get("/members/portfolio/holdings/{holding_id}")
def holding(holding_id: int, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    try:
        return service.holding_detail(db, holding_id, _current(db, pc_session))
    except MemberError as exc:
        raise _err(exc) from exc


# --- scan with your phone: a QR code on the computer signs the phone in (one time, 10 minutes) ------------------------------

HANDOFF_MINUTES = 10


@router.post("/members/portfolio/handoff", status_code=status.HTTP_201_CREATED)
def start_handoff(db: DbSession, runtime: Runtime, pc_session: SessionToken = None) -> dict[str, Any]:
    """A one-time link for the member's phone (shown as a QR code): opening it signs the phone in to THIS member and opens
    the scanner. Single use, 10 minutes, only the hash is stored; a new code replaces older unused ones."""
    import secrets
    from datetime import timedelta

    from app.members.auth import _h
    from app.models.members import MemberAuthRequest

    member = _require(db, pc_session)
    now = utcnow()
    db.query(MemberAuthRequest).filter(MemberAuthRequest.provider == "handoff", MemberAuthRequest.member_id == member.id,
                                       MemberAuthRequest.status == "pending").update({"status": "replaced"})
    db.query(MemberAuthRequest).filter(MemberAuthRequest.expires_at < now - timedelta(days=1)).delete()
    token = secrets.token_urlsafe(24)
    req = MemberAuthRequest(provider="handoff", purpose="handoff", state_hash=_h(token), browser_hash=_h(pc_session or ""),
                            member_id=member.id, status="pending", result={}, expires_at=now + timedelta(minutes=HANDOFF_MINUTES))
    db.add(req)
    db.commit()
    base = runtime.settings.members_public_url.rstrip("/")
    return {"id": req.id, "url": f"{base}/pokemon/portfolio/phone/{token}", "expires_at": req.expires_at.isoformat(), "minutes": HANDOFF_MINUTES}


@router.get("/members/portfolio/handoff/{handoff_id}")
def handoff_status(handoff_id: int, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    """For the computer showing the QR code: has the phone connected yet?"""
    from app.models.members import MemberAuthRequest

    member = _require(db, pc_session)
    req = db.get(MemberAuthRequest, handoff_id)
    if req is None or req.provider != "handoff" or req.member_id != member.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found.")
    expired = req.status == "pending" and req.expires_at < utcnow()
    return {"status": "expired" if expired else "connected" if req.status == "done" else req.status,
            "connected_at": (req.result or {}).get("connected_at"), "device": (req.result or {}).get("device")}


class HandoffClaim(StrictInput):
    token: str = Field(min_length=20, max_length=64)


@router.post("/members/portfolio/handoff/claim")
def claim_handoff(payload: HandoffClaim, request: Request, response: Response, db: DbSession, runtime: Runtime) -> dict[str, Any]:
    """The phone opens the QR link: the code is used up and this browser is signed in to the member who showed it."""
    from app.api.routes.members import _set_session
    from app.members import service as members
    from app.members.auth import _h
    from app.models.members import Member, MemberAuthRequest

    req = db.scalar(select(MemberAuthRequest).where(MemberAuthRequest.state_hash == _h(payload.token), MemberAuthRequest.provider == "handoff"))
    if req is None or req.status != "pending" or req.expires_at < utcnow():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This QR code has expired or was already used. Show a new one on your computer.")
    member = db.get(Member, req.member_id) if req.member_id else None
    if member is None or member.status != "active":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This QR code is no longer valid.")
    ua = request.headers.get("user-agent", "")
    device = "iPhone" if "iPhone" in ua else "iPad" if "iPad" in ua else "Android phone" if "Android" in ua else "phone"
    req.status, req.result = "done", {"connected_at": utcnow().isoformat(), "device": device}  # single use
    db.commit()
    _set_session(response, runtime.settings, members.new_session(db, member, runtime.settings.member_session_days))
    return {"username": member.username}


# --- photos ---------------------------------------------------------------------------------------------------------


@router.post("/members/portfolio/holdings/{holding_id}/photos", status_code=status.HTTP_201_CREATED)
async def upload_photo(holding_id: int, db: DbSession, runtime: Runtime, file: UploadFile = File(...),
                       side: Literal["front", "back", "closeup"] = Form("front"), pc_session: SessionToken = None) -> dict[str, Any]:
    member = _require(db, pc_session)
    try:
        h = service._own(db, member, holding_id)
    except MemberError as exc:
        raise _err(exc) from exc
    store = photos.Storage(runtime.settings)
    if not store.available:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Photo storage isn't set up yet.")
    if (db.scalar(select(func.count()).select_from(PokemonHoldingPhoto).where(PokemonHoldingPhoto.holding_id == h.id)) or 0) >= MAX_PHOTOS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Up to {MAX_PHOTOS} photos per card. Delete one first.")
    today = db.scalar(select(func.count()).select_from(PokemonHoldingPhoto).join(PokemonHolding, PokemonHolding.id == PokemonHoldingPhoto.holding_id)
                      .where(PokemonHolding.portfolio_id == h.portfolio_id, PokemonHoldingPhoto.created_at >= utcnow().replace(hour=0, minute=0, second=0))) or 0
    if today >= PHOTOS_PER_DAY:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "That's a lot of photos for one day. Please continue tomorrow.")
    data = await _read(file)
    try:
        full, thumb, w, ht, sha = photos.process(data)
    except photos.PhotoError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    key, tkey = photos.new_keys(h.portfolio_id)
    try:
        store.put(key, full)
        store.put(tkey, thumb)
    except photos.PhotoError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    if side in ("front", "back"):  # one front and one back: a new one replaces the old
        for old in db.scalars(select(PokemonHoldingPhoto).where(PokemonHoldingPhoto.holding_id == h.id, PokemonHoldingPhoto.side == side)):
            _drop_photo(store, old)
            db.delete(old)
    ph = PokemonHoldingPhoto(holding_id=h.id, side=side, key=key, thumb_key=tkey, width=w, height=ht, bytes=len(full), sha256=sha)
    db.add(ph)
    db.commit()
    return {"id": ph.id, "side": side, "width": w, "height": ht}


def _drop_photo(store: photos.Storage, ph: PokemonHoldingPhoto) -> None:
    for k in (ph.key, ph.thumb_key):
        try:
            store.delete(k)
        except Exception:  # noqa: BLE001 - an orphaned object is harmless; the database row is what grants access
            pass


@router.delete("/members/portfolio/photos/{photo_id}")
def delete_photo(photo_id: int, db: DbSession, runtime: Runtime, pc_session: SessionToken = None) -> dict[str, bool]:
    member = _require(db, pc_session)
    ph = db.get(PokemonHoldingPhoto, photo_id)
    try:
        if ph is None:
            raise MemberError("Not found.")
        service._own(db, member, ph.holding_id)
    except MemberError as exc:
        raise _err(exc) from exc
    _drop_photo(photos.Storage(runtime.settings), ph)
    db.delete(ph)
    db.commit()
    return {"deleted": True}


@router.get("/members/portfolio/photos/{photo_id}")
def get_photo(photo_id: int, db: DbSession, runtime: Runtime, size: Literal["thumb", "full"] = "thumb", pc_session: SessionToken = None) -> Response:
    """A photo, only for its owner or as part of a public card in a public portfolio. Private: plain 404."""
    ph = db.get(PokemonHoldingPhoto, photo_id)
    if ph is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found.")
    if not service.can_view(db, db.get(PokemonHolding, ph.holding_id), _current(db, pc_session)):  # the card's own access rule
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found.")
    data = photos.Storage(runtime.settings).get(ph.thumb_key if size == "thumb" else ph.key)
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff"})


# --- scanner & condition assistant ----------------------------------------------------------------------------------------


@router.post("/members/portfolio/scan")
async def scan_card(db: DbSession, runtime: Runtime, file: UploadFile = File(...), pc_session: SessionToken = None) -> dict[str, Any]:
    member = _require(db, pc_session)
    data = await _read(file)
    try:
        return await scan.identify(db, runtime.settings, member, data, getattr(runtime, "pokemon_transport", None))
    except photos.PhotoError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except MemberError as exc:
        raise _err(exc) from exc


@router.post("/members/portfolio/holdings/{holding_id}/condition")
async def estimate_condition(holding_id: int, db: DbSession, runtime: Runtime, pc_session: SessionToken = None) -> dict[str, Any]:
    member = _require(db, pc_session)
    try:
        h = service._own(db, member, holding_id)
        out = await condition.estimate(db, runtime.settings, member, h, getattr(runtime, "members_transport", None))
    except MemberError as exc:
        raise _err(exc) from exc
    return {**out, "holding": service.holding_detail(db, holding_id, member)}


# --- public portfolios, Explore, community -----------------------------------------------------------------------------


@router.get("/members/portfolios/u/{username}")
def public_portfolio(username: str, db: DbSession, sort: Literal["value", "recent", "name", "set"] = "value",
                     limit: int = Query(60, ge=1, le=120), offset: int = Query(0, ge=0), pc_session: SessionToken = None) -> dict[str, Any]:
    try:
        return service.public_portfolio(db, username, _current(db, pc_session), sort, limit, offset)
    except MemberError as exc:
        raise _err(exc) from exc


@router.get("/members/portfolios/explore")
def explore(db: DbSession, tab: Literal["top", "cards", "valuable", "recent", "featured"] = "top") -> dict[str, Any]:
    return service.explore(db, tab)


@router.get("/members/portfolios/u/{username}/comments")
def comments(username: str, db: DbSession, before: int | None = None, pc_session: SessionToken = None) -> dict[str, Any]:
    try:
        return community.list_comments(db, username, _current(db, pc_session), before=before)
    except MemberError as exc:
        raise _err(exc) from exc


class CommentIn(StrictInput):
    body: str = Field(min_length=1, max_length=1000)
    parent_id: int | None = None
    holding_id: int | None = None


@router.post("/members/portfolios/u/{username}/comments", status_code=status.HTTP_201_CREATED)
def add_comment(username: str, payload: CommentIn, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    try:
        return community.add_comment(db, username, _require(db, pc_session), payload.body, payload.parent_id, payload.holding_id)
    except MemberError as exc:
        raise _err(exc) from exc


@router.delete("/members/portfolio-comments/{comment_id}")
def delete_comment(comment_id: int, db: DbSession, pc_session: SessionToken = None) -> dict[str, bool]:
    try:
        community.delete_comment(db, comment_id, _require(db, pc_session))
    except MemberError as exc:
        raise _err(exc) from exc
    return {"deleted": True}


class VoteIn(StrictInput):
    estimate: int = Field(ge=1, le=10)
    note: str = Field("", max_length=280)


@router.post("/members/portfolio/holdings/{holding_id}/condition-vote")
def condition_vote(holding_id: int, payload: VoteIn, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    try:
        return community.vote_condition(db, holding_id, _require(db, pc_session), payload.estimate, payload.note)
    except MemberError as exc:
        raise _err(exc) from exc


class ReportIn(StrictInput):
    target: Literal["comment", "portfolio", "holding"]
    target_id: int
    reason: str = Field(min_length=3, max_length=280)


@router.post("/members/portfolio-reports", status_code=status.HTTP_201_CREATED)
def report(payload: ReportIn, db: DbSession, pc_session: SessionToken = None) -> dict[str, bool]:
    try:
        community.report(db, _require(db, pc_session), payload.target, payload.target_id, payload.reason)
    except MemberError as exc:
        raise _err(exc) from exc
    return {"reported": True}


# --- owner of Price Chat (admin, under /api/pokemon: admin-only by the guard) ----------------------------------------------


@router.get("/pokemon/portfolios/diagnostics")
def diagnostics(db: DbSession) -> dict[str, Any]:
    """Owner: why holdings are (not) priced, scanner confidence, provider failures, last refresh."""
    from collections import Counter
    from datetime import timedelta

    from app.models.pokemon import PokemonCard, PokemonSyncLog
    from app.models.routing import ScheduledJob
    from app.models.portfolio import PokemonScan
    from app.portfolio import valuation

    rows = db.execute(select(PokemonHolding, PokemonCard).join(PokemonCard, PokemonCard.id == PokemonHolding.card_id)
                      .where(PokemonHolding.removed_at.is_(None))).all()
    reasons = Counter((h.value_reason or ("priced" if h.value_cents is not None else "not_valued_yet")) for h, _ in rows)
    unpriced = [{"holding_id": h.id, "portfolio_id": h.portfolio_id, "card": f"{c.name} · {c.set_name} {c.card_number or ''} · {c.edition}"
                 + (f" · {c.variant}" if c.variant else ""), "grade": f"{h.grading_company} {h.grade}" if h.kind == "graded" else "Raw",
                 "reason": h.value_reason, "note": h.value_note, "valued_at": h.valued_at.isoformat() if h.valued_at else None,
                 "prices_checked_at": (c.external_ids or {}).get("prices_checked_at")}
                for h, c in rows if h.value_cents is None]
    since = utcnow() - timedelta(days=7)
    scans = list(db.scalars(select(PokemonScan).where(PokemonScan.kind == "identify", PokemonScan.at >= since)))
    tops = [((s.result.get("raw") or {}).get("data") or {}).get("matches", [{}])[0].get("score") for s in scans if s.status == "ok"]
    tops = [t for t in tops if isinstance(t, (int, float))]
    failures = db.scalar(select(func.count()).select_from(PokemonSyncLog).where(PokemonSyncLog.provider == "scrydex", PokemonSyncLog.status == "error",
                                                                                PokemonSyncLog.started_at >= utcnow() - timedelta(days=1))) or 0
    job = db.get(ScheduledJob, "pokemon.portfolios")
    return {
        "holdings": len(rows), "identified": sum(1 for _, c in rows if (c.external_ids or {}).get("scrydex")),
        "priced": sum(1 for h, _ in rows if h.value_cents is not None),
        "priced_exact": reasons.get("priced", 0), "priced_comparable": reasons.get("comparable", 0) + reasons.get("grade_equivalent", 0),
        "unpriced": len(unpriced), "by_reason": [{"reason": k, "label": valuation.REASONS.get(k, "Not re-valued since reasons were added (next refresh)"), "count": n} for k, n in reasons.most_common()],
        "unpriced_holdings": unpriced[:200], "provider_failures_24h": failures,
        "last_refresh": {"status": job.last_status if job else None, "at": job.last_run_at.isoformat() if job and job.last_run_at else None},
        "scanner_7d": {"scans": len(scans), "matched": sum(1 for s in scans if s.status == "ok"),
                       "median_top_score": sorted(tops)[len(tops) // 2] if tops else None,
                       "low_confidence": sum(1 for t in tops if t < scan.CONFIDENT)},
    }


@router.post("/pokemon/portfolios/diagnostics/retry")
async def retry_valuations(db: DbSession, runtime: Runtime, limit: int = Query(50, ge=1, le=200)) -> dict[str, Any]:
    """Owner: fetch current prices for unpriced (or estimated) holdings' cards again and re-value them. No rescanning."""
    want = list(db.scalars(select(PokemonHolding).where(PokemonHolding.removed_at.is_(None), PokemonHolding.value_reason.in_(list(prices.NEEDS_FETCH))
                                                        | (PokemonHolding.value_cents.is_(None)))))  # incl. holdings valued before reasons existed
    done: dict[str, int] = {}
    seen: set[int] = set()
    for h in want:
        if h.card_id not in seen and len(seen) < limit:
            seen.add(h.card_id)
            r = await prices.ensure(db, runtime.settings, h.card_id, None, getattr(runtime, "pokemon_transport", None), force=True)
            done[r] = done.get(r, 0) + 1
    for h in want:
        service.revalue_holding(db, h)
    return {"cards_checked": len(seen), "results": done, "holdings_revalued": len(want)}


@router.get("/pokemon/portfolios/moderation")
def moderation(db: DbSession) -> dict[str, Any]:
    from app.models.members import Member

    reports = db.scalars(select(PokemonPortfolioReport).where(PokemonPortfolioReport.status == "open").order_by(PokemonPortfolioReport.id.desc()).limit(200))
    review = db.execute(select(PokemonPortfolio, Member).join(Member, Member.id == PokemonPortfolio.member_id)
                        .where(PokemonPortfolio.review_status == "review")).all()
    return {"reports": [{"id": r.id, "target": r.target, "target_id": r.target_id, "reason": r.reason, "at": r.created_at.isoformat()} for r in reports],
            "review": [{"portfolio_id": p.id, "username": m.username, "public_value": p.public_value_cents, "note": p.review_note} for p, m in review]}


class ReviewIn(StrictInput):
    review_status: Literal["ok", "review", "hidden", "featured"]


@router.patch("/pokemon/portfolios/{portfolio_id}/review")
def set_review(portfolio_id: int, payload: ReviewIn, db: DbSession) -> dict[str, str]:
    p = db.get(PokemonPortfolio, portfolio_id)
    if p is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found.")
    p.review_status, p.review_note = payload.review_status, f"admin: {payload.review_status}"
    db.commit()
    return {"review_status": p.review_status}


class ReportResolve(StrictInput):
    status: Literal["resolved", "dismissed"]
    hide_comment: bool = False


@router.patch("/pokemon/portfolios/reports/{report_id}")
def resolve_report(report_id: int, payload: ReportResolve, db: DbSession) -> dict[str, str]:
    r = db.get(PokemonPortfolioReport, report_id)
    if r is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found.")
    if payload.hide_comment and r.target == "comment":
        c = db.get(PokemonPortfolioComment, r.target_id)
        if c is not None:
            c.status = "hidden"
    r.status = payload.status
    db.commit()
    return {"status": r.status}
