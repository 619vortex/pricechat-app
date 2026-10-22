"""AI-generated community summaries: recent activity, daily, and per topic.

Summaries are interpretations by a model, stored with full provenance (model,
provider, time, window and every source message) and never presented as quotes.
"""

import logging
import time
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.routing import ModelRouter
from app.ai.structured import complete_structured
from app.community import provenance
from app.community.formatting import format_messages, sample_evenly, strip_citations, valid_ids
from app.community.questions import recurring_questions
from app.community.tuning import CommunityTuning
from app.db.base import utcnow
from app.timeutil import local_day_start
from app.models.community import CommunitySummary, CommunityTopic
from app.models.enums import DerivedKind, MessageCategory, SummaryKind, TopicStatus
from app.models.telegram import TelegramMessage, TelegramSource

logger = logging.getLogger(__name__)

TASK_FOR_KIND = {
    SummaryKind.RECENT: "community.summary.recent",
    SummaryKind.DAILY: "community.summary.daily",
    SummaryKind.TOPIC: "community.summary.topic",
}


class Point(BaseModel):
    text: str
    message_ids: list[str] = Field(default_factory=list, description="ids like m123 that support this point")


class SummaryOutput(BaseModel):
    headline: str = Field(description="The main takeaway in at most 15 words; not a date range or chat name")
    overview: str = Field(description="2-4 sentences: what the community is talking about")
    themes: list[Point] = Field(description="Main discussion themes")
    emerging: list[Point] = Field(description="Topics that are becoming important; empty if none")
    questions: list[Point] = Field(description="Questions that keep appearing")
    concerns: list[Point] = Field(description="Recurring worries or complaints; empty if none")
    ideas: list[Point] = Field(description="Interesting ideas or suggestions from members; empty if none")
    content_suggestions: list[Point] = Field(description="Things the brand could create content about, based on the discussion")


SECTIONS = [
    ("themes", "What the community is talking about"),
    ("emerging", "Becoming important"),
    ("questions", "Questions that keep appearing"),
    ("concerns", "Recurring concerns"),
    ("ideas", "Interesting ideas"),
    ("content_suggestions", "Possible content"),
]

SYSTEM = (
    "You are a community analyst for a brand. You read a chat log from the brand's community and report what "
    "members are discussing. Rules:\n"
    "- Describe what members said; do not add facts, prices or predictions of your own.\n"
    "- Paraphrase; never invent quotes. Refer to people only as 'members'.\n"
    "- Cite supporting messages for every point using their ids (e.g. m123).\n"
    "- If there is little discussion, say so briefly and leave sections empty rather than padding them.\n"
    "Return JSON only."
)


def _window_messages(
    db: Session, start: datetime, end: datetime, source_id: int | None = None, topic_id: int | None = None
) -> list[TelegramMessage]:
    stmt = select(TelegramMessage).where(
        TelegramMessage.sent_at >= start,
        TelegramMessage.sent_at < end,
        TelegramMessage.author_is_bot.is_(False),
        (TelegramMessage.category.is_(None)) | (TelegramMessage.category != MessageCategory.NOISE),
    )
    if source_id is not None:
        stmt = stmt.where(TelegramMessage.source_id == source_id)
    if topic_id is not None:
        stmt = stmt.where(TelegramMessage.topic_id == topic_id)
    return list(db.scalars(stmt.order_by(TelegramMessage.sent_at)))


def render_body(out: SummaryOutput) -> str:
    parts = [out.overview.strip()]
    for key, title in SECTIONS:
        points = getattr(out, key)
        if points:
            parts.append(f"### {title}\n" + "\n".join(f"- {p.text.strip()}" for p in points if p.text.strip()))
    return "\n\n".join(parts)


async def generate_summary(
    db: Session,
    router: ModelRouter,
    tuning: CommunityTuning,
    kind: SummaryKind,
    start: datetime,
    end: datetime,
    source: TelegramSource | None = None,
    topic: CommunityTopic | None = None,
    min_messages: int = 5,
    max_messages: int = 250,
) -> CommunitySummary | None:
    messages = _window_messages(db, start, end, source.id if source else None, topic.id if topic else None)
    if len(messages) < min_messages:
        return None

    sample = sample_evenly(messages, max_messages)
    text, included = format_messages(sample, char_budget=14000, max_chars_each=400)
    context = _context(db, tuning, start, end, topic)
    scope = f"topic '{topic.name}'" if topic else f"chat '{source.display_name}'" if source else "all authorised chats"
    user = (
        f"Scope: {scope}\nPeriod: {start:%Y-%m-%d %H:%M} to {end:%Y-%m-%d %H:%M} UTC\n"
        f"Messages in period: {len(messages)} (showing {len(included)})\n{context}\n\nChat log:\n{text}"
    )

    started = time.perf_counter()
    out, result, route = await complete_structured(router, TASK_FOR_KIND[kind], SYSTEM, user, SummaryOutput)
    out.overview = strip_citations(out.overview)
    out.headline = strip_citations(out.headline)
    allowed = set(included)
    data: dict[str, list[dict[str, object]]] = {}
    dropped = 0
    for key, _ in SECTIONS:
        points = []
        for p in getattr(out, key):
            ids = valid_ids(p.message_ids, allowed)
            dropped += len(p.message_ids) - len(ids)
            text = strip_citations(p.text)
            p.text = text
            if text:
                points.append({"text": text, "message_ids": ids})
        data[key] = points

    summary = CommunitySummary(
        kind=kind,
        source_id=source.id if source else None,
        topic_id=topic.id if topic else None,
        period_start=start,
        period_end=end,
        headline=out.headline.strip()[:300] or "Community summary",
        body=render_body(out),
        data={**data, "overview": out.overview.strip(), "invalid_citations_dropped": dropped},
        message_count=len(messages),
        participant_count=len({m.author_id for m in messages if m.author_id is not None}),
        provider=result.provider,
        model=result.model or route.model,
        generated_at=utcnow(),
        duration_ms=round((time.perf_counter() - started) * 1000, 1),
    )
    db.add(summary)
    db.flush()
    provenance.link(db, DerivedKind.SUMMARY, summary.id, included)
    db.commit()
    return summary


def _context(db: Session, tuning: CommunityTuning, start: datetime, end: datetime, topic: CommunityTopic | None) -> str:
    if topic is not None:
        return f"Topic description: {topic.description or '(none yet)'}"
    lines = []
    topics = list(
        db.scalars(
            select(CommunityTopic)
            .where(CommunityTopic.status == TopicStatus.ACTIVE, CommunityTopic.last_seen_at >= start)
            .order_by(CommunityTopic.count_24h.desc())
            .limit(8)
        )
    )
    if topics:
        lines.append("Detected topics (by recent volume): " + "; ".join(f"{t.name} ({t.count_24h} msgs/24h, {t.trend.value})" for t in topics))
    questions = recurring_questions(db, tuning, limit=6, since=start)
    if questions:
        lines.append("Recurring questions: " + "; ".join(f"{q.text} (asked {q.ask_count}×)" for q in questions))
    return "\n".join(lines)


# --- scheduling helpers ---------------------------------------------------------------


async def run_recent_summaries(db: Session, router: ModelRouter, tuning: CommunityTuning, min_messages: int, now: datetime | None = None) -> list[CommunitySummary]:
    """One 'recent activity' summary per chat, covering what happened since the last one (max 6h)."""
    now = now or utcnow()
    made = []
    for source in db.scalars(select(TelegramSource).where(TelegramSource.enabled.is_(True))):
        last_end = db.scalar(
            select(func.max(CommunitySummary.period_end)).where(
                CommunitySummary.kind == SummaryKind.RECENT, CommunitySummary.source_id == source.id
            )
        )
        start = max(_aware(last_end), now - timedelta(hours=6)) if last_end else now - timedelta(hours=6)
        summary = await generate_summary(db, router, tuning, SummaryKind.RECENT, start, now, source=source, min_messages=min_messages)
        if summary:
            made.append(summary)
    return made


async def run_daily_summaries(db: Session, router: ModelRouter, tuning: CommunityTuning, now: datetime | None = None) -> list[CommunitySummary]:
    """Yesterday's summary (local day) for each chat, once."""
    now = now or utcnow()
    day_end = local_day_start(now)
    day_start = local_day_start(now, 1)
    made = []
    for source in db.scalars(select(TelegramSource).where(TelegramSource.enabled.is_(True))):
        exists = db.scalar(
            select(CommunitySummary.id).where(
                CommunitySummary.kind == SummaryKind.DAILY,
                CommunitySummary.source_id == source.id,
                CommunitySummary.period_start == day_start,
            )
        )
        if exists:
            continue
        summary = await generate_summary(db, router, tuning, SummaryKind.DAILY, day_start, day_end, source=source, min_messages=5)
        if summary:
            made.append(summary)
    return made


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
