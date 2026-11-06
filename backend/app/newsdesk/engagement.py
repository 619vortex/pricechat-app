"""What our own audience engaged with lately, as a small adjustment to the virality score (-8 to +8 points).

From X's own metrics for the desk's published posts (collected once a day by engine.collect_metrics): engagements per impression
(likes, reposts x2, replies, quotes, bookmarks) for each kind of story, against the account's average. A kind with few posts is pulled
towards the average, so one lucky post changes little. Telegram's Bot API gives no view counts for channel posts, so Telegram can't feed
this yet; both platforms still use the result, since they post the same stories.

Nothing is adjusted until there are at least MIN_POSTS measured posts: with no data the score is the plain virality.
"""

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.newsdesk import NdPost

DAYS = 21
MIN_POSTS = 8
PRIOR = 5  # pseudo-posts at the average for each kind
SPAN = 8.0  # points at twice (or half) the average rate


def rate(metrics: dict[str, Any]) -> float | None:
    seen = float(metrics.get("impression_count") or 0)
    if seen < 50:
        return None
    acts = sum(float(metrics.get(k) or 0) for k in ("like_count", "reply_count", "quote_count", "bookmark_count")) + 2 * float(metrics.get("retweet_count") or 0)
    return acts / seen


def kind_of(facts: dict[str, Any]) -> str:
    return str(facts.get("topic") or ("move" if facts.get("symbol") else "other"))


def adjustments(db: Session, now: datetime) -> dict[str, float]:
    """{topic: points}. Empty until there is enough measured data."""
    rows = db.scalars(select(NdPost).where(NdPost.status == "published", NdPost.published_at >= now - timedelta(days=DAYS)))
    by: dict[str, list[float]] = {}
    for p in rows:
        r = rate(p.metrics or {})
        if r is not None:
            by.setdefault(kind_of(p.facts or {}), []).append(r)
    every = [r for rs in by.values() for r in rs]
    if len(every) < MIN_POSTS:
        return {}
    mean = sum(every) / len(every)
    if mean <= 0:
        return {}
    out = {}
    for kind, rs in by.items():
        shrunk = (sum(rs) + PRIOR * mean) / (len(rs) + PRIOR)
        ratio = shrunk / mean  # 1.0 is average
        points = SPAN * (ratio - 1) if ratio >= 1 else -SPAN * (1 / ratio - 1)
        out[kind] = round(max(-SPAN, min(SPAN, points)), 1)
    return out
