from typing import Literal

from fastapi import APIRouter, Query

from app.api.deps import DbSession, activity_out
from app.api.schemas import ActivityOut
from app.models.enums import ActivityLevel
from app.services.activity import recent_activity

router = APIRouter(prefix="/activity", tags=["activity"])

Category = Literal["ai", "memory", "community", "social", "task", "config", "safety", "content", "system"]


@router.get("", response_model=list[ActivityOut])
def list_activity(
    db: DbSession,
    level: ActivityLevel | None = None,
    category: Category | None = None,
    limit: int = Query(100, ge=1, le=500),
) -> list[ActivityOut]:
    return [activity_out(a) for a in recent_activity(db, limit=limit, level=level, category=category)]
