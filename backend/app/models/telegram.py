from datetime import datetime

from sqlalchemy import BigInteger, Boolean, ForeignKey, Index, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin, UTCDateTime
from app.models.enums import MessageCategory
from app.models.types import str_enum


class TelegramSource(IdMixin, TimestampMixin, Base):
    """An allow-listed Telegram chat. Messages from any chat not listed here are ignored."""

    __tablename__ = "telegram_sources"

    chat_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(256), nullable=False)
    chat_type: Mapped[str | None] = mapped_column(String(32))  # group | supergroup | channel | private
    username: Mapped[str | None] = mapped_column(String(128))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    ingestion_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_sync_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_message_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    message_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text)
    last_error_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    via: Mapped[str] = mapped_column(String(16), default="bot", server_default="bot", nullable=False)  # bot | account


class TelegramMessage(IdMixin, TimestampMixin, Base):
    """A raw message from an allow-listed chat. created_at is the ingestion time."""

    __tablename__ = "telegram_messages"
    __table_args__ = (
        UniqueConstraint("chat_id", "message_id"),
        Index("ix_telegram_messages_chat_sent", "chat_id", "sent_at"),
        Index("ix_telegram_messages_author", "author_id", "chat_id", "sent_at"),  # one member's messages (worker engagement)
    )

    source_id: Mapped[int] = mapped_column(
        ForeignKey("telegram_sources.id", ondelete="CASCADE"), index=True, nullable=False
    )
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    thread_id: Mapped[int | None] = mapped_column(BigInteger)  # forum topic
    reply_to_message_id: Mapped[int | None] = mapped_column(BigInteger)

    author_id: Mapped[int | None] = mapped_column(BigInteger)
    author_name: Mapped[str | None] = mapped_column(String(128))
    author_is_bot: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    sent_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, index=True)
    edited_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    media_type: Mapped[str | None] = mapped_column(String(32))  # metadata only; files are never downloaded
    forward_from: Mapped[str | None] = mapped_column(String(256))
    forward_date: Mapped[datetime | None] = mapped_column(UTCDateTime())

    # Processing results
    processed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), index=True)
    category: Mapped[MessageCategory | None] = mapped_column(str_enum(MessageCategory))
    is_question: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    classified_by: Mapped[str | None] = mapped_column(String(128))
    topic_id: Mapped[int | None] = mapped_column(ForeignKey("community_topics.id", ondelete="SET NULL"), index=True)
    question_id: Mapped[int | None] = mapped_column(ForeignKey("community_questions.id", ondelete="SET NULL"), index=True)


class TelegramMessageVector(IdMixin, TimestampMixin, Base):
    __tablename__ = "telegram_message_vectors"

    message_id: Mapped[int] = mapped_column(
        ForeignKey("telegram_messages.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    model: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    dimensions: Mapped[int] = mapped_column(Integer, nullable=False)
    vector: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)


class TelegramBotState(IdMixin, TimestampMixin, Base):
    """Single row: who the bot is and where polling left off."""

    __tablename__ = "telegram_bot_state"

    bot_id: Mapped[int | None] = mapped_column(BigInteger)
    bot_username: Mapped[str | None] = mapped_column(String(128))
    bot_name: Mapped[str | None] = mapped_column(String(128))
    last_update_id: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(32), default="not_configured", nullable=False)
    last_sync_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_success_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_error: Mapped[str | None] = mapped_column(Text)
    last_error_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    total_ingested: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    ignored_updates: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class TelegramAccount(IdMixin, TimestampMixin, Base):
    """Telegram account mode (official client API / MTProto). One row.

    The API Hash and the session are secrets: they live in the encrypted secret store, never here.
    Login codes and 2FA passwords are never stored anywhere.
    """

    __tablename__ = "telegram_account"

    api_id: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), default="not_configured", server_default="not_configured", nullable=False)
    user_id: Mapped[int | None] = mapped_column(BigInteger)
    username: Mapped[str | None] = mapped_column(String(128))
    display_name: Mapped[str | None] = mapped_column(String(256))
    phone_hint: Mapped[str | None] = mapped_column(String(16))  # e.g. "••••42" only
    authorized_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_success_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_error: Mapped[str | None] = mapped_column(Text)
    last_error_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    requests_total: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)


class TgStrike(IdMixin, TimestampMixin, Base):
    """A broken group rule (app/telegram/moderation.py): who, which rule, by whom, and the mute it led to. Strikes count up the
    mute ladder (12 h, 24 h, permanent); an admin's /unmute reset clears them."""

    __tablename__ = "tg_strikes"
    __table_args__ = (Index("ix_tg_strikes_chat_user", "chat_id", "user_id"),)

    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    user_name: Mapped[str | None] = mapped_column(String(128))
    rule: Mapped[int | None] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    by: Mapped[str] = mapped_column(String(32), nullable=False)  # "admin:<telegram id>", "admin:anonymous" or "auto"
    muted_until: Mapped[datetime | None] = mapped_column(UTCDateTime())  # None with permanent=True: muted for good
    permanent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    cleared: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)  # reset by an admin: no longer counts
