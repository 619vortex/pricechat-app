"""Minimal client for the official Telegram Bot API (https://core.telegram.org/bots/api).

Read-only by design: it implements only the methods Price Chat needs to observe
chats (getMe, getUpdates, getChat, getChatMemberCount, getChatMember). There is
deliberately no general sendMessage: the ONE exception is `confirm_sign_in`, a fixed
confirmation sent privately to a person who has just pressed Start on a valid Price Chat
website sign-in link (so they know it worked). It can't message anyone else. getChatMember only reads whether one linked member is in a
chat (Kiwi Passport "Join the channel"); for a channel Telegram answers it only when the
bot is an administrator there.

The VIP Trading Bot private group is the one place the bot manages access (invite links that only create join
requests, approving/declining those requests, removing a revoked member): see the methods at the end.
"""

import logging
import math
import time
from typing import Any

import httpx

from app.telegram.errors import (
    TelegramAuthError,
    TelegramConflictError,
    TelegramRateLimitError,
    TelegramRequestError,
    TelegramUnavailableError,
)

logger = logging.getLogger(__name__)

# Update types we ask Telegram for. Everything else is never delivered to us.
# chat_join_request / chat_member: only acted on for the VIP Trading Bot private group (app/tradingbot/service.on_update).
ALLOWED_UPDATES = ["message", "edited_message", "channel_post", "edited_channel_post", "my_chat_member", "chat_join_request", "chat_member",
                   "callback_query"]  # callback_query: the ENTER / REJECT / AUTO buttons on Trading OS signal cards


SENT_HOOKS: list[Any] = []  # called with each sent message (e.g. tradingos.autodelete notes the main chat's ones); never raise


def _sent(result: Any) -> Any:
    for hook in SENT_HOOKS:
        try:
            hook(result)
        except Exception:  # noqa: BLE001 - a hook must never break a send
            logging.getLogger(__name__).exception("sent hook failed")
    return result


class TelegramBotClient:
    def __init__(
        self,
        token: str,
        api_base: str = "https://api.telegram.org",
        timeout_seconds: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not token:
            raise ValueError("Telegram bot token is empty")
        self._token = token
        self.api_base = api_base.rstrip("/")
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(timeout_seconds, connect=10.0), transport=transport)
        self._flood_until: dict[str, float] = {}  # chat id -> monotonic time Telegram's 429 retry_after ends

    def __repr__(self) -> str:  # never show the token
        return f"TelegramBotClient(api_base={self.api_base!r})"

    async def get_me(self) -> dict[str, Any]:
        return await self._call("getMe")

    async def get_updates(self, offset: int | None, timeout: int = 0, limit: int = 100) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"timeout": timeout, "limit": limit, "allowed_updates": ALLOWED_UPDATES}
        if offset is not None:
            params["offset"] = offset
        # Long polling: allow the HTTP request to outlive Telegram's own wait.
        return await self._call("getUpdates", params, read_timeout=timeout + 15)

    async def get_chat(self, chat_id: int | str) -> dict[str, Any]:
        return await self._call("getChat", {"chat_id": chat_id})

    async def get_chat_member(self, chat_id: int | str, user_id: int) -> dict[str, Any]:
        return await self._call("getChatMember", {"chat_id": chat_id, "user_id": user_id})

    async def confirm_sign_in(self, chat_id: int, text: str) -> None:
        """Only for a private chat whose user just pressed Start on a valid sign-in code (see ingestion)."""
        await self._call("sendMessage", {"chat_id": chat_id, "text": text, "disable_web_page_preview": True})

    async def get_chat_member_count(self, chat_id: int) -> int:
        return int(await self._call("getChatMemberCount", {"chat_id": chat_id}))

    # --- Trading OS (app/tradingos): the bot answers commands in private chats with members who wrote to it first, and
    # posts / updates signal cards in the one signal group set in Admin → Trading OS. Nothing else is ever sent.
    async def send_message(self, chat_id: int | str, text: str, buttons: list[list[dict[str, str]]] | None = None, html: bool = False,
                           reply_to: int | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {"chat_id": chat_id, "text": text[:4096], "disable_web_page_preview": True}
        if buttons:
            params["reply_markup"] = {"inline_keyboard": buttons}
        if html:
            params["parse_mode"] = "HTML"
        if reply_to:
            params["reply_parameters"] = {"message_id": reply_to, "allow_sending_without_reply": True}
        return _sent(await self._call("sendMessage", params))

    async def send_photo(self, chat_id: int | str, png: bytes, caption: str, buttons: list[list[dict[str, str]]] | None = None,
                         reply_to: int | None = None, html: bool = False) -> dict[str, Any]:
        """A chart image with a caption (multipart upload: the PNG is generated here, never fetched from a URL)."""
        import json as _json

        data: dict[str, Any] = {"chat_id": str(chat_id), "caption": caption[:1024]}
        if html:
            data["parse_mode"] = "HTML"
        if buttons:
            data["reply_markup"] = _json.dumps({"inline_keyboard": buttons})
        if reply_to:
            data["reply_parameters"] = _json.dumps({"message_id": reply_to, "allow_sending_without_reply": True})
        return _sent(await self._call("sendPhoto", data, files={"photo": ("chart.png", png, "image/png")}))

    async def send_document(self, chat_id: int | str, content: bytes, filename: str, caption: str = "", html: bool = False,
                            mime: str = "text/plain") -> dict[str, Any]:
        """A small generated file (Kiwi VPN: a member's WireGuard .conf, only ever in their private chat with the bot)."""
        data: dict[str, Any] = {"chat_id": str(chat_id), "caption": caption[:1024]}
        if html:
            data["parse_mode"] = "HTML"
        url = f"{self.api_base}/bot{self._token}/sendDocument"
        try:
            response = await self._client.post(url, data=data, files={"document": (filename, content, mime)})
        except httpx.TransportError as exc:
            raise TelegramUnavailableError(f"Cannot reach Telegram ({type(exc).__name__})") from None
        try:
            body = response.json()
        except ValueError:
            raise TelegramUnavailableError(f"Telegram returned a non-JSON response (HTTP {response.status_code})") from None
        if not body.get("ok"):
            raise TelegramRequestError(str(body.get("description") or "sendDocument failed"))
        return _sent(body["result"])

    async def delete_message(self, chat_id: int | str, message_id: int) -> None:
        """Only the bot's own messages (tradingos.autodelete: the main chat's, after a few minutes)."""
        await self._call("deleteMessage", {"chat_id": chat_id, "message_id": message_id})

    async def edit_caption(self, chat_id: int | str, message_id: int, caption: str, buttons: list[list[dict[str, str]]] | None = None) -> None:
        params: dict[str, Any] = {"chat_id": chat_id, "message_id": message_id, "caption": caption[:1024]}
        params["reply_markup"] = {"inline_keyboard": buttons or []}
        await self._call("editMessageCaption", params)

    async def set_commands(self, commands: list[tuple[str, str]], scope: str = "default") -> None:
        """The "/" menu Telegram shows (scope: default = private chats, all_group_chats = groups)."""
        await self._call("setMyCommands", {"commands": [{"command": c, "description": d} for c, d in commands], "scope": {"type": scope}})

    async def answer_callback(self, callback_id: str, text: str, alert: bool = False) -> None:
        await self._call("answerCallbackQuery", {"callback_query_id": callback_id, "text": text[:200], "show_alert": alert})

    # --- VIP Trading Bot private group (app/tradingbot). Only ever called for the one group set in Admin → Trading Bot
    # Access, where the bot is an administrator with "Invite users" (and "Ban users" for revocation). They manage who
    # gets in; they never post anything.
    async def create_join_request_link(self, chat_id: int | str, name: str, expire_date: int) -> str:
        """A personal invite that only creates a join request (the bot then approves the one verified person)."""
        r = await self._call("createChatInviteLink", {"chat_id": chat_id, "name": name[:32], "expire_date": expire_date,
                                                      "creates_join_request": True})
        return str(r["invite_link"])

    async def create_invite_link(self, chat_id: int | str, name: str) -> str:
        """A named invite link to the community (Worker System: Telegram reports the link's name when someone joins with it)."""
        r = await self._call("createChatInviteLink", {"chat_id": chat_id, "name": name[:32]})
        return str(r["invite_link"])

    async def revoke_invite_link(self, chat_id: int | str, link: str) -> None:
        await self._call("revokeChatInviteLink", {"chat_id": chat_id, "invite_link": link})

    async def approve_join_request(self, chat_id: int | str, user_id: int) -> None:
        await self._call("approveChatJoinRequest", {"chat_id": chat_id, "user_id": user_id})

    async def decline_join_request(self, chat_id: int | str, user_id: int) -> None:
        await self._call("declineChatJoinRequest", {"chat_id": chat_id, "user_id": user_id})

    async def remove_member(self, chat_id: int | str, user_id: int) -> None:
        """Take a revoked member out of the group (ban, then unban: a removal, not a permanent ban)."""
        await self._call("banChatMember", {"chat_id": chat_id, "user_id": user_id})
        await self._call("unbanChatMember", {"chat_id": chat_id, "user_id": user_id, "only_if_banned": True})

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _call(self, method: str, params: dict[str, Any] | None = None, read_timeout: float | None = None,
                    files: dict[str, Any] | None = None) -> Any:
        # A 429 for a chat: no more requests to it until retry_after has passed (each early one only extends the limit).
        chat = str((params or {}).get("chat_id", ""))
        wait = self._flood_until.get(chat, 0.0) - time.monotonic() if chat else 0.0
        if wait > 0:
            raise TelegramRateLimitError(f"Telegram rate limit; retry after {math.ceil(wait)}s", math.ceil(wait))
        url = f"{self.api_base}/bot{self._token}/{method}"
        # an upload goes as multipart form fields (nested values JSON-encoded by the caller), everything else as JSON
        kwargs: dict[str, Any] = {"data": params or {}, "files": files} if files else {"json": params or {}}
        if read_timeout is not None:
            kwargs["timeout"] = httpx.Timeout(read_timeout, connect=10.0)
        try:
            response = await self._client.post(url, **kwargs)
        # `from None` everywhere: chained httpx errors carry the URL, which contains the token.
        except httpx.TimeoutException:
            raise TelegramUnavailableError(f"Telegram did not respond to {method} in time") from None
        except httpx.TransportError as exc:
            # str(exc) can include the URL (and so the token); report only the error type.
            raise TelegramUnavailableError(f"Cannot reach Telegram ({type(exc).__name__})") from None

        try:
            body = response.json()
        except ValueError:
            raise TelegramUnavailableError(f"Telegram returned a non-JSON response (HTTP {response.status_code})") from None

        if body.get("ok"):
            return body.get("result")

        code = int(body.get("error_code") or response.status_code)
        description = str(body.get("description") or "unknown error")
        if code == 401 or code == 404 and method == "getMe":
            raise TelegramAuthError("Telegram rejected the bot token. Check TELEGRAM_BOT_TOKEN in .env.")
        if code == 409:
            raise TelegramConflictError(
                "Another program is reading updates for this bot (or a webhook is set). "
                "Stop the other program or remove the webhook."
            )
        if code == 429:
            retry_after = int((body.get("parameters") or {}).get("retry_after") or 30)
            if chat:
                self._flood_until[chat] = time.monotonic() + retry_after
            raise TelegramRateLimitError(f"Telegram rate limit; retry after {retry_after}s", retry_after)
        if code >= 500:
            raise TelegramUnavailableError(f"Telegram server error {code}: {description}")
        raise TelegramRequestError(f"Telegram rejected {method}: {description}")
