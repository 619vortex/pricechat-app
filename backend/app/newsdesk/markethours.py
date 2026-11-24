"""One set of market-hours, session and freshness rules for every market figure the desk prints (owner, 2026-11-21, Phase 1).

Before this the rules disagreed by path: moves and the pre-send re-read allowed a 20-minute-old price, data posts 90 minutes, the picture
check 90 minutes; a data post about a session that ended on Friday still said "today" on Saturday; the recap measured US indices over a
rolling 24 hours (Telegram 203's mistake); 52-week highs and round levels were stamped with the time of the desk's pass, not the price's.

- FRESH: the one age limit for a price a post prints, on every path, when the post is made and when it is sent.
- Sessions: crypto trades around the clock and its "day" is the UTC day; US stocks, indices and futures are dated in New York, where their
  sessions are (a Friday 16:00 ET close is Friday's close whatever the UTC date). day_word() names the session a figure belongs to the way
  a reader says it: "today", "yesterday", "on Friday", "on 14 Nov".
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

FRESH = timedelta(minutes=20)
TAG_MAX = timedelta(hours=1)  # a news story's measured price tag ("(BTC -5.4% IN 24H)") is only printed this long after it was measured
NEW_YORK = ZoneInfo("America/New_York")
CRYPTO_GROUPS = ("crypto_major", "crypto_alt")


def zone_for(group: str) -> timezone | ZoneInfo:
    return timezone.utc if group in CRYPTO_GROUPS else NEW_YORK


def group_of(symbol: str) -> str:
    """The watchlist group of a Yahoo symbol ("" when it isn't watched)."""
    from app.newsdesk import moves, sources

    item = next((x for x in moves.watch_items(sources.DEFAULT_WATCHLIST) if x["symbol"] == symbol), None)
    if item is not None:
        return str(item.get("group") or "")
    if symbol.endswith("-USD"):
        return "crypto_alt"
    if symbol.endswith("=F"):
        return "commodity"
    if symbol.startswith("^"):
        return "index"
    return "stock"


def session_date(at: datetime, group: str) -> datetime:
    t = at if at.tzinfo else at.replace(tzinfo=timezone.utc)
    return t.astimezone(zone_for(group))


def day_word(at: datetime, now: datetime, group: str) -> str:
    """The session a figure belongs to, as a reader says it, from `now`'s point of view in the market's own time zone."""
    d, today = session_date(at, group).date(), session_date(now, group).date()
    if d == today:
        return "today"
    if (today - d).days == 1:
        return "yesterday"
    if 1 < (today - d).days < 7:
        return f"on {session_date(at, group):%A}"
    return f"on {session_date(at, group):%d %b}".replace(" 0", " ")


# NYSE full-day closures (the exchange's published calendar), so a weekday without a bar is a holiday, never a missing session
US_HOLIDAYS = frozenset({
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25", "2026-06-19", "2026-07-03", "2026-09-07", "2026-11-26",
    "2026-12-25", "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31", "2027-06-18", "2027-07-05", "2027-09-06",
    "2027-11-25", "2027-12-24",
})
FUTURES_DAY_ROLL = timedelta(hours=6)  # CME's trading day starts at 18:00 New York time: 18:00 ET + 6 h is the next calendar day


def trading_day(at: datetime, group: str, symbol: str = "") -> date:
    """The session a price belongs to: the UTC day for crypto; for futures the CME trading day, which starts at 18:00 New York time the
    evening before (silver at 01:40 UTC on 11-20 trades in the 11-20 session, though New York's date is still 11-19); for stocks and
    indices the New York date. A weekend price belongs to the next trading day."""
    t = at if at.tzinfo else at.replace(tzinfo=timezone.utc)
    if group in CRYPTO_GROUPS or symbol.endswith("-USD"):
        return t.astimezone(timezone.utc).date()
    local = t.astimezone(NEW_YORK)
    if symbol.endswith("=F") or group == "commodity":
        local = local + FUTURES_DAY_ROLL
    d = local.date()
    while d.weekday() >= 5 or d.isoformat() in US_HOLIDAYS:
        d += timedelta(days=1)
    return d


def bar_day(t: datetime, group: str, symbol: str = "") -> date:
    """The session a daily bar is stamped with (Yahoo stamps futures bars 00:00 New York, stocks at the open, crypto 00:00 UTC)."""
    t = t if t.tzinfo else t.replace(tzinfo=timezone.utc)
    if group in CRYPTO_GROUPS or symbol.endswith("-USD"):
        return t.astimezone(timezone.utc).date()
    return t.astimezone(NEW_YORK).date()


def previous_trading_day(d: date, group: str, symbol: str = "") -> date:
    if group in CRYPTO_GROUPS or symbol.endswith("-USD"):
        return d - timedelta(days=1)
    d -= timedelta(days=1)
    while d.weekday() >= 5 or d.isoformat() in US_HOLIDAYS:
        d -= timedelta(days=1)
    return d


def fresh(at: datetime, now: datetime) -> bool:
    t = at if at.tzinfo else at.replace(tzinfo=timezone.utc)
    return now - t <= FRESH


__all__ = ["FRESH", "NEW_YORK", "US_HOLIDAYS", "bar_day", "day_word", "fresh", "group_of", "previous_trading_day", "session_date", "trading_day",
           "zone_for"]
