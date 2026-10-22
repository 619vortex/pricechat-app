from typing import Any

from sqlalchemy import JSON, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin
from app.models.enums import AgentTask, PromptPlatform
from app.models.types import str_enum


class ConversationMessage(IdMixin, TimestampMixin, Base):
    """Every chat turn, persisted. Also serves as the agent's conversation memory."""

    __tablename__ = "conversation_messages"

    agent_id: Mapped[int] = mapped_column(
        ForeignKey("agent_configs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    conversation_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False)  # user | assistant
    content: Mapped[str] = mapped_column(Text, nullable=False)
    task: Mapped[AgentTask] = mapped_column(str_enum(AgentTask), default=AgentTask.CHAT, nullable=False)
    platform: Mapped[PromptPlatform] = mapped_column(
        str_enum(PromptPlatform), default=PromptPlatform.DASHBOARD, nullable=False
    )

    # Populated on assistant turns.
    provider: Mapped[str | None] = mapped_column(String(64))
    model: Mapped[str | None] = mapped_column(String(128))
    latency_ms: Mapped[float | None] = mapped_column(Float)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)
    memory_ids: Mapped[list[int]] = mapped_column(JSON, default=list, nullable=False)
    extra: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
