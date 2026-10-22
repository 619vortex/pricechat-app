"""Pokémon Portfolio: holdings, valuation refresh, history, dashboard, public profiles and Explore.

History is honest:
  - every add / remove / quantity / grade change is an event with the estimated value at that moment;
  - one snapshot per portfolio per day records its value (recorded on that day, never back-filled);
  - a period's change is split into the MARKET move and the value of cards ADDED / REMOVED (flows), so adding a $500
    card is never shown as a +$500 "gain";
  - a period with no snapshot at its start shows no change, with the reason.
"""

from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.members import xp as ledger
from app.members.service import MemberError
from app.models.members import Member
from app.models.pokemon import PokemonCard
from app.models.portfolio import (
    PokemonHolding, PokemonHoldingEvent, PokemonHoldingPhoto, PokemonPortfolio, PokemonPortfolioSnapshot,
)
from app.pokemon.grades import GradeError, normalise
from app.portfolio import valuation

MAX_HOLDINGS = 2000  # per portfolio (abuse protection; a real collection this size can ask)
MAX_QUANTITY = 999
GRADED = ("PSA", "BGS", "CGC")
PERIODS = {"30d": 30, "90d": 90, "1y": 365}
REVIEW_VALUE = 250_000_00  # a public self-reported collection above this waits for a review before ranking
REVIEW_JUMP = 100_000_00  # ... or one that grows by more than this in a day


# --- portfolio ---------------------------------------------------------------------------------------------------


def get_portfolio(db: Session, member: Member, create: bool = True) -> PokemonPortfolio | None:
    p = db.scalar(select(PokemonPortfolio).where(PokemonPortfolio.member_id == member.id))
    if p is None and create:
        p = PokemonPortfolio(member_id=member.id, visibility="private", featured=[], last_activity_at=utcnow(), value_cents=0, public_value_cents=0)
        db.add(p)
        db.flush()
    return p


def update_settings(db: Session, member: Member, *, visibility: str | None = None, show_value: bool | None = None,
                    show_purchase: bool | None = None, comments_enabled: bool | None = None, bio: str | None = None,
                    featured: list[int] | None = None) -> PokemonPortfolio:
    p = get_portfolio(db, member)
    assert p is not None
    if visibility is not None:
        if visibility not in ("private", "public"):
            raise MemberError("Visibility is private or public.")
        if visibility == "public" and p.visibility != "public":
            ledger.award(db, member, "portfolio.public", f"portfolio:{p.id}", commit=False)
        p.visibility = visibility
    if show_value is not None:
        p.show_value = show_value
    if show_purchase is not None:
        p.show_purchase = show_purchase
    if comments_enabled is not None:
        p.comments_enabled = comments_enabled
    if bio is not None:
        p.bio = bio.strip()[:280]
    if featured is not None:
        mine = set(db.scalars(select(PokemonHolding.id).where(PokemonHolding.portfolio_id == p.id, PokemonHolding.removed_at.is_(None))))
        p.featured = [h for h in featured if h in mine][:6]
    db.commit()
    return p


# --- holdings ----------------------------------------------------------------------------------------------------


def _clean_grade(kind: str, company: str | None, grade: str | None) -> tuple[str | None, str | None]:
    if kind == "raw":
        return None, None
    if kind != "graded":
        raise MemberError("Choose Raw or Graded.")
    try:
        g = normalise(company or "", grade or "")
    except GradeError as exc:
        raise MemberError(f"{exc}") from None
    if g.company not in GRADED:
        raise MemberError("Graded cards: PSA, BGS or CGC.")
    return g.company, g.grade


def _condition(low: float | None, high: float | None) -> tuple[float | None, float | None]:
    if low is None and high is None:
        return None, None
    lo, hi = (low if low is not None else high), (high if high is not None else low)
    assert lo is not None and hi is not None
    if not (1 <= lo <= 10 and 1 <= hi <= 10):
        raise MemberError("Condition estimates are 1-10 grade-equivalents.")
    return min(lo, hi), max(lo, hi)


def _event(db: Session, h: PokemonHolding, kind: str, qty_delta: int, now: datetime) -> None:
    db.add(PokemonHoldingEvent(portfolio_id=h.portfolio_id, holding_id=h.id, at=now, kind=kind, quantity_delta=qty_delta,
                               value_delta_cents=(h.value_cents * qty_delta) if h.value_cents is not None else None))


def add_holding(db: Session, member: Member, card_id: int, kind: str, *, company: str | None = None, grade: str | None = None,
                quantity: int = 1, cert_number: str | None = None, condition_low: float | None = None, condition_high: float | None = None,
                purchase_price_cents: int | None = None, purchase_date: date | None = None, notes: str = "", is_public: bool = True,
                source: str = "manual") -> PokemonHolding:
    p = get_portfolio(db, member)
    assert p is not None
    card = db.get(PokemonCard, card_id)
    if card is None:
        raise MemberError("Unknown card.")
    if not 1 <= quantity <= MAX_QUANTITY:
        raise MemberError(f"Quantity is 1-{MAX_QUANTITY}.")
    active = db.scalar(select(func.count()).select_from(PokemonHolding).where(PokemonHolding.portfolio_id == p.id, PokemonHolding.removed_at.is_(None))) or 0
    if active >= MAX_HOLDINGS:
        raise MemberError(f"A portfolio holds at most {MAX_HOLDINGS} entries.")
    comp, gr = _clean_grade(kind, company, grade)
    lo, hi = _condition(condition_low, condition_high) if kind == "raw" else (None, None)
    now = utcnow()
    h = PokemonHolding(portfolio_id=p.id, card_id=card.id, kind=kind, grading_company=comp, grade=gr,
                       cert_number=((cert_number or "").strip()[:32] or None) if kind == "graded" else None,
                       condition_low=lo, condition_high=hi, condition_source="owner" if lo is not None else None,
                       quantity=quantity, purchase_price_cents=purchase_price_cents, purchase_date=purchase_date,
                       notes=(notes or "").strip()[:500], is_public=is_public, source="scan" if source == "scan" else "manual")
    db.add(h)
    db.flush()
    valuation.apply(h, valuation.Pricer(db, [h], now).value(h), now)
    _event(db, h, "add", quantity, now)
    p.last_activity_at = now
    ledger.award(db, member, "portfolio.first_card", f"portfolio:{p.id}", commit=False)
    if h.source == "scan":
        ledger.award(db, member, "portfolio.first_scan", f"portfolio:{p.id}", commit=False)
    _refresh_totals(db, p)
    db.commit()
    return h


def _own(db: Session, member: Member, holding_id: int) -> PokemonHolding:
    p = get_portfolio(db, member, create=False)
    h = db.get(PokemonHolding, holding_id)
    if p is None or h is None or h.portfolio_id != p.id or h.removed_at is not None:
        raise MemberError("That card isn't in your portfolio.")
    return h


def _own_real(db: Session, member: Member, holding_id: int) -> PokemonHolding:
    h = _own(db, member, holding_id)
    if h.source == GIVEAWAY:
        raise MemberError("This is a virtual card from the Pack Giveaway: it follows your binder (sell or list it there).")
    return h


# --- Pack Giveaway pulls in the portfolio (opt-in) -----------------------------------------------------------------------

GIVEAWAY = "giveaway"  # holding.source of a virtual giveaway card


def sync_giveaway(db: Session, member: Member, now: datetime | None = None) -> None:
    """Mirror the member's Pack Giveaway binder into their portfolio while they opt in (and remove it when they don't).
    VIRTUAL cards: raw, marked source=giveaway / verification=virtual, and private (is_public=False), so they count in the
    owner's own value and chart but never in a public value, the collectors' leaderboard, explore, or the marketplace
    (graded slabs only). Each change is an add / quantity / remove event, so the chart shows them as cards added, not as
    market moves. The caller commits."""
    from app.models.members import MemberBinderCard

    now = now or utcnow()
    p = get_portfolio(db, member, create=bool(member.giveaway_in_portfolio))
    if p is None:
        return
    db.flush()
    want: dict[int, int] = {}
    if member.giveaway_in_portfolio:
        want = dict(db.execute(select(MemberBinderCard.card_id, MemberBinderCard.count).where(
            MemberBinderCard.member_id == member.id, MemberBinderCard.count > 0)).all())
    have = {h.card_id: h for h in db.scalars(select(PokemonHolding).where(
        PokemonHolding.portfolio_id == p.id, PokemonHolding.source == GIVEAWAY, PokemonHolding.removed_at.is_(None)))}
    changed = False
    for cid, h in have.items():
        if cid not in want:
            _event(db, h, "remove", -h.quantity, now)
            h.removed_at = now
            changed = True
    room = MAX_HOLDINGS - (db.scalar(select(func.count()).select_from(PokemonHolding).where(
        PokemonHolding.portfolio_id == p.id, PokemonHolding.removed_at.is_(None))) or 0)
    new = []
    for cid, n in want.items():
        n = min(n, MAX_QUANTITY)
        h = have.get(cid)
        if h is None:
            if room <= 0:
                continue  # the portfolio is full: the rest stay in the binder only
            h = PokemonHolding(portfolio_id=p.id, card_id=cid, kind="raw", quantity=n, is_public=False, source=GIVEAWAY,
                               verification="virtual", notes="Virtual card from the Pack Giveaway (not owned, not real).")
            db.add(h)
            new.append(h)
            room -= 1
        elif h.quantity != n:
            _event(db, h, "quantity", n - h.quantity, now)
            h.quantity = n
            changed = True
    if new:
        db.flush()
        pricer = valuation.Pricer(db, new, now)
        for h in new:
            valuation.apply(h, pricer.value(h), now)
            _event(db, h, "add", h.quantity, now)
        changed = True
    if changed:
        p.last_activity_at = now
        _refresh_totals(db, p)


def update_holding(db: Session, member: Member, holding_id: int, **changes: Any) -> PokemonHolding:
    h = _own_real(db, member, holding_id)
    now = utcnow()
    before_each = h.value_cents
    revalue = False
    if "card_id" in changes and changes["card_id"] and changes["card_id"] != h.card_id:
        # Edit Identification: the same physical card, re-identified. Photos, quantity, purchase details and its history stay;
        # the value change is recorded as a change of WHAT is held (a flow), never as a market move
        card = db.get(PokemonCard, int(changes["card_id"]))
        if card is None:
            raise MemberError("Unknown card.")
        h.card_id = card.id
        h.prices_checked_at = None
        revalue = True
    if "kind" in changes or "company" in changes or "grade" in changes:
        kind = changes.get("kind", h.kind)
        comp, gr = _clean_grade(kind, changes.get("company", h.grading_company), changes.get("grade", h.grade))
        if (kind, comp, gr) != (h.kind, h.grading_company, h.grade):
            h.kind, h.grading_company, h.grade = kind, comp, gr
            if kind == "raw":
                h.cert_number, h.cert_status = None, "unchecked"
            else:
                h.condition_low = h.condition_high = None
                h.condition_source, h.condition_report, h.community_review = None, None, False
            revalue = True
    if "condition_low" in changes or "condition_high" in changes:
        if h.kind != "raw":
            raise MemberError("Condition estimates are for raw cards; graded cards have their official grade.")
        h.condition_low, h.condition_high = _condition(changes.get("condition_low"), changes.get("condition_high"))
        h.condition_source = "owner" if h.condition_low is not None else None
        revalue = True
    if "quantity" in changes:
        q = int(changes["quantity"])
        if not 1 <= q <= MAX_QUANTITY:
            raise MemberError(f"Quantity is 1-{MAX_QUANTITY}.")
        if q != h.quantity:
            _event(db, h, "quantity", q - h.quantity, now)
            h.quantity = q
    for k in ("purchase_price_cents", "purchase_date", "is_public", "community_review"):
        if k in changes:
            setattr(h, k, changes[k])
    if "notes" in changes:
        h.notes = (changes["notes"] or "").strip()[:500]
    if "cert_number" in changes and h.kind == "graded":
        h.cert_number, h.cert_status = (changes["cert_number"] or "").strip()[:32] or None, "unchecked"
    if h.community_review and h.kind != "raw":
        h.community_review = False
    if revalue:
        valuation.apply(h, valuation.Pricer(db, [h], now).value(h), now)
        # both at the NEW quantity: a quantity change in the same edit was already recorded (at the old price) above
        before_value = before_each * h.quantity if before_each is not None else None
        after_value = h.value_cents * h.quantity if h.value_cents is not None else None
        # a regrade / condition change is a change of WHAT is held: recorded as a flow, never as a market move
        if before_value is not None or after_value is not None:
            db.add(PokemonHoldingEvent(portfolio_id=h.portfolio_id, holding_id=h.id, at=now, kind="identity" if "card_id" in changes else "grade",
                                       quantity_delta=0, value_delta_cents=(after_value or 0) - (before_value or 0)))
    p = db.get(PokemonPortfolio, h.portfolio_id)
    assert p is not None
    p.last_activity_at = now
    _refresh_totals(db, p)
    db.commit()
    return h


def remove_holding(db: Session, member: Member, holding_id: int) -> None:
    h = _own_real(db, member, holding_id)
    now = utcnow()
    _event(db, h, "remove", -h.quantity, now)
    h.removed_at = now
    p = db.get(PokemonPortfolio, h.portfolio_id)
    assert p is not None
    p.featured = [x for x in (p.featured or []) if x != h.id]
    p.last_activity_at = now
    _refresh_totals(db, p)
    db.commit()


def duplicate_holding(db: Session, member: Member, holding_id: int) -> PokemonHolding:
    h = _own_real(db, member, holding_id)
    return add_holding(db, member, h.card_id, h.kind, company=h.grading_company, grade=h.grade, quantity=1,
                       condition_low=h.condition_low, condition_high=h.condition_high, is_public=h.is_public)


# --- valuation refresh, totals, snapshots ------------------------------------------------------------------------------


def _active(db: Session, portfolio_id: int) -> list[PokemonHolding]:
    return list(db.scalars(select(PokemonHolding).where(PokemonHolding.portfolio_id == portfolio_id, PokemonHolding.removed_at.is_(None))))


def _refresh_totals(db: Session, p: PokemonPortfolio) -> None:
    db.flush()  # the session doesn't autoflush: totals must see this request's changes
    hs = _active(db, p.id)
    priced = [h for h in hs if h.value_cents is not None]
    # the PRICED subtotal; unpriced holdings are counted separately (unpriced_holdings), never silently as $0 value
    p.value_cents = sum(h.value_cents * h.quantity for h in priced)  # type: ignore[operator]
    p.public_value_cents = sum(h.value_cents * h.quantity for h in priced if h.is_public)  # type: ignore[operator]
    p.priced_holdings, p.unpriced_holdings = len(priced), len(hs) - len(priced)
    p.valued_at = utcnow()
    _review_check(db, p)


def _review_check(db: Session, p: PokemonPortfolio) -> None:
    """Leaderboard protection: a very valuable or suddenly much more valuable public collection (all self-reported) waits
    for a person to look before it can rank. It stays visible on its own profile."""
    if p.review_status in ("hidden",) or p.visibility != "public":
        return
    value = p.public_value_cents or 0
    last = db.scalar(select(PokemonPortfolioSnapshot).where(PokemonPortfolioSnapshot.portfolio_id == p.id)
                     .order_by(PokemonPortfolioSnapshot.day.desc()).limit(1))
    jump = value - (last.value_cents if last else 0)
    if p.review_status in ("ok", "featured") and (value >= REVIEW_VALUE or jump >= REVIEW_JUMP):
        p.review_status = "review"
        p.review_note = f"automatic: public value ${value / 100:,.0f}" + (f", +${jump / 100:,.0f} since the last snapshot" if jump >= REVIEW_JUMP else "")


def revalue_holding(db: Session, h: PokemonHolding, now: datetime | None = None) -> None:
    """Re-price one holding after its card's prices were fetched (same holding, same card: not a flow)."""
    now = now or utcnow()
    valuation.apply(h, valuation.Pricer(db, [h], now).value(h), now)
    h.prices_checked_at = now
    p = db.get(PokemonPortfolio, h.portfolio_id)
    if p is not None:
        _refresh_totals(db, p)
    db.commit()


def revalue(db: Session, p: PokemonPortfolio, now: datetime | None = None) -> None:
    """Re-price every active holding (bounded by this portfolio's cards) and refresh the cached totals."""
    now = now or utcnow()
    hs = _active(db, p.id)
    pricer = valuation.Pricer(db, hs, now)
    for h in hs:
        valuation.apply(h, pricer.value(h), now)
    _refresh_totals(db, p)


def snapshot(db: Session, p: PokemonPortfolio, now: datetime | None = None) -> PokemonPortfolioSnapshot | None:
    """Today's value + the flows since the previous snapshot. One per day (later runs that day update it)."""
    now = now or utcnow()
    hs = _active(db, p.id)
    if not hs and not db.scalar(select(PokemonPortfolioSnapshot.id).where(PokemonPortfolioSnapshot.portfolio_id == p.id).limit(1)):
        return None
    prev = db.scalar(select(PokemonPortfolioSnapshot).where(PokemonPortfolioSnapshot.portfolio_id == p.id, PokemonPortfolioSnapshot.day < now.date())
                     .order_by(PokemonPortfolioSnapshot.day.desc()).limit(1))
    since = prev.taken_at if prev else datetime.min.replace(tzinfo=now.tzinfo)
    flows = db.scalar(select(func.coalesce(func.sum(PokemonHoldingEvent.value_delta_cents), 0)).where(
        PokemonHoldingEvent.portfolio_id == p.id, PokemonHoldingEvent.at > since, PokemonHoldingEvent.at <= now)) or 0
    row = db.scalar(select(PokemonPortfolioSnapshot).where(PokemonPortfolioSnapshot.portfolio_id == p.id, PokemonPortfolioSnapshot.day == now.date()))
    if row is None:
        row = PokemonPortfolioSnapshot(portfolio_id=p.id, day=now.date(), taken_at=now, value_cents=0, flows_cents=0, copies=0, priced_copies=0)
        db.add(row)
    row.taken_at, row.value_cents, row.flows_cents = now, p.value_cents or 0, int(flows)
    row.copies = sum(h.quantity for h in hs)
    row.priced_copies = sum(h.quantity for h in hs if h.value_cents is not None)
    return row


def refresh_all(db: Session, now: datetime | None = None, batch: int = 50) -> dict[str, Any]:
    """The scheduled job: re-price and snapshot every portfolio that has (or had) holdings, a batch at a time."""
    now = now or utcnow()
    ids = list(db.scalars(select(PokemonPortfolio.id).order_by(PokemonPortfolio.id)))
    done = snaps = 0
    for i in range(0, len(ids), batch):
        for p in db.scalars(select(PokemonPortfolio).where(PokemonPortfolio.id.in_(ids[i:i + batch]))):
            revalue(db, p, now)
            if snapshot(db, p, now) is not None:
                snaps += 1
            done += 1
        db.commit()
        db.expunge_all()  # bounded memory, whatever the number of portfolios
    return {"portfolios": done, "snapshots": snaps}


# --- read models -------------------------------------------------------------------------------------------------------


def _picture(c: PokemonCard) -> str | None:
    from app.members.packs import picture  # the card's picture for its own print (Unlimited: no 1st Edition stamp)

    return picture(c)


def card_brief(c: PokemonCard) -> dict[str, Any]:
    return {"id": c.id, "name": c.name, "pokemon_name": c.pokemon_name, "set_name": c.set_name, "set_code": c.set_code,
            "card_number": c.card_number, "year": c.year, "language": c.language, "edition": c.edition, "variant": c.variant,
            "rarity": c.rarity, "image_url": _picture(c), "era": c.era}


def grade_label(h: PokemonHolding) -> str:
    if h.kind == "graded":
        return f"{h.grading_company} {h.grade}"
    if h.condition_low is not None:
        lo, hi = h.condition_low, h.condition_high
        return f"Raw · ~{lo:g}" + (f"-{hi:g}" if hi != lo else "") + " eq."
    return "Raw"


def holding_out(h: PokemonHolding, card: PokemonCard, *, owner: bool, show_value: bool = True, show_purchase: bool = False,
                photos: list[PokemonHoldingPhoto] | None = None, changes: dict[str, Any] | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": h.id, "card": card_brief(card), "kind": h.kind, "grading_company": h.grading_company, "grade": h.grade,
        "label": grade_label(h), "quantity": h.quantity, "added_at": h.created_at.isoformat(), "source": h.source,
        "verification": h.verification, "cert_status": h.cert_status if h.kind == "graded" else None,
        "condition": ({"low": h.condition_low, "high": h.condition_high, "source": h.condition_source} if h.condition_low is not None else None),
        "community_review": h.community_review, "is_public": h.is_public,
        "photos": [{"id": ph.id, "side": ph.side, "width": ph.width, "height": ph.height} for ph in (photos or [])],
    }
    if show_value:
        out["value"] = {"each": h.value_cents, "low": h.value_low_cents, "high": h.value_high_cents,
                        "total": h.value_cents * h.quantity if h.value_cents is not None else None, "source": h.value_source,
                        "confidence": h.value_confidence, "note": h.value_note, "valued_at": h.valued_at.isoformat() if h.valued_at else None,
                        "reason": h.value_reason, "reason_label": valuation.REASONS.get(h.value_reason or "", None)}
        out["changes"] = changes or {}
    if owner:
        out.update({"cert_number": h.cert_number, "notes": h.notes, "purchase_price": h.purchase_price_cents,
                    "purchase_date": h.purchase_date.isoformat() if h.purchase_date else None,
                    "condition_report": h.condition_report})
    elif show_purchase and h.purchase_price_cents is not None:
        out["purchase_price"] = h.purchase_price_cents
    return out


def _market_changes(db: Session, hs: list[PokemonHolding]) -> dict[int, dict[str, Any]]:
    """Each holding's 30D / 90D market move for its exact grade (raw: none), from the market's own rows when cached.
    Only card-level price movements: never derived from the portfolio's own history."""
    from app.pokemon import market

    out: dict[int, dict[str, Any]] = {}
    graded = [h for h in hs if h.kind == "graded"]
    by_grade: dict[tuple[str, str], list[PokemonHolding]] = defaultdict(list)
    for h in graded:
        by_grade[(h.grading_company or "", h.grade or "")].append(h)
    now = utcnow()
    for (comp, gr), group in by_grade.items():
        try:
            g = normalise(comp, gr)
        except GradeError:
            continue
        ids = sorted({h.card_id for h in group})
        by_card: dict[int, dict[str, Any]] = {}
        for i in range(0, len(ids), market.SMALL_IDS):  # small id lists: computed for those cards alone (never the catalogue)
            by_card.update({r["card"]["id"]: r for r in market.rows_for(db, g, now, ids=ids[i:i + market.SMALL_IDS])})
        for h in group:
            r = by_card.get(h.card_id)
            if r:
                out[h.id] = {k: r.get(f"change_{k}") for k in ("30d", "90d")}
    return out


def _photos(db: Session, ids: list[int]) -> dict[int, list[PokemonHoldingPhoto]]:
    out: dict[int, list[PokemonHoldingPhoto]] = defaultdict(list)
    for i in range(0, len(ids), 500):
        for ph in db.scalars(select(PokemonHoldingPhoto).where(PokemonHoldingPhoto.holding_id.in_(ids[i:i + 500])).order_by(PokemonHoldingPhoto.id)):
            out[ph.holding_id].append(ph)
    return out


def period_changes(db: Session, p: PokemonPortfolio, now: datetime | None = None) -> dict[str, dict[str, Any]]:
    """30D / 90D / 1Y: total change, of which market move and cards added / removed. None (with a reason) until a snapshot
    from the start of the period exists."""
    now = now or utcnow()
    snaps = list(db.scalars(select(PokemonPortfolioSnapshot).where(PokemonPortfolioSnapshot.portfolio_id == p.id)
                            .order_by(PokemonPortfolioSnapshot.day)))
    current = p.value_cents
    out: dict[str, dict[str, Any]] = {}
    for k, days in PERIODS.items():
        start = (now - timedelta(days=days)).date()
        base = None
        for s in snaps:
            if s.day <= start:
                base = s
            else:
                break
        if base is None or current is None:
            first = snaps[0].day.isoformat() if snaps else None
            out[k] = {"value": None, "market": None, "flows": None, "pct_market": None,
                      "reason": f"history recorded since {first}" if first else "history starts with today's first snapshot"}
            continue
        flows = sum(s.flows_cents for s in snaps if s.day > base.day)
        # flows since the last snapshot (today's adds before tonight's snapshot)
        last = snaps[-1]
        flows += db.scalar(select(func.coalesce(func.sum(PokemonHoldingEvent.value_delta_cents), 0)).where(
            PokemonHoldingEvent.portfolio_id == p.id, PokemonHoldingEvent.at > last.taken_at)) or 0
        total = current - base.value_cents
        mkt = total - flows
        out[k] = {"value": total, "market": mkt, "flows": flows, "base": base.value_cents, "base_day": base.day.isoformat(),
                  "pct_market": round(mkt / base.value_cents * 100, 2) if base.value_cents else None, "reason": None}
    return out


def history_series(db: Session, p: PokemonPortfolio) -> list[dict[str, Any]]:
    """The recorded daily values (genuine snapshots only) plus the current value as today's point."""
    out = [{"t": s.day.isoformat(), "value": s.value_cents, "flows": s.flows_cents, "copies": s.copies}
           for s in db.scalars(select(PokemonPortfolioSnapshot).where(PokemonPortfolioSnapshot.portfolio_id == p.id).order_by(PokemonPortfolioSnapshot.day))]
    today = utcnow().date().isoformat()
    if p.value_cents is not None and (not out or out[-1]["t"] != today):
        out.append({"t": today, "value": p.value_cents, "flows": None, "copies": None, "current": True})
    return out


def _composition(items: list[tuple[PokemonHolding, PokemonCard]], key: Any) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for h, c in items:
        k = key(h, c) or "Unknown"
        g = groups.setdefault(k, {"key": k, "copies": 0, "value": 0, "unpriced": 0})
        g["copies"] += h.quantity
        if h.value_cents is not None:
            g["value"] += h.value_cents * h.quantity
        else:
            g["unpriced"] += h.quantity
    return sorted(groups.values(), key=lambda g: (-g["value"], -g["copies"], g["key"]))[:12]


def dashboard(db: Session, member: Member) -> dict[str, Any]:
    p = get_portfolio(db, member)
    assert p is not None
    db.commit()
    hs = _active(db, p.id)
    cards = {c.id: c for c in db.scalars(select(PokemonCard).where(PokemonCard.id.in_({h.card_id for h in hs} or {0})))}
    items = [(h, cards[h.card_id]) for h in hs if h.card_id in cards]
    photos = _photos(db, [h.id for h, _ in items])
    changes = _market_changes(db, [h for h, _ in items])
    priced = sorted((x for x in items if x[0].value_cents is not None), key=lambda x: -(x[0].value_cents or 0) * x[0].quantity)
    out = {
        "portfolio": settings_out(p, member),
        "summary": {
            "value": p.value_cents, "valued_at": p.valued_at.isoformat() if p.valued_at else None,
            "unique_cards": len({h.card_id for h, _ in items}), "copies": sum(h.quantity for h, _ in items),
            "graded": sum(h.quantity for h, _ in items if h.kind == "graded"), "raw": sum(h.quantity for h, _ in items if h.kind == "raw"),
            "priced_holdings": p.priced_holdings, "unpriced_holdings": p.unpriced_holdings,
            "virtual_value": sum((h.value_cents or 0) * h.quantity for h, _ in items if h.source == GIVEAWAY),
            "virtual_copies": sum(h.quantity for h, _ in items if h.source == GIVEAWAY),
            "most_valuable": holding_out(priced[0][0], priced[0][1], owner=True) if priced else None,
            "changes": period_changes(db, p),
        },
        "top": [{**holding_out(h, c, owner=True, photos=photos.get(h.id), changes=changes.get(h.id)),
                 "share": round((h.value_cents or 0) * h.quantity / p.value_cents * 100, 1) if p.value_cents else None} for h, c in priced[:5]],
        "composition": {
            "set": _composition(items, lambda h, c: c.set_name), "language": _composition(items, lambda h, c: c.language),
            "grade": _composition(items, lambda h, c: f"{h.grading_company} {h.grade}" if h.kind == "graded" else "Raw"),
            "rarity": _composition(items, lambda h, c: c.rarity),
            "kind": _composition(items, lambda h, c: "Graded" if h.kind == "graded" else "Raw"),
        },
        "recent": [holding_out(h, c, owner=True, photos=photos.get(h.id)) for h, c in sorted(items, key=lambda x: x[0].created_at, reverse=True)[:6]],
        "holdings": [holding_out(h, c, owner=True, photos=photos.get(h.id), changes=changes.get(h.id)) for h, c in items],
        "history": history_series(db, p),
    }
    return out


def settings_out(p: PokemonPortfolio, member: Member) -> dict[str, Any]:
    return {"id": p.id, "username": member.username, "visibility": p.visibility, "show_value": p.show_value, "show_purchase": p.show_purchase,
            "comments_enabled": p.comments_enabled, "bio": p.bio, "featured": p.featured or [], "review_status": p.review_status,
            "public_url": f"/pokemon/portfolio/u/{member.username}"}


def can_view(db: Session, h: PokemonHolding | None, viewer: Member | None) -> bool:
    """The one access rule for a card (and its photos): its owner, or a public card in a public, visible portfolio of an
    active member. Everything else is answered as "not found"."""
    if h is None or h.removed_at is not None:
        return False
    p = db.get(PokemonPortfolio, h.portfolio_id)
    if p is None:
        return False
    if viewer is not None and viewer.id == p.member_id:
        return True
    owner = db.get(Member, p.member_id)
    return p.visibility == "public" and h.is_public and p.review_status != "hidden" and owner is not None and owner.status == "active"


def holding_detail(db: Session, holding_id: int, viewer: Member | None) -> dict[str, Any]:
    h = db.get(PokemonHolding, holding_id)
    if h is None or h.removed_at is not None:
        raise MemberError("Not found.")
    p = db.get(PokemonPortfolio, h.portfolio_id)
    owner = db.get(Member, p.member_id) if p else None
    mine = viewer is not None and owner is not None and viewer.id == owner.id
    if not mine and (p is None or p.visibility != "public" or not h.is_public or p.review_status == "hidden" or owner is None or owner.status != "active"):
        raise MemberError("Not found.")  # private: indistinguishable from missing
    card = db.get(PokemonCard, h.card_id)
    assert card is not None and p is not None and owner is not None
    out = holding_out(h, card, owner=mine, show_value=mine or p.show_value, show_purchase=p.show_purchase,
                      photos=_photos(db, [h.id]).get(h.id), changes=_market_changes(db, [h]).get(h.id))
    out["owner"] = owner.username
    from app.portfolio import community

    out["community"] = community.condition_summary(db, h, viewer)
    return out


# --- public --------------------------------------------------------------------------------------------------------------


def public_portfolio(db: Session, username: str, viewer: Member | None, sort: str = "value", limit: int = 60, offset: int = 0) -> dict[str, Any]:
    member = db.scalar(select(Member).where(Member.username_key == username.lower(), Member.status == "active"))
    p = get_portfolio(db, member, create=False) if member else None
    mine = viewer is not None and member is not None and viewer.id == member.id
    if member is None or p is None or (not mine and (p.visibility != "public" or p.review_status == "hidden")):
        raise MemberError("This portfolio is private, or doesn't exist.")
    show_value = mine or p.show_value
    hs = [h for h in _active(db, p.id) if mine or h.is_public]
    cards = {c.id: c for c in db.scalars(select(PokemonCard).where(PokemonCard.id.in_({h.card_id for h in hs} or {0})))}
    items = [(h, cards[h.card_id]) for h in hs if h.card_id in cards]
    key = {"value": lambda x: -((x[0].value_cents or 0) * x[0].quantity), "recent": lambda x: -x[0].created_at.timestamp(),
           "name": lambda x: x[1].name.lower(), "set": lambda x: (x[1].year, x[1].set_name)}.get(sort if show_value or sort != "value" else "recent")
    items.sort(key=key)  # type: ignore[arg-type]
    page = items[offset: offset + max(1, min(limit, 120))]
    photos = _photos(db, [h.id for h, _ in page])
    rank = ledger.rank_for(db, member.rank_score)
    featured = [x for x in items if x[0].id in (p.featured or [])]
    top = sorted((x for x in items if x[0].value_cents is not None), key=lambda x: -(x[0].value_cents or 0))[:6] if show_value else []
    return {
        "username": member.username, "bio": p.bio, "rank": {"name": rank.name, "key": rank.key, "level": rank.level, "art": rank.art or {}},
        "owner": mine, "visibility": p.visibility, "show_value": show_value, "comments_enabled": p.comments_enabled,
        "value": (p.value_cents if mine else p.public_value_cents) if show_value else None,
        "value_note": "Estimated from market data; self-reported holdings, not verified by Price Chat.",
        "cards": sum(h.quantity for h, _ in items), "unique_cards": len({h.card_id for h, _ in items}),
        "graded": sum(h.quantity for h, _ in items if h.kind == "graded"), "raw": sum(h.quantity for h, _ in items if h.kind == "raw"),
        "featured": [holding_out(h, c, owner=mine, show_value=show_value, show_purchase=p.show_purchase) for h, c in featured],
        "top": [holding_out(h, c, owner=mine, show_value=show_value, show_purchase=p.show_purchase) for h, c in top],
        "composition": {"set": _composition(items, lambda h, c: c.set_name), "grade": _composition(items, lambda h, c: f"{h.grading_company} {h.grade}" if h.kind == "graded" else "Raw")}
        if show_value else None,
        "holdings": [holding_out(h, c, owner=mine, show_value=show_value, show_purchase=p.show_purchase, photos=photos.get(h.id)) for h, c in page],
        "total": len(items), "offset": offset, "review_status": p.review_status if mine else None,
    }


EXPLORE_TABS = ("top", "cards", "valuable", "recent", "featured")


def explore(db: Session, tab: str = "top", limit: int = 50) -> dict[str, Any]:
    """Public portfolios only. Value rankings use portfolios that show their value, are not waiting for a review, and were
    valued in the last few days; every value is labelled as an estimate of self-reported holdings."""
    tab = tab if tab in EXPLORE_TABS else "top"
    fresh = utcnow() - timedelta(days=3)
    q = (select(PokemonPortfolio, Member).join(Member, Member.id == PokemonPortfolio.member_id)
         .where(PokemonPortfolio.visibility == "public", Member.status == "active", PokemonPortfolio.review_status.in_(("ok", "featured"))))
    rows: list[tuple[PokemonPortfolio, Member]] = [tuple(r) for r in db.execute(q).all()]  # type: ignore[misc]
    counts = dict(db.execute(select(PokemonHolding.portfolio_id, func.sum(PokemonHolding.quantity)).where(
        PokemonHolding.removed_at.is_(None), PokemonHolding.is_public.is_(True)).group_by(PokemonHolding.portfolio_id)).all())
    rows = [r for r in rows if counts.get(r[0].id)]
    out = []
    if tab == "valuable":
        best = db.execute(select(PokemonHolding, PokemonCard).join(PokemonCard, PokemonCard.id == PokemonHolding.card_id).where(
            PokemonHolding.removed_at.is_(None), PokemonHolding.is_public.is_(True), PokemonHolding.value_cents.is_not(None),
            PokemonHolding.portfolio_id.in_([p.id for p, _ in rows if p.show_value] or [0])).order_by(PokemonHolding.value_cents.desc()).limit(limit)).all()
        owners = {p.id: m for p, m in rows}
        return {"tab": tab, "rows": [{**holding_out(h, c, owner=False), "owner": owners[h.portfolio_id].username} for h, c in best]}
    if tab == "top":
        rows = [r for r in rows if r[0].show_value and r[0].public_value_cents and r[0].valued_at and r[0].valued_at >= fresh]
        rows.sort(key=lambda r: -(r[0].public_value_cents or 0))
    elif tab == "cards":
        rows.sort(key=lambda r: -counts.get(r[0].id, 0))
    elif tab == "recent":
        rows.sort(key=lambda r: -(r[0].last_activity_at.timestamp() if r[0].last_activity_at else 0))
    else:
        rows = [r for r in rows if r[0].review_status == "featured"]
    all_ranks = ledger.ranks(db)  # read once, not once per row
    for i, (p, m) in enumerate(rows[:limit]):
        rank = ledger.rank_for(db, m.rank_score, all_ranks)
        out.append({"position": i + 1, "username": m.username, "bio": p.bio, "rank": {"name": rank.name, "key": rank.key, "art": rank.art or {}},
                    "value": p.public_value_cents if p.show_value else None, "cards": counts.get(p.id, 0),
                    "updated_at": p.last_activity_at.isoformat() if p.last_activity_at else None})
    return {"tab": tab, "rows": out, "note": "Estimated values of self-reported collections. Price Chat has not verified ownership."}
