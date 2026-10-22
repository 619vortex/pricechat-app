"""✨ Estimate Condition: an AI review of a RAW card's photos (Anthropic vision model).

It returns an estimated condition RANGE as a grade-equivalent (e.g. "approximately 5-7 equivalent"), the visible issues
(centering, corners, edges, surface, creases, scratches, whitening, print defects), a confidence, and the photos it would
need to be surer. It is never a grade, never authentication, never a promise of a grading outcome:
  - it needs a front AND a back photo of usable resolution, otherwise it asks for them (no call, no cost);
  - the top of the range is capped at 9 (a 10 cannot be judged from phone photos);
  - limits per member and site-wide per day; the same photos are never assessed twice.
"""

import base64
import hashlib
import json
import logging
import re
from datetime import timedelta
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config.settings import Settings
from app.db.base import utcnow
from app.members.service import MemberError
from app.models.members import Member
from app.models.pokemon import PokemonCard
from app.models.portfolio import PokemonHolding, PokemonHoldingPhoto, PokemonScan
from app.portfolio import photos, valuation

logger = logging.getLogger(__name__)
API = "https://api.anthropic.com/v1/messages"
MIN_SIDE = 900  # px on the shorter side of the processed photo: below this, details can't be judged
MAX_HIGH = 9.0

PROMPT = """You are helping a Pokémon card collector understand the physical condition of an UNGRADED card from their photos.
Card: {card}.
Look only at what is visible. Assess centering (front and back), corners, edges, surface (scratches, print lines, dents,
creases, stains), whitening and print defects. Then estimate the condition as a RANGE of grade-equivalents on a 1-10 scale
(like "5-7"), the way an experienced collector would, without claiming it IS that grade. Phone photos hide surface wear and
cannot show a 10. If the photos are blurry, glared, cropped, or don't show both sides clearly, say so and lower confidence.
Never comment on authenticity.
Reply with JSON only, no prose, in exactly this shape:
{{"enough_photos": true|false, "condition_low": number, "condition_high": number, "confidence": "low"|"medium"|"high",
"centering": "...", "corners": "...", "edges": "...", "surface": "...", "issues": ["..."], "more_photos": ["..."]}}"""


def configured(settings: Settings) -> bool:
    from app.security.credentials import resolve

    return bool(resolve("anthropic_api_key", settings))


def _limits(db: Session, settings: Settings, member: Member) -> None:
    since = utcnow() - timedelta(days=1)
    mine = db.scalar(select(func.count()).select_from(PokemonScan).where(PokemonScan.member_id == member.id, PokemonScan.kind == "condition",
                                                                         PokemonScan.at >= since, PokemonScan.status == "ok")) or 0
    if mine >= settings.portfolio_conditions_per_day:
        raise MemberError(f"You've used today's {settings.portfolio_conditions_per_day} condition estimates. Try again tomorrow.")
    total = db.scalar(select(func.count()).select_from(PokemonScan).where(PokemonScan.kind == "condition", PokemonScan.at >= since,
                                                                          PokemonScan.status == "ok")) or 0
    if total >= settings.portfolio_conditions_total_per_day:
        raise MemberError("The condition assistant is very busy today. Please try again tomorrow.")


def _parse(text: str) -> dict[str, Any]:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("no JSON")
    d = json.loads(m.group(0))
    lo, hi = float(d["condition_low"]), float(d["condition_high"])
    lo, hi = max(1.0, min(lo, hi)), min(MAX_HIGH, max(lo, hi))
    if lo > hi:
        lo = hi
    clean = {k: str(d.get(k) or "")[:400] for k in ("centering", "corners", "edges", "surface")}
    return {"enough_photos": bool(d.get("enough_photos", True)), "low": round(lo * 2) / 2, "high": round(hi * 2) / 2,
            "confidence": d.get("confidence") if d.get("confidence") in ("low", "medium", "high") else "low",
            **clean, "issues": [str(x)[:200] for x in (d.get("issues") or [])][:10],
            "more_photos": [str(x)[:160] for x in (d.get("more_photos") or [])][:6]}


async def estimate(db: Session, settings: Settings, member: Member, holding: PokemonHolding, transport: Any = None) -> dict[str, Any]:
    if holding.kind != "raw":
        raise MemberError("Condition estimates are for raw cards. Graded cards have their official grade.")
    pics = list(db.scalars(select(PokemonHoldingPhoto).where(PokemonHoldingPhoto.holding_id == holding.id).order_by(PokemonHoldingPhoto.id)))
    sides = {p.side for p in pics}
    usable = [p for p in pics if min(p.width, p.height) >= MIN_SIDE]
    missing = [s for s in ("front", "back") if s not in sides]
    if missing or len(usable) < 2:
        return {"status": "need_photos", "missing": missing,
                "message": "Add a sharp, well-lit photo of the " + (" and ".join(missing) if missing else "front and back") +
                           " (whole card in frame, no glare) so the assistant can judge the condition."}
    if not configured(settings):
        raise MemberError("The condition assistant isn't set up yet.")
    key = hashlib.sha256("|".join(sorted(p.sha256 for p in pics)).encode()).hexdigest()
    if holding.condition_report and holding.condition_report.get("photos_key") == key:
        return {"status": "ok", "report": holding.condition_report, "cached": True}
    _limits(db, settings, member)
    from app.security.credentials import resolve

    store = photos.Storage(settings)
    card = db.get(PokemonCard, holding.card_id)
    assert card is not None
    blocks: list[dict[str, Any]] = []
    for p in sorted(pics, key=lambda p: {"front": 0, "back": 1}.get(p.side, 2))[:4]:
        blocks.append({"type": "text", "text": f"{p.side} photo:"})
        blocks.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(store.get(p.key)).decode()}})
    label = f"{card.year} {card.set_name} {card.name} {card.card_number or ''} ({card.language}, {card.edition})"
    blocks.append({"type": "text", "text": PROMPT.format(card=label)})
    row = PokemonScan(member_id=member.id, at=utcnow(), kind="condition", image_sha256=key, status="error", credits=0, result={})
    db.add(row)
    db.commit()  # counted towards the limits at once, and nothing written is held open during the (up to 90 s) review
    try:
        async with httpx.AsyncClient(timeout=90.0, transport=transport) as client:
            r = await client.post(API, headers={"x-api-key": resolve("anthropic_api_key", settings) or "", "anthropic-version": "2023-06-01",
                                                "content-type": "application/json"},
                                  json={"model": settings.condition_model, "max_tokens": 900, "messages": [{"role": "user", "content": blocks}]})
        if r.status_code >= 400:
            logger.warning("Condition assistant refused: %s %s", r.status_code, r.text[:200])
            raise MemberError("The condition assistant couldn't review these photos right now. Please try again later.")
        body = r.json()
        text = "".join(b.get("text", "") for b in body.get("content", []) if b.get("type") == "text")
        report = _parse(text)
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        db.commit()
        raise MemberError("The condition assistant couldn't read these photos. Try clearer photos.") from None
    except MemberError:
        db.commit()
        raise
    row.status = "ok"
    usage = body.get("usage") or {}
    row.result = {"model": settings.condition_model, "input_tokens": usage.get("input_tokens"), "output_tokens": usage.get("output_tokens")}
    report.update({"photos_key": key, "model": settings.condition_model, "at": utcnow().isoformat(),
                   "label": "AI estimate: not a professional grade, not authentication"})
    holding.condition_report = report
    if report["enough_photos"]:
        before = holding.value_cents * holding.quantity if holding.value_cents is not None else None
        holding.condition_low, holding.condition_high, holding.condition_source = report["low"], report["high"], "ai"
        now = utcnow()
        valuation.apply(holding, valuation.Pricer(db, [holding], now).value(holding), now)
        after = holding.value_cents * holding.quantity if holding.value_cents is not None else None
        if before is not None or after is not None:  # a re-assessment changes WHAT is held: a flow, never a market move
            from app.models.portfolio import PokemonHoldingEvent

            db.add(PokemonHoldingEvent(portfolio_id=holding.portfolio_id, holding_id=holding.id, at=now, kind="grade", quantity_delta=0,
                                       value_delta_cents=(after or 0) - (before or 0)))
        from app.models.portfolio import PokemonPortfolio
        from app.portfolio.service import _refresh_totals

        p = db.get(PokemonPortfolio, holding.portfolio_id)
        if p is not None:
            _refresh_totals(db, p)
    db.commit()
    return {"status": "ok", "report": report, "cached": False}
