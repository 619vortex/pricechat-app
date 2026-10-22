"""Price engine v2: stored sale records -> market statistics for ONE card + ONE grading company + ONE grade.

Market price = weighted median of VERIFIED sales:
  window   last 90 days; if that holds fewer than MIN_SALES_90D sales, the last 365 days (older sales weigh 0.25)
  weights  0-7 days 1.0 | 8-30 days 0.8 | 31-90 days 0.5 | older 0.25; with 3+ sales no single sale may carry more than
           MAX_SHARE of the total weight (one odd sale can't set the price)
  outliers with 5+ sales, a sale further than OUTLIER_MADS robust deviations (MAD) AND more than 50% away from the median
           is marked "outlier" and left out of the price; with 3-4 sales, one more than 3x above / below the median of the
           others is. It stays in the history and in the provenance.
Unverified sales and sales an admin excluded are never priced (listed in the provenance with the reason).
No eligible sale -> no market price (None, shown as "—").

Confidence (HIGH / MEDIUM / LOW) from the number of recent verified sales, the age of the latest one, how much the sales
disagree (coefficient of variation) and source quality (sales with a source reference: listing id or URL).
Estimated market cap = market price x known population of that exact grade (graded only), else None.
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from statistics import mean, median, pstdev
from typing import Any, Iterable

ALGORITHM = "price-v2.1 (verified sales, 90d weighted median, step weights 1.0/0.8/0.5/0.25, MAD outliers, 3x small-sample outliers)"
WINDOW_DAYS = 90
FALLBACK_DAYS = 365
MIN_SALES_90D = 3
MAX_SHARE = 1 / 3
OUTLIER_MIN_SALES = 5
OUTLIER_MADS = 3.5
SMALL_OUTLIER_MIN_SALES = 3  # 3-4 sales: ratio test against the median of the others
SMALL_OUTLIER_RATIO = 3.0


def weight_for(age_days: float) -> float:
    if age_days <= 7:
        return 1.0
    if age_days <= 30:
        return 0.8
    if age_days <= 90:
        return 0.5
    return 0.25


@dataclass(frozen=True, slots=True)
class SalePoint:
    price_cents: int
    when: datetime
    sale_id: int | None = None
    verified: bool = True
    excluded: bool = False  # an admin excluded it
    source: str = ""
    has_reference: bool = False  # listing id or URL: traceable to the source


@dataclass(slots=True)
class Stats:
    market_price: int | None = None
    confidence: str | None = None
    confidence_reasons: list[str] = field(default_factory=list)
    last_sale: int | None = None
    last_sale_at: datetime | None = None
    sales_count: int = 0  # sales the price uses
    sales_7d: int = 0
    sales_30d: int = 0
    sales_90d: int = 0
    volume_7d: int = 0  # cents, verified sales
    volume_30d: int = 0
    median_30d: int | None = None
    median_90d: int | None = None
    average_30d: int | None = None
    high_30d: int | None = None
    low_30d: int | None = None
    window_days: int = WINDOW_DAYS

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["last_sale_at"] = self.last_sale_at.isoformat() if self.last_sale_at else None
        return d


def weighted_median(values: list[tuple[int, float]]) -> int | None:
    """The value where the cumulative weight first reaches half the total (ties between two values: their mean)."""
    vals = sorted((v, w) for v, w in values if w > 0)
    if not vals:
        return None
    total = sum(w for _, w in vals)
    acc = 0.0
    for i, (v, w) in enumerate(vals):
        acc += w
        if acc >= total / 2:
            if abs(acc - total / 2) < 1e-12 and i + 1 < len(vals):
                return round((v + vals[i + 1][0]) / 2)
            return v
    return vals[-1][0]


def _age(now: datetime, when: datetime) -> float:
    return (now - when).total_seconds() / 86400


def evaluate(sales: Iterable[SalePoint], now: datetime) -> tuple[Stats, dict[str, Any]]:
    """(statistics, provenance). The provenance explains every sale: used with its weight, or left out and why."""
    pts = sorted(sales, key=lambda s: s.when)
    st = Stats()
    prov: dict[str, Any] = {"algorithm": ALGORITHM, "included": [], "excluded": [], "window_days": WINDOW_DAYS}
    eligible: list[SalePoint] = []
    for s in pts:
        if not s.verified:
            prov["excluded"].append(_row(s, now, reason="not verified"))
        elif s.excluded:
            prov["excluded"].append(_row(s, now, reason="excluded by an admin"))
        elif _age(now, s.when) < -1:
            prov["excluded"].append(_row(s, now, reason="dated in the future"))
        else:
            eligible.append(s)
    if eligible:
        st.last_sale, st.last_sale_at = eligible[-1].price_cents, eligible[-1].when
    within = lambda days: [s for s in eligible if _age(now, s.when) <= days]  # noqa: E731
    d7, d30, d90 = within(7), within(30), within(90)
    st.sales_7d, st.sales_30d, st.sales_90d = len(d7), len(d30), len(d90)
    st.volume_7d, st.volume_30d = sum(s.price_cents for s in d7), sum(s.price_cents for s in d30)
    p30 = [s.price_cents for s in d30]
    if p30:
        st.median_30d, st.average_30d, st.high_30d, st.low_30d = round(median(p30)), round(mean(p30)), max(p30), min(p30)
    if d90:
        st.median_90d = round(median([s.price_cents for s in d90]))
    window = d90
    if len(d90) < MIN_SALES_90D:
        window, st.window_days = within(FALLBACK_DAYS), FALLBACK_DAYS
        prov["window_days"] = FALLBACK_DAYS
    for s in eligible:
        if s not in window:
            prov["excluded"].append(_row(s, now, reason=f"older than {st.window_days} days"))
    # outliers (robust: median absolute deviation)
    used = list(window)
    if len(used) >= OUTLIER_MIN_SALES:
        med = median(s.price_cents for s in used)
        mad = median(abs(s.price_cents - med) for s in used) * 1.4826
        if mad > 0:
            keep = []
            for s in used:
                far = abs(s.price_cents - med)
                if far > OUTLIER_MADS * mad and far > 0.5 * med:
                    prov["excluded"].append(_row(s, now, reason=f"outlier: {far / med:.0%} from the median of {len(used)} sales"))
                else:
                    keep.append(s)
            used = keep
    elif len(used) >= SMALL_OUTLIER_MIN_SALES:
        # too few sales for a MAD: a sale more than SMALL_OUTLIER_RATIO x above / below the median of the OTHER sales is
        # almost always a mismatched edition / grade or a shill, so it is left out (and listed in the provenance)
        keep = []
        for s in used:
            others = median(o.price_cents for o in used if o is not s)
            ratio = s.price_cents / others if others else 1.0
            if ratio > SMALL_OUTLIER_RATIO or ratio < 1 / SMALL_OUTLIER_RATIO:
                prov["excluded"].append(_row(s, now, reason=f"outlier: {ratio:.1f}x the median of the other {len(used) - 1} sales"))
            else:
                keep.append(s)
        used = keep
    weights = [(s, weight_for(_age(now, s.when))) for s in used]
    if len(weights) >= 3:
        cap = sum(w for _, w in weights) * MAX_SHARE
        weights = [(s, min(w, cap)) for s, w in weights]
    st.sales_count = len(weights)
    st.market_price = weighted_median([(s.price_cents, w) for s, w in weights])
    prov["included"] = [_row(s, now, weight=round(w, 4)) for s, w in sorted(weights, key=lambda x: x[0].when, reverse=True)]
    prov["excluded"].sort(key=lambda r: r["sale_date"], reverse=True)
    st.confidence, st.confidence_reasons = confidence(used, st, now)
    prov["confidence"] = {"level": st.confidence, "reasons": st.confidence_reasons}
    return st, prov


def compute(sales: Iterable[SalePoint], now: datetime) -> Stats:
    return evaluate(sales, now)[0]


def _row(s: SalePoint, now: datetime, weight: float | None = None, reason: str | None = None) -> dict[str, Any]:
    r: dict[str, Any] = {"sale_id": s.sale_id, "price": s.price_cents, "sale_date": s.when.isoformat(), "age_days": round(_age(now, s.when), 1),
                         "source": s.source}
    if weight is not None:
        r["weight"] = weight
    if reason is not None:
        r["reason"] = reason
    return r


def confidence(used: list[SalePoint], st: Stats, now: datetime) -> tuple[str | None, list[str]]:
    if st.market_price is None or not used:
        return None, ["no verified sales to price from"]
    recent = [s for s in used if _age(now, s.when) <= WINDOW_DAYS]
    latest = min(_age(now, s.when) for s in used)
    prices = [s.price_cents for s in used]
    cv = pstdev(prices) / mean(prices) if len(prices) >= 2 and mean(prices) else 0.0
    referenced = sum(1 for s in used if s.has_reference)
    why = [f"{len(recent)} verified sale{'s' if len(recent) != 1 else ''} in {WINDOW_DAYS} days", f"latest {latest:.0f} days ago"]
    if len(prices) >= 2:
        why.append(f"spread {cv:.0%}")
    if len(recent) >= 10 and latest <= 30 and cv <= 0.35:
        level = "HIGH"
    elif len(recent) >= 3 and latest <= WINDOW_DAYS:
        level = "MEDIUM"
    else:
        level = "LOW"
    if level == "HIGH" and referenced * 2 < len(used):
        level = "MEDIUM"
        why.append("most sales have no source reference")
    if level == "MEDIUM" and cv > 0.6:
        level = "LOW"
        why.append("sales disagree strongly")
    return level, why


def market_cap(price_cents: int | None, population: int | None, graded: bool) -> int | None:
    """ESTIMATED market cap: price x known population of that grade. None unless both are known (and it's graded)."""
    if not graded or price_cents is None or population is None or population <= 0:
        return None
    return price_cents * population


def change(current: int | None, past: int | None) -> float | None:
    """Percentage move between two market observations, or None when either is unknown."""
    if current is None or past is None or past == 0:
        return None
    return round((current - past) / past * 100, 2)


def since(now: datetime, days: float) -> datetime:
    return now - timedelta(days=days)
