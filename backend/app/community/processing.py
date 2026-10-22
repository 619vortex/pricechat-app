"""Raw messages → classified, embedded, clustered messages."""

import logging
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.routing import ModelRouter
from app.community.classifier import HeuristicClassifier, LLMClassifier
from app.community.questions import QuestionGrouper
from app.community.topics import TopicAssigner, eligible_for_topic
from app.community.tuning import CommunityTuning
from app.community.vectors import embed_messages
from app.db.base import utcnow
from app.models.enums import MessageCategory
from app.models.telegram import TelegramMessage
from app.providers.base import EmbeddingProvider
from app.providers.errors import ProviderError

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ProcessReport:
    processed: int = 0
    noise: int = 0
    questions: int = 0
    topics_new: int = 0
    classifier: str = ""
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "processed": self.processed,
            "noise": self.noise,
            "questions": self.questions,
            "new_topics": self.topics_new,
            "classifier": self.classifier,
            "notes": self.notes,
        }


async def process_pending(
    db: Session,
    router: ModelRouter,
    embedder: EmbeddingProvider | None,
    tuning: CommunityTuning,
    limit: int = 200,
) -> ProcessReport:
    report = ProcessReport()
    messages = list(
        db.scalars(
            select(TelegramMessage)
            .where(TelegramMessage.processed_at.is_(None))
            .order_by(TelegramMessage.sent_at)
            .limit(limit)
        )
    )
    if not messages:
        return report

    # 1. Classify (fast model; rules if the model is unavailable)
    try:
        llm = LLMClassifier(router)
        labels = await llm.classify(messages)
        report.classifier = llm.name
    except ProviderError as exc:
        labels = await HeuristicClassifier().classify(messages)
        report.classifier = "rules"
        report.notes.append(f"Model classification unavailable, used rules: {exc}")
        logger.warning("Classification fell back to rules: %s", exc)

    for m in messages:
        label = labels[m.id]
        m.category = label.category
        m.is_question = label.is_question
        m.classified_by = report.classifier

    # 2. Embed + cluster the substantive messages. Embedding failures abort the batch
    #    (messages stay unprocessed and are retried), so nothing is silently skipped.
    substantive = [
        m for m in messages if eligible_for_topic(m) or (m.is_question and not m.author_is_bot and len(m.text.strip()) >= 8)
    ]
    if embedder is None:
        report.notes.append("No embedding provider configured: topics and question grouping are disabled.")
    elif substantive:
        vectors = await embed_messages(db, embedder, substantive)
        topics = TopicAssigner(db, embedder.model, tuning)
        questions = QuestionGrouper(db, embedder.model, tuning)
        before = len(topics.topics)
        for m in substantive:
            vec = vectors[m.id]
            if m.topic_id is None and eligible_for_topic(m):
                topics.assign(m, vec)
            if m.is_question and m.question_id is None:
                questions.assign(m, vec)
        report.topics_new = len(topics.topics) - before

    now = utcnow()
    for m in messages:
        m.processed_at = now
        report.processed += 1
        report.noise += m.category == MessageCategory.NOISE
        report.questions += m.is_question
    db.commit()
    return report
