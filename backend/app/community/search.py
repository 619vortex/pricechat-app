"""Search community intelligence and (optionally) answer from the evidence found.

Returns traceable evidence first: raw messages, community memories, summaries and
topics, each with ids and timestamps. The optional answer is generated only from
that evidence, must cite it, and is labelled as AI-generated.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import numpy as np
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.ai.routing import ModelRouter
from app.ai.structured import complete_structured
from app.community.vectors import from_blob, normalise
from app.db.base import utcnow
from app.timeutil import local_day_start, local_tz
from app.memory.embeddings import EmbeddingIndex
from app.memory.records import MemoryRecord
from app.memory.retrieval.lexical import query_terms
from app.memory.store import MemoryFilters, MemoryStore
from app.models.community import CommunitySummary, CommunityTopic
from app.models.enums import MemoryLayer, MessageCategory, TopicStatus
from app.models.telegram import TelegramMessage, TelegramMessageVector
from app.providers.base import EmbeddingProvider
from app.providers.errors import ProviderError

MESSAGE_MIN_SIMILARITY = 0.55


@dataclass(frozen=True, slots=True)
class TimeWindow:
    start: datetime | None
    end: datetime | None
    label: str | None


def parse_time_window(query: str, now: datetime | None = None) -> TimeWindow:
    """Understand simple time phrases: today, yesterday, this/last week, this month, last N days/hours."""
    now = now or utcnow()
    q = query.lower()
    midnight = local_day_start(now)
    if m := re.search(r"\b(?:last|past)\s+(\d{1,3})\s+(day|days|hour|hours)\b", q):
        n, unit = int(m.group(1)), m.group(2)
        delta = timedelta(days=n) if unit.startswith("day") else timedelta(hours=n)
        return TimeWindow(now - delta, now, m.group(0))
    if "yesterday" in q:
        return TimeWindow(midnight - timedelta(days=1), midnight, "yesterday")
    if "today" in q:
        return TimeWindow(midnight, now, "today")
    weekday = now.astimezone(local_tz()).weekday()
    if "last week" in q:
        week_start = local_day_start(now, weekday)
        return TimeWindow(local_day_start(now, weekday + 7), week_start, "last week")
    if "this week" in q:
        return TimeWindow(local_day_start(now, weekday), now, "this week")
    if "this month" in q:
        return TimeWindow(local_day_start(now, now.astimezone(local_tz()).day - 1), now, "this month")
    return TimeWindow(None, None, None)


@dataclass(slots=True)
class MessageHit:
    message: TelegramMessage
    score: float
    reason: str


@dataclass(slots=True)
class SearchResults:
    query: str
    window: TimeWindow
    messages: list[MessageHit] = field(default_factory=list)
    memories: list[tuple[MemoryRecord, float]] = field(default_factory=list)
    summaries: list[CommunitySummary] = field(default_factory=list)
    topics: list[tuple[CommunityTopic, float]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


class CommunitySearch:
    def __init__(self, db: Session, agent_id: int, embedder: EmbeddingProvider | None) -> None:
        self.db = db
        self.agent_id = agent_id
        self.embedder = embedder

    async def search(self, query: str, limit: int = 15, source_id: int | None = None, now: datetime | None = None) -> SearchResults:
        window = parse_time_window(query, now)
        results = SearchResults(query, window)
        qvec = None
        if self.embedder is not None:
            try:
                qvec = normalise((await self.embedder.embed([query], purpose="query"))[0])
            except ProviderError as exc:
                results.notes.append(f"Meaning-based search unavailable, using keywords: {exc}")
        else:
            results.notes.append("Meaning-based search disabled; using keywords.")

        results.messages = self._messages(query, qvec, window, limit, source_id)
        results.memories = await self._memories(query, limit=6)
        results.summaries = self._summaries(window, source_id, query)
        results.topics = self._topics(query, qvec, window)
        return results

    def _messages(self, query: str, qvec: np.ndarray | None, window: TimeWindow, limit: int, source_id: int | None) -> list[MessageHit]:
        base = select(TelegramMessage).where(
            (TelegramMessage.category.is_(None)) | (TelegramMessage.category != MessageCategory.NOISE)
        )
        if window.start is not None:
            base = base.where(TelegramMessage.sent_at >= window.start, TelegramMessage.sent_at < window.end)
        if source_id is not None:
            base = base.where(TelegramMessage.source_id == source_id)

        hits: dict[int, MessageHit] = {}
        if qvec is not None and self.embedder is not None:
            rows = self.db.execute(
                select(TelegramMessageVector.message_id, TelegramMessageVector.vector)
                .join(TelegramMessage, TelegramMessage.id == TelegramMessageVector.message_id)
                .where(TelegramMessageVector.model == self.embedder.model, TelegramMessage.id.in_(base.with_only_columns(TelegramMessage.id)))
            ).all()
            scored = sorted(
                ((float(from_blob(blob) @ qvec), mid) for mid, blob in rows if len(blob) == qvec.nbytes), reverse=True
            )[: limit * 2]
            keep = {mid: s for s, mid in scored if s >= MESSAGE_MIN_SIMILARITY}
            for m in self.db.scalars(select(TelegramMessage).where(TelegramMessage.id.in_(keep))):
                hits[m.id] = MessageHit(m, round(keep[m.id], 3), f"meaning match {keep[m.id]:.2f}")

        terms = [t for t in query_terms(query) if t not in {"community", "say", "said", "talk", "people", "member", "week", "yesterday", "today", "question", "ask"}]
        if terms:
            clauses = [TelegramMessage.text.ilike(f"%{t}%") for t in terms]
            for m in self.db.scalars(base.where(or_(*clauses)).order_by(TelegramMessage.sent_at.desc()).limit(limit * 2)):
                matched = [t for t in terms if t in m.text.lower()]
                score = 0.4 + 0.4 * len(matched) / len(terms)
                if m.id in hits:
                    hits[m.id].score = round(min(1.0, hits[m.id].score + 0.1), 3)
                    hits[m.id].reason += f" · keywords: {', '.join(matched)}"
                else:
                    hits[m.id] = MessageHit(m, round(score, 3), f"keywords: {', '.join(matched)}")

        if not terms and qvec is None and window.start is not None:
            for m in self.db.scalars(base.order_by(TelegramMessage.sent_at.desc()).limit(limit)):
                hits[m.id] = MessageHit(m, 0.3, "in time range")
        return sorted(hits.values(), key=lambda h: (h.score, h.message.sent_at), reverse=True)[:limit]

    async def _memories(self, query: str, limit: int) -> list[tuple[MemoryRecord, float]]:
        store = MemoryStore(self.db, self.agent_id)
        filters = MemoryFilters(layers=(MemoryLayer.COMMUNITY,))
        if self.embedder is not None:
            try:
                hits = await EmbeddingIndex(self.db, self.agent_id, self.embedder).search(query, filters, limit)
                records = store.get_many([h.memory_id for h in hits if h.similarity >= 0.55])
                return [(records[h.memory_id], round(h.similarity, 3)) for h in hits if h.memory_id in records]
            except ProviderError:
                pass
        return [(r, 0.5) for r in store.search_terms(query_terms(query), filters, limit)]

    def _summaries(self, window: TimeWindow, source_id: int | None, query: str) -> list[CommunitySummary]:
        stmt = select(CommunitySummary).order_by(CommunitySummary.period_end.desc())
        if window.start is not None:
            stmt = stmt.where(CommunitySummary.period_end > window.start, CommunitySummary.period_start < window.end)
        if source_id is not None:
            stmt = stmt.where(CommunitySummary.source_id == source_id)
        summaries = list(self.db.scalars(stmt.limit(20)))
        terms = query_terms(query)
        if window.start is None and terms:
            matching = [s for s in summaries if any(t in (s.headline + s.body).lower() for t in terms)]
            summaries = matching or summaries[:1]
        return summaries[:3]

    def _topics(self, query: str, qvec: np.ndarray | None, window: TimeWindow) -> list[tuple[CommunityTopic, float]]:
        stmt = select(CommunityTopic).where(CommunityTopic.status == TopicStatus.ACTIVE)
        if window.start is not None:
            stmt = stmt.where(CommunityTopic.last_seen_at >= window.start)
        topics = list(self.db.scalars(stmt))
        terms = query_terms(query)
        scored: list[tuple[CommunityTopic, float]] = []
        for t in topics:
            score = 0.0
            if qvec is not None and t.centroid and len(t.centroid) == qvec.nbytes:
                score = float(from_blob(t.centroid) @ qvec)
            text = f"{t.name} {t.description} {' '.join(t.keywords)}".lower()
            if any(term in text for term in terms):
                score = max(score, 0.6) + 0.1
            if score >= 0.55:
                scored.append((t, round(score, 3)))
        if not scored and window.start is not None:
            # "What were the biggest topics yesterday?" → the busiest topics in that window.
            scored = [(t, 0.5) for t in sorted(topics, key=lambda t: t.message_count, reverse=True)[:5]]
        return sorted(scored, key=lambda x: x[1], reverse=True)[:5]


# --- grounded answer ------------------------------------------------------------------------


class _Answer(BaseModel):
    answer: str = Field(description="Answer using only the evidence; cite ids like [m12], [s3], [t4], [k7]")
    cited: list[str] = Field(description="Every evidence id used")
    sufficient: bool = Field(description="false if the evidence is not enough to answer")


ANSWER_SYSTEM = (
    "You answer questions about what a brand's community has been discussing, using ONLY the evidence provided. "
    "Evidence types: [m..] raw member messages, [k..] community memories (observations, not verified facts), "
    "[s..] AI-generated summaries (interpretations, not quotes), [t..] detected topics.\n"
    "- Cite evidence ids for every claim.\n- Do not quote members by name; say 'members'.\n"
    "- If the evidence does not answer the question, say so and set sufficient=false.\nReturn JSON only."
)


def evidence_text(results: SearchResults) -> tuple[str, set[str]]:
    lines, ids = [], set()
    for t, _ in results.topics:
        ids.add(f"t{t.id}")
        lines.append(f"[t{t.id}] Topic '{t.name}': {t.description} ({t.message_count} messages, trend {t.trend.value})")
    for s in results.summaries:
        ids.add(f"s{s.id}")
        lines.append(f"[s{s.id}] Summary {s.period_start:%Y-%m-%d %H:%M}–{s.period_end:%H:%M}: {s.headline}. {s.data.get('overview', '')}")
    for r, _ in results.memories:
        ids.add(f"k{r.id}")
        lines.append(f"[k{r.id}] Community memory ({r.review_status.value}): {r.title or ''} {r.content}")
    for h in results.messages:
        ids.add(f"m{h.message.id}")
        lines.append(f"[m{h.message.id}] {h.message.sent_at:%Y-%m-%d %H:%M} member: {' '.join(h.message.text.split())[:300]}")
    return "\n".join(lines), ids


async def answer_from_evidence(router: ModelRouter, question: str, results: SearchResults) -> dict[str, object]:
    text, ids = evidence_text(results)
    if not ids:
        return {"answer": "No community evidence matched this question.", "cited": [], "sufficient": False, "model": None}
    out, result, route = await complete_structured(
        router, "community.search_answer", ANSWER_SYSTEM, f"Question: {question}\n\nEvidence:\n{text}", _Answer
    )
    cited = [c.strip("[] ") for c in out.cited if c.strip("[] ") in ids]
    return {
        "answer": out.answer.strip(),
        "cited": cited,
        "sufficient": out.sufficient and bool(cited),
        "model": result.model or route.model,
        "provider": result.provider,
    }
