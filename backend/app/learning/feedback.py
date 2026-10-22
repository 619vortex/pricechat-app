"""Human feedback on AI outputs, and an interpretable preference model learned from it.

No model fine-tuning: decisions are stored as events with simple, readable features
("keyword:funding", "signal:topic", "insight:concern"...). Preference weights are
per-feature approval/rejection balances, and similar past decisions are found by
embedding similarity. Both are shown to the user as plain-English reasons.
"""

import logging
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.community.vectors import from_blob, normalise, to_blob
from app.db.base import utcnow
from app.memory.retrieval.lexical import STOPWORDS
from app.models.community import CommunityQuestion, CommunityTopic, ContentOpportunity
from app.models.content import Content
from app.models.enums import (
    ActivityLevel,
    ContentStatus,
    FeedbackDecision,
    FeedbackTarget,
    OpportunityStatus,
    QuestionStatus,
    ReviewStatus,
    SocialOpportunityStatus,
)
from app.models.memory import Memory
from app.models.social_intel import FeedbackEvent, SocialOpportunity
from app.providers.base import EmbeddingProvider
from app.providers.errors import ProviderError
from app.services.activity import ActivityLogger

logger = logging.getLogger(__name__)

POSITIVE = {FeedbackDecision.APPROVE, FeedbackDecision.IMPORTANT}
NEGATIVE = {FeedbackDecision.REJECT, FeedbackDecision.NOT_IMPORTANT}
_WORD = re.compile(r"[a-z][a-z0-9\-]{3,}")


class FeedbackError(ValueError):
    pass


def keywords(text: str, n: int = 6) -> list[str]:
    counts = Counter(w for w in _WORD.findall(text.lower()) if w not in STOPWORDS)
    return [w for w, _ in counts.most_common(n)]


# --- feature extraction (what a decision was "about") ----------------------------------------


def features_for(db: Session, kind: FeedbackTarget, target: Any) -> tuple[list[str], str, str]:
    """Return (features, label, text) describing a target, for learning."""
    if kind == FeedbackTarget.OPPORTUNITY:
        o: ContentOpportunity = target
        feats = [f"signal:{o.origin_kind}", f"urgency:{o.urgency.value}", *[f"platform:{p}" for p in o.platforms]]
        text = f"{o.title}. {o.angle}"
        return feats + [f"keyword:{k}" for k in keywords(text)], o.title, text
    if kind == FeedbackTarget.TOPIC:
        t: CommunityTopic = target
        text = f"{t.name}. {t.description}"
        return [f"keyword:{k}" for k in (t.keywords or keywords(text))] + ["target:topic"], t.name, text
    if kind == FeedbackTarget.QUESTION:
        q: CommunityQuestion = target
        return [f"keyword:{k}" for k in keywords(q.text)] + ["target:question"], q.text, q.text
    if kind == FeedbackTarget.MEMORY:
        m: Memory = target
        text = f"{m.title or ''}. {m.content}"
        insight = (m.extra or {}).get("insight_type")
        feats = [f"tag:{t}" for t in m.tags] + ([f"insight:{insight}"] if insight else []) + [f"keyword:{k}" for k in keywords(text)]
        return feats, m.title or m.content[:80], text
    if kind == FeedbackTarget.SOCIAL_OPPORTUNITY:
        s: SocialOpportunity = target
        text = f"{s.topic}. {s.why}"
        feats = [f"kind:{s.kind.value}", f"action:{s.suggested_action.value}"] + [f"keyword:{k}" for k in keywords(text)]
        return feats, s.topic or s.why[:80], text
    if kind == FeedbackTarget.DRAFT:
        c: Content = target
        text = f"{c.title or ''}. {c.body}"
        feats = [f"draft:{c.draft_type}"] if c.draft_type else []
        if c.variant_label:
            feats.append(f"style:{c.variant_label}")
        return feats + [f"keyword:{k}" for k in keywords(text, 4)], c.title or c.body[:80], text
    raise FeedbackError(f"Unsupported feedback target {kind}")


_MODELS: dict[FeedbackTarget, type] = {
    FeedbackTarget.OPPORTUNITY: ContentOpportunity,
    FeedbackTarget.TOPIC: CommunityTopic,
    FeedbackTarget.QUESTION: CommunityQuestion,
    FeedbackTarget.MEMORY: Memory,
    FeedbackTarget.SOCIAL_OPPORTUNITY: SocialOpportunity,
    FeedbackTarget.DRAFT: Content,
}

EDITABLE: dict[FeedbackTarget, tuple[str, ...]] = {
    FeedbackTarget.OPPORTUNITY: ("title", "why", "angle"),
    FeedbackTarget.TOPIC: ("name", "description"),
    FeedbackTarget.QUESTION: ("text",),
    FeedbackTarget.MEMORY: ("title", "content"),
    FeedbackTarget.SOCIAL_OPPORTUNITY: ("topic", "contribution"),
    FeedbackTarget.DRAFT: ("title", "body"),
}


def _apply(kind: FeedbackTarget, target: Any, decision: FeedbackDecision, edits: dict[str, Any] | None) -> str:
    """Apply the decision to the target. Returns a short description of the effect."""
    D = FeedbackDecision
    if decision == D.EDIT:
        allowed = EDITABLE[kind]
        changes = {k: str(v).strip() for k, v in (edits or {}).items() if k in allowed and str(v).strip()}
        if not changes:
            raise FeedbackError(f"Nothing to edit; editable fields: {', '.join(allowed)}")
        for k, v in changes.items():
            setattr(target, k, v[:4000])
        return "edited " + ", ".join(changes)

    if kind == FeedbackTarget.MEMORY:
        if decision == D.APPROVE:
            target.review_status = ReviewStatus.APPROVED
        elif decision == D.REJECT:
            target.review_status = ReviewStatus.REJECTED
        elif decision == D.IMPORTANT:
            target.importance = max(target.importance, 0.9)
        else:
            target.importance = min(target.importance, 0.2)
    elif kind in (FeedbackTarget.TOPIC, FeedbackTarget.QUESTION):
        if decision == D.APPROVE:
            target.review_status = "approved"
        elif decision == D.REJECT:
            target.review_status = "rejected"
            if kind == FeedbackTarget.QUESTION:
                target.status = QuestionStatus.DISMISSED
        elif decision == D.IMPORTANT:
            target.user_importance = 1.0
        else:
            target.user_importance = 0.0
    elif kind == FeedbackTarget.OPPORTUNITY:
        if decision == D.APPROVE:
            target.status = OpportunityStatus.APPROVED
            target.reviewed_at = utcnow()
        elif decision == D.REJECT:
            target.status = OpportunityStatus.REJECTED
            target.reviewed_at = utcnow()
        elif decision == D.IMPORTANT:
            target.user_importance = 1.0
        else:
            target.user_importance = 0.0
    elif kind == FeedbackTarget.SOCIAL_OPPORTUNITY:
        if decision == D.APPROVE:
            target.status = SocialOpportunityStatus.ACCEPTED
        elif decision == D.REJECT:
            target.status = SocialOpportunityStatus.DISMISSED
        elif decision == D.IMPORTANT:
            target.priority = min(1.0, target.priority + 0.2)
        else:
            target.priority = max(0.0, target.priority - 0.2)
    elif kind == FeedbackTarget.DRAFT:
        if decision == D.APPROVE:
            target.status = ContentStatus.APPROVED
        elif decision == D.REJECT:
            target.status = ContentStatus.REJECTED
    return decision.value.replace("_", " ")


async def record_feedback(
    db: Session,
    kind: FeedbackTarget,
    target_id: int,
    decision: FeedbackDecision,
    embedder: EmbeddingProvider | None,
    note: str = "",
    edits: dict[str, Any] | None = None,
    actor: str = "dashboard",
) -> FeedbackEvent:
    target = db.get(_MODELS[kind], target_id)
    if target is None:
        raise LookupError(f"{kind.value} {target_id} not found")
    effect = _apply(kind, target, decision, edits)
    feats, label, text = features_for(db, kind, target)

    vector, model = None, None
    if embedder is not None:
        try:
            vector = to_blob(normalise((await embedder.embed([text[:2000]], purpose="document"))[0]))
            model = embedder.model
        except ProviderError as exc:
            logger.info("Feedback stored without embedding: %s", exc)

    event = FeedbackEvent(
        target_kind=kind, target_id=target_id, decision=decision, features=feats, label=label[:300],
        vector=vector, embedding_model=model, note=note[:1000],
    )
    db.add(event)
    db.commit()
    ActivityLogger(db).log(
        "feedback_recorded",
        f'{kind.value.replace("_", " ").capitalize()} "{label[:80]}": {effect}',
        level=ActivityLevel.AUDIT,
        actor=actor,
        details={"target": kind.value, "id": target_id, "decision": decision.value},
    )
    return event


# --- preference model --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FeatureWeight:
    feature: str
    approvals: int
    rejections: int
    weight: float  # -1 (always rejected) .. +1 (always approved), shrunk towards 0 when evidence is thin


@dataclass(frozen=True, slots=True)
class PreferenceScore:
    score: float  # -1 .. +1 ; 0 = no learned preference
    reasons: list[str]


# Which feedback targets inform which ranking.
LEARNS_FROM: dict[str, tuple[FeedbackTarget, ...]] = {
    "opportunity": (FeedbackTarget.OPPORTUNITY, FeedbackTarget.DRAFT, FeedbackTarget.TOPIC),
    "social_opportunity": (FeedbackTarget.SOCIAL_OPPORTUNITY,),
}


class PreferenceModel:
    def __init__(self, db: Session, scope: str = "opportunity", prior: float = 2.0, min_support: int = 2) -> None:
        self.events = list(
            db.scalars(
                select(FeedbackEvent)
                .where(FeedbackEvent.target_kind.in_(LEARNS_FROM[scope]), FeedbackEvent.decision != FeedbackDecision.EDIT)
                .order_by(FeedbackEvent.id.desc())
                .limit(2000)
            )
        )
        self.prior = prior
        self.min_support = min_support
        pos: Counter[str] = Counter()
        neg: Counter[str] = Counter()
        for e in self.events:
            bucket = pos if e.decision in POSITIVE else neg
            bucket.update(set(e.features))
        self.weights = {
            f: FeatureWeight(f, pos[f], neg[f], round((pos[f] - neg[f]) / (pos[f] + neg[f] + prior), 3))
            for f in set(pos) | set(neg)
        }

    def table(self, limit: int = 50) -> list[FeatureWeight]:
        rows = [w for w in self.weights.values() if w.approvals + w.rejections >= self.min_support]
        return sorted(rows, key=lambda w: abs(w.weight), reverse=True)[:limit]

    def score(self, features: list[str], vector: np.ndarray | None = None, model: str | None = None) -> PreferenceScore:
        if not self.events:
            return PreferenceScore(0.0, [])
        reasons: list[str] = []
        relevant = [
            self.weights[f] for f in set(features)
            if f in self.weights and self.weights[f].approvals + self.weights[f].rejections >= self.min_support
            and abs(self.weights[f].weight) >= 0.2
        ]
        feature_score = sum(w.weight for w in relevant) / len(relevant) if relevant else 0.0
        for w in sorted(relevant, key=lambda w: abs(w.weight), reverse=True)[:2]:
            name = w.feature.split(":", 1)[-1].replace("_", " ")
            if w.weight > 0:
                reasons.append(f"you approved {w.approvals} of {w.approvals + w.rejections} similar items about '{name}'")
            else:
                reasons.append(f"you rejected {w.rejections} of {w.approvals + w.rejections} similar items about '{name}'")

        neighbour_score = 0.0
        if vector is not None:
            sims = []
            for e in self.events:
                if e.vector is None or e.embedding_model != model:
                    continue
                v = from_blob(e.vector)
                if v.shape == vector.shape:
                    sims.append((float(v @ vector), e))
            close = sorted([s for s in sims if s[0] >= 0.8], key=lambda s: s[0], reverse=True)[:3]
            if close:
                neighbour_score = sum((1 if e.decision in POSITIVE else -1) * s for s, e in close) / len(close)
                best_sim, best = close[0]
                verb = "approved" if best.decision in POSITIVE else "rejected"
                reasons.append(f"very similar to '{best.label[:60]}', which you {verb}")

        if not relevant and neighbour_score == 0.0:
            return PreferenceScore(0.0, [])
        score = 0.6 * feature_score + 0.4 * neighbour_score if relevant and neighbour_score else (feature_score or neighbour_score)
        return PreferenceScore(round(max(-1.0, min(1.0, score)), 3), reasons)
