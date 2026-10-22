from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.api.schemas import (
    ActivityOut,
    MemoryOut,
    ProviderStatusOut,
    RetrievalOut,
    RetrievedMemoryOut,
)
from app.db.session import get_db
from app.memory.retrieval.base import RetrievalResult, RetrievedMemory
from app.models.activity import ActivityLog
from app.providers.base import ProviderHealth
from app.services.activity import describe_event
from app.services.runtime import AppRuntime


def get_runtime(request: Request) -> AppRuntime:
    return request.app.state.runtime


DbSession = Annotated[Session, Depends(get_db)]
Runtime = Annotated[AppRuntime, Depends(get_runtime)]


def provider_status(health: ProviderHealth) -> ProviderStatusOut:
    return ProviderStatusOut(
        provider=health.provider,
        model=health.model,
        available=health.available,
        model_loaded=health.model_loaded,
        detail=health.detail,
        models=health.models,
        endpoint=health.endpoint,
        latency_ms=health.latency_ms,
    )


def retrieved_out(m: RetrievedMemory) -> RetrievedMemoryOut:
    return RetrievedMemoryOut(
        memory=MemoryOut.model_validate(m.record),
        score=m.score,
        semantic=m.semantic,
        lexical=m.lexical,
        reasons=list(m.reasons),
    )


def retrieval_out(result: RetrievalResult) -> RetrievalOut:
    return RetrievalOut(
        strategy=result.strategy,
        duration_ms=result.duration_ms,
        notes=result.notes,
        memories=[retrieved_out(m) for m in result.memories],
    )


def activity_out(entry: ActivityLog) -> ActivityOut:
    category, label = describe_event(entry.event)
    return ActivityOut(
        id=entry.id,
        level=entry.level,
        event=entry.event,
        category=category,
        label=label,
        message=entry.message,
        actor=entry.actor,
        platform=entry.platform,
        action=entry.action,
        details=entry.details,
        created_at=entry.created_at,
    )
