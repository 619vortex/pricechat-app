"""Transparent content-opportunity ranking.

Each opportunity gets separate 0..1 signals; the overall priority is a documented
weighted sum, and every opportunity carries plain-English reasons. Nothing is a
single opaque model score.
"""

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.community.vectors import normalise
from app.db.base import utcnow
from app.learning.feedback import PreferenceModel, features_for
from app.models.community import CommunityQuestion, CommunityTopic, ContentOpportunity
from app.models.content import Content
from app.models.enums import FeedbackTarget, OpportunityStatus, TopicTrend
from app.models.social_intel import CrossSignal
from app.providers.base import EmbeddingProvider
from app.providers.errors import ProviderError
from app.services.brand_service import get_brand

WEIGHTS = {
    "community_relevance": 0.15,
    "x_relevance": 0.07,  # same topic active on X (from an active cross-platform signal); 0 otherwise
    "recency": 0.10,
    "volume": 0.10,
    "participants": 0.10,
    "novelty": 0.10,
    "brand_relevance": 0.12,
    "content_potential": 0.10,
    "confidence": 0.06,
    "learned_preference": 0.10,
}
HIGH, MEDIUM = 0.6, 0.4
OPEN = (OpportunityStatus.DETECTED, OpportunityStatus.REVIEWING, OpportunityStatus.APPROVED)


@dataclass(slots=True)
class Ranked:
    signals: dict[str, float]
    reasons: list[str]
    priority: float
    label: str


def _aware(d: datetime) -> datetime:
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def label_for(priority: float) -> str:
    return "high" if priority >= HIGH else "medium" if priority >= MEDIUM else "low"


async def _embed(embedder: EmbeddingProvider | None, texts: list[str]) -> list[np.ndarray] | None:
    if embedder is None or not texts:
        return None
    try:
        return [normalise(v) for v in await embedder.embed([t[:1500] for t in texts], purpose="document")]
    except ProviderError:
        return None


async def rank_opportunities(
    db: Session, agent_id: int, embedder: EmbeddingProvider | None, now: datetime | None = None, ids: list[int] | None = None
) -> int:
    now = now or utcnow()
    stmt = select(ContentOpportunity).where(ContentOpportunity.status.in_(OPEN))
    if ids:
        stmt = select(ContentOpportunity).where(ContentOpportunity.id.in_(ids))
    opps = list(db.scalars(stmt))
    if not opps:
        return 0

    brand = get_brand(db, agent_id)
    brand_text = ". ".join(
        p for p in [brand.description, brand.mission, brand.audience, "; ".join(brand.topics_of_interest), "; ".join(brand.social_objectives)] if p
    )
    avoid_text = "; ".join(brand.topics_to_avoid)

    # Recently covered: other opportunities and drafts of the last 30 days.
    month_ago = now - timedelta(days=30)
    covered = [
        (o.id, f"{o.title}. {o.angle}", o.created_at)
        for o in db.scalars(select(ContentOpportunity).where(ContentOpportunity.created_at >= month_ago))
    ] + [(-c.id, f"{c.title or ''}. {c.body[:300]}", c.created_at) for c in db.scalars(select(Content).where(Content.created_at >= month_ago))]

    texts = [f"{o.title}. {o.angle}" for o in opps]
    vecs = await _embed(embedder, texts + [t for _, t, _ in covered] + ([brand_text] if brand_text else []) + ([avoid_text] if avoid_text else []))
    opp_vecs = cov_vecs = None
    brand_vec = avoid_vec = None
    if vecs is not None:
        opp_vecs = vecs[: len(opps)]
        cov_vecs = vecs[len(opps) : len(opps) + len(covered)]
        rest = vecs[len(opps) + len(covered) :]
        if brand_text:
            brand_vec = rest.pop(0)
        if avoid_text:
            avoid_vec = rest.pop(0)

    prefs = PreferenceModel(db, "opportunity")
    model_name = embedder.model if embedder else None
    cross = {c.telegram_topic_id: c for c in db.scalars(select(CrossSignal))}

    for i, o in enumerate(opps):
        r = _rank_one(db, o, now, opp_vecs[i] if opp_vecs else None, covered, cov_vecs, brand_vec, avoid_vec, prefs, model_name, cross)
        o.signals, o.reasons, o.priority, o.priority_label, o.ranked_at = r.signals, r.reasons, r.priority, r.label, now
    db.commit()
    return len(opps)


def _rank_one(db, o, now, vec, covered, cov_vecs, brand_vec, avoid_vec, prefs, model_name, cross) -> Ranked:  # type: ignore[no-untyped-def]
    s: dict[str, float] = {}
    reasons: list[str] = []
    ev = o.evidence or {}
    s["x_relevance"] = 0.0

    # Community evidence: from the database, never from the model.
    last_activity = o.created_at
    if o.origin_kind == "topic" and o.origin_id and (t := db.get(CommunityTopic, o.origin_id)) is not None:
        msgs, people, last_activity = t.message_count, t.participant_count, t.last_seen_at
        s["community_relevance"] = t.user_importance if t.user_importance is not None else t.importance
        reasons.append(f"{msgs} messages from {people} participants")
        if t.trend in (TopicTrend.RISING, TopicTrend.NEW):
            reasons.append(f"volume {t.trend.value} ({t.count_24h} in the last 24h vs {t.count_prev_24h} before)")
        if (c := cross.get(t.id)) is not None and c.status.value == "active":
            s["x_relevance"] = round(min(1.0, math.log1p(c.x_posts) / math.log1p(20)), 3)
            reasons.append(f"also discussed on X ({c.x_posts} monitored posts)")
            if c.early_signal and c.lead_minutes:
                s["x_relevance"] = round(min(1.0, s["x_relevance"] + 0.15), 3)
                lead = f"{c.lead_minutes // 60} h" if c.lead_minutes >= 120 else f"{c.lead_minutes} min"
                reasons.append(f"early community signal: discussed on Telegram {lead} before monitored X accounts")
    elif o.origin_kind == "question" and o.origin_id and (q := db.get(CommunityQuestion, o.origin_id)) is not None:
        msgs, people, last_activity = q.ask_count, q.asker_count, q.last_asked_at
        s["community_relevance"] = q.user_importance if q.user_importance is not None else min(1.0, q.ask_count / 10)
        reasons.append(f"asked {q.ask_count} times by {q.asker_count} members")
    else:
        msgs, people = int(ev.get("messages", 0) or 0), int(ev.get("participants", 0) or 0)
        s["community_relevance"] = 0.5

    hours = max(0.0, (now - _aware(last_activity)).total_seconds() / 3600)
    s["recency"] = round(math.exp(-hours / 48), 3)
    s["volume"] = round(min(1.0, math.log1p(msgs) / math.log1p(60)), 3)
    s["participants"] = round(min(1.0, people / 15), 3)

    # Novelty: has something similar been covered in the last 30 days?
    s["novelty"] = 1.0
    if vec is not None and cov_vecs:
        best, best_text = 0.0, ""
        for (cid, text, _), cv in zip(covered, cov_vecs, strict=False):
            if cid == o.id:
                continue
            sim = float(cv @ vec)
            if sim > best:
                best, best_text = sim, text
        s["novelty"] = round(max(0.0, min(1.0, (0.92 - best) / 0.22)), 3)
        if best >= 0.85:
            reasons.append(f"similar to something covered recently ('{best_text[:50]}')")
        elif s["novelty"] >= 0.8:
            reasons.append("not covered in the last 30 days")

    # Brand relevance: closeness to the brand profile; penalised if near topics to avoid.
    s["brand_relevance"] = 0.5
    if vec is not None and brand_vec is not None:
        sim = float(brand_vec @ vec)
        s["brand_relevance"] = round(max(0.0, min(1.0, (sim - 0.45) / 0.3)), 3)
        if avoid_vec is not None and float(avoid_vec @ vec) >= 0.7:
            s["brand_relevance"] = round(s["brand_relevance"] * 0.3, 3)
            reasons.append("close to a topic the brand avoids")
        elif s["brand_relevance"] >= 0.6:
            reasons.append("strong brand relevance")

    s["content_potential"] = round(o.content_potential if o.content_potential is not None else o.confidence, 3)
    s["confidence"] = round(o.confidence, 3)

    feats, _, _ = features_for(db, FeedbackTarget.OPPORTUNITY, o)
    pref = prefs.score(feats, vec, model_name)
    s["learned_preference"] = round((pref.score + 1) / 2, 3)  # 0.5 = no preference learned
    reasons.extend(pref.reasons)

    priority = sum(WEIGHTS[k] * s[k] for k in WEIGHTS)
    if o.user_importance is not None:  # explicit human signal wins
        priority = priority + (0.25 if o.user_importance >= 0.5 else -0.25)
        reasons.append("marked important by you" if o.user_importance >= 0.5 else "marked not important by you")
    priority = round(max(0.0, min(1.0, priority)), 3)
    return Ranked(s, reasons, priority, label_for(priority))
