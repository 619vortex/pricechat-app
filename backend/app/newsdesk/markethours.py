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

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

FRESH = timedelta(minutes=20)
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


def fresh(at: datetime, now: datetime) -> bool:
    t = at if at.tzinfo else at.replace(tzinfo=timezone.utc)
    return now - t <= FRESH


__all__ = ["FRESH", "NEW_YORK", "day_word", "fresh", "group_of", "session_date", "zone_for"]
