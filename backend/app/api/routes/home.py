"""Admin home: one short list of what's waiting for the owner, and whether the main parts are working. Counts only, each
with the admin page that handles it. Read-only."""

from datetime import timedelta
from typing import Any

from fastapi import APIRouter
from sqlalchemy import func, select

from app.api.deps import DbSession, Runtime
from app.db.base import utcnow
from app.models.content import Content
from app.models.enums import ApprovalStatus, ContentStatus
from app.models.memory import Memory
from app.models.members import Member
from app.models.rewards import RewardClaim
from app.models.routing import ScheduledJob
from app.models.social_intel import ApprovalRequest
from app.models.tradingbot import TbApplication
from app.models.tradingos import TsSignal
from app.models.workers import WkReport
from app.services.agent_service import get_agent_config
from app.services.safety import get_or_create_policy

router = APIRouter(tags=["home"])


@router.get("/admin/home")
async def admin_home(db: DbSession, runtime: Runtime) -> dict[str, Any]:
    def n(stmt) -> int:  # type: ignore[no-untyped-def]
        return int(db.scalar(stmt) or 0)

    def count(model, *where) -> int:  # type: ignore[no-untyped-def]
        return n(select(func.count()).select_from(model).where(*where))

    todo = [
        {"key": "signals", "label": "Trading signals waiting for your OK", "count": count(TsSignal, TsSignal.status == "PENDING_REVIEW"), "to": "/trading-os"},
        {"key": "bot_access", "label": "Trading bot access requests", "count": count(TbApplication, TbApplication.status == "PENDING_APPROVAL"), "to": "/trading-bot"},
        {"key": "knight", "label": "$10 Knight reward claims", "count": count(RewardClaim, RewardClaim.reward_key == "knight", RewardClaim.status == "PENDING"),
         "to": "/rank-rewards?tab=knight"},
        {"key": "vip", "label": "Royal WhatsApp VIP requests", "count": count(RewardClaim, RewardClaim.reward_key == "royal", RewardClaim.status == "PENDING"),
         "to": "/rank-rewards?tab=royal"},
        {"key": "workers", "label": "Worker reports to check", "count": count(WkReport, WkReport.status == "PENDING"), "to": "/workers"},
        {"key": "approvals", "label": "Posts and replies waiting for approval", "count": count(ApprovalRequest, ApprovalRequest.status == ApprovalStatus.PENDING),
         "to": "/approvals"},
        {"key": "drafts", "label": "Content drafts to review", "count": count(Content, Content.status == ContentStatus.DRAFT, Content.draft_type.is_not(None)),
         "to": "/content"},
        {"key": "memories", "label": "Community memories to review", "count": count(Memory, Memory.layer == "community", Memory.review_status == "pending"),
         "to": "/knowledge?tab=community"},
    ]

    labels = {name: spec.label for name, spec in runtime.scheduler.specs.items()} if runtime.scheduler else {}
    failing = [labels.get(j.name, j.name) for j in db.scalars(select(ScheduledJob).where(ScheduledJob.enabled.is_(True), ScheduledJob.consecutive_failures > 0))]
    scan = db.get(ScheduledJob, "tradingos.scan")
    week = utcnow() - timedelta(days=7)
    config = get_agent_config(db)
    health = await runtime.llm_health(config.model_name or runtime.provider.model)
    return {
        "todo": todo,
        "status": {
            "kill_switch": get_or_create_policy(db, config.id).kill_switch,
            "telegram": runtime.telegram.status if runtime.telegram.configured else "not_configured",
            "x": runtime.x.status if runtime.x.configured else "not_configured",
            "ai": bool(health.available and health.model_loaded),
            "last_scan_at": scan.last_finished_at.isoformat() if scan and scan.last_finished_at else None,
            "failing_jobs": failing,
        },
        "numbers": {
            "members": count(Member),
            "new_members_7d": count(Member, Member.created_at >= week),
            "signals_7d": count(TsSignal, TsSignal.created_at >= week, TsSignal.strategy != "test"),
            "open_signals": count(TsSignal, TsSignal.status.in_(("WAITING", "ACTIVE"))),
        },
    }
