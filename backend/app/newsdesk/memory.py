"""Persistent story memory (owner, 2026-11-19: "Build persistent story memory across Telegram, X, News Desk, Trending and X Intelligence.
Only publish follow-ups with substantial new information").

One NdStoryline per real-world story. Every lane writes to it when something actually goes out (a Telegram delivery, a News Desk or
Trending post on X, a released event) and X Intelligence writes its early leads (an X-only report, unverified) and their confirmation
(the first reliable outlet or official source). A storyline keeps the headlines already told, so "what is new?" is answered against
everything we said about the story, not only the last 7 days of posts the repeat gate reads.

Identity and novelty reuse the gate's own rules (newsworthy.event_of / same_event / development), so memory and gate never disagree on
what one event is. SHADOW: the publishing decision is still the existing gate's (owner, 2026-11-19: "preserve the current production
editorial rules ... and the ongoing 30-candidate validation"). followup() is recorded with every candidate and in the validation report;
ENFORCE turns it into a gate once the owner has seen it agree with the gate on real candidates. Nothing here raises.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.newsdesk import NdStoryline

logger = logging.getLogger(__name__)

ENFORCE = False  # shadow until the owner approves: the existing repeat gate decides
KEEP = timedelta(days=30)  # a storyline is matched for this long after it was last seen
MAX_TITLES = 12
MAX_SIGHTINGS = 40
TOLD = "told"
LEAD = "lead"
CONFIRMED = "confirmed"


def _aware(d: datetime | None) -> datetime | None:
    return None if d is None else (d if d.tzinfo else d.replace(tzinfo=timezone.utc))


@lru_cache(maxsize=8192)
def _event(title: str) -> Any:
    """A stored headline's event signature (headlines never change, so each is worked out once per process)."""
    from app.newsdesk import newsworthy

    return newsworthy.event_of(title)


def _norm(title: str) -> str:
    return " ".join(re.findall(r"[a-z0-9$%.]+", (title or "").lower()))


def _events(line: NdStoryline) -> list[Any]:
    titles = list((line.told or {}).get("titles") or []) or [line.title]
    return [_event(t) for t in titles]


def find(db: Session, title: str, summary: str = "", now: datetime | None = None) -> NdStoryline | None:
    """The storyline this headline belongs to, or None: the same event by the gate's own rule, seen in the last KEEP."""
    from app.newsdesk import newsworthy

    now = now or utcnow()
    ev = newsworthy.event_of(title, summary)
    norm = _norm(title)
    for line in db.scalars(select(NdStoryline).where(NdStoryline.last_at >= now - KEEP).order_by(NdStoryline.last_at.desc()).limit(300)):
        titles = list((line.told or {}).get("titles") or []) + [str(s.get("title") or "") for s in (line.sightings or [])[-6:]]
        if norm in {_norm(t) for t in titles} or any(newsworthy.same_event(ev, _event(t)) for t in dict.fromkeys(x for x in titles if x)):
            return line
    return None


def remember(db: Session, lane: str, ref: str, title: str, summary: str = "", at: datetime | None = None, *, status: str = TOLD,
             by: str = "") -> NdStoryline | None:
    """Record one sighting: `told` (it went out on `lane`), `lead` (an unverified X Intelligence report) or `confirmed` (a reliable outlet
    or official source after a lead). Idempotent per (lane, ref, status). Never raises."""
    try:
        from app.newsdesk import newsworthy

        at = _aware(at) or utcnow()
        title = (title or "").strip()[:300]
        if not title:
            return None
        line = find(db, title, summary, at)
        if line is None:
            ev = newsworthy.event_of(title, summary)
            line = NdStoryline(title=title, family=ev.family[:24], anchors=sorted(ev.anchors)[:20], regions=sorted(ev.regions)[:10],
                               states=sorted(ev.states)[:10], told={"titles": []}, sightings=[], first_at=at, last_at=at)
            with db.begin_nested():
                db.add(line)
        seen = list(line.sightings or [])
        if any(s.get("lane") == lane and str(s.get("ref")) == str(ref) and s.get("status") == status for s in seen):
            return line
        seen.append({"lane": lane, "ref": str(ref), "at": at.isoformat(), "title": title[:200], "status": status, **({"by": by[:80]} if by else {})})
        line.sightings = seen[-MAX_SIGHTINGS:]
        line.last_at = max(_aware(line.last_at) or at, at)
        if status == TOLD:
            told = dict(line.told or {})
            titles = [t for t in told.get("titles") or [] if t != title]
            told["titles"] = (titles + [title])[-MAX_TITLES:]
            line.told = told
            if line.told_at is None:
                line.told_at = at
            if line.lead_at is not None and line.confirmed_at is None:
                line.confirmed_at = at  # it went out, so it passed verification
        elif status == LEAD:
            if line.lead_at is None and line.told_at is None:
                line.lead_at, line.lead_by = at, by[:80]
        elif status == CONFIRMED and line.lead_at is not None and line.confirmed_at is None:
            line.confirmed_at = max(at, _aware(line.lead_at) or at)
        return line
    except Exception as e:  # noqa: BLE001 - memory never stops a post
        logger.info("story memory: %s %s not recorded (%s)", lane, ref, type(e).__name__)
        return None


def followup(db: Session, title: str, summary: str = "", now: datetime | None = None) -> dict[str, Any]:
    """What memory says about a candidate: a new story, a follow-up with substantial new information (and what it is), or a follow-up with
    nothing new. Shadow while ENFORCE is False."""
    try:
        from app.newsdesk import newsworthy

        now = now or utcnow()
        line = find(db, title, summary, now)
        if line is None or line.told_at is None:
            return {"verdict": "new story", "storyline": line.id if line else None, "enforced": ENFORCE,
                    **({"lead": {"at": _aware(line.lead_at).isoformat(), "by": line.lead_by}} if line is not None and line.lead_at else {})}
        told_ago = int(((now - (_aware(line.told_at) or now)).total_seconds()) // 3600)
        lanes = sorted({str(s.get("lane")) for s in line.sightings or [] if s.get("status") == TOLD})
        new = newsworthy.development(newsworthy.event_of(title, summary), _events(line), f"{title} {summary or ''}")
        base = {"storyline": line.id, "first_told": str(line.title)[:160], "told_hours_ago": told_ago, "told_on": lanes,
                "times_told": len((line.told or {}).get("titles") or []), "enforced": ENFORCE}
        if new:
            return {"verdict": "follow-up with new information", "new": new, **base}
        return {"verdict": "follow-up with nothing substantially new", "new": "", **base}
    except Exception as e:  # noqa: BLE001
        logger.info("story memory: follow-up check failed (%s)", type(e).__name__)
        return {"verdict": "unknown", "error": type(e).__name__, "enforced": ENFORCE}


def disagrees(memory: dict[str, Any], decision: str) -> str:
    """The gate published what memory calls a follow-up with nothing new (or refused a substantial development): worth a look."""
    v = str(memory.get("verdict") or "")
    if decision == "PUBLISH" and v == "follow-up with nothing substantially new":
        return f"story memory: already told {memory.get('times_told')}x ({', '.join(memory.get('told_on') or [])}) and nothing substantially new"
    return ""


# ------------------------------------------------------------------------------------------------------------------ X Intelligence leads
def _at(s: dict[str, Any]) -> datetime | None:
    raw = s.get("at")
    if not raw:
        return None
    try:
        return _aware(datetime.fromisoformat(str(raw).replace("Z", "+00:00")))
    except ValueError:
        return None


def watch_leads(db: Session, topics: list[Any], now: datetime | None = None) -> int:
    """After each Trending scan: a topic first reported on X (X Intelligence) is an early LEAD until a reliable outlet or official source
    reports it, which CONFIRMS it. Both go into the story's memory with their times, so the Terminal can tell leads from verified facts
    and show how far ahead the X watchlist was. Reads what the scan already has: no extra X request."""
    n = 0
    now = now or utcnow()
    try:
        for t in topics:
            srcs = [s for s in (t.sources or []) if isinstance(s, dict)]
            xs = sorted((s for s in srcs if s.get("source") == "x_accounts" and _at(s)), key=lambda s: _at(s))  # type: ignore[arg-type, return-value]
            if not xs:
                continue
            first = xs[0]
            lead_at = _at(first)
            reliable = sorted((s for s in srcs if s.get("source") != "x_accounts" and (s.get("reliable") or s.get("official")) and _at(s)),
                              key=lambda s: _at(s))  # type: ignore[arg-type, return-value]
            if reliable and (_at(reliable[0]) or now) <= (lead_at or now):
                continue  # the outlets had it first: not an X lead
            by = f"@{first.get('outlet') or first.get('author') or '?'}".replace("@@", "@")
            line = remember(db, "x-intel", f"topic:{t.id}", str(t.title or first.get("title") or ""), at=lead_at, status=LEAD, by=by)
            n += line is not None
            if reliable and line is not None:
                remember(db, "x-intel", f"topic:{t.id}", str(reliable[0].get("title") or t.title or ""), at=_at(reliable[0]), status=CONFIRMED,
                         by=str(reliable[0].get("outlet") or ""))
        db.flush()
    except Exception as e:  # noqa: BLE001
        logger.info("story memory: leads not recorded (%s)", type(e).__name__)
    return n


# ------------------------------------------------------------------------------------------------------------------ what Admin shows
def summary(db: Session, now: datetime | None = None, days: int = 7) -> dict[str, Any]:
    now = now or utcnow()
    rows = list(db.scalars(select(NdStoryline).where(NdStoryline.last_at >= now - timedelta(days=days)).order_by(NdStoryline.last_at.desc())))
    told = [r for r in rows if r.told_at]
    leads = [r for r in rows if r.lead_at]
    confirmed = [r for r in leads if r.confirmed_at]
    minutes = sorted(int(((_aware(r.confirmed_at) or now) - (_aware(r.lead_at) or now)).total_seconds() // 60) for r in confirmed)
    by: dict[str, list[int]] = {}
    for r in leads:
        by.setdefault(r.lead_by or "?", []).append(1 if r.confirmed_at else 0)
    return {
        "enforced": ENFORCE, "days": days, "storylines": len(rows), "told": len(told),
        "followed_up": sum(1 for r in told if len((r.told or {}).get("titles") or []) > 1),
        "leads": {"seen": len(leads), "confirmed": len(confirmed), "unconfirmed": len(leads) - len(confirmed),
                  "median_lead_minutes": minutes[len(minutes) // 2] if minutes else None,
                  "accounts": sorted(({"account": k, "leads": len(v), "confirmed": sum(v)} for k, v in by.items()),
                                     key=lambda a: (-a["confirmed"], -a["leads"]))[:8]},
        "recent": [{"id": r.id, "title": r.title, "told": len((r.told or {}).get("titles") or []), "lanes": sorted({str(s.get("lane")) for s in r.sightings or []}),
                    "lead": bool(r.lead_at), "confirmed": bool(r.confirmed_at), "last": (_aware(r.last_at) or now).isoformat()} for r in rows[:12]],
    }


def story_of(db: Session, title: str, summary: str = "") -> dict[str, Any] | None:
    """The Terminal story panel's "story so far": every sighting across lanes, newest last."""
    try:
        line = find(db, title, summary)
        if line is None:
            return None
        return {"id": line.id, "first": line.title, "told": len((line.told or {}).get("titles") or []), "sightings": list(line.sightings or [])[-10:],
                "lead_at": _aware(line.lead_at).isoformat() if line.lead_at else None, "lead_by": line.lead_by,
                "confirmed_at": _aware(line.confirmed_at).isoformat() if line.confirmed_at else None}
    except Exception:  # noqa: BLE001
        return None
