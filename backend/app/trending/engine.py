"""One Trending Topics tick (the newsdesk.trending job, every job_trending_seconds): read the sources that are due, store their signals,
group and score topics, write a tweet for the hot ones, and publish what is due.

Defaults: Approval mode (every tweet waits for the owner), automatic publishing off, dry run (X is never called). Automatic needs the
owner's explicit confirmation in Admin; Live is a separate switch. Every post passes app/trending/gates and app/trending/coordinate.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.config.settings import Settings
from app.db.base import utcnow
from app.models.trending import TtPost, TtSettings, TtSignal, TtTopic
from app.newsdesk import checks
from app.trending import coordinate, gates, learning, rank, sources, writer

logger = logging.getLogger(__name__)

KEEP_SIGNALS = timedelta(days=3)
TOPIC_WINDOW = timedelta(hours=24)  # signals seen in the last day make the topics
PENDING_TTL = timedelta(hours=6)  # a suggestion nobody approved goes stale
QUEUED_TTL = timedelta(hours=4)
MAX_NEW_PER_SCAN = 5
ALERT_AFTER = 3  # failed reads in a row before the owner hears about a source
MAX_ATTEMPTS = 3
OPEN = ("pending", "queued", "publishing")
SENT = ("published", "publishing", "uncertain", "dry_run")
HTTP_TRANSPORT: Any = None  # tests install a mocked transport here; production reads the real feeds


def _aware(d: datetime | None) -> datetime | None:
    return None if d is None else (d if d.tzinfo else d.replace(tzinfo=timezone.utc))


# --- settings ------------------------------------------------------------------------------------------------------------


def settings_row(db: Session) -> TtSettings:
    row = db.get(TtSettings, 1)
    if row is None:
        # owner, 2026-11-14: automatic on, at most 3 a day, 3 hours apart, posting live only while the News Desk is live
        row = TtSettings(id=1, enabled=True, mode="automatic", auto_confirmed_at=utcnow(), live=True, kill_switch=False, max_per_day=3, gap_minutes=180,
                         categories={c: True for c in rank.CATEGORIES},
                         sources={k: dict(v) for k, v in sources.DEFAULT_SOURCES.items()}, state={})
        db.add(row)
        db.flush()
    return row


def automatic(s: TtSettings) -> bool:
    """Automatic publishing is on only when the owner chose it AND confirmed it."""
    return s.mode == "automatic" and s.auto_confirmed_at is not None


# --- read the sources ----------------------------------------------------------------------------------------------------


def _due(name: str, state: dict[str, Any], now: datetime) -> bool:
    h = ((state.get("health") or {}).get(name) or {})
    last = h.get("last_scan")
    if not last:
        return True
    wait = sources.INTERVAL.get(name, 30)
    if h.get("plan"):  # not on this X plan: ask again once a day
        wait = 24 * 60
    try:
        return now - _aware(datetime.fromisoformat(last)) >= timedelta(minutes=wait)  # type: ignore[operator]
    except ValueError:
        return True


async def read_sources(db: Session, s: TtSettings, settings: Settings, runtime: Any, now: datetime, client: httpx.AsyncClient,
                       only: set[str] | None = None) -> dict[str, Any]:
    opts = sources.merged(s.sources)
    state = dict(s.state or {})
    health = dict(state.get("health") or {})
    usage = dict(state.get("usage") or {})
    if usage.get("date") != now.strftime("%Y-%m-%d"):
        usage = {"date": now.strftime("%Y-%m-%d"), "requests": {}, "cost_usd": {}}
    from app.models.newsdesk import NdSettings

    nd = db.get(NdSettings, 1)
    feed_outlets = {str(f.get("name") or "").lower() for f in ((nd.feeds if nd else None) or [])} | {str(f["name"]).lower() for f in _desk_feeds()}
    stored: dict[str, int] = {}
    for name in sources.DEFAULT_SOURCES:
        o = opts[name]
        if only is not None and name not in only:
            continue
        if not o.get("on"):
            health[name] = {**(health.get(name) or {}), "name": sources.NAMES[name], "status": "off", "problem": None}
            continue
        if only is None and not _due(name, state, now):
            continue
        try:
            if name == "news":
                items, problem, meta = sources.news(db, now)
            elif name == "google":
                items, problem, meta = await sources.google(client, o, feed_outlets)
            elif name == "reddit":
                items, problem, meta = await sources.reddit(client, o, settings)
            else:
                items, problem, meta = await sources.x_source(name, db, settings, runtime, o)
        except Exception as e:  # noqa: BLE001 - one source's bug never stops the others
            logger.exception("trending: %s failed", name)
            items, problem, meta = [], f"{type(e).__name__}: {str(e)[:150]}", {}
        prev = health.get(name) or {}
        unavailable = bool(meta.get("unavailable"))
        idle = bool(meta.get("idle"))
        failing = bool(problem) and not unavailable and not idle
        stored[name] = _store(db, items, now)
        usage["requests"][name] = int(usage["requests"].get(name, 0)) + int(meta.get("requests") or 0)
        if meta.get("cost_usd"):
            usage["cost_usd"][name] = round(float(usage["cost_usd"].get(name, 0)) + float(meta["cost_usd"]), 4)
        health[name] = {"name": sources.NAMES[name], "last_scan": now.isoformat(), "items": len(items), "stored": stored[name],
                        "status": "unavailable" if unavailable else "error" if failing else "idle" if idle else "ok", "problem": problem, "partial": meta.get("partial") or "",
                        "fails": int(prev.get("fails") or 0) + 1 if failing else 0, "last_ok": now.isoformat() if not problem else prev.get("last_ok"),
                        "plan": bool(meta.get("plan")), "auth": meta.get("auth"), "rate_limits": meta.get("rate_limits") or {}}
    state["health"], state["usage"] = health, usage
    s.state = state
    db.commit()
    return {"stored": stored, "health": health}


def _desk_feeds() -> list[dict[str, Any]]:
    from app.newsdesk import sources as nd_sources

    return list(nd_sources.DEFAULT_FEEDS) + list(nd_sources.EXTRA_FEEDS)


def _store(db: Session, items: list[dict[str, Any]], now: datetime) -> int:
    """Insert new signals, refresh known ones (engagement history, last seen). Returns how many were new."""
    new = 0
    if not items:
        return 0
    keys = {(i["source"], i["external_id"]) for i in items}
    have = {(r.source, r.external_id): r for r in db.scalars(select(TtSignal).where(TtSignal.external_id.in_([k[1] for k in keys])))}
    for it in items:
        k = (it["source"], it["external_id"])
        row = have.get(k)
        point = [now.isoformat(), float(it.get("engagement") or 0)]
        if row is None:
            cat, _ = rank.classify(it["title"], it.get("summary") or "", it.get("category_hint"))
            row = TtSignal(source=it["source"], external_id=it["external_id"], title=it["title"][:300], summary=(it.get("summary") or "")[:2000],
                           url=(it.get("url") or "")[:500], outlet=(it.get("outlet") or "")[:80], reliable=bool(it.get("reliable")), category=cat,
                           engagement=point[1], history=[point], extra=it.get("extra") or {}, published_at=it.get("published_at"), first_seen=now, last_seen=now)
            db.add(row)
            have[k] = row
            new += 1
        else:
            row.engagement, row.last_seen = point[1], now
            row.history = (list(row.history or []) + [point])[-12:]
            if it.get("extra"):
                row.extra = {**(row.extra or {}), **it["extra"]}
    db.flush()
    return new


# --- topics ---------------------------------------------------------------------------------------------------------------


def build_topics(db: Session, s: TtSettings, now: datetime) -> list[TtTopic]:
    """Cluster the last day's signals, score them, keep each cluster on the same topic row across scans (shared signals)."""
    rows = list(db.scalars(select(TtSignal).where(TtSignal.last_seen >= now - TOPIC_WINDOW)))
    sigs = [rank.Sig.of(r) for r in rows]
    clusters = rank.cluster(sigs)
    weights = learning.weights(db, now)
    state = dict(s.state or {})
    state["weights"] = weights
    s.state = state
    enabled = s.categories or {}
    existing = list(db.scalars(select(TtTopic).where(TtTopic.last_seen >= now - timedelta(hours=48))))
    owner: dict[int, TtTopic] = {}
    for t in existing:
        for sid in t.signal_ids or []:
            owner.setdefault(int(sid), t)
    touched: list[TtTopic] = []
    for c in clusters:
        if not enabled.get(c.category, True):
            continue
        sc, parts = rank.score(c, now, weights)
        ids = sorted(x.id for x in c.signals)
        topic = next((owner[i] for i in ids if i in owner and owner[i] not in touched), None)
        lead = c.lead
        srcs = [{"source": x.source, "outlet": x.outlet, "title": x.title, "url": x.url, "reliable": x.reliable, "engagement": x.engagement,
                 "at": (x.published_at or x.first_seen).isoformat(), "extra": {k: v for k, v in x.extra.items() if k in ("approx_traffic", "comments", "score", "query", "trend", "geo")}}
                for x in sorted(c.signals, key=lambda x: (not x.reliable, -x.engagement))[:12]]
        metrics: dict[str, Any] = {"signals": len(c.signals)}
        for x in c.signals:
            metrics[x.source] = round(float(metrics.get(x.source, 0)) + x.engagement, 1)
        metrics["reddit_comments"] = int(sum(float(x.extra.get("comments") or 0) for x in c.signals))
        if topic is None:
            topic = TtTopic(key=f"t:{ids[0]}:{int(now.timestamp())}", title=lead.title[:300], category=c.category, status="active", score=sc, peak_score=sc,
                            parts=parts, signal_ids=ids, sources=srcs, metrics=metrics, first_seen=min(x.first_seen for x in c.signals), last_seen=now)
            db.add(topic)
        else:
            topic.title, topic.category, topic.score, topic.parts = lead.title[:300], c.category, sc, parts
            topic.peak_score = max(topic.peak_score or 0, sc)
            topic.signal_ids = sorted(set(topic.signal_ids or []) | set(ids))[-200:]
            topic.sources, topic.metrics, topic.last_seen = srcs, metrics, now
            if topic.status == "cooling":
                topic.status = "active"
        touched.append(topic)
    for t in existing:
        if t not in touched and t.status == "active":
            t.status, t.score = "cooling", round((t.score or 0) * 0.7, 1)
    db.flush()
    return touched


def brief_of(topic: TtTopic) -> tuple[writer.Brief | None, list[dict[str, Any]]]:
    """The writer's facts, from the topic's reliable items only. None when no reliable item backs it."""
    srcs = topic.sources or []
    reliable = [x for x in srcs if x.get("reliable")]
    google = next((x for x in srcs if x.get("source") == "google" and (x.get("extra") or {}).get("query")), None)
    reddit = max((x for x in srcs if x.get("source") == "reddit"), key=lambda x: x.get("engagement") or 0, default=None)
    x_trend = any(x.get("source") == "x_trends" for x in srcs)
    outlets = list(dict.fromkeys(x["outlet"] for x in reliable))
    if not reliable:
        return None, []
    lead = reliable[0]
    measured = []
    if google:
        measured.append("on Google's trending searches")
    if reddit:
        measured.append(f"a top thread on {reddit['outlet']}")
    if len(outlets) >= 3:
        measured.append(f"{len(outlets)} outlets")
    if x_trend:
        measured.append("trending on X")
    source_text = " ".join(f"{x['title']}" for x in reliable) + " " + " ".join(measured)
    return writer.Brief(key=topic.key, category=topic.category, headline=lead["title"], outlet=lead["outlet"], google=bool(google),
                        google_traffic=str(((google or {}).get("extra") or {}).get("approx_traffic") or ""), reddit_sub=(reddit or {}).get("outlet", "")[2:] if reddit else "",
                        x_trend=x_trend, outlets=outlets, source_text=source_text), reliable


def _gate(text: str, b: writer.Brief | None, reliable: list[dict[str, Any]]) -> dict[str, Any]:
    return gates.gate(text, source_text=b.source_text if b else "", source_names=[b.outlet] if b else [], reliable_count=len(reliable),
                      buzz_ok=bool(b and (b.google or b.x_trend)), discussion_ok=bool(b and b.reddit_sub))


async def suggest(db: Session, s: TtSettings, settings: Settings, topic: TtTopic, now: datetime, *, writer_transport: Any = None) -> TtPost:
    """Write and check one tweet for a topic. Its status says what happens next: pending (the owner decides), queued (automatic, hot),
    held (a gate or the News Desk said no; never sent)."""
    b, reliable = brief_of(topic)
    text, angle, used = "", "", "rules"
    held: str | None = None
    result: dict[str, Any] = {"ok": False, "checks": [], "reason": ""}
    if b is None:
        held = "No reliable source backs this topic yet (only social signals): held until an outlet reports it"
        text = f"(no tweet: {topic.title[:200]})"
        result = _gate(text, None, [])
    else:
        model_text = await writer.model(settings, b, transport=writer_transport)
        if model_text:
            result = _gate(model_text, b, reliable)
            if result["ok"]:
                text, angle, used = model_text, "model", "model"
            else:
                logger.info("trending: model text held (%s), using the rules writer", result["reason"])
        if not text:
            text, angle = writer.rules(b)
            result = _gate(text, b, reliable)
        if not result["ok"]:
            held = result["reason"]
    conflict = coordinate.newsdesk_conflict(db, [topic.title] + [x["title"] for x in reliable[:3]], now) if held is None else None
    if conflict:
        held = conflict
    hot = topic.score >= s.hot_score
    status = "held" if held else ("queued" if automatic(s) and hot else "pending")
    post = TtPost(topic_id=topic.id, category=topic.category, status=status, text=text, angle=angle, writer=used, fingerprint=checks.fingerprint(text),
                  score=topic.score, sources=[{"name": x["outlet"], "url": x["url"], "title": x["title"], "at": x.get("at")} for x in reliable[:4]],
                  source_text=b.source_text if b else "", checks=result["checks"], hold_reason=held, metrics={},
                  approved_at=now if status == "queued" else None, expires_at=now + (QUEUED_TTL if status == "queued" else PENDING_TTL))
    db.add(post)
    db.flush()
    return post


def _has_post(db: Session, topic: TtTopic, s: TtSettings, now: datetime) -> bool:
    since = now - timedelta(hours=s.topic_cooldown_hours)
    return db.scalar(select(TtPost.id).where(TtPost.topic_id == topic.id, TtPost.created_at >= since, TtPost.status != "rejected").limit(1)) is not None


def _recent_duplicate(db: Session, topic: TtTopic, s: TtSettings, now: datetime) -> bool:
    """Another topic's post on the same story (a cluster that split and re-formed): one story, one tweet, within the cooldown."""
    since = now - timedelta(hours=s.topic_cooldown_hours)
    for p in db.scalars(select(TtPost).where(TtPost.created_at >= since, TtPost.status.notin_(("rejected", "held", "expired")))):
        if p.topic_id != topic.id and any(coordinate.same_story(topic.title, str(x.get("title") or "")) for x in p.sources or []):
            return True
    return False


# --- publish ---------------------------------------------------------------------------------------------------------------


def expire(db: Session, now: datetime) -> int:
    n = db.execute(update(TtPost).where(TtPost.status.in_(("pending", "queued")), TtPost.expires_at.is_not(None), TtPost.expires_at <= now)
                   .values(status="expired", note="Went stale before it was sent")).rowcount
    n2 = db.execute(update(TtPost).where(TtPost.status == "publishing", TtPost.updated_at <= now - timedelta(minutes=15))
                    .values(status="uncertain", error="The publisher stopped mid-send: check X before resending")).rowcount
    db.commit()
    return int(n or 0) + int(n2 or 0)


def waiting_reason(db: Session, s: TtSettings, post: TtPost, now: datetime) -> str | None:
    """Why this queued post can't go out now, or None."""
    if (why := coordinate.desk_blocked(db, now, s)) is not None:
        return why
    if (why := coordinate.cap_problem(db, s, now)) is not None:
        return why
    if (why := coordinate.spacing_problem(db, s, now)) is not None:
        return why
    titles = [str(x.get("title") or "") for x in post.sources or []]
    if (why := coordinate.newsdesk_conflict(db, titles, now)) is not None:
        return why
    return None


async def publish_due(db: Session, s: TtSettings, settings: Settings, runtime: Any, agent_id: int, now: datetime, only: int | None = None) -> str | None:
    """The best queued post, if nothing stops it. One per tick."""
    stmt = select(TtPost).where(TtPost.status == "queued").order_by(TtPost.score.desc(), TtPost.id)
    if only is not None:
        stmt = stmt.where(TtPost.id == only)
    post = db.scalars(stmt.limit(1)).first()
    if post is None:
        return None
    why = waiting_reason(db, s, post, now)
    if why is not None:
        held = why.startswith("covered by the News Desk")
        post.note = why
        if held:
            post.status, post.hold_reason = "held", why
        db.commit()
        return "held" if held else "waiting"
    re_check = gates.gate(post.text, source_text=post.source_text, source_names=[x["name"] for x in post.sources or []][:1], reliable_count=len(post.sources or []),
                          buzz_ok=_flag(post, "buzz"), discussion_ok=_flag(post, "discussion"))
    if not re_check["ok"]:
        post.status, post.hold_reason, post.checks = "held", re_check["reason"], re_check["checks"]
        db.commit()
        return "held"
    return await publish(db, s, post, settings, runtime, agent_id, now)


def _flag(post: TtPost, which: str) -> bool:
    """The buzz / discussion permissions: only what our own sources measured (brief_of writes it into source_text)."""
    text = post.source_text or ""
    return ("trending" in text) if which == "buzz" else ("a top thread on r/" in text)


def _claim(db: Session, post: TtPost) -> bool:
    done = db.execute(update(TtPost).where(TtPost.id == post.id, TtPost.status == "queued").values(status="publishing", attempts=TtPost.attempts + 1, updated_at=utcnow()))
    db.commit()
    return done.rowcount == 1


async def publish(db: Session, s: TtSettings, post: TtPost, settings: Settings, runtime: Any, agent_id: int, now: datetime) -> str:
    """Send one post, or record it as a dry run. Never raises: the outcome is on the post (and the health line)."""
    from app.connectors.x.errors import XAuthError, XCreditsError, XError, XForbiddenError, XRateLimitError, XUnavailableError
    from app.models.enums import Platform, SocialAction
    from app.social.guard import ActionRequest, SocialActionGuard
    from app.social.x_service import make_x_client

    if not _claim(db, post):
        return "already claimed"
    db.refresh(post)
    topic = db.get(TtTopic, post.topic_id)
    if not coordinate.x_live(db, s):
        why = "Trending's own dry-run switch" if not s.live else "the News Desk's X lane is in dry run"
        post.status, post.published_at, post.note = "dry_run", now, f"Dry run ({why}): recorded, not sent"
        if topic is not None:
            topic.status, topic.posted_at = "posted", now
        db.commit()
        logger.info("trending DRY RUN [%s score %.1f]: %s", post.category, post.score, post.text.replace("\n", " / "))
        return "dry_run"
    guard = SocialActionGuard(db, agent_id, settings)
    req = ActionRequest(Platform.X, SocialAction.POST, text=post.text)
    problem = guard.news_problem(req)
    if problem:
        post.status, post.note = "queued", f"X guard: {problem}"
        db.commit()
        return "blocked"
    client = make_x_client(db, settings, runtime)
    try:
        permit = guard.issue_news_permit(req)
        data = await client.create_post(post.text, permit=permit)
    except XUnavailableError as e:
        post.status = "uncertain" if "in time" in str(e) else ("failed" if post.attempts >= MAX_ATTEMPTS else "queued")
        post.error = f"X unavailable: {str(e)[:200]}"
        db.commit()
        return post.status
    except XRateLimitError as e:
        post.status = "failed" if post.attempts >= MAX_ATTEMPTS else "queued"
        post.error = f"X rate limit / cap: {str(e)[:200]}"
        db.commit()
        return post.status
    except (XCreditsError, XAuthError, XForbiddenError) as e:
        post.status, post.error = "failed", f"X refused it ({type(e).__name__}): {str(e)[:200]}"
        _note(s, "x_last_error", {"at": now.isoformat(), "post_id": post.id, "message": post.error})
        db.commit()
        await _alert(db, settings, "trending:x", f"Price Chat alert: Trending Topics couldn't post to X\n{post.error}", now)
        return "failed"
    except XError as e:
        post.status, post.error = "failed", f"{type(e).__name__}: {str(e)[:200]}"
        db.commit()
        return "failed"
    except Exception as e:  # noqa: BLE001
        post.status, post.error = "uncertain", f"{type(e).__name__} while posting: check X before resending"
        db.commit()
        logger.exception("trending: post %s stopped", post.id)
        return "uncertain"
    finally:
        await client.aclose()
    x_id = str((data or {}).get("id") or "")
    if not x_id:
        post.status, post.error = "uncertain", "X answered without a post id"
        db.commit()
        return "uncertain"
    post.status, post.x_id, post.published_at, post.error = "published", x_id, now, None
    if topic is not None:
        topic.status, topic.posted_at = "posted", now
    guard.record_attempt(req, None, None, "executed", reason="trending topics", external_id=x_id)
    _note(s, "x_last_ok", {"at": now.isoformat(), "post_id": post.id, "x_id": x_id})
    db.commit()
    logger.info("trending published %s (post %s)", x_id, post.id)
    return "published"


def _note(s: TtSettings, key: str, value: dict[str, Any]) -> None:
    s.state = {**(s.state or {}), key: value}


# --- monitoring --------------------------------------------------------------------------------------------------------------


async def _alert(db: Session, settings: Settings, key: str, text: str, now: datetime, open_: bool | None = True) -> bool:
    from app import alerts

    return await alerts.notify_owner(db, key, text, settings=settings, now=now, open_=open_)


async def check_health(db: Session, s: TtSettings, settings: Settings, now: datetime) -> list[str]:
    """Owner alert for a source failing ALERT_AFTER reads in a row (deduplicated: notify_owner sends a key at most every 6 h), and one
    'recovered' message when it works again. A source that is simply not set up or not on the plan never alerts."""
    from app import alerts

    sent = []
    for name, h in ((s.state or {}).get("health") or {}).items():
        key = f"trending:source:{name}"
        if h.get("status") == "error" and int(h.get("fails") or 0) >= ALERT_AFTER:
            if await _alert(db, settings, key, f"Price Chat alert: Trending Topics can't read {h.get('name')}\n{h.get('fails')} reads in a row: {h.get('problem')}", now):
                sent.append(key)
        elif h.get("status") == "ok" and alerts._is_open(db, key):
            if await alerts.notify_owner(db, f"{key}:recovered", f"Price Chat: Trending Topics reads {h.get('name')} again.", settings=settings, now=now, cooldown=False):
                alerts._close(db, key)
                sent.append(f"{key}:recovered")
    return sent


# --- tick ------------------------------------------------------------------------------------------------------------------


async def tick(db: Session, settings: Settings, runtime: Any, agent_id: int, now: datetime | None = None, *, transport: httpx.AsyncBaseTransport | None = None,
               writer_transport: Any = None, only: set[str] | None = None) -> dict[str, Any]:
    now = now or utcnow()
    s = settings_row(db)
    db.commit()
    out: dict[str, Any] = {"mode": s.mode, "automatic": automatic(s), "live": coordinate.x_live(db, s), "stopped": s.kill_switch, "expired": expire(db, now)}
    if not s.enabled:
        out["skipped"] = "Trending Topics is switched off"
        _note(s, "last_run", {"at": now.isoformat(), **out})
        db.commit()
        return out
    db.execute(delete(TtSignal).where(TtSignal.last_seen < now - KEEP_SIGNALS))
    db.execute(delete(TtTopic).where(TtTopic.last_seen < now - KEEP_SIGNALS, TtTopic.status != "posted"))
    db.commit()
    async with httpx.AsyncClient(transport=transport or HTTP_TRANSPORT, timeout=20, follow_redirects=True) as client:
        read = await read_sources(db, s, settings, runtime, now, client, only)
    topics = build_topics(db, s, now)
    made = 0
    for t in sorted(topics, key=lambda t: -t.score):
        if made >= MAX_NEW_PER_SCAN or t.score < s.suggest_score:
            break
        if t.status != "active" or _has_post(db, t, s, now) or _recent_duplicate(db, t, s, now):
            continue
        await suggest(db, s, settings, t, now, writer_transport=writer_transport)
        made += 1
    db.commit()
    published = await publish_due(db, s, settings, runtime, agent_id, now) if automatic(s) or _any_approved(db) else None
    alerts_sent = await check_health(db, s, settings, now)
    db.refresh(s)
    out.update({"stored": read["stored"], "topics": len(topics), "suggested": made, "published": published, "alerts": alerts_sent})
    _note(s, "last_run", {"at": now.isoformat(), **out})
    db.commit()
    return out


def _any_approved(db: Session) -> bool:
    return db.scalar(select(TtPost.id).where(TtPost.status == "queued").limit(1)) is not None


# --- the owner's decisions ----------------------------------------------------------------------------------------------------


async def approve(db: Session, settings: Settings, runtime: Any, agent_id: int, post: TtPost, now: datetime) -> str:
    """Approve a pending tweet: it is checked again and queued; it goes out now if nothing (limits, spacing, the News Desk) stops it,
    otherwise on a later tick. In dry run it is recorded, not sent."""
    if post.status != "pending":
        raise ValueError("Only a pending tweet can be approved (a held one must be edited until it passes).")
    if settings_row(db).kill_switch:
        raise ValueError("Trending Topics' stop is on.")
    result = gates.gate(post.text, source_text=post.source_text, source_names=[x["name"] for x in post.sources or []][:1], reliable_count=len(post.sources or []),
                        buzz_ok=_flag(post, "buzz"), discussion_ok=_flag(post, "discussion"))
    if not result["ok"]:
        post.status, post.hold_reason, post.checks = "held", result["reason"], result["checks"]
        db.commit()
        raise ValueError(f"It fails a check: {result['reason']}")
    s = settings_row(db)
    post.status, post.approved_at, post.hold_reason, post.expires_at = "queued", now, None, now + QUEUED_TTL
    db.commit()
    return await publish_due(db, s, settings, runtime, agent_id, now, only=post.id) or "queued"


def edit(db: Session, post: TtPost, text: str) -> dict[str, Any]:
    """The owner's words go through the same gate (they may not add figures, names, quotes or accusations the sources don't have)."""
    if post.status not in ("pending", "queued", "held"):
        raise ValueError("Only a tweet that hasn't gone out can be edited.")
    if not post.sources:
        raise ValueError("This topic has no reliable source behind it: there is nothing a tweet could stand on.")
    result = gates.gate(text, source_text=post.source_text, source_names=[x["name"] for x in post.sources or []][:1], reliable_count=len(post.sources or []),
                        buzz_ok=_flag(post, "buzz"), discussion_ok=_flag(post, "discussion"))
    if result["ok"]:
        post.original_text = post.original_text or post.text
        post.text, post.fingerprint, post.writer = text.strip(), checks.fingerprint(text), "owner"
        post.checks = result["checks"]
        if post.status == "held":
            post.status, post.hold_reason = "pending", None
        db.commit()
    return result


# --- metrics -------------------------------------------------------------------------------------------------------------------


async def collect_metrics(db: Session, settings: Settings, runtime: Any, now: datetime | None = None) -> dict[str, Any]:
    """Impressions, likes, reposts and replies for the last three days' trend posts (owned reads, $0.001 each; at most 100)."""
    from app.connectors.x.errors import XError
    from app.security import credentials
    from app.social.x_service import make_x_client

    now = now or utcnow()
    posts = list(db.scalars(select(TtPost).where(TtPost.status == "published", TtPost.x_id.is_not(None), TtPost.published_at >= now - timedelta(days=3)).limit(100)))
    if not posts:
        return {"updated": 0}
    if not credentials.x_credentials(settings).user_context:
        return {"updated": 0, "skipped": "X account keys are needed for post metrics"}
    client = make_x_client(db, settings, runtime)
    try:
        data = await client.own_post_metrics([p.x_id for p in posts if p.x_id])
    except XError as e:
        s = settings_row(db)
        _note(s, "metrics_error", {"at": now.isoformat(), "message": f"{type(e).__name__}: {str(e)[:200]}"})
        db.commit()
        return {"updated": 0, "error": type(e).__name__}
    finally:
        await client.aclose()
    by_id = {str(d.get("id")): d for d in (data.get("data") or [])}
    n = 0
    for p in posts:
        d = by_id.get(p.x_id or "")
        if d:
            p.metrics = {**(d.get("public_metrics") or {}), **(d.get("non_public_metrics") or {}), **(d.get("organic_metrics") or {})}
            p.metrics_at = now
            n += 1
    db.commit()
    return {"updated": n}


# --- Admin ------------------------------------------------------------------------------------------------------------------------


def _iso(d: datetime | None) -> str | None:
    return d.isoformat() if d else None


def post_out(p: TtPost) -> dict[str, Any]:
    return {"id": p.id, "topic_id": p.topic_id, "category": p.category, "status": p.status, "text": p.text, "edited": bool(p.original_text), "angle": p.angle,
            "writer": p.writer, "score": p.score, "sources": p.sources, "checks": p.checks, "hold_reason": p.hold_reason, "note": p.note, "error": p.error,
            "x_id": p.x_id, "url": f"https://x.com/i/status/{p.x_id}" if p.x_id else None, "metrics": p.metrics, "created_at": _iso(p.created_at),
            "published_at": _iso(p.published_at), "expires_at": _iso(p.expires_at), "cashtags": gates.cashtags(p.text)}


def topic_out(t: TtTopic, post: TtPost | None) -> dict[str, Any]:
    return {"id": t.id, "title": t.title, "category": t.category, "status": t.status, "score": t.score, "peak_score": t.peak_score, "parts": t.parts,
            "sources": t.sources, "metrics": t.metrics, "first_seen": _iso(t.first_seen), "last_seen": _iso(t.last_seen), "posted_at": _iso(t.posted_at),
            "post": post_out(post) if post else None}


def settings_out(s: TtSettings) -> dict[str, Any]:
    return {"enabled": s.enabled, "mode": s.mode, "automatic": automatic(s), "auto_confirmed_at": _iso(s.auto_confirmed_at), "live": s.live, "kill_switch": s.kill_switch,
            "hot_score": s.hot_score, "suggest_score": s.suggest_score, "max_per_day": s.max_per_day, "gap_minutes": s.gap_minutes,
            "topic_cooldown_hours": s.topic_cooldown_hours, "newsdesk_gap_minutes": s.newsdesk_gap_minutes, "shared_daily_cap": s.shared_daily_cap,
            "categories": {c: bool((s.categories or {}).get(c, True)) for c in rank.CATEGORIES}, "sources": sources.merged(s.sources)}


def overview(db: Session, settings: Settings) -> dict[str, Any]:
    from app.connectors.state import get_connector_state
    from app.models.enums import Platform
    from app.security import credentials

    now = utcnow()
    s = settings_row(db)
    db.commit()
    topics = list(db.scalars(select(TtTopic).where(TtTopic.status.in_(("active", "posted", "cooling"))).order_by(TtTopic.score.desc()).limit(40)))
    posts = {p.topic_id: p for p in db.scalars(select(TtPost).where(TtPost.topic_id.in_([t.id for t in topics])).order_by(TtPost.id))} if topics else {}

    def rows(*statuses: str, limit: int = 30) -> list[dict[str, Any]]:
        return [post_out(p) for p in db.scalars(select(TtPost).where(TtPost.status.in_(statuses)).order_by(TtPost.created_at.desc()).limit(limit))]

    state = s.state or {}
    creds = credentials.x_credentials(settings)
    xstate = get_connector_state(db, Platform.X)
    return {"settings": settings_out(s), "last_run": state.get("last_run"), "health": state.get("health") or {}, "usage": state.get("usage") or {},
            "topics": [topic_out(t, posts.get(t.id)) for t in topics], "queue": rows("pending", "queued"), "held": rows("held", limit=20),
            "published": rows("published", "dry_run", "uncertain", "failed", limit=30), "coordination": coordinate.summary(db, s, now),
            "learning": learning.report(db, now), "writer": "model" if credentials.resolve("anthropic_api_key", settings) and settings.newsdesk_model else "rules",
            "x": {"configured": creds.configured, "can_write": creds.user_context, "status": xstate.status, "last_ok": state.get("x_last_ok"), "last_error": state.get("x_last_error")},
            "cost": {"per_post_usd": float(settings.x_write_cost_estimate_usd), "x_trends_per_request_usd": 0.010, "x_post_read_usd": 0.005}}
