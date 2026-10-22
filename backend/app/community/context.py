"""Relevant community intelligence for one agent request (never the whole chat log)."""

import logging
import re
from datetime import timedelta

import numpy as np
from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.agents.prompting.context import (
    CommunityContext,
    CommunityMessageItem,
    CommunityQuestionItem,
    CommunitySummaryItem,
    CommunityTopicItem,
)
from app.community.questions import recurring_questions
from app.community.tuning import CommunityTuning
from app.community.vectors import from_blob, normalise
from app.db.base import utcnow
from app.models.community import CommunitySummary, CommunityTopic
from app.models.enums import MessageCategory, TopicStatus
from app.models.telegram import TelegramMessage, TelegramMessageVector
from app.providers.base import EmbeddingProvider
from app.providers.errors import ProviderError

logger = logging.getLogger(__name__)

# Messages that are clearly about the community get the general "pulse" even without a close match.
_COMMUNITY_INTENT = re.compile(
    r"\b(community|members?|telegram|people (are|keep)|asking|talking|discuss\w*|trend\w*|sentiment|feedback|faq|"
    r"what do (they|people|users)|content ideas?)\b",
    re.I,
)
TOPIC_SIMILARITY = 0.6
QUESTION_SIMILARITY = 0.65
MESSAGE_SIMILARITY = 0.62


class CommunityContextProvider:
    def __init__(self, db: Session, embedder: EmbeddingProvider | None, tuning: CommunityTuning, max_messages: int = 4) -> None:
        self.db = db
        self.embedder = embedder
        self.tuning = tuning
        self.max_messages = max_messages

    async def gather(self, query: str) -> CommunityContext | None:
        if not self.db.scalar(select(exists().where(TelegramMessage.id.is_not(None)))):
            return None  # no community data yet
        intent = bool(_COMMUNITY_INTENT.search(query))
        qvec = None
        if self.embedder is not None:
            try:
                qvec = normalise((await self.embedder.embed([query], purpose="query"))[0])
            except ProviderError as exc:
                logger.info("Community context without embeddings: %s", exc)

        ctx = CommunityContext(
            topics=self._topics(qvec, intent),
            questions=self._questions(qvec, intent),
            summary=self._summary() if intent else None,
            messages=self._messages(qvec),
        )
        return None if ctx.is_empty() else ctx

    def _topics(self, qvec: np.ndarray | None, intent: bool) -> list[CommunityTopicItem]:
        topics = list(self.db.scalars(select(CommunityTopic).where(
            CommunityTopic.status == TopicStatus.ACTIVE, CommunityTopic.review_status != "rejected")))
        picked: list[CommunityTopic] = []
        if qvec is not None:
            sims = sorted(
                ((float(from_blob(t.centroid) @ qvec), t) for t in topics if t.centroid and len(t.centroid) == qvec.nbytes),
                key=lambda x: x[0],
                reverse=True,
            )
            picked = [t for s, t in sims[:3] if s >= TOPIC_SIMILARITY]
        if intent:
            for t in sorted(topics, key=lambda t: t.importance, reverse=True)[:3]:
                if t not in picked:
                    picked.append(t)
        return [
            CommunityTopicItem(t.name, t.description, t.trend.value, t.count_24h, t.message_count, t.participant_count)
            for t in picked[:4]
        ]

    def _questions(self, qvec: np.ndarray | None, intent: bool) -> list[CommunityQuestionItem]:
        questions = recurring_questions(self.db, self.tuning, limit=30)
        picked = []
        if qvec is not None:
            sims = sorted(
                ((float(from_blob(q.centroid) @ qvec), q) for q in questions if q.centroid and len(q.centroid) == qvec.nbytes),
                key=lambda x: x[0],
                reverse=True,
            )
            picked = [q for s, q in sims[:3] if s >= QUESTION_SIMILARITY]
        if intent:
            for q in questions[:3]:
                if q not in picked:
                    picked.append(q)
        return [CommunityQuestionItem(q.text, q.ask_count, q.asker_count, q.last_asked_at) for q in picked[:4]]

    def _summary(self) -> CommunitySummaryItem | None:
        s = self.db.scalar(select(CommunitySummary).where(CommunitySummary.topic_id.is_(None)).order_by(CommunitySummary.period_end.desc()).limit(1))
        if s is None:
            return None
        return CommunitySummaryItem(s.headline, str(s.data.get("overview", ""))[:700], s.period_start, s.period_end, s.model)

    def _messages(self, qvec: np.ndarray | None) -> list[CommunityMessageItem]:
        if qvec is None or self.embedder is None or self.max_messages <= 0:
            return []
        since = utcnow() - timedelta(days=30)
        rows = self.db.execute(
            select(TelegramMessage.id, TelegramMessage.text, TelegramMessage.sent_at, TelegramMessageVector.vector)
            .join(TelegramMessageVector, TelegramMessageVector.message_id == TelegramMessage.id)
            .where(
                TelegramMessage.sent_at >= since,
                TelegramMessageVector.model == self.embedder.model,
                TelegramMessage.category != MessageCategory.NOISE,
            )
        ).all()
        scored = sorted(
            ((float(from_blob(blob) @ qvec), mid, text, sent) for mid, text, sent, blob in rows if len(blob) == qvec.nbytes),
            key=lambda x: x[0],
            reverse=True,
        )
        return [
            CommunityMessageItem(mid, " ".join(text.split())[:280], sent)
            for s, mid, text, sent in scored[: self.max_messages]
            if s >= MESSAGE_SIMILARITY
        ]
