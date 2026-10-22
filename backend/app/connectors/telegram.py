"""Telegram connector: read-only community intelligence via the official Bot API.

The connector reports status; ingestion runs as a scheduled job (app.jobs) using
app.telegram. There is intentionally no way to send messages from Price Chat.
"""

from app.connectors.base import Capability, ConnectorNotAvailableError, ConnectorStatus, SocialConnector
from app.models.enums import ConnectionStatus, Platform
from app.telegram.state import TelegramRuntime


class TelegramConnector(SocialConnector):
    platform = Platform.TELEGRAM
    display_name = "Telegram"
    planned_capabilities = (Capability.READ_MESSAGES, Capability.ANALYTICS)

    def __init__(self, state: TelegramRuntime | None = None) -> None:
        self.state = state or TelegramRuntime()

    def status(self) -> ConnectorStatus:
        s = self.state
        if not s.configured:
            return ConnectorStatus(
                self.platform, self.display_name, ConnectionStatus.NOT_CONNECTED,
                "Not connected. Add TELEGRAM_BOT_TOKEN to .env and restart.", list(self.planned_capabilities),
            )
        if s.status == "connected":
            who = f"@{s.bot_username}" if s.bot_username else "bot"
            return ConnectorStatus(
                self.platform, self.display_name, ConnectionStatus.CONNECTED,
                f"Read-only as {who} · {s.authorised_chats} authorised chat(s)",
                list(self.planned_capabilities), list(self.planned_capabilities),
            )
        if s.status == "error":
            return ConnectorStatus(
                self.platform, self.display_name, ConnectionStatus.ERROR, s.detail or "Connection error",
                list(self.planned_capabilities),
            )
        return ConnectorStatus(
            self.platform, self.display_name, ConnectionStatus.NOT_CONNECTED,
            "Configured; waiting for the first sync.", list(self.planned_capabilities),
        )

    async def connect(self) -> None:
        raise ConnectorNotAvailableError("Telegram connects automatically through the ingestion job")

    async def disconnect(self) -> None:
        return None
