"""Architecture for future automatic knowledge ingestion.

A KnowledgeSource yields IngestionItems; the Ingestor turns them into memories
through KnowledgeService (same validation, de-duplication and indexing as
manual entries). Phase 2 ships only the contracts and placeholder sources.

Future pipeline: Telegram -> community knowledge -> memory -> agent -> ...
"""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime

from app.memory.records import MemoryInput
from app.models.enums import MemoryKind, MemoryLayer, Platform, SourceType


@dataclass(slots=True)
class IngestionItem:
    """One piece of raw material from a source, before it becomes a memory."""

    external_id: str  # stable id at the source; re-ingesting the same id updates instead of duplicating
    content: str
    title: str | None = None
    author: str | None = None
    occurred_at: datetime | None = None
    url: str | None = None
    tags: list[str] = field(default_factory=list)
    importance: float = 0.4


@dataclass(slots=True)
class IngestionReport:
    source: str
    created: int = 0
    updated: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


class KnowledgeSource(ABC):
    """A place knowledge can be pulled from."""

    key: str
    display_name: str
    source_type: SourceType
    layer: MemoryLayer
    kind: MemoryKind
    platform: Platform | None = None
    description: str = ""

    @property
    def available(self) -> bool:
        return False

    @abstractmethod
    def fetch(self, since: datetime | None = None) -> AsyncIterator[IngestionItem]:
        """Yield new items since the given time."""

    def to_memory(self, item: IngestionItem) -> MemoryInput:
        return MemoryInput(
            content=item.content,
            title=item.title,
            kind=self.kind,
            layer=self.layer,
            tags=item.tags,
            source_type=self.source_type,
            source=item.url or self.display_name,
            external_id=item.external_id,
            platform=self.platform,
            author=item.author,
            importance=item.importance,
            occurred_at=item.occurred_at,
        )


class SourceNotAvailableError(Exception):
    pass
