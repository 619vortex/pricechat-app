"""Admin view of the Terminal Intelligence Engine channel (owner login required: not on the public list). Read-only."""

from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Query

from app.api.deps import DbSession
from app.config.settings import get_settings
from app.db.base import utcnow
from app.intel import gate
from app.intel import router as intel_router

router = APIRouter(prefix="/intel", tags=["intel"])


@router.get("/status")
def status(db: DbSession) -> dict[str, Any]:
    return intel_router.status(db)


@router.get("/shadow")
def shadow(db: DbSession, hours: int = Query(24, ge=1, le=24 * 14), limit: int = Query(100, ge=1, le=500)) -> dict[str, Any]:
    """The shared semantic gate in shadow: agreement with the lanes' current rules and every disagreement, with the router and the AI
    spend ledger (router.status carries ledger.status). Reads only; nothing here decides a post."""
    s = get_settings()
    return {"shadow_enabled": bool(s.intel_shadow_enabled), "report": gate.report(db, utcnow() - timedelta(hours=hours), limit=limit),
            "router": intel_router.status(db)}
