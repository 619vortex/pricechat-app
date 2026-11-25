"""Declarative, JSON-able chart specs for Price Chat "Original Intelligence" graphics.

A `ChartSpec` is everything the renderer needs and nothing it has to guess: the finding as words (kicker, hero figure, headline,
dek), the data (series or rows), the marks that carry the point (annotations), and the provenance (source line, as-of). The G1
approval queue stores a spec next to its Finding, so the picture can be re-rendered, audited and compared with the post text.

Stable API (used by app.growth.render and the Originals queue):

    from app.growth.chart_spec import ChartSpec, Series, Row, Annotation, KINDS, FORMATS, SpecError, validate
    spec = ChartSpec(kind="rebased_lines", kicker="...", hero="-49.5%", headline="...", series=[Series(...), ...], source="...")
    validate(spec)                       # raises SpecError on anything that cannot be drawn honestly
    d = spec.to_dict(); ChartSpec.from_dict(d) == spec   # JSON round trip (json.dumps(d) works)

Kinds (`KINDS`) and the data each one reads:
- rank_table          rows (label, value, sublabel, display, highlight); diverging bars through zero, up to 15 rows.
                      options: scale ("linear" | "log"), ref_value + ref_label (a vertical reference line, e.g. rank 750).
- rebased_lines       2+ date series, each rebased to 0% at its first point; direct end labels; options.shade_between=[i, j].
- spread_lines        one date series of a spread; area above/below zero, record marker via annotations.
- rolling_line        one date series of a rolling statistic (N-day return, rolling correlation); reference lines via annotations.
- ratio_line          one date series of a ratio (BTC in ounces of gold); start and end values are labelled.
- series_with_marker  one long date series (an index level) with a threshold band and the current reading marked.
  (gauge_history is an alias of series_with_marker.)
- drawdown_overlay    several series of percent-below-peak, x = days since the top (numbers) or dates; troughs labelled.
- histogram_marker    series[0].values = the raw observations; options.bin_width; a "you are here" marker annotation (x = value).
- seasonality_bars    series[0]: x = category labels (years), values = returns; options.partial = labels still in progress.
  (seasonality_heatmap is an alias.)
- heat_grid           rows: one tile per asset, coloured by value; options.columns.
- move_card           the hero move, series[0] = recent closes (date series) for a sparkline, series[1] (optional) =
                      the dates/values of every comparable event (a dot strip), annotations mark today.

Every value that is plotted must be finite; every annotation must point at data that exists (`validate`). The renderer calls
`validate` first and refuses (SpecError) rather than draw a misleading picture.

Formats (`FORMATS`): "x" 1600x900 (X, 16:9) and "portrait" 1080x1350 (4:5). Themes: "dark" (default) and "light".
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any

SPEC_VERSION = "pc-intel-1"
FORMATS: dict[str, tuple[int, int]] = {"x": (1600, 900), "portrait": (1080, 1350)}
THEMES = ("dark", "light")
KINDS = (
    "rank_table",
    "rebased_lines",
    "spread_lines",
    "rolling_line",
    "ratio_line",
    "series_with_marker",
    "drawdown_overlay",
    "histogram_marker",
    "seasonality_bars",
    "heat_grid",
    "move_card",
)
ALIASES = {"gauge_history": "series_with_marker", "seasonality_heatmap": "seasonality_bars"}
ROLES = ("highlight", "context", "pos", "neg", "accent", "blue", "auto")
ANNOTATION_KINDS = ("marker", "point_label", "hline", "vline", "band", "xband")
Y_FORMATS = ("pct", "pts", "num", "price", "ratio", "corr", "oz", "rank", "index")

# kinds whose series are date series (x = ISO dates, ascending)
DATE_KINDS = ("rebased_lines", "spread_lines", "rolling_line", "ratio_line", "series_with_marker", "move_card")
ROW_KINDS = ("rank_table", "heat_grid")


class SpecError(ValueError):
    """The spec cannot be drawn honestly (bad numbers, missing data, annotation outside the data)."""


@dataclass
class Series:
    """One line or bar set. `x` holds ISO dates ("2026-11-25"), numbers, or category labels; `values` the plotted numbers."""

    name: str
    values: list[float]
    x: list[Any] = field(default_factory=list)
    role: str = "context"
    label: str | None = None  # direct label text at the line's end (default: name)
    width: float | None = None  # line width override, px at 1600 wide


@dataclass
class Row:
    """One row of a rank table or one tile of a heat grid."""

    label: str
    value: float
    sublabel: str | None = None
    display: str | None = None  # the figure as printed (default: formatted value)
    highlight: bool = False
    role: str = "auto"  # auto: pos/neg by sign


@dataclass
class Annotation:
    """A mark that carries the point.

    marker       a dot + label on series[`series`] at x (must be one of that series' x values); `y` if given must equal the
                 plotted value (self-check). For histogram_marker, x is the observation value ("you are here").
    point_label  like marker, quieter (context points such as previous troughs).
    hline        a horizontal reference line at y.          vline  a vertical line at x (inside the x range).
    band         a horizontal band between y and y2.        xband  a vertical band between x and x2 (inside the x range).
    """

    kind: str
    text: str = ""
    x: Any = None
    y: float | None = None
    x2: Any = None
    y2: float | None = None
    series: int = 0
    role: str = "highlight"
    align: str = "auto"  # auto | left | right | above | below


@dataclass
class ChartSpec:
    kind: str
    headline: str  # the finding as a sentence (no hero figure inside)
    hero: str = ""  # the number that is the finding, as printed ("26.9 pts", "−27.2%")
    hero_role: str = "highlight"
    kicker: str = ""  # category, e.g. "SECTOR ROTATION"
    dek: str = ""  # one muted line under the headline: what exactly is measured
    series: list[Series] = field(default_factory=list)
    rows: list[Row] = field(default_factory=list)
    annotations: list[Annotation] = field(default_factory=list)
    y_format: str = "pct"
    y_label: str = ""
    source: str = ""  # "Source: Yahoo Finance daily closes · as of 25 Nov 2026 19:40 UTC"
    theme: str = "dark"
    options: dict[str, Any] = field(default_factory=dict)
    version: str = SPEC_VERSION

    # ------------------------------------------------------------------ JSON
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ChartSpec:
        d = dict(d)
        d["series"] = [Series(**s) for s in d.get("series") or []]
        d["rows"] = [Row(**r) for r in d.get("rows") or []]
        d["annotations"] = [Annotation(**a) for a in d.get("annotations") or []]
        return cls(**d)

    @property
    def base_kind(self) -> str:
        return ALIASES.get(self.kind, self.kind)


# ---------------------------------------------------------------------------------------------------------------- validate
def _finite(v: Any, what: str) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise SpecError(f"{what}: not a number ({v!r})") from None
    if math.isnan(f) or math.isinf(f):
        raise SpecError(f"{what}: not finite ({v!r})")
    return f


def parse_date(v: Any) -> date:
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        raise SpecError(f"not an ISO date: {v!r}") from None


def _x_key(spec: ChartSpec, v: Any) -> Any:
    """Comparable form of an x value for this spec's series."""
    if spec.base_kind in DATE_KINDS or (spec.base_kind == "drawdown_overlay" and spec.options.get("x_dates")):
        return parse_date(v)
    if spec.base_kind == "seasonality_bars":
        return str(v)
    return _finite(v, "x")


def validate(spec: ChartSpec) -> ChartSpec:
    """Raise SpecError unless every plotted value is finite and every annotation refers to data that exists."""
    kind = spec.base_kind
    if kind not in KINDS:
        raise SpecError(f"unknown chart kind {spec.kind!r}")
    if spec.theme not in THEMES:
        raise SpecError(f"unknown theme {spec.theme!r}")
    if spec.y_format not in Y_FORMATS:
        raise SpecError(f"unknown y_format {spec.y_format!r}")
    if not spec.headline.strip():
        raise SpecError("a chart states its finding: headline is empty")
    if not spec.source.strip():
        raise SpecError("every chart names its source")
    for s in spec.series:
        if s.role not in ROLES:
            raise SpecError(f"series {s.name!r}: unknown role {s.role!r}")
    for r in spec.rows:
        if r.role not in ROLES:
            raise SpecError(f"row {r.label!r}: unknown role {r.role!r}")

    if kind in ROW_KINDS:
        if len(spec.rows) < 2:
            raise SpecError(f"{kind} needs at least 2 rows")
        if kind == "rank_table" and len(spec.rows) > 15:
            raise SpecError("rank_table holds at most 15 rows")
        if kind == "heat_grid" and len(spec.rows) > 120:
            raise SpecError("heat_grid holds at most 120 tiles")
        for r in spec.rows:
            _finite(r.value, f"row {r.label!r}")
        if kind == "rank_table" and spec.options.get("scale") == "log" and any(r.value <= 0 for r in spec.rows):
            raise SpecError("log scale needs positive values")
        if spec.options.get("ref_value") is not None:
            _finite(spec.options["ref_value"], "ref_value")
    else:
        if not spec.series:
            raise SpecError(f"{kind} needs at least one series")
        need = {"rebased_lines": 2}.get(kind, 1)
        if len(spec.series) < need:
            raise SpecError(f"{kind} needs at least {need} series")
        for s in spec.series:
            if not s.values:
                raise SpecError(f"series {s.name!r} is empty")
            for i, v in enumerate(s.values):
                _finite(v, f"series {s.name!r} value {i}")
            if kind != "histogram_marker":
                if len(s.x) != len(s.values):
                    raise SpecError(f"series {s.name!r}: {len(s.x)} x values for {len(s.values)} values")
                keys = [_x_key(spec, x) for x in s.x]
                if kind != "seasonality_bars" and any(b < a for a, b in zip(keys, keys[1:])):
                    raise SpecError(f"series {s.name!r}: x is not ascending")
        if kind == "rebased_lines" and any(s.values[0] == 0 for s in spec.series):
            raise SpecError("cannot rebase a series that starts at 0")
        if kind == "histogram_marker" and len(spec.series[0].values) < 20:
            raise SpecError("a histogram needs at least 20 observations")
        if kind == "histogram_marker" and _finite(spec.options.get("bin_width", 0) or 0, "bin_width") <= 0:
            raise SpecError("histogram_marker needs options.bin_width > 0")

    for a in spec.annotations:
        _check_annotation(spec, a)
    return spec


def _check_annotation(spec: ChartSpec, a: Annotation) -> None:
    kind = spec.base_kind
    if a.kind not in ANNOTATION_KINDS:
        raise SpecError(f"unknown annotation kind {a.kind!r}")
    if a.role not in ROLES:
        raise SpecError(f"annotation {a.text!r}: unknown role {a.role!r}")
    if kind in ROW_KINDS:
        raise SpecError(f"{kind} takes no annotations (use row.highlight / options.ref_value)")
    if a.kind in ("hline", "band"):
        _finite(a.y, f"annotation {a.text!r} y")
        if a.kind == "band":
            _finite(a.y2, f"annotation {a.text!r} y2")
        return
    if kind == "histogram_marker":
        if a.kind not in ("marker", "vline", "point_label"):
            raise SpecError("histogram_marker takes marker / vline annotations")
        xv = _finite(a.x, f"annotation {a.text!r} x")
        vals = spec.series[0].values
        if not (min(vals) <= xv <= max(vals)):
            raise SpecError(f"annotation {a.text!r}: x={xv} is outside the observed range")
        return
    if a.series < 0 or a.series >= len(spec.series):
        raise SpecError(f"annotation {a.text!r}: no series {a.series}")
    s = spec.series[a.series]
    keys = [_x_key(spec, x) for x in s.x]
    if a.kind in ("marker", "point_label"):
        k = _x_key(spec, a.x)
        if k not in keys:
            raise SpecError(f"annotation {a.text!r}: x={a.x!r} is not a point of series {s.name!r}")
        if a.y is not None:
            plotted = s.values[keys.index(k)]
            if abs(_finite(a.y, "y") - plotted) > 1e-6 * max(1.0, abs(plotted)):
                raise SpecError(f"annotation {a.text!r}: y={a.y} disagrees with the plotted value {plotted}")
        return
    # vline / xband: inside the plotted x range of any series
    allk = [_x_key(spec, x) for ser in spec.series for x in ser.x]
    lo, hi = min(allk), max(allk)
    for xv in (a.x, a.x2) if a.kind == "xband" else (a.x,):
        k = _x_key(spec, xv)
        if not (lo <= k <= hi):
            raise SpecError(f"annotation {a.text!r}: x={xv!r} is outside the data ({lo} to {hi})")
