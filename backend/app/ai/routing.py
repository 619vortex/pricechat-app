"""Task → model routing.

Background work (classification, summaries, extraction, drafting) names a *task*;
the router decides which model handles it. Tasks default to a tier ("fast" or
"strong"), tiers map to models via .env, and any task can be pointed at a tier or
a specific model from the dashboard (stored in `model_routes`). Callers never
name models or providers, so moving to Vortex / DGX Spark is a config change.
"""

import logging
import time
from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.gate import AIGate
from app.config.settings import Settings
from app.models.routing import ModelRoute
from app.providers.base import ChatMessage, ChatOptions, ChatResult, LLMProvider
from app.models.enums import ActivityLevel
from app.providers.errors import ModelNotFoundError, ProviderError, ProviderTimeoutError

logger = logging.getLogger(__name__)


class ModelTier(StrEnum):
    FAST = "fast"
    STRONG = "strong"
    DEFAULT = "default"  # the agent's chat model


@dataclass(frozen=True, slots=True)
class TaskSpec:
    key: str
    label: str
    description: str
    tier: ModelTier
    temperature: float = 0.3
    max_tokens: int | None = None


TASKS: dict[str, TaskSpec] = {
    t.key: t
    for t in (
        TaskSpec("telegram.classify", "Message classification", "Label batches of Telegram messages (question, feedback, noise…)", ModelTier.FAST, 0.0, 2000),
        TaskSpec("telegram.topic_naming", "Topic naming", "Name and describe a detected discussion topic", ModelTier.FAST, 0.2, 300),
        TaskSpec("telegram.question_canonical", "Question wording", "Write one clear version of a repeatedly asked question", ModelTier.FAST, 0.1, 120),
        TaskSpec("community.summary.recent", "Recent activity summary", "Summarise the last few hours of community discussion", ModelTier.FAST, 0.3, 1200),
        TaskSpec("community.summary.daily", "Daily summary", "Synthesise a full day of community discussion", ModelTier.STRONG, 0.3, 1800),
        TaskSpec("community.summary.topic", "Topic summary", "Summarise everything said about one topic", ModelTier.STRONG, 0.3, 1500),
        TaskSpec("community.memory_extraction", "Memory extraction", "Decide what is worth remembering long-term", ModelTier.STRONG, 0.2, 1500),
        TaskSpec("content.opportunity", "Content opportunities", "Turn community signals into content ideas", ModelTier.STRONG, 0.5, 1200),
        TaskSpec("content.draft", "Content drafting", "Draft a post from an approved opportunity", ModelTier.STRONG, 0.7, 1500),
        TaskSpec("content.variants", "Draft variants", "Write 3 style variants of a draft", ModelTier.STRONG, 0.8, 2500),
        TaskSpec("topic.merge_judge", "Topic merge check", "Judge whether two topics are the same, related or different", ModelTier.FAST, 0.0, 200),
        TaskSpec("x.relevance", "X relevance filter", "Quickly filter which monitored X posts are worth a closer look", ModelTier.FAST, 0.0, 1500),
        TaskSpec("x.reply", "X reply and post writing", "Write a proposed X reply or post for the approval queue", ModelTier.STRONG, 0.6, 600),
        TaskSpec("x.opportunity", "X opportunity reasoning", "Explain how Price Chat could usefully contribute to an X post", ModelTier.STRONG, 0.3, 900),
        TaskSpec("cross.synthesis", "Cross-platform synthesis", "Summarise a topic active on both Telegram and X", ModelTier.STRONG, 0.3, 500),
        TaskSpec("community.search_answer", "Search answers", "Answer a question using only retrieved community evidence", ModelTier.FAST, 0.2, 700),
        TaskSpec("kiwi.ask", "Ask Kiwi", "Answer a member's question about their trading bot from logged evidence only", ModelTier.STRONG, 0.2, 1200),
        TaskSpec("kiwi.risk_memo", "Risk team memo", "Explain the risk desks' decision on a kitchen setup from their numbers only", ModelTier.FAST, 0.2, 300),
        TaskSpec("kiwi.teach", "Teach Your Kiwi", "Turn a member's plain-language trading rule into the supported config fields", ModelTier.FAST, 0.0, 700),
    )
}


@dataclass(frozen=True, slots=True)
class RouteDecision:
    task: str
    model: str
    tier: ModelTier | None
    temperature: float
    max_tokens: int | None
    customised: bool


class UnknownTaskError(KeyError):
    pass


class ModelRouter:
    def __init__(
        self, db: Session, settings: Settings, provider: LLMProvider, default_model: str | None = None, gate: "AIGate | None" = None
    ) -> None:
        self.gate = gate  # background tasks wait for interactive chat (see app.ai.gate)
        self.db = db
        self.settings = settings
        self.provider = provider
        self.default_model = default_model or provider.model

    def tier_model(self, tier: ModelTier) -> str:
        if tier == ModelTier.FAST:
            return self.settings.model_tier_fast or self.default_model
        if tier == ModelTier.STRONG:
            return self.settings.model_tier_strong or self.default_model
        return self.default_model

    def resolve(self, task: str) -> RouteDecision:
        spec = TASKS.get(task)
        if spec is None:
            raise UnknownTaskError(task)
        override = self.db.get(ModelRoute, task)
        tier = spec.tier
        model: str | None = None
        temperature = spec.temperature
        if override is not None:
            if override.model:
                model, tier = override.model, None  # type: ignore[assignment]
            elif override.tier:
                tier = ModelTier(override.tier)
            if override.temperature is not None:
                temperature = override.temperature
        return RouteDecision(
            task=task,
            model=model or self.tier_model(tier),
            tier=tier,
            temperature=temperature,
            max_tokens=spec.max_tokens,
            customised=override is not None,
        )

    async def complete(
        self,
        task: str,
        messages: list[ChatMessage],
        response_schema: dict | None = None,
        timeout_seconds: float | None = None,
    ) -> tuple[ChatResult, RouteDecision]:
        """Run a task on its routed model. A missing strong model falls back to the fast tier once."""
        route = self.resolve(task)
        try:
            return await self._call(route, messages, response_schema, timeout_seconds), route
        except ModelNotFoundError:
            fast = self.tier_model(ModelTier.FAST)
            if route.model == fast:
                raise
            logger.warning("Model %s for task %s is not installed; falling back to %s", route.model, task, fast)
            fallback = RouteDecision(task, fast, ModelTier.FAST, route.temperature, route.max_tokens, route.customised)
            return await self._call(fallback, messages, response_schema, timeout_seconds), fallback

    async def _call(
        self, route: RouteDecision, messages: list[ChatMessage], schema: dict | None, timeout: float | None
    ) -> ChatResult:
        if self.gate is not None:
            async with self.gate.background(route.task):
                if route.tier == ModelTier.STRONG:
                    return await self._logged(route, messages, schema, timeout)
                return await self._chat(route, messages, schema, timeout)
        return await self._chat(route, messages, schema, timeout)

    async def _logged(
        self, route: RouteDecision, messages: list[ChatMessage], schema: dict | None, timeout: float | None
    ) -> ChatResult:
        """Background strong-model calls appear in Activity: started, then completed (with time) or why not."""
        from app.services.activity import ActivityLogger

        log = ActivityLogger(self.db)
        what = TASKS[route.task].label if route.task in TASKS else route.task
        details = {"task": route.task, "model": route.model}
        log.log("ai_analysis_started", f"Strong-model analysis started: {what} ({route.model})", details=details, commit=False)
        started = time.perf_counter()
        try:
            result = await self._chat(route, messages, schema, timeout)
        except ProviderTimeoutError as exc:
            log.log("ai_analysis_timeout", f"{what} timed out after {time.perf_counter() - started:.0f} s ({route.model}): {exc}",
                    level=ActivityLevel.WARNING, details=details, commit=False)
            raise
        except ProviderError as exc:
            log.log("ai_analysis_failed", f"{what} failed ({route.model}): {exc}", level=ActivityLevel.WARNING, details=details, commit=False)
            raise
        seconds = time.perf_counter() - started
        log.log("ai_analysis_completed", f"{what} completed in {seconds:.1f} s ({route.model})",
                details={**details, "seconds": round(seconds, 1)}, commit=False)
        return result

    async def _chat(
        self, route: RouteDecision, messages: list[ChatMessage], schema: dict | None, timeout: float | None
    ) -> ChatResult:
        started = time.perf_counter()
        try:
            return await self._chat_raw(route, messages, schema, timeout)
        finally:
            try:  # compute visibility: model calls per tier and task (never fails the call)
                from app.services.usage import bump

                tier = route.tier.value if route.tier else "custom"
                bump("model", f"{tier}:{route.task}", seconds=time.perf_counter() - started)
            except Exception:  # noqa: BLE001
                logger.debug("usage counter failed", exc_info=True)

    async def _chat_raw(
        self, route: RouteDecision, messages: list[ChatMessage], schema: dict | None, timeout: float | None
    ) -> ChatResult:
        return await self.provider.chat(
            messages,
            ChatOptions(
                model=route.model,
                temperature=route.temperature,
                max_tokens=route.max_tokens,
                response_schema=schema,
                timeout_seconds=timeout if timeout is not None else (
                    self.settings.llm_strong_timeout_seconds if route.tier == ModelTier.STRONG else None
                ),
            ),
        )


def list_routes(router: ModelRouter) -> list[tuple[TaskSpec, RouteDecision]]:
    return [(spec, router.resolve(key)) for key, spec in TASKS.items()]


def set_route(db: Session, task: str, tier: str | None, model: str | None, temperature: float | None) -> None:
    if task not in TASKS:
        raise UnknownTaskError(task)
    row = db.get(ModelRoute, task)
    if tier is None and model is None and temperature is None:
        if row is not None:
            db.delete(row)  # back to the default
    else:
        if row is None:
            row = ModelRoute(task=task)
            db.add(row)
        row.tier = tier
        row.model = model
        row.temperature = temperature
    db.commit()


def custom_routes(db: Session) -> dict[str, ModelRoute]:
    return {r.task: r for r in db.scalars(select(ModelRoute))}
