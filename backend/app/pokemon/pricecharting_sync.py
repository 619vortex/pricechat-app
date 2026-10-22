"""PriceCharting sync: exact product matching -> reference prices per grade (never sales, never Price Chat's own price).

PriceCharting product = console-name ("Pokemon Base Set") + product-name ("Charizard [1st Edition] #4"). A catalog card matches
a product only when ALL of these agree: the set (console), the card name, the number, and the bracket tags exactly:
  1st Edition -> [1st Edition]; Shadowless -> [Shadowless]; Unlimited -> no edition tag; Gold Star / No Rarity -> that tag.
Any other tag ([Prerelease], [No Symbol], [Reverse Holo], [1999-2000], [Black Dot Error]...) is a different product. Exactly one
product must fit; otherwise the card is logged as unmatched (or ambiguous), never guessed. Every run is logged per card.
"""

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.pokemon import PokemonCard, PokemonSyncLog
from app.pokemon import service
from app.providers.pokemon import Unavailable, provider
from app.providers.pokemon.pricecharting import PriceChartingProvider

PROVIDER = "pricecharting"
# catalog set name -> PriceCharting console-name (only where it is not simply "Pokemon <set>")
CONSOLES: dict[str, str] = {
    "ex deoxys": "pokemon deoxys",
    "ex dragon frontiers": "pokemon dragon frontiers",
    "japanese base set (expansion pack)": "pokemon japanese expansion pack",
    "japanese neo genesis (gold, silver, to a new world...)": "pokemon japanese gold, silver, new world",
    "corocoro comic promo": "pokemon japanese promo",
}
NAMES: dict[str, str] = {"pikachu illustrator": "illustrator pikachu"}  # catalog name -> PriceCharting product name
EDITION_TAGS = {"1st Edition": "1st edition", "Shadowless": "shadowless"}
VARIANT_TAGS = {"gold star": "gold star", "no rarity": "no rarity"}  # catalog variants PriceCharting writes as a tag
_PRODUCT = re.compile(r"^(?P<name>.*?)\s*(?P<tags>(?:\[[^\]]+\]\s*)*)(?:#(?P<num>[\w-]+))?\s*$")


def console_for(card: PokemonCard) -> str | None:
    s = card.set_name.lower()
    out = CONSOLES.get(s, f"pokemon {s}")
    # never cross languages: Japanese cards -> "pokemon japanese ..." sets only; English cards never those
    return out if ("japanese" in out) == (card.language == "Japanese") else None


def parse_product(p: dict[str, Any]) -> tuple[str, set[str], str | None]:
    m = _PRODUCT.match(str(p.get("product-name") or "").strip())
    if not m:
        return "", set(), None
    tags = {t.strip().lower() for t in re.findall(r"\[([^\]]+)\]", m.group("tags") or "")}
    return m.group("name").strip().lower(), tags, m.group("num")


def wanted_tags(card: PokemonCard) -> set[str]:
    tags = set()
    if card.edition in EDITION_TAGS:
        tags.add(EDITION_TAGS[card.edition])
    if card.variant and card.variant.lower() in VARIANT_TAGS:
        tags.add(VARIANT_TAGS[card.variant.lower()])
    return tags


def _norm_name(s: str) -> str:
    """Case, spacing and punctuation don't decide identity ("Here Comes Team Rocket!" == "Here Comes Team Rocket")."""
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s&'-]", " ", s.replace("★", "").lower())).strip()


def matches(card: PokemonCard, p: dict[str, Any]) -> tuple[bool, str]:
    if str(p.get("console-name") or "").strip().lower() != console_for(card):
        return False, f"set {p.get('console-name')}"
    name, tags, num = parse_product(p)
    name = _norm_name(name)
    want_name = NAMES.get(_norm_name(card.name), _norm_name(card.name))
    if name != want_name:
        return False, f"name {name}"
    if card.card_number:
        mine = str(int(re.sub(r"\D", "", card.card_number.split("/")[0]) or 0))
        if (num or "").lstrip("0") != mine:
            return False, f"number #{num}"
    if tags != wanted_tags(card):
        return False, f"tags {sorted(tags) or 'none'} (need {sorted(wanted_tags(card)) or 'none'})"
    return True, "exact"


def query_for(card: PokemonCard) -> str:
    name = NAMES.get(_norm_name(card.name), card.name.replace("★", "").strip())
    num = re.sub(r"\D", "", (card.card_number or "").split("/")[0])
    return " ".join(x for x in (name, (console_for(card) or "").removeprefix("pokemon "), num) if x)


async def sync(db: Session, settings: Any, card_ids: list[int] | None = None, transport: Any = None, max_requests: int = 400) -> dict[str, Any]:
    from app.pokemon import history
    from app.pokemon.scrydex_sync import log_out

    pc = provider(PROVIDER, settings, transport)
    assert isinstance(pc, PriceChartingProvider)
    if not pc.status()[0]:
        raise Unavailable(pc.status()[1], "not_connected")
    pc.requests = 0
    now = utcnow()
    log = PokemonSyncLog(provider=PROVIDER, started_at=now, status="running", requests=0, cache_hits=0, summary={}, entries=[])
    db.add(log)
    db.commit()
    q = select(PokemonCard).order_by(PokemonCard.id)
    if card_ids is not None:
        q = q.where(PokemonCard.id.in_(card_ids))
    cards = [c for c in db.scalars(q) if card_ids is not None or (c.external_ids or {}).get("source") != "scrydex-catalog"]
    entries: list[dict[str, Any]] = []
    summary = {"cards_requested": len(cards), "matched": 0, "unmatched": 0, "ambiguous": 0, "prices_stored": 0, "psa10_prices": 0}
    error = None
    try:
        for c in cards:
            if pc.requests >= max_requests:
                raise Unavailable(f"Stopped at the request cap for this sync ({max_requests}).", "budget")
            entry: dict[str, Any] = {"card_id": c.id, "card": f"{c.name} {c.set_name} {c.edition}"}
            pid = (c.external_ids or {}).get("pricecharting")
            product = None
            if pid:
                product = await pc.product(str(pid))
                ok, why = matches(c, product)
                if not ok:  # a stored link that no longer fits (e.g. an earlier fuzzy link): re-match from scratch
                    entry["relinked_from"] = pid
                    pid, product = None, None
            if not pid:
                hits = [p for p in await pc.search_raw(query_for(c)) if matches(c, p)[0]]
                if len(hits) != 1:
                    key = "ambiguous" if hits else "unmatched"
                    summary[key] += 1
                    entry.update(status=key, reason=f"{len(hits)} PriceCharting products fit exactly" if hits else f"no exact product for '{query_for(c)}'",
                                 candidates=[f"{p.get('console-name')} · {p.get('product-name')}" for p in hits[:4]])
                    entries.append(entry)
                    continue
                pid = str(hits[0]["id"])
                product = await pc.product(pid)
            c.external_ids = {**(c.external_ids or {}), PROVIDER: pid}
            prices = await pc.reference_prices(pid, product)
            stored = service.store_reference_prices(db, PROVIDER, c.id, pid, prices)
            summary["matched"] += 1
            summary["prices_stored"] += len(prices)
            summary["psa10_prices"] += int(any(label == "PSA 10" for label, _, _ in prices))
            entry.update(status="matched", product=f"{product.get('console-name')} · {product.get('product-name')}", product_id=pid,
                         prices={label: cents / 100 for label, _, cents in prices}, sales_volume=product.get("sales-volume"), new_rows=stored)
            entries.append(entry)
            db.commit()
    except Unavailable as exc:
        error = exc.reason
        db.rollback()
    log = db.get(PokemonSyncLog, log.id)
    assert log is not None
    log.finished_at, log.requests, log.summary, log.entries, log.error = utcnow(), pc.requests, summary, entries, error
    log.status = "error" if error and not summary["matched"] else "partial" if error or summary["unmatched"] or summary["ambiguous"] else "ok"
    db.commit()
    history.invalidate()
    service.record_request(db, PROVIDER, pc.requests, pc.info.rate_limit)
    service.record_source(db, PROVIDER, summary["prices_stored"], error)
    db.commit()
    return log_out(log)
