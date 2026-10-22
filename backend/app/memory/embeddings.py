"""Local vector index for memories.

Vectors are stored as L2-normalised float32 blobs in `memory_embeddings`, so
cosine similarity is a dot product. Brute-force numpy search comfortably
handles tens of thousands of memories; swap this module for pgvector or a
dedicated vector store if the knowledge base grows far beyond that.
"""

import hashlib
import logging
from dataclasses import dataclass

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.memory.records import MemoryRecord
from app.memory.store import MemoryFilters
from app.models.memory import Memory, MemoryEmbedding
from app.providers.base import EmbeddingProvider, EmbeddingPurpose
from app.providers.errors import ProviderError

logger = logging.getLogger(__name__)

BATCH_SIZE = 32


def embedding_text(record: MemoryRecord) -> str:
    parts = [record.title or "", record.content]
    if record.tags:
        parts.append("Tags: " + ", ".join(record.tags))
    return "\n".join(p for p in parts if p)


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _normalise(vector: list[float]) -> np.ndarray:
    arr = np.asarray(vector, dtype=np.float32)
    norm = float(np.linalg.norm(arr))
    return arr / norm if norm > 0 else arr


class QueryEmbeddingCache(EmbeddingProvider):
    """Wraps the shared embedder for one request, so a query that several retrievers need (memory search and
    community context in a chat turn) is embedded once. A failure is remembered too: one timeout, not one per caller."""

    def __init__(self, inner: EmbeddingProvider) -> None:
        super().__init__(model=inner.model)
        self.name = inner.name
        self.inner = inner
        self._seen: dict[str, list[float] | Exception] = {}

    async def embed(self, texts: list[str], purpose: EmbeddingPurpose = "document") -> list[list[float]]:
        if purpose != "query" or len(texts) != 1:
            return await self.inner.embed(texts, purpose)
        if texts[0] not in self._seen:
            try:
                self._seen[texts[0]] = (await self.inner.embed(texts, purpose))[0]
            except ProviderError as exc:
                self._seen[texts[0]] = exc
        hit = self._seen[texts[0]]
        if isinstance(hit, Exception):
            raise hit
        return [hit]

    async def aclose(self) -> None:
        """The wrapped embedder is shared by the whole app: never closed from here."""


@dataclass(frozen=True, slots=True)
class SimilarityHit:
    memory_id: int
    similarity: float


class EmbeddingIndex:
    def __init__(self, db: Session, agent_id: int, embedder: EmbeddingProvider) -> None:
        self.db = db
        self.agent_id = agent_id
        self.embedder = embedder

    @property
    def model(self) -> str:
        return self.embedder.model

    async def index(self, records: list[MemoryRecord]) -> int:
        """Embed records whose vector is missing or stale. Raises ProviderError on failure."""
        existing = {
            e.memory_id: e
            for e in self.db.scalars(
                select(MemoryEmbedding).where(MemoryEmbedding.memory_id.in_([r.id for r in records]))
            )
        }
        todo: list[tuple[MemoryRecord, str]] = []
        for record in records:
            text = embedding_text(record)
            current = existing.get(record.id)
            if current is None or current.model != self.model or current.content_hash != content_hash(text):
                todo.append((record, text))

        for start in range(0, len(todo), BATCH_SIZE):
            batch = todo[start : start + BATCH_SIZE]
            vectors = await self.embedder.embed([text for _, text in batch], purpose="document")
            for (record, text), vector in zip(batch, vectors, strict=True):
                arr = _normalise(vector)
                row = existing.get(record.id) or MemoryEmbedding(memory_id=record.id)
                row.model = self.model
                row.dimensions = int(arr.shape[0])
                row.vector = arr.tobytes()
                row.content_hash = content_hash(text)
                self.db.add(row)
            self.db.commit()
        return len(todo)

    async def backfill(self, limit: int = 1000) -> int:
        """Index memories that have no up-to-date vector for the current model."""
        stmt = (
            select(Memory)
            .outerjoin(
                MemoryEmbedding,
                (MemoryEmbedding.memory_id == Memory.id) & (MemoryEmbedding.model == self.model),
            )
            .where(Memory.agent_id == self.agent_id, MemoryEmbedding.id.is_(None))
            .limit(limit)
        )
        records = [MemoryRecord.from_row(r) for r in self.db.scalars(stmt)]
        return await self.index(records) if records else 0

    async def search(
        self, query: str, filters: MemoryFilters | None, limit: int, purpose: EmbeddingPurpose = "query"
    ) -> list[SimilarityHit]:
        # purpose="document" compares like with like (e.g. duplicate detection between two memories).
        query_vec = _normalise((await self.embedder.embed([query], purpose=purpose))[0])

        stmt = (
            select(MemoryEmbedding.memory_id, MemoryEmbedding.vector)
            .join(Memory, Memory.id == MemoryEmbedding.memory_id)
            .where(
                Memory.agent_id == self.agent_id,
                MemoryEmbedding.model == self.model,
                MemoryEmbedding.dimensions == int(query_vec.shape[0]),
            )
        )
        if filters and filters.layers:
            stmt = stmt.where(Memory.layer.in_(filters.layers))
        if filters and filters.kinds:
            stmt = stmt.where(Memory.kind.in_(filters.kinds))
        if filters and filters.review_statuses:
            stmt = stmt.where(Memory.review_status.in_(filters.review_statuses))
        if filters and filters.source_types:
            stmt = stmt.where(Memory.source_type.in_(filters.source_types))

        rows = self.db.execute(stmt).all()
        if not rows:
            return []
        ids = np.fromiter((r[0] for r in rows), dtype=np.int64, count=len(rows))
        matrix = np.vstack([np.frombuffer(r[1], dtype=np.float32) for r in rows])
        sims = matrix @ query_vec
        top = np.argsort(-sims)[:limit]
        return [SimilarityHit(int(ids[i]), float(sims[i])) for i in top]
