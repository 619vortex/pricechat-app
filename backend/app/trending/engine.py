"""One Trending Topics tick (the newsdesk.trending job, every job_trending_seconds): read the sources that are due, store their signals,
group and score topics, write a tweet for the hot ones, and publish what is due.

Defaults: Approval mode (every tweet waits for the owner), automatic publishing off, dry run (X is never called). Automatic needs the
owner's explicit confirmation in Admin; Live is a separate switch. Every post passes app/trending/gates and app/trending/coordinate.
"""

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.config.settings import Settings
from app.db.base import utcnow
from app.models.trending import TtPost, TtSettings, TtSignal, TtTopic
from app.newsdesk import checks
from app.terminal import discovery, intel
from app.trending import coordinate, gates, learning, postmortem, rank, sensitive, sources, status_feeds, writer

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
            elif name == "faa":
                items, problem, meta = await sources.faa(client, o)
            elif name == "status":
                items, problem, meta = await status_feeds.read(client, now)
            elif name in discovery.DEFAULTS:
                items, problem, meta = await discovery.read(name, client, o, now)
            elif name == "x_accounts":
                from app.terminal import xintel

                items, problem, meta = await xintel.read(db, s, settings, runtime, o, now)
                state["x_intel"] = (s.state or {}).get("x_intel") or {}  # the read's since_ids and spend, kept when health is written below
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
        db.commit()  # never hold SQLite's write lock across the next source's network read (2026-11-18: a 16 s GDELT read locked the worker's claim)
        if name == "x_accounts":
            lr = (((s.state or {}).get("x_intel") or {}).get("last_read") or {})
            logger.info("x pilot read: %d new posts, %s requests, $%.4f%s | today $%.4f", len(items), lr.get("requests"), float(lr.get("usd") or 0),
                        f" ({_safe(problem or lr.get('problem'), 120)})" if (problem or lr.get("problem")) else "",
                        float((((s.state or {}).get("x_intel") or {}).get("spend") or {}).get(now.strftime("%Y-%m-%d"), 0)))
        if name in discovery.DEFAULTS:  # the Terminal's own sources: one line per read, so production shows they work
            logger.info("terminal source %s: %d items, %d new%s", name, len(items), stored[name], f" ({_safe(problem, 100)})" if problem else "")
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
    from app.newsdesk import wire

    if any(x.reliable and x.summary for x in sigs):  # the casing reads ordinary words from today's reporting (writer.lead_sentence)
        wire.learn_vocabulary(t for x in sigs if x.reliable for t in (x.title, x.summary))
    clusters = rank.cluster(sigs)
    weights = learning.weights(db, now)
    state = dict(s.state or {})
    state["weights"] = weights
    s.state = state
    enabled = s.categories or {}
    existing = list(db.scalars(select(TtTopic).where(TtTopic.last_seen >= now - timedelta(hours=48))))
    # every topic row a signal belongs to, newest first. Production 2026-11-17: the Apple iCloud outage became a NEW topic row every scan
    # (T40050, T40070, ... 40 rows in 2 h, each with its own held draft) because each signal mapped only to its OLDEST row, which the
    # sibling cluster had already taken, so the second cluster never found the row it made the scan before.
    owner: dict[int, list[TtTopic]] = {}
    for t in sorted(existing, key=lambda t: _aware(t.last_seen) or now, reverse=True):
        for sid in t.signal_ids or []:
            owner.setdefault(int(sid), []).append(t)
    touched: list[TtTopic] = []
    for c in clusters:
        if not enabled.get(c.category, True):
            continue
        sc, parts = rank.score(c, now, weights)
        parts["sensitive"] = sensitive.classify(" ".join(x.title for x in (c.reliable or c.signals)[:8]))
        ids = sorted(x.id for x in c.signals)
        lead = c.lead
        cands: dict[int, tuple[int, TtTopic]] = {}
        for i in ids:
            for o in owner.get(i, []):
                n, _ = cands.get(o.id, (0, o))
                cands[o.id] = (n + 1, o)
        ranked = sorted(cands.values(), key=lambda x: (-x[0], -(_aware(x[1].last_seen) or now).timestamp()))
        topic = next((o for _, o in ranked if o not in touched and same_topic(o, ids, lead.title)), None)
        srcs = [{"source": x.source, "outlet": x.outlet, "title": x.title, "url": x.url, "reliable": x.reliable, "engagement": x.engagement,
                 "summary": (x.summary or "")[:500] if x.reliable else "", "at": (x.published_at or x.first_seen).isoformat(), "extra": {k: v for k, v in x.extra.items() if k in ("approx_traffic", "comments", "score", "query", "trend", "geo", "kind", "official", "magnitude",
                                                                   "alert", "event", "points", "tsunami", "domain", "thread", "tier", "handle",
                                                                   "group", "post_id", "quoted", "fp", "tags", "metrics")}}
                # the topic's lead first: the writer leads with sources[0] (2026-11-16: a quote-led feature led trend post 152)
                for x in sorted(c.signals, key=lambda x: (x is not lead, not x.reliable, rank.quote_led(x.title), -x.engagement))[:12]]
        metrics: dict[str, Any] = {"signals": len(c.signals)}
        for x in c.signals:
            metrics[x.source] = round(float(metrics.get(x.source, 0)) + x.engagement, 1)
        metrics["reddit_comments"] = int(sum(float(x.extra.get("comments") or 0) for x in c.signals))
        if topic is None:
            topic = TtTopic(key=f"t:{ids[0]}:{int(now.timestamp())}", title=lead.title[:300], category=c.category, status="active", score=sc, peak_score=sc,
                            parts=parts, signal_ids=ids, sources=srcs, metrics=metrics, first_seen=min(x.first_seen for x in c.signals), last_seen=now)
            db.add(topic)
        else:
            keep = {k: v for k, v in (topic.parts or {}).items() if k == "latency"}  # what its post measured stays with it
            topic.title, topic.category, topic.score, topic.parts = lead.title[:300], c.category, sc, {**parts, **keep}
            topic.peak_score = max(topic.peak_score or 0, sc)
            topic.signal_ids = sorted(set(topic.signal_ids or []) | set(ids))[-200:]
            topic.sources, topic.metrics, topic.last_seen = srcs, metrics, now
            if topic.status == "cooling":
                topic.status = "active"
        try:
            topic.parts = {**(topic.parts or {}), "terminal": terminal_view(topic, lead.summary or "")}
        except Exception:  # noqa: BLE001 - the Terminal's scores never stop a scan
            logger.exception("trending: terminal scores failed for %s", topic.title[:60])
        touched.append(topic)
    for t in existing:
        if t not in touched and t.status == "active":
            t.status, t.score = "cooling", round((t.score or 0) * 0.7, 1)
    db.flush()
    return touched


def same_topic(topic: TtTopic, ids: list[int], lead_title: str) -> bool:
    """May this cluster continue an existing topic row? Only when it is the same story: most of the cluster's signals are the topic's, most
    of the topic's are in the cluster, or the headlines tell the same event. Production 2026-11-17 ledger: topic 20513, which carried trend
    post 152 (a COP31 story), had become "Wizards' defense collapses in seventh straight loss" through one shared signal, inheriting the
    post (so the new story counted as "already has a tweet") and the old peak score."""
    mine = set(topic.signal_ids or [])
    shared = len(mine & set(ids))
    if shared and (shared * 2 >= len(ids) or shared * 2 >= len(mine)):
        return True
    return coordinate.same_story(topic.title or "", lead_title or "")


def brief_of(topic: TtTopic) -> tuple[writer.Brief | None, list[dict[str, Any]]]:
    """The writer's facts, from the topic's reliable items only, plus what our sources measured (topic.parts.growth). None when no
    reliable item backs it."""
    srcs = topic.sources or []
    # a news report leads before an X post of it; an X post that leads is credited by its outlet's name, never as an @mention
    reliable = sorted([x for x in srcs if x.get("reliable")], key=lambda x: x.get("source") == "x_accounts")
    reliable = [{**x, "outlet": intel.x_credit(x)} if x.get("source") == "x_accounts" else x for x in reliable]
    google = max((x for x in srcs if x.get("source") == "google" and (x.get("extra") or {}).get("query") and not (x.get("extra") or {}).get("kind")),
                 key=lambda x: (x.get("engagement") or 0, (x.get("extra") or {}).get("geo") == "US"), default=None)
    reddit = max((x for x in srcs if x.get("source") == "reddit"), key=lambda x: x.get("engagement") or 0, default=None)
    x_trend = any(x.get("source") == "x_trends" for x in srcs)
    outlets = list(dict.fromkeys(x["outlet"] for x in reliable))
    if not reliable:
        return None, []
    lead = reliable[0]
    parts = topic.parts or {}
    g = parts.get("growth") or {}
    sens = parts["sensitive"] if "sensitive" in parts else sensitive.classify(" ".join(x["title"] for x in reliable[:8]))
    gx = (google or {}).get("extra") or {}
    b = writer.Brief(key=topic.key, category=topic.category, headline=lead["title"], outlet=lead["outlet"], google=bool(google),
                     google_traffic=str(gx.get("approx_traffic") or ""), google_query=str(gx.get("query") or ""), google_geo=str(gx.get("geo") or ""),
                     google_from=float(g.get("google_from") or 0) if g.get("google_up") else 0.0,
                     google_to=float(g.get("google_to") or (google or {}).get("engagement") or 0), google_minutes=int(g.get("google_minutes") or 0),
                     reddit_sub=(reddit or {}).get("outlet", "")[2:] if reddit else "", x_trend=x_trend, outlets=outlets,
                     others=[(x["outlet"], x["title"]) for x in reliable[1:8]], sensitive=sens, summary=str(lead.get("summary") or ""))
    measured = []
    if google:
        measured.append("on Google's trending searches")
    if reddit:
        measured.append(f"a top thread on {reddit['outlet']}")
    if x_trend:
        measured.append("trending on X")
    # the facts a tweet may state: the lead article's own headline and report, and what we measured; never another article's words
    # (2026-11-16: trend post 152 told two different stories as one)
    b.source_text = f"{lead['title']} {b.summary} " + " ".join(measured + writer.measured_lines(b))
    return b, reliable


def _gate(text: str, b: writer.Brief | None, reliable: list[dict[str, Any]], breaking_ok: bool = False) -> dict[str, Any]:
    result = gates.gate(text, source_text=b.source_text if b else "", source_names=[b.outlet] if b else [], reliable_count=len(reliable),
                        buzz_ok=bool(b and (b.google or b.x_trend)), discussion_ok=bool(b and b.reddit_sub), breaking_ok=breaking_ok)
    if b is None:
        return result
    problems = gates.story_problems(text, lead_title=b.headline, lead_summary="", measured=b.source_text, outlet=b.outlet, category=b.category)
    result["checks"] = list(result["checks"]) + [{"check": "story", "passed": not problems, "detail": "; ".join(problems) or "one story, worth a post"}]
    if problems and result["ok"]:
        result["ok"], result["reason"] = False, problems[0]
    return result


def final_problems(post: TtPost) -> list[str]:
    """The last check, immediately before a tweet is sent (owner, 2026-11-16): the story it tells is still worth a post, on this account's
    beat, and the words are the lead article's alone. Written from what the post itself stored when it was checked."""
    srcs = post.sources or []
    if not srcs:
        return ["no reliable source stored with the post"]
    lead = srcs[0]
    return gates.story_problems(post.text or "", lead_title=str(lead.get("title") or ""), lead_summary="", measured=post.source_text or "",
                                outlet=str(lead.get("name") or ""), category=post.category or "")


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
            model_text = writer.keep_one_cashtag(model_text)
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
        elif _fast(topic) and ((topic.parts or {}).get("terminal") or {}).get("breaking_ok") and not text.startswith(("BREAKING", "JUST IN")):
            # a verified breaking event opens with BREAKING when the words still pass every check that way (app/terminal/intel)
            lead_in = f"BREAKING: {text}"
            tried = _gate(lead_in, b, reliable, breaking_ok=True)
            if tried["ok"]:
                text, result = lead_in, tried
        if _fast(topic):
            angle = ("fast:" + angle)[:24]
    fast = _fast(topic)
    conflict = coordinate.newsdesk_conflict(db, [topic.title] + [x["title"] for x in reliable[:3]], now, fast=fast, topic_id=topic.id) if held is None else None
    if conflict:
        held = conflict
    hot, why_not = auto_ok(s, topic, text, b)
    status = "held" if held else ("queued" if automatic(s) and hot else "pending")
    if b is not None and b.sensitive and used == "model":
        angle = f"sensitive:{b.sensitive['kind']}"
    post = TtPost(topic_id=topic.id, category=topic.category, status=status, text=text, angle=angle[:24], writer=used, fingerprint=checks.fingerprint(text),
                  score=topic.score, sources=[{"name": x["outlet"], "url": x["url"], "title": x["title"], "at": x.get("at")} for x in reliable[:4]],
                  source_text=b.source_text if b else "", checks=result["checks"], hold_reason=held, metrics={},
                  note=why_not if status == "pending" and (topic.score >= s.hot_score or (b is not None and b.sensitive)) else None,
                  approved_at=now if status == "queued" else None, expires_at=now + (QUEUED_TTL if status == "queued" else PENDING_TTL))
    db.add(post)
    db.flush()
    return post


# --- the Terminal (app/terminal/intel, owner 2026-11-18) ------------------------------------------------------------------------------


def terminal_view(topic: TtTopic, lead_summary: str = "") -> dict[str, Any]:
    """Viral potential and evidence (never mixed), the rumour flag, the fast lane, measured at this scan."""
    parts, srcs = topic.parts or {}, topic.sources or []
    ev = intel.evidence(topic.title, srcs, parts)
    vi = intel.viral(topic.title, topic.category, srcs, parts)
    lead = intel.lead_of(srcs, topic.title)
    fast, fast_why = intel.fast_lane(str(lead.get("title") or topic.title), lead_summary or str(lead.get("summary") or ""), srcs, parts, ev)
    if fast and (parts.get("sensitive") or {}).get("tier") == "approval":
        fast, fast_why = False, f"approval-only subject ({(parts.get('sensitive') or {}).get('label')})"
    old = (parts.get("terminal") or {}) if isinstance(parts.get("terminal"), dict) else {}
    return {"viral": vi, "evidence": ev, "rumor": ev["rumor"], "ahead": intel.ahead_of_mainstream(srcs, parts, ev), "fast": fast, "fast_why": fast_why,
            "claim": intel.claim_status(topic.title, srcs, ev),
            "breaking_ok": intel.breaking_allowed(ev), "fast_since": old.get("fast_since") or (utcnow().isoformat() if fast else None),
            "hooks": old.get("hooks") if old.get("hooks_for") == topic.title else None, "hooks_for": old.get("hooks_for")}


def _fast(t: TtTopic) -> bool:
    return bool(((t.parts or {}).get("terminal") or {}).get("fast"))


def at_line(s: TtSettings, topic: TtTopic) -> bool:
    """At the automatic line: hot enough, or a verified high-impact event in the fast lane."""
    return topic.score >= s.hot_score or _fast(topic)


def fast_post(db: Session, post: TtPost) -> bool:
    if (post.angle or "").startswith("fast"):
        return True
    topic = db.get(TtTopic, post.topic_id) if post.topic_id else None
    return topic is not None and _fast(topic)


def breaking_ok(db: Session, post: TtPost) -> bool:
    """May this post say BREAKING / JUST IN? Only a story the Terminal reads as verified (corroborated or better, not a rumour)."""
    topic = db.get(TtTopic, post.topic_id) if post.topic_id else None
    return bool(topic is not None and ((topic.parts or {}).get("terminal") or {}).get("breaking_ok"))


def auto_ok(s: TtSettings, topic: TtTopic, text: str, b: writer.Brief | None) -> tuple[bool, str | None]:
    """(may go out without the owner, why not). Hot enough, with measured growth (rank.growth), and nothing sensitive stops it
    (app/trending/sensitive: approval-only subjects never; strict ones only with 2+ independent outlets, attributed, no person named)."""
    if b is not None and b.sensitive and b.sensitive.get("tier") == "approval":
        return False, sensitive.auto_problem(b.sensitive, text=text, outlets=b.outlets)
    term = (topic.parts or {}).get("terminal") or {}
    if term.get("rumor"):
        # owner, 2026-11-18: an unverified rumour never publishes automatically as fact; the Terminal's Rumor watch shows it for review
        return False, "unverified or hedged report (rumour watch): human review only"
    fast = bool(term.get("fast"))
    if topic.score < s.hot_score and not fast:
        return False, f"below the automatic line ({topic.score:.0f} < {s.hot_score:.0f})"
    g = (topic.parts or {}).get("growth") or {}
    if not g.get("measured") and not fast:
        return False, "no measured growth (scans, Google Trends, engagement): approval only"
    inc = (topic.parts or {}).get("incident") or {}
    if inc.get("major") and not inc.get("verified"):
        # owner, 2026-11-17: detect early, publish only on sufficient evidence (two outlets, the company's own word, or an official source)
        return False, f"a major incident not yet verified ({inc.get('evidence')}): approval only"
    if b is not None and (why := sensitive.auto_problem(b.sensitive, text=text, outlets=b.outlets)):
        return False, why
    # owner, 2026-11-18: popular is not the same as newsworthy. Nothing goes out on its own without a concrete, source-stated reason to care
    # (app/newsdesk/newsworthy); momentum, outlet counts and dramatic words never stand in for one. Production: trend post 765 "Democrats
    # probe whether Lutnick helped Cantor avoid Nvidia losses" went out on the fast lane with viral 31.
    from app.newsdesk import newsworthy

    nw = newsworthy.qualify(getattr(topic, "title", "") or "", b.summary if b is not None else "", outlets=len(b.outlets) if b is not None else 0)
    if not nw.ok:
        return False, f"not newsworthy: {nw.reason}"
    return True, None


def _has_post(db: Session, topic: TtTopic, s: TtSettings, now: datetime) -> bool:
    since = now - timedelta(hours=s.topic_cooldown_hours)
    earlier = list(db.scalars(select(TtPost).where(TtPost.topic_id == topic.id, TtPost.created_at >= since, TtPost.status != "rejected")))
    if not earlier:
        return False
    # production 2026-11-15: the first topic over the line (Iran/Bahrain, 75.8) had only a draft written for approval at 60-something that
    # expired unsent, and that draft blocked it for 24 h. A topic that has since crossed the automatic line gets a fresh tweet, written and
    # checked again (every gate, the sensitive rules, the limits), when all it had were unsent drafts from below the line.
    # 2026-11-16: the same for a draft HELD below the line (a gate said no to an earlier, weaker telling), and for one held only because no
    # reliable outlet had reported it yet, once one has. A draft written or held at the line itself, a sent one, or one the owner decided on
    # still blocks, so a topic gets one fresh attempt at the line, never a new draft every scan.
    if not automatic(s) or not at_line(s, topic):
        return True
    reliable_now = brief_of(topic)[0] is not None
    desk_clear = coordinate.newsdesk_conflict(db, [topic.title] + [str(x.get("title") or "") for x in (topic.sources or [])[:3] if x.get("reliable")], now,
                                              topic_id=topic.id) is None
    if all(_replaceable(p, s, reliable_now, desk_clear) for p in earlier):
        for p in earlier:
            if p.status in ("pending", "held"):
                p.status, p.note = "expired", "Replaced: the topic crossed the automatic line, so a fresh tweet was written and checked"
        return False
    return True


UNSENT = ("pending", "expired", "held")


def _replaceable(p: TtPost, s: TtSettings, reliable_now: bool, desk_clear: bool = False) -> bool:
    """May this earlier draft give way to a fresh tweet now that its topic is over the automatic line?"""
    if p.status not in UNSENT or p.approved_at is not None or p.original_text:
        return False  # sent, sending, approved or edited by the owner: their decision stands
    if p.status == "held" and (p.hold_reason or "").startswith("No reliable source"):
        return reliable_now
    if p.status == "held" and (p.hold_reason or "").startswith("covered by the News Desk (event") and "candidate" in (p.hold_reason or ""):
        return desk_clear  # the News Desk never took it (coordinate.CANDIDATE_HOLD): Trending may now
    return (p.score or 0) < s.hot_score and not (p.angle or "").startswith("fast")


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


def incident_post(db: Session, post: TtPost) -> bool:
    """A verified major incident (app/newsdesk/incidents): it may use the incident allowance (coordinate.INCIDENT_EXTRA_PER_DAY)."""
    topic = db.get(TtTopic, post.topic_id) if post.topic_id else None
    inc = ((topic.parts or {}).get("incident") or {}) if topic is not None else {}
    return bool(inc.get("major") and inc.get("verified"))


def waiting_reason(db: Session, s: TtSettings, post: TtPost, now: datetime) -> str | None:
    """Why this queued post can't go out now, or None."""
    if (why := coordinate.desk_blocked(db, now, s)) is not None:
        return why
    # owner, 2026-11-18/19: a queued post is judged by the LATEST editorial rules first, whatever else it waits for. Before, a post
    # next to a News Desk slot reported only "spacing" (the time-dependent failure of
    # test_a_trend_post_queued_before_the_gate_is_judged_again_before_it_goes near 04:00 UTC) and its editorial verdict waited.
    from app.newsdesk import newsworthy

    titles = [str(x.get("title") or "") for x in post.sources or []]
    told = [t for t in (titles[:1] + [(post.text or "").split("\n")[0]]) if t]
    checked = [newsworthy.qualify(t, post.source_text or "", outlets=len(post.sources or [])) for t in told]
    if checked and all(a.strength == "none" for a in checked):
        return f"not newsworthy: {checked[0].reason}"
    incident = incident_post(db, post)
    fast = fast_post(db, post)
    if (why := coordinate.cap_problem(db, s, now, incident=incident, fast=fast)) is not None:
        return why
    if (why := coordinate.spacing_problem(db, s, now, incident=incident, fast=fast)) is not None:
        return why
    if (why := coordinate.newsdesk_conflict(db, titles, now, fast=fast, topic_id=post.topic_id)) is not None:
        return why
    return None


async def publish_due(db: Session, s: TtSettings, settings: Settings, runtime: Any, agent_id: int, now: datetime, only: int | None = None,
                      decision: dict[str, Any] | None = None) -> str | None:
    """The best queued post, if nothing stops it. One per tick. decision (optional): filled with the post id and why, for the scan log."""
    decision = decision if decision is not None else {}
    stmt = select(TtPost).where(TtPost.status == "queued").order_by(TtPost.score.desc(), TtPost.id)
    if only is not None:
        stmt = stmt.where(TtPost.id == only)
    post = db.scalars(stmt.limit(1)).first()
    if post is None:
        return None
    decision["post_id"] = post.id
    topic = db.get(TtTopic, post.topic_id)
    stale = ((topic.parts or {}).get("stale") if topic is not None else None)
    if stale:  # it waited (spacing, caps) until it went stale: today's news or nothing
        post.status, post.note = "expired", f"Went stale while it waited: {stale}"
        decision["why"] = post.note
        db.commit()
        return "expired"
    why = waiting_reason(db, s, post, now)
    if why is not None:
        held = why.startswith("covered by the News Desk")
        post.note = why
        decision["why"] = why
        if held:
            post.status, post.hold_reason = "held", why
        db.commit()
        return "held" if held else "waiting"
    re_check = gates.gate(post.text, source_text=post.source_text, source_names=[x["name"] for x in post.sources or []][:1], reliable_count=len(post.sources or []),
                          buzz_ok=_flag(post, "buzz"), discussion_ok=_flag(post, "discussion"), breaking_ok=breaking_ok(db, post))
    if not re_check["ok"]:
        post.status, post.hold_reason, post.checks = "held", re_check["reason"], re_check["checks"]
        decision["why"] = re_check["reason"]
        db.commit()
        return "held"
    out = await publish(db, s, post, settings, runtime, agent_id, now)
    decision["why"] = post.note or post.error or (f"X post {post.x_id}" if post.x_id else "")
    return out


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
    from app.newsdesk.engine import STOPPED, emergency_stop

    stop = emergency_stop(db)
    if stop:  # the moment of sending, as the News Desk's X and Telegram lanes: a stop switched on after the post was queued still holds it
        post.note = f"Not sent: {stop}"[:500]  # still queued (never claimed, never marked sent): it goes when the stop is lifted, if still fresh
        db.commit()
        logger.warning("trending: post %s not sent: %s", post.id, stop)
        return STOPPED
    if not _claim(db, post):
        return "already claimed"
    db.refresh(post)
    topic = db.get(TtTopic, post.topic_id)
    problems = final_problems(post)
    if problems:  # never sent, in dry run either: it is held with the reason, for the owner to see in Admin
        post.status, post.hold_reason, post.note = "held", f"Final check: {problems[0]}", "; ".join(problems)[:500]
        db.commit()
        logger.info("trending: post %s held by the final check: %s", post.id, "; ".join(problems))
        return "held"
    if not coordinate.x_live(db, s):
        why = "Trending's own dry-run switch" if not s.live else "the News Desk's X lane is in dry run"
        post.status, post.published_at, post.note = "dry_run", now, f"Dry run ({why}): recorded, not sent"
        if topic is not None:
            topic.status, topic.posted_at = "posted", now
        record_latency(s, post, topic, now)
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
    record_latency(s, post, topic, now)
    guard.record_attempt(req, None, None, "executed", reason="trending topics", external_id=x_id)
    _note(s, "x_last_ok", {"at": now.isoformat(), "post_id": post.id, "x_id": x_id})
    from app.newsdesk import memory  # the story's memory across every lane

    memory.remember(db, "x-trending", str(post.id), (topic.title if topic is not None else "") or (post.text or "").split("\n")[0], "", now)
    db.commit()
    logger.info("trending published %s (post %s)", x_id, post.id)
    return "published"


LATENCY_KEEP = 30


def record_latency(s: TtSettings, post: TtPost, topic: TtTopic | None, now: datetime) -> dict[str, Any]:
    """Measured, per post: event (first report) -> first seen by our scans -> tweet written -> sent (or recorded in dry run). On the
    topic (Admin), in the state's last LATENCY_KEEP (Admin's summary) and in the log."""
    tl = ((topic.parts or {}).get("timeline") or {}) if topic is not None else {}

    def mins(a: Any, b: datetime | None) -> int | None:
        try:
            at = _aware(datetime.fromisoformat(a) if isinstance(a, str) else a)
        except ValueError:
            return None
        return int((b - at).total_seconds() // 60) if at is not None and b is not None else None

    created = _aware(post.created_at)
    written = mins(created, now)
    lat = {"post_id": post.id, "status": post.status, "at": now.isoformat(), "event_to_publish_min": mins(tl.get("event_time"), now),
           "detect_to_publish_min": mins(tl.get("first_seen") or (topic.first_seen if topic is not None else None), now),
           # `now` is the scan's start and the tweet is stamped a moment later, in the same scan (production post 90: "written -> sent -1 min")
           "written_to_publish_min": max(0, written) if written is not None else None, "score": post.score}
    if topic is not None:
        topic.parts = {**(topic.parts or {}), "latency": lat}
    state = dict(s.state or {})
    state["latency"] = ([lat] + list(state.get("latency") or []))[:LATENCY_KEEP]
    s.state = state
    logger.info("trending latency post %s (%s): event -> sent %s min, first seen -> sent %s min, written -> sent %s min", post.id, post.status,
                lat["event_to_publish_min"], lat["detect_to_publish_min"], lat["written_to_publish_min"])
    return lat


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


# --- production evidence: the log lines ------------------------------------------------------------------------------------
# Owner, 2026-11-14: no authenticated admin access for automation, so each scan says what it did in a few INFO lines (Railway logs).
# Public headlines, scores and our own decisions only: no secrets, no tokens, no personal data (_safe redacts anything secret-looking).

_STARTUP_LOGGED = False
SECRETISH = re.compile(r"(?i)(bearer\s+\S+|\b(?:api[_-]?key|access[_-]?token|token|secret|password|passwd|authorization)\b\s*[=:]\s*\S+|"
                       r"\bsk-[A-Za-z0-9_-]{6,}|\b[A-Za-z0-9_\-]{32,}\b|[\w.+-]+@[\w-]+\.[\w.]+)")


def _safe(text: Any, n: int = 80) -> str:
    t = re.sub(r"\s+", " ", str(text or "")).strip()
    t = SECRETISH.sub("[redacted]", t)
    return t if len(t) <= n else t[: n - 1].rstrip() + "…"


def _model_writer(settings: Settings) -> bool:
    from app.security import credentials

    return bool(credentials.resolve("anthropic_api_key", settings) and getattr(settings, "newsdesk_model", ""))


def x_access_line(db: Session, s: TtSettings, settings: Settings) -> str:
    """Once per deploy: what X access production has (from the connection's last test and measured usage; no request, no secret)."""
    from app.terminal import feed as terminal_feed

    a = terminal_feed.x_access(db, settings)
    caps = ",".join(f"{k}={v.get('status')}" for k, v in sorted(a["capabilities"].items())) or "never tested"
    o = sources.merged(s.sources).get("x_accounts") or {}
    return (f"x intelligence access: configured={a['configured']} app_only={a['app_only']} user_context={a['user_context']} status={a['status']} "
            f"account=@{a['account'] or '-'} capabilities[{caps}] usage_month=${a['usage_month'].get('cost', 0)} posts_month={int(a['usage_month'].get('posts', 0))} "
            f"budget=${a['budget']['daily_usd']}/day ${a['budget']['monthly_usd']}/month watchlist={'on' if o.get('on') else 'off'} accounts={len(o.get('accounts') or [])}")


def startup_line(db: Session, s: TtSettings, settings: Settings) -> str:
    """Once per process (per deploy): the effective settings."""
    srcs = sources.merged(s.sources)
    on = ",".join(k for k, v in srcs.items() if v.get("on")) or "none"
    return (f"trending startup: enabled={'on' if s.enabled else 'off'} mode={s.mode} auto={'on' if automatic(s) else 'off'} "
            f"hot_threshold={s.hot_score:g} suggest_threshold={s.suggest_score:g} no_growth_cap={rank.NO_GROWTH_CAP:g} daily_max={s.max_per_day} "
            f"min_gap={s.gap_minutes}min newsdesk_gap={s.newsdesk_gap_minutes}min shared_cap={s.shared_daily_cap}/24h "
            f"live_switch={'on' if s.live else 'off'} newsdesk_x={'live' if coordinate.newsdesk_live(db) else 'dry_run'} "
            f"x_live={coordinate.x_live(db, s)} stop={'on' if s.kill_switch else 'off'} sources={on} writer={'model' if _model_writer(settings) else 'rules'}")


# --- major incidents: what Admin shows about each one (owner, 2026-11-17: "show why major stories were missed") ---------------------
INCIDENT_KEEP = timedelta(hours=72)
INCIDENT_MAX = 60


def decision_of(t: TtTopic, p: TtPost | None, s: TtSettings) -> tuple[str, str]:
    """(status, reason) for a topic: its tweet's status and why, or why it has no tweet."""
    pa = t.parts or {}
    if p is None:
        if t.score < s.suggest_score:
            return "no tweet", f"scores {t.score:g}, below the suggestion line ({s.suggest_score:g})"
        if pa.get("skip"):
            return "no tweet", str(pa["skip"])
        return "no tweet", "over this scan's limit of new suggestions"
    why = p.hold_reason if p.status == "held" else p.note
    if p.status == "pending" and not why:
        why = f"waiting for approval (below the automatic line {s.hot_score:g})" if t.score < s.hot_score else "waiting for approval"
    return p.status, str(why or "")


def watch_incidents(db: Session, s: TtSettings, topics: list[TtTopic], now: datetime) -> list[str]:
    """Record every incident topic (parts.incident) in state["incidents"]: when it happened, when we first saw it, its score and peak, and
    what happened to it (posted, queued, held and why, below the line). One log line whenever an incident's status changes."""
    state = dict(s.state or {})
    book: dict[str, dict[str, Any]] = {str(e.get("topic_id")): e for e in state.get("incidents") or [] if isinstance(e, dict) and e.get("major")}
    # major incidents only (production 2026-11-17 05:14: "Did The Simpsons predict a mass power outage?" and blackout-rumour pieces are not incidents)
    rows = [t for t in topics if ((t.parts or {}).get("incident") or {}).get("major")]
    posts: dict[int, TtPost] = {}
    if rows:
        for p in db.scalars(select(TtPost).where(TtPost.topic_id.in_([t.id for t in rows])).order_by(TtPost.id)):
            posts[p.topic_id] = p
    lines = []
    for t in rows:
        pa = t.parts or {}
        inc = pa["incident"]
        tl = pa.get("timeline") or {}
        status, reason = decision_of(t, posts.get(t.id), s)
        key = str(t.id)
        old = book.get(key) or {}
        first = _aware(t.first_seen) or now
        event = tl.get("event_time") or first.isoformat()
        try:
            lag = int((first - _aware(datetime.fromisoformat(event))).total_seconds() // 60)  # type: ignore[operator]
        except (TypeError, ValueError):
            lag = None
        p = posts.get(t.id)
        entry = {"topic_id": t.id, "title": t.title[:200], "category": t.category, "kind": inc.get("kind"), "label": inc.get("label"),
                 "entities": inc.get("entities") or [], "major": bool(inc.get("major")), "verified": bool(inc.get("verified")), "evidence": inc.get("evidence"),
                 "event_time": event, "first_detected": old.get("first_detected") or first.isoformat(), "detect_lag_min": old.get("detect_lag_min", lag),
                 "score": t.score, "peak_score": max(float(old.get("peak_score") or 0), float(t.peak_score or 0), float(t.score or 0)),
                 "outlets": tl.get("outlets", 0), "status": status, "reason": reason[:240], "why": str(pa.get("why") or "")[:300],
                 "post_id": p.id if p else None, "posted_at": _iso(p.published_at) if p and p.published_at else old.get("posted_at"),
                 "first_line_at": old.get("first_line_at") or (now.isoformat() if t.score >= s.hot_score else None),
                 "updated": now.isoformat()}
        if (old.get("status"), old.get("reason")) != (status, entry["reason"]):
            lines.append(f"trending incident: [{inc.get('label')}] \"{_safe(t.title, 80)}\" event {str(event)[11:16]} UTC, first seen "
                         f"{entry['first_detected'][11:16]} UTC (+{lag if lag is not None else '?'} min), score {t.score:g} (peak {entry['peak_score']:g}), "
                         f"{'verified' if entry['verified'] else 'unverified'} ({_safe(inc.get('evidence'), 60)}) | {status}" + (f" ({_safe(reason, 90)})" if reason else ""))
        book[key] = entry
    keep = [e for e in book.values() if _aware(datetime.fromisoformat(str(e.get("updated")))) >= now - INCIDENT_KEEP]  # type: ignore[operator]
    keep.sort(key=lambda e: str(e.get("first_detected") or ""), reverse=True)
    state["incidents"] = keep[:INCIDENT_MAX]
    s.state = state
    db.commit()
    return lines


# --- the latency ledger (owner, 2026-11-17: "for the next 10 significant events record event time, first source publication, first
# detection, verification time, publication time or rejection reason; measure real detection and publication latency") ---------------
# A significant event: a topic that reached the automatic line (peak >= hot_score) or a major incident. Every time is measured from our
# own data: first_report = the earliest publication of anything in the topic (an official status page's start time when it has one),
# first_source = the earliest reliable outlet's publication, detected = our first scan that read it, verified = the first scan that saw
# enough evidence (an incident: incidents.verified; otherwise two independent outlets or an official source), published = the post's
# time on X (or dry run), or the News Desk post that covered it, else the reason it never went out.
LEDGER_KEEP = timedelta(days=7)
LEDGER_MAX = 60
TERMINAL = ("published", "dry_run", "uncertain", "expired", "rejected", "failed")


def _mins(a: Any, b: Any) -> int | None:
    try:
        x = _aware(datetime.fromisoformat(str(a)))
        y = _aware(datetime.fromisoformat(str(b)))
    except (TypeError, ValueError):
        return None
    return int((y - x).total_seconds() // 60) if x and y else None  # type: ignore[operator]


def _covered_by_desk(db: Session, reason: str) -> dict[str, Any] | None:
    from app.models.newsdesk import NdPost

    m = re.match(r"covered by the News Desk \(post (\d+)", reason or "")
    if not m:
        return None
    p = db.get(NdPost, int(m.group(1)))
    if p is None:
        return None
    return {"post": p.id, "status": p.status, "at": _iso(_aware(p.published_at)) if p.published_at else None}


def watch_ledger(db: Session, s: TtSettings, topics: list[TtTopic], now: datetime) -> list[str]:
    state = dict(s.state or {})
    started = state.get("ledger_started") or now.isoformat()
    book: dict[str, dict[str, Any]] = {str(e.get("topic_id")): e for e in state.get("ledger") or [] if isinstance(e, dict)}
    pool = {t.id: t for t in topics}
    for t in db.scalars(select(TtTopic).where(TtTopic.last_seen >= now - timedelta(hours=72), TtTopic.peak_score >= s.hot_score)):
        pool.setdefault(t.id, t)
    for k in list(book):  # keep following every entry until it is final
        tid = int(k)
        if tid not in pool and (t := db.get(TtTopic, tid)) is not None:
            pool[tid] = t
    rows = [t for t in pool.values() if (t.peak_score or 0) >= s.hot_score or ((t.parts or {}).get("incident") or {}).get("major") or _fast(t) or str(t.id) in book]
    posts: dict[int, TtPost] = {}
    if rows:
        for p in db.scalars(select(TtPost).where(TtPost.topic_id.in_([t.id for t in rows])).order_by(TtPost.id)):
            posts[p.topic_id] = p
    lines = []
    for t in rows:
        pa = t.parts or {}
        tl = pa.get("timeline") or {}
        inc = pa.get("incident") or {}
        key = str(t.id)
        old = book.get(key) or {}
        p = posts.get(t.id)
        status, reason = decision_of(t, p, s)
        verified_now = bool(inc.get("verified")) if inc else bool(tl.get("verified"))
        first_detected = _iso(_aware(t.first_seen)) or now.isoformat()
        backfilled = old.get("backfilled", _mins(first_detected, started) is not None and (_mins(first_detected, started) or 0) > 10)
        e = {"topic_id": t.id, "title": t.title[:200], "category": t.category, "kind": inc.get("label") or t.category,
             "entities": inc.get("entities") or [], "first_report": old.get("first_report") or tl.get("first_report") or tl.get("event_time"),
             "first_source": old.get("first_source") or tl.get("event_time"), "detected": old.get("detected") or first_detected,
             "verified": old.get("verified") or (now.isoformat() if verified_now and not backfilled else None),
             "crossed": old.get("crossed") or (now.isoformat() if (t.score or 0) >= s.hot_score and not backfilled else None),
             "peak_score": max(float(old.get("peak_score") or 0), float(t.peak_score or 0)), "score": t.score, "outlets": tl.get("outlets", 0),
             "status": status, "reason": reason[:240], "published": old.get("published"), "via": old.get("via"), "backfilled": backfilled,
             "updated": now.isoformat()}
        if e["verified"] is None and backfilled and verified_now:
            e["verified_note"] = "verified before the ledger started"
        if p is not None and p.status in ("published", "dry_run", "uncertain") and p.published_at:
            e["published"], e["via"] = _iso(_aware(p.published_at)), f"trend post {p.id} ({p.status})"
        elif (desk := _covered_by_desk(db, reason)) is not None and desk.get("at"):
            e["published"], e["via"] = desk["at"], f"News Desk post {desk['post']} ({desk['status']})"
        if e["first_report"] and (_mins(e["detected"], e["first_report"]) or 0) > 0:
            e["first_report"] = e["detected"]  # the topic row is older than its current signals: we saw it first
        base = e["first_report"]
        e["latency_min"] = {"detect": _mins(base, e["detected"]), "verify": _mins(base, e["verified"]) if e["verified"] else None,
                            "crossed": _mins(base, e["crossed"]) if e["crossed"] else None,
                            "publish": _mins(base, e["published"]) if e["published"] else None,
                            "detect_to_publish": _mins(e["detected"], e["published"]) if e["published"] else None}
        sig_old = (old.get("status"), old.get("reason"), old.get("verified"), old.get("published"))
        if sig_old != (e["status"], e["reason"], e["verified"], e["published"]):
            lt = e["latency_min"]
            lines.append(f"trending ledger: topic {t.id} [{e['kind']}] \"{_safe(t.title, 70)}\" report {str(base or '')[5:16]}Z"
                         f" | detected +{lt['detect']}m | verified {('+' + str(lt['verify']) + 'm') if lt['verify'] is not None else '-'}"
                         f" | crossed 75 {('+' + str(lt['crossed']) + 'm') if lt['crossed'] is not None else '-'}"
                         f" | published {('+' + str(lt['publish']) + 'm via ' + str(e['via'])) if lt['publish'] is not None else '-'}"
                         f" | peak {e['peak_score']:g} | {status}" + (f" ({_safe(reason, 80)})" if reason else "") + (" [backfilled]" if backfilled else ""))
        book[key] = e
    keep = [e for e in book.values() if _mins(e.get("updated"), now.isoformat()) is not None and (_mins(e.get("updated"), now.isoformat()) or 0) <= LEDGER_KEEP.total_seconds() / 60]
    keep.sort(key=lambda e: str(e.get("first_report") or e.get("detected") or ""), reverse=True)
    state["ledger"], state["ledger_started"] = keep[:LEDGER_MAX], started
    s.state = state
    db.commit()
    return lines


def ledger_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Medians over the entries measured live (not backfilled)."""
    live = [r for r in rows if not r.get("backfilled")]

    def stat(k: str) -> dict[str, int] | None:
        vals = sorted(v for r in live if isinstance(v := (r.get("latency_min") or {}).get(k), int))
        return {"median": vals[len(vals) // 2], "max": vals[-1], "n": len(vals)} if vals else None

    return {"events": len(rows), "measured_live": len(live), "detect": stat("detect"), "verify": stat("verify"), "crossed": stat("crossed"), "publish": stat("publish"),
            "published": sum(1 for r in rows if r.get("published")), "rows": rows}


def scan_log_lines(db: Session, s: TtSettings, read: dict[str, Any], topics: list[TtTopic], made: int, published: str | None,
                   decision: dict[str, Any], now: datetime) -> list[str]:
    """One summary line, one line per top-5 topic, and the publish decision."""
    health = read.get("health") or {}
    bits = []
    for name in sources.DEFAULT_SOURCES:
        h = health.get(name) or {}
        st = h.get("status") or "-"
        if st == "off":
            bits.append(f"{name}=off")
            continue
        scanned = h.get("last_scan") == now.isoformat()
        b = f"{name}={st}" + (f"/{int(h.get('items') or 0)} new {int(h.get('stored') or 0)}" if scanned else " (not due)")
        if h.get("problem") and scanned:
            b += f" [{_safe(h.get('problem'), 60)}]"
        bits.append(b)
    lines = [f"trending scan {now:%Y-%m-%dT%H:%MZ}: sources {'; '.join(bits)} | topics {len(topics)} | suggested {made}"]
    top = sorted(topics, key=lambda t: -(t.score or 0))[:5]
    posts: dict[int, TtPost] = {}
    if top:
        for p in db.scalars(select(TtPost).where(TtPost.topic_id.in_([t.id for t in top])).order_by(TtPost.id)):
            posts[p.topic_id] = p
    for i, t in enumerate(top, 1):
        pa = t.parts or {}
        g = pa.get("growth") or {}
        sens = pa.get("sensitive") or {}
        p = posts.get(t.id)
        if p is None:
            tw = "no tweet" + (f" (below {s.suggest_score:g})" if t.score < s.suggest_score
                               else f" ({pa['skip']})" if pa.get("skip") else " (over this scan's limit of new suggestions)")
        else:
            why = p.hold_reason if p.status == "held" else p.note
            tw = f"tweet {p.status}" + (f" ({_safe(why, 70)})" if why else "")
        growth = ",".join(g.get("evidence") or []) or ("none, capped" if g.get("capped") else "none")
        notes = f" ({_safe('; '.join(g.get('notes') or []), 60)})" if g.get("notes") else ""
        tl = pa.get("timeline") or {}
        lines.append(f"trending top{i}: {t.score:.1f} [{t.category}] \"{_safe(t.title, 70)}\" m{pa.get('velocity', 0):g} f{pa.get('freshness', 0):g} "
                     f"i{pa.get('impact', 0):g} s{pa.get('surprise', 0):g} r{pa.get('relevance', 0):g} e{pa.get('engagement', 0):g} x{pa.get('learned', 1):g} "
                     f"age={pa.get('age_hours', 0):g}h outlets={tl.get('outlets', 0)} {tl.get('items_per_hour', 0):g}/h"
                     + (" STALE" if pa.get("stale") else "") + f" items={len(t.signal_ids or [])} growth={growth}{notes}" + (f" sensitive={sens.get('kind')}/{sens.get('tier')}" if sens else "") + f" | {tw}")
    if published is None:
        lines.append(f"trending publish: none ({'automatic' if automatic(s) else 'approval mode'}, nothing queued)")
    else:
        lines.append(f"trending publish: {published} post={decision.get('post_id')} ({_safe(decision.get('why') or '-', 90)})")
    return lines


# --- tick ------------------------------------------------------------------------------------------------------------------


async def tick(db: Session, settings: Settings, runtime: Any, agent_id: int, now: datetime | None = None, *, transport: httpx.AsyncBaseTransport | None = None,
               writer_transport: Any = None, only: set[str] | None = None) -> dict[str, Any]:
    global _STARTUP_LOGGED
    now = now or utcnow()
    s = settings_row(db)
    db.commit()
    if not _STARTUP_LOGGED:
        _STARTUP_LOGGED = True
        try:
            logger.info(startup_line(db, s, settings))
            logger.info(x_access_line(db, s, settings))
        except Exception:  # noqa: BLE001
            logger.exception("trending: startup log failed")
    try:  # the owner's X Intelligence pilot (2026-11-18): switched on once, only if the X budget supports it
        from app.terminal import xintel

        if (line := xintel.activate_pilot(db, s, settings, now)) is not None:
            logger.info(line)
            db.commit()
    except Exception:  # noqa: BLE001 - the pilot never stops a scan
        logger.exception("trending: x pilot activation failed")
        db.rollback()
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
    from app.newsdesk import memory  # X Intelligence's early leads and their confirmation (no extra request: the scan's own sources)

    memory.watch_leads(db, topics, now)
    try:
        from app.terminal import xintel

        st = dict(s.state or {})
        xi = dict(st.get("x_intel") or {})
        for line in xintel.pilot_ledger(xi, topics, now):
            logger.info(line)
        st["x_intel"] = xi
        s.state = st
    except Exception:  # noqa: BLE001
        logger.exception("trending: x pilot ledger failed")
    made = 0
    for t in sorted(topics, key=lambda t: (not _fast(t), -t.score)):
        if made >= MAX_NEW_PER_SCAN or (t.score < s.suggest_score and not _fast(t)):
            break
        skip = ("not active" if t.status != "active" else "already has a tweet" if _has_post(db, t, s, now)
                else "same story as another topic's tweet" if _recent_duplicate(db, t, s, now) else "")
        if skip:
            t.parts = {**(t.parts or {}), "skip": skip}  # the scan log says why a hot topic has no tweet
            continue
        p = await suggest(db, s, settings, t, now, writer_transport=writer_transport)
        made += 1
        if _fast(t):
            term = (t.parts or {}).get("terminal") or {}
            logger.info("terminal fast lane: topic %s \"%s\" -> post %s %s (%s) | evidence %s viral %s | %s", t.id, _safe(t.title, 70), p.id, p.status,
                        _safe(p.hold_reason or p.note or "", 80), (term.get("evidence") or {}).get("score"), (term.get("viral") or {}).get("score"),
                        _safe(term.get("fast_why") or "", 120))
    db.commit()
    decision: dict[str, Any] = {}
    published = await publish_due(db, s, settings, runtime, agent_id, now, decision=decision) if automatic(s) or _any_approved(db) else None
    alerts_sent = await check_health(db, s, settings, now)
    try:
        from app.terminal import feed as terminal_feed

        for line in await terminal_feed.alerts(db, settings, topics, now):
            logger.info(line)
    except Exception:  # noqa: BLE001 - the Terminal's alerts never break a scan
        logger.exception("trending: terminal alerts failed")
    db.refresh(s)
    try:
        for line in watch_incidents(db, s, topics, now):
            logger.info(line)
    except Exception:  # noqa: BLE001 - the incident record never breaks a scan
        logger.exception("trending: incident watch failed")
    try:
        for line in watch_ledger(db, s, topics, now):
            logger.info(line)
    except Exception:  # noqa: BLE001 - the ledger never breaks a scan
        logger.exception("trending: ledger failed")
    out.update({"stored": read["stored"], "topics": len(topics), "suggested": made, "published": published, "alerts": alerts_sent})
    _note(s, "last_run", {"at": now.isoformat(), **out})
    db.commit()
    try:
        for line in scan_log_lines(db, s, read, topics, made, published, decision, now):
            logger.info(line)
    except Exception:  # noqa: BLE001 - the log line never breaks a scan
        logger.exception("trending: scan log failed")
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
                        buzz_ok=_flag(post, "buzz"), discussion_ok=_flag(post, "discussion"), breaking_ok=breaking_ok(db, post))
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
                        buzz_ok=_flag(post, "buzz"), discussion_ok=_flag(post, "discussion"), breaking_ok=breaking_ok(db, post))
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
            "published_at": _iso(p.published_at), "expires_at": _iso(p.expires_at), "cashtags": gates.cashtags(p.text),
            "sensitive": sensitive.classify(p.source_text) if p.source_text else None}


def topic_out(t: TtTopic, post: TtPost | None) -> dict[str, Any]:
    return {"id": t.id, "title": t.title, "category": t.category, "status": t.status, "score": t.score, "peak_score": t.peak_score, "parts": t.parts,
            "sources": t.sources, "metrics": t.metrics, "first_seen": _iso(t.first_seen), "last_seen": _iso(t.last_seen), "posted_at": _iso(t.posted_at),
            "post": post_out(post) if post else None}


def settings_out(s: TtSettings) -> dict[str, Any]:
    return {"enabled": s.enabled, "mode": s.mode, "automatic": automatic(s), "auto_confirmed_at": _iso(s.auto_confirmed_at), "live": s.live, "kill_switch": s.kill_switch,
            "hot_score": s.hot_score, "suggest_score": s.suggest_score, "max_per_day": s.max_per_day, "gap_minutes": s.gap_minutes,
            "topic_cooldown_hours": s.topic_cooldown_hours, "newsdesk_gap_minutes": s.newsdesk_gap_minutes, "shared_daily_cap": s.shared_daily_cap,
            "categories": {c: bool((s.categories or {}).get(c, True)) for c in rank.CATEGORIES}, "sources": sources.merged(s.sources)}


def latency_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Median and worst of the measured latencies (minutes), with the recent rows."""
    def stat(key: str) -> dict[str, int] | None:
        vals = sorted(int(r[key]) for r in rows if isinstance(r.get(key), int))
        return {"median": vals[len(vals) // 2], "max": vals[-1], "n": len(vals)} if vals else None

    return {"event_to_publish": stat("event_to_publish_min"), "detect_to_publish": stat("detect_to_publish_min"),
            "written_to_publish": stat("written_to_publish_min"), "recent": rows[:10]}


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
            "learning": learning.report(db, now), "latency": latency_summary(state.get("latency") or []),
            "incidents": state.get("incidents") or [], "missed": postmortem.MISSED, "ledger": ledger_summary(state.get("ledger") or []), "writer": "model" if credentials.resolve("anthropic_api_key", settings) and settings.newsdesk_model else "rules",
            "x": {"configured": creds.configured, "can_write": creds.user_context, "status": xstate.status, "last_ok": state.get("x_last_ok"), "last_error": state.get("x_last_error")},
            "cost": {"per_post_usd": float(settings.x_write_cost_estimate_usd), "x_trends_per_request_usd": 0.010, "x_post_read_usd": 0.005}}
