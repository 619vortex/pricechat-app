"""Brand Manager (docs/BRAND_MANAGER.md): an AI social media manager, TikTok first.

- `BmBrand`: who the brand is + its content strategy (`strategy`, merged over app/brand_manager/defaults.py). The first
  one is Price Chat, seeded from the AI Brain's brand profile (app.models.brand.BrandProfile).
- `BmAutomation`: the START AUTO switch for one brand, and the exact reason when it is blocked.
- `BmAccount`: a connected TikTok account (authorized profile + granted scopes). OAuth tokens are NOT stored here: they
  live in the encrypted secret store (app/security/secrets.py).
- `BmVideo`: one piece of content from topic to analytics; `stages` records every pipeline stage's status.
- `BmJob`: the durable queue AI workers (the DGX Spark, a cloud fallback) claim jobs from, with leases and retries.
- `BmWorker`: a registered worker; only a hash of its token is stored.
- `BmEvent`: the plain-language log the dashboard shows. `BmMetric`: analytics readings from TikTok's API.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, Boolean, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, false
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin, UTCDateTime


class BmBrand(IdMixin, TimestampMixin, Base):
    __tablename__ = "bm_brands"

    name: Mapped[str] = mapped_column(String(80), nullable=False)
    website: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    audience: Mapped[str] = mapped_column(Text, default="", nullable=False)
    industry: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    language: Mapped[str] = mapped_column(String(32), default="English", nullable=False)
    personality: Mapped[str] = mapped_column(Text, default="", nullable=False)
    topics: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    avoid: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    colors: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    logo_url: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    examples: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)  # [{text, approved}]
    cta: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    competitors: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)  # inspiration only, never copied
    strategy: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)  # overrides of defaults.STRATEGY
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)


class BmAutomation(IdMixin, TimestampMixin, Base):
    __tablename__ = "bm_automation"
    __table_args__ = (UniqueConstraint("brand_id"),)

    brand_id: Mapped[int] = mapped_column(ForeignKey("bm_brands.id", ondelete="CASCADE"), nullable=False)
    running: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(String(12), default="stopped", nullable=False)  # running | stopped | blocked | error
    reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    stopped_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_tick_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_discovery_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_analytics_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    emergency_stop: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)
    upload_consent_at: Mapped[datetime | None] = mapped_column(UTCDateTime())  # the owner agreed to automatic draft uploads
    insights: Mapped[dict[str, Any] | None] = mapped_column(JSON)  # the performance analyzer's latest findings


class BmAccount(IdMixin, TimestampMixin, Base):
    __tablename__ = "bm_accounts"
    __table_args__ = (UniqueConstraint("brand_id", "platform"),)

    brand_id: Mapped[int] = mapped_column(ForeignKey("bm_brands.id", ondelete="CASCADE"), nullable=False)
    platform: Mapped[str] = mapped_column(String(16), nullable=False)  # tiktok
    open_id: Mapped[str | None] = mapped_column(String(80))
    username: Mapped[str | None] = mapped_column(String(80))
    display_name: Mapped[str | None] = mapped_column(String(120))
    avatar_url: Mapped[str | None] = mapped_column(String(600))
    profile: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    scopes: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="disconnected", nullable=False)  # connected | expired | revoked | error | disconnected
    access_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    refresh_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    creator_info: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    creator_info_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_error: Mapped[str | None] = mapped_column(Text)
    connected_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class BmVideo(IdMixin, TimestampMixin, Base):
    __tablename__ = "bm_videos"
    __table_args__ = (UniqueConstraint("brand_id", "topic_key"),)

    brand_id: Mapped[int] = mapped_column(ForeignKey("bm_brands.id", ondelete="CASCADE"), index=True, nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    template: Mapped[str] = mapped_column(String(24), nullable=False)
    topic: Mapped[str] = mapped_column(String(300), nullable=False)
    topic_key: Mapped[str] = mapped_column(String(64), nullable=False)  # duplicate detection
    score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    brief: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)  # facts, numbers, images, sources
    # queued | rendering | needs_review | ready | approved | publishing | sent_to_drafts | published | failed | cancelled
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True, nullable=False)
    stages: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)  # stage -> {status, at, note}
    script: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    video_key: Mapped[str | None] = mapped_column(String(200))  # object storage keys, never bytes in the database
    thumb_key: Mapped[str | None] = mapped_column(String(200))
    duration: Mapped[float | None] = mapped_column(Float)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    checksum: Mapped[str | None] = mapped_column(String(64))
    qc: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    render_info: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    slot_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), index=True)
    publish_mode: Mapped[str | None] = mapped_column(String(10))  # drafts | direct
    post_settings: Mapped[dict[str, Any] | None] = mapped_column(JSON)  # the owner's choices on TikTok's posting screen
    approved_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    publish_id: Mapped[str | None] = mapped_column(String(80), unique=True)
    publish_status: Mapped[str | None] = mapped_column(String(40))  # TikTok's own status, verbatim
    post_id: Mapped[str | None] = mapped_column(String(80))  # only once TikTok confirms a public post
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    metrics: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    metrics_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    generation: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    creative: Mapped[dict[str, Any] | None] = mapped_column(JSON)  # Content Studio v2: this video's style/voice/tone choices + scene edits
    # Content Studio v4: the media library ({"assets": [...]}: real footage/photos with rights records) and the editable timeline
    media: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    timeline: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class BmWorker(IdMixin, TimestampMixin, Base):
    __tablename__ = "bm_workers"
    __table_args__ = (UniqueConstraint("token_hash"),)

    name: Mapped[str] = mapped_column(String(60), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    cloud: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    info: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class BmJob(IdMixin, TimestampMixin, Base):
    """A unit of worker work. A worker claims one (lease), reports progress (extends the lease) and finishes or fails it.
    An expired lease goes back to the queue, so a crashed worker or a restarted server never loses a job."""

    __tablename__ = "bm_jobs"
    __table_args__ = (UniqueConstraint("idempotency_key"),)

    video_id: Mapped[int] = mapped_column(ForeignKey("bm_videos.id", ondelete="CASCADE"), index=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), default="render", nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(12), default="queued", index=True, nullable=False)  # queued | claimed | done | failed | cancelled
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    run_after: Mapped[datetime | None] = mapped_column(UTCDateTime())
    lease_until: Mapped[datetime | None] = mapped_column(UTCDateTime())
    worker_id: Mapped[int | None] = mapped_column(ForeignKey("bm_workers.id", ondelete="SET NULL"))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class BmEvent(IdMixin, Base):
    __tablename__ = "bm_events"

    brand_id: Mapped[int] = mapped_column(ForeignKey("bm_brands.id", ondelete="CASCADE"), index=True, nullable=False)
    video_id: Mapped[int | None] = mapped_column(ForeignKey("bm_videos.id", ondelete="CASCADE"), index=True)
    at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True, nullable=False)
    level: Mapped[str] = mapped_column(String(8), default="info", nullable=False)  # info | warn | error
    stage: Mapped[str | None] = mapped_column(String(20))
    message: Mapped[str] = mapped_column(Text, nullable=False)


class BmMetric(IdMixin, Base):
    """One analytics reading from TikTok: a video's counts (video_id set) or the account's follower count (video_id None)."""

    __tablename__ = "bm_metrics"

    brand_id: Mapped[int] = mapped_column(ForeignKey("bm_brands.id", ondelete="CASCADE"), index=True, nullable=False)
    video_id: Mapped[int | None] = mapped_column(ForeignKey("bm_videos.id", ondelete="CASCADE"), index=True)
    at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True, nullable=False)
    views: Mapped[int | None] = mapped_column(BigInteger)
    likes: Mapped[int | None] = mapped_column(BigInteger)
    comments: Mapped[int | None] = mapped_column(BigInteger)
    shares: Mapped[int | None] = mapped_column(BigInteger)
    followers: Mapped[int | None] = mapped_column(BigInteger)


class BmTrendDay(IdMixin, TimestampMixin, Base):
    """One trend word's headline count for one day (the trend scan; app/brand_manager/trends.py). The latest scan of the day
    overwrites the day's row, so each day keeps its own last-24h count: the baseline the next days are compared with."""
    __tablename__ = "bm_trend_days"
    __table_args__ = (UniqueConstraint("day", "term", name="uq_bm_trend_days_day_term"), Index("ix_bm_trend_days_term", "term"))

    day: Mapped[str] = mapped_column(String(10), nullable=False)  # YYYY-MM-DD (UTC)
    term: Mapped[str] = mapped_column(String(80), nullable=False)  # normalized key: "rate cut", "$sol", "genius act"
    label: Mapped[str] = mapped_column(String(80), nullable=False)  # as headlines write it: "Rate cut", "GENIUS Act"
    kind: Mapped[str] = mapped_column(String(12), default="headline", nullable=False)  # headline | search
    docs: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # distinct headlines in the last 24 h
    sources: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)  # outlets that used it
    examples: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)  # [{title, source, url, at}]
