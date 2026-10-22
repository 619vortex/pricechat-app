import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.price_chat import AgentPausedError, AgentProfile, AgentRequest, PriceChatAgent
from app.agents.prompting import BrandProfileData, GoalData
from app.memory import EmbeddingIndex, HybridRetriever, MemoryInput, MemoryStore, SqlConversationMemory
from app.models.activity import ActivityLog
from app.models.agent import AgentConfig
from app.models.conversation import ConversationMessage
from app.models.enums import ActivityLevel, AgentStatus, AgentTask, MemoryKind, PromptPlatform
from app.providers.base import ChatMessage
from app.providers.errors import ModelNotFoundError, ProviderUnavailableError
from app.services.activity import ActivityLogger
from tests.conftest import FakeEmbedder, FakeProvider


def _agent(db: Session, config: AgentConfig, provider: FakeProvider, conversation_id: str = "c1", **profile) -> PriceChatAgent:  # type: ignore[no-untyped-def]
    store = MemoryStore(db, config.id)
    return PriceChatAgent(
        agent_id=config.id,
        profile=AgentProfile(name="Acme Bot", identity="You are Acme Bot.", behavior_rules=["Be brief."], **profile),
        brand=BrandProfileData(brand_name="Acme", tone="Warm", restricted_claims=["Price predictions"]),
        goals=[GoalData("Grow awareness", "Reach new people", 1)],
        provider=provider,
        conversation=SqlConversationMemory(db, config.id, conversation_id, window=10),
        retriever=HybridRetriever(store, EmbeddingIndex(db, config.id, FakeEmbedder())),
        activity=ActivityLogger(db, config.id),
    )


def _events(db: Session) -> list[str]:
    return list(db.scalars(select(ActivityLog.event).order_by(ActivityLog.id)))


async def test_respond_builds_context_from_brand_goals_and_memory(db: Session, agent_config: AgentConfig) -> None:
    provider = FakeProvider()
    agent = _agent(db, agent_config, provider)
    MemoryStore(db, agent_config.id).add(
        MemoryInput("The community meetup is every Thursday.", title="Meetup", kind=MemoryKind.BRAND_FACT)
    )

    reply = await agent.respond(AgentRequest("When is the community meetup?"))

    assert reply.content == "echo: When is the community meetup?"
    assert [m.record.title for m in reply.retrieval.memories] == ["Meetup"]
    system = provider.calls[0][0][0]
    assert system.role == "system"
    for expected in ("You are Acme Bot.", "Brand: Acme", "Tone: Warm", "1. Grow awareness", "Be brief.",
                     "Never state or imply:\n- Price predictions", "every Thursday"):
        assert expected in system.content
    assert reply.result.completion_tokens == 7


async def test_conversation_is_logged_and_used_next_turn(db: Session, agent_config: AgentConfig) -> None:
    provider = FakeProvider()
    await _agent(db, agent_config, provider).respond(AgentRequest("first question"))
    await _agent(db, agent_config, provider).respond(AgentRequest("second question"))  # fresh agent, same conversation

    second_call = provider.calls[1][0]
    assert ChatMessage("user", "first question") in second_call
    assert ChatMessage("assistant", "echo: first question") in second_call
    rows = db.scalars(select(ConversationMessage).order_by(ConversationMessage.id)).all()
    assert [(r.role, r.conversation_id) for r in rows] == [("user", "c1"), ("assistant", "c1")] * 2
    assistant = rows[1]
    assert (assistant.model, assistant.prompt_tokens, assistant.completion_tokens) == ("fake-model", 42, 7)


async def test_activity_logged_for_retrieval_and_inference(db: Session, agent_config: AgentConfig) -> None:
    await _agent(db, agent_config, FakeProvider()).respond(AgentRequest("hello there"))
    assert _events(db)[-2:] == ["memory_retrieval", "ai_inference"]
    inference = db.scalar(select(ActivityLog).where(ActivityLog.event == "ai_inference"))
    assert inference is not None
    assert "Answered" in inference.message and "fake-model" in inference.message
    assert inference.details["completion_tokens"] == 7


async def test_drafting_task_logs_agent_task_and_uses_platform(db: Session, agent_config: AgentConfig) -> None:
    provider = FakeProvider()
    await _agent(db, agent_config, provider).respond(
        AgentRequest("Announce the meetup", task=AgentTask.DRAFT_POST, platform=PromptPlatform.X)
    )
    assert "agent_task" in _events(db)
    assert "280 characters" in provider.calls[0][0][0].content


@pytest.mark.parametrize("error", [ProviderUnavailableError("offline"), ModelNotFoundError("gemma3:27b")])
async def test_provider_failure_is_logged_and_raised(db: Session, agent_config: AgentConfig, error: Exception) -> None:
    provider = FakeProvider()
    provider.error = error  # type: ignore[assignment]
    with pytest.raises(type(error)):
        await _agent(db, agent_config, provider).respond(AgentRequest("hello"))
    failed = db.scalar(select(ActivityLog).where(ActivityLog.event == "ai_inference_failed"))
    assert failed is not None and failed.level == ActivityLevel.ERROR
    assert db.scalar(select(ConversationMessage)) is None  # failed turns are not stored


async def test_paused_agent_refuses(db: Session, agent_config: AgentConfig) -> None:
    with pytest.raises(AgentPausedError):
        await _agent(db, agent_config, FakeProvider(), status=AgentStatus.PAUSED).respond(AgentRequest("hi"))


async def test_model_override_is_passed_to_provider(db: Session, agent_config: AgentConfig) -> None:
    agent = _agent(db, agent_config, FakeProvider(), model_name="gemma3:27b")
    reply = await agent.respond(AgentRequest("hi"))
    assert agent.model == "gemma3:27b"
    assert reply.result.model == "gemma3:27b"


def test_compile_prompt_without_retrieval(db: Session, agent_config: AgentConfig) -> None:
    prompt = _agent(db, agent_config, FakeProvider()).compile_prompt()
    assert "memory" not in prompt.section_keys
    assert prompt.section_keys[0] == "identity"
