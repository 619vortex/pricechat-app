"""Community growth for the public website's "Join the chat" rows: real counts only.

Reads the audience snapshots Price Chat already records (analytics/snapshots.py):
  youtube   subscribers   kind "youtube_channel", from every YouTube channel read (youtube/service.py)
  x         followers     kind "x_account", from X responses / a 6-hourly account lookup
  telegram  members       kind "telegram_chat", from the Bot API every few hours

Nothing is estimated or back-filled: a platform's history starts at its first snapshot, and changes are only
reported when a snapshot old enough exists (24 h / 7 d). A count is only shown for the account the website links to
(the handle must match), never for some other account Price Chat happens to read.
"""

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.connectors.state import get_connector_state
from app.db.base import utcnow
from app.models.enums import Platform
from app.models.social_intel import GrowthSnapshot
from app.models.telegram import TelegramSource

HISTORY_DAYS = 30


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _handle(url: str | None) -> str | None:
    """'https://x.com/pricechat' -> 'pricechat' (lower case), for matching an account to the public link."""
    if not url:
        return None
    tail = url.rstrip("/").rsplit("/", 1)[-1].lstrip("@").lower()
    return tail or None


def _same(link: str | None, username: str | None) -> bool:
    """True unless both are known and differ: never show another account's numbers on our link."""
    a, b = _handle(link), (username or "").lstrip("@").lower() or None
    return a is None or b is None or a == b


def _points(db: Session, kind: str, subject: str, field: str) -> list[tuple[datetime, int]]:
    col = getattr(GrowthSnapshot, field)
    rows = db.execute(select(GrowthSnapshot.taken_at, col).where(
        GrowthSnapshot.kind == kind, GrowthSnapshot.subject == subject, col.is_not(None)).order_by(GrowthSnapshot.taken_at))
    return [(_aware(t), int(v)) for t, v in rows]


def summarize(points: list[tuple[datetime, int]], now: datetime | None = None) -> dict[str, Any] | None:
    """Current count, 24 h / 7 d / 30 d change (None until enough history exists) and the daily history (last value
    of each UTC day, at most 30 days)."""
    if not points:
        return None
    now = now or utcnow()
    at, count = points[-1]

    def change(age: timedelta) -> int | None:
        old = [v for t, v in points if t <= now - age]
        return count - old[-1] if old else None

    start = (now - timedelta(days=HISTORY_DAYS - 1)).date()
    daily: dict[str, int] = {}
    for t, v in points:
        if t.date() >= start:
            daily[t.date().isoformat()] = v  # points are in time order: the day's last value wins
    history = [{"day": d, "value": v} for d, v in sorted(daily.items())]
    first = history[0]["value"] if history else count
    return {
        "count": count, "as_of": at.isoformat(), "tracking_since": points[0][0].date().isoformat(),
        "change_24h": change(timedelta(hours=24)), "change_7d": change(timedelta(days=7)),
        "change_30d": change(timedelta(days=HISTORY_DAYS)),
        # over what the chart shows: since the first day in the window (or since tracking began)
        "change_window": count - first, "change_window_pct": round((count - first) / first * 100, 1) if first else None,
        "window_start": history[0]["day"] if history else at.date().isoformat(),
        "history": history,
    }


def growth(db: Session, links: dict[str, str | None]) -> dict[str, dict[str, Any] | None]:
    """Per network (only those with a public link): the summary, or None when Price Chat has no real count yet."""
    out: dict[str, dict[str, Any] | None] = {"youtube": None, "x": None, "telegram": None}
    yt = get_connector_state(db, Platform.YOUTUBE)
    if links.get("youtube") and yt.account_external_id and _same(links["youtube"], yt.account_username):
        out["youtube"] = summarize(_points(db, "youtube_channel", yt.account_external_id, "followers"))
    x = get_connector_state(db, Platform.X)
    if links.get("x") and x.account_external_id and _same(links["x"], x.account_username):
        out["x"] = summarize(_points(db, "x_account", x.account_external_id, "followers"))
    if links.get("telegram"):
        sources = list(db.scalars(select(TelegramSource).where(TelegramSource.enabled.is_(True))))
        want = _handle(links["telegram"])
        match = [s for s in sources if (s.username or "").lower() == want] or (sources if len(sources) == 1 and want is None else [])
        if match:
            out["telegram"] = summarize(_points(db, "telegram_chat", str(match[0].chat_id), "members"))
    for k, v in out.items():
        if v is not None:
            v["unit"] = {"youtube": "subscribers", "x": "followers", "telegram": "members"}[k]
    return out
