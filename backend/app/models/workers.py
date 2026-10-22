"""Kiwi Passport Worker System (Phase 1: Telegram invite workers).

Roles (WORKER / MANAGER) sit NEXT TO the XP rank: they never change XP, levels or rewards. Every grant and revoke is a row
(history kept). Jobs are assignments with a job_type so later job kinds reuse the same tables. Telegram identities are the
numeric user ids (usernames only help find them). Approvals are an append-only review trail.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin, UTCDateTime

ROLES = ("WORKER", "MANAGER")
JOB_TELEGRAM_INVITES = "TELEGRAM_INVITES"
JOB_TYPES = (JOB_TELEGRAM_INVITES,)


class WkSettings(Base):
    """Program settings (one row)."""

    __tablename__ = "wk_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC", server_default="UTC", nullable=False)  # the reporting day
    community_chat: Mapped[str] = mapped_column(String(64), default="@thepricechat", server_default="@thepricechat", nullable=False)
    community_chat_id: Mapped[int | None] = mapped_column(BigInteger)  # learned from Telegram
    default_target: Mapped[int] = mapped_column(Integer, default=5, server_default="5", nullable=False)
    join_window_hours: Mapped[int] = mapped_column(Integer, default=48, server_default="48", nullable=False)  # join before a submission still counts


class WkRole(IdMixin, TimestampMixin, Base):
    """A role grant. Active while revoked_at is NULL; revoking keeps the row (history)."""

    __tablename__ = "wk_roles"
    __table_args__ = (Index("ix_wk_roles_member_role", "member_id", "role"),)

    member_id: Mapped[int] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)  # WORKER | MANAGER
    granted_by: Mapped[str] = mapped_column(String(64), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    revoked_by: Mapped[str | None] = mapped_column(String(64))
    note: Mapped[str | None] = mapped_column(Text)


class WkAssignment(IdMixin, TimestampMixin, Base):
    """A job given to a worker. One active assignment per (worker, job_type); ending one keeps it (history)."""

    __tablename__ = "wk_assignments"
    __table_args__ = (Index("ix_wk_assignments_member", "member_id", "job_type"),)

    member_id: Mapped[int] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), nullable=False)
    job_type: Mapped[str] = mapped_column(String(32), nullable=False)
    daily_target: Mapped[int] = mapped_column(Integer, nullable=False)
    manager_id: Mapped[int | None] = mapped_column(ForeignKey("members.id", ondelete="SET NULL"))
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)  # job-specific settings (future jobs)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    ended_by: Mapped[str | None] = mapped_column(String(64))


class WkLink(IdMixin, TimestampMixin, Base):
    """A worker's personal invite: a Telegram chat invite link (bot is an admin) and/or the bot's /start wk_<code> deep link."""

    __tablename__ = "wk_links"

    member_id: Mapped[int] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), unique=True, nullable=False)
    code: Mapped[str] = mapped_column(String(24), unique=True, nullable=False)
    chat_link: Mapped[str | None] = mapped_column(String(255))
    chat_link_name: Mapped[str | None] = mapped_column(String(32), unique=True)  # what Telegram reports in chat_member updates
    chat_link_error: Mapped[str | None] = mapped_column(Text)


class WkJoin(IdMixin, TimestampMixin, Base):
    """Someone joined the community group, as the bot saw it. Idempotent on `event_key`."""

    __tablename__ = "wk_joins"
    __table_args__ = (Index("ix_wk_joins_user", "tg_user_id"), Index("ix_wk_joins_username", "username"))

    event_key: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)  # "msg:<chat>:<msg>:<user>" | "cm:<update id>"
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    tg_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    username: Mapped[str | None] = mapped_column(String(64))  # normalised (lower-case, no @)
    first_name: Mapped[str | None] = mapped_column(String(128))
    is_bot: Mapped[bool] = mapped_column(default=False, nullable=False)
    joined_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    invite_link_name: Mapped[str | None] = mapped_column(String(64))  # set when the join came through a named invite link


class WkSubmission(IdMixin, TimestampMixin, Base):
    """One invited person reported by (or attributed to) a worker. The username is unique across ALL workers."""

    __tablename__ = "wk_submissions"
    __table_args__ = (Index("ix_wk_submissions_worker", "worker_id", "created_at"), Index("ix_wk_submissions_tg", "tg_user_id"),
                      Index("ix_wk_submissions_status", "status"))

    worker_id: Mapped[int] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), nullable=False)
    assignment_id: Mapped[int | None] = mapped_column(ForeignKey("wk_assignments.id", ondelete="SET NULL"))
    job_type: Mapped[str] = mapped_column(String(32), nullable=False)
    username: Mapped[str | None] = mapped_column(String(64), unique=True)  # normalised; NULL for link joins without a username
    tg_user_id: Mapped[int | None] = mapped_column(BigInteger)
    source: Mapped[str] = mapped_column(String(16), nullable=False)  # form | chat_link | bot_start
    # SUBMITTED | VERIFIED | PENDING_REVIEW | APPROVED | REJECTED
    status: Mapped[str] = mapped_column(String(16), default="SUBMITTED", nullable=False)
    verified_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    joined_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    reject_reason: Mapped[str | None] = mapped_column(Text)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    report_id: Mapped[int | None] = mapped_column(ForeignKey("wk_reports.id", ondelete="SET NULL"))
    engagement: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # qualifying group messages since joining
    engagement_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    still_member: Mapped[bool | None] = mapped_column()
    member_checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())  # last "still in the group?" check


class WkCredit(IdMixin, TimestampMixin, Base):
    """A Telegram account credited to a worker: at most one worker, ever."""

    __tablename__ = "wk_credits"

    tg_user_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    submission_id: Mapped[int] = mapped_column(ForeignKey("wk_submissions.id", ondelete="CASCADE"), unique=True, nullable=False)
    worker_id: Mapped[int] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), nullable=False)


class WkReport(IdMixin, TimestampMixin, Base):
    """A worker's daily report, once submitted. One per (worker, job, day); a decision is final."""

    __tablename__ = "wk_reports"
    __table_args__ = (UniqueConstraint("worker_id", "job_type", "day", name="uq_wk_reports_worker_job_day"),
                      Index("ix_wk_reports_status", "status", "created_at"))

    worker_id: Mapped[int] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), nullable=False)
    assignment_id: Mapped[int | None] = mapped_column(ForeignKey("wk_assignments.id", ondelete="SET NULL"))
    job_type: Mapped[str] = mapped_column(String(32), nullable=False)
    day: Mapped[str] = mapped_column(String(10), nullable=False)  # YYYY-MM-DD in the program timezone
    target: Mapped[int] = mapped_column(Integer, nullable=False)
    verified_count: Mapped[int] = mapped_column(Integer, nullable=False)
    submission_ids: Mapped[list[int]] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="PENDING", nullable=False)  # PENDING | APPROVED | REJECTED
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    decided_by: Mapped[str | None] = mapped_column(String(96))
    reason: Mapped[str | None] = mapped_column(Text)


class WkReview(IdMixin, TimestampMixin, Base):
    """The approval trail: append-only (no route updates or deletes these rows)."""

    __tablename__ = "wk_reviews"
    __table_args__ = (Index("ix_wk_reviews_report", "report_id"),)

    report_id: Mapped[int] = mapped_column(ForeignKey("wk_reports.id", ondelete="CASCADE"), nullable=False)
    actor_kind: Mapped[str] = mapped_column(String(16), nullable=False)  # manager | admin | worker | system
    actor_member_id: Mapped[int | None] = mapped_column(ForeignKey("members.id", ondelete="SET NULL"))
    actor_name: Mapped[str] = mapped_column(String(96), nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False)  # SUBMITTED | APPROVED | REJECTED
    reason: Mapped[str | None] = mapped_column(Text)


class WkAudit(IdMixin, TimestampMixin, Base):
    """Admin actions on roles, assignments and targets (append-only)."""

    __tablename__ = "wk_audit"

    actor: Mapped[str] = mapped_column(String(96), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    member_id: Mapped[int | None] = mapped_column(ForeignKey("members.id", ondelete="SET NULL"))
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
