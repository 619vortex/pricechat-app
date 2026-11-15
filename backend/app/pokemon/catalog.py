"""The Pokémon master catalog: identity of each collectible, seeding and search.

The seed is ~20 iconic cards for building and testing the catalog and UI. Identity only: no prices, populations or
images are made up (images come from the Pokémon TCG API when the owner runs "Fetch images"). Japanese cards and
promos it doesn't cover keep no image. `set_code` is the Pokémon TCG API set id where one exists.
"""

from dataclasses import dataclass

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.pokemon import PokemonCard
from app.pokemon.grades import Grade, GradeError, normalise


def identity_key(set_name: str, number: str | None, language: str, edition: str, variant: str, name: str) -> str:
    """Two cards are the same asset only if all of these match (1st Edition vs Unlimited are different assets)."""
    parts = [set_name, number or f"#{name}", language, edition, variant]
    return "|".join(re.sub(r"\s+", " ", p.strip().lower()) for p in parts)[:160]


def _c(name: str, pokemon: str, set_name: str, set_code: str | None, number: str | None, year: int, edition: str, rarity: str | None,
       holo: str | None = "Holo", variant: str = "", language: str = "English", promo: bool = False, era: str = "vintage") -> dict[str, Any]:
    return dict(name=name, pokemon_name=pokemon, set_name=set_name, set_code=set_code, card_number=number, year=year, edition=edition,
                rarity=rarity, holo_type=holo, variant=variant, language=language, is_promo=promo, era=era)


SEED: list[dict[str, Any]] = [
    _c("Charizard", "Charizard", "Base Set", "base1", "4/102", 1999, "1st Edition", "Rare Holo"),
    _c("Charizard", "Charizard", "Base Set", "base1", "4/102", 1999, "Shadowless", "Rare Holo"),
    _c("Charizard", "Charizard", "Base Set", "base1", "4/102", 1999, "Unlimited", "Rare Holo"),
    _c("Blastoise", "Blastoise", "Base Set", "base1", "2/102", 1999, "1st Edition", "Rare Holo"),
    _c("Blastoise", "Blastoise", "Base Set", "base1", "2/102", 1999, "Unlimited", "Rare Holo"),
    _c("Venusaur", "Venusaur", "Base Set", "base1", "15/102", 1999, "1st Edition", "Rare Holo"),
    _c("Venusaur", "Venusaur", "Base Set", "base1", "15/102", 1999, "Unlimited", "Rare Holo"),
    _c("Mewtwo", "Mewtwo", "Base Set", "base1", "10/102", 1999, "1st Edition", "Rare Holo"),
    _c("Dark Charizard", "Charizard", "Team Rocket", "base5", "4/82", 2000, "1st Edition", "Rare Holo"),
    _c("Lugia", "Lugia", "Neo Genesis", "neo1", "9/111", 2000, "1st Edition", "Rare Holo"),
    _c("Lugia", "Lugia", "Neo Genesis", "neo1", "9/111", 2000, "Unlimited", "Rare Holo"),
    _c("Shining Charizard", "Charizard", "Neo Destiny", "neo4", "107/105", 2002, "1st Edition", "Rare Shining"),
    _c("Charizard", "Charizard", "Skyridge", "ecard3", "146/144", 2003, "Unlimited", "Rare Secret", variant="Crystal"),
    _c("Rayquaza ★", "Rayquaza", "EX Deoxys", "ex8", "107/107", 2005, "Unlimited", "Rare Holo Star", holo="Gold Star", variant="Gold Star"),
    _c("Charizard ★", "Charizard", "EX Dragon Frontiers", "ex15", "100/101", 2006, "Unlimited", "Rare Holo Star", holo="Gold Star",
       variant="Gold Star"),
    _c("Umbreon ★", "Umbreon", "POP Series 5", "pop5", "17/17", 2007, "Unlimited", "Rare Holo Star", holo="Gold Star", variant="Gold Star"),
    _c("Espeon ★", "Espeon", "POP Series 5", "pop5", "16/17", 2007, "Unlimited", "Rare Holo Star", holo="Gold Star", variant="Gold Star"),
    _c("Pikachu Illustrator", "Pikachu", "CoroCoro Comic Promo", None, None, 1998, "—", "Promo", variant="Illustrator", language="Japanese",
       promo=True),
    _c("Charizard", "Charizard", "Japanese Base Set (Expansion Pack)", None, None, 1996, "—", "Rare Holo", variant="No Rarity",
       language="Japanese"),
    _c("Lugia", "Lugia", "Japanese Neo Genesis (Gold, Silver, to a New World...)", None, None, 2000, "—", "Rare Holo", language="Japanese"),
]


def search_text(c: PokemonCard) -> str:
    words = [c.name, c.pokemon_name, c.set_name, c.set_code or "", c.card_number or "", (c.card_number or "").split("/")[0],
             str(c.year), c.language, c.edition, c.variant, c.rarity or "", c.holo_type or "", c.era, "promo" if c.is_promo else ""]
    if c.edition == "1st Edition":
        words += ["1st", "first", "1st ed"]
    if c.language == "Japanese":
        words += ["jp", "jpn", "japan"]
    if "★" in c.name:
        words += ["star", "gold star"]
    return " ".join(w for w in words if w).lower().replace("★", "")


def ensure_catalog(db: Session) -> int:
    """Insert seed cards that aren't there yet (by identity). Existing rows are never overwritten.
    Runs on most public market requests: it looks up only the seed's keys, never the whole ~50k-card catalogue."""
    keys = [identity_key(s["set_name"], s["card_number"], s["language"], s["edition"], s["variant"], s["name"]) for s in SEED]
    have = set(db.scalars(select(PokemonCard.identity_key).where(PokemonCard.identity_key.in_(keys))))
    added = 0
    for spec, key in zip(SEED, keys):
        if key in have:
            continue
        card = PokemonCard(identity_key=key, external_ids={}, **spec)
        card.search_text = search_text(card)
        db.add(card)
        added += 1
    if added:
        db.commit()
    return added


# --- search --------------------------------------------------------------------------------------------------------

_GRADE_RE = re.compile(r"\b(psa|bgs|cgc|beckett)\s*(10|[1-9](?:\.5)?)\b", re.I)
_STOP = {"pokemon", "pokémon", "card", "the", "edition"}


def parse_query(q: str) -> tuple[list[str], Grade | None]:
    """'Charizard 1st edition PSA 10' -> (['charizard', '1st'], PSA 10). A grade in the query becomes a grade filter."""
    grade = None
    m = _GRADE_RE.search(q or "")
    if m:
        try:
            grade = normalise(m.group(1), m.group(2))
        except GradeError:
            grade = None
        q = q[: m.start()] + " " + q[m.end():]
    words = [w for w in re.split(r"[\s,]+", (q or "").lower().replace("★", "")) if w and w not in _STOP]
    return words, grade


@dataclass(frozen=True, slots=True)
class LiteCard:
    """What whole-catalogue scans need, without loading ~50k ORM rows per request."""
    id: int
    name: str
    pokemon_name: str
    set_name: str
    set_code: str | None
    year: int
    edition: str
    language: str
    image_url: str | None
    search_text: str
    external_ids: dict[str, Any]  # only "supertype" (Pokémon / Trainer / Energy)


def lite(db: Session, refresh: bool = False) -> list[LiteCard]:
    """Every catalog card, light. Cached 15 min; imports and syncs clear it at once (history.invalidate). refresh: the
    warm-up job rebuilds it before it expires (market.warm)."""
    from app.pokemon import history

    def build() -> list[LiteCard]:
        rows = db.execute(select(PokemonCard.id, PokemonCard.name, PokemonCard.pokemon_name, PokemonCard.set_name, PokemonCard.set_code,
                                 PokemonCard.year, PokemonCard.edition, PokemonCard.language, PokemonCard.image_url, PokemonCard.search_text,
                                 PokemonCard.external_ids)).all()
        return [LiteCard(r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], r[9] or "",
                         {"supertype": (r[10] or {}).get("supertype")} if (r[10] or {}).get("supertype") else {}) for r in rows]

    from sqlalchemy import func

    n, top = db.execute(select(func.count(PokemonCard.id), func.max(PokemonCard.id))).one()  # any added / removed card rebuilds it
    if refresh:
        return history.refresh(("catalog-lite", n, top), build, ttl=900.0, older_than=480.0)
    return history.cached(("catalog-lite", n, top), build, ttl=900.0)


def _load(db: Session, ids: list[int]) -> list[PokemonCard]:
    by: dict[int, PokemonCard] = {}
    for i in range(0, len(ids), 500):  # SQLite caps bound variables per statement; the market table asks for thousands
        by.update({c.id: c for c in db.scalars(select(PokemonCard).where(PokemonCard.id.in_(ids[i:i + 500])))})
    return [by[i] for i in ids if i in by]


def search(db: Session, q: str, limit: int = 50) -> tuple[list[PokemonCard], Grade | None]:
    words, grade = parse_query(q)
    cards = lite(db)
    if not words:
        return _load(db, [c.id for c in sorted(cards, key=lambda c: (c.year, c.set_name, c.name))[:limit]]), grade
    hits = []
    for c in cards:
        if all(w in c.search_text for w in words):
            exact = sum(1 for w in words if w in (c.pokemon_name.lower(), c.name.lower().replace("★", "").strip()))
            hits.append((-exact, c.year, c.set_name, c.id))
    return _load(db, [h[-1] for h in sorted(hits)][:limit]), grade
