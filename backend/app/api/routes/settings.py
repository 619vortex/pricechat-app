from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.api.deps import DbSession
from app.api.schemas import (
    ActionLimitOut,
    ActionLimitUpdate,
    SafetyCheckIn,
    SafetyCheckOut,
    SafetyPolicyOut,
    SafetyPolicyUpdate,
    SafetySettingsOut,
)
from app.models.enums import ActivityLevel
from app.models.safety import ActionLimit
from app.services.activity import ActivityLogger
from app.services.agent_service import get_agent_config
from app.services.safety import SafetyGuard, get_or_create_policy, list_limits

router = APIRouter(prefix="/settings/safety", tags=["settings"])

OUTBOUND_NOTE = (
    "Outbound social actions are not implemented in Phase 1. "
    "These settings are stored and enforced by SafetyGuard once connectors exist."
)


def _settings_out(db: DbSession, agent_id: int) -> SafetySettingsOut:
    policy = get_or_create_policy(db, agent_id)
    return SafetySettingsOut(
        policy=SafetyPolicyOut.model_validate(policy),
        limits=[ActionLimitOut.model_validate(limit) for limit in list_limits(db, policy.id)],
        outbound_note=OUTBOUND_NOTE,
    )


@router.get("", response_model=SafetySettingsOut)
def get_safety(db: DbSession) -> SafetySettingsOut:
    return _settings_out(db, get_agent_config(db).id)


@router.patch("", response_model=SafetySettingsOut)
def update_safety(payload: SafetyPolicyUpdate, db: DbSession) -> SafetySettingsOut:
    agent_id = get_agent_config(db).id
    policy = get_or_create_policy(db, agent_id)
    changes = payload.model_dump(exclude_none=True)
    for key, value in changes.items():
        setattr(policy, key, value)
    db.commit()

    if "kill_switch" in changes:
        state = "ENGAGED" if policy.kill_switch else "released"
        ActivityLogger(db, agent_id).log(
            "kill_switch", f"Kill switch {state}", level=ActivityLevel.AUDIT, actor="dashboard"
        )
    if changes.keys() - {"kill_switch"}:
        ActivityLogger(db, agent_id).log(
            "safety_policy_updated",
            f"Safety policy updated: {', '.join(sorted(changes))}",
            level=ActivityLevel.AUDIT,
            actor="dashboard",
            details={k: str(v) for k, v in changes.items()},
        )
    return _settings_out(db, agent_id)


@router.patch("/limits/{limit_id}", response_model=ActionLimitOut)
def update_limit(limit_id: int, payload: ActionLimitUpdate, db: DbSession) -> ActionLimitOut:
    agent_id = get_agent_config(db).id
    policy = get_or_create_policy(db, agent_id)
    limit = db.scalar(
        select(ActionLimit).where(ActionLimit.id == limit_id, ActionLimit.policy_id == policy.id)
    )
    if limit is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Action limit not found")

    # exclude_unset (not exclude_none) so mode=null clears the override.
    changes = payload.model_dump(exclude_unset=True)
    for key, value in changes.items():
        if value is None and key != "mode":
            continue
        setattr(limit, key, value)
    db.commit()
    ActivityLogger(db, agent_id).log(
        "action_limit_updated",
        f"{limit.platform.value}.{limit.action.value} limits updated",
        level=ActivityLevel.AUDIT,
        actor="dashboard",
        platform=limit.platform,
        action=limit.action.value,
        details={k: str(v) for k, v in changes.items()},
    )
    return ActionLimitOut.model_validate(limit)


@router.post("/check", response_model=SafetyCheckOut)
def check_action(payload: SafetyCheckIn, db: DbSession) -> SafetyCheckOut:
    """Dry-run: would this action be allowed right now? Nothing is executed."""
    decision = SafetyGuard(db, get_agent_config(db).id).check(payload.platform, payload.action)
    return SafetyCheckOut(
        allowed=decision.allowed, requires_approval=decision.requires_approval, reason=decision.reason
    )
