import re
import unicodedata
from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.memory.records import MemoryInput, MemoryRecord
from app.models.enums import MemoryKind, MemoryLayer, ReviewStatus, SourceType
from app.models.memory import Memory, MemoryEmbedding

MAX_TAGS = 20
_TAG_CLEAN = re.compile(r"[^a-z0-9 _\-/.#]")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def clean_text(value: str) -> str:
    """Normalise unicode and strip control characters (keeps newlines and tabs)."""
    return _CONTROL.sub("", unicodedata.normalize("NFC", value)).strip()


def normalize_tags(tags: list[str]) -> list[str]:
    out: list[str] = []
    for tag in tags:
        t = _TAG_CLEAN.sub("", clean_text(tag).lower()).strip(" -")[:40]
        if t and t not in out:
            out.append(t)
    return out[:MAX_TAGS]


HUMAN_CONTROLLED = frozenset({"review_status", "importance"})


@dataclass(frozen=True, slots=True)
class MemoryFilters:
    layers: tuple[MemoryLayer, ...] | None = None
    kinds: tuple[MemoryKind, ...] | None = None
    source_types: tuple[SourceType, ...] | None = None
    tag: str | None = None
    review_statuses: tuple[ReviewStatus, ...] | None = None

    def merged(self, other: "MemoryFilters | None") -> "MemoryFilters":
        if other is None:
            return self
        return MemoryFilters(
            layers=other.layers or self.layers,
            kinds=other.kinds or self.kinds,
            source_types=other.source_types or self.source_types,
            tag=other.tag or self.tag,
            review_statuses=other.review_statuses or self.review_statuses,
        )


APPROVED_ONLY = MemoryFilters(review_statuses=(ReviewStatus.APPROVED,))


class MemoryStore:
    """CRUD and query access to long-term memories for one agent."""

    def __init__(self, db: Session, agent_id: int) -> None:
        self.db = db
        self.agent_id = agent_id

    def add(self, item: MemoryInput) -> tuple[MemoryRecord, bool]:
        """Create a memory. If (source_type, external_id) already exists it is updated instead.

        Returns (record, created).
        """
        values = self._values(item)
        if item.external_id:
            existing = self.db.scalar(
                select(Memory).where(
                    Memory.agent_id == self.agent_id,
                    Memory.source_type == item.source_type,
                    Memory.external_id == item.external_id,
                )
            )
            if existing is not None:
                for key, value in values.items():
                    # Re-ingesting never overrides a human's review or importance decision.
                    if key in HUMAN_CONTROLLED:
                        continue
                    setattr(existing, key, value)
                self.db.commit()
                return MemoryRecord.from_row(existing), False

        row = Memory(agent_id=self.agent_id, **values)
        self.db.add(row)
        self.db.commit()
        return MemoryRecord.from_row(row), True

    def get(self, memory_id: int) -> MemoryRecord | None:
        row = self._row(memory_id)
        return MemoryRecord.from_row(row) if row else None

    def update(self, memory_id: int, changes: dict[str, Any]) -> MemoryRecord | None:
        row = self._row(memory_id)
        if row is None:
            return None
        for key, value in changes.items():
            if key == "tags":
                value = normalize_tags(value or [])
            elif key == "content":
                value = clean_text(value)
                if not value:
                    raise ValueError("Memory content cannot be empty")
            elif key in ("title", "source", "author"):
                value = (clean_text(value) or None) if isinstance(value, str) else None
            elif key == "importance":
                value = max(0.0, min(1.0, float(value)))
            setattr(row, key, value)
        row.updated_at = utcnow()
        self.db.commit()
        return MemoryRecord.from_row(row)

    def delete(self, memory_id: int) -> bool:
        row = self._row(memory_id)
        if row is None:
            return False
        self.db.delete(row)
        self.db.commit()
        return True

    def list(self, filters: MemoryFilters | None = None, limit: int = 50, offset: int = 0) -> list[MemoryRecord]:
        stmt = self._filtered(filters).order_by(Memory.updated_at.desc(), Memory.id.desc())
        return [MemoryRecord.from_row(r) for r in self.db.scalars(stmt.limit(limit).offset(offset))]

    def get_many(self, ids: list[int]) -> dict[int, MemoryRecord]:
        if not ids:
            return {}
        rows = self.db.scalars(select(Memory).where(Memory.agent_id == self.agent_id, Memory.id.in_(ids)))
        return {r.id: MemoryRecord.from_row(r) for r in rows}

    def count(self, filters: MemoryFilters | None = None) -> int:
        stmt = self._filtered(filters).with_only_columns(func.count()).order_by(None)
        return int(self.db.scalar(stmt) or 0)

    def search_terms(self, terms: list[str], filters: MemoryFilters | None = None, limit: int = 300) -> list[MemoryRecord]:
        """Memories whose title, content or tags contain any of the terms (case-insensitive)."""
        if not terms:
            return []
        haystacks = (func.lower(Memory.content), func.lower(func.coalesce(Memory.title, "")), func.lower(cast(Memory.tags, String)))
        clauses = [h.contains(t, autoescape=True) for t in terms for h in haystacks]
        stmt = self._filtered(filters).where(or_(*clauses)).order_by(Memory.importance.desc()).limit(limit)
        return [MemoryRecord.from_row(r) for r in self.db.scalars(stmt)]

    def stats(self, embedding_model: str | None) -> dict[str, Any]:
        def grouped(column: Any) -> dict[str, int]:
            stmt = (
                select(column, func.count())
                .where(Memory.agent_id == self.agent_id)
                .group_by(column)
            )
            return {str(k): int(v) for k, v in self.db.execute(stmt).all()}

        embedded = 0
        if embedding_model:
            embedded = int(
                self.db.scalar(
                    select(func.count())
                    .select_from(MemoryEmbedding)
                    .join(Memory, Memory.id == MemoryEmbedding.memory_id)
                    .where(Memory.agent_id == self.agent_id, MemoryEmbedding.model == embedding_model)
                )
                or 0
            )
        return {
            "total": self.count(),
            "by_layer": grouped(Memory.layer),
            "by_kind": grouped(Memory.kind),
            "by_source_type": grouped(Memory.source_type),
            "embedded": embedded,
        }

    def _row(self, memory_id: int) -> Memory | None:
        return self.db.scalar(select(Memory).where(Memory.id == memory_id, Memory.agent_id == self.agent_id))

    def _filtered(self, filters: MemoryFilters | None):  # type: ignore[no-untyped-def]
        stmt = select(Memory).where(Memory.agent_id == self.agent_id)
        if filters is None:
            return stmt
        if filters.layers:
            stmt = stmt.where(Memory.layer.in_(filters.layers))
        if filters.kinds:
            stmt = stmt.where(Memory.kind.in_(filters.kinds))
        if filters.source_types:
            stmt = stmt.where(Memory.source_type.in_(filters.source_types))
        if filters.review_statuses:
            stmt = stmt.where(Memory.review_status.in_(filters.review_statuses))
        if filters.tag:
            tag = normalize_tags([filters.tag])
            if tag:
                stmt = stmt.where(cast(Memory.tags, String).contains(f'"{tag[0]}"', autoescape=True))
        return stmt

    @staticmethod
    def _values(item: MemoryInput) -> dict[str, Any]:
        content = clean_text(item.content)
        if not content:
            raise ValueError("Memory content cannot be empty")
        values = asdict(item)
        values.update(
            content=content,
            title=clean_text(item.title) or None if item.title else None,
            source=clean_text(item.source) or None if item.source else None,
            author=clean_text(item.author) or None if item.author else None,
            tags=normalize_tags(item.tags),
            importance=max(0.0, min(1.0, item.importance)),
            layer=item.resolved_layer(),
        )
        return values
