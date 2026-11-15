"""Trending Topics and the News Desk share one X account. The News Desk's plan comes first; Trending fits around it:

- one story, one post: a topic the News Desk posted, queued or released in the last 24 h (checks.same_event on the headlines, or most of
  the words) is held as "covered by the News Desk"; the News Desk in turn skips an event Trending already posted (engine.deliver_x).
- spacing: no trend post within newsdesk_gap_minutes of a News Desk post, a released event or one of its six scheduled slots, and at
  least gap_minutes between two trend posts.
- one daily cap on X for both (shared_daily_cap, 24 h rolling) and Trending's own max_per_day.
- the News Desk's emergency stop and pauses (X refused it, the network's emergency pause) stop Trending's posting too.
"""

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.newsdesk import NdEvent, NdPost, NdSettings
from app.models.trending import TtPost, TtSettings
from app.newsdesk import checks

SENT_LIVE = ("published", "publishing", "uncertain")
WINDOW = timedelta(hours=24)  # the caps
SAME_STORY_WINDOW = timedelta(hours=48)  # one event, one post across both lanes: yesterday's story is never re-posted


def _aware(d: datetime | None, now: datetime) -> datetime | None:
    return None if d is None else (d if d.tzinfo else d.replace(tzinfo=now.tzinfo or timezone.utc))


def newsdesk_live(db: Session) -> bool:
    nd = db.get(NdSettings, 1)
    return bool(nd is not None and nd.mode == "live")


def x_live(db: Session, s: TtSettings) -> bool:
    """Trend posts reach X only while the News Desk's X lane is live AND Trending's own live switch is on. Otherwise: dry run."""
    return bool(s.live) and newsdesk_live(db)


def _sent_statuses(db: Session, s: TtSettings) -> tuple[str, ...]:
    return SENT_LIVE if x_live(db, s) else SENT_LIVE + ("dry_run",)


def same_story(a: str, b: str) -> bool:
    if not a or not b:
        return False
    return checks.same_event(a, b) or checks.similarity(checks.fingerprint(a), checks.fingerprint(b)) >= 0.5


def newsdesk_conflict(db: Session, titles: list[str], now: datetime) -> str | None:
    """The News Desk already has this story (posted, queued, dry run, or an event released for both platforms) in the last 48 h."""
    since = now - SAME_STORY_WINDOW
    posts = db.scalars(select(NdPost).where(NdPost.created_at >= since, NdPost.status.notin_(("rejected", "expired", "failed"))))
    for p in posts:
        theirs = [str((p.facts or {}).get(k) or "") for k in ("title", "headline")] + [p.text or ""]
        if any(same_story(t, o) for t in titles for o in theirs if o):
            return f"covered by the News Desk (post {p.id}, {p.status})"
    evs = db.scalars(select(NdEvent).where(NdEvent.created_at >= since, NdEvent.status.in_(("released", "candidate"))))
    for e in evs:
        if any(same_story(t, o) for t in titles for o in (e.title, e.headline) if o):
            return f"covered by the News Desk (event {e.id}, {e.status})"
    return None


def trend_covered(db: Session, titles: list[str], now: datetime) -> TtPost | None:
    """For the News Desk: a trend post on the same story went out (or was recorded in dry run) in the last 48 h."""
    rows = db.scalars(select(TtPost).where(TtPost.published_at >= now - SAME_STORY_WINDOW, TtPost.status.in_(SENT_LIVE + ("dry_run",))))
    for p in rows:
        theirs = [str(x.get("title") or "") for x in p.sources or []] + [p.text]
        if any(same_story(t, o) for t in titles if t for o in theirs if o):
            return p
    return None


def desk_blocked(db: Session, now: datetime, s: TtSettings | None = None) -> str | None:
    """Why no trend post may go out now: Trending's own stop, the News Desk's emergency stop, or any News Desk pause (the network's
    emergency pause, X refusing the desk)."""
    if s is not None and s.kill_switch:
        return "Trending Topics' stop is on"
    nd = db.get(NdSettings, 1)
    if nd is None:
        return None
    if nd.kill_switch:
        return "the News Desk's emergency stop is on"
    until = _aware(nd.paused_until, now)
    if until is not None and until > now:
        return f"the News Desk is paused: {nd.pause_reason or 'until ' + until.strftime('%d %b %H:%M UTC')}"
    return None


def x_posts_today(db: Session, s: TtSettings, now: datetime) -> dict[str, int]:
    since = now - WINDOW
    statuses = _sent_statuses(db, s)
    nd = int(db.scalar(select(func.count()).select_from(NdPost).where(NdPost.published_at >= since, NdPost.status.in_(statuses))) or 0)
    tt = int(db.scalar(select(func.count()).select_from(TtPost).where(TtPost.published_at >= since, TtPost.status.in_(statuses))) or 0)
    return {"newsdesk": nd, "trending": tt, "total": nd + tt}


def cap_problem(db: Session, s: TtSettings, now: datetime) -> str | None:
    n = x_posts_today(db, s, now)
    if n["trending"] >= s.max_per_day:
        return f"Trending's daily limit reached ({n['trending']}/{s.max_per_day} in 24 h)"
    if n["total"] >= s.shared_daily_cap:
        return f"the shared X cap is reached ({n['total']}/{s.shared_daily_cap} News Desk + Trending posts in 24 h)"
    return None


def next_slot(db: Session, now: datetime) -> datetime | None:
    from app.newsdesk import events

    nd = db.get(NdSettings, 1)
    try:
        times = events.slot_times(now, events.policy((nd.state if nd else None) or {}), back_hours=2)
    except Exception:  # noqa: BLE001 - the News Desk's plan is advisory here
        return None
    return next((t for t in times if t > now), None)


def spacing_problem(db: Session, s: TtSettings, now: datetime) -> str | None:
    gap = timedelta(minutes=s.newsdesk_gap_minutes)
    statuses = _sent_statuses(db, s)
    last_tt = _aware(db.scalar(select(func.max(TtPost.published_at)).where(TtPost.status.in_(statuses))), now)
    if last_tt is not None and now - last_tt < timedelta(minutes=s.gap_minutes):
        return f"spacing: the last trend post was {int((now - last_tt).total_seconds() // 60)} min ago (gap {s.gap_minutes} min)"
    last_nd = _aware(db.scalar(select(func.max(NdPost.published_at)).where(NdPost.status.in_(statuses))), now)
    if last_nd is not None and now - last_nd < gap:
        return f"spacing: the News Desk posted {int((now - last_nd).total_seconds() // 60)} min ago"
    last_ev = _aware(db.scalar(select(func.max(NdEvent.released_at)).where(NdEvent.status == "released")), now)
    if last_ev is not None and now - last_ev < gap:
        return f"spacing: the News Desk released an event {int((now - last_ev).total_seconds() // 60)} min ago"
    from app.newsdesk import events

    nd = db.get(NdSettings, 1)
    try:
        times = events.slot_times(now, events.policy((nd.state if nd else None) or {}), back_hours=2)
    except Exception:  # noqa: BLE001
        times = []
    near = [t for t in times if abs((t - now).total_seconds()) < gap.total_seconds()]
    if near:
        return f"spacing: a News Desk slot is at {near[0]:%H:%M} UTC"
    return None


def summary(db: Session, s: TtSettings, now: datetime) -> dict[str, Any]:
    nxt = next_slot(db, now)
    return {"posts_24h": x_posts_today(db, s, now), "shared_cap": s.shared_daily_cap, "next_newsdesk_slot": nxt.isoformat() if nxt else None,
            "desk_blocked": desk_blocked(db, now, s), "newsdesk_x_mode": "live" if newsdesk_live(db) else "dry_run", "x_live": x_live(db, s), "spacing": spacing_problem(db, s, now), "cap": cap_problem(db, s, now)}
