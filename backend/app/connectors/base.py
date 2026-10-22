"""Contract for social-platform connectors.

Rules for future implementations:
- Credentials come only from environment variables (see .env.example).
- Every outbound action must first pass SafetyGuard.check(), and successful
  actions must be logged with event=EXECUTED_EVENT so rate limits apply.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum

from app.models.enums import ConnectionStatus, Platform


class Capability(StrEnum):
    READ_POSTS = "read_posts"
    READ_MESSAGES = "read_messages"
    SEARCH_CONVERSATIONS = "search_conversations"
    DRAFT_REPLIES = "draft_replies"
    SCHEDULE_POSTS = "schedule_posts"
    ANALYTICS = "analytics"
    PUBLISH = "publish"
    REPLY = "reply"
    LIKE = "like"


@dataclass(frozen=True, slots=True)
class ConnectorStatus:
    platform: Platform
    display_name: str
    status: ConnectionStatus
    detail: str
    planned_capabilities: list[Capability] = field(default_factory=list)
    enabled_capabilities: list[Capability] = field(default_factory=list)


class ConnectorNotAvailableError(Exception):
    """Raised by placeholder connectors for any operation not yet implemented."""


class SocialConnector(ABC):
    platform: Platform
    display_name: str
    planned_capabilities: tuple[Capability, ...] = ()

    @abstractmethod
    def status(self) -> ConnectorStatus: ...

    @abstractmethod
    async def connect(self) -> None:
        """Validate credentials and establish a session."""

    @abstractmethod
    async def disconnect(self) -> None: ...


class PlaceholderConnector(SocialConnector):
    """Shared behaviour for connectors that exist in the architecture but are not implemented."""

    def status(self) -> ConnectorStatus:
        return ConnectorStatus(
            platform=self.platform,
            display_name=self.display_name,
            status=ConnectionStatus.NOT_CONNECTED,
            detail="Not connected. Integration is planned for a later phase.",
            planned_capabilities=list(self.planned_capabilities),
        )

    async def connect(self) -> None:
        raise ConnectorNotAvailableError(f"{self.display_name} connector is not implemented yet")

    async def disconnect(self) -> None:
        return None

    def _unavailable(self) -> ConnectorNotAvailableError:
        return ConnectorNotAvailableError(f"{self.display_name} connector is not implemented yet")
