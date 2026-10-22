from sqlalchemy import JSON, ForeignKey, Integer, UniqueConstraint, true
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin
from app.models.enums import ActionMode, Platform, SocialAction
from app.models.types import str_enum


class SafetyPolicy(IdMixin, TimestampMixin, Base):
    """Global guardrails for any future outbound social action."""

    __tablename__ = "safety_policies"

    agent_id: Mapped[int] = mapped_column(
        ForeignKey("agent_configs.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    mode: Mapped[ActionMode] = mapped_column(
        str_enum(ActionMode), default=ActionMode.APPROVAL, nullable=False
    )
    kill_switch: Mapped[bool] = mapped_column(default=False, nullable=False)
    # Master switch for outbound actions. Stays off until real connectors exist.
    outbound_enabled: Mapped[bool] = mapped_column(default=False, nullable=False)
    max_actions_per_hour: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    max_actions_per_day: Mapped[int] = mapped_column(Integer, default=50, nullable=False)

    # Phase 3B/4A: SocialActionGuard settings
    platforms_enabled: Mapped[dict[str, bool]] = mapped_column(JSON, default=dict, nullable=False)
    blocked_targets: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)  # usernames or ids
    watchlist_only: Mapped[bool] = mapped_column(default=False, nullable=False)
    cooldown_minutes: Mapped[int] = mapped_column(Integer, default=30, nullable=False)  # per target
    duplicate_window_hours: Mapped[int] = mapped_column(Integer, default=24, nullable=False)
    # X operator: approved actions run the whole pipeline except the final platform request.
    dry_run: Mapped[bool] = mapped_column(default=True, server_default=true(), nullable=False)


class ActionLimit(IdMixin, TimestampMixin, Base):
    """Per-platform, per-action limits and mode overrides."""

    __tablename__ = "action_limits"
    __table_args__ = (UniqueConstraint("policy_id", "platform", "action"),)

    policy_id: Mapped[int] = mapped_column(
        ForeignKey("safety_policies.id", ondelete="CASCADE"), index=True, nullable=False
    )
    platform: Mapped[Platform] = mapped_column(str_enum(Platform), nullable=False)
    action: Mapped[SocialAction] = mapped_column(str_enum(SocialAction), nullable=False)
    enabled: Mapped[bool] = mapped_column(default=False, nullable=False)
    mode: Mapped[ActionMode | None] = mapped_column(str_enum(ActionMode))
    max_per_hour: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    max_per_day: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
