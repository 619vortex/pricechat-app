"""Hybrid retrieval: semantic similarity (if an embedding model is available) blended with
keyword/tag matching and memory importance. Degrades to keyword-only if embeddings fail."""

import logging
import time
from dataclasses import replace

from app.memory.embeddings import EmbeddingIndex
from app.memory.retrieval.base import RetrievalQuery, RetrievalResult, RetrievedMemory
from app.memory.retrieval.lexical import LexicalRetriever, query_terms, score_records
from app.memory.store import MemoryFilters, MemoryStore
from app.providers.errors import ProviderError

logger = logging.getLogger(__name__)


class HybridRetriever:
    def __init__(
        self,
        store: MemoryStore,
        index: EmbeddingIndex | None,
        *,
        min_similarity: float = 0.60,
        semantic_weight: float = 0.65,
        lexical_weight: float = 0.35,
        importance_weight: float = 0.1,
        candidate_pool: int = 40,
        min_keyword_coverage: float = 0.5,
        base_filters: MemoryFilters | None = None,
    ) -> None:
        # Always-applied filters, e.g. APPROVED_ONLY so the agent never recalls unreviewed memories.
        self.base_filters = base_filters
        self.min_keyword_coverage = min_keyword_coverage
        self.store = store
        self.index = index
        self.lexical = LexicalRetriever(store)
        self.min_similarity = min_similarity
        self.semantic_weight = semantic_weight
        self.lexical_weight = lexical_weight
        self.importance_weight = importance_weight
        self.candidate_pool = candidate_pool

    async def retrieve(self, query: RetrievalQuery) -> RetrievalResult:
        started = time.perf_counter()
        if not query.text.strip() or query.limit <= 0:
            return RetrievalResult([], "none", 0.0)
        if self.base_filters is not None:
            query = replace(query, filters=self.base_filters.merged(query.filters))

        notes: list[str] = []
        hits = None
        if self.index is None:
            notes.append("Semantic search disabled (no embedding provider configured).")
        else:
            try:
                hits = await self.index.search(query.text, query.filters, self.candidate_pool)
            except ProviderError as exc:
                logger.warning("Semantic retrieval unavailable, using keywords only: %s", exc)
                notes.append(f"Semantic search unavailable: {exc}")

        if hits is None:
            result = await self.lexical.retrieve(query)
            return RetrievalResult(
                result.memories, "lexical", round((time.perf_counter() - started) * 1000, 1), notes
            )

        similarity = {h.memory_id: h.similarity for h in hits if h.similarity >= self.min_similarity}

        # Score lexical matches over the keyword candidates plus the semantic candidates.
        terms = query_terms(query.text)
        lexical_candidates = self.store.search_terms(terms, query.filters, 300)
        records = {r.id: r for r in lexical_candidates}
        records.update(self.store.get_many([i for i in similarity if i not in records]))
        lexical = score_records(list(records.values()), terms, self.store.count())

        span = 1.0 - self.min_similarity
        scored: list[RetrievedMemory] = []
        for rid, record in records.items():
            sim = similarity.get(rid)
            lex = lexical.get(rid)
            if sim is None and lex is None:
                continue
            # Without a meaning match, one shared word (often the brand name itself) is not enough.
            if sim is None and lex is not None and len(lex.matched) / len(terms) < self.min_keyword_coverage:
                continue
            semantic_part = (sim - self.min_similarity) / span if sim is not None else 0.0
            lexical_part = lex.score if lex else 0.0
            score = (
                self.semantic_weight * semantic_part
                + self.lexical_weight * lexical_part
                + self.importance_weight * record.importance
            )
            reasons = []
            if sim is not None:
                reasons.append(f"meaning match {sim:.2f}")
            if lex:
                reasons.append(f"keywords: {', '.join(lex.matched)}")
            scored.append(
                RetrievedMemory(
                    record=record,
                    score=round(score, 4),
                    lexical=round(lexical_part, 4),
                    semantic=round(sim, 4) if sim is not None else None,
                    reasons=tuple(reasons),
                )
            )

        scored.sort(key=lambda m: m.score, reverse=True)
        return RetrievalResult(
            scored[: query.limit], "hybrid", round((time.perf_counter() - started) * 1000, 1), notes
        )
