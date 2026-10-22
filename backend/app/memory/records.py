from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.models.enums import MemoryKind, MemoryLayer, Platform, ReviewStatus, SourceType
from app.models.memory import Memory

# Default layer for each kind when the caller doesn't specify one.
DEFAULT_LAYER: dict[MemoryKind, MemoryLayer] = {
    MemoryKind.TEXT: MemoryLayer.BRAND,
    MemoryKind.NOTE: MemoryLayer.BRAND,
    MemoryKind.BRAND_FACT: MemoryLayer.BRAND,
    MemoryKind.DOCUMENT: MemoryLayer.BRAND,
    MemoryKind.COMMUNITY_INSIGHT: MemoryLayer.COMMUNITY,
    MemoryKind.MESSAGE: MemoryLayer.COMMUNITY,
    MemoryKind.POST: MemoryLayer.SOCIAL,
    MemoryKind.SUMMARY: MemoryLayer.CONVERSATION,
}


@dataclass(frozen=True, slots=True)
class MemoryRecord:
    id: int
    layer: MemoryLayer
    kind: MemoryKind
    title: str | None
    content: str
    tags: list[str]
    source_type: SourceType
    source: str | None
    external_id: str | None
    platform: Platform | None
    author: str | None
    importance: float
    occurred_at: datetime | None
    created_at: datetime
    updated_at: datetime
    review_status: ReviewStatus = ReviewStatus.APPROVED
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_row(cls, row: Memory) -> "MemoryRecord":
        return cls(
            id=row.id,
            layer=row.layer,
            kind=row.kind,
            title=row.title,
            content=row.content,
            tags=list(row.tags or []),
            source_type=row.source_type,
            source=row.source,
            external_id=row.external_id,
            platform=row.platform,
            author=row.author,
            importance=row.importance,
            occurred_at=row.occurred_at,
            created_at=row.created_at,
            updated_at=row.updated_at,
            review_status=row.review_status,
            extra=dict(row.extra or {}),
        )


@dataclass(slots=True)
class MemoryInput:
    """Normalised input for creating a memory, from the dashboard or a future ingestion source."""

    content: str
    kind: MemoryKind = MemoryKind.TEXT
    layer: MemoryLayer | None = None
    title: str | None = None
    tags: list[str] = field(default_factory=list)
    source_type: SourceType = SourceType.MANUAL
    source: str | None = None
    external_id: str | None = None
    platform: Platform | None = None
    author: str | None = None
    importance: float = 0.5
    occurred_at: datetime | None = None
    review_status: ReviewStatus = ReviewStatus.APPROVED
    extra: dict[str, Any] = field(default_factory=dict)  # e.g. AI generation metadata

    def resolved_layer(self) -> MemoryLayer:
        return self.layer or DEFAULT_LAYER.get(self.kind, MemoryLayer.BRAND)
