from datetime import datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin, UTCDateTime
from app.models.enums import TaskStatus
from app.models.types import str_enum


class Task(IdMixin, TimestampMixin, Base):
    __tablename__ = "tasks"

    agent_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_configs.id", ondelete="SET NULL"), index=True
    )
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[TaskStatus] = mapped_column(
        str_enum(TaskStatus), default=TaskStatus.PENDING, nullable=False, index=True
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    scheduled_for: Mapped[datetime | None] = mapped_column(UTCDateTime())
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
