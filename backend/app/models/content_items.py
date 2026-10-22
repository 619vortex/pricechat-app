"""Published content from any platform, ready for the public website.

Platform-independent on purpose: YouTube today; X, TikTok, articles or Price Chat originals later.
One row per (platform, external_id): syncing again updates the row, never duplicates it.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, Text, UniqueConstraint, false
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin, UTCDateTime
from app.models.enums import Platform
from app.models.types import str_enum


class ContentItem(IdMixin, TimestampMixin, Base):
    __tablename__ = "content_items"
    __table_args__ = (UniqueConstraint("platform", "external_id"),)

    platform: Mapped[Platform] = mapped_column(str_enum(Platform), nullable=False, index=True)
    external_id: Mapped[str] = mapped_column(String(64), nullable=False)  # e.g. the YouTube video id
    channel_id: Mapped[str | None] = mapped_column(String(64), index=True)
    content_type: Mapped[str] = mapped_column(String(32), default="video", nullable=False)  # short | long_form | ...
    type_source: Mapped[str] = mapped_column(String(16), default="auto", server_default="auto", nullable=False)  # auto | manual
    type_reason: Mapped[str] = mapped_column(Text, default="", server_default="", nullable=False)
    title: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    url: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    thumbnail_url: Mapped[str | None] = mapped_column(String(512))
    thumbnails: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), index=True)
    privacy_status: Mapped[str] = mapped_column(String(16), default="unknown", nullable=False)  # public | unlisted | private | unknown
    available: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1", nullable=False)  # false: deleted or no longer accessible
    unavailable_since: Mapped[datetime | None] = mapped_column(UTCDateTime())
    website_visible: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)
    visibility_source: Mapped[str] = mapped_column(String(16), default="auto", server_default="auto", nullable=False)  # auto | manual
    featured: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)  # latest: views, likes, comments (missing = unknown)
    metrics_updated_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_synced_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    # Future distribution hooks (X post, Telegram announcement, TikTok caption...). Recorded, never executed here.
    distribution: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    # Editorial placement, set by a person in the dashboard (sync never touches these). NULL = none.
    series: Mapped[str | None] = mapped_column(String(32), index=True)  # whats_it_worth | originals
    category: Mapped[str | None] = mapped_column(String(16), index=True)  # markets | crypto | money | culture


class ContentItemMetric(IdMixin, Base):
    """Point-in-time public metrics of one content item (for growth over time). Unknown stays NULL."""

    __tablename__ = "content_item_metrics"

    item_id: Mapped[int] = mapped_column(ForeignKey("content_items.id", ondelete="CASCADE"), index=True, nullable=False)
    captured_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    views: Mapped[int | None] = mapped_column(Integer)
    likes: Mapped[int | None] = mapped_column(Integer)
    comments: Mapped[int | None] = mapped_column(Integer)
