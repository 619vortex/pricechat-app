"""Read-side of Market Memory for the detectors: plain in-memory series (pure functions over these, no database inside a detector)."""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any


@dataclass
class Series:
    key: str
    dates: list[date]
    values: list[float]
    source: str = ""

    @classmethod
    def of(cls, key: str, points: Iterable[tuple[date, float]], source: str = "") -> "Series":
        pts = sorted(points)
        return cls(key, [d for d, _ in pts], [float(v) for _, v in pts], source)

    def __len__(self) -> int:
        return len(self.dates)

    @property
    def first(self) -> date:
        return self.dates[0]

    @property
    def last(self) -> date:
        return self.dates[-1]

    def years(self, as_of: date | None = None) -> float:
        """Stored history length in years (to as_of, default the last point)."""
        return ((as_of or self.last) - self.first).days / 365.25 if self.dates else 0.0

    def index_on_or_before(self, d: date, tolerance_days: int = 0) -> int | None:
        i = bisect_right(self.dates, d) - 1
        if i < 0 or (d - self.dates[i]).days > tolerance_days:
            return None
        return i

    def value(self, d: date, tolerance_days: int = 0) -> float | None:
        i = self.index_on_or_before(d, tolerance_days)
        return None if i is None else self.values[i]

    def upto(self, d: date) -> "Series":
        i = bisect_right(self.dates, d)
        return Series(self.key, self.dates[:i], self.values[:i], self.source)

    def window_returns(self, n: int) -> list[tuple[date, float, float, float]]:
        """Every n-calendar-day return in the series: (end day, return %, start value, end value), only where the start day is stored
        exactly n days before the end day (no gaps papered over)."""
        pos = {d: i for i, d in enumerate(self.dates)}
        out = []
        for i, d in enumerate(self.dates):
            j = pos.get(d - timedelta(days=n))
            if j is not None and self.values[j] > 0:
                out.append((d, (self.values[i] / self.values[j] - 1) * 100, self.values[j], self.values[i]))
        return out


@dataclass
class Memory:
    """series key -> Series. Built from the database (from_db) or from plain dicts (tests)."""

    series: dict[str, Series] = field(default_factory=dict)
    _db: Any = None
    _loaded: set[str] = field(default_factory=set)

    @classmethod
    def from_points(cls, data: dict[str, list[tuple[date, float]]], sources: dict[str, str] | None = None) -> "Memory":
        m = cls()
        for k, pts in data.items():
            m.series[k] = Series.of(k, pts, (sources or {}).get(k, _default_source(k)))
        m._loaded.add("")
        return m

    @classmethod
    def from_db(cls, db: Any) -> "Memory":
        return cls(_db=db)

    def _load(self, prefix: str) -> None:
        if self._db is None or "" in self._loaded or prefix in self._loaded:
            return
        from sqlalchemy import select

        from app.models.market_memory import MmSeries

        rows: dict[str, list[tuple[date, float]]] = {}
        src: dict[str, str] = {}
        for s, d, v, so in self._db.execute(select(MmSeries.series, MmSeries.day, MmSeries.value, MmSeries.source)
                                            .where(MmSeries.series.like(prefix + "%")).order_by(MmSeries.series, MmSeries.day)):
            rows.setdefault(s, []).append((d, float(v)))
            src[s] = so
        for k, pts in rows.items():
            self.series[k] = Series.of(k, pts, src[k])
        self._loaded.add(prefix)

    def get(self, key: str) -> Series | None:
        if key not in self.series:
            fam = key.split(":")[0] + ":" if ":" in key else key
            self._load(fam)
        s = self.series.get(key)
        return s if s is not None and len(s) else None

    def keys(self, prefix: str) -> list[str]:
        self._load(prefix.split(":")[0] + ":" if ":" in prefix else prefix)
        return sorted(k for k in self.series if k.startswith(prefix))

    def latest_day(self, key: str = "px:BTC:close") -> date | None:
        s = self.get(key)
        return s.last if s else None


def _default_source(key: str) -> str:
    if key.startswith("cb:"):
        return "coinbase"
    if key == "fng":
        return "alternative_me"
    if key.startswith(("breadth:", "oi:")):
        return "derived_hl"
    if key.startswith("liq:"):
        return "liquidations"
    return "hyperliquid"
