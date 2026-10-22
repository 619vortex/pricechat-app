from fastapi import APIRouter
from sqlalchemy import func, select

from app.api.deps import DbSession, Runtime, activity_out, provider_status
from app.api.routes.connectors import connector_out
from app.api.schemas import ContentOut, GoalOut, OverviewOut, TaskCounts
from app.memory.store import MemoryStore
from app.models.content import Content
from app.models.enums import ConnectionStatus, ContentStatus, TaskStatus
from app.models.social import SocialAccount
from app.models.task import Task
from app.services.activity import recent_activity
from app.services.agent_service import get_agent_config
from app.services.brand_service import list_goals
from app.services.safety import get_or_create_policy

router = APIRouter(prefix="/overview", tags=["overview"])

QUEUED_STATUSES = (ContentStatus.QUEUED, ContentStatus.PENDING_APPROVAL, ContentStatus.APPROVED)


@router.get("", response_model=OverviewOut)
async def overview(db: DbSession, runtime: Runtime) -> OverviewOut:
    config = get_agent_config(db)
    model = config.model_name or runtime.provider.model
    llm = await runtime.llm_health(model)
    runtime.monitor.load_last_success(db)
    runtime.monitor.record_health(db, config.id, llm)

    task_counts = dict(db.execute(select(Task.status, func.count()).group_by(Task.status)).all())
    accounts_connected = db.scalar(
        select(func.count())
        .select_from(SocialAccount)
        .where(SocialAccount.status == ConnectionStatus.CONNECTED)
    )
    queued = db.scalars(
        select(Content)
        .where(Content.status.in_(QUEUED_STATUSES))
        .order_by(Content.scheduled_for.is_(None), Content.scheduled_for, Content.created_at.desc())
        .limit(10)
    )
    last = runtime.monitor.last_success

    return OverviewOut(
        agent_name=config.name,
        agent_status=config.status,
        llm=provider_status(llm),
        effective_model=model,
        last_success_at=last.at if last else None,
        connectors=[connector_out(s) for s in runtime.connectors.statuses()],
        social_accounts_connected=int(accounts_connected or 0),
        tasks=TaskCounts(
            pending=task_counts.get(TaskStatus.PENDING, 0),
            running=task_counts.get(TaskStatus.RUNNING, 0),
            failed=task_counts.get(TaskStatus.FAILED, 0),
        ),
        memory_count=MemoryStore(db, config.id).count(),
        active_goals=[GoalOut.model_validate(g) for g in list_goals(db, config.id, enabled_only=True)],
        kill_switch=get_or_create_policy(db, config.id).kill_switch,
        recent_activity=[activity_out(a) for a in recent_activity(db, limit=10)],
        queued_content=[ContentOut.model_validate(c) for c in queued],
    )
