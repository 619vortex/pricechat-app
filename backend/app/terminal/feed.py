"""The Terminal's dashboard: one read over both desks (Trending's topics and posts, the News Desk's events and posts, the incident watch,
the latency ledger, source health) and the owner's one-click actions, which all go through Trending's own approve / edit / publish."""

import logging
import re
from datetime import datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.settings import Settings
from app.db.base import utcnow
from app.models.newsdesk import NdEvent, NdPost, NdSettings
from app.models.trending import TtPost, TtSettings, TtSignal, TtTopic
from app.terminal import intel
from app.trending import coordinate, engine, gates, rank, writer

logger = logging.getLogger(__name__)

WINDOW = timedelta(hours=24)
LANES = ("breaking", "emerging", "disruption", "rumor", "watch", "published", "rejected")
ALERT_VIRAL = 75.0  # a rumour this viral is worth a person's look now


def _iso(d: datetime | None) -> str | None:
    return engine._iso(d) if d else None


def _term(t: TtTopic) -> dict[str, Any]:
    term = (t.parts or {}).get("terminal")
    if isinstance(term, dict) and term.get("evidence"):
        return term
    return engine.terminal_view(t)  # a topic scored before the Terminal existed


def lane_of(t: TtTopic, term: dict[str, Any], post: TtPost | None) -> str:
    if post is not None and post.status in ("published", "dry_run", "uncertain", "publishing"):
        return "published"
    if t.status == "dismissed" or (post is not None and post.status == "rejected"):
        return "rejected"
    if term.get("fast"):
        return "breaking"
    if ((t.parts or {}).get("incident") or {}).get("major") or any(intel.official(x) for x in t.sources or []):
        return "disruption"
    if term.get("rumor"):
        return "rumor"
    if term.get("ahead") or ((t.parts or {}).get("growth") or {}).get("measured"):
        return "emerging"
    return "watch"


def story_out(t: TtTopic, post: TtPost | None, s: TtSettings) -> dict[str, Any]:
    term = _term(t)
    pa = t.parts or {}
    tl = pa.get("timeline") or {}
    status, reason = engine.decision_of(t, post, s)
    links = [{"outlet": x.get("outlet"), "title": x.get("title"), "url": x.get("url"), "source": x.get("source"), "reliable": bool(x.get("reliable")),
              "official": intel.official(x), "at": x.get("at")} for x in (t.sources or [])[:12]]
    return {"id": t.id, "title": t.title, "category": t.category, "topic_status": t.status, "lane": lane_of(t, term, post),
            "trend_score": t.score, "peak_score": t.peak_score, "viral": term.get("viral"), "evidence": term.get("evidence"),
            "rumor": bool(term.get("rumor")), "ahead_of_mainstream": bool(term.get("ahead")), "fast": bool(term.get("fast")), "fast_why": term.get("fast_why"),
            "breaking_ok": bool(term.get("breaking_ok")), "first_report": tl.get("first_report") or tl.get("event_time"), "detected": _iso(t.first_seen),
            "last_update": tl.get("last_update"), "age_hours": pa.get("age_hours"), "stale": pa.get("stale"), "why": pa.get("why"),
            "incident": pa.get("incident"), "sensitive": pa.get("sensitive"), "sources": links, "decision": {"status": status, "reason": reason},
            "post": engine.post_out(post) if post is not None else None}


def _posts_for(db: Session, ids: list[int]) -> dict[int, TtPost]:
    out: dict[int, TtPost] = {}
    if ids:
        for p in db.scalars(select(TtPost).where(TtPost.topic_id.in_(ids)).order_by(TtPost.id)):
            out[p.topic_id] = p
    return out


def desk_event_out(e: NdEvent) -> dict[str, Any]:
    return {"id": e.id, "title": e.title, "headline": e.headline, "status": e.status, "x_status": e.x_status, "topic": e.topic, "category": e.category,
            "evidence": e.evidence, "virality": e.virality, "why": e.why, "slot": e.slot, "first_seen": _iso(e.first_seen), "released_at": _iso(e.released_at),
            "sources": [{"name": x.get("name"), "url": x.get("url")} for x in (e.sources or [])[:4] if isinstance(x, dict)]}


def controls(db: Session, s: TtSettings) -> dict[str, Any]:
    nd = db.get(NdSettings, 1)
    return {"newsdesk_mode": nd.mode if nd else "dry_run", "newsdesk_stop": bool(nd and nd.kill_switch), "newsdesk_automation": bool(nd and nd.automation),
            "trending_mode": s.mode, "trending_live": bool(s.live), "trending_stop": bool(s.kill_switch), "x_live": coordinate.x_live(db, s),
            "blocked": coordinate.desk_blocked(db, utcnow(), s), "fast_lane": {"evidence_min": intel.FAST_EVIDENCE, "max_age_min": intel.FAST_MAX_AGE_MIN,
            "extra_per_day": coordinate.FAST_EXTRA_PER_DAY, "gap_min": coordinate.FAST_GAP_MINUTES}}


def social_status(s: TtSettings) -> list[dict[str, Any]]:
    """What each social or search source can and can't do here, from Trending's own source health."""
    h = (s.state or {}).get("health") or {}
    notes = {"google": "Google Trends' official trending-now RSS (US, GB): search traffic tiers, measured every 5 minutes.",
             "reddit": "Reddit's Data API needs a free OAuth app (REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET); its public JSON answers 403 to servers.",
             "x_trends": "X API trends by location: $0.010 a request on X's pay-per-use plan. Off until the owner turns it on (paid).",
             "x_accounts": "Recent posts of major accounts via X's recent search: $0.005 per post read. Off until the owner turns it on (paid).",
             "hn": "Hacker News' official API: points and comments, every 15 minutes.",
             "gdelt": "GDELT DOC API: early reports on news sites worldwide, every 10 minutes. Momentum only, never evidence.",
             "usgs": "USGS real-time earthquake feed (official), every 5 minutes.", "gdacs": "GDACS orange and red disaster alerts (official), every 15 minutes.",
             "faa": "FAA airport status (official), every 5 minutes.", "status": "Providers' own status pages (official), every 5 minutes.",
             "news": "The News Desk's curated outlet feeds."}
    out = []
    for k, note in notes.items():
        x = h.get(k) or {}
        out.append({"key": k, "name": x.get("name") or k, "status": x.get("status") or "not read yet", "problem": x.get("problem"), "last_scan": x.get("last_scan"),
                    "items": x.get("items"), "note": note})
    return out


def overview(db: Session, settings: Settings, *, q: str = "", lane: str = "", category: str = "", min_viral: float = 0, min_evidence: float = 0,
             hours: int = 24, limit: int = 120) -> dict[str, Any]:
    now = utcnow()
    s = engine.settings_row(db)
    db.commit()
    since = now - timedelta(hours=max(1, min(hours, 72)))
    topics = list(db.scalars(select(TtTopic).where(TtTopic.last_seen >= since).order_by(TtTopic.last_seen.desc()).limit(600)))
    posts = _posts_for(db, [t.id for t in topics])
    rows = [story_out(t, posts.get(t.id), s) for t in topics]
    counts = {k: sum(1 for r in rows if r["lane"] == k) for k in LANES}
    needle = q.strip().lower()
    if needle:
        rows = [r for r in rows if needle in r["title"].lower() or any(needle in str(x.get("title") or "").lower() or needle in str(x.get("outlet") or "").lower() for x in r["sources"])]
    if lane:
        rows = [r for r in rows if r["lane"] == lane]
    if category:
        rows = [r for r in rows if r["category"] == category]
    rows = [r for r in rows if float((r["viral"] or {}).get("score") or 0) >= min_viral and float((r["evidence"] or {}).get("score") or 0) >= min_evidence]
    order = {"breaking": 0, "disruption": 1, "emerging": 2, "rumor": 3, "watch": 4, "published": 5, "rejected": 6}
    rows.sort(key=lambda r: (order.get(r["lane"], 9), -float((r["viral"] or {}).get("score") or 0), -(r["trend_score"] or 0)))
    events = [desk_event_out(e) for e in db.scalars(select(NdEvent).where(NdEvent.created_at >= now - WINDOW).order_by(NdEvent.id.desc()).limit(60))]
    published = []
    for p in db.scalars(select(NdPost).where(NdPost.published_at >= now - WINDOW, NdPost.status.in_(("published", "dry_run", "uncertain"))).order_by(NdPost.published_at.desc()).limit(30)):
        published.append({"lane": "newsdesk", "id": p.id, "text": p.text, "status": p.status, "at": _iso(p.published_at), "x_id": p.x_id, "priority": p.priority})
    for p in db.scalars(select(TtPost).where(TtPost.published_at >= now - WINDOW, TtPost.status.in_(("published", "dry_run", "uncertain"))).order_by(TtPost.published_at.desc()).limit(30)):
        published.append({"lane": "fast" if (p.angle or "").startswith("fast") else "trending", "id": p.id, "text": p.text, "status": p.status, "at": _iso(p.published_at),
                          "x_id": p.x_id, "topic_id": p.topic_id})
    published.sort(key=lambda x: str(x["at"] or ""), reverse=True)
    state = s.state or {}
    return {"now": now.isoformat(), "counts": counts, "stories": rows[:limit], "total": len(rows), "controls": controls(db, s),
            "desk_events": events, "published": published[:40], "incidents": (state.get("incidents") or [])[:20],
            "ledger": engine.ledger_summary(state.get("ledger") or []), "ledger_rows": (state.get("ledger") or [])[:30],
            "sources": social_status(s), "last_scan": (state.get("last_run") or {}).get("at"), "alerts": (state.get("terminal_alerts") or [])[-20:][::-1]}


# --- one story -------------------------------------------------------------------------------------------------------------------------


def timeline(db: Session, t: TtTopic) -> list[dict[str, Any]]:
    """How the story developed: every signal we hold for it, when it was published and when our scans first saw it, oldest first."""
    ids = [int(i) for i in (t.signal_ids or [])][-200:]
    out = []
    if ids:
        for x in db.scalars(select(TtSignal).where(TtSignal.id.in_(ids))):
            out.append({"at": _iso(x.published_at or x.first_seen), "seen": _iso(x.first_seen), "source": x.source, "outlet": x.outlet, "title": x.title,
                        "url": x.url, "reliable": bool(x.reliable), "official": bool((x.extra or {}).get("official")) or x.source in intel.OFFICIAL_SOURCES,
                        "engagement": x.engagement})
    out.sort(key=lambda e: str(e["at"] or ""))
    return out


def hooks_for(t: TtTopic) -> dict[str, Any]:
    """The rules' three hooks, each through Trending's gate, plus the model's (if the owner asked for them) as stored."""
    term = _term(t)
    b, reliable = engine.brief_of(t)
    lead = intel.lead_of(t.sources or [], t.title)
    outlet = str(lead.get("outlet") or "")
    ev = term.get("evidence") or intel.evidence(t.title, t.sources, t.parts)
    vi = term.get("viral") or {}
    out = []
    for h in intel.rule_hooks(str(lead.get("title") or t.title), outlet, t.category, ev, float(vi.get("score") or 0), (t.parts or {}).get("sensitive")):
        chk = intel.check_hook(h["text"], source_text=b.source_text if b else "", outlet=outlet, reliable_count=len(reliable), breaking_ok=intel.breaking_allowed(ev))
        out.append({**h, "writer": "rules", **chk})
    model = term.get("hooks") if isinstance(term.get("hooks"), list) else []
    return {"hooks": out + model, "rumor": bool(ev.get("rumor")), "breaking_ok": intel.breaking_allowed(ev)}


def story_detail(db: Session, t: TtTopic) -> dict[str, Any]:
    s = engine.settings_row(db)
    posts = list(db.scalars(select(TtPost).where(TtPost.topic_id == t.id).order_by(TtPost.id)))
    out = story_out(t, posts[-1] if posts else None, s)
    led = next((e for e in (s.state or {}).get("ledger") or [] if str(e.get("topic_id")) == str(t.id)), None)
    out.update({"timeline": timeline(db, t), "posts": [engine.post_out(p) for p in posts], "ledger": led, "parts": {k: v for k, v in (t.parts or {}).items() if k not in ("terminal",)},
                **hooks_for(t)})
    return out


MODEL_HOOKS = """You write three alternative X headlines for a breaking-news account (Price Chat) about ONE story, using ONLY the facts given.
Return exactly three lines, in this order, each under 200 characters, no hashtags, no emoji, no links:
1. BREAKING: urgent and plain (start with "BREAKING: " only if VERIFIED is yes, otherwise start with "JUST IN: ").
2. Curiosity-driven: makes the reader want the detail, without withholding the core fact and without bait like "you won't believe".
3. Bold: punchy and direct, still strictly factual.
Never add a figure, name, quote, cause, casualty number or claim that the facts don't contain. Keep every hedge the source uses
("reportedly", "says", "alleged"). Never present a rumour or allegation as established fact. No predictions, no advice."""


async def model_hooks(db: Session, settings: Settings, t: TtTopic, *, transport: httpx.AsyncBaseTransport | None = None) -> list[dict[str, Any]]:
    """Ask the News Desk's model for three hooks (only when the owner presses the button: one request), gate each, store them."""
    from app.newsdesk.writer import ANTHROPIC_URL
    from app.security import credentials

    key = credentials.resolve("anthropic_api_key", settings)
    if not key or not getattr(settings, "newsdesk_model", ""):
        raise ValueError("No model is set up (Anthropic key and the News Desk model): the rules' hooks are shown instead.")
    b, reliable = engine.brief_of(t)
    if b is None:
        raise ValueError("No reliable outlet backs this story yet: hooks are written only from a reliable report.")
    term = _term(t)
    ev = term.get("evidence") or {}
    facts = f"VERIFIED: {'yes' if intel.breaking_allowed(ev) else 'no'}\nOUTLET: {b.outlet}\nHEADLINE: {writer.headline(b.headline)}\nREPORT: {re.sub(r'<[^>]+>', ' ', b.summary)[:500]}"
    body = {"model": settings.newsdesk_model, "max_tokens": 300, "system": MODEL_HOOKS, "messages": [{"role": "user", "content": facts}]}
    try:
        async with httpx.AsyncClient(timeout=30, transport=transport) as client:
            r = await client.post(ANTHROPIC_URL, headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"}, json=body)
    except httpx.HTTPError as e:
        raise ValueError(f"The model didn't answer ({type(e).__name__}).") from None
    if r.status_code != 200:
        raise ValueError(f"The model answered HTTP {r.status_code}.")
    text = "".join(c.get("text", "") for c in r.json().get("content") or [] if isinstance(c, dict) and c.get("type") == "text")
    lines = [re.sub(r"^\s*(?:\d[.)]|[-*])\s*", "", ln).strip() for ln in text.splitlines() if ln.strip()][:3]
    kinds = ("breaking", "curiosity", "bold")
    out = []
    for kind, line in zip(kinds, lines):
        line = intel.with_credit(line, b.outlet)[:280]
        chk = intel.check_hook(line, source_text=b.source_text, outlet=b.outlet, reliable_count=len(reliable), breaking_ok=intel.breaking_allowed(ev))
        out.append({"kind": kind, "text": line, "writer": "model", **chk})
    t.parts = {**(t.parts or {}), "terminal": {**term, "hooks": out, "hooks_for": t.title}}
    db.commit()
    return out


# --- actions ---------------------------------------------------------------------------------------------------------------------------


def _open_post(db: Session, t: TtTopic) -> TtPost | None:
    return db.scalars(select(TtPost).where(TtPost.topic_id == t.id, TtPost.status.in_(("pending", "queued", "held"))).order_by(TtPost.id.desc()).limit(1)).first()


async def draft(db: Session, settings: Settings, t: TtTopic, text: str | None) -> TtPost:
    """The story's open tweet (writing one through Trending's writer and gates if it has none), with the owner's words if given."""
    s = engine.settings_row(db)
    post = _open_post(db, t)
    now = utcnow()
    if post is None:
        post = await engine.suggest(db, s, settings, t, now)
        if post.status == "queued":  # the owner is deciding: nothing goes out until they press publish
            post.status, post.approved_at = "pending", None
        db.commit()
    if text and text.strip() and text.strip() != post.text:
        result = engine.edit(db, post, text.strip())
        if not result["ok"]:
            raise ValueError(result["reason"])
    return post


async def publish_now(db: Session, settings: Settings, runtime: Any, agent_id: int, t: TtTopic, text: str | None) -> dict[str, Any]:
    """The owner's one-click publish: their decision replaces Trending's automatic line, daily limit and spacing; every stop, the one-story
    rule, the facts gate and the editorial bar still apply. In dry run it is recorded, not sent."""
    s = engine.settings_row(db)
    now = utcnow()
    if (why := coordinate.desk_blocked(db, now, s)) is not None:
        raise ValueError(f"Not published: {why}.")
    post = await draft(db, settings, t, text)
    if post.status == "held":
        raise ValueError(f"Not published: {post.hold_reason or 'a check failed'}. Edit the words until they pass.")
    titles = [str(x.get("title") or "") for x in post.sources or []]
    if (why := coordinate.newsdesk_conflict(db, titles, now, fast=True)) is not None:
        raise ValueError(f"Not published: {why}.")
    if (n := coordinate.x_posts_today(db, s, now))["total"] >= s.shared_daily_cap:
        raise ValueError(f"Not published: the shared X cap is reached ({n['total']}/{s.shared_daily_cap} in 24 h).")
    result = gates.gate(post.text, source_text=post.source_text, source_names=[x["name"] for x in post.sources or []][:1], reliable_count=len(post.sources or []),
                        buzz_ok=engine._flag(post, "buzz"), discussion_ok=engine._flag(post, "discussion"), breaking_ok=engine.breaking_ok(db, post))
    if not result["ok"]:
        post.status, post.hold_reason, post.checks = "held", result["reason"], result["checks"]
        db.commit()
        raise ValueError(f"Not published: {result['reason']}")
    post.status, post.approved_at, post.hold_reason, post.expires_at = "queued", now, None, now + engine.QUEUED_TTL
    post.note = "Published from the Terminal by the owner"
    db.commit()
    outcome = await engine.publish(db, s, post, settings, runtime, agent_id, now)
    db.refresh(post)
    logger.info("terminal publish: topic %s post %s -> %s (owner)", t.id, post.id, outcome)
    return {"outcome": outcome, "post": engine.post_out(post)}


def reject(db: Session, t: TtTopic, reason: str = "") -> dict[str, Any]:
    for p in db.scalars(select(TtPost).where(TtPost.topic_id == t.id, TtPost.status.in_(("pending", "queued", "held")))):
        p.status, p.note = "rejected", f"Rejected in the Terminal{': ' + reason if reason else ''}"
    t.status = "dismissed"
    t.parts = {**(t.parts or {}), "rejected": {"at": utcnow().isoformat(), "reason": reason[:300]}}
    db.commit()
    return {"ok": True}


# --- alerts ----------------------------------------------------------------------------------------------------------------------------


async def alerts(db: Session, settings: Settings, topics: list[TtTopic], now: datetime) -> list[str]:
    """Owner alerts (the existing owner channel, app/alerts): a story entering the fast lane, and a rumour viral enough to need a look.
    One alert per story and kind. Returns the lines logged."""
    s = engine.settings_row(db)
    state = dict(s.state or {})
    sent = [a for a in state.get("terminal_alerts") or [] if isinstance(a, dict)][-100:]
    done = {(a.get("topic_id"), a.get("kind")) for a in sent}
    lines = []
    for t in topics:
        term = (t.parts or {}).get("terminal") or {}
        kind = "fast" if term.get("fast") else "rumor" if term.get("rumor") and float((term.get("viral") or {}).get("score") or 0) >= ALERT_VIRAL else None
        if kind is None or (t.id, kind) in done:
            continue
        ev, vi = term.get("evidence") or {}, term.get("viral") or {}
        text = (f"Price Chat Terminal: {'BREAKING (fast lane)' if kind == 'fast' else 'Rumour needs review'}\n{t.title}\n"
                f"Viral {vi.get('score')} | Evidence {ev.get('score')} ({ev.get('level')})\n"
                + (term.get("fast_why") or "; ".join(ev.get("reasons") or [])[:200]) + "\nAdmin > Price Chat Terminal")
        try:
            ok = await engine._alert(db, settings, f"terminal:{kind}:{t.id}", text, now, open_=None)
        except Exception:  # noqa: BLE001 - an alert never stops a scan
            logger.exception("terminal: alert failed")
            ok = False
        sent.append({"topic_id": t.id, "kind": kind, "title": t.title[:160], "at": now.isoformat(), "sent": bool(ok)})
        done.add((t.id, kind))
        lines.append(f"terminal alert: {kind} topic {t.id} \"{engine._safe(t.title, 70)}\" sent={bool(ok)}")
    if lines:
        state["terminal_alerts"] = sent[-100:]
        s.state = state
        db.commit()
    return lines
