from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select

from app.api.community_schemas import (
    CommunityOverviewOut,
    FaqIn,
    OpportunityOut,
    ProvenanceOut,
    QuestionOut,
    QuestionUpdate,
    SearchMessageOut,
    SearchOut,
    SummaryDetailOut,
    SummaryOut,
    SummaryRequest,
    TelegramMessageOut,
    TopicDetailOut,
    TopicOut,
)
from app.api.deps import DbSession, Runtime, activity_out
from app.api.social_schemas import PilotStatsOut
from app.models.routing import ScheduledJob
from app.models.telegram import TelegramBotState
from app.telegram.service import telegram_status
from app.api.routes.telegram import status_out
from app.api.schemas import MemoryOut
from app.community import provenance
from app.community.questions import recurring_questions
from app.community.search import CommunitySearch, answer_from_evidence
from app.community.summaries import generate_summary
from app.community.tuning import CommunityTuning
from app.db.base import utcnow
from app.timeutil import local_day_start
from app.knowledge.service import KnowledgeService
from app.memory.records import MemoryInput, MemoryRecord
from app.models.community import CommunityQuestion, CommunitySummary, CommunityTopic, ContentOpportunity, ProvenanceLink
from app.models.enums import (
    DerivedKind,
    MemoryKind,
    MemoryLayer,
    OpportunityStatus,
    QuestionStatus,
    ReviewStatus,
    SourceType,
    SummaryKind,
    TopicStatus,
    TopicTrend,
)
from app.models.memory import Memory
from app.models.telegram import TelegramMessage, TelegramSource
from app.services.activity import ActivityLogger, recent_activity
from app.services.agent_service import get_agent_config

router = APIRouter(prefix="/community", tags=["community"])

COMMUNITY_EVENTS = {
    "telegram_ingested", "telegram_error", "telegram_recovered", "telegram_source_added", "telegram_source_updated",
    "telegram_source_deleted", "telegram_data_deleted", "community_summary", "community_memories_proposed",
    "content_opportunities_detected", "community_processing_degraded", "job_failed", "job_recovered",
}


def _tuning(runtime: Runtime) -> CommunityTuning:
    return CommunityTuning.from_settings(runtime.settings)


@router.get("/overview", response_model=CommunityOverviewOut)
def overview(db: DbSession, runtime: Runtime) -> CommunityOverviewOut:
    now = utcnow()
    today = local_day_start(now)
    M = TelegramMessage

    def count(*where) -> int:  # type: ignore[no-untyped-def]
        return int(db.scalar(select(func.count()).select_from(M).where(*where)) or 0)

    def members(since) -> int:  # type: ignore[no-untyped-def]
        return int(
            db.scalar(select(func.count(func.distinct(M.author_id))).where(M.sent_at >= since, M.author_is_bot.is_(False))) or 0
        )

    active = list(db.scalars(select(CommunityTopic).where(CommunityTopic.status == TopicStatus.ACTIVE)))
    latest = db.scalar(select(CommunitySummary).where(CommunitySummary.topic_id.is_(None)).order_by(CommunitySummary.period_end.desc()).limit(1))
    opps = db.scalars(
        select(ContentOpportunity)
        .where(ContentOpportunity.status.in_([OpportunityStatus.DETECTED, OpportunityStatus.REVIEWING, OpportunityStatus.APPROVED]))
        .order_by(ContentOpportunity.created_at.desc())
        .limit(5)
    )
    activity = [a for a in recent_activity(db, limit=200) if a.event in COMMUNITY_EVENTS][:12]
    return CommunityOverviewOut(
        telegram=status_out(db, runtime),
        messages_total=count(),
        messages_today=count(M.sent_at >= today),
        active_members_7d=members(now - timedelta(days=7)),
        active_members_today=members(today),
        unprocessed=count(M.processed_at.is_(None)),
        top_topics=[TopicOut.model_validate(t) for t in sorted(active, key=lambda t: t.message_count, reverse=True)[:6]],
        trending_topics=[
            TopicOut.model_validate(t)
            for t in sorted(active, key=lambda t: t.count_24h, reverse=True)
            if t.trend in (TopicTrend.NEW, TopicTrend.RISING)
        ][:6],
        recurring_questions=[QuestionOut.model_validate(q) for q in recurring_questions(db, _tuning(runtime), limit=8)],
        latest_summary=SummaryOut.model_validate(latest) if latest else None,
        opportunities=[OpportunityOut.model_validate(o) for o in opps],
        pending_memories=int(
            db.scalar(
                select(func.count()).select_from(Memory).where(
                    Memory.layer == MemoryLayer.COMMUNITY, Memory.review_status == ReviewStatus.PENDING
                )
            )
            or 0
        ),
        recent_activity=[activity_out(a).model_dump(mode="json") for a in activity],
    )


# --- topics ------------------------------------------------------------------------------------


@router.get("/topics", response_model=list[TopicOut])
def list_topics(
    db: DbSession,
    status_filter: TopicStatus | None = Query(TopicStatus.ACTIVE, alias="status"),
    sort: Literal["volume", "recent", "trending", "importance"] = "importance",
    limit: int = Query(50, ge=1, le=200),
) -> list[TopicOut]:
    stmt = select(CommunityTopic)
    if status_filter is not None:
        stmt = stmt.where(CommunityTopic.status == status_filter)
    order = {
        "volume": CommunityTopic.message_count.desc(),
        "recent": CommunityTopic.last_seen_at.desc(),
        "trending": CommunityTopic.count_24h.desc(),
        "importance": CommunityTopic.importance.desc(),
    }[sort]
    return [TopicOut.model_validate(t) for t in db.scalars(stmt.order_by(order).limit(limit))]


@router.get("/topics/{topic_id}", response_model=TopicDetailOut)
def topic_detail(topic_id: int, db: DbSession) -> TopicDetailOut:
    topic = db.get(CommunityTopic, topic_id)
    if topic is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Topic not found")
    M = TelegramMessage
    reps = {m.id: m for m in db.scalars(select(M).where(M.id.in_(topic.representative_message_ids or [])))}
    recent = list(db.scalars(select(M).where(M.topic_id == topic.id).order_by(M.sent_at.desc()).limit(50)))
    topic_message_ids = select(M.id).where(M.topic_id == topic.id)
    memory_ids = set(
        db.scalars(
            select(ProvenanceLink.derived_id).where(
                ProvenanceLink.derived_kind == DerivedKind.MEMORY, ProvenanceLink.message_id.in_(topic_message_ids)
            )
        )
    )
    memories = [MemoryOut.model_validate(MemoryRecord.from_row(m)) for m in db.scalars(select(Memory).where(Memory.id.in_(memory_ids)))]
    day = func.date(M.sent_at)
    volume = [
        {"day": str(d), "messages": int(c)}
        for d, c in db.execute(select(day, func.count()).where(M.topic_id == topic.id).group_by(day).order_by(day)).all()
    ]
    return TopicDetailOut(
        topic=TopicOut.model_validate(topic),
        representative=[TelegramMessageOut.model_validate(reps[i]) for i in topic.representative_message_ids or [] if i in reps],
        recent=[TelegramMessageOut.model_validate(m) for m in recent],
        questions=[QuestionOut.model_validate(q) for q in db.scalars(select(CommunityQuestion).where(CommunityQuestion.topic_id == topic.id).order_by(CommunityQuestion.ask_count.desc()).limit(20))],
        memories=memories,
        summaries=[SummaryOut.model_validate(s) for s in db.scalars(select(CommunitySummary).where(CommunitySummary.topic_id == topic.id).order_by(CommunitySummary.generated_at.desc()).limit(5))],
        opportunities=[OpportunityOut.model_validate(o) for o in db.scalars(select(ContentOpportunity).where(ContentOpportunity.origin_kind == "topic", ContentOpportunity.origin_id == topic.id))],
        volume_by_day=volume,
    )


@router.patch("/topics/{topic_id}", response_model=TopicOut)
def update_topic(topic_id: int, db: DbSession, status_value: TopicStatus = Query(..., alias="status")) -> TopicOut:
    topic = db.get(CommunityTopic, topic_id)
    if topic is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Topic not found")
    topic.status = status_value
    db.commit()
    return TopicOut.model_validate(topic)


# --- questions ----------------------------------------------------------------------------------


@router.get("/questions", response_model=list[QuestionOut])
def list_questions(
    db: DbSession,
    runtime: Runtime,
    recurring_only: bool = True,
    status_filter: QuestionStatus | None = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=200),
) -> list[QuestionOut]:
    if recurring_only and status_filter is None:
        return [QuestionOut.model_validate(q) for q in recurring_questions(db, _tuning(runtime), limit=limit)]
    stmt = select(CommunityQuestion)
    if status_filter is not None:
        stmt = stmt.where(CommunityQuestion.status == status_filter)
    stmt = stmt.order_by(CommunityQuestion.ask_count.desc(), CommunityQuestion.last_asked_at.desc()).limit(limit)
    return [QuestionOut.model_validate(q) for q in db.scalars(stmt)]


def _question(db: DbSession, question_id: int) -> CommunityQuestion:
    q = db.get(CommunityQuestion, question_id)
    if q is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Question not found")
    return q


@router.patch("/questions/{question_id}", response_model=QuestionOut)
def update_question(question_id: int, payload: QuestionUpdate, db: DbSession) -> QuestionOut:
    q = _question(db, question_id)
    for key, value in payload.model_dump(exclude_unset=True, exclude_none=True).items():
        setattr(q, key, value)
    db.commit()
    return QuestionOut.model_validate(q)


@router.get("/questions/{question_id}/messages", response_model=list[TelegramMessageOut])
def question_messages(question_id: int, db: DbSession) -> list[TelegramMessageOut]:
    _question(db, question_id)
    stmt = select(TelegramMessage).where(TelegramMessage.question_id == question_id).order_by(TelegramMessage.sent_at.desc()).limit(100)
    return [TelegramMessageOut.model_validate(m) for m in db.scalars(stmt)]


@router.post("/questions/{question_id}/faq", response_model=QuestionOut)
async def create_faq(question_id: int, payload: FaqIn, db: DbSession, runtime: Runtime) -> QuestionOut:
    """Turn a recurring question into an approved FAQ entry, with an answer written by a human."""
    q = _question(db, question_id)
    agent_id = get_agent_config(db).id
    record, _, _ = await KnowledgeService(db, agent_id, runtime.embedder).add(
        MemoryInput(
            content=f"Q: {q.text}\nA: {payload.answer}",
            title=f"FAQ: {q.text[:200]}",
            kind=MemoryKind.BRAND_FACT,
            layer=MemoryLayer.BRAND,
            tags=["faq", "community question"],
            source_type=SourceType.MANUAL,
            source=f"FAQ from community question #{q.id}",
            importance=0.8,
        )
    )
    msg_ids = list(db.scalars(select(TelegramMessage.id).where(TelegramMessage.question_id == q.id).limit(50)))
    provenance.link(db, DerivedKind.MEMORY, record.id, msg_ids)
    q.faq_memory_id = record.id
    q.status = QuestionStatus.ANSWERED
    db.commit()
    return QuestionOut.model_validate(q)


# --- summaries ----------------------------------------------------------------------------------


@router.get("/summaries", response_model=list[SummaryOut])
def list_summaries(
    db: DbSession,
    kind: SummaryKind | None = None,
    source_id: int | None = None,
    limit: int = Query(20, ge=1, le=100),
) -> list[SummaryOut]:
    stmt = select(CommunitySummary)
    if kind is not None:
        stmt = stmt.where(CommunitySummary.kind == kind)
    if source_id is not None:
        stmt = stmt.where(CommunitySummary.source_id == source_id)
    return [SummaryOut.model_validate(s) for s in db.scalars(stmt.order_by(CommunitySummary.period_end.desc()).limit(limit))]


@router.get("/summaries/{summary_id}", response_model=SummaryDetailOut)
def summary_detail(summary_id: int, db: DbSession) -> SummaryDetailOut:
    s = db.get(CommunitySummary, summary_id)
    if s is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Summary not found")
    return SummaryDetailOut(
        summary=SummaryOut.model_validate(s),
        messages=[TelegramMessageOut.model_validate(m) for m in provenance.messages_for(db, DerivedKind.SUMMARY, s.id, limit=300)],
    )


@router.post("/summaries", response_model=SummaryOut, status_code=status.HTTP_201_CREATED)
async def create_summary(payload: SummaryRequest, db: DbSession, runtime: Runtime) -> SummaryOut:
    """Generate a summary now (recent window, a full day, or a topic)."""
    now = utcnow()
    source = db.get(TelegramSource, payload.source_id) if payload.source_id else None
    topic = db.get(CommunityTopic, payload.topic_id) if payload.topic_id else None
    if payload.source_id and source is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Telegram source not found")
    if payload.kind == SummaryKind.TOPIC and topic is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "topic_id is required for a topic summary")
    start = now - timedelta(hours=payload.hours if payload.kind != SummaryKind.TOPIC else 24 * 30)
    summary = await generate_summary(
        db, runtime.router(db), _tuning(runtime), payload.kind, start, now, source=source, topic=topic, min_messages=1
    )
    if summary is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "No community messages in that period to summarise")
    ActivityLogger(db).log(
        "community_summary",
        f"Generated {summary.kind.value} summary on request: {summary.headline} ({summary.message_count} messages, {summary.model})",
        actor="dashboard",
        details={"summary_id": summary.id, "model": summary.model},
    )
    return SummaryOut.model_validate(summary)


# --- search & provenance ---------------------------------------------------------------------


@router.get("/search", response_model=SearchOut)
async def search(
    db: DbSession,
    runtime: Runtime,
    q: str = Query(..., min_length=2, max_length=500),
    answer: bool = False,
    source_id: int | None = None,
) -> SearchOut:
    agent_id = get_agent_config(db).id
    results = await CommunitySearch(db, agent_id, runtime.embedder).search(q, source_id=source_id)
    sources = {s.id: s.display_name for s in db.scalars(select(TelegramSource))}
    answer_out = await answer_from_evidence(runtime.router(db), q, results) if answer else None
    w = results.window
    return SearchOut(
        query=q,
        window={"start": w.start, "end": w.end, "label": w.label},
        messages=[
            SearchMessageOut(message=TelegramMessageOut.model_validate(h.message), score=h.score, reason=h.reason, source_name=sources.get(h.message.source_id))
            for h in results.messages
        ],
        memories=[{"memory": MemoryOut.model_validate(r).model_dump(mode="json"), "score": s} for r, s in results.memories],
        summaries=[SummaryOut.model_validate(s) for s in results.summaries],
        topics=[{"topic": TopicOut.model_validate(t).model_dump(mode="json"), "score": s} for t, s in results.topics],
        notes=results.notes,
        answer=answer_out,
    )


@router.get("/provenance/{kind}/{derived_id}", response_model=ProvenanceOut)
def get_provenance(kind: DerivedKind, derived_id: int, db: DbSession) -> ProvenanceOut:
    """The Telegram messages behind any derived record, plus how it was generated."""
    generated = None
    if kind == DerivedKind.MEMORY:
        m = db.get(Memory, derived_id)
        generated = (m.extra or {}) if m else None
    elif kind == DerivedKind.SUMMARY:
        s = db.get(CommunitySummary, derived_id)
        generated = {"provider": s.provider, "model": s.model, "generated_at": s.generated_at.isoformat()} if s else None
    elif kind == DerivedKind.OPPORTUNITY:
        o = db.get(ContentOpportunity, derived_id)
        generated = {"provider": o.provider, "model": o.model, "generated_at": o.generated_at.isoformat() if o.generated_at else None, "confidence": o.confidence} if o else None
    messages = provenance.messages_for(db, kind, derived_id, limit=200)
    if kind == DerivedKind.TOPIC:
        messages = list(db.scalars(select(TelegramMessage).where(TelegramMessage.topic_id == derived_id).order_by(TelegramMessage.sent_at).limit(200)))
    elif kind == DerivedKind.QUESTION:
        messages = list(db.scalars(select(TelegramMessage).where(TelegramMessage.question_id == derived_id).order_by(TelegramMessage.sent_at).limit(200)))
    return ProvenanceOut(kind=kind.value, id=derived_id, generated=generated, messages=[TelegramMessageOut.model_validate(m) for m in messages])


# --- Phase 3B: live pilot statistics --------------------------------------------------------

PILOT_JOBS = ("telegram.ingest", "community.process", "community.topics", "community.summary.recent", "community.memories", "content.opportunities")


@router.get("/pilot", response_model=PilotStatsOut)
def pilot_stats(db: DbSession, runtime: Runtime) -> PilotStatsOut:
    M = TelegramMessage
    today = local_day_start(utcnow())

    def n(stmt) -> int:  # type: ignore[no-untyped-def]
        return int(db.scalar(stmt) or 0)

    state = db.scalar(select(TelegramBotState).limit(1))
    discarded_now = sum(d.ignored_messages for d in runtime.telegram.discovered.values())
    jobs = []
    for name in PILOT_JOBS:
        row = db.get(ScheduledJob, name)
        if row is None:
            continue
        running = runtime.scheduler.is_running(name) if runtime.scheduler and name in runtime.scheduler.specs else False
        jobs.append({
            "name": name, "enabled": row.enabled, "status": "running" if running else row.last_status,
            "last_run_at": row.last_run_at, "next_run_at": row.next_run_at, "last_error": row.last_error,
            "last_result": row.last_result,
        })
    ingest = db.get(ScheduledJob, "telegram.ingest")
    tg = telegram_status(db, runtime.settings)
    processed = db.get(ScheduledJob, "community.process")
    return PilotStatsOut(
        ingestion_enabled=ingest.enabled if ingest else True,
        telegram_status=tg.status,
        telegram_error=tg.last_error,
        authorised_chats=n(select(func.count()).select_from(TelegramSource).where(TelegramSource.enabled.is_(True))),
        last_message_at=db.scalar(select(func.max(M.sent_at))),
        last_processed_at=processed.last_finished_at if processed and processed.last_status == "success" else None,
        messages_today=n(select(func.count()).select_from(M).where(M.sent_at >= today)),
        messages_total=n(select(func.count()).select_from(M)),
        messages_processed=n(select(func.count()).select_from(M).where(M.processed_at.is_not(None))),
        messages_unprocessed=n(select(func.count()).select_from(M).where(M.processed_at.is_(None))),
        messages_ignored=(state.ignored_updates if state else 0) + discarded_now,
        community_memories=n(select(func.count()).select_from(Memory).where(Memory.layer == MemoryLayer.COMMUNITY)),
        pending_memories=n(select(func.count()).select_from(Memory).where(Memory.layer == MemoryLayer.COMMUNITY, Memory.review_status == ReviewStatus.PENDING)),
        topics=n(select(func.count()).select_from(CommunityTopic).where(CommunityTopic.status == TopicStatus.ACTIVE)),
        questions=n(select(func.count()).select_from(CommunityQuestion).where(CommunityQuestion.status != QuestionStatus.DISMISSED)),
        opportunities=n(select(func.count()).select_from(ContentOpportunity).where(ContentOpportunity.status.in_([OpportunityStatus.DETECTED, OpportunityStatus.REVIEWING, OpportunityStatus.APPROVED]))),
        jobs=jobs,
    )
