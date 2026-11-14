"""What our audience engaged with, fed back into ranking as small weights: one per category ("cat:crypto") and per source type
("src:reddit"). From X's metrics for published trend posts (impressions, likes, reposts, replies; engine.collect_metrics).

Bounded so it can't run away: each weight is shrunk towards the average (PRIOR pseudo-posts), stays within 0.8-1.2, and rank.score clamps
their product to the same range. Nothing changes until MIN_POSTS posts are measured.
"""

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.trending import TtPost, TtTopic
from app.newsdesk.engagement import rate

DAYS = 30
MIN_POSTS = 5
PRIOR = 5
LOW, HIGH = 0.8, 1.2
SLOPE = 0.5  # twice the average rate -> 1.2 (capped); half -> 0.8


def bounded(rates: list[float], mean: float) -> float:
    shrunk = (sum(rates) + PRIOR * mean) / (len(rates) + PRIOR)
    return round(max(LOW, min(HIGH, 1 + SLOPE * (shrunk / mean - 1))), 3)


def measured(db: Session, now: datetime) -> list[tuple[TtPost, float, list[str]]]:
    rows = list(db.scalars(select(TtPost).where(TtPost.status == "published", TtPost.published_at >= now - timedelta(days=DAYS))))
    topics = {t.id: t for t in db.scalars(select(TtTopic).where(TtTopic.id.in_([p.topic_id for p in rows])))} if rows else {}
    out = []
    for p in rows:
        r = rate(p.metrics or {})
        if r is None:
            continue
        t = topics.get(p.topic_id)
        out.append((p, r, list((t.parts or {}).get("sources") or []) if t else []))
    return out


def weights(db: Session, now: datetime) -> dict[str, float]:
    data = measured(db, now)
    if len(data) < MIN_POSTS:
        return {}
    mean = sum(r for _, r, _ in data) / len(data)
    if mean <= 0:
        return {}
    by: dict[str, list[float]] = {}
    for p, r, srcs in data:
        by.setdefault(f"cat:{p.category}", []).append(r)
        for s in srcs:
            by.setdefault(f"src:{s}", []).append(r)
    return {k: bounded(v, mean) for k, v in sorted(by.items())}


def report(db: Session, now: datetime) -> dict[str, Any]:
    data = measured(db, now)
    totals = {k: int(sum(float((p.metrics or {}).get(k) or 0) for p, _, _ in data)) for k in ("impression_count", "like_count", "retweet_count", "reply_count")}
    cats: dict[str, dict[str, float]] = {}
    for p, r, _ in data:
        c = cats.setdefault(p.category, {"posts": 0, "impressions": 0, "likes": 0, "reposts": 0, "replies": 0, "rate_sum": 0.0})
        m = p.metrics or {}
        c["posts"] += 1
        c["impressions"] += int(m.get("impression_count") or 0)
        c["likes"] += int(m.get("like_count") or 0)
        c["reposts"] += int(m.get("retweet_count") or 0)
        c["replies"] += int(m.get("reply_count") or 0)
        c["rate_sum"] += r
    per_category = {k: {**{x: v[x] for x in ("posts", "impressions", "likes", "reposts", "replies")}, "rate": round(v["rate_sum"] / v["posts"], 5)}
                    for k, v in sorted(cats.items())}
    return {"measured_posts": len(data), "learning": len(data) >= MIN_POSTS, "needs": MIN_POSTS, "weights": weights(db, now), "totals": totals,
            "bounds": [LOW, HIGH], "per_category": per_category}
