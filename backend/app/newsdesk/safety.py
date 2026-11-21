"""Plausibility limits and the automatic safety stop (owner, 2026-11-21, Phase 1).

Telegram 204 published "$10.1B of BTCUSD_PERP longs liquidated on Binance in an hour": a unit error (coin-margined contracts counted as
coins) that overstated the figure about 570 times. Nothing asked whether such a number could be true. This module does:

1. LIMITS: the largest move a figure may show, per asset group and window, before it is treated as a data error and held for a person.
   Calibrated 2026-11-21 on Yahoo's own data for every watched instrument (2 years of daily closes, 60 days of 5-minute bars; tests/
   newsdesk_plausibility_calibration_20261121.json). The largest REAL moves were 2-7% over 15 minutes to 24 hours and up to 12% (Nasdaq),
   19% (Nvidia), 22% (ETH), 31% (silver) and 34% (XRP) in a day. Each limit sits well above those and above known historic crashes (the
   S&P 500's -20.5% day in 1987), so only a broken figure, or a move with no precedent that a person should look at anyway, trips one.
2. Liquidations: an hour's total for one coin is held when it is far above that coin's own recent hours (LIQ_HISTORY_RATIO x the median
   hour of the last week) or above an absolute ceiling. A 570x unit error is caught by both; a 20x one by the history rule.
3. The safety stop: every held figure is an anomaly (recorded on the desk, the owner told). TRIP_COUNT anomalies within TRIP_WINDOW mean
   something is wrong with the data, not with the market: desk-measured market posts are paused (state.market_pause) until the owner
   resumes them, and the owner is told why.

A held figure is never published and never silently dropped: it is logged, recorded and alerted.
"""

from __future__ import annotations

import logging
import statistics
from datetime import datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

# the largest believable move, in percent, per group and window ("day": against the previous close; "24h": rolling)
LIMITS: dict[str, dict[str, float]] = {
    "crypto_major": {"15m": 25.0, "1h": 35.0, "4h": 45.0, "24h": 60.0, "day": 60.0},
    "crypto_alt": {"15m": 40.0, "1h": 50.0, "4h": 60.0, "24h": 80.0, "day": 80.0},
    "index": {"15m": 8.0, "1h": 12.0, "4h": 15.0, "24h": 25.0, "day": 25.0},
    "stock": {"15m": 40.0, "1h": 50.0, "4h": 60.0, "24h": 80.0, "day": 80.0},
    "commodity": {"15m": 15.0, "1h": 20.0, "4h": 25.0, "24h": 40.0, "day": 40.0},
}
LIQ_CEILING = {"BTC": 500_000_000.0, "ETH": 500_000_000.0}  # one coin's hour on the recorded venues, above which a person looks first
LIQ_CEILING_OTHER = 150_000_000.0
LIQ_HISTORY_RATIO = 50.0  # an hour this many times the coin's median recorded hour of the last week is a data error until shown otherwise
LIQ_HISTORY_MIN_HOURS = 24  # with fewer recorded hours than this, only the ceilings apply
TRIP_COUNT = 3
TRIP_WINDOW = timedelta(hours=1)
KEEP = 50  # anomalies remembered on the desk

_pending: list[dict[str, Any]] = []  # held figures noticed by synchronous code, recorded by settle() in the desk's async pass


def limit(group: str, window: str) -> float:
    return LIMITS.get(group, LIMITS["stock"]).get(window, LIMITS.get(group, LIMITS["stock"])["day"])


def plausible_move(group: str, window: str, change: float) -> str:
    """Why a measured change can't be believed ("" = it can)."""
    cap = limit(group, window)
    if abs(change) > cap:
        return f"{change:+.2f}% over {window} is beyond the {cap:g}% a {group.replace('_', ' ')} has ever plausibly moved"
    return ""


def note(kind: str, subject: str, why: str, **figures: Any) -> None:
    """A figure held as implausible or contradicted: remembered until the desk's next pass records and reports it."""
    logger.warning("newsdesk safety: %s %s held: %s", kind, subject, why)
    _pending.append({"kind": kind, "subject": subject, "why": why[:300], "figures": {k: v for k, v in figures.items() if v is not None}})


def liquidation_problem(coin: str, total: float, hourly_history: list[float]) -> str:
    """Why an hour of liquidations for one coin can't be believed ("" = it can). hourly_history: the coin's recorded hourly totals over
    the last week (hours with at least one liquidation)."""
    ceiling = LIQ_CEILING.get(coin, LIQ_CEILING_OTHER)
    if total > ceiling:
        return f"${total / 1e6:,.1f}M in an hour is above the ${ceiling / 1e6:,.0f}M ceiling for {coin}"
    if len(hourly_history) >= LIQ_HISTORY_MIN_HOURS:
        typical = statistics.median(hourly_history)
        if typical > 0 and total > LIQ_HISTORY_RATIO * typical:
            return f"${total / 1e6:,.1f}M in an hour is {total / typical:,.0f} times {coin}'s typical recorded hour (${typical / 1e6:,.2f}M)"
    return ""


def recent(state: dict[str, Any], now: datetime) -> list[dict[str, Any]]:
    """The anomalies inside TRIP_WINDOW, after the owner's last resume."""
    since = now - TRIP_WINDOW
    reset = state.get("breaker_reset_at")
    if reset:
        since = max(since, _aware(datetime.fromisoformat(str(reset)), now))
    return [a for a in state.get("anomalies") or [] if _aware(datetime.fromisoformat(str(a["at"])), now) >= since]


def _aware(t: datetime, now: datetime) -> datetime:
    return t if t.tzinfo else t.replace(tzinfo=now.tzinfo)


async def settle(db: Any, now: datetime, *, settings: Any = None) -> int:
    """Record the held figures noticed since the last pass, tell the owner, and trip the safety stop when they pile up. Returns how many
    were recorded. Never raises."""
    if not _pending:
        return 0
    items = list(_pending)
    _pending.clear()
    try:
        from app.alerts import notify_owner
        from app.models.newsdesk import NdSettings

        s = db.get(NdSettings, 1)
        if s is None:
            return 0
        state = dict(s.state or {})
        rows = list(state.get("anomalies") or [])
        for it in items:
            rows.append({**it, "at": now.isoformat()})
        state["anomalies"] = rows[-KEEP:]
        tripped = ""
        if len(recent(state, now)) >= TRIP_COUNT and state.get("market_pause") is not True:
            tripped = f"{len(recent(state, now))} implausible or contradicted figures within {int(TRIP_WINDOW.total_seconds() // 60)} minutes"
            state["market_pause"], state["market_pause_reason"], state["market_pause_at"] = True, f"automatic: {tripped}", now.isoformat()
        s.state = state
        db.commit()
        for it in items:
            await notify_owner(db, f"newsdesk:safety:{it['kind']}:{it['subject']}"[:128],
                               f"News Desk held a figure it can't believe ({it['kind']}, {it['subject']}): {it['why']}.\nNothing was published. "
                               "Check Admin > X > News Desk.", settings=settings, now=now)
        if tripped:
            logger.warning("newsdesk safety: market-data posting paused automatically: %s", tripped)
            await notify_owner(db, "newsdesk:safety:stop", f"News Desk SAFETY STOP: market-data posts are paused automatically ({tripped}). "
                               "News goes on. Nothing market-related is posted until you resume it (News Desk settings: market_pause off).",
                               settings=settings, now=now, cooldown=False)
        return len(items)
    except Exception as e:  # noqa: BLE001 - the desk goes on; the anomalies are in the log
        db.rollback()
        logger.warning("newsdesk safety: recording held figures failed (%s)", type(e).__name__)
        return 0


__all__ = ["LIMITS", "TRIP_COUNT", "TRIP_WINDOW", "liquidation_problem", "limit", "note", "plausible_move", "recent", "settle"]
