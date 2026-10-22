"""Recurring question detection.

Questions are grouped by meaning (a stricter similarity than topics), so
"what does Price Chat do?" and "can someone explain what this project is for"
count as the same question. Groups asked by several different people are
"recurring" and become FAQ / content candidates. Nothing is answered or posted.
"""

from datetime import datetime

import numpy as np
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.routing import ModelRouter
from app.ai.structured import complete_structured
from app.community.formatting import format_messages
from app.community.tuning import CommunityTuning
from app.community.vectors import from_blob, normalise, to_blob
from app.db.base import utcnow
from app.models.community import CommunityQuestion
from app.models.enums import QuestionStatus
from app.models.telegram import TelegramMessage

CANONICAL_MILESTONES = (2, 5, 10, 25, 50)


class QuestionGrouper:
    def __init__(self, db: Session, embedding_model: str, tuning: CommunityTuning) -> None:
        self.db = db
        self.model = embedding_model
        self.tuning = tuning
        self.groups: list[CommunityQuestion] = list(
            db.scalars(
                select(CommunityQuestion).where(
                    CommunityQuestion.status != QuestionStatus.DISMISSED, CommunityQuestion.embedding_model == embedding_model
                )
            )
        )
        self._centroids = [from_blob(q.centroid).copy() if q.centroid else None for q in self.groups]

    def assign(self, message: TelegramMessage, vec: np.ndarray) -> CommunityQuestion:
        best, best_sim = -1, -1.0
        for i, c in enumerate(self._centroids):
            if c is None or c.shape != vec.shape:
                continue
            sim = float(c @ vec)
            if sim > best_sim:
                best, best_sim = i, sim

        if best >= 0 and best_sim >= self.tuning.question_similarity:
            group = self.groups[best]
            n = group.ask_count
            centroid = normalise(self._centroids[best] * n + vec)  # type: ignore[operator]
            self._centroids[best] = centroid
            group.centroid = to_blob(centroid)
            group.ask_count = n + 1
            group.last_asked_at = max(group.last_asked_at, message.sent_at)
            group.first_asked_at = min(group.first_asked_at, message.sent_at)
            if group.ask_count in CANONICAL_MILESTONES:
                group.needs_canonical = True
            if group.topic_id is None:
                group.topic_id = message.topic_id
        else:
            text = " ".join(message.text.split())[:300]
            group = CommunityQuestion(
                text=text,
                example=text,
                centroid=to_blob(vec),
                embedding_model=self.model,
                ask_count=1,
                asker_count=1,
                first_asked_at=message.sent_at,
                last_asked_at=message.sent_at,
                topic_id=message.topic_id,
            )
            self.db.add(group)
            self.db.flush()
            self.groups.append(group)
            self._centroids.append(vec.copy())
        message.question_id = group.id
        return group


def refresh_question_stats(db: Session) -> int:
    M = TelegramMessage
    rows = db.execute(
        select(M.question_id, func.count(M.id), func.count(func.distinct(M.author_id)), func.min(M.sent_at), func.max(M.sent_at))
        .where(M.question_id.is_not(None))
        .group_by(M.question_id)
    ).all()
    stats = {r[0]: r[1:] for r in rows}
    n = 0
    for q in db.scalars(select(CommunityQuestion)):
        count, askers, first, last = stats.get(q.id, (0, 0, None, None))
        if count == 0:
            db.delete(q)
            continue
        q.ask_count, q.asker_count = int(count), max(int(askers), 1)
        q.first_asked_at, q.last_asked_at = first, last
        n += 1
    db.flush()
    return n


def recurring_questions(db: Session, tuning: CommunityTuning, limit: int = 20, since: datetime | None = None) -> list[CommunityQuestion]:
    stmt = (
        select(CommunityQuestion)
        .where(
            CommunityQuestion.status != QuestionStatus.DISMISSED,
            CommunityQuestion.ask_count >= tuning.recurring_min_asks,
            CommunityQuestion.asker_count >= min(2, tuning.recurring_min_asks),
        )
        .order_by(CommunityQuestion.ask_count.desc(), CommunityQuestion.last_asked_at.desc())
        .limit(limit)
    )
    if since is not None:
        stmt = stmt.where(CommunityQuestion.last_asked_at >= since)
    return list(db.scalars(stmt))


class _Canonical(BaseModel):
    question: str = Field(description="One clear, neutral question that all the examples are asking")


CANONICAL_SYSTEM = (
    "Several community members asked the same thing in different words. Write ONE short, clear, neutral "
    "question that captures what they are asking. Keep names of products/projects exactly as written. "
    "Do not answer it. Return JSON only."
)


async def canonicalise_questions(db: Session, router: ModelRouter, limit: int = 10) -> int:
    groups = list(
        db.scalars(
            select(CommunityQuestion)
            .where(CommunityQuestion.needs_canonical.is_(True), CommunityQuestion.status != QuestionStatus.DISMISSED)
            .order_by(CommunityQuestion.ask_count.desc())
            .limit(limit)
        )
    )
    done = 0
    for group in groups:
        examples = list(
            db.scalars(
                select(TelegramMessage).where(TelegramMessage.question_id == group.id).order_by(TelegramMessage.sent_at).limit(8)
            )
        )
        text, _ = format_messages(examples, char_budget=3000, max_chars_each=300)
        parsed, _, route = await complete_structured(
            router, "telegram.question_canonical", CANONICAL_SYSTEM, f"Examples:\n{text}", _Canonical
        )
        if parsed.question.strip():
            group.text = parsed.question.strip()[:300]
            group.canonical_model = route.model
        group.needs_canonical = False
        group.updated_at = utcnow()
        db.flush()
        done += 1
    return done
