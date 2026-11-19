"""The owner's one-click story feedback (owner, 2026-11-19: "Add one-click Great / Boring / Wrong Image / Missed Story feedback in Terminal.
Store feedback against actual events and publishing decisions").

A verdict is stored against the real target (a News Desk event or a Terminal / Trending topic) with a snapshot of what the desk had
decided and published at that moment: the gate's decision, score and reason, where it went out, the picture, and the AI editor's shadow
verdict if it has one. That snapshot is what the AI editor's evaluation and the gate's tuning are measured against later. Clicking the
same verdict again removes it. Feedback never changes a post or a decision by itself.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.newsdesk import NdEvent, NdFeedback, NdValidation

VERDICTS = {"great": "Great", "boring": "Boring", "wrong_image": "Wrong image", "missed": "Missed story"}
PUBLISHED_TG = ("delivered", "uncertain")
PUBLISHED_X = ("published", "uncertain", "dry_run")


class FeedbackError(ValueError):
    pass


def _event_snapshot(db: Session, e: NdEvent) -> dict[str, Any]:
    f = e.facts or {}
    visual = f.get("visual") or {}
    cand = db.scalar(select(NdValidation).where(NdValidation.kind == "candidate", NdValidation.ref == str(e.id)))
    chief = db.scalar(select(NdValidation).where(NdValidation.kind == "chief", NdValidation.ref == f"event:{e.id}"))
    c = cand.data if cand is not None else {}
    return {
        "kind": "event", "id": e.id, "title": e.title, "headline": e.headline, "status": e.status, "why": (e.why or "")[:300],
        "telegram": e.tg_status, "x": e.x_status, "published": e.tg_status in PUBLISHED_TG or e.x_status in PUBLISHED_X,
        "tg_message_id": e.tg_message_id, "x_post_id": e.x_post_id, "evidence": e.evidence, "virality": int(e.virality or 0),
        "picture": {"kind": visual.get("kind"), "library": visual.get("library"), "title": visual.get("title"), "credit": visual.get("credit_line")}
        if visual.get("kind") == "photo" else ({"kind": visual.get("kind")} if visual else None),
        "gate": {"decision": c.get("decision"), "interest": c.get("interest"), "why": str(c.get("why") or "")[:300],
                 "release": (c.get("gate") or {}).get("decision")} if c else None,
        "chief": {k: (chief.data or {}).get(k) for k in ("status", "verdict", "why_care", "scores")} if chief is not None else None,
    }


def _topic_snapshot(db: Session, topic_id: int) -> dict[str, Any]:
    from app.models.trending import TtPost, TtTopic

    t = db.get(TtTopic, topic_id)
    if t is None:
        raise FeedbackError("No such story.")
    posts = list(db.scalars(select(TtPost).where(TtPost.topic_id == t.id).order_by(TtPost.id.desc()).limit(3)))
    sent = [p for p in posts if p.status in ("published", "uncertain", "dry_run")]
    term = (t.parts or {}).get("terminal") or {}
    return {
        "kind": "topic", "id": t.id, "title": t.title, "status": t.status, "category": t.category, "score": round(float(t.score or 0), 1),
        "published": bool(sent) or t.status == "posted", "x_posts": [{"id": p.id, "status": p.status, "x_id": p.x_id} for p in sent],
        "telegram": bool((t.parts or {}).get("telegram")), "skip": (t.parts or {}).get("skip"),
        "evidence": (term.get("evidence") or {}).get("score"), "viral": (term.get("viral") or {}).get("score"), "picture": None,
    }


def snapshot(db: Session, target: str) -> dict[str, Any]:
    kind, _, raw = target.partition(":")
    if not raw.isdigit():
        raise FeedbackError("Unknown story.")
    if kind == "event":
        e = db.get(NdEvent, int(raw))
        if e is None:
            raise FeedbackError("No such story.")
        return _event_snapshot(db, e)
    if kind == "topic":
        return _topic_snapshot(db, int(raw))
    raise FeedbackError("Unknown story.")


def give(db: Session, target: str, verdict: str, note: str = "", now: datetime | None = None) -> dict[str, Any]:
    """Toggle one verdict on one story. Great and Boring exclude each other (the latest click wins); Wrong image only for a story that went
    out with a picture; Missed story only for one that never went out."""
    if verdict not in VERDICTS:
        raise FeedbackError("Unknown feedback.")
    snap = snapshot(db, target)
    existing = db.scalar(select(NdFeedback).where(NdFeedback.target == target, NdFeedback.verdict == verdict))
    if existing is not None:
        db.delete(existing)
        db.commit()
        return current(db, target)
    if verdict == "wrong_image" and not (snap.get("published") and (snap.get("picture") or {}).get("kind")):
        raise FeedbackError("This story didn't go out with a picture.")
    if verdict == "missed" and snap.get("published"):
        raise FeedbackError("This story was published, so it wasn't missed.")
    if verdict in ("great", "boring"):
        other = "boring" if verdict == "great" else "great"
        old = db.scalar(select(NdFeedback).where(NdFeedback.target == target, NdFeedback.verdict == other))
        if old is not None:
            db.delete(old)
    db.add(NdFeedback(target=target, verdict=verdict, note=(note or "")[:500], snapshot={**snap, "at": (now or utcnow()).isoformat()}))
    db.commit()
    return current(db, target)


def current(db: Session, target: str) -> dict[str, Any]:
    rows = list(db.scalars(select(NdFeedback).where(NdFeedback.target == target)))
    return {"target": target, "verdicts": sorted(r.verdict for r in rows)}


def summary(db: Session, now: datetime | None = None, days: int = 30) -> dict[str, Any]:
    """Counts, and the stories behind each, for Terminal › Settings › Validation and the AI editor's evaluation."""
    now = now or utcnow()
    rows = list(db.scalars(select(NdFeedback).where(NdFeedback.created_at >= now - timedelta(days=days)).order_by(NdFeedback.id.desc())))
    n = Counter(r.verdict for r in rows)
    return {"counts": {k: n.get(k, 0) for k in VERDICTS}, "labels": VERDICTS,
            "rows": [{"target": r.target, "verdict": r.verdict, "title": (r.snapshot or {}).get("headline") or (r.snapshot or {}).get("title"),
                      "published": (r.snapshot or {}).get("published"), "gate": ((r.snapshot or {}).get("gate") or {}).get("decision"),
                      "chief": ((r.snapshot or {}).get("chief") or {}).get("verdict"), "at": r.created_at.isoformat() if r.created_at else None}
                     for r in rows[:30]]}


def labels(db: Session) -> dict[str, set[str]]:
    """{target: {verdicts}}: the owner's labels, for the AI editor's evaluation."""
    out: dict[str, set[str]] = {}
    for r in db.scalars(select(NdFeedback)):
        out.setdefault(r.target, set()).add(r.verdict)
    return out
