"""eBay seller sync for the Vault: the official Browse API only (app keys, client-credentials token).

One sync of one seller:
1. search  `item_summary/search`: the seller's FIXED-PRICE listings in Pokémon Individual Cards (183454) whose
   Language item specific is Japanese, 200 a page, up to `vault_ebay_max_pages` pages;
2. details `item/{id}` for new listings (item specifics, all photos, the seller's condition text, availability),
   at most `vault_ebay_details_per_sync` a run;
3. refresh `item/{id}` for APPROVED listings the search didn't confirm in this run (sold out, ended, or beyond the
   page cap), so what the public sees is never older than eBay's limit allows.
A full search (every page read) marks unseen, unapproved listings as ended. Every call counts against the source's daily
budget (`vault_ebay_daily_calls`); a run stops early rather than exceed it. Prices are copied, never computed.
"""

import re
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.vault import VaultListing, VaultSource
from app.providers.pokemon import Unavailable, provider
from app.providers.pokemon.ebay import BROWSE_SCOPE, CATEGORY, EbayProvider
from app.vault import classify

PAGE = 200


class Budget:
    def __init__(self, source: VaultSource, limit: int, now: datetime) -> None:
        day = now.date().isoformat()
        if source.calls_day != day:
            source.calls_day, source.calls_today = day, 0
        self.source, self.limit = source, limit

    def left(self) -> int:
        return max(0, self.limit - self.source.calls_today)

    def spend(self, n: int) -> None:
        self.source.calls_today += n


def _money(m: dict[str, Any] | None) -> tuple[int, str] | None:
    if not m or m.get("value") in (None, ""):
        return None
    return round(float(m["value"]) * 100), str(m.get("currency") or "USD")[:3]


def _images(it: dict[str, Any]) -> list[str]:
    urls = [(it.get("image") or {}).get("imageUrl")] + [i.get("imageUrl") for i in it.get("additionalImages") or []]
    out: list[str] = []
    for u in urls:
        if u and u.startswith("https://") and u not in out:
            out.append(u[:500])
    return out[:12]


def _shipping(it: dict[str, Any]) -> int | None:
    for o in it.get("shippingOptions") or []:
        m = _money(o.get("shippingCost"))
        if m:
            return m[0]
    return None


def _apply_common(row: VaultListing, it: dict[str, Any]) -> None:
    row.title = str(it.get("title") or row.title or "")[:300]
    row.url = (it.get("itemWebUrl") or row.url or "")[:500] or None
    money = _money(it.get("price"))
    if money:
        row.price_cents, row.currency = money
    ship = _shipping(it)
    if ship is not None or it.get("shippingOptions"):
        row.shipping_cents = ship
    if it.get("condition"):
        row.condition = str(it["condition"])[:80]
    imgs = _images(it)
    if len(imgs) > len(row.images or []):
        row.images = imgs
    seller = it.get("seller") or {}
    if seller.get("username"):
        row.seller_username = str(seller["username"])[:64]
    if seller.get("feedbackPercentage") is not None:
        row.seller_feedback_pct = str(seller["feedbackPercentage"])[:8]
    if seller.get("feedbackScore") is not None:
        row.seller_feedback_score = int(seller["feedbackScore"])
    if it.get("buyingOptions"):
        row.buying_options = ",".join(it["buyingOptions"])[:80]
    if it.get("legacyItemId"):
        row.legacy_id = str(it["legacyItemId"])[:32]


def _apply_details(row: VaultListing, it: dict[str, Any], now: datetime) -> None:
    _apply_common(row, it)
    aspects = {str(a.get("name")): str(a.get("value")) for a in it.get("localizedAspects") or [] if a.get("name")}
    row.aspects = aspects or row.aspects
    descriptors = []
    for d in it.get("conditionDescriptors") or []:
        values = ", ".join(str(v.get("content") or "") for v in d.get("values") or [] if v.get("content"))
        if d.get("name") and values:
            descriptors.append(f"{d['name']}: {values}")
    text = "; ".join(descriptors + ([str(it["conditionDescription"])] if it.get("conditionDescription") else []))
    row.condition_detail = text[:2000] or row.condition_detail
    status = None
    for a in it.get("estimatedAvailabilities") or []:
        status = a.get("estimatedAvailabilityStatus") or status
    row.availability = "sold_out" if status == "OUT_OF_STOCK" else "available"
    if row.availability == "sold_out" and row.ended_at is None:
        row.ended_at = now
    row.details_at = now


def reclassify(row: VaultListing, include_graded: bool) -> None:
    v = classify.classify(row.title, row.aspects, include_graded=include_graded, year_override=row.year_override, set_override=row.set_override)
    row.eligible, row.exclude_reason = v.eligible, (v.reason or None) and v.reason[:120]
    row.pokemon, row.set_name, row.year, row.language, row.graded = (v.pokemon and v.pokemon[:60]), (v.set_name and v.set_name[:80]), v.year, v.language, v.graded


def _ebay(settings: Any, transport: Any) -> EbayProvider:
    p = provider("ebay", settings, transport)
    assert isinstance(p, EbayProvider)
    return p


async def sync_ebay_seller(db: Session, settings: Any, source: VaultSource, transport: Any = None, now: datetime | None = None) -> dict[str, Any]:
    now = now or utcnow()
    counts = {"pages": 0, "seen": 0, "new": 0, "eligible": 0, "details": 0, "refreshed": 0, "ended": 0, "sold_out": 0, "calls": 0, "total": None}
    source.last_sync_at = now
    ebay = _ebay(settings, transport)
    connected, why = ebay.status()
    if not connected:
        source.last_status, source.last_message, source.last_counts = "not_connected", f"eBay isn't connected. {why}", counts
        db.commit()
        return {"status": source.last_status, "message": source.last_message, **counts}
    budget = Budget(source, settings.vault_ebay_daily_calls, now)
    existing = {r.external_id: r for r in db.scalars(select(VaultListing).where(VaultListing.source_id == source.id))}
    seen: set[str] = set()
    complete, problem = False, None

    async def call(path: str, params: dict[str, Any]) -> dict[str, Any]:
        before = ebay.requests
        try:
            return await ebay._get(path, params, BROWSE_SCOPE)
        finally:
            spent = ebay.requests - before
            budget.spend(spent)
            counts["calls"] += spent

    # 1. search
    offset = 0
    try:
        for _ in range(settings.vault_ebay_max_pages):
            if budget.left() < 2:
                problem = "Daily eBay call budget reached; the rest waits for the next run."
                break
            body = await call("/buy/browse/v1/item_summary/search", {
                "category_ids": CATEGORY, "limit": PAGE, "offset": offset, "sort": "newlyListed",
                "filter": f"sellers:{{{source.key}}},buyingOptions:{{FIXED_PRICE}}",
                "aspect_filter": f"categoryId:{CATEGORY},Language:{{Japanese}}"})
            counts["pages"] += 1
            counts["total"] = int(body.get("total") or 0)
            for it in body.get("itemSummaries") or []:
                ext = str(it.get("itemId") or "")[:80]
                if not ext or ext in seen:
                    continue
                if str((it.get("seller") or {}).get("username") or "").lower() != source.key.lower():
                    counts["other_seller"] = counts.get("other_seller", 0) + 1  # never trust the filter alone
                    continue
                seen.add(ext)
                row = existing.get(ext)
                if row is None:
                    row = VaultListing(source_id=source.id, external_id=ext, title="", images=[], first_seen_at=now, review="pending")
                    db.add(row)
                    existing[ext] = row
                    counts["new"] += 1
                _apply_common(row, it)
                row.availability, row.checked_at, row.ended_at = "available", now, None
                reclassify(row, settings.vault_include_graded)
            offset += PAGE
            if offset >= (counts["total"] or 0) or not body.get("itemSummaries"):
                complete = True
                break
    except Unavailable as exc:
        problem = exc.reason
        if exc.code in ("auth", "not_connected"):
            source.last_status, source.last_message, source.last_counts = "error", exc.reason, counts
            db.commit()
            return {"status": "error", "message": exc.reason, **counts}
    counts["seen"] = len(seen)
    db.flush()

    # 2. details for new / never-detailed listings that could be in the store (eligible, or unsure from the title alone)
    maybe = ("language not stated", "no vintage set or year stated")
    todo = [r for r in existing.values() if r.details_at is None and r.external_id in seen and (r.eligible or r.exclude_reason in maybe)]
    todo.sort(key=lambda r: (r.review != "approved", not r.eligible))
    for row in todo[: settings.vault_ebay_details_per_sync]:
        if budget.left() < 1:
            break
        try:
            _apply_details(row, await call(f"/buy/browse/v1/item/{row.external_id}", {}), now)
            row.checked_at = now
            reclassify(row, settings.vault_include_graded)
            counts["details"] += 1
        except Unavailable as exc:
            if exc.code == "not_found":
                row.availability, row.ended_at = "ended", row.ended_at or now
            else:
                problem = exc.reason
                break

    # 3. approved listings the search didn't confirm: ask eBay about each one
    for row in [r for r in existing.values() if r.review == "approved" and r.external_id not in seen and r.availability == "available"]:
        if budget.left() < 1:
            problem = problem or "Daily eBay call budget reached before every approved listing was re-checked."
            break
        try:
            _apply_details(row, await call(f"/buy/browse/v1/item/{row.external_id}", {}), now)
            row.checked_at = now
            counts["refreshed"] += 1
            counts["sold_out"] += row.availability == "sold_out"
        except Unavailable as exc:
            if exc.code == "not_found":
                row.availability, row.ended_at, row.checked_at = "ended", now, now
                counts["ended"] += 1
            else:
                problem = problem or exc.reason
                break

    # 4. a complete search proves the rest are gone from the seller's store
    if complete:
        for row in existing.values():
            if row.external_id not in seen and row.review != "approved" and row.availability == "available":
                row.availability, row.ended_at = "ended", now
                counts["ended"] += 1

    counts["eligible"] = sum(1 for r in existing.values() if r.eligible and r.availability == "available")
    source.last_counts = counts
    if problem:
        source.last_status, source.last_message = "partial", problem
    else:
        source.last_status, source.last_ok_at = "ok", now
        source.last_message = (f"Read {counts['seen']} Japanese listings from {source.key}; {counts['eligible']} look vintage." if counts["seen"]
                               else f"eBay returned no Japanese fixed-price listings for seller “{source.key}”. Check the eBay username.")
    db.commit()
    return {"status": source.last_status, "message": source.last_message, **counts}


def large_image(url: str) -> str:
    """eBay-hosted photo at a display size (same image, eBay's own size variants)."""
    return re.sub(r"/s-l\d+\.(jpg|jpeg|png|webp)", r"/s-l1600.\1", url)


def stale(row: VaultListing, max_age_hours: int, now: datetime | None = None) -> bool:
    now = now or utcnow()
    return row.checked_at is None or now - row.checked_at > timedelta(hours=max_age_hours)
