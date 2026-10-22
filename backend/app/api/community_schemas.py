from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.api.schemas import MemoryOut, StrictInput
from app.models.enums import (
    ContentChannel,
    MessageCategory,
    OpportunityStatus,
    QuestionStatus,
    SummaryKind,
    TopicStatus,
    TopicTrend,
    Urgency,
)


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- Telegram ---------------------------------------------------------------------------


class TelegramSourceOut(ORM):
    id: int
    chat_id: int
    display_name: str
    chat_type: str | None
    username: str | None
    enabled: bool
    ingestion_enabled: bool
    created_at: datetime  # date added
    last_sync_at: datetime | None
    last_message_at: datetime | None
    message_count: int
    last_error: str | None
    via: str = "bot"  # bot | account


class TelegramSourceIn(StrictInput):
    chat_id: int = Field(..., description="Telegram chat id, e.g. -1001234567890")
    display_name: Annotated[str, StringConstraints(strip_whitespace=True, max_length=256)] | None = None


class TelegramSourceUpdate(StrictInput):
    display_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)] | None = None
    enabled: bool | None = None
    ingestion_enabled: bool | None = None


class DiscoveredChatOut(BaseModel):
    chat_id: int
    title: str
    chat_type: str
    last_seen_at: datetime
    ignored_messages: int
    bot_status: str | None


class TelegramStatusOut(BaseModel):
    configured: bool
    status: str
    bot_username: str | None
    bot_name: str | None
    last_sync_at: datetime | None
    last_success_at: datetime | None
    last_error: str | None
    last_error_at: datetime | None
    messages_ingested: int
    ignored_updates: int
    sources: list[TelegramSourceOut]
    discovered_chats: list[DiscoveredChatOut]
    setup_steps: list[str]


class TelegramMessageOut(ORM):
    id: int
    source_id: int
    chat_id: int
    message_id: int
    thread_id: int | None
    reply_to_message_id: int | None
    author_id: int | None
    author_name: str | None
    author_is_bot: bool
    sent_at: datetime
    edited_at: datetime | None
    text: str
    media_type: str | None
    forward_from: str | None
    category: MessageCategory | None
    is_question: bool
    topic_id: int | None
    question_id: int | None
    processed_at: datetime | None
    created_at: datetime  # ingestion time


class DeletionOut(BaseModel):
    messages: int
    summaries: int
    memories: int
    opportunities: int


# --- community ----------------------------------------------------------------------------


class TopicOut(ORM):
    id: int
    name: str
    description: str
    keywords: list[str]
    status: TopicStatus
    first_seen_at: datetime
    last_seen_at: datetime
    message_count: int
    participant_count: int
    count_24h: int
    count_prev_24h: int
    trend: TopicTrend
    importance: float
    confidence: float
    named_by_model: str | None
    aliases: list[str] = []
    related_ids: list[int] = []
    canonical_id: int | None = None
    review_status: str = "pending"
    user_importance: float | None = None


class QuestionOut(ORM):
    id: int
    text: str
    example: str
    status: QuestionStatus
    ask_count: int
    asker_count: int
    first_asked_at: datetime
    last_asked_at: datetime
    topic_id: int | None
    faq_memory_id: int | None
    canonical_model: str | None
    review_status: str = "pending"
    user_importance: float | None = None


class QuestionUpdate(StrictInput):
    status: QuestionStatus | None = None
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=300)] | None = None


class FaqIn(StrictInput):
    answer: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=4000)]


class SummaryOut(ORM):
    id: int
    kind: SummaryKind
    source_id: int | None
    topic_id: int | None
    period_start: datetime
    period_end: datetime
    headline: str
    body: str
    data: dict[str, Any]
    message_count: int
    participant_count: int
    provider: str
    model: str
    generated_at: datetime
    duration_ms: float | None


class SummaryRequest(StrictInput):
    kind: SummaryKind = SummaryKind.RECENT
    source_id: int | None = None
    topic_id: int | None = None
    hours: int = Field(6, ge=1, le=24 * 30)


class OpportunityOut(ORM):
    id: int
    title: str
    why: str
    angle: str
    evidence: dict[str, Any]
    platforms: list[str]
    urgency: Urgency
    confidence: float
    status: OpportunityStatus
    origin_kind: str
    origin_id: int | None
    content_id: int | None
    notes: str
    provider: str | None
    model: str | None
    generated_at: datetime | None
    reviewed_at: datetime | None
    created_at: datetime
    signals: dict[str, float] = {}
    reasons: list[str] = []
    priority: float = 0.0
    priority_label: str = ""
    ranked_at: datetime | None = None
    content_potential: float | None = None
    user_importance: float | None = None


class OpportunityUpdate(StrictInput):
    status: OpportunityStatus | None = None
    notes: Annotated[str, StringConstraints(max_length=4000)] | None = None
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)] | None = None
    angle: Annotated[str, StringConstraints(max_length=2000)] | None = None
    platforms: list[ContentChannel] | None = None


class DraftRequest(StrictInput):
    channel: ContentChannel = ContentChannel.X


class OpportunityDetailOut(BaseModel):
    opportunity: OpportunityOut
    messages: list[TelegramMessageOut]


class TopicDetailOut(BaseModel):
    topic: TopicOut
    representative: list[TelegramMessageOut]
    recent: list[TelegramMessageOut]
    questions: list[QuestionOut]
    memories: list[MemoryOut]
    summaries: list[SummaryOut]
    opportunities: list[OpportunityOut]
    volume_by_day: list[dict[str, Any]]


class SummaryDetailOut(BaseModel):
    summary: SummaryOut
    messages: list[TelegramMessageOut]


class CommunityOverviewOut(BaseModel):
    telegram: TelegramStatusOut
    messages_total: int
    messages_today: int
    active_members_7d: int
    active_members_today: int
    unprocessed: int
    top_topics: list[TopicOut]
    trending_topics: list[TopicOut]
    recurring_questions: list[QuestionOut]
    latest_summary: SummaryOut | None
    opportunities: list[OpportunityOut]
    pending_memories: int
    recent_activity: list[dict[str, Any]]


class SearchMessageOut(BaseModel):
    message: TelegramMessageOut
    score: float
    reason: str
    source_name: str | None


class SearchOut(BaseModel):
    query: str
    window: dict[str, Any]
    messages: list[SearchMessageOut]
    memories: list[dict[str, Any]]
    summaries: list[SummaryOut]
    topics: list[dict[str, Any]]
    notes: list[str]
    answer: dict[str, Any] | None


class ProvenanceOut(BaseModel):
    kind: str
    id: int
    generated: dict[str, Any] | None
    messages: list[TelegramMessageOut]


# --- jobs / routing -------------------------------------------------------------------------


class JobOut(BaseModel):
    name: str
    label: str
    description: str
    enabled: bool
    interval_seconds: int
    status: str
    running: bool
    last_run_at: datetime | None
    last_finished_at: datetime | None
    last_duration_ms: float | None
    next_run_at: datetime | None
    last_error: str | None
    last_result: dict[str, Any]
    run_count: int
    failure_count: int
    consecutive_failures: int


class JobUpdate(StrictInput):
    enabled: bool


class JobRunOut(BaseModel):
    name: str
    status: str
    result: dict[str, Any]
    error: str | None


class RouteOut(BaseModel):
    task: str
    label: str
    description: str
    default_tier: str
    tier: str | None
    model: str
    temperature: float
    customised: bool


class RouteUpdate(StrictInput):
    tier: Annotated[str, StringConstraints(pattern=r"^(fast|strong|default)$")] | None = None
    model: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)] | None = None
    temperature: float | None = Field(None, ge=0.0, le=2.0)


class RoutingOut(BaseModel):
    tiers: dict[str, str]
    routes: list[RouteOut]
    installed_models: list[str]
