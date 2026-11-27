"""Original Intelligence renderer: every chart kind renders in both formats, and bad specs are refused, not drawn."""

from __future__ import annotations

import io
import json
import math
from datetime import date, timedelta

import pytest
from PIL import Image

from app.growth.chart_spec import ALIASES, FORMATS, KINDS, Annotation, ChartSpec, Row, Series, SpecError, validate
from app.growth.render import RENDERER_VERSION, render, render_info

SRC = "Source: test data · as of 25 Nov 2026 15:40 UTC"


def _dates(n: int, end: date = date(2026, 11, 25)) -> list[str]:
    return [(end - timedelta(days=n - 1 - i)).isoformat() for i in range(n)]


def _wave(n: int, base: float = 100.0, amp: float = 10.0) -> list[float]:
    return [base + amp * math.sin(i / 7) + i * 0.05 for i in range(n)]


def make(kind: str) -> ChartSpec:
    """A small valid spec for each kind."""
    d = _dates(120)
    common = dict(kind=kind, kicker="Test", hero="−12.3%", hero_role="neg", headline="A finding stated as a sentence", dek="What is measured",
                  source=SRC)
    base = ALIASES.get(kind, kind)
    if base == "rank_table":
        return ChartSpec(**common, rows=[Row(f"Asset {i}", v) for i, v in enumerate([12.0, 5.5, -3.0, -20.0, 1.0])])
    if base == "heat_grid":
        return ChartSpec(**common, rows=[Row(f"C{i}", (-1) ** i * i * 0.7, highlight=i % 5 == 0) for i in range(40)])
    if base == "rebased_lines":
        return ChartSpec(**common, series=[Series("A", _wave(120), d, role="context"), Series("B", _wave(120, 90, 20), d, role="neg")],
                         options={"shade_between": [0, 1]})
    if base in ("spread_lines", "rolling_line", "ratio_line", "series_with_marker"):
        v = _wave(120, 0 if base != "ratio_line" else 20, 10)
        return ChartSpec(**common, series=[Series("S", v, d, role="highlight", label="" if base != "ratio_line" else "S")],
                         annotations=[Annotation("marker", "Now\nlabel", x=d[-1]), Annotation("point_label", "Then", x=d[30], role="context"),
                                      Annotation("hline", "Ref", y=2.0, role="context")],
                         options={"end_labels": base == "ratio_line"})
    if base == "drawdown_overlay":
        return ChartSpec(**common, series=[Series("Old", [-i * 0.3 for i in range(200)], list(range(200)), role="context"),
                                           Series("Now", [-i * 0.4 for i in range(150)], list(range(150)), role="neg", label="")],
                         annotations=[Annotation("marker", "−59.6%\nToday", x=149, series=1, role="neg")])
    if base == "histogram_marker":
        obs = [math.sin(i) * 40 + (i % 13) for i in range(500)]
        return ChartSpec(**common, series=[Series("obs", obs)], annotations=[Annotation("marker", "You are here", x=obs[3], role="neg")],
                         options={"bin_width": 5})
    if base == "seasonality_bars":
        return ChartSpec(**common, series=[Series("Nov", [10.0, -20.0, 35.0, -5.0, -25.0], ["2022", "2023", "2024", "2025", "2026"])],
                         options={"partial": ["2026"]})
    if base == "move_card":
        ev = _dates(10)
        return ChartSpec(**common, series=[Series("events", [-4.0 - i * 0.3 for i in range(10)], ev)],
                         annotations=[Annotation("marker", "Today", x=ev[-1], role="neg")], options={"threshold": -4.0})
    raise AssertionError(kind)


ALL_KINDS = list(KINDS) + list(ALIASES)


@pytest.mark.parametrize("kind", ALL_KINDS)
@pytest.mark.parametrize("fmt", list(FORMATS))
def test_every_kind_renders_in_both_formats(kind: str, fmt: str) -> None:
    png, meta = render_info(make(kind), fmt)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    img = Image.open(io.BytesIO(png))
    assert img.size == FORMATS[fmt]
    assert 20_000 < len(png) < 3_000_000
    assert meta["version"] == RENDERER_VERSION and meta["template"].startswith("intel/")
    assert meta["phone_note_px"] >= 5.0  # footnotes still legible on a 390 px phone


def test_light_theme_and_default_dark_differ() -> None:
    spec = make("rank_table")
    dark = Image.open(io.BytesIO(render(spec))).convert("RGB")
    spec.theme = "light"
    light = Image.open(io.BytesIO(render(spec))).convert("RGB")
    assert sum(dark.getpixel((5, 5))) < 100 < sum(light.getpixel((5, 5)))


def test_spec_round_trips_through_json() -> None:
    for kind in KINDS:
        spec = make(kind)
        back = ChartSpec.from_dict(json.loads(json.dumps(spec.to_dict())))
        assert back == spec
        assert render(back) == render(spec)


def _refused(spec: ChartSpec, match: str) -> None:
    with pytest.raises(SpecError, match=match):
        render(spec)


def test_refuses_nan_and_infinite_values() -> None:
    s = make("rolling_line")
    s.series[0].values[10] = float("nan")
    _refused(s, "not finite")
    r = make("rank_table")
    r.rows[0].value = float("inf")
    _refused(r, "not finite")
    h = make("histogram_marker")
    h.series[0].values[0] = float("nan")
    _refused(h, "not finite")


def test_refuses_annotation_outside_the_data() -> None:
    s = make("rolling_line")
    s.annotations[0].x = "2030-01-01"
    _refused(s, "not a point of series")
    v = make("rolling_line")
    v.annotations.append(Annotation("vline", "x", x="2001-01-01"))
    _refused(v, "outside the data")
    h = make("histogram_marker")
    h.annotations[0].x = 999.0
    _refused(h, "outside the observed range")
    d = make("drawdown_overlay")
    d.annotations[0].x = 400
    _refused(d, "not a point of series")
    m = make("rolling_line")
    m.annotations[0].series = 5
    _refused(m, "no series")


def test_refuses_annotation_that_disagrees_with_the_plotted_value() -> None:
    s = make("rolling_line")
    s.annotations[0].y = s.series[0].values[-1] + 1.0
    _refused(s, "disagrees with the plotted value")
    s.annotations[0].y = s.series[0].values[-1]
    render(s)  # the matching value is fine


def test_refuses_malformed_specs() -> None:
    _refused(ChartSpec(kind="pie", headline="x", source=SRC), "unknown chart kind")
    bad = make("rank_table")
    bad.headline = " "
    _refused(bad, "headline is empty")
    bad = make("rank_table")
    bad.source = ""
    _refused(bad, "names its source")
    bad = make("rebased_lines")
    bad.series[1].x = bad.series[1].x[:-1]
    _refused(bad, "x values for")
    bad = make("rebased_lines")
    bad.series = bad.series[:1]
    _refused(bad, "at least 2 series")
    bad = make("rolling_line")
    bad.series[0].x = list(reversed(bad.series[0].x))
    _refused(bad, "not ascending")
    bad = make("rank_table")
    bad.rows = bad.rows * 4
    _refused(bad, "at most 15 rows")
    bad = make("histogram_marker")
    bad.options = {}
    _refused(bad, "bin_width")
    bad = make("rank_table")
    bad.theme = "neon"
    _refused(bad, "unknown theme")
    with pytest.raises(SpecError, match="unknown format"):
        render(make("rank_table"), "square")


def test_validate_returns_the_spec() -> None:
    spec = make("spread_lines")
    assert validate(spec) is spec
