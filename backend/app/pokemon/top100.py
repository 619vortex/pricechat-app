"""The Top 100: the strongest, best-supported VINTAGE (1996-2003) PSA 10 market records, English and Japanese.

Two steps, kept apart:
  1. ELIGIBILITY. Every vintage card's PSA 10 record is checked (below). A card that fails any check is excluded from
     the Top 100 (never deleted: it stays in search and in the "All cards" view) and the failed checks are its reasons.
  2. RANK. Eligible cards are ranked ONLY by estimated market cap = PSA 10 market price x PSA 10 population. The quality
     score never changes the order (it is shown as the card's Data Confidence).
If fewer than 100 cards qualify, the Top 100 shows how many do; it is never padded with unreliable cards.

Checks (each one a reason when it fails):
  price        a PSA 10 price from verified sales or Scrydex (PriceCharting alone has no history: backup only)
  population   a PSA 10 population on record (needed for a market cap at all)
  history      reliable 30D AND 90D movements: either at least N real observation days on record (provider
               valuations and verified sales combined), or the provider's own published 30-day and 90-day trends on a fresh
               valuation. A vintage card is not excluded just because Price Chat's database holds few snapshots of it.
               Charts are never filled in.
  identity     the exact printing is confirmed by a provider (Scrydex card + variant) or by matched verified sales
  freshness    the valuation is at most N days old (sales: a sale in the last 180 days)
  moves        no 30D / 90D move beyond the spike limits unless verified sales support it
  spikes       the last 90 days of price history have no single jump of 3x or more up or down
  consistency  where verified eBay / auction sales exist, Scrydex is within 50% of their median; without sales, Scrydex and
               PriceCharting (an internal cross-check only, never shown) must not differ by more than 2x
"""

import statistics
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.pokemon import PokemonCard, PokemonProviderValuation, PokemonSale, PokemonTop100Event, PokemonTop100Snapshot
from app.pokemon import history
from app.pokemon.grades import Grade

GRADE = Grade("PSA", "10")
LIMIT = 100
VINTAGE_UNTIL = 2003
SPIKE_LIMITS = {"30d": 100.0, "90d": 200.0}  # % moves that need verified sales behind them
CROSS_CHECK = 1.0  # Scrydex vs PriceCharting: more than 100% apart (one is over double the other) is an unexplained outlier
JUMP = 3.0  # one observation to the next: x3 up or down is a spike
SPIKE_DAYS = 90  # spikes count within the chart's default window; older jumps are noted, not disqualifying
DISAGREE = 0.5  # Scrydex vs the median of verified sales
SALES_DAYS = 180
CHECKS = ("price", "population", "history", "identity", "freshness", "moves", "spikes", "consistency")
LABELS = {"price": "Reliable PSA 10 price", "population": "PSA 10 population", "history": "30D / 90D history", "identity": "Exact card identity",
          "freshness": "Fresh data", "moves": "No unverified moves", "spikes": "No price spikes", "consistency": "Agrees with sales"}
WEIGHTS = {"price": 25, "population": 20, "history": 20, "identity": 10, "freshness": 10, "moves": 5, "spikes": 5, "consistency": 5}


def _settings(settings: Any) -> tuple[int, int]:
    if settings is None:
        from app.config.settings import get_settings

        settings = get_settings()
    return settings.pokemon_top100_min_history, settings.pokemon_top100_max_age_days


def _observations(db: Session, now: datetime) -> dict[int, list[tuple[Any, int]]]:
    """PSA 10 Scrydex valuations of the last year per card, oldest first: (day, cents)."""
    out: dict[int, list[tuple[Any, int]]] = defaultdict(list)
    for cid, day, cents in db.execute(select(PokemonProviderValuation.card_id, PokemonProviderValuation.day, PokemonProviderValuation.market_cents).where(
            PokemonProviderValuation.provider == "scrydex", PokemonProviderValuation.grading_company == GRADE.company,
            PokemonProviderValuation.grade == GRADE.grade, PokemonProviderValuation.market_cents.is_not(None),
            PokemonProviderValuation.day >= (now - timedelta(days=365)).date()).order_by(PokemonProviderValuation.day)):
        out[cid].append((day, cents))
    return out


def _sales(db: Session, now: datetime) -> dict[int, list[tuple[Any, int]]]:
    """Verified PSA 10 sales of the last SALES_DAYS per card: (sale day, USD cents)."""
    out: dict[int, list[tuple[Any, int]]] = defaultdict(list)
    for cid, when, cents in db.execute(select(PokemonSale.card_id, PokemonSale.sale_date, PokemonSale.sale_price_usd_cents).where(
            PokemonSale.verified.is_(True), PokemonSale.excluded.is_(False), PokemonSale.grading_company == GRADE.company,
            PokemonSale.grade == GRADE.grade, PokemonSale.sale_date >= now - timedelta(days=SALES_DAYS))):
        out[cid].append((when.date(), cents))
    return out


def _check(row: dict[str, Any], card: dict[str, Any], ids: dict[str, Any], obs: list[tuple[Any, int]], sales: list[int],
           now: datetime, min_history: int, max_age: int) -> dict[str, Any]:
    checks: dict[str, tuple[bool, str]] = {}
    src = row.get("price_source")
    price = row.get("market_price")
    if price is None:
        checks["price"] = (False, "no PSA 10 price")
    elif src == "pricecharting":
        checks["price"] = (False, "PSA 10 price only from PriceCharting (backup source, no history)")
    else:
        checks["price"] = (True, f"{'verified sales' if src == 'sales' else 'Scrydex'} PSA 10 price")
    pop = row.get("population")
    checks["population"] = (True, f"PSA 10 population {pop:,}") if pop else (False, "no PSA 10 population on record")
    # every real observation counts, whatever the shown price's source: provider daily valuations and verified sale days
    n = len({d for d, _ in obs} | {d for d, _ in sales})
    moves_ok = row.get("change_30d") is not None and row.get("change_90d") is not None
    by = row.get("change_sources") or {}
    fresh_provider = src == "scrydex" and row.get("provider") and         (now.date() - datetime.fromisoformat(row["provider"]["as_of"]).date()).days <= max_age
    obs_txt = f"{n} price observation{'s' if n != 1 else ''} on record"
    if n >= min_history:
        checks["history"] = (True, obs_txt)
    elif moves_ok and (fresh_provider or src == "sales"):
        checks["history"] = (True, f"30D and 90D movements from {', '.join(sorted({by.get('30d') or '?', by.get('90d') or '?'}))}; {obs_txt}")
    else:
        missing = [k.upper() for k in ("30d", "90d") if row.get(f"change_{k}") is None]
        checks["history"] = (False, f"{obs_txt} (needs {min_history})" + (f" and no {' / '.join(missing)} movement" if missing else ""))
    exact = bool(ids.get("scrydex") and ids.get("scrydex_variant"))
    checks["identity"] = (True, f"Scrydex {ids.get('scrydex')} · {ids.get('scrydex_variant')}") if exact else \
        (True, "matched verified sales") if src == "sales" and sales else (False, "exact printing not confirmed by a provider")
    if src == "scrydex" and row.get("provider"):
        age = (now.date() - datetime.fromisoformat(row["provider"]["as_of"]).date()).days
        checks["freshness"] = (age <= max_age, f"Scrydex valuation {age} day{'s' if age != 1 else ''} old" + ("" if age <= max_age else f" (max {max_age})"))
    elif src == "sales" and row.get("last_sale_at"):
        age = (now - datetime.fromisoformat(row["last_sale_at"])).days
        checks["freshness"] = (age <= SALES_DAYS, f"last verified sale {age} days ago")
    else:
        checks["freshness"] = (None, "no valuation to date")  # not applicable: the missing price is the reason
    loud = [(k, row.get(f"change_{k}")) for k in SPIKE_LIMITS if isinstance(row.get(f"change_{k}"), (int, float))
            and abs(row[f"change_{k}"]) > SPIKE_LIMITS[k]]
    backed = (row.get("sales_30d") or 0) > 0
    checks["moves"] = (True, "moves within limits") if not loud else (True, "large move backed by verified sales") if backed else \
        (False, "unverified " + ", ".join(f"{v:+.0f}% {k}" for k, v in loud) + " move")
    all_jumps = [(d1, b / a if a else 0) for (_, a), (d1, b) in zip(obs, obs[1:]) if a and b and (b / a >= JUMP or b / a <= 1 / JUMP)]
    jumps = [j for j in all_jumps if (now.date() - j[0]).days <= SPIKE_DAYS]
    older = len(all_jumps) - len(jumps)
    note = f" ({older} older jump{'s' if older != 1 else ''} before the last {SPIKE_DAYS} days)" if older else ""
    checks["spikes"] = (True, f"no jumps in the last {SPIKE_DAYS} days{note}") if not jumps else \
        (False, f"price history jumps x{jumps[-1][1]:.2f} on {jumps[-1][0].isoformat()}" + (f" (+{len(jumps) - 1} more)" if len(jumps) > 1 else ""))
    sx = (row.get("provider") or {}).get("market")
    recent = [x for x in sales if (now.date() - x[0]).days <= 60]
    sales_cmp = recent if len(recent) >= 3 else sales  # compare with the current market, not a 6-month-old one
    if sx and len(sales_cmp) >= 3:  # Scrydex vs the verified sales of the same card + grade, whichever is the shown price
        med = statistics.median(c for _, c in sales_cmp)
        gap = abs(sx - med) / med
        checks["consistency"] = (gap <= DISAGREE, f"Scrydex {('within' if gap <= DISAGREE else 'off by')} {gap * 100:.0f}% of the median of {len(sales_cmp)} "
                                                  f"verified sales{' (last 60 days)' if sales_cmp is recent else ''}")
    elif sx and (row.get("reference") or {}).get("price"):
        pc = row["reference"]["price"]
        gap = abs(sx - pc) / min(sx, pc)
        checks["consistency"] = (gap <= CROSS_CHECK, f"no verified sales; Scrydex and an independent reference are "
                                                     f"{('within' if gap <= CROSS_CHECK else 'apart by')} {gap * 100:.0f}%")
    else:
        checks["consistency"] = (True, "no verified sales to compare" if len(sales) < 3 else "no Scrydex value to compare")
    score = sum(WEIGHTS[k] for k, (ok, _) in checks.items() if ok)
    if checks["history"][0] is False and n:  # partial credit
        score += round(WEIGHTS["history"] * min(n, min_history) / min_history / 2)  # partial credit, still ineligible
    eligible = all(ok is not False for ok, _ in checks.values()) and checks["price"][0] is True
    confidence = "high" if eligible and score >= 95 and (sales or n >= 4 * min_history) else "medium" if eligible or score >= 70 else "low"
    return {"eligible": eligible, "score": min(score, 100), "confidence": confidence, "history_points": n,
            "checks": [{"code": k, "label": LABELS[k], "ok": checks[k][0], "detail": checks[k][1]} for k in CHECKS],
            "reasons": [checks[k][1] for k in CHECKS if checks[k][0] is False], "failed": [k for k in CHECKS if checks[k][0] is False]}


def assess(db: Session, now: datetime | None = None, settings: Any = None) -> dict[int, dict[str, Any]]:
    """Quality of every catalog card's PSA 10 record (vintage and modern; only vintage can enter the Top 100). Cached with
    the market rows; any sync / import clears it."""
    from app.pokemon import market

    now = now or utcnow()
    min_history, max_age = _settings(settings)

    def build() -> dict[int, dict[str, Any]]:
        # only vintage cards can enter the Top 100: modern ones aren't assessed (a 512 MB server can't hold 50k assessments)
        rows = [r for r in market.base_rows(db, GRADE, now) if r["card"]["era"] == "vintage"]
        ids = {cid: (x or {}) for cid, x in db.execute(select(PokemonCard.id, PokemonCard.external_ids).where(PokemonCard.era == "vintage"))}
        obs, sales = _observations(db, now), _sales(db, now)
        return {r["card"]["id"]: _check(r, r["card"], ids.get(r["card"]["id"], {}), obs.get(r["card"]["id"], []), sales.get(r["card"]["id"], []),
                                        now, min_history, max_age) for r in rows}

    return history.cached(("quality", min_history, max_age), build, ttl=market.BASE_TTL)


def top100(db: Session, now: datetime | None = None, language: str | None = None, settings: Any = None) -> dict[str, Any]:
    """Eligible vintage cards ranked by PSA 10 market cap (at most 100), with how many qualified and why the others didn't."""
    from app.pokemon import market

    now = now or utcnow()
    q = assess(db, now, settings)
    rows = [r for r in market.base_rows(db, GRADE, now) if r["card"]["era"] == "vintage" and (not language or r["card"]["language"] == language)]
    eligible = sorted((r for r in rows if q.get(r["card"]["id"], {}).get("eligible")), key=lambda r: -(r["market_cap"] or 0))
    reasons: dict[str, int] = defaultdict(int)
    for r in rows:
        for k in q.get(r["card"]["id"], {}).get("failed", []):
            reasons[k] += 1
    min_history, max_age = _settings(settings)
    top = [{**market._public(r), "rank": i + 1, "quality": brief(q[r["card"]["id"]])} for i, r in enumerate(eligible[:LIMIT])]
    return {"grade": GRADE.label, "language": language, "rows": top, "as_of": now.isoformat(),
            "summary": {"scope": "vintage (1996-2003) cards, PSA 10", "candidates": len(rows), "eligible": len(eligible), "shown": len(top),
                        "excluded": len(rows) - len(eligible), "limit": LIMIT, "min_history": min_history, "max_age_days": max_age,
                        "excluded_by_reason": {k: reasons[k] for k in CHECKS if reasons.get(k)},
                        "labels": {k: LABELS[k] for k in CHECKS},
                        "total_market_cap": sum(r["market_cap"] for r in top) or None}}


def brief(q: dict[str, Any] | None) -> dict[str, Any] | None:
    return None if q is None else {k: q[k] for k in ("eligible", "score", "confidence", "history_points", "reasons")}


def eligible_ids(db: Session, now: datetime | None = None) -> set[int]:
    return {cid for cid, x in assess(db, now).items() if x["eligible"]}


def candidates_to_improve(db: Session, now: datetime | None = None, fixable: tuple[str, ...] = ("history", "freshness")) -> list[int]:
    """Vintage cards that would qualify if their history / freshness were refreshed (largest market cap first): a price,
    a population, an exact identity, and only `fixable` checks failing."""
    from app.pokemon import market

    now = now or utcnow()
    q = assess(db, now)
    out = []
    for r in market.base_rows(db, GRADE, now):
        x = q.get(r["card"]["id"])
        if r["card"]["era"] == "vintage" and x and not x["eligible"] and r["market_cap"] and set(x["failed"]) <= set(fixable) and r.get("price_source") == "scrydex":
            out.append((r["market_cap"], r["card"]["id"]))
    return [cid for _, cid in sorted(out, reverse=True)]


RECHECK_DAYS = 30  # a card whose Scrydex history was fetched recently is not fetched again (credits)
CHART_TARGETS = LIMIT * 3  # the Top 100 and the next eligible cards: the ones whose charts people open


def chart_gaps(db: Session, now: datetime | None = None, settings: Any = None) -> list[int]:
    """Eligible vintage cards, largest market cap first (the Top 100 and the next ones), whose chart has fewer than the
    minimum real observations, and whose Scrydex history was not already fetched in the last RECHECK_DAYS days."""
    from app.pokemon import market

    now = now or utcnow()
    min_history, _ = _settings(settings)
    q = assess(db, now, settings)
    ranked = [r for r in market.base_rows(db, GRADE, now) if r["card"]["era"] == "vintage" and (q.get(r["card"]["id"]) or {}).get("eligible")]
    ranked = ranked[:CHART_TARGETS]  # base rows are already in market-cap order
    checked = {cid: (x or {}).get("scrydex_history_checked") for cid, x in db.execute(
        select(PokemonCard.id, PokemonCard.external_ids).where(PokemonCard.id.in_([r["card"]["id"] for r in ranked] or [0])))}
    cutoff = (now - timedelta(days=RECHECK_DAYS)).date().isoformat()
    return [r["card"]["id"] for r in ranked
            if q[r["card"]["id"]]["history_points"] < min_history and (checked.get(r["card"]["id"]) or "") < cutoff]


def mark_history_checked(db: Session, card_ids: list[int], now: datetime) -> None:
    for c in db.scalars(select(PokemonCard).where(PokemonCard.id.in_(card_ids or [0]))):
        c.external_ids = {**(c.external_ids or {}), "scrydex_history_checked": now.date().isoformat()}
    db.commit()


def ebay_targets(db: Session, limit: int = 150) -> list[int]:
    """Cards worth an eBay search: the Top 100 first, then the largest vintage caps just outside it."""
    from app.pokemon import market

    now = utcnow()
    top = [r["card"]["id"] for r in top100(db, now)["rows"]]
    seen = set(top)
    vintage = [r for r in market.base_rows(db, GRADE, now) if r["card"]["era"] == "vintage" and r["card"]["id"] not in seen]
    capped = [r["card"]["id"] for r in vintage if r["market_cap"]]  # already ranked by market cap
    priced = sorted((r for r in vintage if not r["market_cap"] and r["market_price"]), key=lambda r: -r["market_price"])
    rest = [r["card"]["id"] for r in vintage if not r["market_cap"] and not r["market_price"]]
    return (top + capped + [r["card"]["id"] for r in priced] + rest)[:limit]


async def improve(db: Session, settings: Any, max_requests: int | None = None, transport: Any = None, target: int = LIMIT + 20) -> dict[str, Any]:
    """Fetch Scrydex price history (and a fresh valuation), within the credit budget of this run, for:
      1. eligible cards near the top (the Top 100 and the next ones) whose chart lacks real observations (chart_gaps), then
      2. near-miss candidates that fail only on history / freshness, largest cap first, while fewer than `target` qualify.
    Each card is fetched at most once per RECHECK_DAYS. Never invents data: a card Scrydex has no history for keeps none."""
    from app.pokemon import scrydex_sync

    budget = settings.pokemon_top100_backfill_requests if max_requests is None else max_requests
    before = len(eligible_ids(db))
    out: dict[str, Any] = {"eligible_before": before, "cards_refreshed": 0, "requests": 0, "runs": []}
    gaps = chart_gaps(db, settings=settings)
    out["chart_gaps"] = len(gaps)
    near = [] if before >= target else [cid for cid in candidates_to_improve(db) if cid not in set(gaps)]
    todo = gaps + near
    if not todo or budget <= 0:
        out["eligible_after"] = before
        return out
    while todo and out["requests"] < budget:
        batch, todo = todo[:20], todo[20:]
        log = await scrydex_sync.sync(db, settings, batch, with_history=True, transport=transport, max_requests=budget - out["requests"],
                                      history_grades=(("PSA", "10"),))
        out["requests"] += log["requests"]
        out["cards_refreshed"] += sum(1 for e in log["entries"] if e.get("status") == "matched")
        out["runs"].append({"cards": len(batch), "requests": log["requests"], "status": log["status"], "error": log["error"]})
        mark_history_checked(db, batch, utcnow())
        history.invalidate()
        if log["error"]:
            break
    out["eligible_after"] = len(eligible_ids(db))
    return out


def record(db: Session, now: datetime | None = None) -> dict[str, Any]:
    """Store today's Top 100 (replacing an earlier assessment of today) and an event for every card that entered or left
    since the last stored Top 100."""
    now = now or utcnow()
    t = top100(db, now)
    q = assess(db, now)
    today = now.date()
    last_day = db.scalar(select(func.max(PokemonTop100Snapshot.day)))
    before = {s.card_id: s.rank for s in db.scalars(select(PokemonTop100Snapshot).where(PokemonTop100Snapshot.day == last_day))} if last_day else {}
    for s in db.scalars(select(PokemonTop100Snapshot).where(PokemonTop100Snapshot.day == today)):
        db.delete(s)
    db.flush()
    now_ids: dict[int, int] = {}
    for r in t["rows"]:
        cid = r["card"]["id"]
        now_ids[cid] = r["rank"]
        db.add(PokemonTop100Snapshot(day=today, card_id=cid, rank=r["rank"], market_cap_cents=r["market_cap"], price_cents=r["market_price"],
                                     price_source=r["price_source"] or "", population=r["population"], history_points=r["quality"]["history_points"],
                                     score=r["quality"]["score"], confidence=r["quality"]["confidence"]))
    events = 0
    for cid, rank in now_ids.items():
        if cid not in before:
            db.add(PokemonTop100Event(at=now, card_id=cid, kind="entered", rank=rank, reasons=[f"eligible, rank {rank} by PSA 10 market cap"]))
            events += 1
    for cid, rank in before.items():
        if cid not in now_ids:
            why = (q.get(cid) or {}).get("reasons") or ["outranked: larger eligible market caps took its place"]
            db.add(PokemonTop100Event(at=now, card_id=cid, kind="left", rank=rank, reasons=why))
            events += 1
    db.commit()
    return {"day": today.isoformat(), "members": len(now_ids), "entered": sum(1 for c in now_ids if c not in before),
            "left": sum(1 for c in before if c not in now_ids), "events": events, "eligible": t["summary"]["eligible"]}
