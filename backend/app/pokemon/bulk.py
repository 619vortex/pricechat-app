"""The whole card catalogue from Scrydex: every English and Japanese card (all physical sets), with its graded market
prices and PSA population, refreshed weekly.

Cost: Scrydex lists a set's cards 100 per request (1 credit) with prices and population included, so a full pass of
~47,000 cards is ~480 credits. A pass is spread over hourly job runs (`max_sets` per run, so a run never blocks the
other background jobs for long); a set is due again 7 days after its last refresh (from the run logs, provider
"scrydex-catalog"). Credits are checked before every run and a run never starts what it can't finish.

Rules:
  - One catalog card per Scrydex card + regular printing (1st Edition / Shadowless / Unlimited / No Rarity; special prints
    such as stamps are left out). Reverse holos are their own printing for VINTAGE sets (e.g. Legendary Collection "Reverse
    Foil", e-Card reverse holos: distinct collectibles with their own prices); modern reverse holos are left out. Cards that are already in the catalog (linked by the per-card
    Scrydex sync, or the same set + number + edition) are never duplicated or changed: they keep their own sync.
  - Imported cards are tagged external_ids.source = "scrydex-catalog". Every card is imported, priced or not.
  - Prices: Scrydex market valuations (provider estimates, never sales) for the grades the market shows; signed / error /
    "perfect" prices are ignored and a grade 10 priced below its grade 9 is not used (scrydex_sync.inverted).
  - Japanese cards come only from Japanese sets (language_code "JA") and get only those sets' prices: never English ones.
"""

import asyncio
import math
import re
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.pokemon import (PokemonCard, PokemonPopulation, PokemonPopulationHistory, PokemonProviderValuation,
                                PokemonSyncLog)
from app.pokemon import catalog, history, service
from app.pokemon.grades import GradeError, normalise
from app.pokemon.scrydex_sync import DAILY_GRADES, _cents, _graded, inverted, scrydex_id
from app.providers.pokemon import Unavailable, provider
from app.providers.pokemon.scrydex import ScrydexProvider, variant_tokens

PROVIDER = "scrydex"
LOG_PROVIDER = "scrydex-catalog"
SOURCE_TAG = "scrydex-catalog"
REFRESH_DAYS = 7
VINTAGE_UNTIL = 2003
KEEP_GRADES = DAILY_GRADES  # daily history kept for these; other grades / raw conditions: one row per card, updated in place
FIELDS = "id,name,number,printed_number,rarity,supertype,images,variants,translation,language_code,expansion"

_SUFFIX = re.compile(r"(?:\s+|-)(?:ex|EX|GX|V|VMAX|VSTAR|V-UNION|LV\.?\s?X|BREAK|Prime|LEGEND|δ|☆|Star|◇|TAG TEAM)$")
_PREFIX = re.compile(r"^(?:M|Mega|Radiant|Shining|Dark|Light|Galarian|Alolan|Hisuian|Paldean|Rocket's|Team (?:Aqua|Magma)'s|[\w.]+?'s)\s+", re.I)


def base_pokemon(name: str, supertype: str | None) -> str:
    """'Charizard ex' / 'M Charizard-EX' / 'Radiant Charizard' / "Blaine's Charizard" -> 'Charizard'. Trainers and
    Energy keep their full name (they are not a Pokémon)."""
    if supertype and not supertype.lower().startswith("pok"):
        return name
    n = name.replace("★", "").strip()
    for _ in range(3):
        m = _SUFFIX.sub("", n).strip()
        m = _PREFIX.sub("", m).strip()
        if m == n:
            break
        n = m
    return n or name


def _day(s: Any) -> date | None:
    try:
        return date.fromisoformat(str(s)[:10].replace("/", "-"))
    except ValueError:
        return None


def _image(c: dict[str, Any]) -> str | None:
    for im in c.get("images") or []:
        if im.get("type") in (None, "front") and (im.get("large") or im.get("medium")):
            return str(im.get("large") or im.get("medium"))
    return None


def printings(variants: list[dict[str, Any]], vintage: bool = False) -> list[tuple[dict[str, Any], str, str, str]]:
    """The printings of one card as (variant, edition, variant label, holo type). Special prints are not catalog cards;
    reverse holos are, for vintage sets only (label "Reverse Holo"). A card with no variant at all is still one card."""
    def keep(v: dict[str, Any]) -> bool:
        name = str(v.get("name", ""))
        t = variant_tokens(name)
        special = variant_tokens(name.lower().replace("reverse", ""))["special"]  # "reverse" alone doesn't make a special print
        return not special and (not t["reverse"] or vintage)

    regular = [v for v in variants if keep(v) and not variant_tokens(str(v.get("name", "")))["reverse"]]
    if not regular and not vintage:
        regular = [v for v in variants if not variant_tokens(str(v.get("name", "")))["special"]]
    reverse = [v for v in variants if vintage and keep(v) and variant_tokens(str(v.get("name", "")))["reverse"]]
    if not regular:
        regular = variants[:1]
    has_first = any(variant_tokens(str(v.get("name", "")))["first"] for v in variants)
    out = []
    for v in regular:
        t = variant_tokens(str(v.get("name", "")))
        edition = "1st Edition" if t["first"] else "Shadowless" if t["shadowless"] else "Unlimited" if (has_first or t["unlimited"]) else "—"
        out.append((v, edition, "No Rarity" if t["norarity"] else "", "Holo" if t["holo"] else "Non-Holo"))
    # two printings that would look identical (e.g. a promo in non-holo AND holo): the holo type tells them apart
    seen: dict[tuple[str, str], int] = {}
    for _, e, lab, _h in out:
        seen[(e, lab)] = seen.get((e, lab), 0) + 1
    out = [(v, e, (f"{lab} {h}".strip() if seen[(e, lab)] > 1 else lab), h) for v, e, lab, h in out] or [({"name": ""}, "—", "", "Non-Holo")]
    for v in reverse:  # vintage reverse holos: the same edition as the regular print, labelled as its own printing
        if any(v is r for r, _, _, _ in out):
            continue  # a card whose only print is a reverse holo already has it as its one printing
        t = variant_tokens(str(v.get("name", "")))
        edition = "1st Edition" if t["first"] else "Unlimited" if (has_first or t["unlimited"]) else "—"
        out.append((v, edition, "Reverse Holo", "Reverse Holo"))
    return out


def expansions(rows: list[dict[str, Any]], languages: tuple[str, ...]) -> list[dict[str, Any]]:
    return sorted((e for e in rows if not e.get("is_online_only") and str(e.get("language_code") or "").upper() in languages),
                  key=lambda e: (str(e.get("release_date") or ""), str(e.get("id"))))


async def all_expansions(p: ScrydexProvider) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    page = 1
    while True:
        b = await p.get("/pokemon/v1/expansions", {"page_size": 100, "page": page})
        rows += b.get("data") or []
        if not b.get("data") or len(rows) >= (b.get("total_count") or 0):
            return rows
        page += 1


def refreshed_since(db: Session, since: datetime) -> set[str]:
    """Sets refreshed by a catalog run since `since` (from the run logs)."""
    done: set[str] = set()
    for log in db.scalars(select(PokemonSyncLog).where(PokemonSyncLog.provider == LOG_PROVIDER, PokemonSyncLog.started_at >= since)):
        done |= {str(e.get("expansion")) for e in log.entries or [] if e.get("status") == "ok"}
    return done


def _set_names(db: Session) -> dict[str, str]:
    """Set names the catalog already uses per Scrydex set id, so imported cards join the same set ("Base Set", not "Base")."""
    from app.pokemon.japanese import SCRYDEX_SETS

    names: dict[str, str] = {}
    for c in db.scalars(select(PokemonCard)):
        if c.set_code:
            names.setdefault(str(c.set_code), c.set_name)
        sid = SCRYDEX_SETS.get(c.set_name.lower())
        # only a real set (not a catalog grouping such as "CoroCoro Comic Promo" inside Scrydex's unnumbered promos)
        if c.language == "Japanese" and sid and list(SCRYDEX_SETS.values()).count(sid) == 1:
            names.setdefault(sid, c.set_name)
    return names


def _existing(db: Session) -> tuple[dict[tuple[str, str], PokemonCard], dict[tuple[str, str, str], PokemonCard], set[str]]:
    """Catalog cards by (Scrydex card id, Scrydex variant) and by (Scrydex card id, edition, variant label); every identity key."""
    by_variant: dict[tuple[str, str], PokemonCard] = {}
    by: dict[tuple[str, str, str], PokemonCard] = {}
    keys: set[str] = set()
    for c in db.scalars(select(PokemonCard)):
        keys.add(c.identity_key)
        ids = c.external_ids or {}
        sid = str(ids.get("scrydex") or "") or scrydex_id(c) or ""
        if sid and ids.get("scrydex_variant"):
            by_variant[(sid, str(ids["scrydex_variant"]))] = c
        if sid:
            by[(sid, c.edition, c.variant)] = c
    return by_variant, by, keys


def _store_prices(db: Session, card: PokemonCard, sid: str, v: dict[str, Any], today: date, now: datetime,
                  have: dict[tuple[int, str, str], PokemonProviderValuation], summary: dict[str, Any],
                  extra: dict[tuple[int, str, str], PokemonProviderValuation] | None = None) -> None:
    extra = extra if extra is not None else {}
    bad = inverted(v.get("prices") or [])
    for pr in v.get("prices") or []:
        g = _graded(pr)
        if g is None or (g.company, g.grade) in bad or _cents(pr.get("market")) is None:
            if g is not None and (g.company, g.grade) in bad:
                summary["inconsistent"] += 1
            continue
        row = have.get((card.id, g.company, g.grade))
        if row is None and (g.company, g.grade) not in KEEP_GRADES:
            # every other grade and the raw conditions (portfolio holdings): ONE row per card + grade, updated in place,
            # so a weekly refresh of 25,000 cards doesn't add hundreds of thousands of daily rows to a small volume
            row = extra.get((card.id, g.company, g.grade))
            if row is not None:
                row.day = today
                have[(card.id, g.company, g.grade)] = row
        if row is None:
            row = PokemonProviderValuation(provider=PROVIDER, card_id=card.id, grading_company=g.company, grade=g.grade, day=today)
            db.add(row)
            have[(card.id, g.company, g.grade)] = row
            summary["valuations_new"] += 1
        row.provider_card_id, row.provider_variant, row.kind, row.fetched_at = sid, str(v.get("name") or ""), "current", now
        row.market_cents, row.low_cents = _cents(pr.get("market")), _cents(pr.get("low"))
        row.mid_cents, row.high_cents = _cents(pr.get("mid")), _cents(pr.get("high"))
        row.currency = str(pr.get("currency") or "USD")[:3]
        row.trends = pr.get("trends")
        summary["valuations"] += 1


def _store_population(db: Session, card: PokemonCard, v: dict[str, Any], today: date, now: datetime,
                      pops: dict[tuple[int, str, str], PokemonPopulation], hist: dict[tuple[int, str, str], PokemonPopulationHistory],
                      summary: dict[str, Any]) -> None:
    for rep in v.get("pop_reports") or []:
        company = str(rep.get("company") or "")
        for gr in rep.get("grades") or []:
            try:
                g = normalise(company, str(gr.get("grade")))
            except GradeError:
                continue
            n = gr.get("count")
            if (g.company, g.grade) not in KEEP_GRADES or not isinstance(n, int):
                continue
            k = (card.id, g.company, g.grade)
            if k in pops:
                pops[k].count, pops[k].as_of = n, now
            else:
                pops[k] = PokemonPopulation(card_id=card.id, grading_company=g.company, grade=g.grade, count=n, source=PROVIDER, as_of=now)
                db.add(pops[k])
            if k in hist:
                hist[k].count = n
            else:
                hist[k] = PokemonPopulationHistory(card_id=card.id, grading_company=g.company, grade=g.grade, count=n, source=PROVIDER, day=today)
                db.add(hist[k])
            summary["population_rows"] += 1


async def sync(db: Session, settings: Any, max_sets: int = 40, languages: tuple[str, ...] = ("EN", "JA"), set_ids: list[str] | None = None,
               transport: Any = None, max_requests: int = 150, now: datetime | None = None) -> dict[str, Any]:
    """Import / refresh up to `max_sets` sets that are due (not refreshed in the last 7 days), oldest sets first."""
    now = now or utcnow()
    today = now.date()
    p = provider(PROVIDER, settings, transport)
    assert isinstance(p, ScrydexProvider)
    p.max_requests = max_requests
    if not p.status()[0]:
        raise Unavailable(p.status()[1], "not_connected")
    log = PokemonSyncLog(provider=LOG_PROVIDER, started_at=now, status="running", requests=0, cache_hits=0, summary={}, entries=[])
    db.add(log)
    db.commit()
    summary: dict[str, Any] = {"sets": 0, "cards_seen": 0, "cards_created": 0, "cards_existing": 0, "valuations": 0, "valuations_new": 0,
                               "population_rows": 0, "inconsistent": 0, "sets_due": 0, "sets_total": 0, "credits_remaining": None}
    entries: list[dict[str, Any]] = []
    error = None
    try:
        usage = await p.usage()
        summary["credits_remaining"] = usage.get("credits_remaining")
        exps = expansions(await all_expansions(p), languages)
        summary["sets_total"] = len(exps)
        if set_ids is not None:
            due = [e for e in exps if e.get("id") in set_ids]
        else:
            done = refreshed_since(db, now - timedelta(days=REFRESH_DAYS))
            due = [e for e in exps if e.get("id") not in done]
        summary["sets_due"] = len(due)
        names = _set_names(db)
        by_variant, by, keys = _existing(db)
        for e in due[:max_sets]:
            pages = max(1, math.ceil((e.get("total") or 0) / 100))
            left = summary["credits_remaining"]
            if p.requests + pages > p.max_requests or (isinstance(left, int) and left - p.requests < pages):
                entries.append({"expansion": e["id"], "status": "deferred", "reason": "request cap or credits for this run"})
                break
            for attempt in range(4):
                try:
                    entry = await _sync_set(db, p, e, names, by_variant, by, keys, today, now, summary)
                    db.commit()
                    break
                except OperationalError as exc:  # another job holds the SQLite write lock: wait, rebuild what was rolled back, retry
                    db.rollback()
                    if "locked" not in str(exc) or attempt == 3:
                        raise
                    await asyncio.sleep(20 * (attempt + 1))
                    by_variant, by, keys = _existing(db)
            entries.append(entry)
            summary["sets"] += 1
    except Unavailable as exc:
        error = exc.reason
        db.rollback()
    log = db.get(PokemonSyncLog, log.id)
    assert log is not None
    summary["requests"], summary["cache_hits"] = p.requests, p.cache_hits
    log.finished_at, log.requests, log.cache_hits = utcnow(), p.requests, p.cache_hits
    log.summary, log.entries, log.error = summary, entries, error
    log.status = "error" if error and not summary["sets"] else "partial" if error else "ok"
    db.commit()
    history.invalidate()
    service.record_request(db, PROVIDER, p.requests, p.info.rate_limit)
    service.record_source(db, PROVIDER, summary["valuations"] + summary["population_rows"], error)
    db.commit()
    return {"id": log.id, "status": log.status, "summary": summary, "entries": entries, "error": error}


async def _sync_set(db: Session, p: ScrydexProvider, e: dict[str, Any], names: dict[str, str], by_variant: dict[tuple[str, str], PokemonCard],
                    by: dict[tuple[str, str, str], PokemonCard],
                    keys: set[str], today: date, now: datetime, summary: dict[str, Any]) -> dict[str, Any]:
    sid_set = str(e["id"])
    lang = "Japanese" if str(e.get("language_code")).upper() == "JA" else "English"
    en_set = str(((e.get("translation") or {}).get("en") or {}).get("name") or e.get("name") or sid_set)
    set_name = names.get(sid_set) or (f"Japanese {en_set}" if lang == "Japanese" else en_set)
    rel = _day(e.get("release_date"))
    total = e.get("printed_total") or e.get("total")
    is_promo_set = "promo" in en_set.lower() or sid_set.endswith("p") or "promo" in str(e.get("series") or "").lower()
    cards: list[dict[str, Any]] = []
    page = 1
    while True:
        b = await p.get(f"/pokemon/v1/expansions/{sid_set}/cards", {"page_size": 100, "page": page, "include": "prices,pop_reports", "select": FIELDS})
        cards += b.get("data") or []
        if not b.get("data") or len(cards) >= (b.get("total_count") or 0):
            break
        page += 1
    ids_here: list[int] = []
    pending: list[tuple[PokemonCard, str, dict[str, Any]]] = []
    created = existing = 0
    for c in cards:
        code = str(c.get("language_code") or e.get("language_code") or "").upper()
        if (code == "JA") != (lang == "Japanese"):
            continue  # never cross languages
        summary["cards_seen"] += 1
        en_name = str(((c.get("translation") or {}).get("en") or {}).get("name") or "") if lang == "Japanese" else ""
        name = en_name or str(c.get("name") or "")
        number = str(c.get("number") or "").strip()
        if lang == "Japanese":  # Japanese vintage "numbers" are Pokédex numbers: keep what is printed ("No.006"), never "6/102"
            card_number = str(c.get("printed_number") or "").strip()[:16] or None
        else:
            card_number = f"{number}/{total}" if number.isdigit() and total else (number[:16] or None)
        for v, edition, label, holo in printings(c.get("variants") or [], vintage=bool(rel and rel.year <= VINTAGE_UNTIL)):
            hit = by_variant.get((c["id"], str(v.get("name") or ""))) or by.get((c["id"], edition, label))
            if hit is not None:
                existing += 1
                if (hit.external_ids or {}).get("source") == SOURCE_TAG:
                    pending.append((hit, c["id"], v))
                    ids_here.append(hit.id)
                continue  # cards that were in the catalog before keep their own (per-card) Scrydex sync
            year = rel.year if rel else 0
            # identity by the Scrydex card id: printed numbers aren't unique (unnumbered promos; Japanese vintage prints
            # the Pokédex number, and Scrydex has e.g. Paras and Parasect both "No.046"). Cards that were in the catalog before
            # were matched above by their Scrydex link / set + number.
            ikey = catalog.identity_key(set_name, f"scrydex:{c['id']}", lang, edition, label, name)
            if ikey in keys or not name or not year:
                existing += 1
                continue
            card = PokemonCard(identity_key=ikey, name=name, pokemon_name=base_pokemon(name, c.get("supertype")), set_name=set_name,
                               set_code=sid_set, card_number=card_number, year=year, language=lang, edition=edition,
                               rarity=(str(c.get("rarity"))[:48] if c.get("rarity") else None), variant=label, holo_type=holo,
                               era="vintage" if year <= VINTAGE_UNTIL else "modern", is_promo=is_promo_set,
                               image_url=_image(c), image_source="scrydex" if _image(c) else None, release_date=rel,
                               external_ids={"scrydex": c["id"], "scrydex_variant": str(v.get("name") or ""), "source": SOURCE_TAG,
                                             "supertype": c.get("supertype"), **({"name_ja": c.get("name")} if lang == "Japanese" else {})})
            card.search_text = catalog.search_text(card) + (f" {c.get('name')}" if lang == "Japanese" else "")
            db.add(card)
            keys.add(ikey)
            by[(c["id"], edition, label)] = card
            by_variant[(c["id"], str(v.get("name") or ""))] = card
            pending.append((card, c["id"], v))
            created += 1
    db.flush()
    ids_here += [k.id for k, _, _ in pending if k.id not in ids_here]
    have = {(r.card_id, r.grading_company, r.grade): r for r in db.scalars(select(PokemonProviderValuation).where(
        PokemonProviderValuation.provider == PROVIDER, PokemonProviderValuation.card_id.in_(ids_here), PokemonProviderValuation.day == today))}
    extra: dict[tuple[int, str, str], PokemonProviderValuation] = {}
    for r in db.scalars(select(PokemonProviderValuation).where(  # the single kept row of each other grade / raw condition
            PokemonProviderValuation.provider == PROVIDER, PokemonProviderValuation.card_id.in_(ids_here), PokemonProviderValuation.kind == "current")):
        if (r.grading_company, r.grade) not in KEEP_GRADES:
            extra[(r.card_id, r.grading_company, r.grade)] = r
    pops = {(r.card_id, r.grading_company, r.grade): r for r in db.scalars(select(PokemonPopulation).where(
        PokemonPopulation.source == PROVIDER, PokemonPopulation.card_id.in_(ids_here)))}
    hist = {(r.card_id, r.grading_company, r.grade): r for r in db.scalars(select(PokemonPopulationHistory).where(
        PokemonPopulationHistory.source == PROVIDER, PokemonPopulationHistory.card_id.in_(ids_here), PokemonPopulationHistory.day == today))}
    priced = 0
    for card, sid, v in pending:
        before = summary["valuations"]
        _store_prices(db, card, sid, v, today, now, have, summary, extra)
        priced += int(summary["valuations"] > before)
        _store_population(db, card, v, today, now, pops, hist, summary)
    summary["cards_created"] += created
    summary["cards_existing"] += existing
    return {"expansion": sid_set, "name": set_name, "language": lang, "status": "ok", "cards": len(cards), "created": created,
            "existing": existing, "priced": priced}
