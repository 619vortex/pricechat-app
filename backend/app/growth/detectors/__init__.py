"""G1 insight detectors: pure functions (Memory, as_of) -> list[Finding]. No database, no network, no model inside a detector.

Five formats (owner priority): extremes, history, breadth, crowded, relative. A detector that hasn't enough stored history returns [] (it
never claims a window longer than what is stored). run_all() never lets one failing detector stop the others.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date

from app.growth.detectors import breadth, crowded, extremes, history, relative
from app.growth.findings import Finding
from app.growth.memory import Memory

log = logging.getLogger(__name__)

Detector = Callable[[Memory, date], list[Finding]]

DETECTORS: dict[str, tuple[str, Detector]] = {
    "rarity": ("extremes", extremes.rarity_move),
    "record": ("extremes", extremes.record_level),
    "sentiment": ("extremes", extremes.sentiment_rarity),
    "drawdown_cycle": ("history", history.drawdown_cycle),
    "seasonality": ("history", history.seasonality),
    "then_now": ("history", history.then_now),
    "breadth_count": ("breadth", breadth.breadth_count),
    "breadth_ma": ("breadth", breadth.breadth_ma),
    "funding_extremes": ("crowded", crowded.funding_extremes),
    "funding_streak": ("crowded", crowded.funding_streak),
    "oi_buildup": ("crowded", crowded.oi_buildup),
    "leaderboard": ("relative", relative.leaderboard),
    "ratio_record": ("relative", relative.ratio_record),
    "divergence": ("relative", relative.divergence),
    "beta": ("relative", relative.beta),
}


def default_as_of(mem: Memory) -> date | None:
    """The newest complete day with a Bitcoin close (the detectors' as-of)."""
    return mem.latest_day("px:BTC:close")


def run_all(mem: Memory, as_of: date | None = None, only: list[str] | None = None) -> tuple[list[Finding], list[str]]:
    as_of = as_of or default_as_of(mem)
    if as_of is None:
        return [], ["no Bitcoin closes stored yet"]
    found: list[Finding] = []
    errors: list[str] = []
    for name, (_, fn) in DETECTORS.items():
        if only and name not in only:
            continue
        try:
            found += fn(mem, as_of)
        except Exception as e:  # noqa: BLE001 - one broken detector must not stop the others
            log.exception("growth detector %s failed", name)
            errors.append(f"{name}: {type(e).__name__}: {e}")
    return found, errors
