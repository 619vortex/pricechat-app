"""Price Chat Predictions (migration 0125): free daily and weekly market calls, voted in Telegram or on the Passport, settled from
Hyperliquid's daily close. XP only, never money: prediction XP is its own ledger category ("predictor", app/predictions/rules.py),
which never counts towards the Kiwi rank or any reward with monetary or trading value.

pred_questions: one call (daily direction or weekly range). `slot` makes the scheduler idempotent ("daily:BTC:2026-11-28").
pred_votes:     one row per voter per call (UNIQUE question + voter_key). voter_key is "m:<member id>" for a Passport member and
                "tg:<telegram user id>" for a Telegram member who hasn't linked a Passport yet (re-keyed to "m:" when they link).
pred_tg_users:  what the bot knows about a Telegram voter: group membership check, private-message opt-ins, linked member.
"""

from datetime import date, datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, Boolean, Date, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin, UTCDateTime


class PredQuestion(IdMixin, TimestampMixin, Base):
    __tablename__ = "pred_questions"
    __table_args__ = (UniqueConstraint("slot"), Index("ix_pred_questions_status", "status", "settles_at"))

    slot: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)  # daily | weekly
    qtype: Mapped[str] = mapped_column(String(16), nullable=False)  # direction | range
    asset: Mapped[str] = mapped_column(String(16), nullable=False)  # BTC (Hyperliquid perpetual)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    options: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)  # [{key, label, lo?, hi?}]
    ref_price: Mapped[float | None] = mapped_column(Float)
    ref_day: Mapped[date | None] = mapped_column(Date)  # the daily candle whose close is the reference
    settle_day: Mapped[date] = mapped_column(Date, nullable=False)  # the daily candle whose close settles the call
    opens_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    locks_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    settles_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)  # when that candle closes
    status: Mapped[str] = mapped_column(String(12), default="scheduled", nullable=False)  # scheduled|open|locked|settled|void|held
    outcome: Mapped[str | None] = mapped_column(String(16))  # an option key, or "tie"
    settle_price: Mapped[float | None] = mapped_column(Float)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)  # reads, candle, source, who resolved
    void_reason: Mapped[str] = mapped_column(String(300), default="", server_default="", nullable=False)
    settled_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    tg_chat: Mapped[str] = mapped_column(String(64), default="", server_default="", nullable=False)
    tg_message_id: Mapped[int | None] = mapped_column(BigInteger)
    tg_shown: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)  # the vote count last shown
    tg_edited_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    reminded_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_by: Mapped[str] = mapped_column(String(40), default="system", server_default="system", nullable=False)
    audit: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)


class PredVote(IdMixin, TimestampMixin, Base):
    __tablename__ = "pred_votes"
    __table_args__ = (UniqueConstraint("question_id", "voter_key"), Index("ix_pred_votes_member", "member_id"),
                      Index("ix_pred_votes_tg", "tg_user_id"))

    question_id: Mapped[int] = mapped_column(ForeignKey("pred_questions.id", ondelete="CASCADE"), nullable=False, index=True)
    voter_key: Mapped[str] = mapped_column(String(40), nullable=False)
    member_id: Mapped[int | None] = mapped_column(ForeignKey("members.id", ondelete="SET NULL"))
    tg_user_id: Mapped[int | None] = mapped_column(BigInteger)
    option: Mapped[str] = mapped_column(String(16), nullable=False)
    via: Mapped[str] = mapped_column(String(8), nullable=False)  # telegram | web
    voted_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)  # the first vote (server time)
    changed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    changes: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    status: Mapped[str] = mapped_column(String(12), default="counted", server_default="counted", nullable=False)  # counted|duplicate
    credited_at: Mapped[datetime | None] = mapped_column(UTCDateTime())  # XP written to the ledger (linked members only)


class PredTgUser(IdMixin, TimestampMixin, Base):
    __tablename__ = "pred_tg_users"
    __table_args__ = (UniqueConstraint("tg_user_id"),)

    tg_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    username: Mapped[str | None] = mapped_column(String(64))
    first_name: Mapped[str] = mapped_column(String(64), default="", server_default="", nullable=False)
    is_bot: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0", nullable=False)
    in_group: Mapped[bool | None] = mapped_column(Boolean)
    group_checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    dm_ok: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0", nullable=False)  # has started the bot privately
    reminders: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0", nullable=False)  # opt-in
    results_dm: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0", nullable=False)  # opt-in
    note: Mapped[str] = mapped_column(Text, default="", server_default="", nullable=False)
