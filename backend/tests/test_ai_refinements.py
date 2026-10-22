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
