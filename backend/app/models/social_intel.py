"""Platform-neutral social intelligence tables (X first; YouTube/TikTok/Instagram later).

Our own connected accounts stay in `social_accounts`; external accounts we see or
watch are `social_profiles`. Posts from any platform are normalised into
`social_posts` (see models/social.py).
"""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Index, Integer, LargeBinary, String, Text, UniqueConstraint, false
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin, UTCDateTime
from app.models.enums import (
    ApprovalStatus,
    CrossSignalStatus,
    FeedbackDecision,
    FeedbackTarget,
    MergeStatus,
    MonitorRuleType,
    Platform,
    SocialAction,
    SocialOpportunityKind,
    SocialOpportunityStatus,
    SuggestedAction,
    WatchCategory,
)
from app.models.types import str_enum


class SocialProfile(IdMixin, TimestampMixin, Base):
    """A public account on a platform. Public profile fields only; nothing inferred."""

    __tablename__ = "social_profiles"
    __table_args__ = (UniqueConstraint("platform", "external_id"),)

    platform: Mapped[Platform] = mapped_column(str_enum(Platform), nullable=False)
    external_id: Mapped[str] = mapped_column(String(64), nullable=False)
    username: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    display_name: Mapped[str | None] = mapped_column(String(256))
    followers_count: Mapped[int | None] = mapped_column(Integer)
    following_count: Mapped[int | None] = mapped_column(Integer)
    post_count: Mapped[int | None] = mapped_column(Integer)
    verified: Mapped[bool | None] = mapped_column(Boolean)
    is_self: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class WatchedAccount(IdMixin, TimestampMixin, Base):
    __tablename__ = "watched_accounts"

    profile_id: Mapped[int] = mapped_column(ForeignKey("social_profiles.id", ondelete="CASCADE"), unique=True, nullable=False)
    reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    category: Mapped[WatchCategory] = mapped_column(str_enum(WatchCategory), default=WatchCategory.OTHER, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=3, nullable=False)  # 1 = highest
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_success_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    since_id: Mapped[str | None] = mapped_column(String(64))  # newest post already read
    last_error: Mapped[str | None] = mapped_column(Text)


class MonitorRule(IdMixin, TimestampMixin, Base):
    """An explicit, user-defined thing to monitor. Nothing outside these rules is read."""

    __tablename__ = "monitor_rules"

    platform: Mapped[Platform] = mapped_column(str_enum(Platform), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    type: Mapped[MonitorRuleType] = mapped_column(str_enum(MonitorRuleType), nullable=False)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    max_results: Mapped[int] = mapped_column(Integer, default=10, nullable=False)  # per check; reads cost money
    last_checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_success_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    since_id: Mapped[str | None] = mapped_column(String(64))
    last_result_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    posts_matched: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text)


class SocialPostVector(IdMixin, TimestampMixin, Base):
    __tablename__ = "social_post_vectors"

    post_id: Mapped[int] = mapped_column(ForeignKey("social_posts.id", ondelete="CASCADE"), unique=True, nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    dimensions: Mapped[int] = mapped_column(Integer, nullable=False)
    vector: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)


class SocialPostMetric(IdMixin, Base):
    """A point-in-time metrics snapshot. Unavailable metrics stay NULL; never estimated."""

    __tablename__ = "social_post_metrics"

    post_id: Mapped[int] = mapped_column(ForeignKey("social_posts.id", ondelete="CASCADE"), index=True, nullable=False)
    captured_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    source: Mapped[str] = mapped_column(String(32), default="public", nullable=False)  # public | non_public | organic
    impressions: Mapped[int | None] = mapped_column(Integer)
    views: Mapped[int | None] = mapped_column(Integer)
    likes: Mapped[int | None] = mapped_column(Integer)
    replies: Mapped[int | None] = mapped_column(Integer)
    reposts: Mapped[int | None] = mapped_column(Integer)
    quotes: Mapped[int | None] = mapped_column(Integer)
    bookmarks: Mapped[int | None] = mapped_column(Integer)
    profile_visits: Mapped[int | None] = mapped_column(Integer)
    url_clicks: Mapped[int | None] = mapped_column(Integer)
    followers_attributed: Mapped[int | None] = mapped_column(Integer)


class SocialRelationship(IdMixin, TimestampMixin, Base):
    """Non-sensitive interaction history with one account, so the agent doesn't treat everyone as a stranger."""

    __tablename__ = "social_relationships"

    profile_id: Mapped[int] = mapped_column(ForeignKey("social_profiles.id", ondelete="CASCADE"), unique=True, nullable=False)
    first_interaction_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_interaction_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    interaction_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    mention_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    reply_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    our_reply_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    our_like_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    our_repost_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    last_liked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_liked_post_id: Mapped[str | None] = mapped_column(String(64))
    topics: Mapped[dict[str, int]] = mapped_column(JSON, default=dict, nullable=False)
    they_follow_us: Mapped[bool | None] = mapped_column(Boolean)  # NULL = unknown (API didn't say)
    we_follow_them: Mapped[bool | None] = mapped_column(Boolean)
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)


class SocialOpportunity(IdMixin, TimestampMixin, Base):
    """A post where Price Chat could add value. Suggestions only; nothing is executed."""

    __tablename__ = "social_opportunities"

    platform: Mapped[Platform] = mapped_column(str_enum(Platform), nullable=False)
    post_id: Mapped[int] = mapped_column(ForeignKey("social_posts.id", ondelete="CASCADE"), index=True, nullable=False)
    kind: Mapped[SocialOpportunityKind] = mapped_column(str_enum(SocialOpportunityKind), nullable=False)
    topic: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    relevance: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    why: Mapped[str] = mapped_column(Text, default="", nullable=False)
    contribution: Mapped[str] = mapped_column(Text, default="", nullable=False)
    suggested_action: Mapped[SuggestedAction] = mapped_column(str_enum(SuggestedAction), nullable=False)
    knowledge: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    engagement: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    signals: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    reasons: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    priority: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    status: Mapped[SocialOpportunityStatus] = mapped_column(
        str_enum(SocialOpportunityStatus), default=SocialOpportunityStatus.NEW, nullable=False, index=True
    )
    provider: Mapped[str | None] = mapped_column(String(64))
    model: Mapped[str | None] = mapped_column(String(128))
    generated_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class CrossSignal(IdMixin, TimestampMixin, Base):
    """The same topic active on Telegram and X. Informational: co-occurrence, not causation."""

    __tablename__ = "cross_signals"

    topic_name: Mapped[str] = mapped_column(String(200), nullable=False)
    telegram_topic_id: Mapped[int | None] = mapped_column(ForeignKey("community_topics.id", ondelete="CASCADE"), unique=True)
    similarity: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    telegram_messages_24h: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    telegram_messages_total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    telegram_participants: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    telegram_trend: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    x_posts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    x_posts_24h: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    x_engagement_24h: Mapped[int | None] = mapped_column(Integer)
    x_engagement_prev_24h: Mapped[int | None] = mapped_column(Integer)
    x_trend: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    x_post_ids: Mapped[list[int]] = mapped_column(JSON, default=list, nullable=False)
    recommendation: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[CrossSignalStatus] = mapped_column(str_enum(CrossSignalStatus), default=CrossSignalStatus.ACTIVE, nullable=False)
    first_detected_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    last_updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    opportunity_id: Mapped[int | None] = mapped_column(ForeignKey("content_opportunities.id", ondelete="SET NULL"))
    # Early community signal: Telegram discussed it before it showed up in monitored X sources.
    early_signal: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)
    lead_minutes: Mapped[int | None] = mapped_column(Integer)


class ApprovalRequest(IdMixin, TimestampMixin, Base):
    """A proposed social action awaiting a human decision. Execution is disabled in this phase."""

    __tablename__ = "approval_requests"

    platform: Mapped[Platform] = mapped_column(str_enum(Platform), nullable=False)
    action: Mapped[SocialAction] = mapped_column(str_enum(SocialAction), nullable=False)
    target_external_id: Mapped[str | None] = mapped_column(String(64))
    target_username: Mapped[str | None] = mapped_column(String(128))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    source_kind: Mapped[str] = mapped_column(String(32), default="manual", nullable=False)
    source_id: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[ApprovalStatus] = mapped_column(str_enum(ApprovalStatus), default=ApprovalStatus.PENDING, nullable=False, index=True)
    guard_result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    executed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    execution_result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)


class SocialActionLog(IdMixin, TimestampMixin, Base):
    """Every attempted social write and the guard's decision (used for duplicates and cooldowns)."""

    __tablename__ = "social_actions"

    platform: Mapped[Platform] = mapped_column(str_enum(Platform), nullable=False)
    action: Mapped[SocialAction] = mapped_column(str_enum(SocialAction), nullable=False)
    target_external_id: Mapped[str | None] = mapped_column(String(64), index=True)
    target_account: Mapped[str | None] = mapped_column(String(128), index=True)  # author/account acted on (lower-case)
    external_id: Mapped[str | None] = mapped_column(String(64))  # id X returned for the action (e.g. the new post)
    text_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    approval_id: Mapped[int | None] = mapped_column(ForeignKey("approval_requests.id", ondelete="SET NULL"))
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)  # blocked | simulated | executed | failed
    reason: Mapped[str] = mapped_column(Text, default="", nullable=False)


class FeedbackEvent(IdMixin, TimestampMixin, Base):
    """A human decision about an AI output, kept for interpretable preference learning."""

    __tablename__ = "feedback_events"

    target_kind: Mapped[FeedbackTarget] = mapped_column(str_enum(FeedbackTarget), nullable=False, index=True)
    target_id: Mapped[int] = mapped_column(Integer, nullable=False)
    decision: Mapped[FeedbackDecision] = mapped_column(str_enum(FeedbackDecision), nullable=False)
    features: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    label: Mapped[str] = mapped_column(String(300), default="", nullable=False)
    vector: Mapped[bytes | None] = mapped_column(LargeBinary)
    embedding_model: Mapped[str | None] = mapped_column(String(128))
    note: Mapped[str] = mapped_column(Text, default="", nullable=False)


class TopicMergeSuggestion(IdMixin, TimestampMixin, Base):
    __tablename__ = "topic_merge_suggestions"
    __table_args__ = (UniqueConstraint("topic_a_id", "topic_b_id"),)

    topic_a_id: Mapped[int] = mapped_column(ForeignKey("community_topics.id", ondelete="CASCADE"), nullable=False)
    topic_b_id: Mapped[int] = mapped_column(ForeignKey("community_topics.id", ondelete="CASCADE"), nullable=False)
    similarity: Mapped[float] = mapped_column(Float, nullable=False)
    shared_participants: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    shared_threads: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    verdict: Mapped[str] = mapped_column(String(16), default="", nullable=False)  # same | related | different
    reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    judged_by: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[MergeStatus] = mapped_column(str_enum(MergeStatus), default=MergeStatus.PENDING, nullable=False, index=True)
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class ConnectorState(IdMixin, TimestampMixin, Base):
    """Connection status, capabilities, rate limits and spend for one platform connector."""

    __tablename__ = "connector_state"

    platform: Mapped[Platform] = mapped_column(str_enum(Platform), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="not_configured", nullable=False)
    account_external_id: Mapped[str | None] = mapped_column(String(64))
    account_username: Mapped[str | None] = mapped_column(String(128))
    account_name: Mapped[str | None] = mapped_column(String(256))
    auth_modes: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    capabilities: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    rate_limits: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    usage: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)  # per UTC day: requests, resources, est_cost
    paused: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    blocked_until: Mapped[datetime | None] = mapped_column(UTCDateTime())  # rate limit / usage cap
    last_success_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_error: Mapped[str | None] = mapped_column(Text)
    last_error_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class OperatorPreference(IdMixin, TimestampMixin, Base):
    """The human's control over a learned preference: accept it, disable it, or reset its evidence."""

    __tablename__ = "operator_preferences"

    key: Mapped[str] = mapped_column(String(96), unique=True, nullable=False)
    state: Mapped[str] = mapped_column(String(16), default="active", server_default="active", nullable=False)  # active | accepted | disabled
    reset_at: Mapped[datetime | None] = mapped_column(UTCDateTime())  # evidence before this is ignored


class UsageCounter(IdMixin, TimestampMixin, Base):
    """Daily counters for compute and API use (fast/strong model calls, Telegram requests...)."""

    __tablename__ = "usage_counters"
    __table_args__ = (UniqueConstraint("day", "kind", "key"),)

    day: Mapped[str] = mapped_column(String(10), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)  # model | telegram
    key: Mapped[str] = mapped_column(String(64), nullable=False)  # tier:task, bot, account
    count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    seconds: Mapped[float] = mapped_column(Float, default=0.0, server_default="0", nullable=False)


class DiscoveryIgnore(IdMixin, TimestampMixin, Base):
    """X accounts the human chose to ignore in creator discovery."""

    __tablename__ = "discovery_ignores"

    profile_id: Mapped[int] = mapped_column(ForeignKey("social_profiles.id", ondelete="CASCADE"), unique=True, nullable=False)


class GrowthSnapshot(IdMixin, Base):
    """A point-in-time audience count, recorded from now on (history is never back-filled or estimated).

    kind="x_account": our X account (followers/following/posts), mostly taken from X responses Price Chat
    already receives. kind="telegram_chat": an authorised Telegram chat (members, messages collected so far).
    """

    __tablename__ = "growth_snapshots"
    __table_args__ = (Index("ix_growth_snapshots_kind_subject_taken", "kind", "subject", "taken_at"),)

    kind: Mapped[str] = mapped_column(String(32), nullable=False)  # x_account | telegram_chat
    subject: Mapped[str] = mapped_column(String(64), nullable=False)  # X user id | Telegram chat id
    label: Mapped[str] = mapped_column(String(256), default="", nullable=False)
    taken_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    followers: Mapped[int | None] = mapped_column(Integer)
    following: Mapped[int | None] = mapped_column(Integer)
    posts: Mapped[int | None] = mapped_column(Integer)
    members: Mapped[int | None] = mapped_column(Integer)
    messages: Mapped[int | None] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(32), default="", nullable=False)  # x_response | users_me | bot_api | account_api | local

