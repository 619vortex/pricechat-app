"""Price Chat community members: identity, linked platform accounts, Kiwi XP ledger, ranks, badges, entitlements.

Principles (see app/members/__init__.py): accounts are linked only by explicit sign-in on each platform; the only
OAuth tokens kept are a member's own X tokens (encrypted, MemberXToken) so actions they press can run as them;
every XP change is a ledger row with a source and a reason; reversals are new rows, never edits.
Portable column types only (no SQLite-specific features) so the schema moves to PostgreSQL unchanged.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, Boolean, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, false
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin, UTCDateTime


class Member(IdMixin, TimestampMixin, Base):
    """A Price Chat member. created_at is the join date."""

    __tablename__ = "members"

    public_id: Mapped[str] = mapped_column(String(36), unique=True, nullable=False)  # opaque id for URLs/APIs
    username: Mapped[str] = mapped_column(String(32), nullable=False)
    username_key: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)  # lower-case, for uniqueness
    username_chosen: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)
    display_name: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    display_name_changed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())  # rename cooldown (Kiwi rank reward)
    passport_accent: Mapped[str | None] = mapped_column(String(16))  # cosmetic Passport frame (Kiwi rank reward)
    avatar_url: Mapped[str | None] = mapped_column(String(512))  # optional custom avatar later; default is the rank Kiwi
    status: Mapped[str] = mapped_column(String(16), default="active", server_default="active", nullable=False)  # active | banned
    xp_suspended: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)
    leaderboard_opt_out: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)
    xp_total: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)  # reputation XP (ledger sum)
    bonus_xp: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)  # premium/partner XP, never ranks
    rank_level: Mapped[int] = mapped_column(Integer, default=1, server_default="1", nullable=False)
    legacy_points: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)  # weighted, capped Combot history
    rank_score: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)  # what the rank ladder uses
    last_active_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), index=True)
    public_profile: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)  # /u/<username>
    # Pack Giveaway pulls shown in the Pokémon Portfolio (opt-in): a separate virtual section, never real holdings
    giveaway_in_portfolio: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)
    # Play Market: extra copies pulled from a pack are sold instantly for Play USDT (opt-in)
    auto_sell_dupes: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)
    missions_seen_at: Mapped[datetime | None] = mapped_column(UTCDateTime())  # for "new mission" notifications
    # passwordless sign-in: set ONLY after the member typed a code sent to it (never from a provider or a guess)
    email: Mapped[str | None] = mapped_column(String(254))  # normalised (trimmed, lower-case); unique via ux_members_email
    email_verified_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


Index("ux_members_email", Member.email, unique=True)


class MemberEmailCode(IdMixin, TimestampMixin, Base):
    """A 6-digit sign-in code sent by email. Only keyed hashes are stored (of the address, the code, the browser and
    the IP). Single use, short-lived, a few attempts. Rows also serve as the rate-limit record (per address, per IP)."""

    __tablename__ = "member_email_codes"
    __table_args__ = (Index("ix_member_email_codes_email", "email_hash", "created_at"), Index("ix_member_email_codes_ip", "ip_hash", "created_at"),
                      Index("ix_member_email_codes_created", "created_at"))

    email_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    purpose: Mapped[str] = mapped_column(String(8), nullable=False)  # login | link (add an email to the signed-in member)
    member_id: Mapped[int | None] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"))  # for link
    browser_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    ip_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(12), default="pending", nullable=False)  # pending | used | replaced | locked | unsent
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class MemberSession(IdMixin, TimestampMixin, Base):
    """A signed-in browser. Only a SHA-256 hash of the cookie value is stored."""

    __tablename__ = "member_sessions"

    member_id: Mapped[int] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), index=True, nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    kind: Mapped[str | None] = mapped_column(String(16))  # "desktop" for the Kiwi VPN app; empty for a browser


class LinkedIdentity(IdMixin, TimestampMixin, Base):
    """A platform account the member proved they own by signing in to it. One per platform per member."""

    __tablename__ = "linked_identities"
    __table_args__ = (UniqueConstraint("platform", "platform_user_id"), UniqueConstraint("member_id", "platform"))

    member_id: Mapped[int] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), index=True, nullable=False)
    platform: Mapped[str] = mapped_column(String(16), nullable=False)  # google | x | telegram
    platform_user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    username: Mapped[str | None] = mapped_column(String(128))
    display_name: Mapped[str | None] = mapped_column(String(128))
    verified_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="active", server_default="active", nullable=False)


class MemberAuthRequest(IdMixin, TimestampMixin, Base):
    """A sign-in or link attempt in progress (OAuth state, Telegram start code). Short-lived, single use."""

    __tablename__ = "member_auth_requests"

    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    purpose: Mapped[str] = mapped_column(String(8), nullable=False)  # login | link
    state_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)  # hash of the state / start code
    browser_hash: Mapped[str] = mapped_column(String(64), nullable=False)  # binds the attempt to the browser that started it
    member_id: Mapped[int | None] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"))  # for link
    secret: Mapped[str | None] = mapped_column(Text)  # encrypted PKCE verifier / OAuth 1.0a request-token secret
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)  # pending | verified | done | failed
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)  # verified identity until claimed
    error: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, index=True)


class Rank(IdMixin, TimestampMixin, Base):
    """A Kiwi rank. Art is layered on the canonical Kiwi: a treatment plus an optional overlay image."""

    __tablename__ = "ranks"

    level: Mapped[int] = mapped_column(Integer, unique=True, nullable=False)
    key: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    min_xp: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    art: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)  # treatment, overlay, placeholder


class XPRule(IdMixin, TimestampMixin, Base):
    """How much one verified event is worth, and the limits that stop farming."""

    __tablename__ = "xp_rules"

    event_type: Mapped[str] = mapped_column(String(48), unique=True, nullable=False)
    label: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    xp: Mapped[int] = mapped_column(Integer, nullable=False)
    daily_cap: Mapped[int | None] = mapped_column(Integer)  # max XP per member per UTC day from this rule
    cooldown_seconds: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_per_member: Mapped[int | None] = mapped_column(Integer)  # e.g. 1 for one-time rewards
    min_member_age_hours: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    verification_source: Mapped[str] = mapped_column(String(16), nullable=False)  # website | telegram | x | google | moderator | admin | partner
    availability: Mapped[str] = mapped_column(String(16), default="available", nullable=False)  # available | unavailable | future
    availability_note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    category: Mapped[str] = mapped_column(String(16), default="reputation", nullable=False)  # reputation | premium


class ReputationEvent(IdMixin, TimestampMixin, Base):
    """The XP ledger. Rows are never edited except to mark a reversal; a reversal is its own negative row."""

    __tablename__ = "reputation_events"
    __table_args__ = (
        UniqueConstraint("member_id", "event_type", "source_ref"),  # the same activity never pays twice
        Index("ix_reputation_events_member_day", "member_id", "day"),
    )

    member_id: Mapped[int] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), index=True, nullable=False)
    event_type: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    xp: Mapped[int] = mapped_column(Integer, nullable=False)
    category: Mapped[str] = mapped_column(String(16), default="reputation", nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)  # website | telegram | x | google | moderator | admin
    source_ref: Mapped[str] = mapped_column(String(128), nullable=False)  # dedupe key, e.g. "tg-day:2026-10-04"
    reason: Mapped[str] = mapped_column(String(256), nullable=False)  # shown to the member
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    day: Mapped[str] = mapped_column(String(10), nullable=False)  # UTC date of occurred_at, for daily caps
    actor: Mapped[str] = mapped_column(String(64), default="system", nullable=False)
    reverses_id: Mapped[int | None] = mapped_column(ForeignKey("reputation_events.id", ondelete="SET NULL"))
    reversed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class XPClaim(IdMixin, TimestampMixin, Base):
    """One-time rewards per external identity, kept as a salted hash so they survive account deletion
    (deleting and re-creating an account cannot farm "linked Telegram" again) without keeping the identity."""

    __tablename__ = "xp_claims"

    claim_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)


class Badge(IdMixin, TimestampMixin, Base):
    __tablename__ = "badges"

    key: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    kind: Mapped[str] = mapped_column(String(16), default="manual", nullable=False)  # auto | manual | partner
    criteria: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class MemberBadge(IdMixin, TimestampMixin, Base):
    __tablename__ = "member_badges"
    __table_args__ = (UniqueConstraint("member_id", "badge_id"),)

    member_id: Mapped[int] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), index=True, nullable=False)
    badge_id: Mapped[int] = mapped_column(ForeignKey("badges.id", ondelete="CASCADE"), nullable=False)
    awarded_by: Mapped[str] = mapped_column(String(64), default="system", nullable=False)
    reason: Mapped[str] = mapped_column(String(256), default="", nullable=False)


class Entitlement(IdMixin, TimestampMixin, Base):
    """Something a rank (or, later, a purchase) unlocks. Defined now; nothing is gated or created automatically yet."""

    __tablename__ = "entitlements"

    key: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    required_rank: Mapped[int | None] = mapped_column(Integer)  # None = not rank-based (manual / future purchase)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class MemberEntitlement(IdMixin, TimestampMixin, Base):
    """A grant outside the rank ladder (manual now; partner/purchase later)."""

    __tablename__ = "member_entitlements"
    __table_args__ = (UniqueConstraint("member_id", "entitlement_id"),)

    member_id: Mapped[int] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), index=True, nullable=False)
    entitlement_id: Mapped[int] = mapped_column(ForeignKey("entitlements.id", ondelete="CASCADE"), nullable=False)
    granted_by: Mapped[str] = mapped_column(String(64), default="admin", nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class ModerationFlag(IdMixin, TimestampMixin, Base):
    """Something a person should look at. Flags never change XP or status by themselves."""

    __tablename__ = "moderation_flags"

    member_id: Mapped[int] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), index=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)  # rapid_activity | repeated_messages | cap_pressure | manual
    reason: Mapped[str] = mapped_column(String(256), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="open", nullable=False, index=True)  # open | resolved | dismissed
    created_by: Mapped[str] = mapped_column(String(64), default="system", nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    resolved_by: Mapped[str | None] = mapped_column(String(64))
    resolution: Mapped[str | None] = mapped_column(Text)


class MemberAuditLog(IdMixin, TimestampMixin, Base):
    """Every admin action on members, plus member self-service changes. Kept after deletion without identity."""

    __tablename__ = "member_audit_logs"

    member_id: Mapped[int | None] = mapped_column(ForeignKey("members.id", ondelete="SET NULL"), index=True)
    actor: Mapped[str] = mapped_column(String(64), nullable=False)  # dashboard | member | system
    action: Mapped[str] = mapped_column(String(48), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class MemberBurnerWallet(IdMixin, TimestampMixin, Base):
    """A burner wallet made in the member's browser. Only the ENCRYPTED recovery phrase is stored: it was encrypted in the
    browser with the member's own password (PBKDF2-SHA256 + AES-GCM), so Price Chat can never read it or recover it."""

    __tablename__ = "member_burner_wallets"
    __table_args__ = (UniqueConstraint("member_id", name="uq_member_burner_wallets_member"),)

    member_id: Mapped[int] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), nullable=False)
    address: Mapped[str] = mapped_column(String(42), nullable=False)  # 0x... EVM address (public)
    vault: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)  # {v, kdf, iterations, salt, iv, ct}: ciphertext only


class Mission(IdMixin, TimestampMixin, Base):
    """Something a member can do now. Generated from real content or standing (daily / weekly / link).
    `verification` is what Price Chat can actually prove: verified | verified_click | self_reported | unverifiable."""

    __tablename__ = "missions"

    key: Mapped[str] = mapped_column(String(96), unique=True, nullable=False)  # dedupe, e.g. "yt-open:<video id>"
    kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    cadence: Mapped[str] = mapped_column(String(16), nullable=False)  # once | daily | weekly
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    platform: Mapped[str] = mapped_column(String(16), nullable=False)  # youtube | x | telegram | website
    xp: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    verification: Mapped[str] = mapped_column(String(16), nullable=False)
    verification_method: Mapped[str] = mapped_column(Text, default="", nullable=False)
    requires_platform: Mapped[str | None] = mapped_column(String(16))  # a linked account needed to take part
    target: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)  # url, external ids, preview text
    goal: Mapped[int] = mapped_column(Integer, default=1, nullable=False)  # e.g. 3 active days for a weekly mission
    priority: Mapped[int] = mapped_column(Integer, default=50, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False, index=True)  # active | paused | disabled | expired
    created_by: Mapped[str] = mapped_column(String(64), default="system", nullable=False)
    starts_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), index=True)


class MemberStreak(IdMixin, TimestampMixin, Base):
    """Daily consistency streak: consecutive UTC days on which the member completed at least one task."""

    __tablename__ = "member_streaks"

    member_id: Mapped[int] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), unique=True, nullable=False)
    current: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    best: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_day: Mapped[str | None] = mapped_column(String(10))  # YYYY-MM-DD (UTC) of the last counted day


class MemberPackOpen(IdMixin, Base):
    """A free virtual pack opened in the Pack Giveaway (just for fun: no money, no real cards). `slot` = 1..N that day."""

    __tablename__ = "member_pack_opens"
    __table_args__ = (UniqueConstraint("member_id", "day", "slot"),)

    member_id: Mapped[int] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), index=True, nullable=False)
    pack_key: Mapped[str] = mapped_column(String(16), nullable=False)
    day: Mapped[str] = mapped_column(String(10), nullable=False)
    slot: Mapped[int] = mapped_column(Integer, nullable=False)
    opened_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    cards: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    bonus: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)  # a Level-up pack


class MemberBinderCard(IdMixin, Base):
    """A virtual card in the member's Pack Giveaway binder."""

    __tablename__ = "member_binder_cards"
    __table_args__ = (UniqueConstraint("member_id", "card_id"),)

    member_id: Mapped[int] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), index=True, nullable=False)
    card_id: Mapped[int] = mapped_column(ForeignKey("pokemon_cards.id", ondelete="CASCADE"), nullable=False)
    count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    tier: Mapped[str] = mapped_column(String(10), nullable=False)
    pack_key: Mapped[str] = mapped_column(String(16), nullable=False)
    first_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class MemberMission(IdMixin, TimestampMixin, Base):
    """A member's progress on one mission in one period (the day, the ISO week, or "once")."""

    __tablename__ = "member_missions"
    __table_args__ = (UniqueConstraint("member_id", "mission_id", "period"),)

    member_id: Mapped[int] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), index=True, nullable=False)
    mission_id: Mapped[int] = mapped_column(ForeignKey("missions.id", ondelete="CASCADE"), index=True, nullable=False)
    period: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="in_progress", nullable=False)  # in_progress | pending | completed | rejected
    progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    xp_awarded: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    event_id: Mapped[int | None] = mapped_column(ForeignKey("reputation_events.id", ondelete="SET NULL"))
    opened_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    claimed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class MemberAction(IdMixin, TimestampMixin, Base):
    """A click / open on the Price Chat website (not a platform action). At most one per target per day."""

    __tablename__ = "member_actions"
    __table_args__ = (UniqueConstraint("member_id", "kind", "target", "day"),)

    member_id: Mapped[int] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), index=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)  # open | like_intent | reply_intent | repost_intent | share | subscribe_intent
    platform: Mapped[str] = mapped_column(String(16), nullable=False)
    target: Mapped[str] = mapped_column(String(128), nullable=False)
    day: Mapped[str] = mapped_column(String(10), nullable=False)


class MemberNotification(IdMixin, TimestampMixin, Base):
    __tablename__ = "member_notifications"

    member_id: Mapped[int] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), index=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)  # rank_up | badge | xp | mission_verified | mission_rejected | disconnected
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    body: Mapped[str] = mapped_column(Text, default="", nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class CombotRecord(IdMixin, TimestampMixin, Base):
    """Historical Telegram reputation from Combot, kept SEPARATE from the Price Chat XP ledger.
    Keyed by the numeric Telegram user id (usernames are display metadata only and are never used to match)."""

    __tablename__ = "combot_reputation"

    telegram_user_id: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    username: Mapped[str | None] = mapped_column(String(128))  # as Combot last saw it; may be outdated
    name: Mapped[str | None] = mapped_column(String(256))
    combot_xp: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    combot_messages: Mapped[int | None] = mapped_column(Integer)
    combot_rep: Mapped[int | None] = mapped_column(Integer)
    combot_rank: Mapped[str | None] = mapped_column(String(64))
    combot_active_days: Mapped[int | None] = mapped_column(Integer)
    source_timestamp: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)  # when the data was true at Combot
    imported_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    source_type: Mapped[str] = mapped_column(String(16), nullable=False)  # api | leaderboard | csv | json
    import_id: Mapped[int | None] = mapped_column(ForeignKey("combot_imports.id", ondelete="SET NULL"))


class CombotImport(IdMixin, TimestampMixin, Base):
    """One import batch (API sync or uploaded file). Files are previewed first and applied only on confirmation."""

    __tablename__ = "combot_imports"

    source_type: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="preview", nullable=False)  # preview | applied | discarded | failed
    filename: Mapped[str | None] = mapped_column(String(256))
    rows: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)  # validated rows awaiting confirmation
    stats: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(64), default="dashboard", nullable=False)
    applied_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class ReputationSettings(IdMixin, TimestampMixin, Base):
    """Single row: how the Kiwi Rank Score is built, and the state of the Combot source."""

    __tablename__ = "reputation_settings"

    price_chat_weight: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    combot_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)  # contributes to rank
    combot_weight: Mapped[float] = mapped_column(Float, default=0.25, nullable=False)
    legacy_cap: Mapped[int] = mapped_column(Integer, default=500, nullable=False)  # max legacy points; 0 = no cap
    combot_mode: Mapped[str] = mapped_column(String(16), default="off", nullable=False)  # api | manual | off
    combot_chat_id: Mapped[str | None] = mapped_column(String(32))
    last_sync_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_success_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_error: Mapped[str | None] = mapped_column(Text)
    last_records: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    telegram_daily_cap: Mapped[int] = mapped_column(Integer, default=10, server_default="10", nullable=False)  # all Telegram XP per day
    watch_seconds: Mapped[int] = mapped_column(Integer, default=30, server_default="30", nullable=False)  # Watch & Earn play time


class MemberXToken(IdMixin, TimestampMixin, Base):
    """A member's own X user token (OAuth 2.0 user context), so a reply or repost they press in Price Chat is sent as
    them. Encrypted at rest with the app's secret key, never sent to the browser or logged, never used for anything
    the member didn't press, and deleted when they disconnect X. Not the @pricechat brand connector."""

    __tablename__ = "member_x_tokens"

    member_id: Mapped[int] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), unique=True, nullable=False)
    x_user_id: Mapped[str] = mapped_column(String(64), nullable=False)  # must equal the member's linked X identity
    access_token: Mapped[str] = mapped_column(Text, nullable=False)  # encrypted
    refresh_token: Mapped[str | None] = mapped_column(Text)  # encrypted
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    scopes: Mapped[str] = mapped_column(String(256), default="", nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)  # active | reconnect
    last_error: Mapped[str | None] = mapped_column(String(256))  # plain-language reason, for the admin view
    last_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


# --- growth: referrals and program settings ----------------------------------------------------------------------


class ReferralCode(IdMixin, TimestampMixin, Base):
    """A member's own invite code (one per member, never reused). `clicks` counts visits to the invite link: a visit
    never earns anything."""

    __tablename__ = "member_referral_codes"

    member_id: Mapped[int] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), unique=True, nullable=False)
    code: Mapped[str] = mapped_column(String(24), unique=True, nullable=False)
    clicks: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)


class Referral(IdMixin, TimestampMixin, Base):
    """Who invited whom, by internal member ids (never usernames). Immutable history: a row is created when the invited
    person creates their Passport ("joined"), and only ever moves on to "qualified" (both rewards paid through the XP
    ledger) or "rejected". A member can be referred once (unique referred_id). Deleting a member keeps the row with the
    id cleared, so counts and one-time rewards stay correct."""

    __tablename__ = "member_referrals"

    referrer_id: Mapped[int | None] = mapped_column(ForeignKey("members.id", ondelete="SET NULL"), index=True)
    referred_id: Mapped[int | None] = mapped_column(ForeignKey("members.id", ondelete="SET NULL"), unique=True)
    code: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="joined", nullable=False, index=True)  # joined | qualified | rejected
    reason: Mapped[str | None] = mapped_column(String(64))  # why rejected
    browser_hash: Mapped[str | None] = mapped_column(String(64))  # hash of the sign-in browser binding, to spot patterns
    qualified_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    qualifying_event_type: Mapped[str | None] = mapped_column(String(48))
    referrer_event_id: Mapped[int | None] = mapped_column(Integer)  # ledger rows (reputation_events.id)
    welcome_event_id: Mapped[int | None] = mapped_column(Integer)
    referrer_xp: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    welcome_xp: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)


class CrewReferral(IdMixin, TimestampMixin, Base):
    """Invite your crew: a Telegram account that pressed Start on a member's personal bot invite (t.me/<bot>?start=crew_<code>).
    One row per Telegram account EVER (unique tg_user_id): attribution can't be reassigned and join/leave can't pay twice.
    pending (opened the invite) -> joined (seen in the Price Chat Telegram) -> verified (still there after the check period:
    XP paid) | rejected (reason: self / existing_member / bot / referrer_gone / already_counted)."""

    __tablename__ = "member_crew_referrals"

    referrer_id: Mapped[int | None] = mapped_column(ForeignKey("members.id", ondelete="SET NULL"), index=True)
    tg_user_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    code: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(12), default="pending", nullable=False, index=True)
    reason: Mapped[str | None] = mapped_column(String(48))
    joined_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    verified_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    xp: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    event_id: Mapped[int | None] = mapped_column(Integer)


class ProgramSettings(IdMixin, TimestampMixin, Base):
    """Single row: growth settings set in the dashboard (XP amounts of referral rewards live in their XP rules)."""

    __tablename__ = "member_program_settings"

    # Invite your crew (Telegram): the community a friend must join, how long they must stay, the bonus threshold
    crew_chat: Mapped[str] = mapped_column(String(64), default="@thepricechat", server_default="@thepricechat", nullable=False)
    crew_hold_hours: Mapped[int] = mapped_column(Integer, default=24, server_default="24", nullable=False)
    crew_bonus_at: Mapped[int] = mapped_column(Integer, default=5, server_default="5", nullable=False)

    referrals_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # ledger event types that count as the invited member's first meaningful, verified action
    referral_qualifying_events: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    referral_min_age_hours: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    referral_daily_limit: Mapped[int] = mapped_column(Integer, default=5, nullable=False)  # paid referrals per referrer per UTC day
    updates_channel: Mapped[str | None] = mapped_column(String(64))  # e.g. "@Pricechatupdates"
    channel_join_xp: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    discussion_xp: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    discussion_window_days: Mapped[int] = mapped_column(Integer, default=3, nullable=False)  # how long an announcement stays a mission
    discussion_daily_limit: Mapped[int] = mapped_column(Integer, default=3, nullable=False)  # paid discussion missions per member per day
    leaderboard_periods: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    # what Price Chat's bot can see of the channel (checked, never assumed)
    channel_id: Mapped[int | None] = mapped_column(BigInteger)
    channel_title: Mapped[str | None] = mapped_column(String(128))
    channel_linked_chat_id: Mapped[int | None] = mapped_column(BigInteger)
    channel_bot_can_verify: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    channel_checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    channel_error: Mapped[str | None] = mapped_column(Text)


# --- Play Market (virtual): giveaway cards sold / bought for Play USDT, a test currency with no value -------------------------


class PlayWallet(IdMixin, Base):
    """A member's Play USDT balance, in cents. Play USDT is NOT real USDT: it can't be bought, deposited or withdrawn; it
    only comes from selling virtual giveaway cards and only buys virtual giveaway cards. Every change is a PlayLedger row."""

    __tablename__ = "play_wallets"

    member_id: Mapped[int] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), unique=True, nullable=False)
    balance_cents: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0", nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)  # compare-and-swap
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class PlayLedger(IdMixin, Base):
    """Every Play USDT change (append-only). `ref` makes each entry idempotent per member and kind."""

    __tablename__ = "play_ledger"
    __table_args__ = (UniqueConstraint("member_id", "kind", "ref"), Index("ix_play_ledger_member_at", "member_id", "at"))

    member_id: Mapped[int] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), nullable=False)
    delta_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # instant_sell | auto_sell | sale | purchase
    ref: Mapped[str] = mapped_column(String(64), nullable=False)
    card_id: Mapped[int | None] = mapped_column(ForeignKey("pokemon_cards.id", ondelete="SET NULL"))
    note: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class PlayListing(IdMixin, Base):
    """One virtual card listed for Play USDT. The copy leaves the seller's binder while listed (back on cancel)."""

    __tablename__ = "play_listings"
    __table_args__ = (Index("ix_play_listings_status_at", "status", "created_at"), Index("ix_play_listings_seller", "seller_id", "status"))

    seller_id: Mapped[int] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), nullable=False)
    card_id: Mapped[int] = mapped_column(ForeignKey("pokemon_cards.id", ondelete="CASCADE"), nullable=False)
    tier: Mapped[str] = mapped_column(String(10), nullable=False)
    pack_key: Mapped[str] = mapped_column(String(16), nullable=False)
    price_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(10), default="open", nullable=False)  # open | sold | cancelled
    buyer_id: Mapped[int | None] = mapped_column(ForeignKey("members.id", ondelete="SET NULL"))
    version: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)  # compare-and-swap
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class PlayBid(IdMixin, Base):
    """A standing bid for one virtual copy of a card, in Play USDT. The amount is held from the bidder's wallet while open
    (released on cancel); it fills when someone sells or lists that card at or below it."""

    __tablename__ = "play_bids"
    __table_args__ = (Index("ix_play_bids_card_status", "card_id", "status", "price_cents"), Index("ix_play_bids_bidder", "bidder_id", "status"))

    bidder_id: Mapped[int] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), nullable=False)
    card_id: Mapped[int] = mapped_column(ForeignKey("pokemon_cards.id", ondelete="CASCADE"), nullable=False)
    price_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(10), default="open", nullable=False)  # open | filled | cancelled
    seller_id: Mapped[int | None] = mapped_column(ForeignKey("members.id", ondelete="SET NULL"))
    version: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
