"""Price movements (30D / 90D) and the table's 90-day sparkline, for one card + grade.

Vintage cards are illiquid collectibles: the market reads them over months, not hours, so there are no 24h / 7d moves.

Source priority for a movement (each value says which one it came from; nothing is mixed into one number):
  1. "sales"            Price Chat's verified-sales estimate now vs the estimate in force at the start of the period,
                        only when a verified sale happened since (history.period_change). The ONLY source for a card
                        whose shown price is the verified-sales estimate: a movement always belongs to the price shown
                        (a Scrydex trend next to a sales price would describe a different number).
  For a card priced by Scrydex:
  2. "scrydex"          Scrydex's own published trend for that period (trends.days_30 / days_90 / days_180) on the
                        current PSA-grade valuation. Valid on its own: it does not need our database to have history.
  3. "scrydex_history"  Scrydex's current valuation vs its recorded valuation (provider price history, or a daily
                        valuation Price Chat stored itself) at the start of the period. Only when an observation exists
                        at most MAX_BASE_GAP days before the start; never extrapolated.
PriceCharting values are never used for movements: its API has no history, and a current price is not a past one.

Sparkline: genuine observations of the shown price's own source in the last 90 days, nothing carried forward or repeated:
verified-sales estimates on sale days (+ today's estimate) for a sales price; Scrydex daily valuations for a Scrydex price.
At least two observations on different days, otherwise no sparkline (and the reason).
"""

from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from app.pokemon import history, pricing

PERIODS = {"30d": 30, "90d": 90}
TREND_KEYS = {"30d": "days_30", "90d": "days_90"}
MAX_BASE_GAP = 45  # days: a recorded value further before the period start than this is too old to be its baseline
SPARK_DAYS = 90
SOURCE_LABELS = {"sales": "Verified sales", "scrydex": "Scrydex trend", "scrydex_history": "Scrydex price history"}

Obs = tuple[date, int]  # (day, USD cents)


def _label(days: int) -> str:
    return f"{days} days"


def scrydex_trend(trends: dict[str, Any] | None, period: str) -> float | None:
    key = TREND_KEYS.get(period)
    v = ((trends or {}).get(key) or {}).get("percent_change") if key else None
    return round(float(v), 2) if isinstance(v, (int, float)) else None


def from_series(series: list[Obs], current: int | None, now: datetime, days: int) -> dict[str, Any] | None:
    """current vs the last recorded value on or before the period start (at most MAX_BASE_GAP days before it)."""
    if not current or not series:
        return None
    start = (now - timedelta(days=days)).date()
    base = None
    for d, v in series:
        if d <= start:
            base = (d, v)
        else:
            break
    if base is None or not base[1] or (start - base[0]).days > MAX_BASE_GAP:
        return None
    return {"value": pricing.change(current, base[1]), "base": base[1], "base_day": base[0].isoformat()}


def movements(price_source: str | None, points: list[Any], price: int | None, trends: dict[str, Any] | None,
              series: list[Obs], scrydex_price: int | None, now: datetime) -> dict[str, dict[str, Any]]:
    """{period: {"value", "source", "reason", "base", "base_day"}} for 30d / 90d."""
    out: dict[str, dict[str, Any]] = {}
    first = series[0][0] if series else None
    for k, days in PERIODS.items():
        res: dict[str, Any] = {"value": None, "source": None, "reason": None, "base": None, "base_day": None}
        out[k] = res
        if price is None:
            res["reason"] = "no current price"
            continue
        if price_source == "pricecharting":
            res["reason"] = "PriceCharting publishes no price history"
            continue
        why = []
        if price_source == "sales":
            c = history.period_change(points, price, now, days)
            if c["value"] is not None:
                res.update(value=c["value"], source="sales", base=c["base"], base_day=c["base_day"])
            else:
                res["reason"] = c["reason"]
            continue
        if scrydex_price:
            t = scrydex_trend(trends, k)
            if t is not None:
                res.update(value=t, source="scrydex")
                continue
            c2 = from_series(series, scrydex_price, now, days)
            if c2 is not None:
                res.update(source="scrydex_history", **c2)
                continue
            why.append(f"Scrydex publishes no {_label(days)} trend for this card" if k in TREND_KEYS
                       else f"Scrydex publishes no {_label(days)} trend")
            start = (now - timedelta(days=days)).date()
            if first and first > start - timedelta(days=MAX_BASE_GAP):
                why.append(f"needs a recorded value from about {start.isoformat()}; price history on record begins {first.isoformat()}")
            elif not first:
                why.append("no price history on record")
        else:
            why.append("no Scrydex valuation")
        res["reason"] = "; ".join(why)
    return out


def sparkline(price_source: str | None, points: list[Any], price: int | None, series: list[Obs], now: datetime,
              days: int = SPARK_DAYS) -> tuple[dict[str, Any] | None, str | None]:
    """(sparkline or None, why none)."""
    start = (now - timedelta(days=days)).date()
    obs: list[Obs]
    if price_source == "sales":
        obs = [(p.day, p.market_price_cents) for p in points if start <= p.day <= now.date()]
        if price is not None and obs and obs[-1][0] < now.date() and obs[-1][1] != price:
            obs.append((now.date(), price))  # today's estimate, only when it differs (a repeat would be a fake flat tail)
        source = "sales"
    elif price_source == "scrydex":
        obs = [(d, v) for d, v in series if d >= start]
        source = "scrydex"
    elif price_source == "pricecharting":
        return None, "PriceCharting publishes no price history"
    else:
        return None, "no price"
    if len({d for d, _ in obs}) < 2:
        return None, "only one observation on record" if obs else "no observation in the last year"
    ts = lambda d: int(datetime.combine(d, time.min, timezone.utc).timestamp())  # noqa: E731
    return {"points": [[ts(d), v] for d, v in obs], "source": source, "from": obs[0][0].isoformat(), "to": obs[-1][0].isoformat(),
            "span_days": (obs[-1][0] - obs[0][0]).days, "observations": len(obs)}, None
