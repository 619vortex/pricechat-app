from app.connectors.base import ConnectorStatus, SocialConnector
from app.connectors.telegram import TelegramConnector
from app.connectors.x import XConnector, XRuntime
from app.models.enums import ConnectionStatus, Platform
from app.telegram.state import TelegramRuntime


class ConnectorRegistry:
    def __init__(
        self, connectors: list[SocialConnector] | None = None, telegram: TelegramRuntime | None = None, x: XRuntime | None = None
    ) -> None:
        items = connectors if connectors is not None else [XConnector(x), TelegramConnector(telegram)]
        self._connectors: dict[Platform, SocialConnector] = {c.platform: c for c in items}

    def get(self, platform: Platform) -> SocialConnector:
        return self._connectors[platform]

    def statuses(self) -> list[ConnectorStatus]:
        return [c.status() for c in self._connectors.values()]

    def connected_count(self) -> int:
        return sum(1 for s in self.statuses() if s.status == ConnectionStatus.CONNECTED)
