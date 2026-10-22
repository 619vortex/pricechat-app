from datetime import datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin, UTCDateTime
from app.models.enums import ConnectionStatus, ContentStatus, InteractionType, Platform
from app.models.types import str_enum


class SocialAccount(IdMixin, TimestampMixin, Base):
    """A linked platform account. Credentials are never stored here, only the .env key prefix."""

    __tablename__ = "social_accounts"
    __table_args__ = (UniqueConstraint("platform", "handle"),)

    platform: Mapped[Platform] = mapped_column(str_enum(Platform), nullable=False, index=True)
    handle: Mapped[str] = mapped_column(String(128), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[ConnectionStatus] = mapped_column(
        str_enum(ConnectionStatus), default=ConnectionStatus.NOT_CONNECTED, nullable=False
    )
    credentials_env_prefix: Mapped[str | None] = mapped_column(String(64))
    extra: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class SocialPost(IdMixin, TimestampMixin, Base):
    """A post on a platform: either ours (linked to Content) or an external post we observed."""

    __tablename__ = "social_posts"
    __table_args__ = (UniqueConstraint("platform", "external_id"),)

    platform: Mapped[Platform] = mapped_column(str_enum(Platform), nullable=False, index=True)
    account_id: Mapped[int | None] = mapped_column(
        ForeignKey("social_accounts.id", ondelete="SET NULL"), index=True
    )
    content_id: Mapped[int | None] = mapped_column(
        ForeignKey("content.id", ondelete="SET NULL"), index=True
    )
    external_id: Mapped[str | None] = mapped_column(String(128))
    author_handle: Mapped[str | None] = mapped_column(String(128))
    text: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str | None] = mapped_column(String(512))
    is_own: Mapped[bool] = mapped_column(default=False, nullable=False)
    status: Mapped[ContentStatus] = mapped_column(
        str_enum(ContentStatus), default=ContentStatus.DRAFT, nullable=False
    )
    posted_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)  # latest public metrics

    # Phase 3B/4A: normalised observed posts
    author_profile_id: Mapped[int | None] = mapped_column(ForeignKey("social_profiles.id", ondelete="SET NULL"), index=True)
    conversation_id: Mapped[str | None] = mapped_column(String(64))
    in_reply_to_external_id: Mapped[str | None] = mapped_column(String(64))
    referenced: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)  # quotes / replies / reposts
    media: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, server_default="[]", nullable=False)  # photos / video previews
    lang: Mapped[str | None] = mapped_column(String(16))
    source_kinds: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)  # own | mention | search | watchlist
    rule_ids: Mapped[list[int]] = mapped_column(JSON, default=list, nullable=False)
    metrics_updated_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    content_type: Mapped[str | None] = mapped_column(String(32))  # e.g. post | reply | quote | thread


class Interaction(IdMixin, TimestampMixin, Base):
    """An inbound or outbound engagement: messages, mentions, replies, likes."""

    __tablename__ = "interactions"

    platform: Mapped[Platform] = mapped_column(str_enum(Platform), nullable=False, index=True)
    type: Mapped[InteractionType] = mapped_column(str_enum(InteractionType), nullable=False)
    post_id: Mapped[int | None] = mapped_column(
        ForeignKey("social_posts.id", ondelete="SET NULL"), index=True
    )
    external_id: Mapped[str | None] = mapped_column(String(128))
    author_handle: Mapped[str | None] = mapped_column(String(128))
    text: Mapped[str | None] = mapped_column(Text)
    direction: Mapped[str] = mapped_column(String(16), default="inbound", nullable=False)
    occurred_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    extra: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    profile_id: Mapped[int | None] = mapped_column(ForeignKey("social_profiles.id", ondelete="SET NULL"), index=True)
