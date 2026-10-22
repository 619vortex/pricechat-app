"""Kiwi Passport rank rewards: what each rank unlocks, the beginner content, the Knight trading reward and Royal VIP claims,
their audit trail, and Kiwi Arena runs.

A reward is unlocked by the member's CURRENT rank (computed from the ledger), never stored as a flag, so members who
already passed a rank qualify automatically and XP is never recalculated for it. Claims are rows with a status and a
unique (member, reward) pair: a reward can't be claimed twice. Nothing here moves money.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, ForeignKey, Index, Integer, String, Text, UniqueConstraint, false, true
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin, UTCDateTime

CLAIM_STATUSES = ("PENDING", "APPROVED", "FULFILLED", "REJECTED", "REVOKED")


class RankReward(IdMixin, TimestampMixin, Base):
    """One reward per rank. Seeded once (members/rewards.py); after that the dashboard owns title, text and on/off."""

    __tablename__ = "rank_rewards"

    rank_key: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)  # none | content | profile | earn | arena | trading_credit | vip | tba
    title: Mapped[str] = mapped_column(String(96), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true(), nullable=False)


class RewardSettings(Base):
    """One row: the Knight reward's funding/terms, the Royal VIP invite and Kiwi Arena limits. Changed only from the dashboard."""

    __tablename__ = "reward_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Knight Kiwi trading reward: never claimable until an admin activates it with written terms
    knight_status: Mapped[str] = mapped_column(String(16), default="NOT_CONFIGURED", server_default="NOT_CONFIGURED", nullable=False)  # NOT_CONFIGURED | ACTIVE | PAUSED
    knight_amount_usd: Mapped[int] = mapped_column(Integer, default=10, server_default="10", nullable=False)
    knight_terms: Mapped[str] = mapped_column(Text, default="", nullable=False)  # conditions, shown to members
    knight_regions: Mapped[str] = mapped_column(Text, default="", nullable=False)  # geographic restrictions, shown to members
    knight_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    knight_budget: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)  # max approved+fulfilled claims (0 = none)
    # Royal Kiwi WhatsApp VIP: the invite link, encrypted; only shown to approved members
    vip_invite_enc: Mapped[str | None] = mapped_column(Text)
    vip_description: Mapped[str] = mapped_column(Text, default="", nullable=False)  # who's in it: only what the admin confirms
    # Kiwi Arena XP (every limit enforced on the server)
    arena_kills_per_xp: Mapped[int] = mapped_column(Integer, default=10, server_default="10", nullable=False)
    arena_run_xp_cap: Mapped[int] = mapped_column(Integer, default=10, server_default="10", nullable=False)
    arena_daily_xp_cap: Mapped[int] = mapped_column(Integer, default=20, server_default="20", nullable=False)
    arena_min_run_seconds: Mapped[int] = mapped_column(Integer, default=45, server_default="45", nullable=False)
    arena_runs_per_hour: Mapped[int] = mapped_column(Integer, default=12, server_default="12", nullable=False)


class BeginnerContent(IdMixin, TimestampMixin, Base):
    """Kiwi Chick reward: an educational item the admin writes. Members only ever see published items."""

    __tablename__ = "beginner_content"

    title: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    video_url: Mapped[str | None] = mapped_column(String(500))
    thumbnail_url: Mapped[str | None] = mapped_column(String(500))
    guide_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    position: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    published: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)


class RewardClaim(IdMixin, TimestampMixin, Base):
    """A member's claim of a reward that needs a person (Knight trading reward, Royal VIP). One per member and reward."""

    __tablename__ = "reward_claims"
    __table_args__ = (UniqueConstraint("member_id", "reward_key", name="ux_reward_claims_member_reward"),
                      Index("ix_reward_claims_status", "reward_key", "status"))

    public_id: Mapped[str] = mapped_column(String(36), unique=True, nullable=False)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), nullable=False)
    reward_key: Mapped[str] = mapped_column(String(32), nullable=False)  # knight | royal
    status: Mapped[str] = mapped_column(String(12), default="PENDING", nullable=False)
    rank_level: Mapped[int] = mapped_column(Integer, nullable=False)  # at the time of the claim
    wallet: Mapped[str | None] = mapped_column(String(42))  # verified Hyperliquid wallet at the time of the claim (knight)
    member_note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    admin_note: Mapped[str] = mapped_column(Text, default="", nullable=False)  # internal
    fulfillment_ref: Mapped[str | None] = mapped_column(String(200))  # e.g. the transfer reference an admin made by hand
    reviewed_by: Mapped[str | None] = mapped_column(String(64))
    reviewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class RewardAudit(IdMixin, TimestampMixin, Base):
    """Append-only: every claim, decision, setting and content change."""

    __tablename__ = "reward_audit"

    claim_id: Mapped[int | None] = mapped_column(ForeignKey("reward_claims.id", ondelete="SET NULL"), index=True)
    member_id: Mapped[int | None] = mapped_column(ForeignKey("members.id", ondelete="SET NULL"), index=True)
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(48), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class ArenaRun(IdMixin, TimestampMixin, Base):
    """One Kiwi Arena run. The server times it and counts kills only from heartbeats it accepted: the client never says how
    much XP it earned."""

    __tablename__ = "arena_runs"
    __table_args__ = (Index("ix_arena_runs_member_started", "member_id", "started_at"),)

    public_id: Mapped[str] = mapped_column(String(36), unique=True, nullable=False)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), nullable=False)
    seed: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(12), default="ACTIVE", nullable=False)  # ACTIVE | FINISHED | ABANDONED
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    last_beat_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    beats: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    active_beats: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # beats with player input
    kills_reported: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    kills_accepted: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    inputs_reported: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # the client's running input count
    best_wave: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    xp_awarded: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    flags: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)  # why kills were discarded
    hero_class: Mapped[str] = mapped_column(String(12), default="knight", server_default="knight", nullable=False)


class ArenaHero(IdMixin, TimestampMixin, Base):
    """A member's saved Kiwi Arena hero, one per class (Knight / Ranger / Mage). It levels up from server-counted kills and
    waves only (never from anything the game reports on its own), and its level gives a small permanent bonus in the game."""

    __tablename__ = "arena_heroes"
    __table_args__ = (UniqueConstraint("member_id", "hero_class", name="ux_arena_heroes_member_class"),)

    member_id: Mapped[int] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), nullable=False)
    hero_class: Mapped[str] = mapped_column(String(12), nullable=False)
    level: Mapped[int] = mapped_column(Integer, default=1, server_default="1", nullable=False)
    xp: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)  # toward the next level
    runs: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    kills: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    best_wave: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
