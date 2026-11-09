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

import html as _html
import logging
import math
import re
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


TEXT_LIMIT, CAPTION_LIMIT = 4096, 1024  # Telegram's limits, in UTF-16 units of the text it shows (after the HTML is parsed)
_TOKEN = re.compile(r"<[^<>]*>|&#?\w+;|[^<&]+|[<&]")
_TAG = re.compile(r"<\s*(/?)\s*([a-zA-Z][\w-]*)")


def _u16(s: str) -> int:
    return len(s.encode("utf-16-le")) // 2


def fit_html(text: str, limit: int, ellipsis: str = "…") -> str:
    """Telegram HTML cut to `limit` shown characters without breaking it: never inside a tag or an entity, at the last
    space / line break where there is one, every tag still open closed again. Under the limit: unchanged."""
    tokens = _TOKEN.findall(text)
    if sum(0 if t.startswith("<") and t.endswith(">") and len(t) > 1 else 1 if t.startswith("&") and t.endswith(";") and len(t) > 1
           else _u16(t) for t in tokens) <= limit:
        return text
    budget = limit - _u16(ellipsis)
    out: list[str] = []
    stack: list[str] = []
    used = 0
    for t in tokens:
        if t.startswith("<") and t.endswith(">") and len(t) > 1:
            m = _TAG.match(t)
            if m and m.group(1):
                if m.group(2).lower() in stack:
                    while stack and stack.pop() != m.group(2).lower():
                        pass
            elif m and not t.endswith("/>"):
                stack.append(m.group(2).lower())
            out.append(t)
            continue
        n = 1 if t.startswith("&") and t.endswith(";") and len(t) > 1 else _u16(t)
        if used + n <= budget:
            out.append(t)
            used += n
            continue
        if not t.startswith("&"):  # plain text: as much as fits, back to the last space / line break in it
            part, size = "", used
            for ch in t:
                if size + _u16(ch) > budget:
                    break
                part, size = part + ch, size + _u16(ch)
            space = max(part.rfind(" "), part.rfind("\n"))
            out.append(part[:space] if space > 0 else part)
        break
    while out and not out[-1].strip() and not out[-1].startswith("<"):
        out.pop()
    return "".join(out).rstrip() + ellipsis + "".join(f"</{name}>" for name in reversed(stack))


def fit_text(text: str, limit: int, html: bool) -> str:
    """HTML: fit_html. Plain text can't break, so it is only cut (as before)."""
    return fit_html(text, limit) if html else text[:limit]


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

    async def get_chat_administrators(self, chat_id: int | str) -> list[dict[str, Any]]:
        return list(await self._call("getChatAdministrators", {"chat_id": chat_id}) or [])

    async def restrict_member(self, chat_id: int | str, user_id: int, until: int, can_send: bool) -> None:
        """Group rules (app/telegram/moderation.py): mute (can_send False) until a Unix time, 0 = for good; or lift a mute."""
        keys = ("can_send_messages", "can_send_audios", "can_send_documents", "can_send_photos", "can_send_videos", "can_send_video_notes",
                "can_send_voice_notes", "can_send_polls", "can_send_other_messages", "can_add_web_page_previews")
        # lifting a mute: Telegram ends a restriction only when every permission is true (the group's own defaults still apply)
        perms = {k: can_send for k in keys + (("can_change_info", "can_invite_users", "can_pin_messages", "can_manage_topics") if can_send else ())}
        await self._call("restrictChatMember", {"chat_id": chat_id, "user_id": user_id, "permissions": perms, "until_date": until,
                                                "use_independent_chat_permissions": True})

    async def get_chat_member_count(self, chat_id: int) -> int:
        return int(await self._call("getChatMemberCount", {"chat_id": chat_id}))

    # --- Trading OS (app/tradingos): the bot answers commands in private chats with members who wrote to it first, and
    # posts / updates signal cards in the one signal group set in Admin → Trading OS. Nothing else is ever sent.
    # The News Desk's Telegram lane (app/newsdesk/telegram) also posts here, to the one announcement channel, after checking the bot's rights.
    async def send_message(self, chat_id: int | str, text: str, buttons: list[list[dict[str, str]]] | None = None, html: bool = False,
                           reply_to: int | None = None, silent: bool = False) -> dict[str, Any]:
        params: dict[str, Any] = {"chat_id": chat_id, "text": fit_text(text, TEXT_LIMIT, html), "disable_web_page_preview": True}
        if buttons:
            params["reply_markup"] = {"inline_keyboard": buttons}
        if html:
            params["parse_mode"] = "HTML"
        if reply_to:
            params["reply_parameters"] = {"message_id": reply_to, "allow_sending_without_reply": True}
        if silent:
            params["disable_notification"] = True
        return _sent(await self._call("sendMessage", params))

    async def send_photo(self, chat_id: int | str, png: bytes, caption: str, buttons: list[list[dict[str, str]]] | None = None,
                         reply_to: int | None = None, html: bool = False, silent: bool = False) -> dict[str, Any]:
        """A chart image or a news card with a caption (multipart upload: the PNG is generated here, never fetched from a URL)."""
        import json as _json

        data: dict[str, Any] = {"chat_id": str(chat_id), "caption": fit_text(caption, CAPTION_LIMIT, html)}
        if html:
            data["parse_mode"] = "HTML"
        if buttons:
            data["reply_markup"] = _json.dumps({"inline_keyboard": buttons})
        if reply_to:
            data["reply_parameters"] = _json.dumps({"message_id": reply_to, "allow_sending_without_reply": True})
        if silent:
            data["disable_notification"] = "true"
        kind = ("photo.jpg", "image/jpeg") if png[:3] == b"\xff\xd8\xff" else ("chart.png", "image/png")
        return _sent(await self._call("sendPhoto", data, files={"photo": (kind[0], png, kind[1])}))

    async def send_document(self, chat_id: int | str, content: bytes, filename: str, caption: str = "", html: bool = False,
                            mime: str = "text/plain") -> dict[str, Any]:
        """A small generated file (Kiwi VPN: a member's WireGuard .conf, only ever in their private chat with the bot)."""
        data: dict[str, Any] = {"chat_id": str(chat_id), "caption": fit_text(caption, CAPTION_LIMIT, html)}
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

    async def edit_message_text(self, chat_id: int | str, message_id: int, text: str, html: bool = False,
                                buttons: list[list[dict[str, str]]] | None = None) -> dict[str, Any]:
        """Rewrite one of the bot's own text messages (Vortex trade checks: the placeholder becomes the answer; the committee:
        the placeholder becomes the summary, and its buttons switch views). `buttons` replaces the inline keyboard."""
        params: dict[str, Any] = {"chat_id": chat_id, "message_id": message_id, "text": fit_text(text, TEXT_LIMIT, html), "disable_web_page_preview": True}
        if html:
            params["parse_mode"] = "HTML"
        if buttons is not None:
            params["reply_markup"] = {"inline_keyboard": buttons}
        result = await self._call("editMessageText", params)
        return _sent(result) if isinstance(result, dict) else {}

    async def edit_caption(self, chat_id: int | str, message_id: int, caption: str, buttons: list[list[dict[str, str]]] | None = None) -> None:
        params: dict[str, Any] = {"chat_id": chat_id, "message_id": message_id, "caption": fit_text(caption, CAPTION_LIMIT, False)}
        params["reply_markup"] = {"inline_keyboard": buttons or []}
        await self._call("editMessageCaption", params)

    async def edit_photo(self, chat_id: int | str, message_id: int, png: bytes, caption: str,
                         buttons: list[list[dict[str, str]]] | None = None, html: bool = False) -> None:
        """Replace one of the bot's own chart photos in place (an "fc" chart's timeframe buttons): new image, caption and
        buttons. Multipart: the PNG is generated here, never fetched from a URL."""
        import json as _json

        media: dict[str, Any] = {"type": "photo", "media": "attach://chart", "caption": fit_text(caption, CAPTION_LIMIT, html)}
        if html:
            media["parse_mode"] = "HTML"
        data: dict[str, Any] = {"chat_id": str(chat_id), "message_id": str(message_id), "media": _json.dumps(media),
                                "reply_markup": _json.dumps({"inline_keyboard": buttons or []})}
        await self._call("editMessageMedia", data, files={"chart": ("chart.png", png, "image/png")})

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
