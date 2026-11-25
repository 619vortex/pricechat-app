"""Terminal Intelligence Engine, Phase 0: the shared semantic gate's SHADOW record (app/intel/gate). One row per candidate per lane: what
the current rules decided and what the semantic gate would have decided, side by side. Written only while intel_shadow_enabled is on;
nothing reads it to decide a post."""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin, UTCDateTime


class IntelShadow(IdMixin, TimestampMixin, Base):
    __tablename__ = "intel_shadow"
    __table_args__ = (UniqueConstraint("lane", "ref", name="uq_intel_shadow_lane_ref"), Index("ix_intel_shadow_seen", "last_seen"))

    lane: Mapped[str] = mapped_column(String(16), nullable=False)  # trending | newsdesk
    ref: Mapped[str] = mapped_column(String(64), nullable=False)  # topic:<id> | event:<id>
    title: Mapped[str] = mapped_column(String(300), default="", nullable=False)
    rules_ok: Mapped[bool] = mapped_column(Boolean, nullable=False)  # the lane's current newsworthy decision
    semantic_ok: Mapped[bool] = mapped_column(Boolean, nullable=False)  # what the semantic gate would decide
    agree: Mapped[bool] = mapped_column(Boolean, nullable=False)
    tier: Mapped[str] = mapped_column(String(12), default="", nullable=False)  # major | significant | notable | noise
    source: Mapped[str] = mapped_column(String(16), default="rules", nullable=False)  # model | rules (no analysis: the rules verdict)
    seen: Mapped[int] = mapped_column(Integer, default=1, nullable=False)  # how many times this candidate was judged
    last_seen: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    verdict: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)  # the full SemanticVerdict
