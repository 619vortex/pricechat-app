"""The price (Y) axis of every chart the bots draw (community request, 2026-11-10; deliberately not configurable).

If the highest price on the chart is more than phi (1.618, the golden ratio) times the lowest, the axis is logarithmic, so equal moves in
percent look equal. Otherwise it stays linear. Decided from the prices actually shown, every time; there is no setting or switch.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

PHI = (1 + math.sqrt(5)) / 2


def needs_log(lo: float, hi: float) -> bool:
    """True when the range covers more than one factor of phi. Non-positive prices can't be on a log axis (they stay linear)."""
    return lo > 0 and hi > 0 and max(lo, hi) / min(lo, hi) > PHI


@dataclass(frozen=True)
class Axis:
    lo: float  # the bottom of the axis, padding included
    hi: float  # the top of the axis, padding included
    log: bool

    def frac(self, p: float) -> float:
        """Where a price sits on the axis: 0 at the bottom, 1 at the top."""
        if self.log:
            p = max(p, self.lo * 1e-9)
            return (math.log(p) - math.log(self.lo)) / ((math.log(self.hi) - math.log(self.lo)) or 1.0)
        return (p - self.lo) / ((self.hi - self.lo) or 1.0)

    def value(self, f: float) -> float:
        """The price at a fraction of the axis (the inverse of frac)."""
        if self.log:
            return math.exp(math.log(self.lo) + (math.log(self.hi) - math.log(self.lo)) * f)
        return self.lo + (self.hi - self.lo) * f

    def ticks(self, n: int = 6) -> list[float]:
        """n gridline prices from bottom to top, evenly spaced on the axis (geometric steps on a log axis)."""
        return [self.value(i / (n - 1)) for i in range(n)] if n > 1 else [self.lo]


def axis(lo: float, hi: float, pad: float = 0.0) -> Axis:
    """The axis for prices from lo to hi, with `pad` (a share of the range) added above and below. Log or linear by needs_log(lo, hi)."""
    lo, hi = min(lo, hi), max(lo, hi)
    if needs_log(lo, hi):
        a, b = math.log(lo), math.log(hi)
        s = (b - a) * pad
        return Axis(math.exp(a - s), math.exp(b + s), True)
    span = (hi - lo) or abs(hi) * 0.01 or 1.0
    return Axis(lo - span * pad, hi + span * pad, False)
