"""Admin view of the Terminal Intelligence Engine channel (owner login required: not on the public list). Read-only."""

from typing import Any

from fastapi import APIRouter

from app.api.deps import DbSession
from app.intel import router as intel_router

router = APIRouter(prefix="/intel", tags=["intel"])


@router.get("/status")
def status(db: DbSession) -> dict[str, Any]:
    return intel_router.status(db)
