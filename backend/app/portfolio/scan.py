"""Card scanner: a photo -> the exact catalogue printing(s) it could be, for the member to confirm.

Recognition: Scrydex Vision (POST /vision/v1/cards/identify, 5 credits a request). Its matches are Scrydex cards; each is
mapped to OUR catalogue (the canonical card ids), printing by printing:
  - Scrydex names the printing (variant) only when it is confident; then that exact printing is "suggested";
  - otherwise EVERY printing of that card is offered (1st Edition, Shadowless, Unlimited...), none preselected: an
    expensive printing is never chosen for the member on thin evidence. The member always confirms before saving.
Graded slabs: Scrydex reads the label (company, grade, cert) and the grade is proposed, not saved, until confirmed.

Limits (credits): per member and site-wide per day; the same photo re-sent within a day reuses the stored answer.
The photo itself is not stored by the scan (only a hash); it's kept only if the member saves it with the card.
"""

import hashlib
import logging
from datetime import timedelta
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config.settings import Settings
from app.db.base import utcnow
from app.members.service import MemberError
from app.models.members import Member
from app.models.pokemon import PokemonCard, PokemonProviderValuation
from app.models.portfolio import PokemonScan
from app.pokemon.grades import GradeError, normalise
from app.portfolio import photos

logger = logging.getLogger(__name__)
CREDITS = 5
CONFIDENT = 1.05  # Scrydex scores run ~0.7-1.3+; above this, with a clear margin over the next card, the card is likely
MARGIN = 0.08


def configured(settings: Settings) -> bool:
    from app.security.credentials import resolve

    return bool(resolve("scrydex_api_key", settings) and resolve("scrydex_team_id", settings))


def _limits(db: Session, settings: Settings, member: Member) -> None:
    since = utcnow() - timedelta(days=1)
    mine = db.scalar(select(func.count()).select_from(PokemonScan).where(PokemonScan.member_id == member.id, PokemonScan.at >= since,
                                                                         PokemonScan.kind == "identify", PokemonScan.credits > 0)) or 0
    if mine >= settings.portfolio_scans_per_day:
        raise MemberError(f"You've used today's {settings.portfolio_scans_per_day} scans. Add cards by search, or scan again tomorrow.")
    total = db.scalar(select(func.count()).select_from(PokemonScan).where(PokemonScan.at >= since, PokemonScan.kind == "identify",
                                                                          PokemonScan.credits > 0)) or 0
    if total >= settings.portfolio_scans_total_per_day:
        raise MemberError("The scanner is very busy today. Please add the card by search for now.")


def _catalogue_matches(db: Session, scrydex_id: str) -> list[PokemonCard]:
    """Every catalogue printing of one Scrydex card (catalogue cards carry their Scrydex id + variant)."""
    return list(db.scalars(select(PokemonCard).where(func.json_extract(PokemonCard.external_ids, "$.scrydex") == scrydex_id)
                           .order_by(PokemonCard.id)))


def _prices(db: Session, ids: list[int]) -> dict[int, dict[str, int]]:
    out: dict[int, dict[str, int]] = {}
    for r in db.scalars(select(PokemonProviderValuation).where(
            PokemonProviderValuation.card_id.in_(ids or [0]), PokemonProviderValuation.kind == "current", PokemonProviderValuation.currency == "USD",
            PokemonProviderValuation.market_cents.is_not(None),
            ((PokemonProviderValuation.grading_company == "PSA") & (PokemonProviderValuation.grade == "10"))
            | ((PokemonProviderValuation.grading_company == "RAW") & (PokemonProviderValuation.grade == "NM"))).order_by(PokemonProviderValuation.day)):
        out.setdefault(r.card_id, {})["PSA 10" if r.grading_company == "PSA" else "Raw NM"] = r.market_cents
    return out


def candidate_out(c: PokemonCard, prices: dict[str, int], suggested: bool, score: float | None) -> dict[str, Any]:
    from app.portfolio.service import card_brief

    return {"card": card_brief(c), "suggested": suggested, "score": round(score, 3) if score is not None else None,
            "reference_prices": prices}


def _label(gd: dict[str, Any]) -> dict[str, Any]:
    """What a slab's label says (text + year), as evidence for the PRINTING. A label is never proof of authenticity: it only
    decides between printings of a card the artwork already matched, and a mismatch with the catalogue is flagged."""
    text = " ".join(str(v) for k, v in gd.items() if isinstance(v, (str, int)) and k not in ("cert", "grade_number", "grade_code")).lower()
    year = next((int(v) for k, v in gd.items() if "year" in k and str(v).isdigit() and 1995 <= int(v) <= 2100), None)
    return {"text": text, "year": year, "reverse": "reverse" in text, "first": "1st" in text or "first edition" in text}


def interpret(db: Session, body: dict[str, Any]) -> dict[str, Any]:
    """Scrydex Vision answer -> our candidates (see the module notes)."""
    data = body.get("data") or {}
    analysis = data.get("analysis") or {}
    matches = [m for m in (data.get("matches") or []) if (m.get("card") or {}).get("id")]
    groups: list[dict[str, Any]] = []
    seen: set[int] = set()
    for i, m in enumerate(matches[:5]):
        sid = str(m["card"]["id"])
        printings = [c for c in _catalogue_matches(db, sid) if c.id not in seen]
        if not printings:
            continue
        seen |= {c.id for c in printings}
        variant = m.get("variant")
        exact = [c for c in printings if variant and str((c.external_ids or {}).get("scrydex_variant") or "") == str(variant)]
        groups.append({"scrydex_id": sid, "name": m["card"].get("name"), "score": float(m.get("score") or 0), "variant": variant,
                       "printings": printings, "exact": exact[0].id if len(exact) == 1 else (printings[0].id if len(printings) == 1 else None)})
    gd = analysis.get("graded_details") or {}
    label = _label(gd) if analysis.get("type") == "graded" and gd else None
    if label and groups:
        # the label names the printing ("REVERSE FOIL", "1ST EDITION"): within the matched card, that printing is the one
        for g in groups:
            if not any(w in label["text"] for w in str(g["name"] or "").lower().split()[:1]):
                continue  # the label text wasn't read (or names another card): it can't decide the printing
            fit = [c for c in g["printings"] if ("reverse" in (c.variant or "").lower()) == label["reverse"]
                   and (not label["first"] or c.edition == "1st Edition")]
            if len(fit) == 1:
                g["exact"] = fit[0].id
    prices = _prices(db, [c.id for g in groups for c in g["printings"]])
    top = groups[0] if groups else None
    second = groups[1]["score"] if len(groups) > 1 else 0.0
    likely = bool(top and top["score"] >= CONFIDENT and top["score"] - second >= MARGIN)
    candidates = []
    for gi, g in enumerate(groups):
        for c in sorted(g["printings"], key=lambda c: c.id != g["exact"]):  # the label's printing first
            out = candidate_out(c, prices.get(c.id, {}), suggested=(gi == 0 and likely and g["exact"] == c.id), score=g["score"])
            if label and label["year"] and c.year and abs(label["year"] - c.year) > 1:
                out["warning"] = f"The slab label says {label['year']}; this card is from {c.year}. Check the label."
            candidates.append(out)
    graded = None
    if analysis.get("type") == "graded" and gd.get("company"):
        try:
            g = normalise(str(gd.get("company")), str(gd.get("grade_number") or gd.get("grade_code") or ""))
            graded = {"company": g.company, "grade": g.grade, "cert": str(gd.get("cert") or "") or None}
        except GradeError:
            graded = None
    need_choice = not likely or not top or top["exact"] is None
    reason = ("" if not need_choice else "no catalogue card matched" if not top else
              "several cards look alike: pick the right one" if not likely else
              "the printing (e.g. 1st Edition vs Unlimited) can't be told from the photo: pick it")
    return {"candidates": candidates, "confidence": "likely" if likely and not need_choice else "uncertain", "needs_choice": need_choice,
            "reason": reason, "detected": {"type": analysis.get("type"), "language": analysis.get("language_code"), "graded": graded,
                         "label": {k: label[k] for k in ("year", "reverse", "first")} if label else None}}


async def identify(db: Session, settings: Settings, member: Member, image: bytes, transport: Any = None) -> dict[str, Any]:
    if not configured(settings):
        raise MemberError("The scanner isn't available right now. Add the card by search.")
    full, _thumb, _w, _h, sha = photos.process(image)  # validated + re-encoded; ~300 KB, as Scrydex recommends
    cached = db.scalar(select(PokemonScan).where(PokemonScan.member_id == member.id, PokemonScan.image_sha256 == sha, PokemonScan.kind == "identify",
                                                 PokemonScan.status == "ok", PokemonScan.at >= utcnow() - timedelta(days=1)).order_by(PokemonScan.id.desc()).limit(1))
    if cached is not None and cached.result.get("raw"):
        return {**interpret(db, cached.result["raw"]), "scan_id": cached.id, "cached": True}
    _limits(db, settings, member)
    from app.security.credentials import resolve

    headers = {"X-Api-Key": resolve("scrydex_api_key", settings) or "", "X-Team-ID": resolve("scrydex_team_id", settings) or ""}
    url = f"{settings.scrydex_api_base.rstrip('/')}/vision/v1/cards/identify"
    row = PokemonScan(member_id=member.id, at=utcnow(), kind="identify", image_sha256=sha, status="error", credits=0, result={})
    db.add(row)
    db.commit()  # counted towards the limits at once, and nothing written is held open while Scrydex answers
    try:
        async with httpx.AsyncClient(timeout=45.0, transport=transport) as client:
            r = await client.post(url, headers=headers, files={"image": ("card.jpg", full, "image/jpeg")}, data={"games": "pokemon"})
        row.credits = CREDITS if r.status_code < 500 else 0
        if r.status_code >= 400:
            logger.warning("Scrydex Vision refused a scan: %s %s", r.status_code, r.text[:200])
            raise MemberError("We couldn't read that photo. Try again with the whole card in frame, or search for it.")
        body = r.json()
    except httpx.HTTPError:
        db.commit()
        raise MemberError("The scanner didn't answer. Please try again in a moment.") from None
    except MemberError:
        db.commit()
        raise
    out = interpret(db, body)
    row.status = "ok" if out["candidates"] else "no_match"
    row.result = {"raw": {"data": {"analysis": (body.get("data") or {}).get("analysis"), "matches": [
        {"score": m.get("score"), "variant": m.get("variant"), "card": {"id": (m.get("card") or {}).get("id"), "name": (m.get("card") or {}).get("name")}}
        for m in ((body.get("data") or {}).get("matches") or [])[:5]]}}, "candidates": len(out["candidates"])}
    db.commit()
    return {**out, "scan_id": row.id, "cached": False}


def image_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
