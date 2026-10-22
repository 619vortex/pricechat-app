"""Portfolio valuation: the estimated value of one copy of a holding, with its range, source, confidence and time.

GRADED (PSA / BGS / CGC + official grade): the market's own price for that EXACT grade, with the market's precedence:
  1. Price Chat's verified-sales estimate for the card at that grade (pricing engine), else
  2. Scrydex's current valuation for that exact grade (provider estimate, labelled), else
  3. no value ("Price unavailable"). A PSA 9 is never valued as a PSA 10; no grade is ever substituted.
  PriceCharting is not used here: its licence covers internal use only, and portfolios can be public.

RAW (not professionally graded): real ungraded market prices by condition (Scrydex raw prices: Near Mint, Lightly Played,
Moderately Played, Heavily Played, Damaged). A raw card's condition is unknown until estimated, so:
  - no condition estimate: a conservative range spanning roughly a PSA 4-8 equivalent, Moderately Played .. Near Mint, with
    the Lightly Played price as the point estimate (confidence low: condition not assessed);
  - with an estimate (owner or AI, e.g. "5-7 equivalent"): the range of the conditions it spans, point at its lower middle;
  - no raw prices for that printing: a GRADE-EQUIVALENT fallback from the same printing's real PSA prices for the grades the
    condition range spans, minus what grading costs (a raw copy is worth a graded copy less the cost of grading it); if that
    leaves nothing, "Price unavailable". Never from PSA 10 alone, never averaged across all grades, never invented.
  A raw card never gets an official grade, and grade-equivalents are labelled as estimates, not grades.

GRADED, exact grade unpriced: a COMPARABLE-GRADES estimate from the same printing's nearest priced grades below and above
(e.g. BGS 7.5 between PSA 7 and PSA 8): the point is the LOWER grade's price (conservative), the range runs to the upper
one; labelled "comparable grades", low confidence. Never priced from a higher grade alone.

Every result carries a machine-readable `reason` (see REASONS), so unpriced holdings say exactly why.

Batched: one set of bounded queries for any list of holdings (never the whole catalogue).
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.pokemon import PokemonProviderValuation
from app.pokemon import history, pricing

RAW_ORDER = ("DMG", "HP", "MP", "LP", "NM")
RAW_LABEL = {"NM": "Near Mint", "LP": "Lightly Played", "MP": "Moderately Played", "HP": "Heavily Played", "DMG": "Damaged"}
DEFAULT_RAW = (4.0, 8.0)  # condition unknown: considered as roughly a 4-8 grade-equivalent
STALE_DAYS = 21  # a provider value older than this lowers confidence
GRADING_COST = 25_00  # what grading one card typically costs (submission + shipping, value tier): taken off graded prices
REASONS = {
    "priced": "Exact grade / condition price",
    "comparable": "Estimated from comparable grades of the same printing",
    "grade_equivalent": "Raw card estimated from graded prices (less grading cost)",
    "catalog_match_missing": "The card has no provider link, so no market data can be fetched",
    "grade_price_unavailable": "No market price for this grade (or nearby grades) of this printing",
    "raw_prices_unavailable": "No ungraded market prices for this printing",
    "provider_error": "The price provider didn't answer; retried automatically",
    "identification_incomplete": "The card's identity needs confirming",
}


def grade_number(company: str, grade: str) -> float | None:
    try:
        return float(grade)
    except (TypeError, ValueError):
        return None


def raw_condition(equivalent: float) -> str:
    """Numeric grade-equivalent -> the raw market's condition bucket (an approximation, labelled as such)."""
    if equivalent >= 7:
        return "NM"
    if equivalent >= 5:
        return "LP"
    if equivalent >= 3:
        return "MP"
    if equivalent >= 2:
        return "HP"
    return "DMG"


@dataclass
class Value:
    cents: int | None
    low: int | None
    high: int | None
    source: str  # sales | scrydex | scrydex_raw | none
    confidence: str | None  # high | medium | low
    note: str
    as_of: str | None = None
    reason: str = "priced"

    def as_dict(self) -> dict[str, Any]:
        return {"value": self.cents, "low": self.low, "high": self.high, "source": self.source, "confidence": self.confidence,
                "note": self.note, "as_of": self.as_of, "reason": self.reason, "reason_label": REASONS.get(self.reason, self.reason)}


def _latest_provider(db: Session, card_ids: list[int]) -> dict[tuple[int, str, str], PokemonProviderValuation]:
    out: dict[tuple[int, str, str], PokemonProviderValuation] = {}
    for i in range(0, len(card_ids), 500):  # bounded IN lists
        for r in db.scalars(select(PokemonProviderValuation).where(
                PokemonProviderValuation.card_id.in_(card_ids[i:i + 500]), PokemonProviderValuation.kind == "current",
                PokemonProviderValuation.currency == "USD", PokemonProviderValuation.market_cents.is_not(None))
                .order_by(PokemonProviderValuation.day)):
            out[(r.card_id, r.grading_company, r.grade)] = r  # latest day wins
    return out


class Pricer:
    """Values holdings. Load once for a batch of holdings (bounded by their card ids), then call value() per holding."""

    def __init__(self, db: Session, holdings: Iterable[Any], now: datetime | None = None) -> None:
        self.now = now or utcnow()
        hs = list(holdings)
        ids = sorted({h.card_id for h in hs})
        self.prov = _latest_provider(db, ids) if ids else {}
        graded_keys = {(h.card_id, h.grading_company, h.grade) for h in hs if h.kind == "graded" and h.grading_company and h.grade}
        self.sales: dict[tuple[int, str, str], pricing.Stats] = {}
        for cid, comp, grade in graded_keys:
            pts = history.load_sales(db, [cid], comp, grade).get((cid, comp, grade), [])
            if pts:
                self.sales[(cid, comp, grade)] = pricing.compute(pts, self.now)

    def _fresh(self, r: PokemonProviderValuation) -> bool:
        return (self.now.date() - r.day) <= timedelta(days=STALE_DAYS)

    def graded(self, card_id: int, company: str, grade: str) -> Value:
        st = self.sales.get((card_id, company, grade))
        if st is not None and st.market_price is not None:
            return Value(st.market_price, st.low_30d, st.high_30d,
                         "sales", {"HIGH": "high", "MEDIUM": "medium"}.get(str(st.confidence).upper(), "low"),
                         f"Price Chat estimate from {st.sales_count} verified {company} {grade} sale{'s' if st.sales_count != 1 else ''}",
                         st.last_sale_at.isoformat() if st.last_sale_at else None)
        pv = self.prov.get((card_id, company, grade))
        if pv is not None:
            fresh = self._fresh(pv)
            # no range: Scrydex's low / high are the spread of listings, not a value range (often 10x apart)
            return Value(pv.market_cents, None, None, "scrydex", "medium" if fresh else "low",
                         f"Scrydex market estimate for {company} {grade} (provider estimate, not a sale)" + ("" if fresh else f"; last updated {pv.day.isoformat()}"),
                         pv.day.isoformat())
        return self._comparable(card_id, company, grade)

    def _graded_prices(self, card_id: int) -> dict[tuple[str, float], PokemonProviderValuation]:
        out: dict[tuple[str, float], PokemonProviderValuation] = {}
        for (cid, comp, gr), r in self.prov.items():
            n = grade_number(comp, gr) if cid == card_id and comp != "RAW" else None
            if n is not None:
                out[(comp, n)] = r
        return out

    def _comparable(self, card_id: int, company: str, grade: str) -> Value:
        """Exact grade unpriced: the nearest priced grades of the SAME printing below and above (same company first, then
        PSA). Point = the lower one (conservative); range up to the upper one. No lower neighbour: unavailable."""
        target = grade_number(company, grade)
        prices = self._graded_prices(card_id)
        if target is None or not prices:
            return Value(None, None, None, "none", None, f"Price unavailable: no {company} {grade} market data for this printing", reason="grade_price_unavailable")
        for comp in dict.fromkeys((company, "PSA", "BGS", "CGC")):
            below = sorted((n, r) for (c, n), r in prices.items() if c == comp and n < target and target - n <= 2)
            above = sorted((n, r) for (c, n), r in prices.items() if c == comp and n > target and n - target <= 2)
            if below:
                lo_n, lo = below[-1]
                hi_n, hi = above[0] if above else (None, None)
                if hi is not None and hi.market_cents < lo.market_cents:
                    hi = None  # an upper grade priced below the lower one: no range from it
                fresh = (self.now.date() - lo.day) <= timedelta(days=STALE_DAYS)
                label = f"{comp} {lo_n:g}" + (f" and {comp} {hi_n:g}" if hi is not None else "")
                return Value(lo.market_cents, lo.market_cents, hi.market_cents if hi is not None else None, "comparable", "low",
                             f"No {company} {grade} price for this printing: estimated from its {label} prices "
                             f"({company} {grade} sits {'between them' if hi is not None else 'just above'}). Conservative: the lower grade's price."
                             + ("" if fresh else f" Prices last updated {lo.day.isoformat()}."),
                             lo.day.isoformat(), reason="comparable")
        return Value(None, None, None, "none", None, f"Price unavailable: no {company} {grade} price, and no nearby lower grade priced for this printing",
                     reason="grade_price_unavailable")

    def raw(self, card_id: int, low_eq: float | None, high_eq: float | None) -> Value:
        prices = {c: self.prov[(card_id, "RAW", c)] for c in RAW_ORDER if (card_id, "RAW", c) in self.prov}
        assessed = low_eq is not None and high_eq is not None
        lo_eq, hi_eq = (low_eq, high_eq) if assessed else DEFAULT_RAW
        lo_eq, hi_eq = min(lo_eq, hi_eq), max(lo_eq, hi_eq)  # type: ignore[type-var]
        if not prices:
            return self._grade_equivalent(card_id, lo_eq, hi_eq, assessed)
        span = RAW_ORDER[RAW_ORDER.index(raw_condition(lo_eq)): RAW_ORDER.index(raw_condition(hi_eq)) + 1]
        got = [(c, prices[c].market_cents) for c in span if c in prices]
        if not got:
            have = ", ".join(RAW_LABEL[c] for c in prices)
            return Value(None, None, None, "none", None, f"Price unavailable for this condition range (raw prices exist only for {have})")
        # point: the lower middle of the range (conservative); when the condition is unknown, Lightly Played if priced
        point_cond = "LP" if not assessed and "LP" in prices else span[max(0, (len(span) - 1) // 2)]
        point = prices[point_cond].market_cents if point_cond in prices else sorted(v for _, v in got)[(len(got) - 1) // 2]
        vals = [v for _, v in got]
        oldest = min(prices[c].day for c, _ in got)
        fresh = (self.now.date() - oldest) <= timedelta(days=STALE_DAYS)
        conf = ("medium" if fresh else "low") if assessed else "low"
        conds = f"{RAW_LABEL[span[0]]}" + (f" to {RAW_LABEL[span[-1]]}" if len(span) > 1 else "")
        how = (f"estimated condition {lo_eq:g}-{hi_eq:g} equivalent" if assessed else
               "condition not assessed: considered roughly a 4-8 equivalent")
        return Value(point, min(vals), max(vals), "scrydex_raw", conf,
                     f"Ungraded market prices ({conds}); {how}. Not professionally graded.", oldest.isoformat())

    def _grade_equivalent(self, card_id: int, lo_eq: float, hi_eq: float, assessed: bool) -> Value:
        """No raw prices: the same printing's PSA prices for the grades the condition range spans, less grading cost."""
        psa = {n: r for (c, n), r in self._graded_prices(card_id).items() if c == "PSA" and lo_eq <= n <= hi_eq}
        if not psa:
            return Value(None, None, None, "none", None, "Price unavailable: no ungraded prices, and no PSA prices in this condition range for this printing",
                         reason="raw_prices_unavailable")
        nets = sorted((n, r.market_cents - GRADING_COST, r) for n, r in psa.items())
        usable = [(n, v, r) for n, v, r in nets if v > 0]
        if not usable:
            top = max(r.market_cents for _, _, r in nets)
            return Value(None, None, None, "none", None,
                         f"Price unavailable: no ungraded prices, and this printing's graded prices (up to ${top / 100:,.2f}) don't exceed the cost of grading,"
                         " so a raw value can't be inferred from them", reason="raw_prices_unavailable")
        vals = [v for _, v, _ in usable]
        point = sorted(vals)[(len(vals) - 1) // 2] if assessed else min(vals)  # condition unknown: the lowest (conservative)
        grades = ", ".join(f"PSA {n:g}" for n, _, _ in usable)
        oldest = min(r.day for _, _, r in usable)
        return Value(point, min(vals), max(vals), "grade_equivalent", "low",
                     f"No ungraded prices for this printing: estimated from its {grades} prices less ${GRADING_COST / 100:.0f} grading cost "
                     f"({'estimated condition' if assessed else 'condition not assessed, considered'} {lo_eq:g}-{hi_eq:g} equivalent). Not professionally graded.",
                     oldest.isoformat(), reason="grade_equivalent")

    def value(self, h: Any) -> Value:
        if h.kind == "graded" and h.grading_company and h.grade:
            return self.graded(h.card_id, h.grading_company, h.grade)
        return self.raw(h.card_id, h.condition_low, h.condition_high)


def apply(h: Any, v: Value, now: datetime | None = None) -> None:
    """Store a valuation on the holding (per copy)."""
    h.value_cents, h.value_low_cents, h.value_high_cents = v.cents, v.low, v.high
    h.value_source, h.value_confidence, h.value_note = v.source, v.confidence, v.note
    h.value_reason = v.reason
    h.valued_at = now or utcnow()
