"""Approval queue for proposed social actions, and write-permission settings.

Supervised operation: every write is proposed here and needs a human decision.
Approve → the SocialActionGuard re-checks everything → dry run stops here and records exactly
what WOULD be sent → otherwise the action is sent through the official API with a single-use
permit → recorded as Executed only when the platform confirms it, Failed otherwise.
AUTOMATIC mode exists in the data model but cannot be selected, and nothing executes without approval.
"""

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.settings import Settings
from app.connectors.x import XRuntime
from app.connectors.x.errors import (
    XAuthError,
    XBudgetExceededError,
    XCreditsError,
    XError,
    XForbiddenError,
    XNotConfiguredError,
    XNotFoundError,
    XRateLimitError,
    XUnavailableError,
    XUsageCapError,
)
from app.db.base import utcnow
from app.learning.operator import record_decision
from app.models.enums import ActionMode, ActivityLevel, ApprovalStatus, FeedbackDecision, PermissionMode, Platform, SocialAction
from app.models.safety import ActionLimit
from app.models.social_intel import ApprovalRequest
from app.services.activity import ActivityLogger
from app.services.safety import get_or_create_policy, list_limits
from app.social.guard import AUTOMATIC_LOCK_REASON, UNAVAILABLE_MARK, ActionRequest, SocialActionGuard

APPROVAL_TTL = timedelta(hours=48)
AVAILABLE_MODES = [PermissionMode.DISABLED, PermissionMode.APPROVAL]  # AUTOMATIC is locked (see guard)
MAX_TEXT = 280


class PermissionError_(ValueError):
    pass


def mode_of(limit: ActionLimit, policy_mode: ActionMode) -> PermissionMode:
    if not limit.enabled:
        return PermissionMode.DISABLED
    return PermissionMode.APPROVAL  # automatic is locked, so an enabled action always needs approval


def set_permission(db: Session, agent_id: int, platform: Platform, action: SocialAction, mode: PermissionMode | None,
                   max_per_hour: int | None, max_per_day: int | None, actor: str = "dashboard") -> ActionLimit:
    if mode == PermissionMode.AUTOMATIC:
        raise PermissionError_(f"{AUTOMATIC_LOCK_REASON}. Use Disabled or Approval required.")
    policy = get_or_create_policy(db, agent_id)
    limit = db.scalar(select(ActionLimit).where(ActionLimit.policy_id == policy.id, ActionLimit.platform == platform, ActionLimit.action == action))
    if limit is None:
        raise LookupError("Unknown capability")
    if mode is not None:
        limit.enabled = mode != PermissionMode.DISABLED
        limit.mode = ActionMode.APPROVAL
    if max_per_hour is not None:
        limit.max_per_hour = max_per_hour
    if max_per_day is not None:
        limit.max_per_day = max_per_day
    db.commit()
    ActivityLogger(db, agent_id).log(
        "permission_updated", f"{platform.value} {action.value}: {mode_of(limit, policy.mode).value} "
        f"(max {limit.max_per_hour}/hour, {limit.max_per_day}/day)", level=ActivityLevel.AUDIT, actor=actor,
    )
    return limit


def permissions(db: Session, agent_id: int) -> list[tuple[ActionLimit, PermissionMode]]:
    policy = get_or_create_policy(db, agent_id)
    return [(l, mode_of(l, policy.mode)) for l in list_limits(db, policy.id) if l.action != SocialAction.DIRECT_MESSAGE]


@dataclass(slots=True)
class ApprovalOutcome:
    approval: ApprovalRequest
    executed: bool
    reason: str


def _request(a: ApprovalRequest) -> ActionRequest:
    return ActionRequest(a.platform, a.action, a.target_external_id, a.target_username, (a.payload or {}).get("text"))


def describe(action: SocialAction, account: str | None, payload: dict | None) -> str:
    """Human wording for logs: "@account's post about Bitcoin funding"."""
    topic = (payload or {}).get("topic")
    if action in (SocialAction.LIKE, SocialAction.REPOST):
        return (f"@{account}'s post" if account else "a post") + (f" about {topic}" if topic else "")
    return f"@{account}" if account else ""


def explain_x_error(exc: Exception) -> tuple[str, str]:
    """(kind, human-readable reason). Never includes credentials or raw auth responses."""
    if isinstance(exc, XCreditsError):
        return "billing", "X billing: your developer account has no API credit (402). Add credit at console.x.com › Billing, then try again."
    if isinstance(exc, XUsageCapError):
        return "billing", "X usage cap or spending limit reached. Raise it at console.x.com, or wait for the next cycle."
    if isinstance(exc, XRateLimitError):
        when = f" after {exc.reset_at:%H:%M} UTC" if exc.reset_at else " later"
        return "rate_limit", f"X rate limit reached (429). Try again{when}."
    if isinstance(exc, XAuthError):
        return "auth", ("X rejected the account credentials (401). Regenerate the Access Token and Secret in the X developer console "
                        "and save them on X / Twitter › Connection.")
    if isinstance(exc, XForbiddenError):
        return "permission", ("X refused permission (403). The app needs Read and write permission and Access Tokens generated after "
                              "switching to it; X may also refuse if the author restricts interactions.")
    if isinstance(exc, XNotFoundError):
        return "unavailable", f"The post or account was deleted or is {UNAVAILABLE_MARK} (404)."
    if isinstance(exc, XUnavailableError):
        if "in time" in str(exc):
            return "timeout", "X did not answer in time. The action may still have reached X: check X before retrying."
        return "x_service", "X service problem (5xx or network). Nothing was confirmed; try again later."
    if isinstance(exc, XBudgetExceededError):
        return "budget", f"Stopped by Price Chat's own X budget: {exc}"
    if isinstance(exc, XNotConfiguredError):
        return "not_configured", str(exc)
    return "error", str(exc)


def _guard_dict(d) -> dict:  # type: ignore[no-untyped-def]
    return {"allowed": d.allowed, "requires_approval": d.requires_approval, "executable": d.executable, "reason": d.reason, "checks": d.checks}


def propose(db: Session, agent_id: int, req: ActionRequest, source_kind: str = "manual", source_id: int | None = None,
            extra: dict | None = None, actor: str = "dashboard", settings: Settings | None = None) -> ApprovalRequest:
    if req.text is not None and len(req.text) > MAX_TEXT and req.platform == Platform.X:
        raise ValueError(f"Text is {len(req.text)} characters; X allows {MAX_TEXT}")
    decision = SocialActionGuard(db, agent_id, settings).evaluate(req)
    a = ApprovalRequest(
        platform=req.platform, action=req.action, target_external_id=req.target_external_id, target_username=req.target_username,
        payload={"text": req.text, **(extra or {})}, source_kind=source_kind, source_id=source_id, status=ApprovalStatus.PENDING,
        guard_result=_guard_dict(decision), expires_at=utcnow() + APPROVAL_TTL,
    )
    db.add(a)
    db.commit()
    msg = (f"Like proposed: {describe(req.action, req.account, extra)}" if req.action == SocialAction.LIKE else
           f"Proposed {req.platform.value} {req.action.value}" + (f" to @{req.target_username}" if req.target_username else ""))
    ActivityLogger(db, agent_id).log(
        "approval_proposed", msg + f" (guard: {decision.reason})", level=ActivityLevel.AUDIT, actor=actor, details={"approval_id": a.id},
    )
    return a


async def decide(db: Session, agent_id: int, approval: ApprovalRequest, approve: bool, note: str = "", *,
                 settings: Settings, x_runtime: XRuntime | None, edited_text: str | None = None, actor: str = "dashboard") -> ApprovalOutcome:
    if approval.status != ApprovalStatus.PENDING:
        raise ValueError(f"This request is already {approval.status.value}")
    if approval.expires_at and approval.expires_at < utcnow():
        approval.status = ApprovalStatus.EXPIRED
        db.commit()
        raise ValueError("This request has expired")
    approval.decided_at, approval.notes = utcnow(), note
    payload = dict(approval.payload or {})
    original = payload.get("text") or ""
    if not approve:
        approval.status = ApprovalStatus.REJECTED
        record_decision(db, approval, FeedbackDecision.REJECT, note=note)
        db.commit()
        msg = (f"Like rejected: {describe(approval.action, _request(approval).account, approval.payload)}" if approval.action == SocialAction.LIKE
               else f"Rejected proposed {approval.platform.value} {approval.action.value}")
        ActivityLogger(db, agent_id).log("approval_rejected", msg, level=ActivityLevel.AUDIT, actor=actor, details={"approval_id": approval.id})
        return ApprovalOutcome(approval, False, "rejected")

    if edited_text is not None and edited_text.strip() != original.strip():
        text = edited_text.strip()
        if not text:
            raise ValueError("The edited text is empty")
        if approval.platform == Platform.X and len(text) > MAX_TEXT:
            raise ValueError(f"Text is {len(text)} characters; X allows {MAX_TEXT}")
        payload["original_text"], payload["text"] = original, text
        approval.payload = payload
        record_decision(db, approval, FeedbackDecision.EDIT, original=original, final=text, note=note)
    else:
        record_decision(db, approval, FeedbackDecision.APPROVE, note=note)
    approval.status = ApprovalStatus.APPROVED
    db.commit()
    ActivityLogger(db, agent_id).log(
        "approval_approved", (f"Like approved: {describe(approval.action, _request(approval).account, payload)}" if approval.action == SocialAction.LIKE
                              else f"Approved {approval.platform.value} {approval.action.value}" + (" (edited)" if "original_text" in payload else "")),
        level=ActivityLevel.AUDIT, actor=actor, details={"approval_id": approval.id},
    )
    return await execute(db, agent_id, approval, settings=settings, x_runtime=x_runtime)


async def execute(db: Session, agent_id: int, approval: ApprovalRequest, *, settings: Settings, x_runtime: XRuntime | None) -> ApprovalOutcome:
    """The only path to a social write. Requires a human approval and re-checks the guard first."""
    if approval.status != ApprovalStatus.APPROVED or approval.decided_at is None:
        raise PermissionError("Only a human-approved request can be executed")
    guard = SocialActionGuard(db, agent_id, settings)
    req = _request(approval)
    log = ActivityLogger(db, agent_id)
    decision = guard.evaluate(req, approved=True)
    approval.guard_result = _guard_dict(decision)
    if not decision.allowed:
        guard.record_attempt(req, decision, approval.id, "blocked")
        approval.status = ApprovalStatus.FAILED
        approval.execution_result = {"executed": False, "blocked": True, "reason": f"Blocked by the safety guard: {decision.reason}"}
        db.commit()
        log.log("x_action_blocked", (f"Like blocked: {decision.reason}" if req.action == SocialAction.LIKE
                                     else f"{req.action.value} blocked at execution: {decision.reason}"), level=ActivityLevel.WARNING,
                platform=req.platform, action=req.action.value, details={"approval_id": approval.id})
        return ApprovalOutcome(approval, False, decision.reason)

    would = {"action": req.action.value, "target_post_id": req.target_external_id if req.action != SocialAction.FOLLOW else None,
             "target_account": req.target_username, "target_user_id": req.target_external_id if req.action == SocialAction.FOLLOW else None,
             "text": req.text, "quote_post_id": (approval.payload or {}).get("quote_post_id")}
    if get_or_create_policy(db, agent_id).dry_run:
        guard.record_attempt(req, decision, approval.id, "dry_run")
        approval.execution_result = {"executed": False, "dry_run": True, "would_execute": would,
                                     "reason": "Dry run: every check passed; nothing was sent to X"}
        db.commit()
        dry_msg = (f"WOULD LIKE POST: {describe(req.action, req.account, approval.payload)} (dry run, nothing sent)" if req.action == SocialAction.LIKE
                   else f"WOULD EXECUTE {req.action.value}" + (f" to @{req.target_username}" if req.target_username else "")
                   + (f": {req.text[:120]}" if req.text else ""))
        log.log("x_action_dry_run", dry_msg, level=ActivityLevel.AUDIT, platform=req.platform, action=req.action.value,
                details={"approval_id": approval.id})
        return ApprovalOutcome(approval, False, "dry run")

    if req.platform != Platform.X or x_runtime is None:
        raise PermissionError(f"No write connector for {req.platform.value}")
    from app.social.x_executor import perform

    permit = guard.issue_permit(req, decision, approval.id)
    try:
        result = await perform(db, settings, x_runtime, approval, req, permit)
    except (XError, PermissionError) as exc:
        uncertain = isinstance(exc, XUnavailableError) and "in time" in str(exc)
        kind, human = explain_x_error(exc)
        guard.record_attempt(req, decision, approval.id, "failed", reason=human)
        approval.status = ApprovalStatus.FAILED
        approval.execution_result = {"executed": False, "error": human, "error_code": getattr(exc, "code", "error"), "error_kind": kind,
                                     "uncertain": uncertain, "attempted_at": utcnow().isoformat()}
        db.commit()
        log.log("x_action_failed", (f"Like failed: {human}" if req.action == SocialAction.LIKE else f"X {req.action.value} failed: {human}"),
                level=ActivityLevel.ERROR, platform=req.platform, action=req.action.value, details={"approval_id": approval.id, "error_kind": kind})
        return ApprovalOutcome(approval, False, human)

    if not result.confirmed:
        guard.record_attempt(req, decision, approval.id, "failed", reason="X did not confirm the action")
        approval.status = ApprovalStatus.FAILED
        approval.execution_result = {"executed": False, "error": "X did not confirm the action", "error_kind": "not_confirmed", "response": result.response}
        db.commit()
        log.log("x_action_failed", f"X {req.action.value}: X did not confirm the action", level=ActivityLevel.ERROR,
                platform=req.platform, action=req.action.value, details={"approval_id": approval.id})
        return ApprovalOutcome(approval, False, "not confirmed")

    guard.record_attempt(req, decision, approval.id, "executed", reason="confirmed by X", external_id=result.x_id)
    approval.status = ApprovalStatus.EXECUTED
    approval.executed_at = utcnow()
    from app.connectors.state import get_connector_state

    state = get_connector_state(db, req.platform)
    approval.execution_result = {
        "executed": True, "x_id": result.x_id, "url": result.url, "response": result.response,
        "executed_at": approval.executed_at.isoformat(), "approval_id": approval.id,
        "executed_by": {"username": state.account_username, "user_id": state.account_external_id},
        "target_post_id": req.target_external_id if req.action != SocialAction.FOLLOW else None,
        "target_account": req.target_username, "target_author_id": (approval.payload or {}).get("target_author_id"),
    }
    db.commit()
    msg = (f"Liked {describe(req.action, req.account, approval.payload)}." if req.action == SocialAction.LIKE else
           f"Executed on X: {req.action.value}" + (f" to @{req.target_username}" if req.target_username else "") + (f" ({result.url})" if result.url else ""))
    log.log("x_action_executed", msg, level=ActivityLevel.AUDIT, platform=req.platform, action=req.action.value,
            details={"approval_id": approval.id, "x_id": result.x_id, "url": result.url})
    return ApprovalOutcome(approval, True, "executed")


def expire_pending(db: Session) -> int:
    rows = list(db.scalars(select(ApprovalRequest).where(ApprovalRequest.status == ApprovalStatus.PENDING, ApprovalRequest.expires_at < utcnow())))
    for a in rows:
        a.status = ApprovalStatus.EXPIRED
    db.commit()
    return len(rows)
