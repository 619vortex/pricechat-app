"""The ONE adapter between a Finding's chart dict and the shared renderer (app.growth.chart_spec + app.growth.render, built on another
branch). Both are imported lazily: until that branch is merged, graphic_png raises RendererMissing and the API answers 503.

TODO(integration): replace to_spec()'s pass-through with the real conversion to chart_spec's schema once app/growth/chart_spec.py lands
(field names, palette, the attribution line, and the chart self-check that the plotted values equal the Finding's numbers).
"""

from __future__ import annotations

from typing import Any

FORMATS = {"x": (1600, 900), "portrait": (1080, 1350)}


class RendererMissing(Exception):
    """app.growth.render isn't installed (the renderer branch isn't merged yet)."""


def to_spec(chart: dict[str, Any], fmt: str) -> Any:
    """chart dict -> the renderer's spec. TODO(integration): map to app.growth.chart_spec; today the dict is passed through unchanged with the
    target format added, which matches the chart dict documented in app/growth/findings.py."""
    try:
        from app.growth import chart_spec  # type: ignore[attr-defined]  # noqa: F401
    except ImportError as e:
        raise RendererMissing("chart_spec not installed") from e
    w, h = FORMATS[fmt]
    spec = dict(chart)
    spec.update({"format": fmt, "width": w, "height": h})
    build = getattr(chart_spec, "from_dict", None) or getattr(chart_spec, "parse", None)
    return build(spec) if callable(build) else spec


def graphic_png(finding_or_chart: Any, fmt: str = "x") -> bytes:
    """PNG bytes for a Finding (or a draft's chart dict) in format 'x' (1600x900) or 'portrait' (1080x1350)."""
    if fmt not in FORMATS:
        raise ValueError(f"unknown format {fmt!r}")
    chart = finding_or_chart if isinstance(finding_or_chart, dict) else getattr(finding_or_chart, "chart", None)
    if not isinstance(chart, dict) or not chart.get("kind"):
        raise ValueError("no chart")
    try:
        from app.growth import render  # type: ignore[attr-defined]
    except ImportError as e:
        raise RendererMissing("renderer not installed") from e
    return render.render(to_spec(chart, fmt), fmt)
