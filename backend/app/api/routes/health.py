import logging

from fastapi import APIRouter
from sqlalchemy import text

from app.api.deps import DbSession, Runtime, provider_status
from app.api.schemas import HealthOut
from app.services.agent_service import get_agent_config

router = APIRouter(tags=["health"])
logger = logging.getLogger(__name__)


@router.get("/health", response_model=HealthOut)
async def health(db: DbSession, runtime: Runtime) -> HealthOut:
    try:
        db.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        logger.exception("Database health check failed")
        db_ok = False

    model = get_agent_config(db).model_name if db_ok else None
    llm = await runtime.llm_health(model)
    return HealthOut(
        status="ok" if db_ok else "degraded",
        app=runtime.settings.app_name,
        environment=runtime.settings.environment,
        database=db_ok,
        llm=provider_status(llm),
    )


@router.get("/health/live")
def health_live(db: DbSession) -> dict[str, object]:
    """Public liveness probe (Railway's health check): the database only, nothing about the setup, no AI call."""
    try:
        db.execute(text("SELECT 1"))
        return {"status": "ok", "database": True}
    except Exception:
        logger.exception("Database health check failed")
        return {"status": "degraded", "database": False}
