from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from app.api.community_schemas import (
    DraftRequest,
    OpportunityDetailOut,
    OpportunityOut,
    OpportunityUpdate,
    TelegramMessageOut,
)
from app.api.deps import DbSession, Runtime
from app.api.schemas import ContentOut
from app.community import provenance
from app.community.opportunities import draft_opportunity
from app.db.base import utcnow
from app.models.community import ContentOpportunity
from app.models.enums import ActivityLevel, DerivedKind, OpportunityStatus
from app.services.activity import ActivityLogger
from app.services.agent_service import get_agent_config

router = APIRouter(prefix="/opportunities", tags=["opportunities"])


@router.get("", response_model=list[OpportunityOut])
def list_opportunities(
    db: DbSession,
    status_filter: OpportunityStatus | None = Query(None, alias="status"),
    limit: int = Query(100, ge=1, le=500),
) -> list[OpportunityOut]:
    stmt = select(ContentOpportunity)
    if status_filter is not None:
        stmt = stmt.where(ContentOpportunity.status == status_filter)
    return [OpportunityOut.model_validate(o) for o in db.scalars(stmt.order_by(ContentOpportunity.priority.desc(), ContentOpportunity.created_at.desc()).limit(limit))]


def _get(db: DbSession, opp_id: int) -> ContentOpportunity:
    opp = db.get(ContentOpportunity, opp_id)
    if opp is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Opportunity not found")
    return opp


@router.get("/{opp_id}", response_model=OpportunityDetailOut)
def get_opportunity(opp_id: int, db: DbSession) -> OpportunityDetailOut:
    opp = _get(db, opp_id)
    messages = provenance.messages_for(db, DerivedKind.OPPORTUNITY, opp.id, limit=50)
    return OpportunityDetailOut(
        opportunity=OpportunityOut.model_validate(opp), messages=[TelegramMessageOut.model_validate(m) for m in messages]
    )


@router.patch("/{opp_id}", response_model=OpportunityOut)
def update_opportunity(opp_id: int, payload: OpportunityUpdate, db: DbSession) -> OpportunityOut:
    opp = _get(db, opp_id)
    changes = payload.model_dump(exclude_unset=True, exclude_none=True)
    if changes.get("status") == OpportunityStatus.PUBLISHED:
        # Publishing happens only through a connector + SafetyGuard, never by editing a row.
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Opportunities cannot be marked published manually")
    if "platforms" in changes:
        changes["platforms"] = [p.value for p in changes["platforms"]]
    for key, value in changes.items():
        setattr(opp, key, value)
    if "status" in changes:
        opp.reviewed_at = utcnow()
    db.commit()
    if "status" in changes:
        ActivityLogger(db).log(
            "opportunity_reviewed",
            f'Content opportunity "{opp.title}" marked {opp.status.value}',
            level=ActivityLevel.AUDIT,
            actor="dashboard",
            details={"opportunity_id": opp.id},
        )
    return OpportunityOut.model_validate(opp)


@router.post("/{opp_id}/draft", response_model=ContentOut, status_code=status.HTTP_201_CREATED)
async def draft(opp_id: int, payload: DraftRequest, db: DbSession, runtime: Runtime) -> ContentOut:
    """Write a draft (saved to Content as 'draft'). Nothing is published."""
    opp = _get(db, opp_id)
    if opp.status in (OpportunityStatus.REJECTED, OpportunityStatus.PUBLISHED):
        raise HTTPException(status.HTTP_409_CONFLICT, f"Cannot draft a {opp.status.value} opportunity")
    content = await draft_opportunity(db, runtime.router(db), get_agent_config(db).id, opp, payload.channel)
    return ContentOut.model_validate(content)
