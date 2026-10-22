"""Assembles PriceChatAgent from stored configuration and runtime providers."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.price_chat import AgentProfile, AgentReply, AgentRequest, PriceChatAgent
from app.community.context import CommunityContextProvider
from app.community.tuning import CommunityTuning
from app.db.seed import DEFAULT_AGENT_SLUG, seed_defaults
from app.memory.conversation import SqlConversationMemory
from app.memory.embeddings import EmbeddingIndex, QueryEmbeddingCache
from app.memory.retrieval.hybrid import HybridRetriever
from app.memory.store import APPROVED_ONLY, MemoryStore, clean_text
from app.models.agent import AgentConfig
from app.providers.base import EmbeddingProvider
from app.providers.errors import ProviderError, ProviderUnavailableError
from app.services.activity import ActivityLogger
from app.services.brand_service import brand_data, get_brand, goal_data, list_goals
from app.services.runtime import AppRuntime

DEFAULT_CONVERSATION = "default"


def get_agent_config(db: Session) -> AgentConfig:
    config = db.scalar(select(AgentConfig).where(AgentConfig.slug == DEFAULT_AGENT_SLUG))
    return config or seed_defaults(db)


def build_retriever(
    db: Session, runtime: AppRuntime, agent_id: int, approved_only: bool = True, embedder: EmbeddingProvider | None = None
) -> HybridRetriever:
    store = MemoryStore(db, agent_id)
    embedder = embedder or runtime.embedder
    index = EmbeddingIndex(db, agent_id, embedder) if embedder else None
    return HybridRetriever(
        store,
        index,
        min_similarity=runtime.settings.retrieval_min_similarity,
        base_filters=APPROVED_ONLY if approved_only else None,
    )


def build_agent(db: Session, runtime: AppRuntime, conversation_id: str = DEFAULT_CONVERSATION) -> PriceChatAgent:
    config = get_agent_config(db)
    profile = AgentProfile(
        name=config.name,
        identity=config.identity,
        behavior_rules=list(config.behavior_rules or []),
        status=config.status,
        model_name=config.model_name,
        temperature=config.temperature,
    )
    settings = runtime.settings
    embedder = QueryEmbeddingCache(runtime.embedder) if runtime.embedder else None  # memory + community share one query vector
    return PriceChatAgent(
        agent_id=config.id,
        profile=profile,
        brand=brand_data(get_brand(db, config.id)),
        goals=goal_data(list_goals(db, config.id, enabled_only=True)),
        provider=runtime.provider,
        conversation=SqlConversationMemory(db, config.id, conversation_id, settings.conversation_window),
        retriever=build_retriever(db, runtime, config.id, embedder=embedder),
        activity=ActivityLogger(db, config.id),
        recall_limit=settings.memory_recall_limit,
        memory_char_budget=settings.memory_prompt_char_budget,
        community=CommunityContextProvider(db, embedder, CommunityTuning.from_settings(settings)),
    )


async def run_chat(db: Session, runtime: AppRuntime, request: AgentRequest, conversation_id: str) -> AgentReply:
    """Run one chat turn and keep the provider monitor up to date."""
    agent = build_agent(db, runtime, conversation_id)
    runtime.monitor.load_last_success(db)
    try:
        async with runtime.gate.interactive():  # background model work waits while the user is chatting
            reply = await agent.respond(request)
    except ProviderError as exc:
        runtime.monitor.record_failure(db, agent.agent_id, str(exc), isinstance(exc, ProviderUnavailableError))
        runtime.invalidate_health()
        raise
    runtime.monitor.record_success(db, agent.agent_id, reply.result)
    return reply


def update_agent_config(db: Session, config: AgentConfig, changes: dict[str, Any], actor: str = "dashboard") -> AgentConfig:
    if "behavior_rules" in changes:
        changes["behavior_rules"] = [r for r in (clean_text(x) for x in changes["behavior_rules"]) if r]
    for key in ("name", "identity"):
        if key in changes:
            changes[key] = clean_text(changes[key])
    changed = [k for k, v in changes.items() if getattr(config, k) != v]
    for key in changed:
        setattr(config, key, changes[key])
    db.commit()
    if changed:
        labels = ", ".join(k.replace("_", " ") for k in changed)
        event = "model_config_updated" if set(changed) <= {"model_name", "temperature"} else "agent_config_updated"
        message = f"Agent settings changed: {labels}"
        if "status" in changed:
            message = f"Agent is now {config.status.value}" + (f"; also changed {labels}" if len(changed) > 1 else "")
        if event == "model_config_updated":
            message = f"Model set to {config.model_name or 'default'}" + (
                f", temperature {config.temperature}" if "temperature" in changed and config.temperature is not None else ""
            )
        ActivityLogger(db, config.id).log(event, message, actor=actor, details={"fields": changed})
    return config
