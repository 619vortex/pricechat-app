from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.models.enums import (
    ActionMode,
    ActivityLevel,
    AgentStatus,
    AgentTask,
    ConnectionStatus,
    ContentStatus,
    MemoryKind,
    MemoryLayer,
    Platform,
    PromptPlatform,
    ReviewStatus,
    SocialAction,
    SourceType,
)

ConversationId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_\-]{1,64}$")]
ListItem = Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)]
Tag = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class StrictInput(BaseModel):
    """Request bodies reject unknown fields so typos fail loudly."""

    model_config = ConfigDict(extra="forbid")


class ErrorOut(BaseModel):
    detail: str
    code: str


# --- health / model ---------------------------------------------------------


class ProviderStatusOut(BaseModel):
    provider: str
    model: str
    available: bool
    model_loaded: bool
    detail: str | None = None
    models: list[str] = []
    endpoint: str | None = None
    latency_ms: float | None = None


class HealthOut(BaseModel):
    status: str
    app: str
    environment: str
    database: bool
    llm: ProviderStatusOut


class ModelStatusOut(BaseModel):
    provider: str
    model: str
    default_model: str
    model_overridden: bool
    temperature: float
    status: str
    endpoint: str | None
    detail: str | None
    health_latency_ms: float | None
    installed_models: list[str]
    last_success_at: datetime | None
    last_success_model: str | None
    last_inference_latency_ms: float | None
    last_error: str | None
    embedding_provider: str | None
    embedding_model: str | None


class ModelConfigUpdate(StrictInput):
    model_name: Annotated[str, StringConstraints(strip_whitespace=True, max_length=128)] | None = None
    temperature: float | None = Field(None, ge=0.0, le=2.0)


class ModelTestOut(BaseModel):
    ok: bool
    reply: str
    model: str
    provider: str
    latency_ms: float | None
    prompt_tokens: int | None
    completion_tokens: int | None
    tokens_per_second: float | None


# --- agent / brand / goals -----------------------------------------------------


class AgentConfigOut(ORMModel):
    id: int
    slug: str
    name: str
    identity: str
    behavior_rules: list[str]
    status: AgentStatus
    model_name: str | None
    temperature: float | None
    updated_at: datetime


class AgentConfigUpdate(StrictInput):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)] | None = None
    identity: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)] | None = None
    behavior_rules: list[ListItem] | None = Field(None, max_length=50)
    status: AgentStatus | None = None
    model_name: Annotated[str, StringConstraints(strip_whitespace=True, max_length=128)] | None = None
    temperature: float | None = Field(None, ge=0.0, le=2.0)


class BrandProfileOut(ORMModel):
    brand_name: str
    description: str
    mission: str
    audience: str
    tone: str
    writing_style: str
    personality: str
    products_services: list[str]
    topics_of_interest: list[str]
    topics_to_avoid: list[str]
    terminology: list[str]
    allowed_claims: list[str]
    restricted_claims: list[str]
    social_objectives: list[str]
    x_voice: list[str] = []
    x_avoid: list[str] = []
    updated_at: datetime


class BrandProfileUpdate(StrictInput):
    brand_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)] | None = None
    description: Annotated[str, StringConstraints(max_length=4000)] | None = None
    mission: Annotated[str, StringConstraints(max_length=2000)] | None = None
    audience: Annotated[str, StringConstraints(max_length=2000)] | None = None
    tone: Annotated[str, StringConstraints(max_length=1000)] | None = None
    writing_style: Annotated[str, StringConstraints(max_length=2000)] | None = None
    personality: Annotated[str, StringConstraints(max_length=1000)] | None = None
    products_services: list[ListItem] | None = Field(None, max_length=50)
    topics_of_interest: list[ListItem] | None = Field(None, max_length=50)
    topics_to_avoid: list[ListItem] | None = Field(None, max_length=50)
    terminology: list[ListItem] | None = Field(None, max_length=100)
    allowed_claims: list[ListItem] | None = Field(None, max_length=50)
    restricted_claims: list[ListItem] | None = Field(None, max_length=50)
    social_objectives: list[ListItem] | None = Field(None, max_length=50)
    x_voice: list[ListItem] | None = Field(None, max_length=30)
    x_avoid: list[ListItem] | None = Field(None, max_length=30)


class BrandOut(BaseModel):
    profile: BrandProfileOut
    completeness: int
    empty_fields: list[str]


class GoalOut(ORMModel):
    id: int
    name: str
    description: str
    priority: int
    enabled: bool
    updated_at: datetime


class GoalIn(StrictInput):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]
    description: Annotated[str, StringConstraints(max_length=2000)] = ""
    priority: int = Field(3, ge=1, le=5)
    enabled: bool = True


class GoalUpdate(StrictInput):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)] | None = None
    description: Annotated[str, StringConstraints(max_length=2000)] | None = None
    priority: int | None = Field(None, ge=1, le=5)
    enabled: bool | None = None


class MemoryStatsOut(BaseModel):
    total: int
    by_layer: dict[str, int]
    by_kind: dict[str, int]
    by_source_type: dict[str, int]
    embedded: int
    embedding_model: str | None
    conversations: int


class AgentBrainOut(BaseModel):
    config: AgentConfigOut
    effective_model: str
    active_goals: list[GoalOut]
    memory: MemoryStatsOut
    brand_completeness: int
    available_providers: list[str]


# --- chat / prompt -----------------------------------------------------------------


class ChatIn(StrictInput):
    message: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=8000)]
    conversation_id: ConversationId = "default"
    task: AgentTask = AgentTask.CHAT
    platform: PromptPlatform = PromptPlatform.DASHBOARD


class MemoryOut(ORMModel):
    id: int
    layer: MemoryLayer
    kind: MemoryKind
    title: str | None
    content: str
    tags: list[str]
    source_type: SourceType
    source: str | None
    external_id: str | None
    platform: Platform | None
    author: str | None
    importance: float
    occurred_at: datetime | None
    created_at: datetime
    updated_at: datetime
    review_status: ReviewStatus = ReviewStatus.APPROVED
    extra: dict[str, Any] = {}


class RetrievedMemoryOut(BaseModel):
    memory: MemoryOut
    score: float
    semantic: float | None
    lexical: float
    reasons: list[str]


class RetrievalOut(BaseModel):
    strategy: str
    duration_ms: float
    notes: list[str]
    memories: list[RetrievedMemoryOut]


class ChatOut(BaseModel):
    reply: str
    conversation_id: str
    message_id: int
    model: str
    provider: str
    latency_ms: float | None
    prompt_tokens: int | None
    completion_tokens: int | None
    tokens_per_second: float | None
    retrieval: RetrievalOut
    prompt_chars: int


class ConversationMessageOut(ORMModel):
    id: int
    role: str
    content: str
    task: AgentTask
    platform: PromptPlatform
    model: str | None
    latency_ms: float | None
    prompt_tokens: int | None
    completion_tokens: int | None
    memory_ids: list[int]
    created_at: datetime


class PromptSectionOut(BaseModel):
    key: str
    title: str | None
    chars: int


class CompiledPromptOut(BaseModel):
    text: str
    chars: int
    approx_tokens: int
    sections: list[PromptSectionOut]
    retrieval: RetrievalOut | None


# --- knowledge --------------------------------------------------------------------


class KnowledgeIn(StrictInput):
    title: Annotated[str, StringConstraints(strip_whitespace=True, max_length=256)] | None = None
    content: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20000)]
    kind: MemoryKind = MemoryKind.TEXT
    layer: MemoryLayer | None = None
    tags: list[Tag] = Field(default_factory=list, max_length=20)
    source: Annotated[str, StringConstraints(strip_whitespace=True, max_length=256)] | None = None
    importance: float = Field(0.5, ge=0.0, le=1.0)


class KnowledgeUpdate(StrictInput):
    title: Annotated[str, StringConstraints(strip_whitespace=True, max_length=256)] | None = None
    content: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20000)] | None = None
    kind: MemoryKind | None = None
    layer: MemoryLayer | None = None
    tags: list[Tag] | None = Field(None, max_length=20)
    source: Annotated[str, StringConstraints(strip_whitespace=True, max_length=256)] | None = None
    importance: float | None = Field(None, ge=0.0, le=1.0)
    review_status: ReviewStatus | None = None


class KnowledgeSaved(BaseModel):
    memory: MemoryOut
    created: bool
    indexed: bool


class KnowledgeSearchOut(BaseModel):
    results: list[RetrievedMemoryOut]
    strategy: str
    duration_ms: float
    notes: list[str]


class KnowledgeSourceOut(BaseModel):
    key: str
    display_name: str
    description: str
    source_type: SourceType
    layer: MemoryLayer
    available: bool


class ReindexOut(BaseModel):
    indexed: int
    embedding_model: str | None


# --- content ----------------------------------------------------------------


class ContentIn(StrictInput):
    body: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=10000)]
    title: Annotated[str, StringConstraints(max_length=256)] | None = None
    status: ContentStatus = ContentStatus.DRAFT
    target_platform: Platform | None = None
    scheduled_for: datetime | None = None


class ContentUpdate(StrictInput):
    body: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=10000)] | None = None
    title: Annotated[str, StringConstraints(max_length=256)] | None = None
    status: ContentStatus | None = None
    target_platform: Platform | None = None
    scheduled_for: datetime | None = None


class ContentOut(ORMModel):
    id: int
    title: str | None
    body: str
    status: ContentStatus
    target_platform: Platform | None
    scheduled_for: datetime | None
    created_by: str
    created_at: datetime
    updated_at: datetime
    draft_type: str | None = None
    opportunity_id: int | None = None
    variant_group: str | None = None
    variant_label: str | None = None
    context: dict[str, Any] = {}


# --- connectors / activity / overview --------------------------------------


class ConnectorOut(BaseModel):
    platform: Platform
    display_name: str
    status: ConnectionStatus
    detail: str
    planned_capabilities: list[str]
    enabled_capabilities: list[str]


class ActivityOut(ORMModel):
    id: int
    level: ActivityLevel
    event: str
    category: str
    label: str
    message: str
    actor: str
    platform: Platform | None
    action: str | None
    details: dict[str, Any]
    created_at: datetime


class TaskCounts(BaseModel):
    pending: int
    running: int
    failed: int


class OverviewOut(BaseModel):
    agent_name: str
    agent_status: AgentStatus
    llm: ProviderStatusOut
    effective_model: str
    last_success_at: datetime | None
    connectors: list[ConnectorOut]
    social_accounts_connected: int
    tasks: TaskCounts
    memory_count: int
    active_goals: list[GoalOut]
    kill_switch: bool
    recent_activity: list[ActivityOut]
    queued_content: list[ContentOut]


# --- safety -----------------------------------------------------------------


class ActionLimitOut(ORMModel):
    id: int
    platform: Platform
    action: SocialAction
    enabled: bool
    mode: ActionMode | None
    max_per_hour: int
    max_per_day: int


class ActionLimitUpdate(StrictInput):
    enabled: bool | None = None
    mode: ActionMode | None = None
    max_per_hour: int | None = Field(None, ge=0, le=1000)
    max_per_day: int | None = Field(None, ge=0, le=10000)


class SafetyPolicyOut(ORMModel):
    id: int
    mode: ActionMode
    kill_switch: bool
    outbound_enabled: bool
    max_actions_per_hour: int
    max_actions_per_day: int
    updated_at: datetime


class SafetyPolicyUpdate(StrictInput):
    mode: ActionMode | None = None
    kill_switch: bool | None = None
    max_actions_per_hour: int | None = Field(None, ge=0, le=1000)
    max_actions_per_day: int | None = Field(None, ge=0, le=10000)


class SafetySettingsOut(BaseModel):
    policy: SafetyPolicyOut
    limits: list[ActionLimitOut]
    outbound_note: str


class SafetyCheckIn(StrictInput):
    platform: Platform
    action: SocialAction


class SafetyCheckOut(BaseModel):
    allowed: bool
    requires_approval: bool
    reason: str
