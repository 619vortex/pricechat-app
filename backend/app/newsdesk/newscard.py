"""The branded Price Chat news card as the last picture (owner, 2026-11-24: "Branded Price Chat news cards as an image fallback").

Order of pictures for a post, unchanged above this one: a licensed photograph or chart (the Visual Editor), then the source article's own
link preview (Telegram's preview of the publisher's page; X's card of the link). Only when neither exists does a post get this card,
instead of waiting for the owner (24.3% of the 7-day replay's stories were held for want of a picture, docs/newsdesk/2026-11-24-terminal-
overhaul.md). The card is drawn here from the post's own words (app/newsdesk/card: the headline as posted, its tickers, the outlets and the
time, the kiwi and PRICE CHAT NEWS); nothing is fetched, nothing is licensed from anyone, nothing the post doesn't say.

The owner can switch it off in the desk's state: `card_fallback: false` (then the old rule: held for review).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


def enabled(state: dict[str, Any] | None) -> bool:
    return bool((state or {}).get("card_fallback", True))


def _aware(t: datetime, now: datetime) -> datetime:
    return t if t.tzinfo else t.replace(tzinfo=now.tzinfo or timezone.utc)


def render(ev: Any, now: datetime) -> bytes | None:
    """The card for this event, or None when it can't be drawn (a drawing failure never sends the post bare)."""
    from app.newsdesk import card, events

    headline = str(getattr(ev, "headline", "") or getattr(ev, "title", "") or "").strip()
    if not headline:
        return None
    try:
        f = getattr(ev, "facts", None) or {}
        b = events.brief_of(ev, now)
        names = [str(x.get("name")) for x in (getattr(ev, "sources", None) or []) if x.get("name")][:2] or ["Price Chat"]
        stamp = f.get("as_of") or f.get("published_at")
        at = _aware(datetime.fromisoformat(str(stamp)), now) if stamp else now
        stat = str(f.get("stat") or "") if (getattr(ev, "kind", "") in ("market", "update", "data") or f.get("symbol")) else ""
        return card.render(headline, b, names, at, stat=stat, spark=list(f.get("spark") or []))
    except Exception as e:  # noqa: BLE001 - the fallback never breaks the lane: without it the post is held, as before
        logger.warning("newsdesk newscard: event %s: the card could not be drawn (%s)", getattr(ev, "id", None), type(e).__name__)
        return None


def fallback(db: Any, ev: Any, now: datetime) -> bytes | None:
    """The card when the owner's switch allows it, else None."""
    from app.newsdesk import engine

    try:
        state = engine.settings_row(db).state or {}
    except Exception:  # noqa: BLE001 - a settings read failure keeps the default (on)
        state = {}
    if not enabled(state):
        return None
    png = render(ev, now)
    if png:
        logger.info("newsdesk newscard: event %s: no licensed picture and no article preview: the branded Price Chat card", getattr(ev, "id", None))
    return png


__all__ = ["enabled", "fallback", "render"]
