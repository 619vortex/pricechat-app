from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Float, ForeignKey, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin, UTCDateTime
from app.models.enums import MemoryKind, MemoryLayer, Platform, ReviewStatus, SourceType
from app.models.types import str_enum


class Memory(IdMixin, TimestampMixin, Base):
    """A unit of long-term knowledge. Manual knowledge entries and future ingested items share this table."""

    __tablename__ = "memories"
    __table_args__ = (UniqueConstraint("agent_id", "source_type", "external_id"),)

    agent_id: Mapped[int] = mapped_column(
        ForeignKey("agent_configs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    layer: Mapped[MemoryLayer] = mapped_column(
        str_enum(MemoryLayer), default=MemoryLayer.BRAND, nullable=False, index=True
    )
    kind: Mapped[MemoryKind] = mapped_column(str_enum(MemoryKind), nullable=False, index=True)
    title: Mapped[str | None] = mapped_column(String(256))
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)

    source_type: Mapped[SourceType] = mapped_column(
        str_enum(SourceType), default=SourceType.MANUAL, nullable=False, index=True
    )
    source: Mapped[str | None] = mapped_column(String(256))  # human label, e.g. "Team call 3 Oct" or a URL
    external_id: Mapped[str | None] = mapped_column(String(128))  # id at the source, for de-duplication
    platform: Mapped[Platform | None] = mapped_column(str_enum(Platform))
    author: Mapped[str | None] = mapped_column(String(128))
    occurred_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    importance: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)
    # Human review gate: AI-derived memories start pending; the agent only recalls approved ones.
    review_status: Mapped[ReviewStatus] = mapped_column(
        str_enum(ReviewStatus), default=ReviewStatus.APPROVED, nullable=False, index=True
    )
    extra: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class MemoryEmbedding(IdMixin, TimestampMixin, Base):
    """Vector for a memory, tied to the embedding model that produced it."""

    __tablename__ = "memory_embeddings"

    memory_id: Mapped[int] = mapped_column(
        ForeignKey("memories.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    model: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    dimensions: Mapped[int] = mapped_column(Integer, nullable=False)
    vector: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)  # float32, L2-normalised
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
