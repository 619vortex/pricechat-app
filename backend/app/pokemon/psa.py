"""PSA certs -> catalog cards -> card-wide PSA population.

A catalog card is linked to PSA by PSA's own identifier, the SpecID, which every cert lookup returns. Linking is
structured (PSA's Year / Brand / CardNumber / Subject / Variety against the catalog fields), never a fuzzy title guess:
exactly one catalog card must fit every stated field, otherwise the link waits for the owner's confirmation.
Certificate facts (one slab) are returned as such and never stored as population; population comes only from the spec
population endpoint and is stored per grade with source "psa".
"""

import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.pokemon import PokemonCard
from app.pokemon import service
from app.pokemon.grades import Grade, GradeError, normalise
from app.pokemon.matching import SET_ALIASES, has, norm
from app.providers.pokemon import Unavailable, provider
from app.providers.pokemon.psa import PSAProvider

# PSA "Brand" names that don't contain the set's own name (PSA labels 1999 Base Set as "POKEMON GAME")
PSA_BRANDS: dict[str, str] = {"pokemon game": "base set", "pokemon rocket": "team rocket", "pokemon japanese base": "base set"}
MAX_SYNC_PER_RUN = 25  # spec lookups per sync run (free tier: 100 calls/day; results are cached for hours)


@dataclass
class PsaMatch:
    card_id: int | None
    status: str  # matched | review | not_in_catalog
    reasons: list[str] = field(default_factory=list)
    candidates: list[int] = field(default_factory=list)


def _edition(variety: str) -> str:
    v = norm(variety)
    if has(v, "1st edition") or has(v, "1st ed"):
        return "1st Edition"
    if has(v, "shadowless"):
        return "Shadowless"
    return "Unlimited"  # PSA states 1st Edition / Shadowless on the label; an unlimited print has neither


def _check(c: PokemonCard, cert: dict[str, Any], cards: list[PokemonCard]) -> tuple[bool, bool, list[str]]:
    """(fits, sure, reasons). fits=False: a stated field contradicts this card. sure=False: fits, but not every field confirmed."""
    subject = norm(str(cert.get("subject", "")).replace("-", " "))
    brand = norm(str(cert.get("brand", "")))
    why: list[str] = []
    sure = True
    words = [w for w in norm(c.name.replace("★", "")).split() if w]
    if not all(has(subject, w) for w in words):
        return False, False, [f"subject is not {c.name}"]
    why.append(f"name {c.name}")
    year = str(cert.get("year") or "")[:4]
    if year.isdigit():
        if int(year) != c.year:
            return False, False, [f"year {year}, not {c.year}"]
        why.append(f"year {year}")
    else:
        sure = False
    japanese = "japanese" in brand
    if japanese != (c.language == "Japanese"):
        return False, False, [f"{'Japanese' if japanese else 'English'}, not {c.language}"]
    num = re.sub(r"\D", "", str(cert.get("card_number") or ""))
    if num and c.card_number:
        if int(num) != int(c.card_number.split("/")[0]):
            return False, False, [f"number {num}, not {c.card_number}"]
        why.append(f"number {c.card_number}")
    elif num or c.card_number:
        sure = False
        why.append("card number not confirmed")
    if c.edition not in ("—", ""):
        ed = _edition(str(cert.get("variety") or ""))
        if ed != c.edition:
            return False, False, [f"{ed}, not {c.edition}"]
        why.append(c.edition)
    aliases = SET_ALIASES.get(c.set_name.lower(), ()) + (norm(c.set_name),)
    mapped = next((s for b, s in PSA_BRANDS.items() if has(brand, b)), None)
    if any(has(brand, a) for a in aliases) or (mapped and mapped in (c.set_name.lower(), *aliases)):
        why.append(f"set {c.set_name}")
    else:
        sure = False
        why.append(f"set '{cert.get('brand')}' not confirmed as {c.set_name}")
    return True, sure, why


def match_cert(cert: dict[str, Any], cards: list[PokemonCard]) -> PsaMatch:
    results = [(c.id, *_check(c, cert, cards)) for c in cards]
    fits = [(cid, sure, why) for cid, ok, sure, why in results if ok]
    if not fits:
        return PsaMatch(None, "not_in_catalog", ["no catalog card fits this PSA cert"])
    sure = [f for f in fits if f[1]]
    if len(sure) == 1 and len(fits) == 1:
        return PsaMatch(sure[0][0], "matched", sure[0][2], [sure[0][0]])
    best = (sure or fits)[0]
    reasons = list(best[2]) + (["another catalog card fits too"] if len(fits) > 1 else [])
    return PsaMatch(best[0], "review", reasons, [f[0] for f in fits])


def _psa(settings: Any, transport: Any) -> PSAProvider:
    p = provider("psa", settings, transport)
    assert isinstance(p, PSAProvider)
    return p


async def refresh_population(db: Session, p: PSAProvider, card: PokemonCard) -> dict[str, Any]:
    """Card-wide PSA population for a linked card, stored per plain grade (qualified counts are reported, not stored)."""
    spec = (card.external_ids or {}).get("psa_spec")
    if not spec:
        raise Unavailable("This card isn't linked to a PSA spec yet.", "not_linked")
    pop = await p.get_spec_population(spec)
    stored = 0
    for rec in pop["records"]:
        try:
            g = normalise("PSA", rec.grade)
        except GradeError:
            continue
        service.store_population(db, card.id, Grade(g.company, g.grade), rec.count, "psa", rec.as_of)
        stored += 1
    return {"spec_id": pop["spec_id"], "description": pop["description"], "grades_stored": stored, "total": pop["total"],
            "qualified": pop["qualified"], "as_of": pop["as_of"]}


async def link_cert(db: Session, settings: Any, cert_number: str, card_id: int | None = None, confirm: bool = False,
                    transport: Any = None) -> dict[str, Any]:
    """Look a cert up, match it to the catalog, and (when certain, or confirmed by the owner) link the card to its PSA spec
    and pull the card-wide population. Returns the cert facts, the match, and what was stored."""
    p = _psa(settings, transport)
    out: dict[str, Any] = {"linked": False, "population": None, "population_error": None}
    try:
        cert = await p.get_cert(cert_number)
    finally:
        service.record_request(db, "psa", p.requests, p.info.rate_limit)
        db.commit()
    out["cert"] = cert
    cards = list(db.scalars(select(PokemonCard).order_by(PokemonCard.id)))
    m = match_cert(cert, cards)
    out["match"] = {"status": m.status, "card_id": m.card_id, "reasons": m.reasons, "candidates": m.candidates}
    target = card_id if card_id is not None else m.card_id
    if target is None:
        return out
    if card_id is not None and card_id not in m.candidates:
        card = db.get(PokemonCard, card_id)
        fits = _check(card, cert, cards) if card else (False, False, ["unknown card"])
        out["match"]["reasons"] = fits[2]
        out["match"]["status"] = "mismatch"
        return out  # a stated PSA field contradicts that card: never linked, not even with confirm
    if m.status != "matched" or m.card_id != target:
        if not confirm:
            return out  # the owner confirms on the card page
    if not cert.get("spec_id"):
        out["match"]["reasons"].append("PSA returned no SpecID for this cert")
        return out
    card = db.get(PokemonCard, target)
    assert card is not None
    ids = dict(card.external_ids or {})
    certs = [str(x) for x in ids.get("psa_certs", []) if str(x) != str(cert["cert_number"])]
    ids["psa_spec"] = str(cert["spec_id"])
    ids["psa_certs"] = ([str(cert["cert_number"])] + certs)[:20]
    card.external_ids = ids
    db.commit()
    out["linked"], out["card_id"] = True, card.id
    p.requests = 0
    try:
        out["population"] = await refresh_population(db, p, card)
        service.record_source(db, "psa", out["population"]["grades_stored"], None)
    except Unavailable as exc:
        out["population_error"] = exc.reason
        service.record_source(db, "psa", 0, exc.reason)
    service.record_request(db, "psa", p.requests, p.info.rate_limit)
    db.commit()
    from app.pokemon import history

    history.invalidate()
    return out


async def sync_psa(db: Session, settings: Any, transport: Any = None) -> dict[str, Any]:
    """Refresh card-wide PSA population for every card linked to a PSA spec (a few per run; cached)."""
    from app.pokemon import history

    p = _psa(settings, transport)
    if not p.status()[0]:
        raise Unavailable(p.status()[1], "not_connected")
    linked = [c for c in db.scalars(select(PokemonCard).order_by(PokemonCard.id)) if (c.external_ids or {}).get("psa_spec")]
    out: dict[str, Any] = {"linked": len(linked), "refreshed": 0, "population_rows": 0, "error": None}
    for card in linked[:MAX_SYNC_PER_RUN]:
        try:
            got = await refresh_population(db, p, card)
        except Unavailable as exc:
            out["error"] = exc.reason
            if exc.code in ("not_approved", "auth", "rate_limited", "not_connected"):
                break  # same answer for every card: stop spending calls
            continue
        out["refreshed"] += 1
        out["population_rows"] += got["grades_stored"]
        db.commit()
    history.invalidate()
    service.record_request(db, "psa", p.requests, p.info.rate_limit)
    service.record_source(db, "psa", out["population_rows"], out["error"])
    db.commit()
    return out
