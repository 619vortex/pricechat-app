"""Provider syncs. Each provider runs on its own: one failing (no keys, no access, limits, outage) never stops the others,
and its error is recorded on the Data Sources page. Nothing here is scraped; nothing is priced from listings."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.pokemon import PokemonCard
from app.pokemon import matching, service
from app.pokemon.fx import FxRates
from app.providers.pokemon import Unavailable, provider

EBAY_GRADES = ("PSA 10",)  # the graded markets searched per card (raw listings are too ambiguous to match)


async def sync_ebay(db: Session, settings: Any, transport: Any = None) -> dict[str, Any]:
    from app.providers.pokemon.ebay import EbayProvider, query_for

    ebay = provider("ebay", settings, transport)
    assert isinstance(ebay, EbayProvider)
    if not ebay.status()[0]:
        raise Unavailable(ebay.status()[1], "not_connected")
    fx = FxRates(settings.fx_api_base, transport)
    out: dict[str, Any] = {"listings": {"seen": 0, "new": 0, "matched": 0, "review": 0, "rejected": 0}, "sold": None, "queries": 0}
    sold_ok = True
    error = None
    try:
        from app.pokemon import ebay_cards, top100

        ids = top100.ebay_targets(db, limit=settings.ebay_cards_per_sync)
        for card in db.scalars(select(PokemonCard).where(PokemonCard.id.in_(ids))):
            for grade in EBAY_GRADES:
                q = query_for(card, grade)
                out["queries"] += 1
                got = service.store_observations(db, "ebay", "active", await ebay.get_listing_data(q), cards=ebay_cards.candidates(db, card))
                for k in out["listings"]:
                    out["listings"][k] += got.get(k, 0)
                if sold_ok:
                    try:
                        sold = service.store_observations(db, "ebay", "sold", await ebay.get_sales(q), fx, cards=ebay_cards.candidates(db, card))
                        out["sold"] = {k: (out["sold"] or {}).get(k, 0) + v for k, v in sold.items()}
                    except Unavailable as exc:
                        if exc.code != "no_access":
                            raise
                        sold_ok, out["sold"] = False, exc.reason  # SOLD DATA UNAVAILABLE FROM CURRENT EBAY API ACCESS
    except Unavailable as exc:
        error = exc.reason
    service.record_request(db, "ebay", ebay.requests, ebay.info.rate_limit)
    imported = out["listings"]["new"] + (out["sold"].get("sales", 0) if isinstance(out["sold"], dict) else 0)
    service.record_source(db, "ebay", imported, error)
    out["error"] = error
    return out


async def sync_pricecharting(db: Session, settings: Any, transport: Any = None, card_ids: list[int] | None = None) -> dict[str, Any]:
    """Reference prices for catalog cards matched EXACTLY to a PriceCharting product (see pricecharting_sync). Returns the run log."""
    from app.pokemon import pricecharting_sync

    out = await pricecharting_sync.sync(db, settings, card_ids, transport)
    return {**out, "priced": out["summary"].get("matched", 0)}


def gemrate_query(card: PokemonCard) -> str:
    """GemRate's structured format: '<year> <set name> <subject> <parallel> <number>'."""
    parallel = " ".join(x for x in (card.edition if card.edition not in ("—", "Unlimited") else "", card.variant, card.holo_type or "") if x)
    number = (card.card_number or "").split("/")[0]
    return " ".join(x for x in (str(card.year), "Pokemon", card.set_name.split("(")[0].strip(), card.name.replace("★", "").strip(), parallel, number) if x)


async def sync_gemrate(db: Session, settings: Any, transport: Any = None, history_too: bool = True) -> dict[str, Any]:
    """Population per exact grade from GemRate for catalog cards linked with certainty, plus population history when the plan
    includes it. A card is linked when a search hit's description matches THIS catalog card (matcher, no grade needed), or
    when an admin set external_ids["gemrate"]. Everything else stays unlinked (population "—"), never guessed."""
    from app.pokemon import history
    from app.pokemon.grades import Grade
    from app.providers.pokemon.gemrate import GemRateProvider

    gr = provider("gemrate", settings, transport)
    assert isinstance(gr, GemRateProvider)
    if not gr.status()[0]:
        raise Unavailable(gr.status()[1], "not_connected")
    cards = list(db.scalars(select(PokemonCard).order_by(PokemonCard.id)))
    out: dict[str, Any] = {"linked": 0, "unlinked": [], "population_rows": 0, "history_rows": 0, "history": None}
    error = None
    history_ok = history_too
    try:
        for card in cards:
            gid = (card.external_ids or {}).get("gemrate")
            if not gid:
                for hit in await gr.search_cards(gemrate_query(card)):
                    m = matching.match(hit.name, cards, need_grade=False)
                    if m.status == "matched" and m.card_id == card.id:
                        gid = hit.provider_id
                        card.external_ids = {**(card.external_ids or {}), "gemrate": gid}
                        break
            if not gid:
                out["unlinked"].append(card.id)
                continue
            out["linked"] += 1
            for rec in await gr.get_population(gid):
                service.store_population(db, card.id, Grade(rec.grading_company, rec.grade), rec.count, "gemrate", rec.as_of)
                out["population_rows"] += 1
            if history_ok:
                try:
                    for rec in await gr.population_history(gid):
                        history.record_population(db, card.id, rec.grading_company, rec.grade, rec.count, "gemrate", rec.as_of)
                        out["history_rows"] += 1
                except Unavailable as exc:
                    if exc.code != "no_access":
                        raise
                    history_ok, out["history"] = False, exc.reason  # the plan has no history: current population only
            db.commit()
    except Unavailable as exc:
        error = exc.reason
    db.commit()
    history.invalidate()
    service.record_request(db, "gemrate", gr.requests, gr.info.rate_limit)
    service.record_source(db, "gemrate", out["population_rows"], error)
    out["error"] = error
    return out


async def sync_all(db: Session, settings: Any, transport: Any = None) -> dict[str, Any]:
    """Every connected provider that can sync; the rest are reported, not attempted."""
    from app.pokemon.psa import sync_psa

    results: dict[str, Any] = {}
    for key, fn in (("ebay", sync_ebay), ("pricecharting", sync_pricecharting), ("gemrate", sync_gemrate), ("psa", sync_psa)):
        try:
            results[key] = await fn(db, settings, transport)
        except Unavailable as exc:
            results[key] = {"skipped": exc.reason}
        except Exception as exc:  # one provider's bug never stops the others
            db.rollback()
            service.record_source(db, key, 0, f"sync failed: {type(exc).__name__}")
            results[key] = {"error": f"{type(exc).__name__}"}
    return results


async def test_connection(db: Session, settings: Any, key: str, transport: Any = None) -> dict[str, Any]:
    """One small, real request per provider to prove the connection works (or say exactly why it doesn't). Read-only."""
    from app.providers.pokemon import PROVIDERS

    if key not in PROVIDERS:
        raise Unavailable(f"Unknown provider: {key}", "unknown")
    p = provider(key, settings, transport)
    connected, why = p.status()
    if key == "manual":
        return {"ok": True, "message": "The CSV importer is available (Import sales below)."}
    if not connected:
        return {"ok": False, "message": why}
    try:
        if key == "scrydex":
            from app.providers.pokemon.scrydex import ScrydexProvider

            assert isinstance(p, ScrydexProvider)
            body = await p.get("/pokemon/v1/cards", {"page_size": 1}, cache=False)
            total = body.get("total_count") if isinstance(body, dict) else None
            try:
                u = await p.usage()
                usage = ", ".join(f"{k}: {v}" for k, v in u.items() if not isinstance(v, (dict, list))) or "no usage fields"
            except Unavailable as exc:
                usage = f"usage endpoint: {exc.reason}"
            service.record_request(db, key, p.requests, p.info.rate_limit)
            db.commit()
            return {"ok": True, "message": f"OK: Scrydex accepted the key and team (HTTP 200, {total if total is not None else '?'} cards). Usage: {usage}."}
        elif key == "tcgdex":
            card = await p.get_card("base1-4")
            ja = await p.get_card("ja:PMCG1-021")
            msg = f"OK: TCGdex answered in English ({card.name}, {card.set_name}) and Japanese ({ja.name}, {ja.set_name})."
        elif key == "pokemontcg":
            card = await p.get_card("base1-4")
            msg = f"OK: the API answered with {card.name} ({card.set_name} {card.number})."
        elif key == "psa":
            try:
                await p.get_cert("1")  # type: ignore[attr-defined]
                msg = "OK: PSA accepted the key."
            except Unavailable as exc:
                if exc.code not in ("not_found", "invalid"):
                    raise
                msg = "OK: PSA accepted the key (the test cert doesn't exist, as expected)."
        elif key == "ebay":
            from app.providers.pokemon.ebay import EbayProvider

            assert isinstance(p, EbayProvider)
            got = await p.get_listing_data("1999 Pokemon Base Set Charizard 4/102")
            try:
                await p.get_sales("1999 Pokemon Base Set Charizard 4/102")
                sold = "sold data: Marketplace Insights granted"
            except Unavailable as exc:
                sold = exc.reason if exc.code == "no_access" else f"sold data: {exc.reason}"
            service.record_request(db, "ebay", p.requests, p.info.rate_limit)
            msg = f"OK: Browse API answered ({len(got)} active listings). {sold}"
        elif key == "pricecharting":
            hits = await p.search_cards("charizard base set")
            msg = f"OK: PriceCharting answered ({len(hits)} products)."
        elif key == "gemrate":
            hits = await p.search_cards("1999 Pokemon Base Set Charizard Holo 4")
            msg = f"OK: GemRate answered ({len(hits)} matches for 1999 Base Set Charizard)."
        else:
            return {"ok": False, "message": why or "Not available."}
    except Unavailable as exc:
        service.record_request(db, key, getattr(p, "requests", 1), p.info.rate_limit or None)  # a refused call still counts
        service.record_source(db, key, 0, exc.reason)
        return {"ok": False, "message": exc.reason}
    service.record_request(db, key, 1, p.info.rate_limit or None)
    db.commit()
    return {"ok": True, "message": msg}
