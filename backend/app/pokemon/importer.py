"""Admin sales importer (CSV): preview -> admin confirmation -> import. Nothing is written by a preview.

Columns (header names are case-insensitive; common aliases accepted): card, set, year, card_number, language, edition,
variant, grading_company, grade, cert_number, sale_price, currency, sale_date, source, source_listing_id, url,
auction_house, sale_type; optional: card_id (exact catalog id), title (a listing title), usd_price / fx_rate.

Each row is matched to ONE catalog card with the same matcher as marketplace titles (matching.py): unsure matches are
"review" and are imported only if the admin picks the card. Impossible grades / prices / dates are rejected; duplicates
(same source + listing id, or same card + grade + price + day + source) are skipped; currencies are converted to USD at
the ECB rate of the sale date (fx.py). The commit re-checks everything server-side.
"""

import csv
import io
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.pokemon import PokemonCard, PokemonSale
from app.pokemon import matching
from app.pokemon.fx import FxError, FxRates
from app.pokemon.grades import Grade, GradeError, normalise, parse_label

MAX_CHARS = 2_000_000
MAX_ROWS = 5000
MAX_USD_CENTS = 20_000_000_00  # $20M: above any recorded Pokémon card sale
MIN_USD_CENTS = 1_00
ALIASES = {"name": "card", "card_name": "card", "pokemon": "card", "set_name": "set", "number": "card_number", "no": "card_number",
           "grader": "grading_company", "company": "grading_company", "grading": "grading_company", "price": "sale_price",
           "sold_price": "sale_price", "amount": "sale_price", "date": "sale_date", "sold_date": "sale_date", "listing_id": "source_listing_id",
           "lot": "source_listing_id", "lot_number": "source_listing_id", "link": "url", "auction": "auction_house", "type": "sale_type",
           "cert": "cert_number", "certificate": "cert_number",
           # auction-house result exports (Heritage, Goldin, Fanatics Collect / PWCC ...): their usual column names
           "price_realized": "sale_price", "prices_realized": "sale_price", "realized_price": "sale_price", "sold_for": "sale_price",
           "final_price": "sale_price", "winning_bid": "sale_price", "total_price": "sale_price", "purchase_price": "sale_price",
           "lot_title": "title", "item_title": "title", "lot_description": "title", "description": "title", "item": "title",
           "auction_date": "sale_date", "end_date": "sale_date", "closing_date": "sale_date", "date_sold": "sale_date",
           "sale_end": "sale_date", "close_date": "sale_date", "lot_no": "source_listing_id", "lot_id": "source_listing_id",
           "item_id": "source_listing_id", "item_number": "source_listing_id", "lot_url": "url", "item_url": "url"}
# Auction-house presets for "Import sales (CSV)": source + house for rows that don't name them. The file must be an export
# or record of results the owner may use (e.g. their own account's won lots / consignments); nothing is fetched from the site.
PRESETS = {"heritage": ("heritage", "Heritage Auctions"), "goldin": ("goldin", "Goldin"), "fanatics": ("fanatics", "Fanatics Collect"),
           "pwcc": ("pwcc", "PWCC"), "ebay": ("ebay", None)}
SYMBOLS = {"$": "USD", "£": "GBP", "€": "EUR", "¥": "JPY"}
_DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%d %b %Y", "%d %B %Y", "%b %d %Y", "%B %d %Y", "%b %d, %Y", "%B %d, %Y")


def _key(h: str) -> str:
    k = re.sub(r"[^a-z0-9]+", "_", (h or "").strip().lower()).strip("_")
    return ALIASES.get(k, k)


def read_csv(text: str) -> list[dict[str, str]]:
    if len(text) > MAX_CHARS:
        raise ValueError("The file is too large (2 MB max). Split it.")
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")), dialect=dialect)
    rows = [{_key(k): (v or "").strip() for k, v in r.items() if k is not None} for r in reader]
    if len(rows) > MAX_ROWS:
        raise ValueError(f"Too many rows ({len(rows)}); import at most {MAX_ROWS} at a time.")
    return rows


def parse_date(value: str) -> datetime:
    v = value.strip()
    if re.fullmatch(r"\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}", v):
        raise ValueError(f"ambiguous date {v!r}: use YYYY-MM-DD")
    try:
        d = datetime.fromisoformat(v.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    for f in _DATE_FORMATS:
        try:
            return datetime.strptime(v.replace(",", ", ").replace(",  ", ", "), f).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise ValueError(f"unreadable date {v!r}: use YYYY-MM-DD")


def parse_price(value: str, currency: str) -> tuple[int, str]:
    v = value.strip()
    cur = currency.strip().upper()
    for sym, code in SYMBOLS.items():
        if sym in v:
            if cur and cur != code:
                raise ValueError(f"price says {code} but currency column says {cur}")
            cur = code
            v = v.replace(sym, "")
    v = v.replace(",", "").replace(" ", "")
    if cur.endswith("$") or not re.fullmatch(r"\d+(\.\d{1,2})?", v):
        raise ValueError(f"unreadable price {value!r}")
    return round(float(v) * 100), (cur or "USD")


def parse_grade(company: str, grade: str) -> Grade:
    c, g = company.strip(), grade.strip()
    if not c and " " in g:
        return parse_label(g)
    if c.lower() in ("sgc", "ace", "tag", "ags", "hga"):
        raise GradeError(f"{c.upper()} grades aren't tracked (PSA, BGS, CGC or RAW)")
    return normalise(c or "RAW", g or ("NM" if (c or "RAW").upper() in ("RAW", "UNGRADED") else g))


def card_label(c: PokemonCard) -> str:
    return " ".join(x for x in (str(c.year), c.set_name, c.name, c.card_number or "", c.edition if c.edition != "—" else "",
                                c.language if c.language != "English" else "", c.variant) if x)


def _fingerprint(source: str, listing: str | None, card_id: int, g: Grade, price: int, when: datetime) -> str:
    from app.pokemon.service import fingerprint

    return fingerprint(source, listing, card_id, g, price, when)


def preview(db: Session, text: str, fx: FxRates, card_overrides: dict[int, int] | None = None, preset: str | None = None) -> dict[str, Any]:
    """Every row with its card match, normalised values and a status: ready | review | duplicate | error.
    `preset` (heritage / goldin / fanatics / pwcc / ebay) fills source and auction house where a row has none; a row with no
    grade columns takes the grade written in its title (e.g. "... PSA 10")."""
    raw = read_csv(text)
    if preset:
        if preset not in PRESETS:
            raise ValueError(f"Unknown source preset: {preset}")
        src, house = PRESETS[preset]
        for r in raw:
            r["source"] = r.get("source") or src
            if house:
                r["auction_house"] = r.get("auction_house") or house
                r["sale_type"] = r.get("sale_type") or "auction"
    cards = list(db.scalars(select(PokemonCard)))
    by_id = {c.id: c for c in cards}
    overrides = card_overrides or {}
    seen: set[str] = set()
    out = []
    for i, r in enumerate(raw, start=1):
        row: dict[str, Any] = {"row": i, "input": r, "status": "error", "errors": [], "warnings": [], "card_id": None, "card": None,
                               "confidence": None, "candidates": [], "reasons": []}
        errs, warns = row["errors"], row["warnings"]
        # card
        if r.get("card_id"):
            c = by_id.get(int(r["card_id"])) if r["card_id"].isdigit() else None
            if c is None:
                errs.append(f"card_id {r['card_id']} isn't in the catalog")
            else:
                row.update(card_id=c.id, confidence=1.0, match="matched", reasons=["card_id given"])
        else:
            title = r.get("title") or matching.from_fields(r.get("card", ""), r.get("set", ""), r.get("year", ""), r.get("card_number", ""),
                                                           r.get("language", ""), r.get("edition", ""), r.get("variant", ""))
            if not title.strip():
                errs.append("no card: give card/set/number (or card_id)")
            else:
                m = matching.match(title, cards, need_grade=False)
                row.update(card_id=m.card_id, confidence=m.confidence, match=m.status, reasons=m.reasons,
                           candidates=[{"card_id": cid, "label": card_label(by_id[cid]), "confidence": sc} for cid, sc in m.candidates])
                if m.status == "rejected":
                    errs.append("; ".join(m.reasons))
        if i in overrides:
            c = by_id.get(overrides[i])
            if c is None:
                errs.append("the chosen card isn't in the catalog")
            else:
                row.update(card_id=c.id, match="matched", reasons=row["reasons"] + ["card chosen by admin"])
        # grade, price, currency, date
        try:
            if not r.get("grading_company") and not r.get("grade") and r.get("title"):
                tg = matching.match(r["title"], cards).grade
                if tg is None:
                    raise GradeError("no grade column and no grade in the title")
                r["grading_company"], r["grade"] = tg.company, tg.grade
                warns.append(f"grade {tg.label} read from the title")
            g = parse_grade(r.get("grading_company", ""), r.get("grade", ""))
            row["grade"] = g.label
        except GradeError as exc:
            g = None
            errs.append(str(exc))
        try:
            price, currency = parse_price(r.get("sale_price", ""), r.get("currency", ""))
            row.update(price=price, currency=currency)
        except ValueError as exc:
            price, currency = 0, ""
            errs.append(str(exc))
        try:
            when = parse_date(r.get("sale_date", ""))
            row["sale_date"] = when.isoformat()
            if when > utcnow() + timedelta(days=1):
                errs.append("sale date is in the future")
            if when.year < 1996:
                errs.append("sale date is before Pokémon cards existed")
        except ValueError as exc:
            when = None
            errs.append(str(exc))
        if price and currency and when:
            try:
                if r.get("usd_price"):
                    usd, _ = parse_price(r["usd_price"], "USD")
                    rate, fx_source = round(usd / price, 6), "admin"
                elif r.get("fx_rate"):
                    rate, fx_source = float(r["fx_rate"]), "admin"
                    usd = round(price * rate)
                else:
                    rate, fx_source = fx.usd_per_unit(currency, when.date() if isinstance(when, datetime) else date.today())
                    usd = round(price * rate)
                row.update(price_usd=usd, fx_rate=rate, fx_source=fx_source)
                if usd < MIN_USD_CENTS or usd > MAX_USD_CENTS:
                    errs.append(f"price ${usd / 100:,.2f} is outside $1 to $20,000,000")
            except (FxError, ValueError) as exc:
                errs.append(f"no USD value: {exc}. Add a usd_price or fx_rate column.")
        source = (r.get("source") or "manual").strip().lower()[:32]
        listing = r.get("source_listing_id") or None
        row.update(source=source, source_listing_id=listing, url=r.get("url") or None, auction_house=r.get("auction_house") or None,
                   cert_number=r.get("cert_number") or None, sale_type=(r.get("sale_type") or "").lower() or None)
        if row["url"] and not re.match(r"^https?://", row["url"]):
            warns.append("url doesn't start with http(s)://, ignored")
            row["url"] = None
        if not listing and not row["url"]:
            warns.append("no listing id or URL: the sale can't be traced to its source")
        # status
        if not errs and row["card_id"] and g and when:
            fp = _fingerprint(source, listing, row["card_id"], g, price, when)
            row["fingerprint"] = fp
            if fp in seen or db.scalar(select(PokemonSale.id).where(PokemonSale.fingerprint == fp)):
                row["status"] = "duplicate"
            else:
                seen.add(fp)
                row["status"] = "ready" if row.get("match") == "matched" else "review"
                _sanity(db, row, g, warns)
        elif not errs and not row["card_id"]:
            errs.append("no catalog card")
        if errs:
            row["status"] = "error"
        if row["card_id"]:
            row["card"] = card_label(by_id[row["card_id"]])
        out.append(row)
    counts = {s: sum(1 for x in out if x["status"] == s) for s in ("ready", "review", "duplicate", "error")}
    return {"rows": out, "counts": counts, "total": len(out)}


def _sanity(db: Session, row: dict[str, Any], g: Grade, warns: list[str]) -> None:
    """Warn (never block) when a price is far from the current market for that exact card + grade."""
    from app.pokemon.service import valuations

    v = valuations(db, [row["card_id"]]).get((row["card_id"], g.company, g.grade))
    mp = v["market_price"] if v else None
    if mp and row.get("price_usd") and not (mp / 5 <= row["price_usd"] <= mp * 5):
        warns.append(f"far from the current {g.label} market price (${mp / 100:,.0f}): check the grade and edition")


def commit(db: Session, text: str, fx: FxRates, accept: dict[int, int | None], verified: bool, preset: str | None = None) -> dict[str, Any]:
    """Import the rows the admin accepted. `accept` maps row number -> chosen card id (or None to keep the match).
    Ready rows import as matched; review rows only with a chosen card; duplicates and errors never."""
    overrides = {r: cid for r, cid in accept.items() if cid}
    pv = preview(db, text, fx, overrides, preset)
    results, imported, skipped, failed = [], 0, 0, 0
    touched: set[tuple[int, str, str]] = set()
    for row in pv["rows"]:
        n = row["row"]
        if n not in accept:
            skipped += 1
            results.append({"row": n, "outcome": "skipped", "why": "not selected"})
            continue
        if row["status"] == "duplicate":
            skipped += 1
            results.append({"row": n, "outcome": "skipped", "why": "duplicate"})
            continue
        if row["status"] == "error" or (row["status"] == "review" and n not in overrides):
            failed += 1
            results.append({"row": n, "outcome": "failed", "why": "; ".join(row["errors"]) or "card match needs a decision"})
            continue
        g = parse_label(row["grade"])
        s = PokemonSale(card_id=row["card_id"], grading_company=g.company, grade=g.grade, cert_number=row["cert_number"],
                        sale_price_cents=row["price"], currency=row["currency"], sale_price_usd_cents=row["price_usd"],
                        sale_date=datetime.fromisoformat(row["sale_date"]), source=row["source"], source_listing_id=row["source_listing_id"],
                        auction_house=row["auction_house"], url=row["url"], sale_type=row["sale_type"], verified=verified,
                        verified_by="manual" if verified else None, fingerprint=row["fingerprint"], fx_rate=row["fx_rate"],
                        fx_source=row["fx_source"], title=row["input"].get("title") or None,
                        match_confidence=None if n in overrides else row["confidence"])
        db.add(s)
        touched.add((s.card_id, g.company, g.grade))
        imported += 1
        results.append({"row": n, "outcome": "imported"})
    from app.pokemon.service import record_source

    record_source(db, "manual", imported, None)
    db.commit()
    from app.pokemon import history

    history.rebuild(db, touched)
    return {"imported": imported, "skipped": skipped, "failed": failed, "results": results}


# --- population reports (CSV) ------------------------------------------------------------------------------------------


def import_population_csv(db: Session, text: str, source: str) -> dict[str, Any]:
    """Rows: card_id (or card / set / card_number / edition ...), grading_company, grade, count, as_of (default today).
    Only certain card matches are imported; every count is kept in the population history too."""
    from app.pokemon import history
    from app.pokemon.service import _record_source, store_population

    raw = read_csv(text)
    cards = list(db.scalars(select(PokemonCard)))
    by_id = {c.id: c for c in cards}
    src = re.sub(r"[^a-z0-9_]+", "", (source or "manual").lower())[:32] or "manual"
    out: dict[str, Any] = {"imported": 0, "errors": []}
    for i, r in enumerate(raw, start=1):
        try:
            if r.get("card_id"):
                card = by_id.get(int(r["card_id"])) if r["card_id"].isdigit() else None
                if card is None:
                    raise ValueError(f"card_id {r['card_id']} isn't in the catalog")
            else:
                title = r.get("title") or matching.from_fields(r.get("card", ""), r.get("set", ""), r.get("year", ""), r.get("card_number", ""),
                                                               r.get("language", ""), r.get("edition", ""), r.get("variant", ""))
                m = matching.match(title, cards, need_grade=False)
                if m.status != "matched" or m.card_id is None:
                    raise ValueError(f"card not matched with certainty ({'; '.join(m.reasons[:2]) or m.status}): give card_id")
                card = by_id[m.card_id]
            g = parse_grade(r.get("grading_company", ""), r.get("grade", ""))
            if not g.graded:
                raise ValueError("population is for graded cards only")
            count = int(str(r.get("count") or r.get("population") or "").replace(",", ""))
            if count < 0:
                raise ValueError("count can't be negative")
            as_of = parse_date(r["as_of"]) if r.get("as_of") else utcnow()
            if as_of > utcnow() + timedelta(days=1):
                raise ValueError("as_of is in the future")
        except (KeyError, ValueError, GradeError) as exc:
            out["errors"].append({"row": i, "error": str(exc) or "invalid row"})
            continue
        store_population(db, card.id, g, count, src, as_of)
        out["imported"] += 1
    _record_source(db, "manual", out["imported"], None)
    db.commit()
    history.invalidate()
    return out
