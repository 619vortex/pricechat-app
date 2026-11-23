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
   resumes them, and the owner is told why. Only while market posts are actually ON (the owner resumed them: market_pause False): with
   them already paused (by default, events.MARKET_PAUSE_DEFAULT, or by the owner) there is nothing to stop, so no "SAFETY STOP" is sent
   (2026-11-22: production tripped it on one repeated PIEVERSE figure while market posts were paused by default).
4. Owner alerts: at most one per (kind, subject) per ALERT_EVERY, however often the same figure is held (2026-11-22: the same PIEVERSE
   liquidation figure was held on every 5-minute pass). Every held figure is still recorded on the desk for Admin.

A held figure is never published and never silently dropped: it is logged, recorded and alerted.

Owner, 2026-11-23 ("A liquidation-data problem must never silence independently verified market news"), after production's pause of 11-22
09:35 UTC: one small token's real liquidation squeeze (PIEVERSE, 30x its usual volume), re-noted on every 5-minute pass, counted as three
anomalies and paused every market post, so the S&P, Nasdaq, Dow, gold and silver moves of 11-23 never went out. Four changes:
5. The breaker counts distinct (kind, subject, UTC hour) anomalies, not rows: one figure re-read every pass is one anomaly.
6. Scoped: liquidation anomalies pause liquidation posts only (state.liq_pause); price anomalies (an implausible move, two markets
   disagreeing, figures that don't match) pause market-price posts (state.market_pause), as before. The owner's own market_pause switch
   still holds every market post.
7. A liquidation hour is judged against that hour's market (Hyperliquid's own hourly candles): far above the coin's usual hour is
   believed when the price range or the volume of that same hour shows a squeeze; and never when the total is more than LIQ_VOLUME_SHARE
   times the hour's traded value (the 11-21 unit error: $10.1B against a few hundred million traded). The absolute ceilings stay.
8. (app/newsdesk/sources) the second market's tolerance grows with the size of the move; Nasdaq and the Dow have a second source.
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
    "commodity": {"15m": 15.0, "1h": 20.0, "4h": 25.0, "24h": 50.0, "day": 50.0},  # silver fell 31% in a day within the 2 years
}
LIQ_CEILING = {"BTC": 500_000_000.0, "ETH": 500_000_000.0}  # one coin's hour on the recorded venues, above which a person looks first
LIQ_CEILING_OTHER = 150_000_000.0
LIQ_HISTORY_RATIO = 50.0  # an hour this many times the coin's median recorded hour of the last week is a data error until shown otherwise
LIQ_HISTORY_MIN_HOURS = 24  # with fewer recorded hours than this, only the ceilings apply
LIQ_VOLUME_SHARE = 2.0  # liquidations on Binance+Bybit+OKX above this many times Hyperliquid's traded value in the same hour: a data error
LIQ_SQUEEZE_RANGE = 3.0  # the hour's price range this many times the coin's median hour: a real squeeze
LIQ_SQUEEZE_VOLUME = 4.0  # or its traded value this many times the median hour
LIQUIDATION_KINDS = {"implausible_liquidations"}
TRIP_COUNT = 3
TRIP_WINDOW = timedelta(hours=1)
KEEP = 50  # anomalies remembered on the desk
ALERT_EVERY = timedelta(hours=1)  # one owner alert per (kind, subject) in this time; the state keeps when each was last told (safety_alerted)

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


def liquidation_problem(coin: str, total: float, hourly_history: list[float], market: dict[str, float] | None = None) -> str:
    """Why an hour of liquidations for one coin can't be believed ("" = it can). hourly_history: the coin's recorded hourly totals over
    the last week (hours with at least one liquidation). market (optional): the same hour on Hyperliquid: range_ratio and volume_ratio
    (this hour against the coin's median hour) and volume_usd (traded value)."""
    ceiling = LIQ_CEILING.get(coin, LIQ_CEILING_OTHER)
    if total > ceiling:
        return f"${total / 1e6:,.1f}M in an hour is above the ${ceiling / 1e6:,.0f}M ceiling for {coin}"
    vol = float((market or {}).get("volume_usd") or 0)
    if vol > 0 and total > LIQ_VOLUME_SHARE * vol:
        return f"${total / 1e6:,.1f}M in an hour is {total / vol:,.1f} times the ${vol / 1e6:,.1f}M of {coin} traded on Hyperliquid in that hour"
    if len(hourly_history) >= LIQ_HISTORY_MIN_HOURS:
        typical = statistics.median(hourly_history)
        if typical > 0 and total > LIQ_HISTORY_RATIO * typical:
            rng, vr = float((market or {}).get("range_ratio") or 0), float((market or {}).get("volume_ratio") or 0)
            if rng >= LIQ_SQUEEZE_RANGE or vr >= LIQ_SQUEEZE_VOLUME:
                return ""  # far above its usual hour, but so was the market: a real squeeze (production 11-22 PIEVERSE, 11-23 BTC/NVDA)
            calm = f"; that hour's price range was {rng:.1f}x and its volume {vr:.1f}x the usual" if market else "; the hour's market couldn't be read"
            return f"${total / 1e6:,.1f}M in an hour is {total / typical:,.0f} times {coin}'s typical recorded hour (${typical / 1e6:,.2f}M){calm}"
    return ""


def recent(state: dict[str, Any], now: datetime) -> list[dict[str, Any]]:
    """The anomalies inside TRIP_WINDOW, after the owner's last resume."""
    since = now - TRIP_WINDOW
    reset = state.get("breaker_reset_at")
    if reset:
        since = max(since, _aware(datetime.fromisoformat(str(reset)), now))
    return [a for a in state.get("anomalies") or [] if _aware(datetime.fromisoformat(str(a["at"])), now) >= since]


def distinct(rows: list[dict[str, Any]]) -> int:
    """How many different problems: one (kind, subject, UTC hour) re-noted on every pass is one anomaly (production 2026-11-22: one PIEVERSE
    figure re-read three times tripped the breaker)."""
    return len({(a.get("kind"), a.get("subject"), str(a.get("at") or "")[:13]) for a in rows})


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
        tripped, liq_tripped = "", ""
        window_min = int(TRIP_WINDOW.total_seconds() // 60)
        recent_rows = recent(state, now)
        price_rows = [a for a in recent_rows if a.get("kind") not in LIQUIDATION_KINDS]
        liq_rows = [a for a in recent_rows if a.get("kind") in LIQUIDATION_KINDS]
        if distinct(liq_rows) >= TRIP_COUNT and not state.get("liq_pause"):
            # scoped: liquidation data pauses liquidation posts only, never the price news checked against two markets
            liq_tripped = f"{distinct(liq_rows)} implausible liquidation figures within {window_min} minutes"
            state["liq_pause"], state["liq_pause_reason"], state["liq_pause_at"] = True, f"automatic: {liq_tripped}", now.isoformat()
        if distinct(price_rows) >= TRIP_COUNT:
            from app.newsdesk.events import MARKET_PAUSE_DEFAULT  # here: events imports this module

            pause = state.get("market_pause")
            if not (MARKET_PAUSE_DEFAULT if pause is None else bool(pause)):  # as events.market_paused: market posts are ON, so they are stopped
                tripped = f"{distinct(price_rows)} implausible or contradicted price figures within {window_min} minutes"
                state["market_pause"], state["market_pause_reason"], state["market_pause_at"] = True, f"automatic: {tripped}", now.isoformat()
            else:  # unset (paused by default, events.MARKET_PAUSE_DEFAULT) or paused by the owner: nothing to stop
                logger.info("newsdesk safety: %d held price figures within %d minutes; market posts are already paused, no safety stop needed",
                            distinct(price_rows), window_min)
        told = dict(state.get("safety_alerted") or {})
        tell = []
        for it in items:
            key = f"newsdesk:safety:{it['kind']}:{it['subject']}"[:128]
            last = told.get(key)
            if last and now - _aware(datetime.fromisoformat(str(last)), now) < ALERT_EVERY:
                continue  # told within the hour: recorded above, not alerted again
            told[key] = now.isoformat()
            tell.append((key, it))
        state["safety_alerted"] = {k: v for k, v in told.items() if now - _aware(datetime.fromisoformat(str(v)), now) < timedelta(days=1)}
        s.state = state
        db.commit()
        for key, it in tell:
            await notify_owner(db, key, f"News Desk held a figure it can't believe ({it['kind']}, {it['subject']}): {it['why']}.\nNothing was published. "
                               "Check Admin > X > News Desk.", settings=settings, now=now)
        if liq_tripped:
            logger.warning("newsdesk safety: liquidation posts paused automatically: %s", liq_tripped)
            await notify_owner(db, "newsdesk:safety:liq-stop", f"News Desk: liquidation posts are paused automatically ({liq_tripped}). Price news, checked "
                               "against two markets, goes on. Liquidation posts stay off until you resume them (News Desk settings: liq_pause off).",
                               settings=settings, now=now, cooldown=False)
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


__all__ = ["LIMITS", "LIQUIDATION_KINDS", "TRIP_COUNT", "TRIP_WINDOW", "distinct", "liquidation_problem", "limit", "note", "plausible_move", "recent", "settle"]
