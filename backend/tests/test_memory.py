from sqlalchemy import select
from sqlalchemy.orm import Session

from app.memory import (
    EmbeddingIndex,
    HybridRetriever,
    LexicalRetriever,
    MemoryFilters,
    MemoryInput,
    MemoryStore,
    RetrievalQuery,
    SqlConversationMemory,
    TurnContext,
)
from app.memory.retrieval.lexical import query_terms
from app.models.agent import AgentConfig
from app.models.enums import MemoryKind, MemoryLayer, Platform, SourceType
from app.models.memory import MemoryEmbedding
from app.providers.base import ChatResult
from app.providers.errors import ProviderUnavailableError
from tests.conftest import FakeEmbedder


def _seed_memories(store: MemoryStore) -> None:
    store.add(MemoryInput("The weekly community call happens every Thursday at 18:00 UTC.", title="Community call", kind=MemoryKind.BRAND_FACT, tags=["Events", "community"], importance=0.8))
    store.add(MemoryInput("Members keep asking how alerts work on mobile.", kind=MemoryKind.COMMUNITY_INSIGHT, tags=["questions"]))
    store.add(MemoryInput("Our logo colour is deep green.", title="Brand colour", kind=MemoryKind.BRAND_FACT, tags=["design"]))


# --- store ------------------------------------------------------------------------


def test_add_normalises_and_assigns_layer(db: Session, agent_config: AgentConfig) -> None:
    store = MemoryStore(db, agent_config.id)
    record, created = store.add(
        MemoryInput("  Hello\x00 world  ", title=" Greeting ", kind=MemoryKind.COMMUNITY_INSIGHT,
                    tags=["Alpha", "alpha", " Beta!! ", ""], importance=3.0)
    )
    assert created
    assert record.content == "Hello world"
    assert record.title == "Greeting"
    assert record.tags == ["alpha", "beta"]
    assert record.importance == 1.0
    assert record.layer == MemoryLayer.COMMUNITY  # derived from kind
    assert record.source_type == SourceType.MANUAL


def test_external_id_deduplicates(db: Session, agent_config: AgentConfig) -> None:
    store = MemoryStore(db, agent_config.id)
    first, created = store.add(MemoryInput("v1", kind=MemoryKind.MESSAGE, source_type=SourceType.TELEGRAM,
                                           external_id="chat1:42", platform=Platform.TELEGRAM, author="alice"))
    second, created_again = store.add(MemoryInput("v2", kind=MemoryKind.MESSAGE, source_type=SourceType.TELEGRAM,
                                                  external_id="chat1:42", platform=Platform.TELEGRAM, author="alice"))
    assert created and not created_again
    assert first.id == second.id and second.content == "v2"
    assert store.count() == 1
    assert second.author == "alice" and second.platform == Platform.TELEGRAM


def test_filters_and_stats(db: Session, agent_config: AgentConfig) -> None:
    store = MemoryStore(db, agent_config.id)
    _seed_memories(store)
    assert store.count(MemoryFilters(layers=(MemoryLayer.COMMUNITY,))) == 1
    assert store.count(MemoryFilters(tag="Events")) == 1
    assert store.count(MemoryFilters(kinds=(MemoryKind.BRAND_FACT,))) == 2
    stats = store.stats(None)
    assert stats["total"] == 3
    assert stats["by_layer"] == {"brand": 2, "community": 1}


def test_update_and_delete(db: Session, agent_config: AgentConfig) -> None:
    store = MemoryStore(db, agent_config.id)
    record, _ = store.add(MemoryInput("original"))
    updated = store.update(record.id, {"content": "changed", "tags": ["New Tag"]})
    assert updated is not None and updated.content == "changed" and updated.tags == ["new tag"]
    assert store.delete(record.id) and store.get(record.id) is None
    assert store.update(9999, {"content": "x"}) is None


# --- lexical retrieval -----------------------------------------------------------------


def test_query_terms_drop_stopwords_and_stem() -> None:
    assert query_terms("When is the community call happening?") == ["community", "call", "happen"]


async def test_lexical_retrieval_ranks_relevant_first(db: Session, agent_config: AgentConfig) -> None:
    store = MemoryStore(db, agent_config.id)
    _seed_memories(store)
    result = await LexicalRetriever(store).retrieve(RetrievalQuery("When is the community call?", limit=5))
    assert result.strategy == "lexical"
    assert result.memories[0].record.title == "Community call"
    assert "keywords" in result.memories[0].reasons[0]
    assert all("logo" not in m.record.content for m in result.memories)


async def test_lexical_matches_tags(db: Session, agent_config: AgentConfig) -> None:
    store = MemoryStore(db, agent_config.id)
    _seed_memories(store)
    result = await LexicalRetriever(store).retrieve(RetrievalQuery("design", limit=5))
    assert [m.record.title for m in result.memories] == ["Brand colour"]


async def test_no_match_returns_nothing(db: Session, agent_config: AgentConfig) -> None:
    store = MemoryStore(db, agent_config.id)
    _seed_memories(store)
    result = await LexicalRetriever(store).retrieve(RetrievalQuery("quantum entanglement", limit=5))
    assert result.memories == []


# --- embeddings + hybrid -----------------------------------------------------------------


async def test_embedding_index_and_staleness(db: Session, agent_config: AgentConfig) -> None:
    store = MemoryStore(db, agent_config.id)
    embedder = FakeEmbedder()
    index = EmbeddingIndex(db, agent_config.id, embedder)
    record, _ = store.add(MemoryInput("alpha beta"))
    assert await index.index([record]) == 1
    assert await index.index([record]) == 0  # unchanged -> skipped
    changed = store.update(record.id, {"content": "gamma delta"})
    assert changed is not None
    assert await index.index([changed]) == 1  # content changed -> re-embedded
    row = db.scalar(select(MemoryEmbedding).where(MemoryEmbedding.memory_id == record.id))
    assert row is not None and row.model == "fake-embed" and row.dimensions == 64


async def test_backfill_indexes_missing(db: Session, agent_config: AgentConfig) -> None:
    store = MemoryStore(db, agent_config.id)
    _seed_memories(store)
    index = EmbeddingIndex(db, agent_config.id, FakeEmbedder())
    assert await index.backfill() == 3
    assert await index.backfill() == 0
    assert store.stats("fake-embed")["embedded"] == 3


async def test_hybrid_finds_meaning_without_keyword_overlap(db: Session, agent_config: AgentConfig) -> None:
    # The embedder treats 'meetup' and 'call' as the same concept; keywords alone would miss it.
    embedder = FakeEmbedder(synonyms={"meetup": "call", "gathering": "call"})
    store = MemoryStore(db, agent_config.id)
    _seed_memories(store)
    index = EmbeddingIndex(db, agent_config.id, embedder)
    await index.backfill()

    lexical = await LexicalRetriever(store).retrieve(RetrievalQuery("weekly meetup", limit=3))
    hybrid = await HybridRetriever(store, index, min_similarity=0.2).retrieve(RetrievalQuery("weekly meetup", limit=3))

    assert hybrid.strategy == "hybrid"
    assert hybrid.memories[0].record.title == "Community call"
    assert hybrid.memories[0].semantic is not None
    assert any("meaning match" in r for r in hybrid.memories[0].reasons)
    # lexical still matches "weekly" but hybrid ranks by meaning and keywords together
    assert lexical.memories[0].record.title == "Community call"


async def test_hybrid_falls_back_to_keywords_when_embedder_down(db: Session, agent_config: AgentConfig) -> None:
    embedder = FakeEmbedder()
    store = MemoryStore(db, agent_config.id)
    _seed_memories(store)
    embedder.error = ProviderUnavailableError("embedder offline")
    result = await HybridRetriever(store, EmbeddingIndex(db, agent_config.id, embedder)).retrieve(
        RetrievalQuery("community call", limit=3)
    )
    assert result.strategy == "lexical"
    assert result.memories[0].record.title == "Community call"
    assert "embedder offline" in result.notes[0]


async def test_hybrid_respects_layer_filter(db: Session, agent_config: AgentConfig) -> None:
    store = MemoryStore(db, agent_config.id)
    _seed_memories(store)
    index = EmbeddingIndex(db, agent_config.id, FakeEmbedder())
    await index.backfill()
    result = await HybridRetriever(store, index, min_similarity=0.0).retrieve(
        RetrievalQuery("community call alerts", limit=5, filters=MemoryFilters(layers=(MemoryLayer.COMMUNITY,)))
    )
    assert {m.record.layer for m in result.memories} == {MemoryLayer.COMMUNITY}


# --- conversation memory -----------------------------------------------------------------


def test_conversation_memory_persists_and_windows(db: Session, agent_config: AgentConfig) -> None:
    memory = SqlConversationMemory(db, agent_config.id, "conv-1", window=4)
    for i in range(3):
        memory.record_exchange(f"q{i}", ChatResult(f"a{i}", "m", "fake"), TurnContext(), [])
    # A new instance (e.g. after a restart) sees the same history.
    again = SqlConversationMemory(db, agent_config.id, "conv-1", window=4)
    assert [(m.role, m.content) for m in again.recent()] == [("user", "q1"), ("assistant", "a1"), ("user", "q2"), ("assistant", "a2")]
    assert SqlConversationMemory(db, agent_config.id, "other", window=4).recent() == []


def test_conversation_window_never_starts_with_assistant(db: Session, agent_config: AgentConfig) -> None:
    memory = SqlConversationMemory(db, agent_config.id, "conv-2", window=3)
    for i in range(2):
        memory.record_exchange(f"q{i}", ChatResult(f"a{i}", "m", "fake"), TurnContext(), [])
    assert memory.recent()[0].role == "user"


async def test_hybrid_ignores_single_common_keyword_without_meaning(db: Session, agent_config: AgentConfig) -> None:
    # Every memory mentions the brand name; a question sharing only that word should recall nothing.
    store = MemoryStore(db, agent_config.id)
    store.add(MemoryInput("Price Chat community call is on Thursdays."))
    store.add(MemoryInput("Price Chat mascot is an owl."))
    index = EmbeddingIndex(db, agent_config.id, FakeEmbedder())
    await index.backfill()
    retriever = HybridRetriever(store, index, min_similarity=0.95)  # effectively no meaning matches
    unrelated = await retriever.retrieve(RetrievalQuery("Will the token price go up next month?", limit=5))
    assert unrelated.memories == []
    related = await retriever.retrieve(RetrievalQuery("mascot owl", limit=5))
    assert [m.record.content for m in related.memories] == ["Price Chat mascot is an owl."]
