from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import Platform
from app.models.social_intel import ConnectorState


def get_connector_state(db: Session, platform: Platform) -> ConnectorState:
    state = db.scalar(select(ConnectorState).where(ConnectorState.platform == platform))
    if state is None:
        state = ConnectorState(platform=platform)
        db.add(state)
        db.commit()  # short transaction: callers may await network requests next
    return state
