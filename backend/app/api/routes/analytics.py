"""Analytics › Growth: one executive view of whether the social activity is producing growth."""

from fastapi import APIRouter, HTTPException, Query, status

from app.analytics.growth import PERIODS, dashboard
from app.api.deps import DbSession, Runtime

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/growth")
def growth(db: DbSession, runtime: Runtime, period: str = Query("7d")) -> dict[str, object]:
    """Read-only: computed from collected data. Never calls X."""
    if period not in PERIODS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"period must be one of {', '.join(PERIODS)}")
    return dashboard(db, runtime.settings, period)
