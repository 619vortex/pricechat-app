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
