from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from app.api.deps import DbSession
from app.api.schemas import ContentIn, ContentOut, ContentUpdate
from app.models.content import Content
from app.models.enums import ContentStatus
from app.services.activity import ActivityLogger
from app.services.agent_service import get_agent_config

router = APIRouter(prefix="/content", tags=["content"])


def _reject_published(value: ContentStatus | None) -> None:
    # Publishing only happens through a connector + SafetyGuard, never by editing a row.
    if value == ContentStatus.PUBLISHED:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Content cannot be marked published manually"
        )


@router.get("", response_model=list[ContentOut])
def list_content(
    db: DbSession,
    status_filter: ContentStatus | None = Query(None, alias="status"),
    limit: int = Query(100, ge=1, le=500),
) -> list[ContentOut]:
    stmt = select(Content).order_by(Content.created_at.desc(), Content.id.desc()).limit(limit)
    if status_filter is not None:
        stmt = stmt.where(Content.status == status_filter)
    return [ContentOut.model_validate(c) for c in db.scalars(stmt)]


@router.post("", response_model=ContentOut, status_code=status.HTTP_201_CREATED)
def create_content(payload: ContentIn, db: DbSession) -> ContentOut:
    _reject_published(payload.status)
    config = get_agent_config(db)
    item = Content(agent_id=config.id, created_by="dashboard", **payload.model_dump())
    db.add(item)
    db.commit()
    ActivityLogger(db, config.id).log(
        "content_created", f"Created content #{item.id} ({item.status.value})", actor="dashboard"
    )
    return ContentOut.model_validate(item)


@router.patch("/{content_id}", response_model=ContentOut)
def update_content(content_id: int, payload: ContentUpdate, db: DbSession) -> ContentOut:
    item = db.get(Content, content_id)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Content not found")
    changes = payload.model_dump(exclude_unset=True)
    for required in ("body", "status"):  # NOT NULL columns: null means "leave as is" (title etc. may be cleared)
        if required in changes and changes[required] is None:
            changes.pop(required)
    _reject_published(changes.get("status"))
    for key, value in changes.items():
        setattr(item, key, value)
    db.commit()
    ActivityLogger(db, item.agent_id).log(
        "content_updated", f"Updated content #{item.id} ({item.status.value})", actor="dashboard"
    )
    return ContentOut.model_validate(item)


@router.delete("/{content_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_content(content_id: int, db: DbSession) -> None:
    item = db.get(Content, content_id)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Content not found")
    agent_id = item.agent_id
    db.delete(item)
    db.commit()
    ActivityLogger(db, agent_id).log("content_deleted", f"Deleted content #{content_id}", actor="dashboard")
