"""Telegram account mode: read authorised groups/channels with a user session (official client API, MTProto).

Uses Telethon, an established client for Telegram's official client API. Price Chat only:
- logs in with the standard phone → code → (2FA password) flow,
- lists *groups and channels* (never private one-to-one chats),
- reads messages from chats the human explicitly authorised (the allow-list is enforced by the caller),
- never sends messages, never downloads media, never reads contacts.

`AccountGateway` is the small surface Price Chat needs; tests use an in-memory fake.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

from app.telegram.parser import ChatInfo, ParsedMessage


class AccountError(Exception):
    code = "telegram_account"


class PasswordNeeded(AccountError):
    code = "telegram_2fa_required"


class InvalidCode(AccountError):
    code = "telegram_code_invalid"


class CodeExpired(AccountError):
    code = "telegram_code_expired"


class InvalidPassword(AccountError):
    code = "telegram_password_invalid"


class InvalidPhone(AccountError):
    code = "telegram_phone_invalid"


class InvalidApiCredentials(AccountError):
    code = "telegram_api_invalid"


class SessionRevoked(AccountError):
    code = "telegram_session_revoked"


class FloodWait(AccountError):
    code = "telegram_flood_wait"

    def __init__(self, seconds: int) -> None:
        super().__init__(f"Telegram asked Price Chat to wait {seconds} seconds before the next request")
        self.seconds = seconds


@dataclass(frozen=True, slots=True)
class DialogInfo:
    chat_id: int  # marked id, same format as the Bot API (-100... for supergroups/channels)
    title: str
    chat_type: str  # group | supergroup | channel
    username: str | None
    members: int | None


@dataclass(frozen=True, slots=True)
class AccountIdentity:
    user_id: int
    username: str | None
    display_name: str
    phone_hint: str | None


class AccountGateway(Protocol):
    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    async def is_authorized(self) -> bool: ...
    async def send_code(self, phone: str) -> str: ...  # returns phone_code_hash
    async def sign_in_code(self, phone: str, code: str, phone_code_hash: str) -> None: ...
    async def sign_in_password(self, password: str) -> None: ...
    async def qr_check(self) -> tuple[bool, str | None]: ...  # (logged in?, the QR login URL while waiting)
    async def me(self) -> AccountIdentity: ...
    async def dialogs(self, limit: int = 200) -> list[DialogInfo]: ...
    async def messages(self, chat: DialogInfo, *, min_id: int = 0, since: datetime | None = None, limit: int = 500) -> list[ParsedMessage]: ...
    async def estimate_since(self, chat: DialogInfo, since: datetime) -> int | None: ...
    async def log_out(self) -> None: ...
    def session_string(self) -> str: ...


def _media_type(m: Any) -> str | None:
    for attr, name in (("photo", "photo"), ("video", "video"), ("voice", "voice"), ("audio", "audio"), ("sticker", "sticker"),
                       ("gif", "animation"), ("poll", "poll"), ("document", "document")):
        if getattr(m, attr, None):
            return name
    return "other" if getattr(m, "media", None) else None


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def to_parsed(m: Any, chat: DialogInfo) -> ParsedMessage | None:
    """Telethon Message → the same ParsedMessage the bot path stores. Service messages are skipped."""
    if getattr(m, "action", None) is not None or not getattr(m, "date", None):
        return None
    sender = getattr(m, "sender", None)
    if sender is not None and getattr(sender, "title", None):
        author_name = sender.title  # posted as a channel
    elif sender is not None:
        author_name = " ".join(p for p in (getattr(sender, "first_name", None), getattr(sender, "last_name", None)) if p) or getattr(sender, "username", None)
    else:
        author_name = None
    reply = getattr(m, "reply_to", None)
    reply_id = getattr(reply, "reply_to_msg_id", None) if reply else None
    thread_id = (getattr(reply, "reply_to_top_id", None) or reply_id) if reply is not None and getattr(reply, "forum_topic", False) else None
    fwd = getattr(m, "fwd_from", None)
    forward_from = None
    if fwd is not None:
        forward_from = getattr(fwd, "from_name", None) or ("forwarded" if getattr(fwd, "from_id", None) else None)
    return ParsedMessage(
        update_id=0, chat=ChatInfo(chat.chat_id, chat.title, chat.chat_type, chat.username), message_id=int(m.id),
        edited=bool(getattr(m, "edit_date", None)), thread_id=thread_id, reply_to_message_id=reply_id,
        author_id=getattr(m, "sender_id", None), author_name=(author_name or None) and str(author_name)[:128],
        author_is_bot=bool(getattr(sender, "bot", False)), sent_at=_aware(m.date), edited_at=_aware(getattr(m, "edit_date", None)),  # type: ignore[arg-type]
        text=(getattr(m, "message", None) or "")[:20000], media_type=_media_type(m),
        forward_from=(forward_from or None) and str(forward_from)[:256], forward_date=_aware(getattr(fwd, "date", None)) if fwd else None,
    )


class TelethonGateway:
    """The real gateway (official Telegram client API through Telethon)."""

    def __init__(self, api_id: int, api_hash: str, session: str | None = None) -> None:
        from telethon import TelegramClient
        from telethon.sessions import StringSession

        # flood_sleep_threshold=0: never sleep silently; FloodWait is reported to the user instead.
        self._client = TelegramClient(StringSession(session or ""), api_id, api_hash, flood_sleep_threshold=0,
                                      receive_updates=False, device_model="Price Chat", app_version="1.0")
        self._api = (api_id, api_hash)
        self._entities: dict[int, Any] = {}

    def session_string(self) -> str:
        return self._client.session.save()

    async def _call(self, coro):  # type: ignore[no-untyped-def]
        from telethon import errors

        try:
            return await coro
        except errors.FloodWaitError as exc:
            raise FloodWait(int(exc.seconds)) from None
        except (errors.AuthKeyUnregisteredError, errors.SessionRevokedError, errors.UserDeactivatedError) as exc:
            raise SessionRevoked("The Telegram session is no longer valid; log in again") from exc
        except errors.ApiIdInvalidError as exc:
            raise InvalidApiCredentials("Telegram rejected the API ID / API Hash") from exc

    async def connect(self) -> None:
        await self._call(self._client.connect())

    async def disconnect(self) -> None:
        await self._client.disconnect()

    async def is_authorized(self) -> bool:
        return bool(await self._call(self._client.is_user_authorized()))

    async def send_code(self, phone: str) -> str:
        from telethon import errors

        try:
            sent = await self._call(self._client.send_code_request(phone))
        except (errors.PhoneNumberInvalidError, errors.PhoneNumberBannedError) as exc:
            raise InvalidPhone("Telegram did not accept this phone number") from exc
        return sent.phone_code_hash

    async def sign_in_code(self, phone: str, code: str, phone_code_hash: str) -> None:
        from telethon import errors

        try:
            await self._call(self._client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash))
        except errors.SessionPasswordNeededError:
            raise PasswordNeeded("This account has two-step verification: enter the password") from None
        except errors.PhoneCodeInvalidError:
            raise InvalidCode("That code is not correct") from None
        except errors.PhoneCodeExpiredError:
            raise CodeExpired("That code has expired; request a new one") from None

    async def sign_in_password(self, password: str) -> None:
        from telethon import errors

        try:
            await self._call(self._client.sign_in(password=password))
        except errors.PasswordHashInvalidError:
            raise InvalidPassword("That two-step verification password is not correct") from None

    async def qr_check(self) -> tuple[bool, str | None]:
        """QR login ("Link Desktop Device" in the Telegram app), polled: no login code needed. Each call asks Telegram for
        the login token: while nobody has scanned it, it's a token to show as a QR (tg://login?token=...); once the member
        scans and approves it on their phone, it's a success and this client is logged in. Polling (not update events)
        because this client never receives updates."""
        import base64

        from telethon import errors, functions, types

        try:
            r = await self._call(self._client(functions.auth.ExportLoginTokenRequest(api_id=self._api[0], api_hash=self._api[1], except_ids=[])))
            if isinstance(r, types.auth.LoginTokenMigrateTo):  # the account lives on another data centre
                await self._client._switch_dc(r.dc_id)  # noqa: SLF001 - Telethon's own DC switch
                r = await self._call(self._client(functions.auth.ImportLoginTokenRequest(token=r.token)))
        except errors.SessionPasswordNeededError:
            raise PasswordNeeded("This account has two-step verification: enter the password") from None
        if isinstance(r, types.auth.LoginTokenSuccess):
            await self._client._on_login(r.authorization.user)  # noqa: SLF001 - marks the session as logged in
            return True, None
        token = base64.urlsafe_b64encode(r.token).decode().rstrip("=")
        return False, f"tg://login?token={token}"

    async def me(self) -> AccountIdentity:
        u = await self._call(self._client.get_me())
        name = " ".join(p for p in (u.first_name, u.last_name) if p) or (u.username or str(u.id))
        phone = getattr(u, "phone", None)
        return AccountIdentity(int(u.id), u.username, name, f"••••{phone[-2:]}" if phone else None)

    async def dialogs(self, limit: int = 200) -> list[DialogInfo]:
        from telethon.tl.types import Channel, Chat

        out: list[DialogInfo] = []
        dialogs = await self._call(self._client.get_dialogs(limit=limit))
        for d in dialogs:
            e = d.entity
            if isinstance(e, Chat):
                kind = "group"
            elif isinstance(e, Channel):
                kind = "supergroup" if getattr(e, "megagroup", False) else "channel"
            else:
                continue  # private one-to-one chats are never listed
            self._entities[int(d.id)] = e
            out.append(DialogInfo(int(d.id), d.name or "", kind, getattr(e, "username", None), getattr(e, "participants_count", None)))
        return out

    async def _entity(self, chat: DialogInfo):  # type: ignore[no-untyped-def]
        if chat.chat_id not in self._entities:
            await self.dialogs()  # string sessions don't persist entities; the dialog list resolves them
        if chat.chat_id not in self._entities:
            raise AccountError(f"'{chat.title}' is no longer accessible to this Telegram account")
        return self._entities[chat.chat_id]

    async def messages(self, chat: DialogInfo, *, min_id: int = 0, since: datetime | None = None, limit: int = 500) -> list[ParsedMessage]:
        entity = await self._entity(chat)
        out: list[ParsedMessage] = []
        it = self._client.iter_messages(entity, limit=limit, min_id=min_id or 0)
        try:
            async for m in it:
                if since is not None and _aware(m.date) < since:  # type: ignore[operator]
                    break
                p = to_parsed(m, chat)
                if p is not None:
                    out.append(p)
        except Exception as exc:  # noqa: BLE001 - mapped below
            from telethon import errors

            if isinstance(exc, errors.FloodWaitError):
                raise FloodWait(int(exc.seconds)) from None
            if isinstance(exc, errors.ChannelPrivateError):
                raise AccountError(f"No longer allowed to read '{chat.title}'") from None
            raise
        return list(reversed(out))  # oldest first

    async def estimate_since(self, chat: DialogInfo, since: datetime) -> int | None:
        entity = await self._entity(chat)
        latest = await self._call(self._client.get_messages(entity, limit=1))
        before = await self._call(self._client.get_messages(entity, limit=1, offset_date=since))
        if not latest:
            return 0
        top = latest[0].id
        bottom = before[0].id if before else 0
        return max(0, int(top) - int(bottom))  # upper bound: ids also count deleted and service messages

    async def log_out(self) -> None:
        await self._call(self._client.log_out())
