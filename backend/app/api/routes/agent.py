from fastapi import APIRouter, Path, Query

from app.agents.price_chat import AgentRequest
from app.api.deps import DbSession, Runtime, retrieval_out
from app.api.schemas import (
    AgentBrainOut,
    AgentConfigOut,
    AgentConfigUpdate,
    ChatIn,
    ChatOut,
    CompiledPromptOut,
    ConversationMessageOut,
    GoalOut,
    MemoryStatsOut,
    PromptSectionOut,
)
from app.memory.conversation import conversation_count, conversation_history
from app.memory.store import MemoryStore
from app.models.enums import AgentTask, PromptPlatform
from app.providers.factory import available_providers
from app.services.agent_service import build_agent, get_agent_config, run_chat, update_agent_config
from app.services.brand_service import brand_completeness, get_brand, list_goals
from app.services.runtime import AppRuntime

router = APIRouter(prefix="/agent", tags=["agent"])


def memory_stats(db: DbSession, runtime: AppRuntime, agent_id: int) -> MemoryStatsOut:
    embedding_model = runtime.embedder.model if runtime.embedder else None
    stats = MemoryStore(db, agent_id).stats(embedding_model)
    return MemoryStatsOut(**stats, embedding_model=embedding_model, conversations=conversation_count(db, agent_id))


@router.get("", response_model=AgentBrainOut)
def agent_brain(db: DbSession, runtime: Runtime) -> AgentBrainOut:
    config = get_agent_config(db)
    completeness, _ = brand_completeness(get_brand(db, config.id))
    return AgentBrainOut(
        config=AgentConfigOut.model_validate(config),
        effective_model=config.model_name or runtime.provider.model,
        active_goals=[GoalOut.model_validate(g) for g in list_goals(db, config.id, enabled_only=True)],
        memory=memory_stats(db, runtime, config.id),
        brand_completeness=completeness,
        available_providers=available_providers(),
    )


@router.patch("/config", response_model=AgentConfigOut)
def update_config(payload: AgentConfigUpdate, db: DbSession, runtime: Runtime) -> AgentConfigOut:
    changes = payload.model_dump(exclude_unset=True)
    for required in ("name", "identity", "status", "behavior_rules"):
        if required in changes and changes[required] is None:
            changes.pop(required)
    if changes.get("model_name") == "":
        changes["model_name"] = None  # empty string clears the override
    config = update_agent_config(db, get_agent_config(db), changes)
    runtime.invalidate_health()
    return AgentConfigOut.model_validate(config)


@router.post("/chat", response_model=ChatOut)
async def chat(payload: ChatIn, db: DbSession, runtime: Runtime) -> ChatOut:
    # Provider and paused-agent errors are mapped to structured responses in app.main.
    reply = await run_chat(
        db,
        runtime,
        AgentRequest(message=payload.message, task=payload.task, platform=payload.platform),
        payload.conversation_id,
    )
    r = reply.result
    return ChatOut(
        reply=reply.content,
        conversation_id=reply.conversation_id,
        message_id=reply.message_id,
        model=r.model,
        provider=r.provider,
        latency_ms=r.latency_ms,
        prompt_tokens=r.prompt_tokens,
        completion_tokens=r.completion_tokens,
        tokens_per_second=r.tokens_per_second,
        retrieval=retrieval_out(reply.retrieval),
        prompt_chars=len(reply.prompt.text),
    )


@router.get("/conversations/{conversation_id}", response_model=list[ConversationMessageOut])
def get_conversation(
    db: DbSession,
    conversation_id: str = Path(..., pattern=r"^[A-Za-z0-9_\-]{1,64}$"),
    limit: int = Query(100, ge=1, le=500),
) -> list[ConversationMessageOut]:
    config = get_agent_config(db)
    return [ConversationMessageOut.model_validate(m) for m in conversation_history(db, config.id, conversation_id, limit)]


@router.get("/prompt", response_model=CompiledPromptOut)
async def compiled_prompt(
    db: DbSession,
    runtime: Runtime,
    task: AgentTask = AgentTask.CHAT,
    platform: PromptPlatform = PromptPlatform.DASHBOARD,
    query: str | None = Query(None, max_length=2000, description="Optional sample message to preview memory retrieval"),
) -> CompiledPromptOut:
    """Read-only preview of the system prompt exactly as the agent would build it."""
    agent = build_agent(db, runtime)
    has_query = bool(query and query.strip())
    retrieval = await agent.recall(query) if has_query else None  # type: ignore[arg-type]
    community = await agent.recall_community(query) if has_query else None  # type: ignore[arg-type]
    prompt = agent.compile_prompt(retrieval, task, platform, community)
    return CompiledPromptOut(
        text=prompt.text,
        chars=len(prompt.text),
        approx_tokens=len(prompt.text) // 4,
        sections=[PromptSectionOut(key=s.key, title=s.title, chars=len(s.body)) for s in prompt.sections],
        retrieval=retrieval_out(retrieval) if retrieval else None,
    )
