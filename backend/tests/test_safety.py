from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.activity import ActivityLog
from app.models.agent import AgentConfig
from app.models.enums import ActionMode, ActivityLevel, Platform, SocialAction
from app.models.safety import ActionLimit
from app.services.activity import ActivityLogger
from app.services.safety import EXECUTED_EVENT, SafetyGuard, get_or_create_policy
from tests.conftest import connect_x_user


def _enable(db: Session, agent_id: int, platform: Platform, action: SocialAction, **kw) -> ActionLimit:  # type: ignore[no-untyped-def]
    policy = get_or_create_policy(db, agent_id)
    policy.outbound_enabled = True
    limit = db.scalar(
        select(ActionLimit).where(
            ActionLimit.policy_id == policy.id,
            ActionLimit.platform == platform,
            ActionLimit.action == action,
        )
    )
    assert limit is not None
    limit.enabled = True
    for key, value in kw.items():
        setattr(limit, key, value)
    db.commit()
    return limit


def test_defaults_require_connection_and_approval(db: Session, agent_config: AgentConfig) -> None:
    decision = SafetyGuard(db, agent_config.id).check(Platform.X, SocialAction.POST)
    assert decision.allowed is False and decision.reason == "X is not connected"
    connect_x_user(db)
    decision = SafetyGuard(db, agent_config.id).check(Platform.X, SocialAction.POST)
    assert decision.allowed and decision.requires_approval  # supervised by default: never automatic
    telegram = SafetyGuard(db, agent_config.id).check(Platform.TELEGRAM, SocialAction.POST)
    assert telegram.allowed is False  # Telegram stays read-only


def test_kill_switch_overrides_everything(db: Session, agent_config: AgentConfig) -> None:
    connect_x_user(db)
    _enable(db, agent_config.id, Platform.X, SocialAction.POST)
    policy = get_or_create_policy(db, agent_config.id)
    policy.kill_switch = True
    db.commit()
    decision = SafetyGuard(db, agent_config.id).check(Platform.X, SocialAction.POST)
    assert (decision.allowed, decision.reason) == (False, "Social actions are paused")


def test_approval_mode_requires_approval(db: Session, agent_config: AgentConfig) -> None:
    connect_x_user(db)
    _enable(db, agent_config.id, Platform.X, SocialAction.REPLY)
    decision = SafetyGuard(db, agent_config.id).check(Platform.X, SocialAction.REPLY)
    assert decision.allowed and decision.requires_approval


def test_automatic_mode_is_locked(db: Session, agent_config: AgentConfig) -> None:
    connect_x_user(db)
    _enable(db, agent_config.id, Platform.X, SocialAction.LIKE, mode=ActionMode.AUTOMATIC)  # even if stored in the DB
    decision = SafetyGuard(db, agent_config.id).check(Platform.X, SocialAction.LIKE)
    assert decision.allowed and decision.requires_approval


def test_per_action_hourly_limit(db: Session, agent_config: AgentConfig) -> None:
    connect_x_user(db)
    _enable(db, agent_config.id, Platform.X, SocialAction.POST, max_per_hour=1)
    ActivityLogger(db, agent_config.id).log(
        EXECUTED_EVENT, "posted", platform=Platform.X, action=SocialAction.POST.value
    )
    decision = SafetyGuard(db, agent_config.id).check(Platform.X, SocialAction.POST)
    assert (decision.allowed, decision.reason) == (False, "Hourly limit for post reached (1/hour)")


def test_every_check_is_audited(db: Session, agent_config: AgentConfig) -> None:
    SafetyGuard(db, agent_config.id).check(Platform.TELEGRAM, SocialAction.POST)
    audit = db.scalars(select(ActivityLog).where(ActivityLog.level == ActivityLevel.AUDIT)).all()
    assert len(audit) == 1
    assert audit[0].event == "safety_check"
    assert audit[0].details["allowed"] is False
