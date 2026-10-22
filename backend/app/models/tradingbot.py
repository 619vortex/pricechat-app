"""VIP Trading Bot access funnel: settings, verified wallets, signing nonces, applications, the audit trail and funnel
analytics. Nothing here moves money or trades: wallets are read through Hyperliquid's public read-only API."""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, Boolean, Float, ForeignKey, Index, Integer, String, Text, false, true
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin, UTCDateTime


class TbSettings(IdMixin, TimestampMixin, Base):
    """One row. Changed only from Admin → Trading Bot Access."""

    __tablename__ = "tb_settings"

    # Closed until the owner has worked through the launch checklist: only admins can use the funnel while closed.
    public_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)
    min_rank_level: Mapped[int] = mapped_column(Integer, default=1, server_default="1", nullable=False)  # 1 = open to every member (0072)
    min_deposit_usd: Mapped[float] = mapped_column(Float, default=0.0, server_default="0", nullable=False)
    # accepted codes, comma-separated; the first is shown to members (the link's code), the rest are what Hyperliquid records
    referral_code: Mapped[str] = mapped_column(String(64), default="BANTER,KLGXFMNI", server_default="BANTER", nullable=False)
    referral_url: Mapped[str] = mapped_column(String(200), default="https://app.hyperliquid.xyz/join/BANTER", nullable=False)
    # the VIP group: its @username (public group with "Approve new members" on) or its chat id (-100...)
    telegram_group_id: Mapped[str | None] = mapped_column(String(33), default="@botpricechat")
    auto_verify: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true(), nullable=False)
    manual_approval: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true(), nullable=False)
    applications_per_day: Mapped[int] = mapped_column(Integer, default=50, server_default="50", nullable=False)  # all members
    invite_ttl_hours: Mapped[int] = mapped_column(Integer, default=48, server_default="48", nullable=False)
    review_rules: Mapped[str] = mapped_column(Text, default="", server_default="", nullable=False)  # shown to reviewers
    bot_username: Mapped[str] = mapped_column(String(64), default="cryptopricechatbot", server_default="cryptopricechatbot", nullable=False)
    bot_wallet: Mapped[str | None] = mapped_column(String(42))  # the bot's Hyperliquid account: its public PnL track record


class TbWallet(IdMixin, TimestampMixin, Base):
    """A wallet a member proved they own (signed nonce), and what Hyperliquid's records say about it."""

    __tablename__ = "tb_wallets"

    member_id: Mapped[int] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), unique=True, nullable=False)
    address: Mapped[str] = mapped_column(String(42), unique=True, nullable=False)  # lower-case 0x...
    verified_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    # verified | not_referred | unverified (the API couldn't say) | pending (not checked yet)
    referral_status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    referral_code_seen: Mapped[str | None] = mapped_column(String(64))
    # verified | insufficient | unverified (inflows the API can't classify; a person must look) | pending
    deposit_status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    deposit_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)  # bridge deposits only (ledger "deposit")
    other_inflow_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)  # transfers in: never auto-counted
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)  # what the API returned (summary)


class TbNonce(IdMixin, TimestampMixin, Base):
    """A single-use sign-in-with-wallet challenge, bound to one member and one address, valid for 10 minutes."""

    __tablename__ = "tb_nonces"
    __table_args__ = (Index("ix_tb_nonces_member", "member_id"),)

    member_id: Mapped[int | None] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"))  # None: a "sign in with wallet" nonce
    address: Mapped[str] = mapped_column(String(42), nullable=False)
    nonce: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class TbApplication(IdMixin, TimestampMixin, Base):
    """A request for private access. The member-facing status is PENDING_APPROVAL → APPROVED | REJECTED, then REVOKED."""

    __tablename__ = "tb_applications"
    __table_args__ = (Index("ix_tb_applications_status", "status"), Index("ix_tb_applications_member", "member_id"))

    public_id: Mapped[str] = mapped_column(String(36), unique=True, nullable=False)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    rank_level: Mapped[int] = mapped_column(Integer, nullable=False)  # snapshots at the moment of applying
    xp: Mapped[int] = mapped_column(Integer, nullable=False)
    wallet: Mapped[str] = mapped_column(String(42), nullable=False)
    referral_status: Mapped[str] = mapped_column(String(16), nullable=False)
    deposit_status: Mapped[str] = mapped_column(String(16), nullable=False)
    deposit_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    telegram_user_id: Mapped[int | None] = mapped_column(BigInteger)
    telegram_username: Mapped[str | None] = mapped_column(String(64))
    needs_manual_review: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    review_notes: Mapped[str] = mapped_column(Text, default="", nullable=False)
    reviewed_by: Mapped[str | None] = mapped_column(String(64))
    reviewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    # The join-request invite (encrypted at rest; given only to this member while APPROVED, never in a list or export)
    invite_link_enc: Mapped[str | None] = mapped_column(Text)
    invite_created_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    invite_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    invite_error: Mapped[str | None] = mapped_column(Text)
    join_requested_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    joined_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    left_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class TbAudit(IdMixin, TimestampMixin, Base):
    __tablename__ = "tb_audit"
    __table_args__ = (Index("ix_tb_audit_app", "application_id"),)

    application_id: Mapped[int | None] = mapped_column(ForeignKey("tb_applications.id", ondelete="CASCADE"))
    member_id: Mapped[int | None] = mapped_column(ForeignKey("members.id", ondelete="SET NULL"))
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(48), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class TbEvent(IdMixin, TimestampMixin, Base):
    """Funnel analytics. One row per tracked event; `visitor` is a salted hash so views can be counted once per visitor."""

    __tablename__ = "tb_events"
    __table_args__ = (Index("ix_tb_events_kind_at", "kind", "created_at"),)

    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    member_id: Mapped[int | None] = mapped_column(ForeignKey("members.id", ondelete="SET NULL"))
    visitor: Mapped[str | None] = mapped_column(String(64))


class TbAffiliateSnapshot(IdMixin, TimestampMixin, Base):
    """Hourly copy of what Hyperliquid reports for our affiliate (referrer) wallet, so growth can be charted: Hyperliquid itself
    only returns current cumulative numbers. `data` holds the totals and each referred user's cumulative volume and fees."""

    __tablename__ = "tb_affiliate_snapshots"
    __table_args__ = (Index("ix_tb_affiliate_snapshots_wallet_created", "wallet", "created_at"),)

    wallet: Mapped[str] = mapped_column(String(42), nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
