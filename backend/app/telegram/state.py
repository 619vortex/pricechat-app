"""In-memory Telegram runtime state shared by the connector, jobs and API."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.db.base import utcnow
from app.telegram.parser import ChatInfo


@dataclass(slots=True)
class DiscoveredChat:
    """A chat the bot can see but that is NOT on the allow-list.

    Only the id, title and type are kept (in memory, never in the database) so the
    user can choose to authorise it. Its messages are discarded.
    """

    chat_id: int
    title: str
    chat_type: str
    last_seen_at: datetime
    ignored_messages: int = 0
    bot_status: str | None = None


@dataclass(slots=True)
class TelegramRuntime:
    configured: bool = False
    status: str = "not_configured"  # not_configured | disconnected | connected | error
    detail: str = ""
    bot_username: str | None = None
    authorised_chats: int = 0
    discovered: dict[int, DiscoveredChat] = field(default_factory=dict)
    # Optional HTTP transport for the Bot API client (tests inject a mock Telegram here).
    transport: Any = None

    def set_status(self, status: str, detail: str = "", bot_username: str | None = None) -> None:
        self.status, self.detail = status, detail
        if bot_username:
            self.bot_username = bot_username

    def note_unauthorised(self, chat: ChatInfo, bot_status: str | None = None, message: bool = True) -> None:
        entry = self.discovered.get(chat.chat_id)
        if entry is None:
            entry = DiscoveredChat(chat.chat_id, chat.title, chat.chat_type, utcnow())
            self.discovered[chat.chat_id] = entry
        entry.title, entry.chat_type, entry.last_seen_at = chat.title, chat.chat_type, utcnow()
        if message:
            entry.ignored_messages += 1
        if bot_status:
            entry.bot_status = bot_status

    def forget(self, chat_id: int) -> None:
        self.discovered.pop(chat_id, None)
