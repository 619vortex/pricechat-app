from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UTCDateTime, utcnow


class ModelRoute(Base):
    """Dashboard override of which model handles a task (defaults live in app.ai.routing.TASKS)."""

    __tablename__ = "model_routes"

    task: Mapped[str] = mapped_column(String(64), primary_key=True)
    tier: Mapped[str | None] = mapped_column(String(16))
    model: Mapped[str | None] = mapped_column(String(128))
    temperature: Mapped[float | None] = mapped_column(Float)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow, nullable=False)


class ScheduledJob(Base):
    """Persistent state of a background job, shown on the dashboard."""

    __tablename__ = "scheduled_jobs"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    last_status: Mapped[str] = mapped_column(String(16), default="never", nullable=False)  # never|running|success|failed|skipped
    last_run_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_duration_ms: Mapped[float | None] = mapped_column(Float)
    last_error: Mapped[str | None] = mapped_column(Text)
    last_result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    next_run_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    run_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failure_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
