"""XConnector: status and capabilities. Read-only; no write method exists."""

from dataclasses import dataclass, field

from app.connectors.base import Capability, ConnectorNotAvailableError, ConnectorStatus, SocialConnector
from app.models.enums import ConnectionStatus, Platform


@dataclass(slots=True)
class XRuntime:
    """In-memory snapshot of X connection state shared by the connector, jobs and API."""

    configured: bool = False
    status: str = "not_configured"  # not_configured | disconnected | connected | error | paused
    detail: str = ""
    username: str | None = None
    capabilities: dict[str, str] = field(default_factory=dict)  # capability -> status
    transport: object = None  # tests inject a mock X API here


class XConnector(SocialConnector):
    platform = Platform.X
    display_name = "X / Twitter"
    planned_capabilities = (
        Capability.READ_POSTS,
        Capability.SEARCH_CONVERSATIONS,
        Capability.DRAFT_REPLIES,
        Capability.SCHEDULE_POSTS,
        Capability.ANALYTICS,
        Capability.PUBLISH,
        Capability.REPLY,
        Capability.LIKE,
    )
    READ_ONLY = (Capability.READ_POSTS, Capability.SEARCH_CONVERSATIONS, Capability.DRAFT_REPLIES, Capability.ANALYTICS)

    def __init__(self, state: XRuntime | None = None) -> None:
        self.state = state or XRuntime()

    def status(self) -> ConnectorStatus:
        s = self.state
        planned = list(self.planned_capabilities)
        if not s.configured:
            return ConnectorStatus(self.platform, self.display_name, ConnectionStatus.NOT_CONNECTED,
                                   "Not connected. Add your X API credentials on the X › Connection page.", planned)
        if s.status == "connected":
            who = f"@{s.username}" if s.username else "app"
            return ConnectorStatus(self.platform, self.display_name, ConnectionStatus.CONNECTED,
                                   f"Read-only as {who}. Posting is disabled.", planned, list(self.READ_ONLY))
        if s.status == "error":
            return ConnectorStatus(self.platform, self.display_name, ConnectionStatus.ERROR, s.detail or "Connection error", planned)
        if s.status == "paused":
            return ConnectorStatus(self.platform, self.display_name, ConnectionStatus.DISABLED, "Paused by you", planned)
        return ConnectorStatus(self.platform, self.display_name, ConnectionStatus.NOT_CONNECTED,
                               "Configured; click Test connection.", planned)

    async def connect(self) -> None:
        raise ConnectorNotAvailableError("X connects through the Connection page (Test connection)")

    async def disconnect(self) -> None:
        return None
