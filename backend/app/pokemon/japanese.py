"""Japanese vintage cards (1996-2003): linking catalog cards to Scrydex's JAPANESE cards.

Japanese vintage cards mostly have no printed set number, so the Scrydex id can't be derived like English ones
("base1-4"). A catalog card is linked only when exactly ONE card in the matching Scrydex Japanese expansion has the same
English name (Scrydex's translation). Japanese cards are never linked to English cards, prices or populations.
"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.pokemon import PokemonCard
from app.pokemon import service
from app.pokemon.scrydex_sync import JA_NAMES
from app.providers.pokemon import provider
from app.providers.pokemon.scrydex import ScrydexProvider

# catalog set name (lower case) -> Scrydex Japanese expansion
SCRYDEX_SETS: dict[str, str] = {
    "japanese base set (expansion pack)": "base1_ja",
    "japanese jungle": "base2_ja",
    "japanese fossil (mystery of the fossils)": "base3_ja",
    "japanese team rocket (rocket gang)": "base4_ja",
    "japanese gym heroes (leaders' stadium)": "gym1_ja",
    "japanese gym challenge (challenge from the darkness)": "gym2_ja",
    "japanese neo genesis (gold, silver, to a new world...)": "neo1_ja",
    "japanese neo discovery (crossing the ruins...)": "neo2_ja",
    "japanese neo revelation (awakening legends)": "neo3_ja",
    "japanese neo destiny (darkness, and to light...)": "neo4_ja",
    "corocoro comic promo": "miscp_ja",
    "japanese promo": "miscp_ja",
    "japanese p promo": "miscpp_ja",
}


def english_name(c: dict[str, Any]) -> str:
    return str(((c.get("translation") or {}).get("en") or {}).get("name") or "")


async def expansion_cards(p: ScrydexProvider, exp: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    page = 1
    while True:
        b = await p.get(f"/pokemon/v1/expansions/{exp}/cards",
                        {"page_size": 100, "page": page, "select": "id,name,translation,number,printed_number,language_code,national_pokedex_numbers"})
        rows += b.get("data") or []
        if not b.get("data") or len(rows) >= (b.get("total_count") or 0):
            return rows
        page += 1


async def map_scrydex(db: Session, settings: Any, card_ids: list[int] | None = None, transport: Any = None, max_requests: int = 10) -> dict[str, Any]:
    p = provider("scrydex", settings, transport)
    assert isinstance(p, ScrydexProvider)
    p.max_requests = max_requests
    q = select(PokemonCard).where(PokemonCard.language == "Japanese").order_by(PokemonCard.id)
    if card_ids is not None:
        q = q.where(PokemonCard.id.in_(card_ids))
    out: dict[str, Any] = {"linked": [], "already": [], "unmatched": []}
    cache: dict[str, list[dict[str, Any]]] = {}
    for card in db.scalars(q):
        have = str((card.external_ids or {}).get("scrydex") or "")
        if have.split("-")[0].endswith("_ja"):
            out["already"].append({"card_id": card.id, "card": card.name, "scrydex_id": have})
            continue
        exp = SCRYDEX_SETS.get(card.set_name.lower())
        if exp is None:
            out["unmatched"].append({"card_id": card.id, "card": card.name, "reason": f"no Scrydex Japanese set known for {card.set_name}"})
            continue
        if exp not in cache:
            cache[exp] = await expansion_cards(p, exp)
        want = JA_NAMES.get(card.name.lower(), card.name).lower()
        hits = [c for c in cache[exp] if english_name(c).lower() == want and str(c.get("language_code") or "JA").upper() == "JA"]
        if len(hits) != 1:
            out["unmatched"].append({"card_id": card.id, "card": card.name, "reason": f"{len(hits)} cards named {want!r} in {exp}",
                                     "candidates": [h.get("id") for h in hits][:8]})
            continue
        card.external_ids = {**(card.external_ids or {}), "scrydex": hits[0]["id"]}
        out["linked"].append({"card_id": card.id, "card": card.name, "scrydex_id": hits[0]["id"], "scrydex_name": hits[0].get("name")})
    db.commit()
    service.record_request(db, "scrydex", p.requests, p.info.rate_limit)
    db.commit()
    out["requests"], out["cache_hits"] = p.requests, p.cache_hits
    return out
