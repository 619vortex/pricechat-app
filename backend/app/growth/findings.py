"""The Finding record (doc §3): one computed, re-runnable fact with every figure that may appear in a post, its proof and its chart."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any

from app.growth import sources as src

FORMATS = ("extremes", "history", "breadth", "crowded", "relative")
FORMAT_LABEL = {"extremes": "Market extremes", "history": "Historical comparison", "breadth": "Market breadth", "crowded": "Crowded trades",
                "relative": "Relative performance"}
CHART_KINDS = ("rank_table", "rebased_lines", "spread_lines", "histogram_marker", "seasonality_bars", "drawdown_overlay", "rolling_line",
               "heat_grid", "ratio_line", "move_card", "series_with_marker")
PLACEHOLDER = re.compile(r"\{([a-z][a-z0-9_]*)\}")


@dataclass
class Finding:
    key: str  # stable identity of the fact (detector:subject:window:as_of)
    format: str
    detector: str
    subject: str  # the asset / universe it is about (variety and dedupe)
    headline: str  # the claim in one plain sentence (deterministic)
    numbers: dict[str, dict[str, Any]]  # name -> {value, text, label}: EVERY figure the text may show
    words: dict[str, str]  # non-numeric fill-ins (asset name, direction word): never a figure
    templates: list[str]  # deterministic post texts with {name} placeholders (numbers + words); [0] is the default
    window: str
    series: list[str]
    sources: list[str]  # registry names (app.growth.sources)
    as_of: date
    calculation: str  # human-readable formula with its inputs
    rarity: float  # 0..1: how unusual vs the series' own history
    chart: dict[str, Any]
    comparatives: list[str] = field(default_factory=list)  # superlatives / "first since" style words the text may use
    novelty: float = 1.0
    freshness: float = 1.0
    notes: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        assert self.format in FORMATS, self.format
        assert self.chart.get("kind") in CHART_KINDS, self.chart.get("kind")
        for t in self.templates:
            for name in PLACEHOLDER.findall(t):
                assert name in self.numbers or name in self.words, f"{self.detector}: template placeholder {name} has no value"
        for k, n in self.numbers.items():
            assert isinstance(n.get("text"), str) and n["text"], f"{self.detector}: number {k} has no text"

    @property
    def score(self) -> float:
        return round(100.0 * max(0.0, min(1.0, self.rarity)) * self.novelty * self.freshness, 2)

    @property
    def attribution(self) -> list[str]:
        return src.attribution(self.sources)

    @property
    def publishable(self) -> bool:
        return src.publishable(self.sources)

    def fill(self, template: str) -> str:
        values = {k: v["text"] for k, v in self.numbers.items()} | self.words
        return PLACEHOLDER.sub(lambda m: values[m.group(1)], template)

    def text(self, variant: int = 0) -> str:
        return self.fill(self.templates[variant % len(self.templates)])

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["as_of"] = self.as_of.isoformat()
        d["score"] = self.score
        d["attribution"] = self.attribution
        d["source_labels"] = src.labels(self.sources)
        d["publishable"] = self.publishable
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Finding":
        keep = {k: d[k] for k in cls.__dataclass_fields__ if k in d}
        keep["as_of"] = date.fromisoformat(str(d["as_of"]))
        return cls(**keep)


def chart(kind: str, title: str, kicker: str, series: list[dict[str, Any]], *, annotations: list[dict[str, Any]] | None = None,
          sources: list[str], as_of: date, **extra: Any) -> dict[str, Any]:
    """The chart dict (converted to the renderer's ChartSpec by app.growth.graphics.to_spec). `extra` carries what the graphic says, as
    templates over the Finding's numbers/words: hero (a number key or a template), hero_role, headline, dek, every annotation label; plus
    y_format, options, rows (rank_table / heat_grid) and variants ({other kind: overrides}, Regenerate -> "Other chart"). An annotation
    with "number": key is checked against the plotted value (the chart shows the Finding's own figures)."""
    return {"kind": kind, "title": title, "kicker": kicker, "series": series, "annotations": annotations or [],
            "source": " · ".join(src.attribution(sources)), "as_of": as_of.isoformat(), **extra}


def pts(pairs: list[tuple[Any, float]], dp: int = 4) -> list[list[Any]]:
    return [[(k.isoformat() if isinstance(k, date) else k), round(float(v), dp)] for k, v in pairs]


def n(value: float, text: str, label: str) -> dict[str, Any]:
    return {"value": float(value), "text": text, "label": label}
