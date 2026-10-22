from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin
from app.models.enums import AgentStatus
from app.models.types import str_enum


class AgentConfig(IdMixin, TimestampMixin, Base):
    __tablename__ = "agent_configs"

    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    # Core role statement; the brand-specific detail lives in BrandProfile.
    identity: Mapped[str] = mapped_column(Text, nullable=False)
    behavior_rules: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[AgentStatus] = mapped_column(
        str_enum(AgentStatus), default=AgentStatus.IDLE, nullable=False
    )
    # Optional per-agent overrides; fall back to settings when empty.
    model_name: Mapped[str | None] = mapped_column(String(128))
    temperature: Mapped[float | None]


class AgentGoal(IdMixin, TimestampMixin, Base):
    __tablename__ = "agent_goals"

    agent_id: Mapped[int] = mapped_column(
        ForeignKey("agent_configs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=3, nullable=False)  # 1 = highest, 5 = lowest
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
