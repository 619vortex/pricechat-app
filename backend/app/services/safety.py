"""Safety policy storage and the legacy SafetyGuard facade.

All social write decisions are made by app.social.guard.SocialActionGuard.
SafetyGuard is kept as a thin compatibility wrapper so there is one code path.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import ActionMode, Platform, SocialAction
from app.models.safety import ActionLimit, SafetyPolicy

# Event name connectors log after a successful external action; rate limits count these.
EXECUTED_EVENT = "social_action_executed"

# Supervised X operation: every X write needs human approval, under conservative ceilings (not targets).
# (per hour, per day). Dry run is on by default, so nothing reaches X until the user turns it off.
X_DEFAULT_LIMITS: dict[SocialAction, tuple[int, int]] = {
    SocialAction.POST: (1, 3),
    SocialAction.REPLY: (3, 10),
    SocialAction.LIKE: (5, 20),
    SocialAction.REPOST: (2, 5),
    SocialAction.FOLLOW: (2, 5),
}

DEFAULT_ACTIONS: dict[Platform, list[SocialAction]] = {
    Platform.X: [SocialAction.POST, SocialAction.REPLY, SocialAction.LIKE, SocialAction.REPOST, SocialAction.FOLLOW],
    Platform.TELEGRAM: [SocialAction.POST, SocialAction.REPLY],
}


@dataclass(frozen=True, slots=True)
class SafetyDecision:
    allowed: bool
    requires_approval: bool
    reason: str


def get_or_create_policy(db: Session, agent_id: int) -> SafetyPolicy:
    policy = db.scalar(select(SafetyPolicy).where(SafetyPolicy.agent_id == agent_id))
    if policy is None:
        policy = SafetyPolicy(agent_id=agent_id, outbound_enabled=True, dry_run=True)
        db.add(policy)
        db.flush()
    ensure_default_limits(db, policy)
    return policy


def ensure_default_limits(db: Session, policy: SafetyPolicy) -> None:
    """Add any missing (platform, action) rows. X actions start as approval-required with conservative
    ceilings; everything else starts disabled. Existing rows are never changed."""
    have = {(l.platform, l.action) for l in db.scalars(select(ActionLimit).where(ActionLimit.policy_id == policy.id))}
    added = False
    for platform, actions in DEFAULT_ACTIONS.items():
        for action in actions:
            if (platform, action) not in have:
                if platform == Platform.X and action in X_DEFAULT_LIMITS:
                    per_hour, per_day = X_DEFAULT_LIMITS[action]
                    db.add(ActionLimit(policy_id=policy.id, platform=platform, action=action, enabled=True,
                                       mode=ActionMode.APPROVAL, max_per_hour=per_hour, max_per_day=per_day))
                else:
                    db.add(ActionLimit(policy_id=policy.id, platform=platform, action=action, enabled=False))
                added = True
    if added or policy in db.new:
        db.commit()


def list_limits(db: Session, policy_id: int) -> list[ActionLimit]:
    stmt = select(ActionLimit).where(ActionLimit.policy_id == policy_id).order_by(ActionLimit.platform, ActionLimit.action)
    return list(db.scalars(stmt))


class SafetyGuard:
    """Compatibility facade over SocialActionGuard (same rules, same audit trail)."""

    def __init__(self, db: Session, agent_id: int) -> None:
        self._db = db
        self._agent_id = agent_id

    def check(self, platform: Platform, action: SocialAction) -> SafetyDecision:
        from app.social.guard import ActionRequest, SocialActionGuard

        d = SocialActionGuard(self._db, self._agent_id).evaluate(ActionRequest(platform, action))
        return SafetyDecision(d.allowed, d.requires_approval, d.reason)
