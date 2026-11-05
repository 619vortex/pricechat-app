"""The News Desk: Price Chat's automatic X news account (app/newsdesk). Settings, what it read, and every post it made or would make."""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, Boolean, Float, Index, Integer, String, Text, false, true
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin, UTCDateTime


class NdSettings(Base):
    """One row. Admin › X › News Desk. mode dry_run never touches X; going live is a deliberate switch."""

    __tablename__ = "nd_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    automation: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true(), nullable=False)
    mode: Mapped[str] = mapped_column(String(10), default="dry_run", server_default="dry_run", nullable=False)  # dry_run | live
    kill_switch: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)
    paused_until: Mapped[datetime | None] = mapped_column(UTCDateTime())
    pause_reason: Mapped[str | None] = mapped_column(Text)  # set by the desk itself when X refuses it
    target_per_day: Mapped[int] = mapped_column(Integer, default=12, server_default="12", nullable=False)
    max_breaking_per_day: Mapped[int] = mapped_column(Integer, default=6, server_default="6", nullable=False)
    breaking_gap_minutes: Mapped[int] = mapped_column(Integer, default=20, server_default="20", nullable=False)
    sensitivity: Mapped[str] = mapped_column(String(10), default="medium", server_default="medium", nullable=False)  # low | medium | high
    categories: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    feeds: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    watchlist: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    state: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)  # last prices, feed health, last run


class NdStory(IdMixin, TimestampMixin, Base):
    """One item read from a feed."""

    __tablename__ = "nd_stories"
    __table_args__ = (Index("ix_nd_stories_created", "created_at"),)

    link: Mapped[str] = mapped_column(String(500), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    source: Mapped[str] = mapped_column(String(60), nullable=False)
    tier: Mapped[int] = mapped_column(Integer, default=2, nullable=False)  # 0 official, 1 major outlet, 2 specialist
    category: Mapped[str | None] = mapped_column(String(16))
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    fingerprint: Mapped[str] = mapped_column(Text, default="", nullable=False)
    score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    used: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)


class NdPost(IdMixin, TimestampMixin, Base):
    """A post: queued, published, or what a dry run would have published. Every source and check is kept with it."""

    __tablename__ = "nd_posts"
    __table_args__ = (Index("ix_nd_posts_status", "status", "created_at"), Index("ix_nd_posts_key", "key", "created_at"))

    kind: Mapped[str] = mapped_column(String(10), nullable=False)  # news | market
    category: Mapped[str] = mapped_column(String(16), nullable=False)
    priority: Mapped[str] = mapped_column(String(10), default="scheduled", nullable=False)  # scheduled | breaking
    # queued → publishing → published | failed | uncertain; dry_run: what would have gone out; rejected; expired
    status: Mapped[str] = mapped_column(String(12), default="queued", nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    original_text: Mapped[str | None] = mapped_column(Text)  # before a person edited it
    key: Mapped[str] = mapped_column(String(160), nullable=False)  # the same event never goes out twice
    fingerprint: Mapped[str] = mapped_column(Text, default="", nullable=False)
    score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    facts: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)  # the verified figures, as of when
    sources: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)  # [{name, url, at}]
    checks: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    not_before: Mapped[datetime | None] = mapped_column(UTCDateTime())
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    x_id: Mapped[str | None] = mapped_column(String(32))
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class NdTelegramSettings(Base):
    """One row. The Telegram lane's own switch and limits: X's settings don't touch it, nor it them."""

    __tablename__ = "nd_telegram_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true(), nullable=False)
    max_per_day: Mapped[int] = mapped_column(Integer, default=10, server_default="10", nullable=False)
    gap_minutes: Mapped[int] = mapped_column(Integer, default=45, server_default="45", nullable=False)
    state: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)  # last run, and why it's paused


class NdTelegramPost(IdMixin, TimestampMixin, Base):
    """One story's one try at the channel. message_id is Telegram's own answer: without it, nothing here claims to have been delivered."""

    __tablename__ = "nd_telegram_posts"
    __table_args__ = (Index("ix_nd_telegram_posts_status", "status", "created_at"),)

    story_link: Mapped[str] = mapped_column(String(500), unique=True, nullable=False)  # the claim: one story, one send
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    headline: Mapped[str] = mapped_column(String(200), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(60), nullable=False)
    sources: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)  # [{name, url}]
    fingerprint: Mapped[str] = mapped_column(Text, default="", server_default="", nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False)  # publishing -> delivered | uncertain; refused: it failed the checks
    writer: Mapped[str | None] = mapped_column(String(10))  # model | feed
    chat: Mapped[str | None] = mapped_column(String(64))
    chat_id: Mapped[int | None] = mapped_column(BigInteger)
    message_id: Mapped[int | None] = mapped_column(BigInteger)
    error: Mapped[str | None] = mapped_column(Text)
    delivered_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    @property
    def url(self) -> str | None:
        return f"https://t.me/{self.chat[1:]}/{self.message_id}" if self.message_id and self.chat and self.chat.startswith("@") else None
