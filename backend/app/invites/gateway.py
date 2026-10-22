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
    """The account may no longer post in this group."""


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
    async def send(self, peer: int | str, text: str) -> None: ...
    async def stop(self) -> None: ...


class TelethonGateway:
    def __init__(self, api_id: int, api_hash: str, session: str = "") -> None:
        from telethon import TelegramClient
        from telethon.sessions import StringSession

        self.client = TelegramClient(StringSession(session), api_id, api_hash, device_model="Price Chat Invite Bot", app_version="1.0")

    async def start(self) -> bool:
        await self.client.connect()
        return bool(await self.client.is_user_authorized())

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
        async for d in self.client.iter_dialogs(limit=300):
            if d.is_group or (d.is_channel and getattr(d.entity, "megagroup", False)):
                out.append(GroupInfo(get_peer_id(d.entity), d.name or "?", getattr(d.entity, "username", None)))
        return out

    async def group_messages(self, chat_id: int, after_id: int, limit: int) -> list[Msg]:
        from telethon.tl.types import User

        out = []
        async for m in self.client.iter_messages(chat_id, min_id=after_id, limit=limit):
            s = m.sender
            if isinstance(s, User):
                out.append(Msg(m.id, s.id, public_username(s), m.raw_text or "", m.date, not is_real_person(s)))
        return out

    async def private_messages(self, since: datetime, limit: int) -> list[Msg]:
        from telethon.tl.types import User

        out = []
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
        return out

    async def send(self, peer: int | str, text: str) -> None:
        from telethon import errors

        try:
            await self.client.send_message(peer, text, link_preview=False)
        except errors.FloodWaitError as exc:
            raise RateLimited(int(exc.seconds), f"Telegram rate limit: wait {exc.seconds}s") from exc
        except (errors.PeerFloodError, errors.SlowModeWaitError) as exc:
            raise RateLimited(24 * 3600, f"Telegram limited messaging ({type(exc).__name__})") from exc
        except (errors.UserIsBlockedError, errors.UserPrivacyRestrictedError, errors.InputUserDeactivatedError, errors.UserDeactivatedError,
                ValueError) as exc:
            raise Unreachable(type(exc).__name__) from exc
        except (errors.ChatWriteForbiddenError, errors.ChatAdminRequiredError, errors.ChannelPrivateError) as exc:
            raise NotAllowed(type(exc).__name__) from exc

    async def stop(self) -> None:
        await self.client.disconnect()


def default_factory(api_id: int, api_hash: str, session: str = "") -> Any:
    return TelethonGateway(api_id, api_hash, session)
