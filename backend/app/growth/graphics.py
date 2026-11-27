"""The ONE adapter between a Finding's chart dict (app.growth.findings, filled by the G1 detectors) and the shared renderer
(app.growth.chart_spec + app.growth.render).

What it does, and what it never does:
- Words come from the Finding: the hero, headline, dek and every label are templates whose {placeholders} are filled with the Finding's own
  formatted figures (`numbers[k]["text"]`) and words. The adapter never formats or recomputes a printed figure.
- The plotted values are the detector's values, unchanged. An annotation that names a Finding number ("number": key) is checked against the
  value plotted at its x (up to the rounding the detector stored the points with): a disagreement is a SpecError, never a silent fix.
- The source line is the Finding's attribution ("Data: Hyperliquid", "Fear & Greed Index: alternative.me", ...) and its as-of day.
- "Other chart" (Regenerate): a chart dict may carry `variants` ({kind: overrides}) from the detector; when the draft's kind is one of them
  the overrides are applied over the base dict. Kinds that read the same data (rank_table <-> heat_grid, the line family) need no variant.

Chart dict (built by app.growth.findings.chart):
    kind, title, hero (a number key, or a template), hero_role (neg | pos | highlight | auto), headline (template), dek (template),
    series [{name, points [[x, v], ...], role, label, width}], rows [{label, value, display, sublabel, highlight, role}] (row kinds;
    default: the series' points), annotations [{type, x, y, x2, y2, label, role, series, align, number}], y_format, options,
    source, as_of, variants, variant_of
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from hashlib import sha256
from typing import Any

from app.growth import fmt as gfmt
from app.growth import sources as src

FORMATS = {"x": (1600, 900), "portrait": (1080, 1350)}
ROW_KINDS = ("rank_table", "heat_grid")
PLACEHOLDER = re.compile(r"\{([a-z][a-z0-9_]*)\}")
DETECTOR_LABEL = {"rarity": "Rarity", "record": "Record close", "sentiment": "Sentiment rarity", "drawdown_cycle": "Cycle analog",
                  "seasonality": "Seasonality", "then_now": "Then and now", "breadth_count": "Market breadth", "breadth_ma": "Trend breadth",
                  "funding_extremes": "Crowded trade · funding", "funding_streak": "Crowded trade · funding streak",
                  "oi_buildup": "Crowded trade · leverage", "leaderboard": "Leaderboard", "ratio_record": "Relative value",
                  "divergence": "Divergence", "beta": "Beta to Bitcoin", "streak": "Streak", "volatility_regime": "Volatility regime",
                  "volume_spike": "Volume anomaly", "fear_aftermath": "After extreme fear", "year_paths": "Year to date, every year",
                  "gold_ratio": "Priced in gold", "weekly_streak": "Weekly streak", "drawdown_table": "Distance from the highs"}
ANN_KIND = {"marker": "marker", "point_label": "point_label", "hline": "hline", "vline": "vline", "band": "band", "xband": "xband"}


class RendererMissing(Exception):
    """app.growth.render isn't installed."""


def _finding_dict(finding: Any) -> dict[str, Any]:
    if finding is None:
        return {}
    if isinstance(finding, dict):
        return finding
    d = getattr(finding, "to_dict", None)
    return d() if callable(d) else {}


class _Filler:
    """Fills {name} placeholders with the Finding's formatted figures and words. An unknown name is an error: no figure is invented."""

    def __init__(self, fd: dict[str, Any]):
        self.numbers: dict[str, Any] = fd.get("numbers") or {}
        self.values = {k: str(v.get("text", "")) for k, v in self.numbers.items()} | dict(fd.get("words") or {})

    def __call__(self, text: Any) -> str:
        from app.growth.chart_spec import SpecError

        def sub(m: re.Match[str]) -> str:
            if m.group(1) not in self.values:
                raise SpecError(f"chart text placeholder {{{m.group(1)}}} is not a figure or word of the finding")
            return self.values[m.group(1)]

        return PLACEHOLDER.sub(sub, str(text)) if text else ""


def _merge(chart: dict[str, Any]) -> dict[str, Any]:
    """The chart dict to draw: the base dict with the detector's overrides for the current (variant) kind applied."""
    kind = chart.get("kind")
    base = chart.get("variant_of") or kind
    out = {k: v for k, v in chart.items() if k != "variants"}
    if kind != base:
        out.update((chart.get("variants") or {}).get(kind) or {})
        out["kind"] = kind
    return out


def variant_kinds(chart: dict[str, Any]) -> list[str]:
    """Every kind this chart can be drawn as: the detector's kind first, then its variants (or the generic same-data alternative)."""
    from app.growth.drafts import ALT_CHART

    base = chart.get("variant_of") or chart.get("kind")
    kinds = [base] + [k for k in (chart.get("variants") or {}) if k != base]
    if len(kinds) == 1 and ALT_CHART.get(base):
        kinds.append(ALT_CHART[base])
    return kinds


def _decimals(values: list[float]) -> int:
    best = 0
    for v in values:
        s = repr(float(v))
        if "e" in s:
            return 6
        if "." in s:
            best = max(best, len(s.split(".")[1].rstrip("0")))
    return min(best, 6)


def _rows(m: dict[str, Any], kind: str) -> list[Any]:
    from app.growth.chart_spec import Row

    raw = m.get("rows")
    if not raw:
        raw = [{"label": str(x), "value": v, "highlight": bool(s.get("highlight"))} for s in m.get("series") or [] for x, v in s.get("points") or []]
    rows = [Row(label=str(r["label"]), value=float(r["value"]), sublabel=r.get("sublabel"), display=r.get("display"),
                highlight=bool(r.get("highlight", False)), role=r.get("role", "auto")) for r in raw]
    if kind == "rank_table" and len(rows) > 15:  # longer than a table holds: the 7 highest and the 7 lowest
        rows = sorted(rows, key=lambda r: -r.value)
        rows = rows[:7] + rows[-7:]
    return rows[:120]


def _series(m: dict[str, Any]) -> list[Any]:
    from app.growth.chart_spec import Series

    out = []
    for s in m.get("series") or []:
        pts = s.get("points") or []
        out.append(Series(name=str(s.get("name", "")), values=[float(v) for _, v in pts], x=[x for x, _ in pts],
                          role=s.get("role") or ("highlight" if s.get("highlight") else "context"), label=s.get("label"), width=s.get("width")))
    return out


def _plotted(kind: str, s: Any, x: Any) -> tuple[float, float] | None:
    """(the value drawn at x, its rounding tolerance) or None when x isn't a point of the series."""
    dp = _decimals(s.values)
    half = 0.5 * 10 ** -dp
    for xx, v in zip(s.x, s.values):
        same = (str(xx)[:10] == str(x)[:10]) if isinstance(xx, str) else (isinstance(x, (int, float)) and float(xx) == float(x))
        if same:
            if kind == "rebased_lines":
                v0 = float(s.values[0])
                return (float(v) / v0 - 1) * 100, 100 * half * (1 / abs(v0) + abs(float(v)) / v0 ** 2)
            return float(v), half
    return None


def _check_number(kind: str, series: list[Any], a: dict[str, Any], numbers: dict[str, Any]) -> None:
    """An annotation tied to a Finding number must sit on a plotted value equal to it (up to the rounding of the stored points)."""
    from app.growth.chart_spec import SpecError

    key = a.get("number")
    if not key:
        return
    if key not in numbers:
        raise SpecError(f"annotation refers to number {key!r}, which the finding doesn't have")
    want = float(numbers[key]["value"])
    if kind == "histogram_marker":
        got, tol = float(a["x"]), 0.5 * 10 ** -_decimals([float(a["x"])])
    else:
        i = int(a.get("series", 0))
        hit = _plotted(kind, series[i], a.get("x")) if 0 <= i < len(series) else None
        if hit is None:
            raise SpecError(f"annotation for {key!r}: x={a.get('x')!r} is not a plotted point")
        got, tol = hit
    if abs(got - want) > tol + 1e-9 * max(1.0, abs(want)):
        raise SpecError(f"the chart plots {got} where the finding's {key!r} is {want}")


def _source(fd: dict[str, Any], m: dict[str, Any]) -> str:
    names = fd.get("sources") or []
    attr = list(fd.get("attribution") or src.attribution(names))
    if not attr:
        attr = [str(m.get("source") or "")]
    as_of = fd.get("as_of") or m.get("as_of")
    when = [f"as of {gfmt.day(date.fromisoformat(str(as_of)[:10]))} (daily, UTC)"] if as_of else []
    return " · ".join([a for a in attr if a] + when)


def _hero(m: dict[str, Any], fill: _Filler) -> tuple[str, str]:
    h = str(m.get("hero") or "")
    val = None
    if h in fill.numbers:
        text, val = str(fill.numbers[h]["text"]), fill.numbers[h].get("value")
    else:
        text = fill(h)
    role = m.get("hero_role") or "auto"
    if role == "auto":
        role = "neg" if val is not None and float(val) < 0 else "pos" if val is not None and float(val) > 0 else "highlight"
    return text, role


def to_spec(chart: dict[str, Any], fmt: str, finding: Any = None) -> Any:
    """Chart dict + its Finding -> a validated app.growth.chart_spec.ChartSpec. Raises chart_spec.SpecError when it can't be drawn honestly."""
    try:
        from app.growth.chart_spec import Annotation, ChartSpec, validate
    except ImportError as e:
        raise RendererMissing("chart_spec not installed") from e
    if fmt not in FORMATS:
        raise ValueError(f"unknown format {fmt!r}")
    fd = _finding_dict(finding)
    fill = _Filler(fd)
    m = _merge(chart)
    kind = m["kind"]
    hero, hero_role = _hero(m, fill)
    det = fd.get("detector") or ""
    kicker = DETECTOR_LABEL.get(det) or str(m.get("kicker") or "")
    headline = fill(m.get("headline")) or str(fd.get("headline") or m.get("title") or "")
    options = {k: (v.get(fmt) if isinstance(v, dict) and set(v) <= set(FORMATS) else v) for k, v in (m.get("options") or {}).items()}
    if "hero_note" in options:
        options["hero_note"] = fill(options["hero_note"])
    elif "alternative_me" in (fd.get("sources") or []):
        options["hero_note"] = src.get("alternative_me").attribution  # type: ignore[union-attr]  # "next to the number" (licence)
    series: list[Any] = []
    rows: list[Any] = []
    anns: list[Any] = []
    if kind in ROW_KINDS:
        rows = _rows(m, kind)
        for r in rows:
            r.display = fill(r.display) if r.display else None
        if kind == "heat_grid" and len(rows) <= 8 and not options.get("columns"):
            options["columns"] = len(rows) if fmt == "x" else min(len(rows), 4)
    else:
        series = _series(m)
        for a in m.get("annotations") or []:
            _check_number(kind, series, a, fill.numbers)  # also "check" entries: a number the chart shows by itself (an end label)
            t = ANN_KIND.get(a.get("type") or "")
            if t is None:
                continue
            y = a.get("y") if t in ("hline", "band") or kind == "histogram_marker" else None  # markers sit on the plotted value
            anns.append(Annotation(kind=t, text=fill(a.get("label") or ""), x=a.get("x"), y=None if y is None else float(y), x2=a.get("x2"),
                                   y2=None if a.get("y2") is None else float(a["y2"]), series=int(a.get("series", 0)),
                                   role=a.get("role", "highlight"), align=a.get("align", "auto")))
    spec = ChartSpec(kind=kind, headline=headline, hero=hero, hero_role=hero_role, kicker=kicker, dek=fill(m.get("dek")), series=series,
                     rows=rows, annotations=anns, y_format=m.get("y_format") or "pct", source=_source(fd, m), options=options)
    return validate(spec)


def render_graphic(chart: dict[str, Any], fmt: str = "x", finding: Any = None) -> tuple[bytes, dict[str, Any]]:
    """(PNG bytes, render meta). Meta = app.growth.render.render_info's (template, version, spec_version, format, theme, size, phone text
    sizes, label_overlaps) + the chart kind, the PNG's sha256 and when it was drawn: stored on the draft for the growth learner."""
    if fmt not in FORMATS:
        raise ValueError(f"unknown format {fmt!r}")
    if not isinstance(chart, dict) or not chart.get("kind"):
        raise ValueError("no chart")
    try:
        from app.growth import render
    except ImportError as e:
        raise RendererMissing("renderer not installed") from e
    png, meta = render.render_info(to_spec(chart, fmt, finding), fmt)
    meta = dict(meta) | {"kind": chart.get("kind"), "variant_of": chart.get("variant_of"), "sha256": sha256(png).hexdigest(),
                         "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    return png, meta


def graphic_png(finding_or_chart: Any, fmt: str = "x", finding: Any = None) -> bytes:
    """PNG bytes for a Finding, or for a draft's chart dict with its finding, in 'x' (1600x900) or 'portrait' (1080x1350)."""
    if isinstance(finding_or_chart, dict):
        chart = finding_or_chart
    else:
        chart, finding = getattr(finding_or_chart, "chart", None), finding if finding is not None else finding_or_chart
    return render_graphic(chart, fmt, finding)[0]  # type: ignore[arg-type]
