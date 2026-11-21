from enum import StrEnum


class AgentStatus(StrEnum):
    IDLE = "idle"
    ACTIVE = "active"
    PAUSED = "paused"
    ERROR = "error"


class MemoryLayer(StrEnum):
    """Which part of the brain a memory belongs to."""

    CONVERSATION = "conversation"  # A: notable things from chats
    BRAND = "brand"  # B: long-term brand knowledge
    COMMUNITY = "community"  # C: learned community knowledge (future: Telegram)
    SOCIAL = "social"  # D: social knowledge (future: X / Twitter)


class MemoryKind(StrEnum):
    """What form the memory takes."""

    TEXT = "text"
    NOTE = "note"
    BRAND_FACT = "brand_fact"
    COMMUNITY_INSIGHT = "community_insight"
    MESSAGE = "message"  # e.g. an ingested community chat message
    POST = "post"  # e.g. an ingested social post
    DOCUMENT = "document"
    SUMMARY = "summary"


class SourceType(StrEnum):
    """Where a memory came from."""

    MANUAL = "manual"
    CHAT = "chat"
    AGENT = "agent"
    TELEGRAM = "telegram"
    X = "x"
    DOCUMENT = "document"
    URL = "url"
    SYSTEM = "system"


class AgentTask(StrEnum):
    """What the agent is being asked to do. Shapes the system prompt."""

    CHAT = "chat"
    DRAFT_POST = "draft_post"
    DRAFT_REPLY = "draft_reply"
    SUMMARIZE = "summarize"
    MODEL_TEST = "model_test"


class PromptPlatform(StrEnum):
    """Where the output is meant to appear."""

    DASHBOARD = "dashboard"
    X = "x"
    TELEGRAM = "telegram"
    YOUTUBE = "youtube"
    YOUTUBE_SHORTS = "youtube_shorts"
    TIKTOK = "tiktok"


class ContentStatus(StrEnum):
    DRAFT = "draft"
    QUEUED = "queued"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    PUBLISHED = "published"
    REJECTED = "rejected"


class Platform(StrEnum):
    X = "x"
    TELEGRAM = "telegram"
    YOUTUBE = "youtube"


class ConnectionStatus(StrEnum):
    NOT_CONNECTED = "not_connected"
    CONNECTED = "connected"
    ERROR = "error"
    DISABLED = "disabled"


class InteractionType(StrEnum):
    MESSAGE = "message"
    MENTION = "mention"
    REPLY = "reply"
    LIKE = "like"
    REPOST = "repost"


class TaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ActivityLevel(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    AUDIT = "audit"


class ActionMode(StrEnum):
    APPROVAL = "approval"
    AUTOMATIC = "automatic"


class SocialAction(StrEnum):
    POST = "post"
    REPLY = "reply"
    LIKE = "like"
    REPOST = "repost"
    FOLLOW = "follow"
    DIRECT_MESSAGE = "direct_message"
    DELETE = "delete"  # only the owner's confirmed correction of the News Desk's own post (app/newsdesk/corrections)


class ReviewStatus(StrEnum):
    """Human review state of a memory. The agent only uses approved memories."""

    APPROVED = "approved"
    PENDING = "pending"
    REJECTED = "rejected"


class MessageCategory(StrEnum):
    QUESTION = "question"
    FEEDBACK = "feedback"
    CONCERN = "concern"
    IDEA = "idea"
    INFORMATION = "information"
    ANNOUNCEMENT = "announcement"
    CHATTER = "chatter"
    NOISE = "noise"


class TopicStatus(StrEnum):
    CANDIDATE = "candidate"  # too few messages to call it a topic yet
    ACTIVE = "active"
    ARCHIVED = "archived"
    MERGED = "merged"  # folded into another (canonical) topic; kept as an alias


class TopicTrend(StrEnum):
    NEW = "new"
    RISING = "rising"
    STEADY = "steady"
    FALLING = "falling"
    INSUFFICIENT_DATA = "insufficient_data"


class QuestionStatus(StrEnum):
    OPEN = "open"
    ANSWERED = "answered"  # e.g. turned into an FAQ entry
    DISMISSED = "dismissed"


class SummaryKind(StrEnum):
    RECENT = "recent"
    DAILY = "daily"
    TOPIC = "topic"


class OpportunityStatus(StrEnum):
    DETECTED = "detected"
    REVIEWING = "reviewing"
    APPROVED = "approved"
    DRAFTED = "drafted"
    REJECTED = "rejected"
    PUBLISHED = "published"  # future: set only by a connector after a safety-checked publish


class Urgency(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ContentChannel(StrEnum):
    """Where an opportunity could be published (ideas only; nothing is published)."""

    X = "x"
    YOUTUBE = "youtube"
    YOUTUBE_SHORTS = "youtube_shorts"
    TIKTOK = "tiktok"
    TELEGRAM = "telegram"


class DerivedKind(StrEnum):
    """Kinds of AI-derived records that can cite Telegram messages."""

    MEMORY = "memory"
    SUMMARY = "summary"
    TOPIC = "topic"
    QUESTION = "question"
    OPPORTUNITY = "opportunity"


# --- Phase 3B / 4A: social intelligence ------------------------------------------------


class FeedbackDecision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    EDIT = "edit"
    IMPORTANT = "important"
    NOT_IMPORTANT = "not_important"
    SKIP = "skip"  # reviewed later / passed over in a review session


class FeedbackTarget(StrEnum):
    MEMORY = "memory"
    TOPIC = "topic"
    QUESTION = "question"
    OPPORTUNITY = "opportunity"
    SOCIAL_OPPORTUNITY = "social_opportunity"
    DRAFT = "draft"
    SOCIAL_ACTION = "social_action"  # a proposed X action in the approval queue


class MergeStatus(StrEnum):
    PENDING = "pending"
    MERGED = "merged"
    KEPT_SEPARATE = "kept_separate"


class DraftType(StrEnum):
    X_POST = "x_post"
    X_THREAD = "x_thread"
    TELEGRAM_POST = "telegram_post"
    YOUTUBE_IDEA = "youtube_idea"
    YOUTUBE_TITLES = "youtube_titles"
    YOUTUBE_OUTLINE = "youtube_outline"
    SHORT_VIDEO_IDEA = "short_video_idea"
    SHORT_HOOK = "short_hook"
    TIKTOK_IDEA = "tiktok_idea"


class WatchCategory(StrEnum):
    MAJOR_CREATOR = "major_creator"
    INDUSTRY = "industry"
    NEWS = "news"
    PROJECT = "project"
    COMMUNITY_MEMBER = "community_member"
    PARTNER = "partner"
    EMERGING_CREATOR = "emerging_creator"
    OTHER = "other"


class MonitorRuleType(StrEnum):
    KEYWORD = "keyword"
    SEARCH = "search"  # raw platform search query
    TOPIC = "topic"
    ACCOUNT = "account"


class SocialOpportunityKind(StrEnum):
    MENTION = "mention"
    QUESTION = "question"
    CONVERSATION = "conversation"
    GROWING_TOPIC = "growing_topic"
    KNOWLEDGE_MATCH = "knowledge_match"
    COMMUNITY_CROSSOVER = "community_crossover"
    CREATOR_DISCUSSION = "creator_discussion"


class SuggestedAction(StrEnum):
    REPLY = "reply"
    STANDALONE_POST = "standalone_post"
    QUOTE_CANDIDATE = "quote_candidate"
    RESEARCH = "research"
    IGNORE = "ignore"


class SocialOpportunityStatus(StrEnum):
    NEW = "new"
    REVIEWING = "reviewing"
    ACCEPTED = "accepted"
    DISMISSED = "dismissed"
    EXPIRED = "expired"


class CrossSignalStatus(StrEnum):
    ACTIVE = "active"
    DISMISSED = "dismissed"
    STALE = "stale"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    EXECUTED = "executed"
    FAILED = "failed"


class PermissionMode(StrEnum):
    """Per-capability write permission. AUTOMATIC exists for the future but cannot be selected yet."""

    DISABLED = "disabled"
    APPROVAL = "approval"
    AUTOMATIC = "automatic"
