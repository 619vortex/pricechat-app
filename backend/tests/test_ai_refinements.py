"""Regression tests for the AI-side review (feedback edits, embeddings, gate, providers, extraction)."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.learning.feedback import record_feedback
from app.memory.embeddings import EmbeddingIndex, content_hash, embedding_text
from app.memory.records import MemoryRecord
from app.models.agent import AgentConfig
from app.models.enums import FeedbackDecision, FeedbackTarget, ReviewStatus
from app.models.memory import Memory, MemoryEmbedding
from app.providers.errors import ProviderUnavailableError
from tests.conftest import FakeEmbedder


def _memory(db: Session, agent: AgentConfig, content: str = "Members ask about fees") -> Memory:
    mem = Memory(agent_id=agent.id, layer="community", kind="community_insight", content=content, title="Fees",
                 review_status=ReviewStatus.PENDING, importance=0.5, tags=["concern"], source_type="telegram")
    db.add(mem)
    db.commit()
    return mem


# --- feedback edits ------------------------------------------------------------------------------


async def test_feedback_edit_is_cut_to_the_column_size(db: Session, agent_config: AgentConfig) -> None:
    mem = _memory(db, agent_config)
    await record_feedback(db, FeedbackTarget.MEMORY, mem.id, FeedbackDecision.EDIT, None, edits={"title": "t" * 600})
    assert len(mem.title) == 256  # memories.title is String(256): Postgres would reject the longer value


async def test_feedback_edit_of_a_memory_re_embeds_it(db: Session, agent_config: AgentConfig) -> None:
    emb = FakeEmbedder()
    mem = _memory(db, agent_config)
    await EmbeddingIndex(db, agent_config.id, emb).index([MemoryRecord.from_row(mem)])
    old = db.scalar(select(MemoryEmbedding.vector).where(MemoryEmbedding.memory_id == mem.id))

    await record_feedback(db, FeedbackTarget.MEMORY, mem.id, FeedbackDecision.EDIT, emb,
                          edits={"content": "Members want lower withdrawal limits"})
    row = db.scalar(select(MemoryEmbedding).where(MemoryEmbedding.memory_id == mem.id))
    assert row is not None and row.vector != old
    assert row.content_hash == content_hash(embedding_text(MemoryRecord.from_row(mem)))


async def test_feedback_edit_with_embedder_down_leaves_it_for_the_backfill(db: Session, agent_config: AgentConfig) -> None:
    emb = FakeEmbedder()
    mem = _memory(db, agent_config)
    index = EmbeddingIndex(db, agent_config.id, emb)
    await index.index([MemoryRecord.from_row(mem)])

    emb.error = ProviderUnavailableError("down")
    await record_feedback(db, FeedbackTarget.MEMORY, mem.id, FeedbackDecision.EDIT, emb, edits={"content": "Brand new text"})
    assert db.scalar(select(MemoryEmbedding).where(MemoryEmbedding.memory_id == mem.id)) is None

    emb.error = None
    assert await index.backfill() == 1  # the backfill only sees memories without a vector: the stale one would be skipped
    row = db.scalar(select(MemoryEmbedding).where(MemoryEmbedding.memory_id == mem.id))
    assert row is not None and row.content_hash == content_hash(embedding_text(MemoryRecord.from_row(mem)))


# --- AI gate ---------------------------------------------------------------------------------------


async def test_gate_waiting_count_survives_failures_and_cancelled_waiters() -> None:
    import asyncio

    import pytest

    from app.ai.gate import AIGate

    gate = AIGate(max_wait_seconds=5)
    started, release = asyncio.Event(), asyncio.Event()

    async def failing_job() -> None:
        async with gate.background("a"):
            started.set()
            await release.wait()
            raise RuntimeError("model said no")

    async def queued_job() -> None:
        async with gate.background("b"):
            pass

    a = asyncio.create_task(failing_job())
    await started.wait()
    b = asyncio.create_task(queued_job())
    c = asyncio.create_task(queued_job())
    await asyncio.sleep(0)
    assert gate.status()["background_waiting"] == 2

    c.cancel()  # a job timeout while queued behind "a"
    await asyncio.gather(c, return_exceptions=True)
    assert gate.status()["background_waiting"] == 1

    release.set()  # "a" fails: that must not take "b" off the queue count
    with pytest.raises(RuntimeError):
        await a
    await b
    assert gate.waiting == 0 and gate.status()["background_task"] is None


# --- Ollama provider -------------------------------------------------------------------------------


async def test_ollama_credentials_in_the_url_never_reach_errors_or_the_dashboard() -> None:
    import httpx
    import pytest

    from app.providers.ollama import OllamaProvider

    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        raise httpx.ConnectError("refused", request=request)

    p = OllamaProvider(model="m", base_url="https://owner:s3cret@ollama.example.com:8443/", transport=httpx.MockTransport(handler))
    assert p.endpoint == "https://ollama.example.com:8443"
    with pytest.raises(ProviderUnavailableError) as err:
        await p.chat([])
    assert "s3cret" not in str(err.value) and "ollama.example.com" in str(err.value)
    assert seen["auth"] and seen["auth"].startswith("Basic ")  # the credentials are still used for the request
    health = await p.health()
    assert "s3cret" not in (health.detail or "") and "s3cret" not in (health.endpoint or "")


async def test_ollama_null_or_non_object_replies_are_provider_errors() -> None:
    import httpx
    import pytest

    from app.providers.errors import ProviderResponseError
    from app.providers.ollama import OllamaProvider

    replies = iter([
        httpx.Response(200, json={"model": "m", "message": {"role": "assistant", "content": None}}),
        httpx.Response(200, json=["not", "an", "object"]),
        httpx.Response(500, json=["boom"]),
    ])
    p = OllamaProvider(model="m", transport=httpx.MockTransport(lambda r: next(replies)))
    for _ in range(3):
        with pytest.raises(ProviderResponseError):  # never AttributeError -> a 500 / a crashed job
            await p.chat([])


# --- conversation history ----------------------------------------------------------------------------


def test_history_sent_to_the_model_is_char_bounded(db: Session, agent_config: AgentConfig) -> None:
    from app.memory.conversation import HISTORY_CHAR_BUDGET, SqlConversationMemory, TurnContext
    from app.providers.base import ChatResult

    memory = SqlConversationMemory(db, agent_config.id, "long", window=20)
    for i in range(6):  # six 8,000-char questions with 3,000-char answers: ~66k chars in the window
        memory.record_exchange(f"q{i} " + "x" * 8000, ChatResult(f"a{i} " + "y" * 3000, "m", "fake"), TurnContext(), [])

    history = memory.recent()
    assert sum(len(m.content) for m in history) <= HISTORY_CHAR_BUDGET
    assert history[0].role == "user" and history[-1].content.startswith("a5")  # newest kept, starts on a user turn
    assert len(SqlConversationMemory(db, agent_config.id, "long", window=4, char_budget=10**6).recent()) == 4
