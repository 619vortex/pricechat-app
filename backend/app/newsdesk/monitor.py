"""Admin's World Monitor (owner, 2026-11-11: "add the World Monitor to the admin panel, and a scanning feature so we can see what's
happening and what it is going to tweet/post").

Two read-only views; reading them changes nothing, the desk decides as before:

- scan: every story the desk read from its feeds in the last hours, newest first, judged exactly as the desk judges it (chaos.candidates,
  then events.important for world news that isn't market chaos): verified (and on what evidence, which outlets confirm it), held (waiting
  for a second outlet or an official source) or rejected (why), whether a World Monitor catalog outlet carried it, and what became of it
  (a candidate, posted, merged as a repeat). Plus the feeds' health and the World Monitor outlets' share of what was found.
- upcoming: what the desk is about to post, in the order it would go: the exact Telegram caption, the exact X post (with its X length), the
  links, the verification line and who wrote the words (app/newsdesk/network on a detached copy of each event), and the picture URL.
  Price checks in the scan use no live quotes (a market claim shows as unchecked here; the desk checks it with prices when it decides).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.newsdesk import NdEvent, NdStory
from app.newsdesk import chaos, editorial, events, network, sources

logger = logging.getLogger(__name__)
SCAN_LIMIT = 200
_pkg_cache: dict[tuple[int, str], dict[str, Any]] = {}


def _iso(t: datetime | None) -> str | None:
    return t.isoformat() if t else None


def scan(db: Session, now: datetime, hours: int = 12, categories: dict[str, bool] | None = None, desk_state: dict[str, Any] | None = None) -> dict[str, Any]:
    p = events.policy(desk_state)
    rows = list(db.scalars(select(NdStory).where(NdStory.created_at >= now - timedelta(hours=36))))
    ready, other = chaos.candidates(rows, {}, now, max_age=timedelta(hours=hours), categories=categories)
    promoted = {v.story.id: v for v in events.important(other, rows, now)}
    verdicts = {v.story.id: v for v in [*ready, *other]}
    verdicts.update(promoted)
    by_key = {e.key: e for e in db.scalars(select(NdEvent).where(NdEvent.created_at >= now - timedelta(hours=hours + 36)))}
    out = []
    for story_id, v in verdicts.items():
        s = v.story
        ev = by_key.get(f"story:{s.link}"[:200]) or by_key.get(f"update:{s.link}"[:200])
        outlets = sorted({o.source for o in v.others} - {s.source})
        state, why = ("verified" if v.ok else v.status), v.why or ""
        if not v.ok and story_id not in promoted and editorial.admissible(why) and editorial.significance(s.title or "", s.summary or "") is not None:
            state, why = "held", "world news: waiting for an official source or a second outlet"  # what events.important will need
        out.append({"story": story_id, "title": s.title, "link": s.link, "source": s.source, "world_monitor": bool(sources.via(s.source)),
                    "published_at": _iso(s.published_at), "category": s.category, "state": state, "why": why, "evidence": v.evidence or "",
                    "confirmed_by": outlets, "world_monitor_confirms": [o for o in outlets if sources.via(o)], "important": story_id in promoted,
                    "roundup": network.is_roundup(s.title or ""),
                    "event": None if ev is None else {"id": ev.id, "status": ev.status, "score": float(ev.virality or 0), "telegram": ev.tg_status, "x": ev.x_status,
                                                      "why": (ev.why or "")[:300]}})
    out.sort(key=lambda r: r["published_at"] or "", reverse=True)
    out = out[:SCAN_LIMIT]
    from app.newsdesk import engine

    health = ((engine.settings_row(db).state or {}).get("feed_health") or {})
    feeds = [{"url": u, **h, "world_monitor": bool(sources.via(str(h.get("name") or "")))} for u, h in (health.get("feeds") or {}).items()]
    counts = {k: sum(1 for r in out if r["state"] == k) for k in ("verified", "held", "rejected")}
    return {"at": now.isoformat(), "hours": hours, "counts": counts, "world_monitor_found": sum(1 for r in out if r["world_monitor"]),
            "world_monitor_confirmed": sum(1 for r in out if r["state"] == "verified" and (r["world_monitor"] or r["world_monitor_confirms"])),
            "next_slot_dubai": events.slot_label(events.next_slot(now, p)), "stories": out,
            "feeds": sorted(feeds, key=lambda f: (not f["world_monitor"], f.get("name") or "")), "feeds_checked_at": health.get("at"),
            "note": "Market claims show as unchecked here: the desk checks them against live prices when it decides."}


def _copy(ev: NdEvent) -> NdEvent:
    c = NdEvent(key=ev.key, kind=ev.kind, title=ev.title, headline=ev.headline, summary=ev.summary, topic=ev.topic, label=ev.label, evidence=ev.evidence,
                category=ev.category, sources=list(ev.sources or []), facts=dict(ev.facts or {}), virality=ev.virality, released_at=ev.released_at,
                first_seen=ev.first_seen, slot=ev.slot, parent_id=ev.parent_id)
    c.id = ev.id
    return c


async def package_for(db: Session, ev: NdEvent, settings: Any = None) -> dict[str, Any]:
    """The words and links this event would post with: the stored package if Telegram already wrote it, else written now on a copy."""
    stored = network.stored(ev)
    if stored:
        return {**stored, "final": True}
    key = (ev.id, f"{ev.updated_at}|{ev.headline}")
    if key in _pkg_cache:
        return _pkg_cache[key]
    from app.newsdesk import engine
    from app.newsdesk.telegram import stories_of

    rows = await stories_of(db, ev)
    try:
        pkg = await network.package(_copy(ev), settings=settings, rows=rows, x_links=network.x_links_on(engine.settings_row(db).state))
        got = {**pkg.out(), "final": False}
    except Exception as e:  # noqa: BLE001 - shown, never raised: the monitor must not break Admin
        logger.info("newsdesk monitor: no package for event %s (%s)", ev.id, type(e).__name__)
        got = {"ok": False, "error": type(e).__name__, "final": False}
    if len(_pkg_cache) > 200:
        _pkg_cache.clear()
    _pkg_cache[key] = got
    return got


async def upcoming(db: Session, now: datetime, settings: Any = None, desk_state: dict[str, Any] | None = None, limit: int = 6) -> dict[str, Any]:
    """What goes out next: released posts still owed to a platform first, then the next slot's candidates in the desk's order."""
    from app.newsdesk import queue

    q = queue.build(db, now, desk_state)
    out = []
    for r in q["releasing"]:
        ev = db.get(NdEvent, r["event"])
        if ev is not None:
            out.append({"event": ev.id, "when": "now: released, being delivered", "will_post": not r["blocked"], "status": f"Telegram {ev.tg_status}, X {ev.x_status}",
                        "score": float(ev.virality or 0), "image": f"/api/x/newsdesk/editorial/preview/{ev.id}.jpg", "package": await package_for(db, ev, settings)})
    for c in q["candidates"]:
        if len(out) >= limit:
            break
        ev = db.get(NdEvent, c["event"])
        if ev is None:
            continue
        out.append({"event": ev.id, "when": f"{q['next_slot_dubai']} Dubai slot" if c["will_post"] else "not at the next slot", "will_post": c["will_post"],
                    "status": c["status"], "score": c["score"], "gates": c["gates"], "image": c["image"], "package": await package_for(db, ev, settings)})
    return {"at": now.isoformat(), "next_slot": q["next_slot"], "next_slot_dubai": q["next_slot_dubai"], "posts": out,
            "x_links": network.x_links_on((desk_state or {})), "simultaneous": "X posts the moment Telegram delivers (same words, links and picture)."}


__all__ = ["package_for", "scan", "upcoming"]
