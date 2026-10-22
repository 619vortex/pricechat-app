"""Invite Bot: consent-based Telegram invitations for the Price Chat community (dashboard › Invite Bot).

Several Telegram user accounts watch the groups they're approved for. They invite privately ONLY people who explicitly
asked (messaged the account, or wrote the opt-in keyword in an approved group), and post in groups only where the admins
agreed. Sessions are encrypted (APP_ENCRYPTION_KEY). For active members only the numeric id, public username, group and
last-activity time are stored — never message text, names or phone numbers.
"""

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, ForeignKey, Index, Integer, String, Text, UniqueConstraint, false, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin, UTCDateTime


class InviteSettings(IdMixin, TimestampMixin, Base):
    __tablename__ = "invite_settings"

    api_id: Mapped[int | None] = mapped_column(Integer)  # the API hash is in the encrypted secret store ("invite_api_hash")
    invite_link: Mapped[str] = mapped_column(String(200), default="https://t.me/PriceChat", nullable=False)
    dm_message: Mapped[str] = mapped_column(Text, nullable=False)
    group_message: Mapped[str] = mapped_column(Text, nullable=False)
    daily_limit: Mapped[int] = mapped_column(Integer, default=20, nullable=False)  # per account, all invitations
    group_post_interval_days: Mapped[int] = mapped_column(Integer, default=7, nullable=False)
    opt_in_keywords: Mapped[str] = mapped_column(String(300), default="!pricechat,#invite", nullable=False)
    opt_out_words: Mapped[str] = mapped_column(String(300), default="stop,unsubscribe,no thanks,no", nullable=False)
    active_days: Mapped[int] = mapped_column(Integer, default=14, nullable=False)
    retention_days: Mapped[int] = mapped_column(Integer, default=60, nullable=False)
    monitoring: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("1"), nullable=False)
    group_posts_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)  # master switch
    auto_send: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("1"), nullable=False)  # Auto: sends by itself; off: owner approves
    invite_direct_contacts: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)  # people who DM the account qualify without group activity
    dm_interval_seconds: Mapped[int] = mapped_column(Integer, default=300, server_default="300", nullable=False)  # gap between sends (10 s .. 1 h)


class InviteAccount(IdMixin, TimestampMixin, Base):
    __tablename__ = "invite_accounts"

    name: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    phone_hint: Mapped[str] = mapped_column(String(4), default="", nullable=False)
    tg_user_id: Mapped[int | None] = mapped_column(BigInteger)
    tg_username: Mapped[str | None] = mapped_column(String(64))
    session_enc: Mapped[str | None] = mapped_column(Text)  # encrypted Telethon string session; never returned or logged
    status: Mapped[str] = mapped_column(String(16), default="connected", nullable=False)  # connected | disconnected | error
    halted_until: Mapped[datetime | None] = mapped_column(UTCDateTime())
    halt_reason: Mapped[str | None] = mapped_column(String(200))
    last_poll_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_error: Mapped[str | None] = mapped_column(String(300))


class InviteGroup(IdMixin, TimestampMixin, Base):
    __tablename__ = "invite_groups"
    __table_args__ = (UniqueConstraint("account_id", "chat_id"),)

    account_id: Mapped[int] = mapped_column(ForeignKey("invite_accounts.id", ondelete="CASCADE"), index=True, nullable=False)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    username: Mapped[str | None] = mapped_column(String(64))
    allow_posts: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)  # admins agreed
    last_post_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_message_id: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)  # polling cursor


class InviteMember(Base):
    __tablename__ = "invite_members"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str | None] = mapped_column(String(64))
    group_id: Mapped[int | None] = mapped_column(ForeignKey("invite_groups.id", ondelete="SET NULL"))
    last_active_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True, nullable=False)


class InviteConsent(Base):
    """An explicit request for an invitation (or a recorded "no"). A revoked consent is never re-opened."""

    __tablename__ = "invite_consents"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("invite_accounts.id", ondelete="CASCADE"), index=True, nullable=False)
    username: Mapped[str | None] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(10), nullable=False)  # dm | keyword | none (an opt-out with no request)
    given_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    revoked_reason: Mapped[str | None] = mapped_column(String(100))


class InviteSend(IdMixin, Base):
    __tablename__ = "invite_sends"
    __table_args__ = (
        Index("ux_invite_one_dm_per_user", "user_id", unique=True, sqlite_where=text("method = 'dm' AND status = 'sent'"),
              postgresql_where=text("method = 'dm' AND status = 'sent'")),
        Index("ux_invite_one_personal_per_user", "user_id", unique=True, sqlite_where=text("method = 'personal'"), postgresql_where=text("method = 'personal'")),
        Index("ix_invite_sends_day", "account_id", "at"),
    )

    account_id: Mapped[int] = mapped_column(ForeignKey("invite_accounts.id", ondelete="CASCADE"), nullable=False)
    method: Mapped[str] = mapped_column(String(8), nullable=False)  # dm (sent by the bot) | personal (you sent it in Telegram) | group | skip
    user_id: Mapped[int | None] = mapped_column(BigInteger)
    group_id: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(8), nullable=False)  # sent | failed | opened (personal) | skipped
    error: Mapped[str | None] = mapped_column(String(300))
    at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    replied_at: Mapped[datetime | None] = mapped_column(UTCDateTime())  # they wrote back to the account afterwards
