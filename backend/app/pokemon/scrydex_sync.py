"""Scrydex sync: exact card variants -> provider valuations (PSA and other graded prices), PSA population per grade, and
(optionally) the provider's daily price history. Every run is logged card by card (pokemon_sync_logs).

Matching: Scrydex card ids are "<set id>-<number>" (the catalog's set_code + card number). The returned card must agree
on set, number and name, and each catalog edition row must resolve to exactly ONE Scrydex variant (1st Edition / Shadowless
/ Unlimited, holo). Anything else is logged as unmatched, never guessed.

Japanese cards: only through an explicit Scrydex id in a Japanese expansion ("base1_ja-21", set by japanese.map_scrydex),
and the returned card must say language_code "JA". An English card must come back "EN". So a Japanese card can never get
an English price or population, nor the reverse.
"""

import re
from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.pokemon import PokemonCard, PokemonProviderValuation, PokemonSyncLog
from app.pokemon import service
from app.pokemon.grades import Grade, GradeError, normalise
from app.providers.pokemon import Unavailable, provider
from app.providers.pokemon.scrydex import ScrydexProvider, match_variant

PROVIDER = "scrydex"
HISTORY_GRADES = (("PSA", "10"),)  # price history requests per card (1 credit each); PSA 10 first


def scrydex_id(card: PokemonCard) -> str | None:
    if card.language == "Japanese":
        sid = str((card.external_ids or {}).get("scrydex") or "")
        return sid if sid.split("-")[0].endswith("_ja") else None
    linked = str((card.external_ids or {}).get("scrydex") or "")
    if card.language == "English" and linked and not linked.split("-")[0].endswith("_ja"):
        return linked
    if card.language != "English" or not card.set_code or not card.card_number or card.set_code.endswith("_ja"):
        return None
    n = re.sub(r"\D", "", card.card_number.split("/")[0])
    return f"{card.set_code}-{int(n)}" if n else None


def _cents(v: Any) -> int | None:
    return int(round(float(v) * 100)) if isinstance(v, (int, float)) and v > 0 else None


def _same_name(a: str, b: str) -> bool:
    x, y = (re.sub(r"[^a-z]", "", s.lower().replace("★", "")) for s in (a, b))
    return bool(x and y) and (x == y or x.endswith(y) or y.endswith(x))


# grades whose daily valuations are kept as history (market table, charts, Top 100); every other grade and the raw
# conditions keep ONE current row per card, updated in place (portfolio valuations need today's price, not a daily series)
DAILY_GRADES = {("PSA", "10"), ("PSA", "9"), ("PSA", "8"), ("PSA", "7"), ("BGS", "10"), ("BGS", "9.5"), ("CGC", "10")}


class _CardRows:
    """One card's stored Scrydex valuations, read once: _upsert looks rows up here instead of one SELECT (and a flush) per
    price point (a year of history is hundreds of points per card)."""

    def __init__(self, db: Session, card_id: int) -> None:
        self.by_day: dict[tuple[str, str, date], PokemonProviderValuation] = {}
        self.current: dict[tuple[str, str], PokemonProviderValuation] = {}
        for r in db.scalars(select(PokemonProviderValuation).where(PokemonProviderValuation.provider == PROVIDER,
                                                                   PokemonProviderValuation.card_id == card_id)
                            .order_by(PokemonProviderValuation.id)):
            self.by_day[(r.grading_company, r.grade, r.day)] = r
            if r.kind == "current":
                self.current.setdefault((r.grading_company, r.grade), r)

    def drop(self, r: PokemonProviderValuation) -> None:
        self.by_day.pop((r.grading_company, r.grade, r.day), None)
        if self.current.get((r.grading_company, r.grade)) is r:
            del self.current[(r.grading_company, r.grade)]


def _upsert(db: Session, card: PokemonCard, sid: str, variant: str, g: Grade, day: date, kind: str, price: dict[str, Any], now: datetime,
            rows: _CardRows | None = None) -> bool:
    if kind == "current" and (g.company, g.grade) not in DAILY_GRADES:
        if rows is not None:
            row = rows.current.get((g.company, g.grade))
        else:
            row = db.scalar(select(PokemonProviderValuation).where(
                PokemonProviderValuation.provider == PROVIDER, PokemonProviderValuation.card_id == card.id, PokemonProviderValuation.kind == "current",
                PokemonProviderValuation.grading_company == g.company, PokemonProviderValuation.grade == g.grade).limit(1))
        if row is not None:
            if rows is not None:
                rows.by_day.pop((row.grading_company, row.grade, row.day), None)
                rows.by_day[(g.company, g.grade, day)] = row
            row.day = day
    elif rows is not None:
        row = rows.by_day.get((g.company, g.grade, day))
    else:
        row = db.scalar(select(PokemonProviderValuation).where(
            PokemonProviderValuation.provider == PROVIDER, PokemonProviderValuation.card_id == card.id,
            PokemonProviderValuation.grading_company == g.company, PokemonProviderValuation.grade == g.grade, PokemonProviderValuation.day == day))
    if row is not None and row.kind == "current" and kind == "history":
        return False  # today's published price wins over a history point for the same day
    new = row is None
    if new:
        row = PokemonProviderValuation(provider=PROVIDER, card_id=card.id, grading_company=g.company, grade=g.grade, day=day)
        db.add(row)
        if rows is not None:
            rows.by_day[(g.company, g.grade, day)] = row
    if rows is not None and kind == "current":
        rows.current.setdefault((g.company, g.grade), row)
    row.provider_card_id, row.provider_variant, row.kind, row.fetched_at = sid, variant, kind, now
    row.market_cents, row.low_cents = _cents(price.get("market")), _cents(price.get("low"))
    row.mid_cents, row.high_cents = _cents(price.get("mid")), _cents(price.get("high"))
    row.currency = str(price.get("currency") or "USD")[:3]
    row.trends = price.get("trends") if kind == "current" else None
    if rows is None:
        db.flush()
    return new


JA_NAMES = {"pikachu illustrator": "illustrator"}  # catalog name -> Scrydex English translation


def language_ok(card: PokemonCard, data: dict[str, Any]) -> bool:
    code = str(data.get("language_code") or (data.get("expansion") or {}).get("language_code") or "").upper()
    return code == "JA" if card.language == "Japanese" else code in ("EN", "")


def identity_ok(card: PokemonCard, sid: str, data: dict[str, Any]) -> bool:
    exp = data.get("expansion") or {}
    if card.language == "Japanese":
        en = str(((data.get("translation") or {}).get("en") or {}).get("name") or "")
        want = JA_NAMES.get(card.name.lower(), card.name)
        return data.get("id") == sid and str(exp.get("id") or sid.split("-")[0]) == sid.split("-")[0] and en.lower() == want.lower()
    return exp.get("id") == card.set_code and str(data.get("number")) == sid.split("-")[-1] and _same_name(card.name, str(data.get("name", "")))


def inverted(prices: list[dict[str, Any]]) -> set[tuple[str, str]]:
    """(company, "10") when that company's grade 10 is priced BELOW its grade 9: a provider inconsistency (e.g. a PSA 10 at
    $3,398 next to a PSA 9 at $25,736). That 10 is not used rather than shown. Lower and half grades are too thinly
    traded to judge this way and are kept as published."""
    best: dict[tuple[str, str], float] = {}
    for pr in prices:
        g = _graded(pr)
        if g is not None and isinstance(pr.get("market"), (int, float)):
            best[(g.company, g.grade)] = float(pr["market"])
    return {(co, "10") for (co, gr), m in best.items() if gr == "10" and (co, "9") in best and m < best[(co, "9")]}


def _graded(price: dict[str, Any]) -> Grade | None:
    """The exact grade a Scrydex price is for: a graded one (PSA 9, BGS 9.5...) or a raw (ungraded) condition (RAW NM / LP /
    MP / HP / DMG: real ungraded market prices, used for raw portfolio holdings). Signed / error / "perfect" (e.g. CGC
    Pristine) copies are different products and are skipped."""
    if price.get("is_signed") or price.get("is_error") or price.get("is_perfect"):
        return None
    try:
        if price.get("type") == "graded":
            return normalise(str(price.get("company") or ""), str(price.get("grade") or ""))
        if price.get("type") == "raw" and price.get("condition"):
            return normalise("RAW", str(price.get("condition")))
    except GradeError:
        return None
    return None


async def sync(db: Session, settings: Any, card_ids: list[int] | None = None, with_history: bool = True, transport: Any = None,
               max_requests: int | None = None, history_grades: tuple[tuple[str, str], ...] = HISTORY_GRADES,
               invalidate: bool = True) -> dict[str, Any]:
    """Sync the given catalog cards (all English cards with a set code when None). Returns the run log."""
    from app.pokemon import history

    p = provider(PROVIDER, settings, transport)
    assert isinstance(p, ScrydexProvider)
    if max_requests is not None:
        p.max_requests = max_requests
    if not p.status()[0]:
        raise Unavailable(p.status()[1], "not_connected")
    now = utcnow()
    log = PokemonSyncLog(provider=PROVIDER, started_at=now, status="running", requests=0, cache_hits=0, summary={}, entries=[])
    db.add(log)
    db.commit()
    q = select(PokemonCard).order_by(PokemonCard.id)
    if card_ids is not None:
        q = q.where(PokemonCard.id.in_(card_ids))
    cards = list(db.scalars(q))
    groups: dict[str, list[PokemonCard]] = {}
    entries: list[dict[str, Any]] = []
    for c in cards:
        sid = scrydex_id(c)
        if sid is None:
            entries.append({"card_id": c.id, "card": c.name, "status": "skipped",
                            "reason": "Japanese card not mapped to a Scrydex Japanese card yet" if c.language == "Japanese" else "no set code / number"})
        else:
            groups.setdefault(sid, []).append(c)
    summary: dict[str, Any] = {"cards_requested": len(cards), "scrydex_cards": len(groups), "matched_variants": 0, "valuations": 0,
                               "valuations_new": 0, "population_rows": 0, "history_points": 0, "unmatched": 0, "usage_before": None}
    error = None
    try:
        try:
            summary["usage_before"] = await p.usage()
        except Unavailable as exc:
            if exc.code in ("auth", "not_connected"):
                raise
            summary["usage_before"] = {"unavailable": exc.reason}
        for sid, rows in groups.items():
            try:
                data = await p.card(sid)
            except Unavailable as exc:
                if exc.code != "not_found":
                    raise
                for c in rows:
                    entries.append({"card_id": c.id, "card": c.name, "scrydex_id": sid, "status": "unmatched", "reason": exc.reason})
                    summary["unmatched"] += 1
                continue
            exp = data.get("expansion") or {}
            if not language_ok(rows[0], data):
                for c in rows:
                    entries.append({"card_id": c.id, "card": c.name, "scrydex_id": sid, "status": "unmatched",
                                    "reason": f"language: catalog card is {c.language}, Scrydex {sid} is {data.get('language') or data.get('language_code')}"})
                    summary["unmatched"] += 1
                continue
            ok_id = identity_ok(rows[0], sid, data)
            if not ok_id:
                for c in rows:
                    entries.append({"card_id": c.id, "card": c.name, "scrydex_id": sid, "status": "unmatched",
                                    "reason": f"Scrydex {sid} is {data.get('name')} ({exp.get('id')} #{data.get('number')}), not {c.name}"})
                    summary["unmatched"] += 1
                continue
            for c in rows:
                linked_v = (c.external_ids or {}).get("scrydex_variant")
                exact = next((x for x in data.get("variants") or [] if linked_v and x.get("name") == linked_v), None)
                v, why = (exact, f"variant {linked_v} (linked)") if exact else match_variant(data.get("variants") or [], c.edition, c.set_code, c.holo_type, c.variant)
                entry: dict[str, Any] = {"card_id": c.id, "card": f"{c.name} {c.set_name} {c.edition}", "scrydex_id": sid, "match": why}
                if v is None:
                    entry.update(status="unmatched")
                    summary["unmatched"] += 1
                    entries.append(entry)
                    continue
                summary["matched_variants"] += 1
                stored = _CardRows(db, c.id)
                c.external_ids = {**(c.external_ids or {}), "scrydex": sid, "scrydex_variant": v.get("name")}
                prices: dict[str, Any] = {}
                bad = inverted(v.get("prices") or [])
                for pr in v.get("prices") or []:
                    g = _graded(pr)
                    if g is None:
                        continue
                    if (g.company, g.grade) in bad:
                        entry.setdefault("not_used", []).append(f"{g.label} {pr.get('market')} is below a lower grade's price (provider inconsistency)")
                        summary["inconsistent"] = summary.get("inconsistent", 0) + 1
                        for stale in db.scalars(select(PokemonProviderValuation).where(  # an earlier run today may have stored it
                                PokemonProviderValuation.provider == PROVIDER, PokemonProviderValuation.card_id == c.id, PokemonProviderValuation.kind == "current",
                                PokemonProviderValuation.grading_company == g.company, PokemonProviderValuation.grade == g.grade,
                                PokemonProviderValuation.day == now.date())):
                            stored.drop(stale)
                            db.delete(stale)
                        continue
                    if _upsert(db, c, sid, str(v.get("name")), g, now.date(), "current", pr, now, stored):
                        summary["valuations_new"] += 1
                    summary["valuations"] += 1
                    prices[g.label] = {"market": pr.get("market"), "low": pr.get("low"), "mid": pr.get("mid"), "high": pr.get("high"),
                                       "currency": pr.get("currency"), "7d %": ((pr.get("trends") or {}).get("days_7") or {}).get("percent_change")}
                pops: dict[str, int] = {}
                for rep in v.get("pop_reports") or []:
                    company = str(rep.get("company") or "")
                    for gr in rep.get("grades") or []:
                        try:
                            g = normalise(company, str(gr.get("grade")))
                        except GradeError:
                            continue
                        if isinstance(gr.get("count"), int):
                            service.store_population(db, c.id, g, gr["count"], PROVIDER, now)
                            pops[g.label] = gr["count"]
                            summary["population_rows"] += 1
                entry.update(status="matched", variant=v.get("name"), prices=prices, population={k: pops[k] for k in ("PSA 10", "PSA 9", "PSA 8") if k in pops},
                             population_grades=len(pops))
                if with_history:
                    pts = 0
                    for company, grade in history_grades:
                        if not any(k == f"{company} {grade}" for k in prices):
                            continue  # no current price at that grade: no history request
                        db.commit()  # what's stored so far is saved first: no flushed write (SQLite's write lock) waits on Scrydex
                        try:
                            days = await p.price_history(sid, str(v.get("name")), company, grade)
                        except Unavailable as exc:
                            if exc.code in ("no_access", "not_found"):
                                entry["history"] = exc.reason
                                continue
                            raise
                        for d in days:
                            try:
                                day = date.fromisoformat(str(d.get("date"))[:10].replace("/", "-"))  # Scrydex: "2026/10/07"
                            except ValueError:
                                continue
                            for pr in d.get("prices") or []:
                                if pr.get("variant") not in (None, v.get("name")):
                                    continue
                                g = _graded(pr)
                                if g is None or (g.company, g.grade) != (company, grade):
                                    continue
                                pts += int(_upsert(db, c, sid, str(v.get("name")), g, day, "history", pr, now, stored))
                    summary["history_points"] += pts
                    entry["history_points"] = pts
                entries.append(entry)
                db.commit()
    except Unavailable as exc:
        error = exc.reason
        db.rollback()
    summary["requests"], summary["cache_hits"] = p.requests, p.cache_hits
    log = db.get(PokemonSyncLog, log.id)
    assert log is not None
    log.finished_at, log.requests, log.cache_hits = utcnow(), p.requests, p.cache_hits
    log.summary, log.entries, log.error = summary, entries, error
    log.status = "error" if error and not summary["matched_variants"] else "partial" if error or summary["unmatched"] else "ok"
    db.commit()
    if invalidate:  # a single card fetched for a portfolio doesn't force the 50k-card market rebuild
        history.invalidate()
    service.record_request(db, PROVIDER, p.requests, p.info.rate_limit)
    service.record_source(db, PROVIDER, summary["valuations"] + summary["population_rows"], error)
    db.commit()
    return log_out(log)


def log_out(log: PokemonSyncLog) -> dict[str, Any]:
    return {"id": log.id, "provider": log.provider, "status": log.status, "started_at": log.started_at.isoformat(),
            "finished_at": log.finished_at.isoformat() if log.finished_at else None, "requests": log.requests, "cache_hits": log.cache_hits,
            "summary": log.summary, "entries": log.entries, "error": log.error}
