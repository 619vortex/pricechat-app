"""Summaries → candidate community memories (pending human review).

Raw messages are never written to long-term memory. A strong model reads a
summary plus its supporting messages and proposes the few things worth
remembering. Each proposal is stored as a *pending* community memory with its
generation metadata and links to the exact supporting messages.
"""

import hashlib
import logging
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.routing import ModelRouter
from app.ai.structured import complete_structured, unit_score
from app.community import provenance
from app.community.formatting import format_messages, strip_citations, valid_ids
from app.db.base import utcnow
from app.knowledge.service import KnowledgeService
from app.memory.records import MemoryInput
from app.memory.store import MemoryFilters, MemoryStore
from app.models.community import CommunitySummary
from app.models.enums import DerivedKind, MemoryKind, MemoryLayer, Platform, ReviewStatus, SourceType
from app.models.telegram import TelegramSource
from app.providers.base import EmbeddingProvider
from app.providers.errors import ProviderError

logger = logging.getLogger(__name__)

InsightType = Literal[
    "recurring_question", "important_topic", "observation", "terminology",
    "concern", "product_feedback", "content_idea", "useful_fact",
]
# Document-to-document similarity above which two observations are the same (calibrated on nomic-embed-text:
# paraphrases 0.81-0.91, related-but-different <= 0.79).
DUPLICATE_SIMILARITY = 0.80
MAX_AI_IMPORTANCE = 0.7  # humans can raise it further


class _Insight(BaseModel):
    type: InsightType
    title: str = Field(description="Short label, max 10 words")
    content: str = Field(description="One or two sentences, phrased as an observation about the community")
    importance: float = Field(description="0.0 to 1.0")
    confidence: float = Field(description="0.0 to 1.0: how well the messages support this")
    message_ids: list[str] = Field(description="Supporting message ids like m123")

    _scores = field_validator("importance", "confidence", mode="before")(lambda v: unit_score(v))


class _Insights(BaseModel):
    insights: list[_Insight] = Field(description="At most 6; empty if nothing is worth remembering")


SYSTEM = (
    "You decide what a brand's AI should remember long-term about its community. From the summary and "
    "messages, propose at most 6 durable, reusable insights: recurring questions, important topics, community "
    "terminology (term and meaning), common concerns, product feedback, content ideas, or useful factual context "
    "members shared.\nRules:\n"
    "- Skip one-off chatter, greetings and anything only true for a moment.\n"
    "- Phrase insights as observations ('Members frequently ask…', 'The community uses \"X\" to mean…'). "
    "They are NOT verified brand facts.\n"
    "- Never include personal information about individual members.\n"
    "- Do not repeat anything in the 'Already known' list.\n"
    "- Cite supporting message ids. Return JSON only."
)


@dataclass(slots=True)
class ExtractionReport:
    summaries: int = 0
    created: int = 0
    duplicates: int = 0
    memory_ids: list[int] = field(default_factory=list)


def _external_id(title: str, content: str) -> str:
    return "insight:" + hashlib.sha1(f"{title.lower()}|{content.lower()}".encode()).hexdigest()[:20]


async def extract_memories(
    db: Session,
    router: ModelRouter,
    agent_id: int,
    embedder: EmbeddingProvider | None,
    limit: int = 3,
) -> ExtractionReport:
    report = ExtractionReport()
    pending = list(
        db.scalars(
            select(CommunitySummary)
            .where(CommunitySummary.memories_extracted_at.is_(None), CommunitySummary.topic_id.is_(None))
            .order_by(CommunitySummary.period_end)
            .limit(limit)
        )
    )
    if not pending:
        return report

    knowledge = KnowledgeService(db, agent_id, embedder)
    store = MemoryStore(db, agent_id)
    for summary in pending:
        messages = provenance.messages_for(db, DerivedKind.SUMMARY, summary.id, limit=80)
        text, included = format_messages(messages, char_budget=9000, max_chars_each=300)
        known = store.list(MemoryFilters(layers=(MemoryLayer.COMMUNITY,)), limit=40)
        known_text = "\n".join(f"- {m.title or m.content[:80]}" for m in known) or "(nothing yet)"
        user = f"Summary:\n{summary.body}\n\nAlready known:\n{known_text}\n\nSupporting messages:\n{text}"

        out, result, route = await complete_structured(router, "community.memory_extraction", SYSTEM, user, _Insights)
        source = db.get(TelegramSource, summary.source_id) if summary.source_id else None
        allowed = set(included)
        for insight in out.insights[:6]:
            ids = valid_ids(insight.message_ids, allowed)
            insight.content = strip_citations(insight.content)
            insight.title = strip_citations(insight.title)
            if not ids or not insight.content:
                continue  # unsupported claims are not remembered
            if await _is_duplicate(knowledge, insight.content):
                report.duplicates += 1
                continue
            record, created, _ = await knowledge.add(
                MemoryInput(
                    content=insight.content.strip(),
                    title=insight.title.strip()[:256],
                    kind=MemoryKind.COMMUNITY_INSIGHT,
                    layer=MemoryLayer.COMMUNITY,
                    tags=[insight.type.replace("_", " "), "telegram"],
                    source_type=SourceType.TELEGRAM,
                    source=f"Telegram · {source.display_name if source else 'community'} · summary #{summary.id}",
                    external_id=_external_id(insight.title, insight.content),
                    platform=Platform.TELEGRAM,
                    importance=min(insight.importance, MAX_AI_IMPORTANCE),
                    occurred_at=summary.period_end,
                    review_status=ReviewStatus.PENDING,
                    extra={
                        "insight_type": insight.type,
                        "generated_by": {"provider": result.provider, "model": result.model or route.model, "task": route.task},
                        "generated_at": utcnow().isoformat(),
                        "confidence": round(insight.confidence, 3),
                        "summary_id": summary.id,
                    },
                ),
                actor="community-pipeline",
                log=False,
            )
            provenance.link(db, DerivedKind.MEMORY, record.id, ids)
            if created:
                report.created += 1
                report.memory_ids.append(record.id)
        summary.memories_extracted_at = utcnow()
        report.summaries += 1
        db.commit()
    return report


async def _is_duplicate(knowledge: KnowledgeService, content: str) -> bool:
    if knowledge.index is None:
        return False
    try:
        hits = await knowledge.index.search(content, MemoryFilters(layers=(MemoryLayer.COMMUNITY,)), limit=1, purpose="document")
    except ProviderError:
        return False
    return bool(hits) and hits[0].similarity >= DUPLICATE_SIMILARITY
