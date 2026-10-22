"""Fact card: derived comparisons computed in code, so the writer model never does arithmetic.

The local writer (gemma) can't calculate, and the fact checker rejects any number that isn't in the research. That used
to cost the two strongest hook shapes (Comparison, Time Jump) their best material: "2.6x the April sale", "+160% in 6
months". Here those comparisons are computed from the brief's own numbers and handed to the writer as plain strings it
can quote. `safety` recomputes the same card from the stored brief, so every derived number stays verifiable.

    card = derive(brief)   ->  {"derived": ["2.6x the Apr 20, 2026 sale", ...], "derived_from": ["17225.41 / 6750", ...]}

Only comparisons with a real anchor in the input are made: two values with the same unit and the same period, a value
over its own count, a chart's own first/last/high, dates in the facts. Rounded to at most 2 significant figures.
External anchors (e.g. "13x a 2019 Golf GTI") are used only when the brief carries them in `anchors` with a source."""

import math
import re
from datetime import date, datetime, timezone
from typing import Any

MONTHS = {m: i + 1 for i, m in enumerate("january february march april may june july august september october november december".split())}
DATE_RX = re.compile(r"\b(January|February|March|April|May|June|July|August|September|October|November|December) (\d{1,2}), (\d{4})\b")
CURRENCY = {"usd": "$", "eur": "€", "gbp": "£", "aed": "AED "}
SCALE = {"bn": 1e9, "m": 1e6, "k": 1e3}
MAX_DERIVED = 10


def sig2(x: float) -> float:
    """At most 2 significant figures (2.5521 -> 2.6, 155.2 -> 160, 10475 -> 10000)."""
    if x == 0 or not math.isfinite(x):
        return 0.0
    d = 2 - int(math.floor(math.log10(abs(x)))) - 1
    return round(x, d)


def _num(x: float) -> str:
    x = sig2(x)
    return f"{x:,.0f}" if float(x).is_integer() else f"{x:g}"


def money(v: float, cur: str = "$") -> str:
    """$10K, $1.2m, AED 3.4m, €550m: compact, 2 significant figures."""
    v = sig2(v)
    for unit, div in (("bn", 1e9), ("m", 1e6), ("K", 1e3)):
        if abs(v) >= div:
            return f"{cur}{_num(v / div)}{unit if unit != 'K' else 'K'}"
    return f"{cur}{_num(v)}"


def pct(change: float) -> str:
    return f"{'+' if change > 0 else ''}{_num(change)}%"


# --- reading the brief into labelled values -----------------------------------------------------------------------------
def _parse_key(key: str) -> dict[str, Any]:
    """'transactions_aed_bn_2025' -> {base: 'transactions', currency: 'AED ', scale: 1e9, year: '2025'}."""
    k = key.lower()
    year = ""
    m = re.match(r"(.+?)_((?:19|20)\d\d)$", k)
    if m:
        k, year = m.group(1), m.group(2)
    m = re.search(r"\b((?:19|20)\d\d)\b|_(h\d_(?:19|20)\d\d|q\d_fy\d+|fy\d+)_?", k)
    period = (m.group(1) or m.group(2)) if m else ""
    scale = 1.0
    for s, mult in SCALE.items():
        if k.endswith("_" + s):
            scale, k = mult, k[: -len(s) - 1]
            break
    cur = ""
    for c, sym in CURRENCY.items():
        if k.endswith("_" + c):
            cur, k = sym, k[: -len(c) - 1]
            break
    unit = "pct" if k.endswith("_pct") else ("count" if re.search(r"(_count|_units|total_built|robots|investors|projects_completed|_new_units)$", k) else "")
    if unit == "pct":
        k = k[:-4]
    return {"base": k, "currency": cur, "scale": scale, "year": year or period, "unit": unit or ("money" if cur else "")}


def values(brief: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for key, v in (brief.get("numbers") or {}).items():
        if isinstance(v, bool) or not isinstance(v, (int, float)) or key == "year":
            continue
        meta = _parse_key(key)
        out.append({"key": key, "value": float(v) * meta["scale"], **meta})
    return out


def label(v: dict[str, Any]) -> str:
    base = v["base"]
    for noise in ("_usd", "_eur", "_aed", "_gaap"):
        base = base.replace(noise, "")
    base = re.sub(r"\b(h\d|q\d|fy\d+)\b", lambda m: m.group(0).upper(), base.replace("_", " "))
    return base.strip()


def noun(v: dict[str, Any]) -> str:
    """What a value counts or prices, singular: 'h1_2026_projects_completed' -> 'project', 'transactions_count' -> 'transaction'."""
    words = [w for w in v["base"].split("_") if w and not re.fullmatch(r"(h\d|q\d|fy\d+|(19|20)\d\d|count|completed|new|total|value|aed|usd|eur)", w)]
    w = words[0] if words else ""
    return w[:-1] if w.endswith("s") and not w.endswith("ss") else w


def dates(brief: dict[str, Any]) -> list[date]:
    text = " ".join(str(f.get("text") if isinstance(f, dict) else f) for f in brief.get("facts") or []) + " " + str(brief.get("headline") or "")
    out = sorted({date(int(y), MONTHS[m.lower()], int(d)) for m, d, y in DATE_RX.findall(text)})
    return out


def _d(t: float) -> str:
    dt = datetime.fromtimestamp(t, tz=timezone.utc)
    return f"{dt:%b} {dt.day}, {dt.year}"


# --- the derivations ----------------------------------------------------------------------------------------------------
def derive(brief: dict[str, Any]) -> dict[str, list[str]]:
    lines: list[tuple[str, str]] = []

    def add(text: str, how: str) -> None:
        if text not in (t for t, _ in lines):
            lines.append((text, how))

    ch = brief.get("chart") or {}
    pts = [(float(p[0]), float(p[1])) for p in ch.get("points") or [] if isinstance(p, (list, tuple)) and len(p) >= 2 and p[1]]
    cur = str(ch.get("prefix") or "$")
    if len(pts) >= 2:
        first, last = pts[0], pts[-1]
        hi = max(pts, key=lambda p: p[1])
        lo = min(pts, key=lambda p: p[1])
        kind = "sale" if ch.get("kind") == "sales" else "close"
        if last[1] / first[1] >= 1.5:
            add(f"{_num(last[1] / first[1])}x the {_d(first[0])} {kind}", f"{last[1]:g} / {first[1]:g}")
        add(f"{pct((last[1] / first[1] - 1) * 100)} since {_d(first[0])}", f"({last[1]:g} / {first[1]:g} - 1) x 100")
        months = (last[0] - first[0]) / (30.44 * 86400)
        if months >= 1.5:
            add(f"about {_num(months)} months from {_d(first[0])} to {_d(last[0])}", f"({last[0]:g} - {first[0]:g}) s / 30.44 days")
        if hi != last:
            add(f"{pct((last[1] / hi[1] - 1) * 100)} from the {_d(hi[0])} high", f"({last[1]:g} / {hi[1]:g} - 1) x 100")
            days = round((last[0] - hi[0]) / 86400)
            if days >= 2:
                add(f"{_num(days)} days after the {_d(hi[0])} high", f"({last[0]:g} - {hi[0]:g}) s / 86400")
        elif len(pts) >= 5:
            add(f"the highest of {len(pts)} recorded {kind}s" if kind == "sale" else f"the highest close in {len(pts)} sessions", f"max of {len(pts)} points")
        if lo != first and lo != last and hi[1] / lo[1] >= 1.3:
            add(f"{money(hi[1] - lo[1], cur)} between the low and the high", f"{hi[1]:g} - {lo[1]:g}")
        if kind == "sale" and last[1] - first[1] >= 1000:
            add(f"{money(last[1] - first[1], cur)} more than the {_d(first[0])} sale", f"{last[1]:g} - {first[1]:g}")

    vals = values(brief)
    money_vals = [v for v in vals if v["unit"] == "money"]
    counts = [v for v in vals if v["unit"] == "count"]
    # same unit, same period: shares and changes (never across two different years or periods)
    for i, a in enumerate(money_vals):
        for b in money_vals[i + 1:]:
            if a["currency"] != b["currency"] or a["year"] != b["year"] or not a["value"] or not b["value"]:
                continue
            if "peak" in a["key"] or "peak" in b["key"] or "outlook" in a["key"] or "outlook" in b["key"]:
                continue  # a peak or a forecast isn't a part of the other number
            big, small = (a, b) if a["value"] >= b["value"] else (b, a)
            r = small["value"] / big["value"]
            if 0.05 <= r <= 0.98:
                add(f"{label(small)} make up {_num(r * 100)}% of {label(big)} value", f"{small['value']:g} / {big['value']:g}")
    # outlook vs result (both stated by the company): the guided change
    res = next((v for v in money_vals if "revenue" in v["base"] and "outlook" not in v["base"]), None)
    guide = next((v for v in money_vals if "outlook" in v["base"]), None)
    if res and guide and res["value"]:
        add(f"guidance is {pct((guide['value'] / res['value'] - 1) * 100)} on the last quarter", f"({guide['value']:g} / {res['value']:g} - 1) x 100")
    # a value over its own count (the same thing, the same period): an average
    for c in counts:
        for m in money_vals:
            if noun(m) and noun(m) == noun(c) and m["year"] == c["year"] and c["value"]:
                add(f"about {money(m['value'] / c['value'], m['currency'])} per {noun(c)} on average", f"{m['value']:g} / {c['value']:g}")
    # a unit price times units built (a product, labelled as at list price)
    price = next((v for v in money_vals if "price" in v["base"]), None)
    built = next((v for v in counts if "built" in v["base"] or "units" in v["base"]), None)
    if price and built:
        add(f"{money(price['value'] * built['value'], price['currency'])} of {int(built['value'])} cars at list price", f"{price['value']:g} x {built['value']:g}")
    # unit conversions the brief implies
    for v in vals:
        if v["key"].endswith("_kmh"):
            add(f"{_num(v['value'] / 1.609344)} mph", f"{v['value']:g} km/h / 1.609344")
    # a share inside a count ("129.6 thousand new of 193.1 thousand")
    nums = brief.get("numbers") or {}
    for f in brief.get("facts") or []:
        t = str(f.get("text") if isinstance(f, dict) else f)
        m = re.search(r"(\d[\d.,]*) thousand,? including (\d[\d.,]*) thousand new", t)
        if m:
            whole, part = float(m.group(1).replace(",", "")), float(m.group(2).replace(",", ""))
            if whole:
                add(f"{_num(part / whole * 100)}% of them new", f"{part:g} / {whole:g}")
    # dates in the facts: the span, and an item's age at the time
    ds = dates(brief)
    year = nums.get("year") or (brief.get("card") or {}).get("year")
    if len(ds) >= 2 and not any(" months from " in t for t, _ in lines):  # the chart already gave the span
        months = (ds[-1] - ds[0]).days / 30.44
        span = f"about {_num(months / 12)} years" if months >= 24 else f"about {_num(months)} months"
        aged = isinstance(year, (int, float)) and ds[0].year == int(year)  # the release date: the age line below says it
        if months >= 1.5 and not aged:
            add(f"{span} between {ds[0]:%B} {ds[0].day}, {ds[0].year} and {ds[-1]:%B} {ds[-1].day}, {ds[-1].year}", f"{ds[-1]} - {ds[0]}")
    if isinstance(year, (int, float)) and ds and 1900 < year < ds[-1].year:
        add(f"{ds[-1].year - int(year)} years after it came out", f"{ds[-1].year} - {int(year)}")
    # external anchors only when the research supplies them (with a source)
    for an in brief.get("anchors") or []:
        target = next((v for v in money_vals if v["key"] == an.get("compare")), None) or (money_vals[0] if money_vals else None)
        if target and isinstance(an.get("value"), (int, float)) and an["value"] and an.get("label"):
            add(f"{_num(target['value'] / an['value'])}x {an['label']}", f"{target['value']:g} / {an['value']:g} ({an.get('source', 'source')})")
    lines = lines[:MAX_DERIVED]
    return {"derived": [t for t, _ in lines], "derived_from": [h for _, h in lines]}


def attach(brief: dict[str, Any]) -> dict[str, Any]:
    """The brief with its fact card (the `derived` field the writer quotes)."""
    return {**brief, **derive(brief)}
