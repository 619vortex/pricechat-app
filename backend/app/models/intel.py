"""Terminal Intelligence Engine channel (app/intel): the PC worker's scoped token record, the leased job queue and the shared AI ledger.

Additive only (migration 0121_intel_channel). Nothing here publishes; with settings.intel_enabled False no row is ever written."""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, Float, Index, Integer, String, Text, UniqueConstraint, false, true
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin, UTCDateTime


class IntelWorker(IdMixin, TimestampMixin, Base):
    """The owner's PC, authenticated by a pci_ token whose SHA-256 is configured (INTEL_WORKER_TOKEN_SHA256). The token itself is never
    stored. The row holds only the heartbeat (last_seen_at, models) and an off switch; rotating the configured hash retires the old token."""

    __tablename__ = "intel_workers"
    __table_args__ = (UniqueConstraint("token_hash", name="uq_intel_workers_token_hash"),)

    name: Mapped[str] = mapped_column(String(60), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true(), nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    info: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)  # models available, worker version, busy


class IntelJob(IdMixin, TimestampMixin, Base):
    """One intel.analyze / intel.embed job. route 'pc' = offered to the PC (claim with a lease and a single-use nonce), 'cloud' = Railway
    answers it itself. A PC result must be HMAC-signed over (id, nonce, canonical result) and arrive inside its lease; it is validated
    against app.intel.schema before it is stored. shadow jobs (breaking stories answered by the cloud) only record the PC's answer."""

    __tablename__ = "intel_jobs"
    __table_args__ = (Index("ix_intel_jobs_status", "status", "route", "priority"), Index("ix_intel_jobs_key", "key"))

    kind: Mapped[str] = mapped_column(String(20), nullable=False)  # intel.analyze | intel.embed
    priority: Mapped[str] = mapped_column(String(10), nullable=False)  # breaking | slot | bulk
    route: Mapped[str] = mapped_column(String(8), nullable=False)  # pc | cloud
    shadow: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)
    key: Mapped[str | None] = mapped_column(String(120))  # the caller's identity for the item (dedup / joins), optional
    status: Mapped[str] = mapped_column(String(10), default="queued", nullable=False)  # queued | leased | done | failed | expired
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    nonce: Mapped[str | None] = mapped_column(String(64))  # issued at claim, single use (cleared by result / fail / expiry)
    worker_id: Mapped[int | None] = mapped_column(Integer)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    offer_until: Mapped[datetime | None] = mapped_column(UTCDateTime())  # unclaimed by then: Railway answers (None = no claim deadline)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())  # the job's last moment at all (bulk: 6 h)
    lease_until: Mapped[datetime | None] = mapped_column(UTCDateTime())
    claimed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    result_by: Mapped[str | None] = mapped_column(String(8))  # pc | cloud | rules
    error: Mapped[str | None] = mapped_column(Text)
    latency_ms: Mapped[int | None] = mapped_column(Integer)


class AiUsage(IdMixin, Base):
    """The shared AI spend ledger: one row per paid call (or a reservation made before it). The daily cap (intel_daily_usd_cap) is
    checked against today's sum INCLUDING open reservations, so two concurrent callers can't both slip under it."""

    __tablename__ = "ai_usage"
    __table_args__ = (Index("ix_ai_usage_day", "day"),)

    day: Mapped[str] = mapped_column(String(10), nullable=False)  # YYYY-MM-DD (UTC)
    at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    model: Mapped[str] = mapped_column(String(60), nullable=False)
    purpose: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(10), default="done", nullable=False)  # reserved | done | failed
    tokens_in: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cache_read: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
