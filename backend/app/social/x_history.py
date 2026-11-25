"""X performance history: a metrics curve for every Price Chat X post, and an hourly follower count.

What it does (read-only; nothing here can write to X):
- Every Price Chat X post (News Desk `nd_posts`, Trending Topics `tt_posts`, and any other own post in `social_posts`) gets a snapshot
  at ~1 h, 6 h, 24 h, 72 h and 7 d after it was published. Each snapshot is one `social_post_metrics` row (source "curve_<cp>", plus
  "_pub" when X returned public metrics only), linked to the post's `social_posts` row (created, by x_id, if the desk post had none).
- The due posts of a run are read together: GET /2/tweets with up to 100 ids per request (owned reads, $0.001 a post returned; X
  does not charge twice for the same post on the same UTC day). A checkpoint that was missed (the job was off) is skipped: the newest
  one that is due is taken, never an older one late.
- First run: one backfill read of every own post of the last PRIVATE_DAYS days that has no snapshot yet (source "curve_backfill").
  X only gives non-public metrics (impressions, profile/url clicks) for posts younger than 30 days, so older posts are never asked.
- nd_posts.metrics / tt_posts.metrics keep getting the latest raw values (what Admin and the Viral Engine already read).
- The own account's followers / following / post count go into growth_snapshots once an hour (one user read), unless a free
  snapshot (our user object in another X response) was taken in the last 55 minutes.

Spending: every request is checked against (1) this collector's own daily cap (X_PERF_HISTORY_DAILY_USD), (2) the posting reserve
(terminal/xintel.protect_posting: the day's and month's X budget must still hold what posting needs), and (3) the account's X budget
(social/budget.PlatformBudget, inside the client). A refused read costs nothing and is tried again on the next run.
Off switch: X_PERF_HISTORY_ENABLED=false.

State (backfill done, cost per day, posts X no longer returns) lives in the X connector state's usage JSON under "_x_history"
(keys starting with "_" are not counted as spend by PlatformBudget): no new table or column.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.settings import Settings
from app.connectors.state import get_connector_state
from app.connectors.x.client import PRICE_OWNED_READ, PRICE_USER_READ
from app.connectors.x.errors import XBudgetExceededError, XError, XNotFoundError, XRateLimitError
from app.connectors.x.normalize import metrics as to_metrics
from app.db.base import utcnow
from app.models.enums import ContentStatus, Platform
from app.models.newsdesk import NdPost
from app.models.social import SocialPost
from app.models.social_intel import SocialPostMetric
from app.models.trending import TtPost

logger = logging.getLogger(__name__)

# (hours after publishing, label). The newest due one is taken; the curve ends after the last one plus CURVE_GRACE.
CHECKPOINTS: tuple[tuple[int, str], ...] = ((1, "1h"), (6, "6h"), (24, "24h"), (72, "72h"), (168, "7d"))
CURVE_GRACE = timedelta(hours=24)
PRIVATE_DAYS = 29  # backfill window: X's non-public metrics exist for 30 days; a day of margin
BATCH = 100  # ids per GET /2/tweets
MAX_MISSES = 3  # a post X stops returning (deleted) is asked for at most this many times
STATE_KEY = "_x_history"
SOURCE_PREFIX = "curve_"
FOLLOWER_GAP = timedelta(minutes=55)
KEEP_DAYS = 35


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _day(now: datetime) -> str:
    return now.astimezone(timezone.utc).strftime("%Y-%m-%d")


# --- state ---------------------------------------------------------------------------------------------------------------------------


def _hist(db: Session) -> dict[str, Any]:
    return dict((get_connector_state(db, Platform.X).usage or {}).get(STATE_KEY) or {})


def _save(db: Session, h: dict[str, Any]) -> None:
    state = get_connector_state(db, Platform.X)
    state.usage = {**(state.usage or {}), STATE_KEY: h}  # reassigned (not mutated) so the JSON change is saved


def spent_today(h: dict[str, Any], now: datetime) -> float:
    return float(((h.get("days") or {}).get(_day(now)) or {}).get("usd") or 0.0)


def _add_spend(h: dict[str, Any], now: datetime, usd: float, posts: int = 0, users: int = 0, requests: int = 1) -> None:
    days = dict(h.get("days") or {})
    d = dict(days.get(_day(now)) or {"usd": 0.0, "posts": 0, "users": 0, "requests": 0})
    d["usd"] = round(float(d.get("usd") or 0) + usd, 4)
    d["posts"] = int(d.get("posts") or 0) + posts
    d["users"] = int(d.get("users") or 0) + users
    d["requests"] = int(d.get("requests") or 0) + requests
    days[_day(now)] = d
    h["days"] = {k: days[k] for k in sorted(days)[-KEEP_DAYS:]}


def refusal(db: Session, settings: Settings, h: dict[str, Any], now: datetime, cost: float) -> str | None:
    """Why a read of `cost` must not happen now, or None. Never spends into the posting reserve."""
    from app.terminal.xintel import protect_posting

    cap = float(settings.x_perf_history_daily_usd)
    if spent_today(h, now) + cost > cap + 1e-9:
        return f"X performance history's own cap of ${cap:.2f} a day is reached"
    why = protect_posting(db, settings, now, extra=cost)
    if why:
        return f"posting reserve: {why}"
    try:
        from app.social.budget import PlatformBudget

        PlatformBudget(db, settings).check(cost)
    except XBudgetExceededError as e:
        return str(e)
    return None


# --- which posts ---------------------------------------------------------------------------------------------------------------------


class _Own:
    """One Price Chat X post, whichever lane wrote it."""

    __slots__ = ("x_id", "published_at", "text", "lane", "nd", "tt", "social")

    def __init__(self, x_id: str, published_at: datetime, text: str, lane: str) -> None:
        self.x_id, self.published_at, self.text, self.lane = x_id, published_at, text, lane
        self.nd: NdPost | None = None
        self.tt: TtPost | None = None
        self.social: SocialPost | None = None


def own_posts(db: Session, since: datetime) -> dict[str, _Own]:
    """Every published Price Chat X post since `since`, by x_id (News Desk, Trending, then any other own post)."""
    out: dict[str, _Own] = {}
    for p in db.scalars(select(NdPost).where(NdPost.status == "published", NdPost.x_id.is_not(None), NdPost.published_at >= since)):
        o = out.setdefault(str(p.x_id), _Own(str(p.x_id), _aware(p.published_at), p.text or "", "newsdesk"))  # type: ignore[arg-type]
        o.nd = p
    for t in db.scalars(select(TtPost).where(TtPost.status == "published", TtPost.x_id.is_not(None), TtPost.published_at >= since)):
        o = out.setdefault(str(t.x_id), _Own(str(t.x_id), _aware(t.published_at), t.text or "", "trending"))  # type: ignore[arg-type]
        o.tt = t
    for s in db.scalars(select(SocialPost).where(SocialPost.platform == Platform.X, SocialPost.is_own.is_(True),
                                                 SocialPost.external_id.is_not(None), SocialPost.posted_at >= since)):
        if any((r or {}).get("type") == "retweeted" for r in s.referenced or []):
            continue  # a repost's numbers are the original post's, not ours
        o = out.setdefault(str(s.external_id), _Own(str(s.external_id), _aware(s.posted_at), s.text or "", "own"))  # type: ignore[arg-type]
        o.social = s
    return out


def _link(db: Session, o: _Own) -> SocialPost:
    """The post's social_posts row (by x_id), created for a desk post that never came through X reading."""
    if o.social is None:
        o.social = db.scalar(select(SocialPost).where(SocialPost.platform == Platform.X, SocialPost.external_id == o.x_id))
    if o.social is None:
        o.social = SocialPost(platform=Platform.X, external_id=o.x_id, text=o.text, url=f"https://x.com/i/status/{o.x_id}", is_own=True,
                              status=ContentStatus.PUBLISHED, posted_at=o.published_at, referenced=[], source_kinds=[o.lane], rule_ids=[],
                              metrics={})
        db.add(o.social)
        db.flush()
    else:
        o.social.is_own = True
        if o.social.posted_at is None:
            o.social.posted_at = o.published_at
        if o.lane not in (o.social.source_kinds or []):
            o.social.source_kinds = [*(o.social.source_kinds or []), o.lane]
    return o.social


def _taken(db: Session, social_ids: list[int]) -> dict[int, set[str]]:
    out: dict[int, set[str]] = {}
    for start in range(0, len(social_ids), 500):
        chunk = social_ids[start:start + 500]
        for pid, src in db.execute(select(SocialPostMetric.post_id, SocialPostMetric.source).where(
                SocialPostMetric.post_id.in_(chunk), SocialPostMetric.source.like(f"{SOURCE_PREFIX}%"))):
            out.setdefault(pid, set()).add(_label_of(src))
    return out


def _label_of(source: str) -> str:
    s = source[len(SOURCE_PREFIX):] if source.startswith(SOURCE_PREFIX) else source
    return s[:-4] if s.endswith("_pub") else s


def due_label(published_at: datetime, now: datetime, taken: set[str]) -> str | None:
    """The checkpoint to take now: the newest one the post has reached, if not taken yet. None after the curve ends."""
    age = now - published_at
    if age < timedelta(0) or age > timedelta(hours=CHECKPOINTS[-1][0]) + CURVE_GRACE:
        return None
    label = None
    for hours, name in CHECKPOINTS:
        if age >= timedelta(hours=hours):
            label = name
    return label if label is not None and label not in taken else None


def plan(db: Session, now: datetime, h: dict[str, Any]) -> list[tuple[_Own, str]]:
    """(post, label) to read this run: due checkpoints first (oldest post first), then the one-time backfill."""
    gone = set(h.get("gone") or [])
    window = timedelta(hours=CHECKPOINTS[-1][0]) + CURVE_GRACE
    since = now - (timedelta(days=PRIVATE_DAYS) if not h.get("backfill_done") else window)
    posts = [o for o in own_posts(db, since).values() if o.x_id not in gone and o.published_at is not None]
    for o in posts:
        _link(db, o)
    taken = _taken(db, [o.social.id for o in posts if o.social is not None])
    due: list[tuple[_Own, str]] = []
    backfill: list[tuple[_Own, str]] = []
    for o in sorted(posts, key=lambda o: o.published_at):
        t = taken.get(o.social.id, set()) if o.social is not None else set()
        label = due_label(o.published_at, now, t)
        if label:
            due.append((o, label))
        elif not h.get("backfill_done") and not t and now - o.published_at >= timedelta(hours=CHECKPOINTS[0][0]):
            backfill.append((o, "backfill"))
    return due + backfill


# --- recording -----------------------------------------------------------------------------------------------------------------------


def _record(db: Session, o: _Own, label: str, tweet: dict[str, Any], media: list[dict[str, Any]], now: datetime) -> None:
    m = to_metrics(tweet)
    row = o.social or _link(db, o)
    row.metrics = {**(row.metrics or {}), **m.as_dict()}
    row.metrics_updated_at = now
    if media and not row.media:
        row.media = media
    db.add(SocialPostMetric(post_id=row.id, captured_at=now, source=f"{SOURCE_PREFIX}{label}" + ("" if m.source == "non_public" else "_pub"),
                            impressions=m.impressions, views=m.views, likes=m.likes, replies=m.replies, reposts=m.reposts, quotes=m.quotes,
                            bookmarks=m.bookmarks, profile_visits=m.profile_visits, url_clicks=m.url_clicks))
    raw = {**(tweet.get("public_metrics") or {}), **(tweet.get("non_public_metrics") or {}), **(tweet.get("organic_metrics") or {})}
    if o.nd is not None:  # what newsdesk.metrics used to write (Admin, the Viral Engine): the latest raw values
        o.nd.metrics = {**raw, "read_at": now.isoformat()}
    if o.tt is not None:
        o.tt.metrics = raw
        o.tt.metrics_at = now


def _media(body: dict[str, Any], tweet: dict[str, Any]) -> list[dict[str, Any]]:
    from app.connectors.x.normalize import _media as norm_media

    """The post's photos / video previews on X's own image host, as social_posts.media stores them (the Kiwi Passport shows them)."""
    media = {str(m["media_key"]): m for m in (body.get("includes") or {}).get("media", []) if "media_key" in m}
    return norm_media(tweet, media)


async def collect(db: Session, settings: Settings, runtime: Any, now: datetime | None = None, client: Any = None) -> dict[str, Any]:
    """One run of the due-checkpoint job. Returns what it read, what it spent and why it stopped (if it did)."""
    from app.security import credentials

    now = _aware(now) or utcnow()
    if not settings.x_perf_history_enabled:
        return {"skipped": "X_PERF_HISTORY_ENABLED is off"}
    if not credentials.x_credentials(settings).user_context:
        return {"skipped": "X account keys (user context) are needed for owned post metrics"}
    state = get_connector_state(db, Platform.X)
    if state.paused:
        return {"skipped": "X reads are paused"}
    if state.blocked_until and _aware(state.blocked_until) > now:  # type: ignore[operator]
        return {"skipped": f"X rate limit / usage cap: waiting until {_aware(state.blocked_until):%H:%M} UTC"}
    h = _hist(db)
    todo = plan(db, now, h)
    db.commit()
    out: dict[str, Any] = {"due": sum(1 for _, lbl in todo if lbl != "backfill"), "backfill": sum(1 for _, lbl in todo if lbl == "backfill"),
                           "requests": 0, "recorded": 0, "usd": 0.0, "stopped": None}
    if not todo:
        if not h.get("backfill_done"):
            h["backfill_done"] = now.isoformat()
        h["last_run"] = {"at": now.isoformat(), **out}
        _save(db, h)
        db.commit()
        return out
    own_client = client is None
    if own_client:
        from app.social.x_service import make_x_client

        client = make_x_client(db, settings, runtime)
    misses = dict(h.get("misses") or {})
    done_backfill = True
    try:
        for start in range(0, len(todo), BATCH):
            chunk = todo[start:start + BATCH]
            est = round(PRICE_OWNED_READ * len(chunk), 4)
            why = refusal(db, settings, h, now, est)
            if why:
                out["stopped"] = why
                done_backfill = False
                break
            ids = [o.x_id for o, _ in chunk]
            db.commit()  # nothing waits on X with a write transaction open
            try:
                body = await client.own_post_metrics(ids, media=True)
            except XNotFoundError:  # every post of the batch is gone (deleted): counted as misses, the run goes on
                body = {"data": []}
            except XRateLimitError as e:
                state = get_connector_state(db, Platform.X)
                state.blocked_until = e.reset_at if e.reset_at and e.reset_at > now else now + timedelta(minutes=15)
                out["stopped"] = f"X rate limit: {e}"
                done_backfill = False
                break
            except XError as e:
                out["stopped"] = f"{type(e).__name__}: {str(e)[:200]}"
                done_backfill = False
                break
            out["requests"] += 1
            got = {str(t.get("id")): t for t in (body.get("data") or []) if isinstance(t, dict)}
            cost = round(PRICE_OWNED_READ * len(got), 4)
            _add_spend(h, now, cost, posts=len(got))
            out["usd"] = round(out["usd"] + cost, 4)
            for o, label in chunk:
                t = got.get(o.x_id)
                if t is None:
                    misses[o.x_id] = int(misses.get(o.x_id) or 0) + 1
                    continue
                misses.pop(o.x_id, None)
                _record(db, o, label, t, _media(body, t), now)
                out["recorded"] += 1
            db.commit()
    finally:
        if own_client:
            await client.aclose()
    gone = list(h.get("gone") or [])
    for x_id, n in list(misses.items()):
        if n >= MAX_MISSES:
            gone.append(x_id)
            misses.pop(x_id)
    h["gone"], h["misses"] = gone[-500:], misses
    if done_backfill and not h.get("backfill_done"):
        h["backfill_done"] = now.isoformat()
    h["last_run"] = {"at": now.isoformat(), **out}
    _save(db, h)
    db.commit()
    if out["stopped"]:
        logger.info("x_history: stopped early: %s", out["stopped"])
    return out


# --- followers -----------------------------------------------------------------------------------------------------------------------


async def snapshot_followers(db: Session, settings: Settings, runtime: Any, now: datetime | None = None, client: Any = None) -> dict[str, Any]:
    """Own account's followers / following / post count into growth_snapshots, at most once an hour (one user read)."""
    from app.analytics.snapshots import last_snapshot, record_x_account
    from app.security import credentials

    now = _aware(now) or utcnow()
    if not settings.x_perf_history_enabled:
        return {"skipped": "X_PERF_HISTORY_ENABLED is off"}
    creds = credentials.x_credentials(settings)
    state = get_connector_state(db, Platform.X)
    if not creds.configured or not (creds.user_context or state.account_username):
        return {"skipped": "X is not configured"}
    if state.paused:
        return {"skipped": "X reads are paused"}
    if state.blocked_until and _aware(state.blocked_until) > now:  # type: ignore[operator]
        return {"skipped": "X rate limit / usage cap"}
    if state.account_external_id:
        last = last_snapshot(db, "x_account", state.account_external_id)
        if last is not None and now - _aware(last.taken_at) < FOLLOWER_GAP:  # type: ignore[operator]
            return {"skipped": "a snapshot was taken in the last hour"}
    h = _hist(db)
    why = refusal(db, settings, h, now, PRICE_USER_READ)
    if why:
        h["followers_last"] = {"at": now.isoformat(), "stopped": why}
        _save(db, h)
        db.commit()
        return {"stopped": why}
    own_client = client is None
    if own_client:
        from app.social.x_service import make_x_client

        client = make_x_client(db, settings, runtime)
    db.commit()
    try:
        user = await (client.me() if creds.user_context else client.user_by_username(state.account_username))
    except XError as e:
        return {"stopped": f"{type(e).__name__}: {str(e)[:200]}"}
    finally:
        if own_client:
            await client.aclose()
    snap = record_x_account(db, user, "hourly", at=now)
    h = _hist(db)
    _add_spend(h, now, PRICE_USER_READ, users=1)
    h["followers_last"] = {"at": now.isoformat(), "followers": (user.get("public_metrics") or {}).get("followers_count")}
    _save(db, h)
    db.commit()
    return {"recorded": snap is not None, "followers": (user.get("public_metrics") or {}).get("followers_count"), "usd": PRICE_USER_READ}


# --- read API for learning -----------------------------------------------------------------------------------------------------------

METRIC_FIELDS = ("impressions", "likes", "replies", "reposts", "quotes", "bookmarks", "profile_visits", "url_clicks")


def _features(db: Session, o: _Own, curve_read: bool = False) -> dict[str, Any]:
    """curve_read: this collector has read the post (it asks X for the attached media), so no media there means no picture."""
    text = o.text or ""
    pub = o.published_at
    f: dict[str, Any] = {"lane": o.lane, "kind": "", "category": "", "priority": "", "angle": "", "hook": "", "format": "", "question": None,
                         "links": "https://" in text, "exploring": None, "style": "", "writer": "", "length": len(text),
                         "hour": pub.hour if pub else None, "weekday": pub.weekday() if pub else None, "has_image": None}
    if o.nd is not None:
        facts = o.nd.facts or {}
        v = facts.get("viral") or {}
        f.update(kind=o.nd.kind, category=o.nd.category, priority=o.nd.priority, hook=str(v.get("hook") or ""), format=str(v.get("format") or ""),
                 question=v.get("question"), exploring=v.get("exploring"), writer=str(v.get("writer") or ""))
        if "links" in v:
            f["links"] = bool(v.get("links"))
        ev_id = facts.get("event_id")
        if ev_id:
            from app.models.newsdesk import NdEvent

            ev = db.get(NdEvent, ev_id)
            if ev is not None:
                f["style"] = str(((ev.facts or {}).get("visual") or {}).get("style") or "")
    if o.tt is not None:
        f.update(kind="trend", category=o.tt.category, angle=o.tt.angle or "", writer=o.tt.writer or "")
    if o.social is not None and o.social.media:
        f["has_image"] = True
    elif curve_read:
        f["has_image"] = False
    if f["has_image"] is None and o.nd is not None:
        from app.models.newsdesk import NdValidation

        if db.scalar(select(NdValidation.id).where(NdValidation.kind == "image", NdValidation.ref == f"x:{o.x_id}")) is not None:
            f["has_image"] = True
    return f


def curves(db: Session, days: int = 30, lane: str | None = None, now: datetime | None = None) -> list[dict[str, Any]]:
    """Every Price Chat X post of the last `days` with its metrics curve and the features known today, newest first:
    {x_id, url, published_at, text, features: {...}, points: [{checkpoint, age_hours, captured_at, impressions, ...,
    private: bool}], latest: {...}}. Points are every snapshot of the post (curve checkpoints and any other read), oldest first."""
    now = _aware(now) or utcnow()
    posts = [o for o in own_posts(db, now - timedelta(days=days)).values() if lane is None or o.lane == lane]
    for o in posts:
        if o.social is None:
            o.social = db.scalar(select(SocialPost).where(SocialPost.platform == Platform.X, SocialPost.external_id == o.x_id))
    ids = [o.social.id for o in posts if o.social is not None]
    rows: dict[int, list[SocialPostMetric]] = {}
    for start in range(0, len(ids), 500):
        for r in db.scalars(select(SocialPostMetric).where(SocialPostMetric.post_id.in_(ids[start:start + 500])).order_by(SocialPostMetric.captured_at)):
            rows.setdefault(r.post_id, []).append(r)
    out = []
    for o in sorted(posts, key=lambda o: o.published_at, reverse=True):
        pts = []
        for r in rows.get(o.social.id, []) if o.social is not None else []:
            at = _aware(r.captured_at)
            pts.append({"checkpoint": _label_of(r.source) if r.source.startswith(SOURCE_PREFIX) else None, "source": r.source,
                        "age_hours": round((at - o.published_at).total_seconds() / 3600, 2) if at else None,  # type: ignore[operator]
                        "captured_at": at.isoformat() if at else None, "private": not r.source.endswith("_pub") and r.source != "public",
                        **{k: getattr(r, k) for k in METRIC_FIELDS}})
        out.append({"x_id": o.x_id, "url": f"https://x.com/i/status/{o.x_id}", "published_at": o.published_at.isoformat(), "text": o.text,
                    "features": _features(db, o, any(p["checkpoint"] for p in pts)), "points": pts, "latest": dict(o.social.metrics or {}) if o.social is not None else {}})
    return out


def status(db: Session, settings: Settings, now: datetime | None = None) -> dict[str, Any]:
    """For Admin / reports: is it on, what it spent today, last run, backfill state."""
    now = _aware(now) or utcnow()
    h = _hist(db)
    return {"enabled": bool(settings.x_perf_history_enabled), "daily_cap_usd": float(settings.x_perf_history_daily_usd),
            "spent_today_usd": spent_today(h, now), "backfill_done": h.get("backfill_done"), "last_run": h.get("last_run"),
            "followers_last": h.get("followers_last"), "days": h.get("days") or {}}
