"""SocialActionGuard: the single gate every social write action must pass.

Checks, in order: social actions active (kill switch) → outbound switch → platform enabled →
platform connection → user-context authentication → write capability → action permission →
hourly/daily limits → duplicate action → duplicate content → per-target cooldown → recent
interaction history → target restrictions → human approval.

Two separate answers:
- `allowed`: the policy permits the action (possibly only after human approval);
- `executable`: it may be performed now. That requires a human approval of this exact request.

Writes are supervised: AUTOMATIC mode exists in the data model but is locked (AUTOMATIC_AVAILABLE
= False), so every write needs a human approval. Connector write methods refuse to run without an
`ExecutionPermit`, which only this guard can issue, only for an approved request that passed every
check, and which can be used once.
"""

import hashlib
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config.settings import Settings, get_settings
from app.connectors.state import get_connector_state
from app.db.base import utcnow
from app.models.activity import ActivityLog
from app.models.enums import ActionMode, ActivityLevel, Platform, SocialAction
from app.models.safety import ActionLimit
from app.models.social_intel import SocialActionLog, SocialProfile, WatchedAccount
from app.security import credentials
from app.services.activity import ActivityLogger
from app.services.safety import EXECUTED_EVENT, get_or_create_policy

# Supervised operation: approved actions may be executed (subject to dry run). Automatic is locked.
WRITE_EXECUTION_AVAILABLE = True
AUTOMATIC_AVAILABLE = False
AUTOMATIC_LOCK_REASON = "Automatic mode is locked: complete the supervised pilot first"
REPEAT_REPLY_MINUTES = 60  # don't keep replying to the same account
LIKE_ACCOUNT_COOLDOWN_HOURS = 12  # at most one like per account in this window
UNAVAILABLE_MARK = "no longer available"  # reason text recorded when X answers 404 for the target

COUNTED = ("executed", "simulated")  # outcomes that count as "done" for duplicates/cooldowns (dry runs don't)
ACTION_WORD = {SocialAction.LIKE: "liked", SocialAction.REPOST: "reposted", SocialAction.FOLLOW: "followed"}


@dataclass(frozen=True, slots=True)
class ActionRequest:
    platform: Platform
    action: SocialAction
    target_external_id: str | None = None  # post id (reply/like/repost/quote) or user id (follow)
    target_username: str | None = None  # account the action is aimed at (post author, or the followed account)
    text: str | None = None

    @property
    def text_hash(self) -> str | None:
        if not self.text:
            return None
        return hashlib.sha256(" ".join(self.text.lower().split()).encode()).hexdigest()

    @property
    def account(self) -> str | None:
        return self.target_username.lower().lstrip("@") if self.target_username else None


@dataclass(slots=True)
class GuardDecision:
    allowed: bool
    requires_approval: bool
    executable: bool
    reason: str
    checks: list[dict[str, object]] = field(default_factory=list)


_PERMIT_KEY = object()  # only this module can mint permits
_USED_PERMITS: set[str] = set()


@dataclass(frozen=True, slots=True)
class ExecutionPermit:
    action: SocialAction
    target_external_id: str | None
    text_hash: str | None
    approval_id: int
    nonce: str
    key: object


def require_permit(permit: "ExecutionPermit | None", action: SocialAction, target_external_id: str | None = None) -> None:
    """Called by every connector write method before any request is sent. Single use."""
    if permit is None or permit.key is not _PERMIT_KEY:
        raise PermissionError("Write blocked: no SocialActionGuard permit")
    if permit.action != action or (target_external_id is not None and permit.target_external_id != target_external_id):
        raise PermissionError("Write blocked: the permit is for a different action")
    if permit.nonce in _USED_PERMITS:
        raise PermissionError("Write blocked: this permit was already used")
    _USED_PERMITS.add(permit.nonce)


class SocialActionGuard:
    def __init__(self, db: Session, agent_id: int, settings: Settings | None = None) -> None:
        self.db = db
        self.agent_id = agent_id
        self.settings = settings or get_settings()

    def evaluate(self, req: ActionRequest, audit: bool = True, approved: bool = False) -> GuardDecision:
        decision = self._evaluate(req, approved)
        if audit:
            ActivityLogger(self.db, self.agent_id).log(
                "safety_check",
                f"{req.platform.value}.{req.action.value}: {decision.reason}",
                level=ActivityLevel.AUDIT,
                actor="safety_guard",
                platform=req.platform,
                action=req.action.value,
                details={"allowed": decision.allowed, "requires_approval": decision.requires_approval,
                         "executable": decision.executable, "target": req.target_username or req.target_external_id},
            )
        return decision

    def issue_permit(self, req: ActionRequest, decision: GuardDecision, approval_id: int) -> ExecutionPermit:
        if not (decision.allowed and decision.executable):
            raise PermissionError(f"Not executable: {decision.reason}")
        return ExecutionPermit(req.action, req.target_external_id, req.text_hash, approval_id, secrets.token_hex(8), _PERMIT_KEY)

    def record_attempt(self, req: ActionRequest, decision: GuardDecision | None, approval_id: int | None, outcome: str,
                       reason: str | None = None, external_id: str | None = None) -> None:
        """Log an attempted execution (blocked / dry_run / executed / failed)."""
        self.db.add(SocialActionLog(
            platform=req.platform, action=req.action, target_external_id=req.target_external_id or req.target_username,
            target_account=req.account, text_hash=req.text_hash, approval_id=approval_id, outcome=outcome,
            reason=reason if reason is not None else (decision.reason if decision else ""), external_id=external_id,
        ))
        self.db.flush()

    # --- checks ---------------------------------------------------------------------------------

    def _evaluate(self, req: ActionRequest, approved: bool) -> GuardDecision:
        policy = get_or_create_policy(self.db, self.agent_id)
        checks: list[dict[str, object]] = []

        def fail(check: str, reason: str) -> GuardDecision:
            checks.append({"check": check, "passed": False, "detail": reason})
            return GuardDecision(False, False, False, reason, checks)

        def ok(check: str, detail: str = "") -> None:
            checks.append({"check": check, "passed": True, "detail": detail})

        if policy.kill_switch:
            return fail("kill_switch", "Social actions are paused")
        ok("kill_switch", "social actions active")
        if not policy.outbound_enabled:
            return fail("outbound", "Outbound actions are disabled")
        ok("outbound")
        if (policy.platforms_enabled or {}).get(req.platform.value) is False:
            return fail("platform", f"{req.platform.value} is disabled for social actions")
        ok("platform")

        if req.platform == Platform.X:
            if (blocked := self._x_api_problem(req)) is not None:
                return fail(*blocked)
            ok("connection", "X connected")
            ok("user_auth", "user-context credentials present")
            ok("capability", "write access not known to be unavailable")

        limit = self.db.scalar(select(ActionLimit).where(
            ActionLimit.policy_id == policy.id, ActionLimit.platform == req.platform, ActionLimit.action == req.action))
        if limit is None or not limit.enabled:
            return fail("permission", "Action is not enabled for this platform")
        ok("permission", "approval required")

        now = utcnow()
        hour_ago, day_ago = now - timedelta(hours=1), now - timedelta(days=1)
        if self._executed_since(hour_ago) >= policy.max_actions_per_hour:
            return fail("rate_limit", "Global hourly rate limit reached")
        if self._executed_since(day_ago) >= policy.max_actions_per_day:
            return fail("rate_limit", "Global daily rate limit reached")
        if self._executed_since(hour_ago, req.platform, req.action) >= limit.max_per_hour:
            return fail("rate_limit", f"Hourly limit for {req.action.value} reached ({limit.max_per_hour}/hour)")
        if self._executed_since(day_ago, req.platform, req.action) >= limit.max_per_day:
            return fail("rate_limit", f"Daily limit for {req.action.value} reached ({limit.max_per_day}/day)")
        ok("rate_limit", f"{self._executed_since(day_ago, req.platform, req.action)}/{limit.max_per_day} today")

        if req.action in ACTION_WORD and req.target_external_id:
            done = self.db.scalar(select(SocialActionLog.id).where(
                SocialActionLog.platform == req.platform, SocialActionLog.action == req.action,
                SocialActionLog.target_external_id == req.target_external_id, SocialActionLog.outcome.in_(COUNTED)))
            if done:
                return fail("duplicate_action", f"Already {ACTION_WORD[req.action]} this {'account' if req.action == SocialAction.FOLLOW else 'post'}")
        ok("duplicate_action")

        if req.text_hash and policy.duplicate_window_hours:
            dup = self.db.scalar(select(SocialActionLog.id).where(
                SocialActionLog.text_hash == req.text_hash, SocialActionLog.outcome.in_(COUNTED),
                SocialActionLog.created_at >= now - timedelta(hours=policy.duplicate_window_hours)))
            if dup:
                return fail("duplicate", f"Duplicate of text already sent in the last {policy.duplicate_window_hours}h")
        ok("duplicate")

        target = req.account or req.target_external_id
        if target and policy.cooldown_minutes:
            recent = self.db.scalar(select(SocialActionLog.id).where(
                (SocialActionLog.target_account == target) | (SocialActionLog.target_external_id == target),
                SocialActionLog.outcome.in_(COUNTED),
                SocialActionLog.created_at >= now - timedelta(minutes=policy.cooldown_minutes)))
            if recent:
                return fail("cooldown", f"Cooldown: this account was acted on in the last {policy.cooldown_minutes} minutes")
        ok("cooldown")

        if req.action == SocialAction.REPLY and req.account:
            last = self.db.scalar(select(func.max(SocialActionLog.created_at)).where(
                SocialActionLog.action == SocialAction.REPLY, SocialActionLog.target_account == req.account,
                SocialActionLog.outcome.in_(COUNTED), SocialActionLog.created_at >= now - timedelta(minutes=REPEAT_REPLY_MINUTES)))
            if last:
                mins = max(1, int((now - (last if last.tzinfo else last.replace(tzinfo=now.tzinfo))).total_seconds() // 60))
                return fail("interaction_history", f"Already replied to @{req.account} {mins} min ago")
        if req.action == SocialAction.LIKE and req.account:
            liked = self.db.scalar(select(func.max(SocialActionLog.created_at)).where(
                SocialActionLog.action == SocialAction.LIKE, SocialActionLog.target_account == req.account,
                SocialActionLog.outcome.in_(COUNTED), SocialActionLog.created_at >= now - timedelta(hours=LIKE_ACCOUNT_COOLDOWN_HOURS)))
            if liked:
                return fail("like_cooldown", f"Already liked a post by @{req.account} in the last {LIKE_ACCOUNT_COOLDOWN_HOURS} hours")
        ok("interaction_history")

        if req.action in (SocialAction.LIKE, SocialAction.REPOST, SocialAction.REPLY) and req.target_external_id:
            gone = self.db.scalar(select(SocialActionLog.id).where(
                SocialActionLog.target_external_id == req.target_external_id, SocialActionLog.outcome == "failed",
                SocialActionLog.reason.like(f"%{UNAVAILABLE_MARK}%")))
            if gone:
                return fail("post_available", "X reported this post as deleted or no longer available")
        ok("post_available")

        if req.target_username or req.target_external_id:
            blocked = {b.lower().lstrip("@") for b in (policy.blocked_targets or [])}
            if {req.account or "", str(req.target_external_id or "").lower()} & blocked:
                return fail("target", "Target account is on the blocked list")
            if policy.watchlist_only and not self._on_watchlist(req):
                return fail("target", "Only watched accounts may be targeted")
        ok("target")

        # Approval: AUTOMATIC is locked, so every write needs a human approval of this exact request.
        mode = limit.mode or policy.mode
        if mode == ActionMode.AUTOMATIC and not AUTOMATIC_AVAILABLE:
            mode = ActionMode.APPROVAL
        checks.append({"check": "approval", "passed": approved,
                       "detail": "approved by a human" if approved else "waiting for human approval"})
        executable = WRITE_EXECUTION_AVAILABLE and approved
        reason = "All checks passed; approved" if approved else "Allowed pending human approval"
        return GuardDecision(True, True, executable, reason, checks)

    def _x_api_problem(self, req: ActionRequest) -> tuple[str, str] | None:
        creds = credentials.x_credentials(self.settings)
        state = get_connector_state(self.db, Platform.X)
        if not creds.configured:
            return "connection", "X is not connected"
        if state.blocked_until and state.blocked_until > utcnow():
            return "connection", f"X is rate limited or capped until {state.blocked_until:%H:%M} UTC"
        if not creds.user_context:
            return "user_auth", "X write actions need user-context credentials (Access Token and Access Token Secret)"
        cap = (state.capabilities or {}).get(f"write_{req.action.value}") or {}
        if cap.get("status") == "unavailable":
            return "capability", f"X does not allow {req.action.value} with these credentials: {cap.get('detail') or 'unavailable'}"
        return None

    def _on_watchlist(self, req: ActionRequest) -> bool:
        stmt = select(WatchedAccount.id).join(SocialProfile, SocialProfile.id == WatchedAccount.profile_id).where(SocialProfile.platform == req.platform)
        if req.target_username:
            stmt = stmt.where(func.lower(SocialProfile.username) == req.account)
        elif req.target_external_id:
            stmt = stmt.where(SocialProfile.external_id == req.target_external_id)
        return self.db.scalar(stmt) is not None

    def _executed_since(self, since: datetime, platform: Platform | None = None, action: SocialAction | None = None) -> int:
        legacy = select(func.count()).select_from(ActivityLog).where(ActivityLog.event == EXECUTED_EVENT, ActivityLog.created_at >= since)
        new = select(func.count()).select_from(SocialActionLog).where(SocialActionLog.outcome == "executed", SocialActionLog.created_at >= since)
        if platform is not None:
            legacy, new = legacy.where(ActivityLog.platform == platform), new.where(SocialActionLog.platform == platform)
        if action is not None:
            legacy, new = legacy.where(ActivityLog.action == action.value), new.where(SocialActionLog.action == action)
        return int(self.db.scalar(legacy) or 0) + int(self.db.scalar(new) or 0)


def executed_today(db: Session, platform: Platform = Platform.X) -> dict[str, int]:
    """Actions actually executed on the platform in the last 24 hours, per action."""
    since = utcnow() - timedelta(days=1)
    rows = db.execute(select(SocialActionLog.action, func.count()).where(
        SocialActionLog.platform == platform, SocialActionLog.outcome == "executed", SocialActionLog.created_at >= since,
    ).group_by(SocialActionLog.action))
    return {a.value: int(n) for a, n in rows}
