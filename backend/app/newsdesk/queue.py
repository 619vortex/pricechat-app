"""Admin's preview queue (owner, 2026-11-10): every candidate for the next slot as the post would look (the exact picture, the headline, the
credit), its score, each gate's result (dedup, evidence, the bar, the numbers check, the headline rules) and whether it will post. The owner
can block a candidate. Reading the queue changes nothing; the desk decides as before, at the slot.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.newsdesk import NdEvent, NdTelegramSettings
from app.newsdesk import checks, editorial, events, visual

logger = logging.getLogger(__name__)
SHOWN = 10
_images: dict[tuple[int, str], tuple[bytes, dict[str, Any]]] = {}


def _gate(ok: bool, detail: str) -> dict[str, Any]:
    return {"ok": ok, "detail": detail}


def gates(ev: NdEvent, now: datetime, p: events.Policy, covered: list[editorial.Covered]) -> dict[str, dict[str, Any]]:
    f = ev.facts or {}
    out: dict[str, dict[str, Any]] = {}
    v = editorial.repeat_verdict(ev, covered, now)
    out["dedup"] = _gate(v.ok, v.reason or "a new story")
    penalty, held = editorial.diversity(ev, covered, now)
    out["diversity"] = _gate(not held, held or (f"{editorial.desk_of(ev)}: -{penalty:g} in the ranking for {penalty / editorial.DIVERSITY_PENALTY:g} "
                                                f"{editorial.desk_of(ev)} post(s) in 24 h" if penalty else f"{editorial.desk_of(ev)}: nothing from this desk in 24 h"))
    strong = ev.evidence in events.STRONG
    out["evidence"] = _gate(strong or ev.evidence == "attributed", {"official": "an official source", "two outlets": "two outlets", "price data": "our own prices",
                                                                    "exchange data": "exchange data", "attributed": "one outlet, naming who says so"}.get(ev.evidence, ev.evidence or "none"))
    held = events.held_score(ev, now, p)
    bar_ok = held >= p.regular_bar
    detail = f"scores {ev.virality:g}" + (f" (best {held:g})" if held > ev.virality else "") + f", bar {p.regular_bar}"
    if ev.kind == "market":
        big, why = editorial.exceptional(ev)
        bar_ok = bar_ok and ev.virality >= p.market_bar and big
        detail += f"; {why}"
    out["bar"] = _gate(bar_ok, detail)
    if len(f.get("points") or []) >= 10:
        stamp = f.get("as_of") or f.get("published_at")
        as_of = datetime.fromisoformat(stamp) if stamp else now
        as_of = as_of if as_of.tzinfo else as_of.replace(tzinfo=now.tzinfo)
        c = visual.verify_series([tuple(x) for x in f["points"]], window=str(f.get("window") or "24h").upper(), headline=visual.post_headline(ev), as_of=as_of, now=now,
                                 max_age_minutes=90 if ev.kind in ("recap", "market") else 360, second_pct=f.get("second_pct"), second_name=str(f.get("second_source") or ""))
        out["numbers"] = _gate(c.ok, "; ".join(c.reasons) or c.detail)
    elif ev.kind == "market":
        out["numbers"] = _gate(True, "checked against the price series when the chart is drawn; refused = no post")
    else:
        out["numbers"] = _gate(True, "no chart or measured figure in this post")
    head, src = visual.post_headline(ev), visual.source_name(ev)
    r = checks.check_post(f"{head}\n\nSource: {src}", source_text=f"{f.get('told') or ev.title} {ev.headline} {ev.summary} 24 hours", source_names=[src], max_len=1024)
    out["headline"] = _gate(r.ok and len(head) <= visual.MAX_HEADLINE + 40, r.reason or f"{len(head)} characters, one line, nothing added")
    b = f.get("blocked") or {}
    out["owner"] = _gate(not b.get("on"), f"blocked: {b.get('reason') or 'no reason given'}" if b.get("on") else "not blocked")
    return out


def build(db: Session, now: datetime, desk_state: dict[str, Any] | None = None) -> dict[str, Any]:
    """The next slot and its candidates, in the order the desk would take them."""
    p = events.policy(desk_state)
    nxt = events.current_slot(now, p)
    nxt = nxt if nxt is not None and events.slot_filled(db, nxt, p) is None else events.next_slot(now, p)
    waiting = list(db.scalars(select(NdEvent).where(NdEvent.status == "candidate")))
    covered = editorial.coverage(db, now)
    waiting.sort(key=lambda e: (-(e.virality + (p.priority_bonus if events.priority(e, p) else 0) + editorial.rank_bonus(e)
                                  - editorial.diversity(e, covered, now)[0]), e.id))
    tg = db.get(NdTelegramSettings, 1)
    recent = list((tg.state or {}).get("recent_visuals") or []) if tg is not None else []
    cards, chosen = [], False
    for ev in waiting[:SHOWN]:
        g = gates(ev, now, p, covered)
        passes = all(x["ok"] for x in g.values())
        will = passes and not chosen
        chosen = chosen or will
        shown = next((m for (i, _), (_, m) in _images.items() if i == ev.id), None)  # the picture Admin last rendered for it
        plan = (ev.facts or {}).get("visual") or shown or visual.plan(ev, recent)
        cards.append({"event": ev.id, "kind": ev.kind, "headline": visual.post_headline(ev), "credit": f"Source: {visual.source_name(ev)}", "score": ev.virality,
                      "held_score": events.held_score(ev, now, p), "topic": ev.topic, "picture": plan.get("kind"), "gates": g, "will_post": will,
                      "status": "will post at the next slot" if will else ("passes, but another candidate ranks first" if passes else "won't post: " + "; ".join(
                          f"{k}: {v['detail']}" for k, v in g.items() if not v["ok"])), "image": f"/api/x/newsdesk/editorial/preview/{ev.id}.jpg",
                      "blocked": bool(((ev.facts or {}).get("blocked") or {}).get("on")), "expires_at": ev.expires_at.isoformat() if ev.expires_at else None})
    held = list(db.scalars(select(NdEvent).where(NdEvent.status == "released", NdEvent.released_at >= now - timedelta(hours=3))))
    owed = [{"event": e.id, "headline": visual.post_headline(e), "telegram": e.tg_status, "x": e.x_status, "blocked": bool(((e.facts or {}).get("blocked") or {}).get("on"))}
            for e in held if e.tg_status in ("pending", "queued") or e.x_status in ("pending", "queued")]
    return {"next_slot": nxt.isoformat(), "next_slot_dubai": events.slot_label(nxt), "rule": editorial.RULE, "candidates": cards, "releasing": owed,
            "fallback": "No candidate passes: the slot gets the day's biggest EXCEPTIONAL market move the channel hasn't shown (charted only if its numbers pass "
                        "the gate), or stays empty. Nothing is posted to fill it.",
            "bar": p.regular_bar}


async def preview(db: Session, event_id: int, now: datetime) -> tuple[bytes, dict[str, Any]] | None:
    """The exact picture the post would carry. Rendered on a detached copy, so nothing about the event changes."""
    ev = db.get(NdEvent, event_id)
    if ev is None:
        return None
    key = (ev.id, f"{ev.headline}|{ev.updated_at}|{(ev.facts or {}).get('visual')}")
    if key in _images:
        return _images[key]
    copy = NdEvent(key=ev.key, kind=ev.kind, title=ev.title, headline=ev.headline, topic=ev.topic, label=ev.label, sources=ev.sources, facts=dict(ev.facts or {}),
                   virality=ev.virality, released_at=ev.released_at, first_seen=ev.first_seen, evidence=ev.evidence)
    copy.id = ev.id
    tg = db.get(NdTelegramSettings, 1)
    state = (tg.state or {}) if tg is not None else {}
    used = {u for u, _ in (state.get("used_photos") or []) if u}
    try:
        got = await visual.for_event(copy, used=used, recent=list(state.get("recent_visuals") or []), now=now)
    except visual.NumbersRefused as e:
        got = visual.graphic(visual.post_headline(copy), palette="crimson", layout="lower", seed=1, source=f"NOT POSTED: numbers check failed: {str(e)[:80]}")
    if len(_images) > 40:
        _images.clear()
    _images[key] = got
    return got


def block(db: Session, event_id: int, on: bool, reason: str, now: datetime) -> NdEvent | None:
    """The owner's block: a candidate never released, a released post not yet delivered skipped on both platforms."""
    ev = db.get(NdEvent, event_id)
    if ev is None:
        return None
    ev.facts = {**(ev.facts or {}), "blocked": {"on": on, "reason": reason[:200], "at": now.isoformat()}}
    if on and ev.status == "candidate":
        ev.why = "blocked by the owner" + (f": {reason}" if reason else "")
    db.commit()
    logger.info("newsdesk queue: event %s %s by the owner", event_id, "blocked" if on else "unblocked")
    return ev


__all__ = ["block", "build", "gates", "preview"]
