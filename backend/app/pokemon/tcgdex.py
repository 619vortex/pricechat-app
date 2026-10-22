"""Import vintage cards (1996-2003) from TCGdex into the catalog: identity, metadata and images. Never prices.

No duplicates: a TCGdex card is first matched to existing catalog cards by set id + number + language (every edition row
of that card is linked: 1st Edition / Shadowless / Unlimited). Existing rows only get EMPTY fields filled (image,
release date, rarity); names, sets, numbers, editions, sales, populations and history are never changed. A new catalog
card is created only when nothing matches, as one asset per card: its 1st Edition print when TCGdex says one exists
(the vintage headline asset), otherwise Unlimited. Japanese catalog cards are linked through the Japanese set and the
exact Pokédex number.
"""

import re
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.pokemon import PokemonCard
from app.pokemon import catalog, service
from app.providers.pokemon import Unavailable, provider
from app.providers.pokemon.tcgdex import TCGdexProvider, image_url

# English WotC-era sets in import priority (iconic first). Only holo cards are imported from each.
PRIORITY_SETS = ("base1", "base2", "base3", "base5", "neo1", "neo4", "gym1", "gym2", "neo2", "neo3", "lc", "base4",
                 "ecard1", "ecard2", "ecard3")
VINTAGE_UNTIL = 2003
# Japanese catalog set name -> TCGdex Japanese set id
JA_SETS: dict[str, str] = {
    "japanese base set (expansion pack)": "PMCG1",
    "japanese neo genesis (gold, silver, to a new world...)": "neo1",
}
_PREFIX = re.compile(r"^(?:dark|light|shining)\s+|^[\w. ]+?'s\s+", re.I)


def pokemon_name(name: str) -> str:
    """'Dark Charizard' -> 'Charizard', "Brock's Onix" -> 'Onix'."""
    return _PREFIX.sub("", name).strip() or name


def _num(local_id: Any) -> int | None:
    digits = re.sub(r"\D", "", str(local_id or ""))
    return int(digits) if digits else None


def _same_name(catalog_name: str, tcgdex_name: str) -> bool:
    a, b = (re.sub(r"[^a-z]", "", s.lower().replace("★", "")) for s in (catalog_name, tcgdex_name))
    return bool(a and b) and (a == b or a.endswith(b) or b.endswith(a))


def _release(s: dict[str, Any]) -> date | None:
    try:
        return date.fromisoformat(str(s.get("releaseDate"))[:10])
    except ValueError:
        return None


def _fill(card: PokemonCard, c: dict[str, Any], s: dict[str, Any], tcgdex_id: str, out: dict[str, Any]) -> None:
    """Link + fill EMPTY fields only. Never overwrites."""
    ids = dict(card.external_ids or {})
    if ids.get("tcgdex") not in (None, tcgdex_id):
        out["skipped"].append({"tcgdex": tcgdex_id, "card_id": card.id, "reason": f"already linked to {ids['tcgdex']}"})
        return
    ids["tcgdex"] = tcgdex_id
    card.external_ids = ids
    img = image_url(c.get("image"))
    if not card.image_url and img:
        card.image_url, card.image_source = img, "tcgdex"
        out["images_added"] += 1
        out["fields_filled"] += 1
    if card.release_date is None and _release(s):
        card.release_date = _release(s)
        out["fields_filled"] += 1
    if not card.rarity and c.get("rarity"):
        card.rarity = c["rarity"]
        out["fields_filled"] += 1
    card.search_text = catalog.search_text(card)
    out["catalog_rows_linked"] += 1


def _new_card(c: dict[str, Any], s: dict[str, Any]) -> PokemonCard | None:
    rel = _release(s)
    number, total = _num(c.get("localId")), (s.get("cardCount") or {}).get("official")
    if rel is None or number is None or not c.get("name"):
        return None
    variants = c.get("variants") or {}
    edition = "1st Edition" if variants.get("firstEdition") else "Unlimited"
    rarity = c.get("rarity")
    if rarity == "Rare" and variants.get("holo"):
        rarity = "Rare Holo"
    spec = dict(name=c["name"], pokemon_name=pokemon_name(c["name"]) if c.get("category", "Pokemon") == "Pokemon" else c["name"], set_name=s.get("name") or s["id"], set_code=s["id"],
                card_number=f"{number}/{total}" if total else str(number), year=rel.year, language="English", edition=edition,
                rarity=rarity, holo_type="Holo" if variants.get("holo") else None, variant="", era="vintage", is_promo=False)
    card = PokemonCard(identity_key=catalog.identity_key(spec["set_name"], spec["card_number"], "English", edition, "", spec["name"]),
                       external_ids={"tcgdex": c["id"]}, image_url=image_url(c.get("image")),
                       image_source="tcgdex" if c.get("image") else None, release_date=rel, **spec)
    card.search_text = catalog.search_text(card)
    return card


async def import_vintage(db: Session, settings: Any, limit: int = 100, transport: Any = None) -> dict[str, Any]:
    """Link/import up to `limit` distinct vintage TCGdex cards. Returns exactly what happened, card by card."""
    catalog.ensure_catalog(db)
    api = provider("tcgdex", settings, transport)
    assert isinstance(api, TCGdexProvider)
    out: dict[str, Any] = {"tcgdex_cards": 0, "linked_existing": 0, "created": 0, "catalog_rows_linked": 0, "fields_filled": 0,
                           "images_added": 0, "japanese_linked": 0, "skipped": [], "error": None, "cards": []}
    done: set[str] = set()
    sets: dict[str, dict[str, Any]] = {}
    dex: dict[str, int] = {}  # English Pokémon name -> Pokédex number (for Japanese matching)
    cards = list(db.scalars(select(PokemonCard).order_by(PokemonCard.id)))
    by_key: dict[tuple[str, int, str], list[PokemonCard]] = {}
    for k in cards:
        n = _num((k.card_number or "").split("/")[0])
        if k.set_code and n is not None:
            by_key.setdefault((k.set_code, n, k.language), []).append(k)
    have = {k.identity_key for k in cards}

    async def set_of(sid: str) -> dict[str, Any]:
        if sid not in sets:
            sets[sid] = await api.set_info(sid)
        return sets[sid]

    async def take(tid: str, existing: list[PokemonCard]) -> None:
        db.commit()  # what's done so far is saved before each TCGdex read: no write transaction (SQLite's only write lock) waits on it
        c = await api.card(tid)
        s = await set_of(c["set"]["id"])
        rel = _release(s)
        if rel and rel.year > VINTAGE_UNTIL:
            out["skipped"].append({"tcgdex": tid, "reason": f"released {rel.year}, after {VINTAGE_UNTIL}"})
            return
        for d in c.get("dexId") or []:
            dex.setdefault(pokemon_name(c.get("name", "")).lower(), int(d))
        rows = [k for k in existing if _same_name(k.name, c.get("name", ""))]
        if existing and not rows:
            out["skipped"].append({"tcgdex": tid, "reason": f"catalog has {existing[0].name} at this number, TCGdex has {c.get('name')}"})
            return
        if rows:
            for k in rows:
                _fill(k, c, s, tid, out)
            out["linked_existing"] += 1
            out["cards"].append({"tcgdex": tid, "name": c.get("name"), "action": "linked", "card_ids": [k.id for k in rows]})
        else:
            new = _new_card(c, s)
            if new is None:
                out["skipped"].append({"tcgdex": tid, "reason": "missing set date, number or name"})
                return
            if new.identity_key in have:
                out["skipped"].append({"tcgdex": tid, "reason": "same identity already in the catalog"})
                return
            db.add(new)
            db.flush()
            have.add(new.identity_key)
            out["created"] += 1
            out["images_added"] += 1 if new.image_url else 0
            out["cards"].append({"tcgdex": tid, "name": new.name, "action": "created", "card_ids": [new.id], "edition": new.edition})
        done.add(tid)
        out["tcgdex_cards"] += 1

    try:
        # 1. existing English vintage catalog cards first (link, fill gaps)
        for (sid, n, lang), rows in sorted(by_key.items()):
            if lang != "English" or min(r.year for r in rows) > VINTAGE_UNTIL or out["tcgdex_cards"] >= limit:
                continue
            try:
                await take(f"{sid}-{n}", rows)
            except Unavailable as exc:
                if exc.code != "not_found":
                    raise
                out["skipped"].append({"tcgdex": f"{sid}-{n}", "reason": "not in TCGdex"})
        # 2. Japanese catalog cards: the Japanese set + exact Pokédex number
        for k in cards:
            if k.language != "Japanese" or (k.external_ids or {}).get("tcgdex") or out["tcgdex_cards"] >= limit:
                continue
            ja_set = JA_SETS.get(k.set_name.lower())
            if not ja_set:
                out["skipped"].append({"card_id": k.id, "reason": f"{k.set_name}: no matching TCGdex Japanese set"})
                continue
            n = dex.get(k.pokemon_name.lower())
            if n is None:
                out["skipped"].append({"card_id": k.id, "reason": f"no Pokédex number known for {k.pokemon_name}"})
                continue
            hits = []
            db.commit()
            for brief in await api.set_by_dex(ja_set, n):
                c = await api.card(brief["id"], "ja")
                if n in (c.get("dexId") or []) and (c.get("variants") or {}).get("holo"):
                    hits.append(c)
            if len(hits) != 1:
                out["skipped"].append({"card_id": k.id, "reason": f"{len(hits)} Japanese {ja_set} holo cards with Pokédex #{n}"})
                continue
            _fill(k, hits[0], await api.set_info(ja_set, "ja"), f"ja:{hits[0]['id']}", out)
            out["japanese_linked"] += 1
            out["tcgdex_cards"] += 1
            out["cards"].append({"tcgdex": f"ja:{hits[0]['id']}", "name": k.name, "action": "linked", "card_ids": [k.id]})
        # 3. holo cards from the priority sets until the limit
        for sid in PRIORITY_SETS:
            if out["tcgdex_cards"] >= limit:
                break
            db.commit()
            for brief in sorted(await api.set_holos(sid), key=lambda b: (_num(b.get("localId")) or 0)):
                if out["tcgdex_cards"] >= limit:
                    break
                if brief["id"] in done:
                    continue
                await take(brief["id"], by_key.get((sid, _num(brief.get("localId")) or -1, "English"), []))
    except Unavailable as exc:
        out["error"] = exc.reason
    db.commit()
    service.record_request(db, "tcgdex", api.requests, None)
    service.record_source(db, "tcgdex", out["created"] + out["catalog_rows_linked"], out["error"])
    out["requests"], out["cache_hits"] = api.requests, api.cache_hits
    return out
