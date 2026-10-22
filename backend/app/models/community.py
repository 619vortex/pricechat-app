from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Float, ForeignKey, Index, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin, UTCDateTime
from app.models.enums import (
    DerivedKind,
    OpportunityStatus,
    QuestionStatus,
    SummaryKind,
    TopicStatus,
    TopicTrend,
    Urgency,
)
from app.models.types import str_enum


class CommunityTopic(IdMixin, TimestampMixin, Base):
    """A cluster of related community messages, found by meaning similarity."""

    __tablename__ = "community_topics"

    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    keywords: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[TopicStatus] = mapped_column(str_enum(TopicStatus), default=TopicStatus.CANDIDATE, nullable=False, index=True)
    centroid: Mapped[bytes | None] = mapped_column(LargeBinary)
    embedding_model: Mapped[str | None] = mapped_column(String(128))
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    message_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    participant_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    count_24h: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    count_prev_24h: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    trend: Mapped[TopicTrend] = mapped_column(str_enum(TopicTrend), default=TopicTrend.INSUFFICIENT_DATA, nullable=False)
    importance: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    representative_message_ids: Mapped[list[int]] = mapped_column(JSON, default=list, nullable=False)
    needs_naming: Mapped[bool] = mapped_column(default=True, nullable=False)
    named_by_model: Mapped[str | None] = mapped_column(String(128))
    named_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    # Phase 3B: canonical topics, aliases and human feedback
    canonical_id: Mapped[int | None] = mapped_column(ForeignKey("community_topics.id", ondelete="SET NULL"))  # set when merged
    aliases: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    related_ids: Mapped[list[int]] = mapped_column(JSON, default=list, nullable=False)
    review_status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)  # pending | approved | rejected
    user_importance: Mapped[float | None] = mapped_column(Float)  # human override (important / not important)


class CommunityQuestion(IdMixin, TimestampMixin, Base):
    """A question the community asks, grouped across differently-worded messages."""

    __tablename__ = "community_questions"

    text: Mapped[str] = mapped_column(Text, nullable=False)  # canonical wording
    example: Mapped[str] = mapped_column(Text, nullable=False)  # first verbatim message
    status: Mapped[QuestionStatus] = mapped_column(str_enum(QuestionStatus), default=QuestionStatus.OPEN, nullable=False, index=True)
    centroid: Mapped[bytes | None] = mapped_column(LargeBinary)
    embedding_model: Mapped[str | None] = mapped_column(String(128))
    ask_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    asker_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    first_asked_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    last_asked_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    topic_id: Mapped[int | None] = mapped_column(ForeignKey("community_topics.id", ondelete="SET NULL"))
    needs_canonical: Mapped[bool] = mapped_column(default=False, nullable=False)
    canonical_model: Mapped[str | None] = mapped_column(String(128))
    faq_memory_id: Mapped[int | None] = mapped_column(ForeignKey("memories.id", ondelete="SET NULL"))
    review_status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    user_importance: Mapped[float | None] = mapped_column(Float)


class CommunitySummary(IdMixin, TimestampMixin, Base):
    """An AI-generated summary. An interpretation, never a quote."""

    __tablename__ = "community_summaries"

    kind: Mapped[SummaryKind] = mapped_column(str_enum(SummaryKind), nullable=False, index=True)
    source_id: Mapped[int | None] = mapped_column(ForeignKey("telegram_sources.id", ondelete="CASCADE"), index=True)
    topic_id: Mapped[int | None] = mapped_column(ForeignKey("community_topics.id", ondelete="CASCADE"), index=True)
    period_start: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    period_end: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    headline: Mapped[str] = mapped_column(String(300), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)  # readable markdown
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)  # structured sections
    message_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    participant_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    generated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    duration_ms: Mapped[float | None] = mapped_column(Float)
    memories_extracted_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class ContentOpportunity(IdMixin, TimestampMixin, Base):
    """A content idea backed by community evidence. Ideas only; nothing is published."""

    __tablename__ = "content_opportunities"

    title: Mapped[str] = mapped_column(String(300), nullable=False)
    why: Mapped[str] = mapped_column(Text, nullable=False)
    angle: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    platforms: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    urgency: Mapped[Urgency] = mapped_column(str_enum(Urgency), default=Urgency.MEDIUM, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)
    status: Mapped[OpportunityStatus] = mapped_column(
        str_enum(OpportunityStatus), default=OpportunityStatus.DETECTED, nullable=False, index=True
    )
    origin_kind: Mapped[str] = mapped_column(String(32), nullable=False)  # topic | question | manual
    origin_id: Mapped[int | None] = mapped_column(Integer)
    content_id: Mapped[int | None] = mapped_column(ForeignKey("content.id", ondelete="SET NULL"))
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)
    provider: Mapped[str | None] = mapped_column(String(64))
    model: Mapped[str | None] = mapped_column(String(128))
    generated_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    reviewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    # Phase 3B: transparent ranking (each signal 0..1, plus human-readable reasons)
    signals: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    reasons: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    priority: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    priority_label: Mapped[str] = mapped_column(String(16), default="", nullable=False)  # high | medium | low
    ranked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    content_potential: Mapped[float | None] = mapped_column(Float)  # model's own estimate, shown as one signal
    user_importance: Mapped[float | None] = mapped_column(Float)


class ProvenanceLink(IdMixin, Base):
    """Links a derived record (memory, summary, topic, question, opportunity) to the messages behind it."""

    __tablename__ = "provenance_links"
    __table_args__ = (
        UniqueConstraint("derived_kind", "derived_id", "message_id"),
        Index("ix_provenance_links_derived", "derived_kind", "derived_id"),
    )

    derived_kind: Mapped[DerivedKind] = mapped_column(str_enum(DerivedKind), nullable=False)
    derived_id: Mapped[int] = mapped_column(Integer, nullable=False)
    message_id: Mapped[int] = mapped_column(
        ForeignKey("telegram_messages.id", ondelete="CASCADE"), index=True, nullable=False
    )
