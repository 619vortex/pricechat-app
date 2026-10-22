"""Plain data the prompt builder works from. No database or provider types here."""

from dataclasses import dataclass, field
from datetime import datetime

from app.memory.retrieval.base import RetrievedMemory
from app.models.enums import AgentTask, PromptPlatform


@dataclass(slots=True)
class BrandProfileData:
    brand_name: str
    description: str = ""
    mission: str = ""
    audience: str = ""
    tone: str = ""
    writing_style: str = ""
    personality: str = ""
    products_services: list[str] = field(default_factory=list)
    topics_of_interest: list[str] = field(default_factory=list)
    topics_to_avoid: list[str] = field(default_factory=list)
    terminology: list[str] = field(default_factory=list)
    allowed_claims: list[str] = field(default_factory=list)
    restricted_claims: list[str] = field(default_factory=list)
    social_objectives: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class GoalData:
    name: str
    description: str = ""
    priority: int = 3


@dataclass(frozen=True, slots=True)
class CommunityTopicItem:
    name: str
    description: str
    trend: str
    messages_24h: int
    messages_total: int
    participants: int


@dataclass(frozen=True, slots=True)
class CommunityQuestionItem:
    text: str
    asked: int
    askers: int
    last_asked_at: datetime


@dataclass(frozen=True, slots=True)
class CommunitySummaryItem:
    headline: str
    overview: str
    period_start: datetime
    period_end: datetime
    model: str


@dataclass(frozen=True, slots=True)
class CommunityMessageItem:
    message_id: int
    text: str
    sent_at: datetime


@dataclass(slots=True)
class CommunityContext:
    """Community intelligence relevant to one request. Each kind is labelled differently in the prompt."""

    topics: list[CommunityTopicItem] = field(default_factory=list)
    questions: list[CommunityQuestionItem] = field(default_factory=list)
    summary: CommunitySummaryItem | None = None
    messages: list[CommunityMessageItem] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.topics or self.questions or self.summary or self.messages)


@dataclass(slots=True)
class PromptContext:
    identity: str
    brand: BrandProfileData
    goals: list[GoalData] = field(default_factory=list)  # active goals only
    behavior_rules: list[str] = field(default_factory=list)
    memories: list[RetrievedMemory] = field(default_factory=list)
    task: AgentTask = AgentTask.CHAT
    platform: PromptPlatform = PromptPlatform.DASHBOARD
    memory_char_budget: int = 4000
    community: CommunityContext | None = None
    community_char_budget: int = 2500
