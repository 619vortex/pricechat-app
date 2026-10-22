"""The only code that talks to Telegram for the Invite Bot (Telethon, official client API). Swappable for tests."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol


# Telegram's own accounts: 777000 = "Telegram" service notifications (login codes!), 42777 = Telegram support/system.
SERVICE_IDS = frozenset({777000, 42777, 333000})


def is_real_person(user: Any) -> bool:
    """Only a real, active, ordinary user can opt in: never Telegram's service/support accounts, bots, deleted,
    scam or fake accounts, or ourselves."""
    return not (getattr(user, "id", None) in SERVICE_IDS or getattr(user, "bot", False) or getattr(user, "support", False)
                or getattr(user, "deleted", False) or getattr(user, "scam", False) or getattr(user, "fake", False)
                or getattr(user, "is_self", False))


def public_username(user: Any) -> str | None:
    """The user's public @username: the classic field, or the first active one of a multi-username account."""
    if getattr(user, "username", None):
        return str(user.username)
    for u in getattr(user, "usernames", None) or []:
        if getattr(u, "active", True) and getattr(u, "username", None):
            return str(u.username)
    return None


class RateLimited(Exception):
    """Telegram asked us to slow down / flagged the account: stop everything."""

    def __init__(self, seconds: int, reason: str) -> None:
        super().__init__(reason)
        self.seconds = seconds


class Unreachable(Exception):
    """This person can't / shouldn't be messaged (blocked us, privacy settings, deleted account)."""


class NotAllowed(Exception):
    """The account may no longer post in (or read) this group."""


class AccountGone(Exception):
    """This account's session is dead (logged out elsewhere, revoked, deleted, duplicated key): stop using it."""


# Telegram restricted the ACCOUNT itself (not one recipient): pause everything for this long.
RESTRICTED_SECONDS = 24 * 3600
BANNED_SECONDS = 7 * 24 * 3600


def account_error(exc: BaseException) -> Exception | None:
    """The account-level meaning of a Telethon error (any call), or None when it's about one chat/recipient.
    Never retried: a flood wait is never slept through and re-sent (flood_sleep_threshold=0 below)."""
    try:
        from telethon import errors
    except ImportError:  # pragma: no cover - telethon is a production dependency
        return None
    if isinstance(exc, errors.FrozenMethodInvalidError):  # the account is frozen by Telegram (spam): long pause
        return RateLimited(BANNED_SECONDS, "Telegram froze this account (FrozenMethodInvalid)")
    if isinstance(exc, errors.UserDeactivatedBanError):  # banned: everything pauses, the owner must look
        return RateLimited(BANNED_SECONDS, "Telegram banned this account (UserDeactivatedBan)")
    if isinstance(exc, errors.FloodError):  # FloodWait, FloodPremiumWait, SlowModeWait, ...
        seconds = int(getattr(exc, "seconds", 0) or 0) or RESTRICTED_SECONDS
        return RateLimited(seconds, f"Telegram rate limit ({type(exc).__name__}): wait {seconds}s")
    if isinstance(exc, (errors.PeerFloodError, errors.UserBannedInChannelError)):  # spam-restricted account
        return RateLimited(RESTRICTED_SECONDS, f"Telegram limited messaging ({type(exc).__name__})")
    if isinstance(exc, (errors.UnauthorizedError, errors.AuthKeyError)):  # AuthKeyUnregistered, SessionRevoked, UserDeactivated...
        return AccountGone(f"The Telegram session no longer works ({type(exc).__name__}): log in again")
    return None


class NeedPassword(Exception):
    pass


@dataclass
class GroupInfo:
    chat_id: int
    title: str
    username: str | None


@dataclass
class Msg:
    id: int
    sender_id: int
    username: str | None
    text: str
    at: datetime
    is_bot: bool = False


class Gateway(Protocol):
    async def start(self) -> bool: ...  # connects; True when the session is authorised
    async def send_code(self, phone: str) -> str: ...
    async def sign_in(self, phone: str, code: str, code_hash: str) -> None: ...
    async def sign_in_password(self, password: str) -> None: ...
    async def me(self) -> tuple[int, str | None]: ...
    def session(self) -> str: ...
    async def groups(self) -> list[GroupInfo]: ...
    async def group_messages(self, chat_id: int, after_id: int, limit: int) -> list[Msg]: ...
    async def private_messages(self, since: datetime, limit: int) -> list[Msg]: ...
    async def send(self, peer: int | str, text: str, expect_id: int | None = None) -> None: ...
    async def stop(self) -> None: ...


class TelethonGateway:
    def __init__(self, api_id: int, api_hash: str, session: str = "") -> None:
        from telethon import TelegramClient
        from telethon.sessions import StringSession

        # flood_sleep_threshold=0: Telethon must never sleep through a FloodWait and silently re-send; we stop instead
        self.client = TelegramClient(StringSession(session), api_id, api_hash, device_model="Price Chat Invite Bot", app_version="1.0",
                                     flood_sleep_threshold=0)

    async def start(self) -> bool:
        try:
            await self.client.connect()
            return bool(await self.client.is_user_authorized())
        except Exception as exc:
            _raise_mapped(exc)
            raise

    async def send_code(self, phone: str) -> str:
        return str((await self.client.send_code_request(phone)).phone_code_hash)

    async def sign_in(self, phone: str, code: str, code_hash: str) -> None:
        from telethon.errors import SessionPasswordNeededError

        try:
            await self.client.sign_in(phone=phone, code=code, phone_code_hash=code_hash)
        except SessionPasswordNeededError as exc:
            raise NeedPassword() from exc

    async def sign_in_password(self, password: str) -> None:
        await self.client.sign_in(password=password)

    async def me(self) -> tuple[int, str | None]:
        m = await self.client.get_me()
        return int(m.id), m.username

    def session(self) -> str:
        return str(self.client.session.save())

    async def groups(self) -> list[GroupInfo]:
        from telethon.utils import get_peer_id

        out = []
        try:
            async for d in self.client.iter_dialogs(limit=300):
                if d.is_group or (d.is_channel and getattr(d.entity, "megagroup", False)):
                    out.append(GroupInfo(get_peer_id(d.entity), d.name or "?", getattr(d.entity, "username", None)))
        except Exception as exc:
            _raise_mapped(exc)
        return out

    async def group_messages(self, chat_id: int, after_id: int, limit: int) -> list[Msg]:
        from telethon import errors
        from telethon.tl.types import User

        out = []
        try:
            async for m in self.client.iter_messages(chat_id, min_id=after_id, limit=limit):
                s = m.sender
                if isinstance(s, User):
                    out.append(Msg(m.id, s.id, public_username(s), m.raw_text or "", m.date, not is_real_person(s)))
        except (errors.ChannelPrivateError, errors.ChatAdminRequiredError, errors.ChatForbiddenError, errors.ChannelInvalidError,
                errors.ChatIdInvalidError, ValueError) as exc:
            raise NotAllowed(type(exc).__name__) from exc  # left / kicked / gone: that one group, not the account
        except Exception as exc:
            _raise_mapped(exc)
        return out

    async def private_messages(self, since: datetime, limit: int) -> list[Msg]:
        from telethon.tl.types import User

        out = []
        try:
            async for d in self.client.iter_dialogs(limit=limit):
                if not d.is_user or not isinstance(d.entity, User) or not is_real_person(d.entity):
                    continue  # e.g. the "Telegram" account that sent the login code is NOT someone asking for an invite
                if d.message is None or d.message.date <= since:
                    continue
                async for m in self.client.iter_messages(d.entity, limit=5):
                    if m.date <= since:
                        break
                    if not m.out:
                        out.append(Msg(m.id, d.entity.id, public_username(d.entity), m.raw_text or "", m.date))
        except Exception as exc:
            _raise_mapped(exc)
        return out

    async def send(self, peer: int | str, text: str, expect_id: int | None = None) -> None:
        """`expect_id`: the person who asked. A @username can change hands: if it now resolves to anyone else, nothing is sent."""
        from telethon import errors

        try:
            if expect_id is not None and isinstance(peer, str):
                peer = await self.client.get_entity(peer)
                if getattr(peer, "id", None) != expect_id:
                    raise Unreachable("UsernameChangedOwner")
            await self.client.send_message(peer, text, link_preview=False)
        except Unreachable:
            raise
        except Exception as exc:
            mapped = account_error(exc)  # first: e.g. UserDeactivated / UserBannedInChannel are about THIS account, not the recipient
            if mapped is not None:
                raise mapped from exc
            if isinstance(exc, (errors.UserIsBlockedError, errors.UserPrivacyRestrictedError, errors.InputUserDeactivatedError,
                                errors.UsernameNotOccupiedError, errors.UsernameInvalidError, errors.PeerIdInvalidError, ValueError)):
                raise Unreachable(type(exc).__name__) from exc
            if isinstance(exc, (errors.ChatWriteForbiddenError, errors.ChatAdminRequiredError, errors.ChannelPrivateError,
                                errors.ChatRestrictedError, errors.ChatSendPlainForbiddenError)):
                raise NotAllowed(type(exc).__name__) from exc
            raise

    async def stop(self) -> None:
        await self.client.disconnect()


def _raise_mapped(exc: Exception) -> None:
    mapped = account_error(exc)
    if mapped is not None:
        raise mapped from exc
    raise exc


def default_factory(api_id: int, api_hash: str, session: str = "") -> Any:
    return TelethonGateway(api_id, api_hash, session)
