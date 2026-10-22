from fastapi import APIRouter

from app.api.deps import Runtime
from app.api.schemas import ConnectorOut
from app.connectors.base import ConnectorStatus

router = APIRouter(prefix="/connectors", tags=["connectors"])


def connector_out(status: ConnectorStatus) -> ConnectorOut:
    return ConnectorOut(
        platform=status.platform,
        display_name=status.display_name,
        status=status.status,
        detail=status.detail,
        planned_capabilities=[c.value for c in status.planned_capabilities],
        enabled_capabilities=[c.value for c in status.enabled_capabilities],
    )


@router.get("", response_model=list[ConnectorOut])
async def list_connectors(runtime: Runtime) -> list[ConnectorOut]:
    return [connector_out(s) for s in runtime.connectors.statuses()]
