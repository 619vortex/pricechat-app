from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import DbSession, Runtime, retrieved_out
from app.api.routes.agent import memory_stats
from app.api.schemas import (
    KnowledgeIn,
    KnowledgeSaved,
    KnowledgeSearchOut,
    KnowledgeSourceOut,
    KnowledgeUpdate,
    MemoryOut,
    MemoryStatsOut,
    ReindexOut,
)
from app.knowledge.ingestion import SOURCES
from app.knowledge.service import KnowledgeService
from app.memory.records import MemoryInput
from app.memory.retrieval.base import RetrievalQuery
from app.memory.store import MemoryFilters, MemoryStore
from app.models.enums import MemoryKind, MemoryLayer, ReviewStatus, SourceType
from app.services.agent_service import build_retriever, get_agent_config

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


def _filters(
    layer: MemoryLayer | None,
    kind: MemoryKind | None,
    source_type: SourceType | None,
    tag: str | None,
    review_status: ReviewStatus | None = None,
) -> MemoryFilters | None:
    if not any((layer, kind, source_type, tag, review_status)):
        return None
    return MemoryFilters(
        layers=(layer,) if layer else None,
        kinds=(kind,) if kind else None,
        source_types=(source_type,) if source_type else None,
        tag=tag,
        review_statuses=(review_status,) if review_status else None,
    )


@router.get("", response_model=list[MemoryOut])
def list_knowledge(
    db: DbSession,
    layer: MemoryLayer | None = None,
    kind: MemoryKind | None = None,
    source_type: SourceType | None = None,
    review_status: ReviewStatus | None = None,
    tag: str | None = Query(None, max_length=40),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> list[MemoryOut]:
    store = MemoryStore(db, get_agent_config(db).id)
    filters = _filters(layer, kind, source_type, tag, review_status)
    return [MemoryOut.model_validate(r) for r in store.list(filters, limit, offset)]


@router.get("/search", response_model=KnowledgeSearchOut)
async def search_knowledge(
    db: DbSession,
    runtime: Runtime,
    q: str = Query(..., min_length=1, max_length=500),
    layer: MemoryLayer | None = None,
    limit: int = Query(10, ge=1, le=50),
) -> KnowledgeSearchOut:
    """Search exactly the way the agent recalls memories."""
    retriever = build_retriever(db, runtime, get_agent_config(db).id)
    result = await retriever.retrieve(RetrievalQuery(q, limit, _filters(layer, None, None, None)))
    return KnowledgeSearchOut(
        results=[retrieved_out(m) for m in result.memories],
        strategy=result.strategy,
        duration_ms=result.duration_ms,
        notes=result.notes,
    )


@router.get("/stats", response_model=MemoryStatsOut)
def knowledge_stats(db: DbSession, runtime: Runtime) -> MemoryStatsOut:
    return memory_stats(db, runtime, get_agent_config(db).id)


@router.get("/sources", response_model=list[KnowledgeSourceOut])
def knowledge_sources(runtime: Runtime) -> list[KnowledgeSourceOut]:
    return [
        KnowledgeSourceOut(
            key=s.key,
            display_name=s.display_name,
            description=s.description,
            source_type=s.source_type,
            layer=s.layer,
            # Telegram feeds the brain through the community pipeline once a bot token is configured.
            available=runtime.telegram.configured if s.key == "telegram" else s.available,
        )
        for s in SOURCES
    ]


@router.post("", response_model=KnowledgeSaved, status_code=status.HTTP_201_CREATED)
async def add_knowledge(payload: KnowledgeIn, db: DbSession, runtime: Runtime) -> KnowledgeSaved:
    service = KnowledgeService(db, get_agent_config(db).id, runtime.embedder)
    try:
        record, created, indexed = await service.add(
            MemoryInput(
                content=payload.content,
                title=payload.title,
                kind=payload.kind,
                layer=payload.layer,
                tags=payload.tags,
                source_type=SourceType.MANUAL,
                source=payload.source or "Dashboard",
                importance=payload.importance,
            )
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    return KnowledgeSaved(memory=MemoryOut.model_validate(record), created=created, indexed=indexed)


@router.post("/reindex", response_model=ReindexOut)
async def reindex(db: DbSession, runtime: Runtime) -> ReindexOut:
    service = KnowledgeService(db, get_agent_config(db).id, runtime.embedder)
    count = await service.reindex(actor="dashboard")  # ProviderError -> structured error in app.main
    return ReindexOut(indexed=count, embedding_model=runtime.embedder.model if runtime.embedder else None)


@router.get("/{memory_id}", response_model=MemoryOut)
def get_knowledge(memory_id: int, db: DbSession) -> MemoryOut:
    record = MemoryStore(db, get_agent_config(db).id).get(memory_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Knowledge item not found")
    return MemoryOut.model_validate(record)


@router.patch("/{memory_id}", response_model=KnowledgeSaved)
async def update_knowledge(memory_id: int, payload: KnowledgeUpdate, db: DbSession, runtime: Runtime) -> KnowledgeSaved:
    service = KnowledgeService(db, get_agent_config(db).id, runtime.embedder)
    changes = payload.model_dump(exclude_unset=True)
    for required in ("content", "kind", "layer", "importance", "tags", "review_status"):
        if required in changes and changes[required] is None:
            changes.pop(required)
    try:
        updated = await service.update(memory_id, changes)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    if updated is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Knowledge item not found")
    record, indexed = updated
    return KnowledgeSaved(memory=MemoryOut.model_validate(record), created=False, indexed=indexed)


@router.delete("/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_knowledge(memory_id: int, db: DbSession, runtime: Runtime) -> None:
    service = KnowledgeService(db, get_agent_config(db).id, runtime.embedder)
    if not service.delete(memory_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Knowledge item not found")
