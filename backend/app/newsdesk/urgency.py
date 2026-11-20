"""How urgent a measured price move is, from its size against the asset's OWN recent behaviour (owner, 2026-11-20).

Production 2026-11-20: silver fell 6.4% in 24 hours (6.75% on the day), its biggest one-day drop in 189 days and 2.8 times a typical day
of the last 90. The desk scored it 56 ("1.3 times its 5% alert line") and a price move needed 70 to take a slot, so the 04:00 Dubai slot
was skipped; the next data post about it waited two more hours and went stale. The size against a fixed line said nothing about the
market context: for silver in a calm year 6% is a once-a-decade day, in a volatile one a few times a year.

So a move is judged three ways, all measured from daily closes we fetch ourselves (app/newsdesk/datapost: Yahoo Finance), never from a
keyword:

  z           today's move against the standard deviation of the asset's last 90 daily moves
  since_days  days since the asset last moved this much (or more) in a day, the same way
  ratio       the move against the desk's alert line for its window and asset class (app/newsdesk/moves.LINES)

  major        past its alert line AND (z >= 4, OR the biggest in 180+ days with z >= 2.5, OR at least twice the alert line).
               Backtested on 2 years of daily closes of 9 priority markets (2024-11-20 .. 2026-11-20): 69 days, ~3 a month across all of
               them, every one a day the market talked about (the April 2025 tariff crash, the January 2026 silver crash, 10 October 2025).
               A major move on a priority asset may break between slots, under its own caps (events.MARKET_BREAKING_*).
  significant  z >= 3, OR the biggest in 90+ days and past its line, OR 1.5 times the line: it takes the next slot without the
               routine move bar (125 days in the same backtest).
  routine      everything else: it still needs Policy.market_bar to take a slot, as before.

Without enough history (fewer than 120 daily closes, or a stale series) the move is "routine" with the reason said, so a missing feed can
never make a post more urgent.
"""

from __future__ import annotations

import statistics
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

MAJOR, SIGNIFICANT, ROUTINE = "major", "significant", "routine"
LEVELS = (ROUTINE, SIGNIFICANT, MAJOR)

Z_MAJOR = 4.0
Z_RARE = 2.5  # with since_days >= SINCE_MAJOR
SINCE_MAJOR = 180
RATIO_MAJOR = 2.0
Z_SIGNIFICANT = 3.0
SINCE_SIGNIFICANT = 90
RATIO_SIGNIFICANT = 1.5
HISTORY = 90  # daily moves the typical day is measured on
MIN_CLOSES = 120
SERIES_MAX_AGE = timedelta(days=4)  # the daily series' last close (weekends and holidays included)

# the moves detector's symbols for markets that aren't one instrument: their context is read from the market's lead instrument
LEAD = {"CRYPTO": "BTC-USD", "US-STOCKS": "^GSPC"}


@dataclass
class Urgency:
    level: str
    why: str
    z: float | None = None
    since_days: int | None = None
    since: str = ""  # "May 2026": the month of the last move this big (empty when none in the series)
    sd: float | None = None
    ratio: float = 0.0
    points: float = 0.0  # ranking points for the context (the score's "context" part); the release rules use the level

    def out(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def rank(self) -> int:
        return LEVELS.index(self.level)


def _utc(t: datetime) -> datetime:
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def context(series: list[tuple[datetime, float]], change_pct: float, now: datetime, last_is_live: bool | None = None) -> tuple[float, int | None, datetime | None, float] | None:
    """(z, days since a one-day move at least this big the same way or None if none in the series, its date, sd) for a move of change_pct
    now, against the daily closes BEFORE today's bar. None when the history can't say. last_is_live: the series' last bar is the session
    still trading (datapost.Last kind intraday / utc_day); None = unknown, then a bar under 24 hours old is taken as the live one."""
    if len(series) < MIN_CLOSES or now - _utc(series[-1][0]) > SERIES_MAX_AGE:
        return None
    closes = list(series)
    # the live bar (Yahoo puts the session's current price as the last daily close) is the move itself, not history
    if last_is_live or (last_is_live is None and now - _utc(closes[-1][0]) < timedelta(hours=24)):
        closes = closes[:-1]
    rets = [(closes[i][0], (closes[i][1] / closes[i - 1][1] - 1) * 100) for i in range(1, len(closes)) if closes[i - 1][1] > 0]
    if len(rets) < HISTORY:
        return None
    sd = statistics.pstdev([x for _, x in rets[-HISTORY:]])
    if sd <= 0:
        return None
    up = change_pct > 0
    last = next((t for t, x in reversed(rets) if (x >= change_pct if up else x <= change_pct)), None)
    days = (now - _utc(last)).days if last is not None else None
    return abs(change_pct) / sd, days, last, sd


def assess(change_pct: float, ratio: float, series: list[tuple[datetime, float]] | None, now: datetime, last_is_live: bool | None = None) -> Urgency:
    """The urgency of a move of change_pct (signed, percent) that is `ratio` times its alert line, given the asset's daily closes."""
    ctx = context(series or [], change_pct, now, last_is_live) if series else None
    way = "rise" if change_pct > 0 else "drop"
    if ctx is None:
        if ratio >= RATIO_MAJOR:
            return Urgency(MAJOR, f"{ratio:.2f}x its alert line (no daily history to compare)", ratio=round(ratio, 2), points=15.0)
        if ratio >= RATIO_SIGNIFICANT:
            return Urgency(SIGNIFICANT, f"{ratio:.2f}x its alert line (no daily history to compare)", ratio=round(ratio, 2), points=7.0)
        return Urgency(ROUTINE, "no daily history to compare it with", ratio=round(ratio, 2))
    z, days, last, sd = ctx
    since = last.strftime("%B %Y") if last is not None else ""
    never = days is None
    rare = never or (days is not None and days >= SINCE_MAJOR)
    told = (f"{abs(change_pct):.1f}% is {z:.1f} times a typical day of the last {HISTORY} ({sd:.1f}%), "
            + ("the biggest one-day " + way + " in the series" if never else f"the biggest one-day {way} since {since} ({days} days)"))
    level = ROUTINE
    if ratio >= 1 and (z >= Z_MAJOR or (rare and z >= Z_RARE) or ratio >= RATIO_MAJOR):
        level = MAJOR
    elif z >= Z_SIGNIFICANT or (ratio >= 1 and (never or (days or 0) >= SINCE_SIGNIFICANT)) or ratio >= RATIO_SIGNIFICANT:
        level = SIGNIFICANT
    points = round(min(20.0, max(0.0, (z - 2) * 8)) + (8.0 if never or (days or 0) >= 365 else 6.0 if (days or 0) >= SINCE_MAJOR else 3.0 if (days or 0) >= SINCE_SIGNIFICANT else 0.0), 1)
    return Urgency(level, told, round(z, 2), days, since, round(sd, 2), round(ratio, 2), points)


def since_sentence(name: str, u: Urgency, change_pct: float) -> str:
    """The verified context line a post may carry ("" when there's nothing remarkable to say). It names what was measured: daily closes."""
    way = "rise" if change_pct > 0 else "drop"
    if u.since_days is None and u.z is not None and u.level != ROUTINE:
        return f"Daily closes show no one-day {way} this large for {name} in at least two years."
    if u.since and (u.since_days or 0) >= SINCE_SIGNIFICANT:
        return f"Daily closes show no one-day {way} this large for {name} since {u.since}."
    return ""


__all__ = ["LEAD", "MAJOR", "ROUTINE", "SIGNIFICANT", "Urgency", "assess", "context", "since_sentence"]
