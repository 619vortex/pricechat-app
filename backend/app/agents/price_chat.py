"""PriceChatAgent: the brand brain.

Depends only on interfaces: LLMProvider, ConversationMemory, MemoryRetriever and
ActivityLogger. It never knows which model backend or storage engine is behind them.
"""

import logging
from dataclasses import dataclass, field
from typing import Protocol

from app.agents.prompting import BrandProfileData, CompiledPrompt, GoalData, PromptContext, SystemPromptBuilder
from app.agents.prompting.context import CommunityContext
from app.memory.conversation import ConversationMemory, TurnContext
from app.memory.retrieval.base import MemoryRetriever, RetrievalQuery, RetrievalResult
from app.models.enums import ActivityLevel, AgentStatus, AgentTask, PromptPlatform
from app.providers.base import ChatMessage, ChatOptions, ChatResult, LLMProvider
from app.providers.errors import ProviderError
from app.services.activity import ActivityLogger

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class AgentProfile:
    name: str
    identity: str
    behavior_rules: list[str] = field(default_factory=list)
    status: AgentStatus = AgentStatus.ACTIVE
    model_name: str | None = None
    temperature: float | None = None


@dataclass(frozen=True, slots=True)
class AgentRequest:
    message: str
    task: AgentTask = AgentTask.CHAT
    platform: PromptPlatform = PromptPlatform.DASHBOARD
    actor: str = "dashboard"


@dataclass(frozen=True, slots=True)
class AgentReply:
    content: str
    result: ChatResult
    retrieval: RetrievalResult
    prompt: CompiledPrompt
    conversation_id: str
    message_id: int
    community: CommunityContext | None = None


class CommunitySource(Protocol):
    """Supplies relevant community intelligence for a request (e.g. from Telegram)."""

    async def gather(self, query: str) -> CommunityContext | None: ...


class AgentPausedError(Exception):
    pass


def _preview(text: str, limit: int = 70) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


class PriceChatAgent:
    def __init__(
        self,
        agent_id: int,
        profile: AgentProfile,
        brand: BrandProfileData,
        goals: list[GoalData],
        provider: LLMProvider,
        conversation: ConversationMemory,
        retriever: MemoryRetriever,
        activity: ActivityLogger,
        prompt_builder: SystemPromptBuilder | None = None,
        recall_limit: int = 6,
        memory_char_budget: int = 4000,
        community: CommunitySource | None = None,
    ) -> None:
        self.community = community
        self.agent_id = agent_id
        self.profile = profile
        self.brand = brand
        self.goals = goals
        self.provider = provider
        self.conversation = conversation
        self.retriever = retriever
        self.activity = activity
        self.prompt_builder = prompt_builder or SystemPromptBuilder()
        self.recall_limit = recall_limit
        self.memory_char_budget = memory_char_budget

    @property
    def model(self) -> str:
        return self.profile.model_name or self.provider.model

    async def recall(self, text: str) -> RetrievalResult:
        return await self.retriever.retrieve(RetrievalQuery(text=text, limit=self.recall_limit))

    async def recall_community(self, text: str) -> CommunityContext | None:
        if self.community is None:
            return None
        try:
            return await self.community.gather(text)
        except Exception:  # noqa: BLE001 - community context is optional; never block a reply
            logger.exception("Community context unavailable")
            return None

    def compile_prompt(
        self,
        retrieval: RetrievalResult | None = None,
        task: AgentTask = AgentTask.CHAT,
        platform: PromptPlatform = PromptPlatform.DASHBOARD,
        community: CommunityContext | None = None,
    ) -> CompiledPrompt:
        return self.prompt_builder.build(
            PromptContext(
                identity=self.profile.identity,
                brand=self.brand,
                goals=self.goals,
                behavior_rules=self.profile.behavior_rules,
                memories=retrieval.memories if retrieval else [],
                task=task,
                platform=platform,
                memory_char_budget=self.memory_char_budget,
                community=community,
            )
        )

    async def respond(self, request: AgentRequest) -> AgentReply:
        if self.profile.status == AgentStatus.PAUSED:
            raise AgentPausedError(f"{self.profile.name} is paused. Activate it on the AI Brain page to chat.")

        retrieval = await self.recall(request.message)
        community = await self.recall_community(request.message)
        self._log_retrieval(request, retrieval, community)

        prompt = self.compile_prompt(retrieval, request.task, request.platform, community)
        messages = [
            ChatMessage("system", prompt.text),
            *self.conversation.recent(),
            ChatMessage("user", request.message),
        ]
        options = ChatOptions(model=self.profile.model_name, temperature=self.profile.temperature)

        try:
            result = await self.provider.chat(messages, options)
        except ProviderError as exc:
            self.activity.log(
                "ai_inference_failed",
                f"{self.profile.name} could not answer: {exc}",
                level=ActivityLevel.ERROR,
                actor=request.actor,
                details={"model": self.model, "error_type": exc.code, "task": request.task.value},
            )
            raise

        memory_ids = [m.record.id for m in retrieval.memories]
        _, assistant_id = self.conversation.record_exchange(
            request.message, result, TurnContext(request.task, request.platform), memory_ids
        )

        seconds = (result.latency_ms or 0) / 1000
        tokens = f", {result.completion_tokens} tokens" if result.completion_tokens else ""
        self.activity.log(
            "ai_inference",
            f'Answered "{_preview(request.message)}" in {seconds:.1f}s with {result.model}{tokens}',
            actor=request.actor,
            details={
                "model": result.model,
                "provider": result.provider,
                "latency_ms": result.latency_ms,
                "prompt_tokens": result.prompt_tokens,
                "completion_tokens": result.completion_tokens,
                "tokens_per_second": result.tokens_per_second,
                "task": request.task.value,
                "platform": request.platform.value,
                "conversation_id": self.conversation.conversation_id,
                "memories_used": memory_ids,
            },
        )
        if request.task not in (AgentTask.CHAT, AgentTask.MODEL_TEST):
            self.activity.log(
                "agent_task",
                f"Completed task '{request.task.value.replace('_', ' ')}' for {request.platform.value}",
                actor=request.actor,
                details={"task": request.task.value, "platform": request.platform.value, "message_id": assistant_id},
            )

        return AgentReply(
            content=result.content,
            result=result,
            retrieval=retrieval,
            prompt=prompt,
            conversation_id=self.conversation.conversation_id,
            message_id=assistant_id,
            community=community,
        )

    def _log_retrieval(self, request: AgentRequest, retrieval: RetrievalResult, community: CommunityContext | None = None) -> None:
        count = len(retrieval.memories)
        if count:
            titles = ", ".join(_preview(m.record.title or m.record.content, 40) for m in retrieval.memories[:3])
            message = f"Recalled {count} {'memory' if count == 1 else 'memories'} ({titles})"
        else:
            message = "No stored knowledge matched this request"
        if community is not None:
            parts = [
                f"{len(community.topics)} topics" if community.topics else "",
                f"{len(community.questions)} recurring questions" if community.questions else "",
                "the latest summary" if community.summary else "",
                f"{len(community.messages)} member messages" if community.messages else "",
            ]
            message += "; community context: " + ", ".join(p for p in parts if p)
        self.activity.log(
            "memory_retrieval",
            message,
            actor=request.actor,
            details={
                "strategy": retrieval.strategy,
                "duration_ms": retrieval.duration_ms,
                "memory_ids": [m.record.id for m in retrieval.memories],
                "scores": [m.score for m in retrieval.memories],
                "notes": retrieval.notes,
                "community_message_ids": [m.message_id for m in community.messages] if community else [],
            },
        )
