"""How our published posts actually did (owner, 2026-11-19: "Measure actual post performance using available metrics, normalized for age,
audience size and topic. Learn which verified stories and hooks attract genuine engagement, without rewarding misleading sensationalism").

AVAILABLE METRICS: X's own figures for our posts (impressions, likes, reposts, replies, quotes, bookmarks), read once a day by the News
Desk's and Trending's collect_metrics (already paid for: owned reads). Telegram's Bot API gives no view or reaction counts for channel
posts, so Telegram can't be measured here: that is stated, not estimated.

NORMALIZED, so a post is compared with what it could have done:
- reach: impressions per follower at the time it went out (growth snapshots), divided by how much of a post's life had passed when it was
  read (1 - e^(-age/LIFE_HOURS): most of a post's impressions come in its first hours), then against the median post: 1.0 is typical.
- engagement: engagements per impression (reposts and bookmarks count double: sharing and saving are deliberate), against the median of
  posts on the same topic, so a quiet topic isn't punished for its topic.
- index: the geometric mean of the two. Nothing is invented: a post without metrics is not scored.

NOT REWARDING SENSATIONALISM: every post here already passed verification. A post whose words read as bait (shouting, "you won't believe",
exclamation marks, "SHOCKING") or that the owner marked Boring is shown but never counted as a lesson; a lesson needs MIN_N posts. What it
learns is REPORTED in the Terminal; it changes no score or decision (the editorial rules stay as they are while they are being validated).
"""
from __future__ import annotations

import math
import re
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow

LIFE_HOURS = 6.0
MIN_N = 3
DAYS = 30
BAIT = re.compile(r"\b(you won'?t believe|shocking|insane|mind[- ]blowing|jaw[- ]dropping|must see|crazy)\b|!{1,}", re.I)


def _aware(d: datetime | None) -> datetime | None:
    return None if d is None else (d if d.tzinfo else d.replace(tzinfo=timezone.utc))


def acts(m: dict[str, Any]) -> float:
    g = lambda k: float(m.get(k) or 0)  # noqa: E731
    return g("like_count") + g("reply_count") + g("quote_count") + 2 * g("retweet_count") + 2 * g("bookmark_count")


def bait(text: str) -> bool:
    letters = [c for c in text or "" if c.isalpha()]
    shouting = len(letters) > 20 and sum(c.isupper() for c in letters) / len(letters) > 0.6
    return shouting or bool(BAIT.search(text or ""))


def _followers(db: Session) -> list[tuple[datetime, int]]:
    from app.models.social_intel import GrowthSnapshot

    rows = list(db.execute(select(GrowthSnapshot.subject, GrowthSnapshot.taken_at, GrowthSnapshot.followers)
                           .where(GrowthSnapshot.kind == "x_account", GrowthSnapshot.followers.is_not(None)).order_by(GrowthSnapshot.taken_at)))
    if not rows:
        return []
    subjects: dict[str, int] = {}
    for s, _, _ in rows:
        subjects[s] = subjects.get(s, 0) + 1
    main = max(subjects, key=subjects.get)  # type: ignore[arg-type]
    return [(_aware(t), int(f)) for s, t, f in rows if s == main]  # type: ignore[misc]


def _at(points: list[tuple[datetime, int]], when: datetime | None) -> int | None:
    best = points[0][1] if points else None
    for t, v in points:
        if when is not None and t <= when:
            best = v
    return best


def _posts(db: Session, now: datetime) -> list[dict[str, Any]]:
    from app.models.newsdesk import NdPost
    from app.models.trending import TtPost

    out = []
    since = now - timedelta(days=DAYS)
    for p in db.scalars(select(NdPost).where(NdPost.status == "published", NdPost.published_at >= since)):
        f = p.facts or {}
        out.append({"lane": "x-newsdesk", "id": p.id, "x_id": p.x_id, "text": p.text or "", "metrics": p.metrics or {}, "at": _aware(p.published_at),
                    "topic": str(f.get("topic") or p.category or "other"), "hook": str((f.get("viral") or {}).get("hook") or ""),
                    "event": f.get("event_id"), "title": str(f.get("title") or f.get("headline") or (p.text or "").split("\n")[0])})
    for p in db.scalars(select(TtPost).where(TtPost.status == "published", TtPost.published_at >= since)):
        out.append({"lane": "x-trending", "id": p.id, "x_id": p.x_id, "text": p.text or "", "metrics": p.metrics or {}, "at": _aware(p.published_at),
                    "topic": str(p.category or "other"), "hook": str(p.angle or ""), "topic_id": p.topic_id, "title": (p.text or "").split("\n")[0]})
    return out


def scored(db: Session, now: datetime | None = None) -> list[dict[str, Any]]:
    """Every measured post with its normalized reach, engagement and index."""
    from app.newsdesk import feedback

    now = now or utcnow()
    followers = _followers(db)
    labels = feedback.labels(db)
    rows = []
    for p in _posts(db, now):
        m = p["metrics"]
        imp = float(m.get("impression_count") or 0)
        if imp <= 0:
            continue
        read = _aware(datetime.fromisoformat(str(m["read_at"]))) if m.get("read_at") else now
        age = max(0.25, ((read or now) - (p["at"] or now)).total_seconds() / 3600)
        f = _at(followers, p["at"])
        life = 1 - math.exp(-age / LIFE_HOURS)
        target = f"event:{p['event']}" if p.get("event") else (f"topic:{p['topic_id']}" if p.get("topic_id") else "")
        rows.append({**{k: v for k, v in p.items() if k not in ("metrics", "at")}, "at": p["at"].isoformat() if p["at"] else None,
                     "impressions": int(imp), "engagements": acts(m), "age_hours": round(age, 1), "followers": f,
                     "reach_raw": imp / (f * life) if f else imp / life, "rate": acts(m) / imp,
                     "bait": bait(p["text"]), "owner": sorted(labels.get(target, set())) if target else []})
    if not rows:
        return []
    mid_reach = median(r["reach_raw"] for r in rows) or 1e-9
    by_topic_rate: dict[str, list[float]] = {}
    for r in rows:
        by_topic_rate.setdefault(r["topic"], []).append(r["rate"])
    mid_all = median(r["rate"] for r in rows) or 1e-9
    for r in rows:
        peers = by_topic_rate[r["topic"]]
        base = median(peers) if len(peers) >= MIN_N else mid_all
        r["reach"] = round(r["reach_raw"] / mid_reach, 2)
        r["engagement"] = round(r["rate"] / (base or 1e-9), 2) if r["rate"] else 0.0
        r["index"] = round(math.sqrt(max(r["reach"], 0.01) * max(r["engagement"], 0.01)), 2)
        r["lesson"] = not r["bait"] and "boring" not in r["owner"]
        r.pop("reach_raw", None)
    return rows


TELEGRAM = "not measurable: Telegram's Bot API gives no view counts for channel posts"


def measure(db: Session, now: datetime | None = None) -> dict[str, Any]:
    """The scored posts and the lessons (by topic and by hook) with enough posts behind them."""
    rows = scored(db, now)
    if not rows:
        return {"measured": 0, "best": [], "worst": [], "by_topic": [], "by_hook": [], "followers_known": bool(_followers(db)), "telegram": TELEGRAM,
                "note": "No X post with metrics yet."}
    lessons = [r for r in rows if r["lesson"]]

    def group(key: str) -> list[dict[str, Any]]:
        g: dict[str, list[float]] = {}
        for r in lessons:
            if r.get(key):
                g.setdefault(str(r[key]), []).append(r["index"])
        return sorted(({key: k, "posts": len(v), "index": round(median(v), 2)} for k, v in g.items() if len(v) >= MIN_N), key=lambda x: -x["index"])

    ranked = sorted(rows, key=lambda r: -r["index"])
    return {"measured": len(rows), "lessons_from": len(lessons), "excluded_bait_or_boring": len(rows) - len(lessons),
            "followers_known": any(r.get("followers") for r in rows), "best": ranked[:5], "worst": ranked[-3:] if len(ranked) > 5 else [],
            "by_topic": group("topic"), "by_hook": group("hook"), "telegram": TELEGRAM,
            "note": f"X posts of the last {DAYS} days. 1.0 = typical. Reach is per follower and adjusted for the post's age; engagement is against its own topic."}


def of_target(db: Session, target: str) -> dict[str, Any] | None:
    """One story's X performance, for the Terminal story panel (None until X's metrics for it are in)."""
    try:
        kind, _, raw = target.partition(":")
        for r in scored(db):
            if (kind == "event" and str(r.get("event")) == raw) or (kind == "topic" and str(r.get("topic_id")) == raw):
                return {k: r.get(k) for k in ("lane", "impressions", "engagements", "age_hours", "reach", "engagement", "index", "bait")}
    except Exception:  # noqa: BLE001
        return None
    return None
