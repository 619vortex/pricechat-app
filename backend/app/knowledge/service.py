"""Single entry point for adding knowledge to the brain, whether typed in the
dashboard or (later) ingested from Telegram, X, documents or URLs."""

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.memory.embeddings import EmbeddingIndex
from app.memory.records import MemoryInput, MemoryRecord
from app.memory.store import MemoryStore
from app.providers.base import EmbeddingProvider
from app.providers.errors import ProviderError
from app.models.enums import ActivityLevel
from app.services.activity import ActivityLogger

logger = logging.getLogger(__name__)


def _label(record: MemoryRecord) -> str:
    text = record.title or " ".join(record.content.split())
    return text if len(text) <= 60 else text[:59] + "…"


class KnowledgeService:
    def __init__(self, db: Session, agent_id: int, embedder: EmbeddingProvider | None) -> None:
        self.db = db
        self.agent_id = agent_id
        self.store = MemoryStore(db, agent_id)
        self.index = EmbeddingIndex(db, agent_id, embedder) if embedder else None
        self.activity = ActivityLogger(db, agent_id)

    async def add(self, item: MemoryInput, actor: str = "dashboard", log: bool = True) -> tuple[MemoryRecord, bool, bool]:
        """Store a memory and index it for semantic search.

        Returns (record, created, indexed). Indexing failure never loses the memory;
        it is retried by the background backfill.
        """
        record, created = self.store.add(item)
        indexed = await self._index(record)
        if log:
            verb = "Added" if created else "Updated"
            self.activity.log(
                "memory_created" if created else "memory_updated",
                f'{verb} {record.kind.value.replace("_", " ")} "{_label(record)}" to {record.layer.value} knowledge',
                actor=actor,
                details={
                    "memory_id": record.id,
                    "layer": record.layer.value,
                    "kind": record.kind.value,
                    "source_type": record.source_type.value,
                    "tags": record.tags,
                    "indexed": indexed,
                },
            )
        return record, created, indexed

    async def update(self, memory_id: int, changes: dict[str, Any], actor: str = "dashboard") -> tuple[MemoryRecord, bool] | None:
        record = self.store.update(memory_id, changes)
        if record is None:
            return None
        indexed = await self._index(record)
        if "review_status" in changes:
            verb = {"approved": "Approved", "rejected": "Rejected", "pending": "Returned to review"}[record.review_status.value]
            self.activity.log(
                "memory_reviewed",
                f'{verb} {record.layer.value} memory "{_label(record)}"'
                + (" (the agent can now use it)" if record.review_status.value == "approved" else ""),
                level=ActivityLevel.AUDIT,
                actor=actor,
                details={"memory_id": record.id, "review_status": record.review_status.value},
            )
        other = [c for c in changes if c != "review_status"]
        if other:
            self.activity.log(
                "memory_updated",
                f'Updated "{_label(record)}" ({", ".join(other)})',
                actor=actor,
                details={"memory_id": record.id, "fields": other, "indexed": indexed},
            )
        return record, indexed

    def delete(self, memory_id: int, actor: str = "dashboard") -> bool:
        record = self.store.get(memory_id)
        if record is None:
            return False
        self.store.delete(memory_id)
        self.activity.log("memory_deleted", f'Removed "{_label(record)}"', actor=actor, details={"memory_id": memory_id})
        return True

    async def reindex(self, actor: str = "system") -> int:
        if self.index is None:
            return 0
        count = await self.index.backfill()
        if count:
            self.activity.log(
                "memory_indexed",
                f"Indexed {count} {'memory' if count == 1 else 'memories'} for meaning-based search",
                actor=actor,
                details={"count": count, "model": self.index.model},
            )
        return count

    async def _index(self, record: MemoryRecord) -> bool:
        if self.index is None:
            return False
        try:
            await self.index.index([record])
            return True
        except ProviderError as exc:
            logger.warning("Could not index memory %s now (will retry later): %s", record.id, exc)
            return False
