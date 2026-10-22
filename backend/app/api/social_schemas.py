"""Schemas for Phase 3B/4A: secrets, Telegram setup, feedback, drafts, X, social actions."""

from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.api.community_schemas import TelegramMessageOut
from app.api.schemas import StrictInput
from app.models.enums import (
    ApprovalStatus,
    DraftType,
    FeedbackDecision,
    FeedbackTarget,
    MonitorRuleType,
    PermissionMode,
    Platform,
    SocialAction,
    SocialOpportunityStatus,
    WatchCategory,
)


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- secrets ---------------------------------------------------------------------------------


class SecretStatusOut(BaseModel):
    """Never contains a secret value: only whether one is set and where it comes from."""

    name: str
    configured: bool
    source: str | None
    saved_at: datetime | None


class TelegramTokenIn(StrictInput):
    token: Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^\d{5,15}:[A-Za-z0-9_-]{30,64}$")]
    verify: bool = True


class SetupStep(BaseModel):
    key: str
    title: str
    detail: str
    done: bool | None  # None = cannot be checked automatically


class TelegramSetupOut(BaseModel):
    token: SecretStatusOut
    bot_username: str | None
    privacy_disabled: bool | None
    ingestion_enabled: bool
    steps: list[SetupStep]


class ToggleIn(StrictInput):
    enabled: bool


class PilotStatsOut(BaseModel):
    ingestion_enabled: bool
    telegram_status: str
    telegram_error: str | None = None
    authorised_chats: int = 0
    last_message_at: datetime | None = None  # newest stored message
    last_processed_at: datetime | None = None  # last finished processing run
    messages_today: int
    messages_total: int
    messages_processed: int
    messages_unprocessed: int
    messages_ignored: int
    community_memories: int
    pending_memories: int
    topics: int
    questions: int
    opportunities: int
    jobs: list[dict[str, Any]]


class MessageTraceOut(BaseModel):
    message: TelegramMessageOut
    source_name: str | None
    topic: dict[str, Any] | None
    question: dict[str, Any] | None
    summaries: list[dict[str, Any]]
    memories: list[dict[str, Any]]
    opportunities: list[dict[str, Any]]


# --- feedback --------------------------------------------------------------------------------


class FeedbackIn(StrictInput):
    decision: FeedbackDecision
    note: Annotated[str, StringConstraints(max_length=1000)] = ""
    edits: dict[str, Any] | None = None  # for decision=edit


class FeedbackOut(ORM):
    id: int
    target_kind: FeedbackTarget
    target_id: int
    decision: FeedbackDecision
    features: list[str]
    label: str
    note: str
    created_at: datetime


class PreferenceOut(BaseModel):
    feature: str
    approvals: int
    rejections: int
    weight: float


# --- topics: merge / split ---------------------------------------------------------------------


class MergeSuggestionOut(BaseModel):
    id: int
    topic_a: dict[str, Any]
    topic_b: dict[str, Any]
    similarity: float
    shared_participants: int
    shared_threads: int
    verdict: str
    reason: str
    judged_by: str | None
    status: str
    created_at: datetime


class SplitIn(StrictInput):
    message_ids: list[int] = Field(..., min_length=1, max_length=500)
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=160)]


# --- drafts --------------------------------------------------------------------------------------


class DraftRequestIn(StrictInput):
    draft_type: DraftType = DraftType.X_POST
    variants: int = Field(1, ge=1, le=3)


class DraftOut(ORM):
    id: int
    title: str | None
    body: str
    status: str
    draft_type: str | None
    opportunity_id: int | None
    variant_group: str | None
    variant_label: str | None
    context: dict[str, Any]
    created_by: str
    created_at: datetime


# --- X ----------------------------------------------------------------------------------------


class XCredentialsIn(StrictInput):
    bearer_token: Annotated[str, StringConstraints(strip_whitespace=True, min_length=20, max_length=512)] | None = None
    api_key: Annotated[str, StringConstraints(strip_whitespace=True, min_length=10, max_length=256)] | None = None
    api_secret: Annotated[str, StringConstraints(strip_whitespace=True, min_length=10, max_length=256)] | None = None
    access_token: Annotated[str, StringConstraints(strip_whitespace=True, min_length=10, max_length=256)] | None = None
    access_token_secret: Annotated[str, StringConstraints(strip_whitespace=True, min_length=10, max_length=256)] | None = None
    username: Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^@?[A-Za-z0-9_]{1,15}$")] | None = None


class XStatusOut(BaseModel):
    configured: bool
    status: str
    credentials: list[SecretStatusOut]
    auth_modes: list[str]
    account_username: str | None
    account_id: str | None
    account_name: str | None
    capabilities: dict[str, Any]
    last_success_at: datetime | None
    last_error: str | None
    last_error_at: datetime | None
    paused: bool
    blocked_until: datetime | None
    usage_today: dict[str, Any]
    usage_month: dict[str, Any]
    budget: dict[str, float]


class MonitorRuleIn(StrictInput):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]
    type: MonitorRuleType
    query: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=512)]
    enabled: bool = True
    priority: int = Field(3, ge=1, le=5)
    max_results: int = Field(10, ge=10, le=100)


class MonitorRuleUpdate(StrictInput):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)] | None = None
    query: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=512)] | None = None
    enabled: bool | None = None
    priority: int | None = Field(None, ge=1, le=5)
    max_results: int | None = Field(None, ge=10, le=100)


class MonitorRuleOut(ORM):
    id: int
    platform: Platform
    name: str
    type: MonitorRuleType
    query: str
    enabled: bool
    priority: int
    max_results: int
    last_checked_at: datetime | None
    last_success_at: datetime | None = None
    last_result_count: int
    posts_matched: int
    last_error: str | None
    posts_24h: int = 0  # posts this rule found in the last 24 hours (from stored data, no API call)


class WatchIn(StrictInput):
    username: Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^@?[A-Za-z0-9_]{1,15}$")]
    reason: Annotated[str, StringConstraints(max_length=1000)] = ""
    category: WatchCategory = WatchCategory.OTHER
    priority: int = Field(3, ge=1, le=5)
    notes: Annotated[str, StringConstraints(max_length=2000)] = ""


class WatchUpdate(StrictInput):
    reason: Annotated[str, StringConstraints(max_length=1000)] | None = None
    category: WatchCategory | None = None
    priority: int | None = Field(None, ge=1, le=5)
    notes: Annotated[str, StringConstraints(max_length=2000)] | None = None
    enabled: bool | None = None


class ProfileOut(ORM):
    id: int
    platform: Platform
    external_id: str
    username: str
    display_name: str | None
    followers_count: int | None
    following_count: int | None
    post_count: int | None
    verified: bool | None
    last_seen_at: datetime | None


class RelationshipOut(ORM):
    first_interaction_at: datetime | None
    last_interaction_at: datetime | None
    interaction_count: int
    mention_count: int
    reply_count: int
    our_reply_count: int
    our_like_count: int = 0
    last_liked_at: datetime | None = None
    last_liked_post_id: str | None = None
    topics: dict[str, int]
    they_follow_us: bool | None
    we_follow_them: bool | None
    notes: str


class WatchOut(BaseModel):
    id: int
    profile: ProfileOut
    reason: str
    category: WatchCategory
    priority: int
    notes: str
    enabled: bool
    last_checked_at: datetime | None
    last_success_at: datetime | None = None
    last_error: str | None
    relationship: RelationshipOut | None


class SocialPostOut(BaseModel):
    id: int
    platform: Platform
    external_id: str | None
    url: str | None
    text: str
    author_username: str | None
    author_name: str | None
    author_followers: int | None
    posted_at: datetime | None
    is_own: bool
    source_kinds: list[str]
    rule_ids: list[int]
    metrics: dict[str, Any]
    lang: str | None
    in_reply_to_external_id: str | None
    ingested_at: datetime | None = None
    topics: list[str] = []  # names of the monitoring rules that found this post


class SocialOpportunityOut(BaseModel):
    id: int
    platform: Platform
    kind: str
    topic: str
    relevance: float
    why: str
    contribution: str
    suggested_action: str
    knowledge: list[dict[str, Any]]
    engagement: dict[str, Any]
    signals: dict[str, Any]
    reasons: list[str]
    priority: float
    status: SocialOpportunityStatus
    model: str | None
    generated_at: datetime | None
    post: SocialPostOut


class SocialOpportunityUpdate(StrictInput):
    status: SocialOpportunityStatus


class CrossSignalOut(ORM):
    id: int
    topic_name: str
    telegram_topic_id: int | None
    similarity: float
    telegram_messages_24h: int
    telegram_messages_total: int
    telegram_participants: int
    telegram_trend: str
    x_posts: int
    x_posts_24h: int
    x_engagement_24h: int | None
    x_engagement_prev_24h: int | None
    x_trend: str
    x_post_ids: list[int]
    recommendation: str
    status: str
    first_detected_at: datetime
    last_updated_at: datetime
    opportunity_id: int | None
    x_authors: int = 0  # distinct X authors behind the matched posts
    x_watched_authors: int = 0  # of which are on your monitored-accounts list
    x_author_names: list[str] = []
    early_signal: bool = False  # Telegram discussed it before monitored X sources did
    lead_minutes: int | None = None


class MetricSnapshotOut(ORM):
    captured_at: datetime
    source: str
    impressions: int | None
    views: int | None
    likes: int | None
    replies: int | None
    reposts: int | None
    quotes: int | None
    bookmarks: int | None
    profile_visits: int | None
    url_clicks: int | None
    followers_attributed: int | None


# --- permissions / approvals ---------------------------------------------------------------------


class PermissionOut(BaseModel):
    platform: Platform
    action: SocialAction
    mode: PermissionMode
    max_per_hour: int
    max_per_day: int
    available_modes: list[PermissionMode]


class PermissionUpdate(StrictInput):
    mode: PermissionMode | None = None
    max_per_hour: int | None = Field(None, ge=0, le=1000)
    max_per_day: int | None = Field(None, ge=0, le=10000)


class GuardSettingsOut(BaseModel):
    kill_switch: bool
    writes_available: bool
    social_actions_active: bool = False  # not paused and outbound enabled
    dry_run: bool = True
    automatic_locked_reason: str = ""
    platforms_enabled: dict[str, bool]
    blocked_targets: list[str]
    watchlist_only: bool
    cooldown_minutes: int
    duplicate_window_hours: int
    permissions: list[PermissionOut]
    note: str


class GuardSettingsUpdate(StrictInput):
    dry_run: bool | None = None
    platforms_enabled: dict[str, bool] | None = None
    blocked_targets: list[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]] | None = None
    watchlist_only: bool | None = None
    cooldown_minutes: int | None = Field(None, ge=0, le=10080)
    duplicate_window_hours: int | None = Field(None, ge=0, le=720)


class GuardCheckIn(StrictInput):
    platform: Platform
    action: SocialAction
    target: Annotated[str, StringConstraints(max_length=64)] | None = None
    text: Annotated[str, StringConstraints(max_length=10000)] | None = None


class GuardDecisionOut(BaseModel):
    allowed: bool
    requires_approval: bool
    executable: bool
    reason: str
    checks: list[dict[str, Any]]


class ApprovalIn(StrictInput):
    platform: Platform
    action: SocialAction
    target_external_id: Annotated[str, StringConstraints(max_length=64)] | None = None
    target_username: Annotated[str, StringConstraints(max_length=128)] | None = None
    text: Annotated[str, StringConstraints(max_length=10000)] | None = None
    source_kind: Annotated[str, StringConstraints(max_length=32)] = "manual"
    source_id: int | None = None


class ApprovalDecisionIn(StrictInput):
    decision: ApprovalStatus  # approved | rejected
    note: Annotated[str, StringConstraints(max_length=1000)] = ""
    text: Annotated[str, StringConstraints(max_length=10000)] | None = None  # "Edit & approve": the final text


class SocialActionsIn(StrictInput):
    active: bool


class ApprovalOut(ORM):
    id: int
    platform: Platform
    action: SocialAction
    target_external_id: str | None
    target_username: str | None
    payload: dict[str, Any]
    source_kind: str
    source_id: int | None
    status: ApprovalStatus
    guard_result: dict[str, Any]
    decided_at: datetime | None
    expires_at: datetime | None
    executed_at: datetime | None
    execution_result: dict[str, Any]
    notes: str
    created_at: datetime
    performance: dict[str, Any] | None = None  # metrics X reported for an executed post/reply (null = not available)
