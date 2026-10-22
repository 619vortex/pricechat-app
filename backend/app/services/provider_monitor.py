"""Tracks model-provider availability and records failure/recovery transitions."""

import logging
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.activity import ActivityLog
from app.models.enums import ActivityLevel
from app.providers.base import ChatResult, ProviderHealth
from app.services.activity import ActivityLogger

logger = logging.getLogger(__name__)

SUCCESS_EVENTS = ("ai_inference", "model_test")


@dataclass(slots=True)
class InferenceStat:
    at: datetime
    model: str
    latency_ms: float | None


class ProviderMonitor:
    def __init__(self) -> None:
        self.online: bool | None = None  # None = unknown since startup
        self.last_error: str | None = None
        self.last_success: InferenceStat | None = None
        self._loaded = False

    def load_last_success(self, db: Session) -> None:
        """Restore 'last successful inference' from the activity log after a restart."""
        if self._loaded:
            return
        self._loaded = True
        entry = db.scalar(
            select(ActivityLog)
            .where(ActivityLog.event.in_(SUCCESS_EVENTS), ActivityLog.level == ActivityLevel.INFO)
            .order_by(ActivityLog.id.desc())
            .limit(1)
        )
        if entry is not None:
            self.last_success = InferenceStat(
                entry.created_at, str(entry.details.get("model", "")), entry.details.get("latency_ms")
            )

    def record_success(self, db: Session, agent_id: int, result: ChatResult) -> None:
        self.last_success = InferenceStat(utcnow(), result.model, result.latency_ms)
        self._transition(db, agent_id, True, None)

    def record_failure(self, db: Session, agent_id: int, error: str, unreachable: bool) -> None:
        # A missing model or bad request doesn't mean the provider is down.
        if unreachable:
            self._transition(db, agent_id, False, error)
        self.last_error = error

    def record_health(self, db: Session, agent_id: int, health: ProviderHealth) -> None:
        self._transition(db, agent_id, health.available, health.detail if not health.available else None)

    def _transition(self, db: Session, agent_id: int, online: bool, error: str | None) -> None:
        previous, self.online = self.online, online
        if online:
            self.last_error = None
        else:
            self.last_error = error
        if previous is None and online:
            return  # first observation, nothing to report
        if previous is not False and not online:
            ActivityLogger(db, agent_id).log(
                "provider_failure",
                f"The AI model service is unavailable: {error}",
                level=ActivityLevel.ERROR,
            )
        elif previous is False and online:
            ActivityLogger(db, agent_id).log("provider_recovery", "The AI model service is reachable again")
