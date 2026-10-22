from datetime import datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin, UTCDateTime
from app.models.enums import ContentStatus, Platform
from app.models.types import str_enum


class Content(IdMixin, TimestampMixin, Base):
    """A piece of brand content, independent of where (or whether) it is published."""

    __tablename__ = "content"

    agent_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_configs.id", ondelete="SET NULL"), index=True
    )
    title: Mapped[str | None] = mapped_column(String(256))
    body: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[ContentStatus] = mapped_column(
        str_enum(ContentStatus), default=ContentStatus.DRAFT, nullable=False, index=True
    )
    target_platform: Mapped[Platform | None] = mapped_column(str_enum(Platform))
    scheduled_for: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_by: Mapped[str] = mapped_column(String(64), default="human", nullable=False)
    extra: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    # Phase 3B: drafts generated from opportunities, with variants and provenance
    draft_type: Mapped[str | None] = mapped_column(String(32))
    opportunity_id: Mapped[int | None] = mapped_column(ForeignKey("content_opportunities.id", ondelete="SET NULL"), index=True)
    variant_group: Mapped[str | None] = mapped_column(String(64), index=True)
    variant_label: Mapped[str | None] = mapped_column(String(64))
    context: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)  # why this draft exists
