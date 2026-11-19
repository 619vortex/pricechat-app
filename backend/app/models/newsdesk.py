"""The News Desk: Price Chat's automatic X news account (app/newsdesk). Settings, what it read, and every post it made or would make."""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, Boolean, Float, Index, Integer, LargeBinary, String, Text, UniqueConstraint, false, true
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


class NdEvent(IdMixin, TimestampMixin, Base):
    """One editorial decision, shared by X and Telegram: a story (or a measured market move) the engine chose, released once, then delivered
    by each platform on its own. Each platform keeps its own status, so one failing never stops or rewrites the other."""

    __tablename__ = "nd_events"
    __table_args__ = (Index("ix_nd_events_status", "status", "released_at"),)

    key: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)  # story:<link> | move:<symbol>:<window>:<dir>:<hour> | liq:<coin>:<hour>
    kind: Mapped[str] = mapped_column(String(10), nullable=False)  # news | market | update
    parent_id: Mapped[int | None] = mapped_column(Integer)  # an update: the event it follows up
    title: Mapped[str] = mapped_column(String(300), nullable=False)  # the outlet's own headline, or the market headline
    headline: Mapped[str] = mapped_column(String(300), nullable=False)  # as posted on both platforms
    summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    topic: Mapped[str] = mapped_column(String(16), default="", nullable=False)
    label: Mapped[str] = mapped_column(String(24), default="", nullable=False)
    evidence: Mapped[str] = mapped_column(String(16), default="", nullable=False)
    category: Mapped[str] = mapped_column(String(16), default="viral", nullable=False)
    virality: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    parts: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)  # how the score was made
    sources: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)  # [{name, url, at}]
    facts: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)  # tickers, flag, figures, what the checks compare against
    # candidate: waiting for a slot | released: both platforms deliver it | missed: never got a slot | superseded: folded into another event
    status: Mapped[str] = mapped_column(String(12), default="candidate", nullable=False)
    slot: Mapped[str] = mapped_column(String(10), default="", nullable=False)  # regular | extra (an extraordinary story outside the daily plan)
    first_seen: Mapped[datetime | None] = mapped_column(UTCDateTime())
    released_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    why: Mapped[str] = mapped_column(Text, default="", nullable=False)  # why it was released, or why not yet
    # each platform's own delivery: pending | queued | dry_run | published | delivered | uncertain | failed | skipped | missed
    x_status: Mapped[str] = mapped_column(String(12), default="pending", nullable=False)
    x_post_id: Mapped[int | None] = mapped_column(Integer)
    x_note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    tg_status: Mapped[str] = mapped_column(String(12), default="pending", nullable=False)
    tg_post_id: Mapped[int | None] = mapped_column(Integer)
    tg_message_id: Mapped[int | None] = mapped_column(BigInteger)
    tg_note: Mapped[str] = mapped_column(Text, default="", nullable=False)


class NdRenderJob(IdMixin, TimestampMixin, Base):
    """A News Desk picture background for the owner's GPU PC (app/newsdesk/render_queue.py). The PC claims it over outbound HTTPS with
    its Brand Manager worker token, renders a text-free 16:9 image and uploads it; the backend draws every headline, number and credit
    itself. One job per event and art direction (idempotency key); an expired lease goes back to the queue; when the PC is offline the
    post uses the desk's own drawn visuals instead."""

    __tablename__ = "nd_render_jobs"
    __table_args__ = (UniqueConstraint("idempotency_key"), Index("ix_nd_render_jobs_status", "status", "run_after"))

    event_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(12), default="queued", nullable=False)  # queued | claimed | done | failed | cancelled
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # breaking first
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    run_after: Mapped[datetime | None] = mapped_column(UTCDateTime())
    lease_until: Mapped[datetime | None] = mapped_column(UTCDateTime())
    deadline: Mapped[datetime | None] = mapped_column(UTCDateTime())  # past this the post goes without it
    worker_id: Mapped[int | None] = mapped_column(Integer)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)  # prompt, negative, size, seed, model, art direction
    image: Mapped[bytes | None] = mapped_column(LargeBinary)  # JPEG, text-free
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)  # model, seconds, quality checks
    error: Mapped[str | None] = mapped_column(Text)
    claimed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class NdValidation(IdMixin, TimestampMixin, Base):
    """The validation round's record (owner, 2026-11-19), kept in production so it never depends on anyone watching: one row per News Desk
    candidate (its editorial decision and the extraordinary-news shadow verdict), per published picture (its automated audit) and the
    report made at 30 candidates. `flag` holds what needs the owner's eyes; `alerted` once the owner was told."""

    __tablename__ = "nd_validation"
    __table_args__ = (UniqueConstraint("kind", "ref", name="uq_nd_validation_kind_ref"), Index("ix_nd_validation_kind", "kind", "created_at"))

    kind: Mapped[str] = mapped_column(String(12), nullable=False)  # candidate | image | report
    ref: Mapped[str] = mapped_column(String(64), nullable=False)  # event id | telegram:<message id> / x:<post id> | "30"
    flag: Mapped[str] = mapped_column(Text, default="", server_default="", nullable=False)
    alerted: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class NdFeedback(IdMixin, TimestampMixin, Base):
    """The owner's one-click verdict on a story (owner, 2026-11-19): great | boring | wrong_image | missed, against the actual event or topic
    and a snapshot of what the desk decided and published when it was given, so it stays true after the story moves on. One row per target
    and verdict: a second click removes it."""

    __tablename__ = "nd_feedback"
    __table_args__ = (UniqueConstraint("target", "verdict", name="uq_nd_feedback_target_verdict"), Index("ix_nd_feedback_created", "created_at"))

    target: Mapped[str] = mapped_column(String(40), nullable=False)  # event:<NdEvent id> | topic:<TtTopic id>
    verdict: Mapped[str] = mapped_column(String(16), nullable=False)
    note: Mapped[str] = mapped_column(Text, default="", server_default="", nullable=False)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class NdStoryline(IdMixin, TimestampMixin, Base):
    """Persistent story memory (owner, 2026-11-19): one real-world story across Telegram, X (News Desk and Trending), released events and X
    Intelligence leads, with the largest facts already told (deaths, sums, moves, hard outcomes) so a follow-up can be judged on what is
    actually new. It outlives the 7-day posting history the repeat gate reads."""

    __tablename__ = "nd_storylines"
    __table_args__ = (Index("ix_nd_storylines_last", "last_at"),)

    title: Mapped[str] = mapped_column(String(300), nullable=False)  # the first headline told (or seen, for a lead)
    family: Mapped[str] = mapped_column(String(24), default="news", nullable=False)
    anchors: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    regions: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    states: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    told: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)  # {deaths, money, pct, outcomes, titles}
    sightings: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)  # [{lane, ref, at, title, status}] newest last
    first_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    last_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    told_at: Mapped[datetime | None] = mapped_column(UTCDateTime())  # first time we published it
    lead_at: Mapped[datetime | None] = mapped_column(UTCDateTime())  # first X Intelligence lead (unverified)
    lead_by: Mapped[str] = mapped_column(String(80), default="", server_default="", nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())  # first reliable outlet or official source after the lead
