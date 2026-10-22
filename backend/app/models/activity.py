from typing import Any

from sqlalchemy import JSON, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin
from app.models.enums import ActivityLevel, Platform
from app.models.types import str_enum


class ActivityLog(IdMixin, TimestampMixin, Base):
    """Append-only activity and audit trail. level=audit entries record safety decisions."""

    __tablename__ = "activity_logs"

    agent_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_configs.id", ondelete="SET NULL"), index=True
    )
    level: Mapped[ActivityLevel] = mapped_column(
        str_enum(ActivityLevel), default=ActivityLevel.INFO, nullable=False, index=True
    )
    event: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    actor: Mapped[str] = mapped_column(String(64), default="system", nullable=False)
    platform: Mapped[Platform | None] = mapped_column(str_enum(Platform))
    action: Mapped[str | None] = mapped_column(String(32), index=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
