"""Turn raw Bot API updates into the minimal fields Price Chat keeps.

Data minimisation: we keep text, timing, thread/reply structure, the author's
Telegram id and display name, forward origin name and a media *type* label.
No phone numbers, bios, profile photos, files or contact cards.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.telegram.errors import MalformedUpdateError

MESSAGE_KEYS = ("message", "edited_message", "channel_post", "edited_channel_post")
MEDIA_KEYS = (
    "photo", "video", "animation", "document", "audio", "voice", "video_note",
    "sticker", "poll", "location", "venue", "contact", "dice", "story",
)
MAX_TEXT = 8000
TELEGRAM_SERVICE_ID = 777000  # "Telegram": the sender of a channel's automatic forwards into its discussion group


@dataclass(frozen=True, slots=True)
class ChatInfo:
    chat_id: int
    title: str
    chat_type: str
    username: str | None


@dataclass(frozen=True, slots=True)
class ParsedMessage:
    update_id: int
    chat: ChatInfo
    message_id: int
    edited: bool
    thread_id: int | None
    reply_to_message_id: int | None
    author_id: int | None
    author_name: str | None
    author_is_bot: bool
    sent_at: datetime
    edited_at: datetime | None
    text: str
    media_type: str | None
    forward_from: str | None
    forward_date: datetime | None
    # sent "as the group" or by an admin with "Remain anonymous" (Telegram shows @GroupAnonymousBot) or as the linked channel
    # (@Channel_Bot): `from` is one of Telegram's stand-in bots and `sender_chat` the group or channel; there is no person
    author_is_anonymous: bool = False
    # who wrote the message this one replies to: an admin's /warn mutes that person (app/telegram/moderation.py)
    reply_author_id: int | None = None
    reply_author_name: str | None = None
    # a channel's own post (channel_post), or its automatic copy in the linked discussion group ("from" Telegram, 777000):
    # no person wrote it here, so the bot never answers it as a command
    channel_post: bool = False
    automatic_forward: bool = False
    # sent as the group itself: an anonymous admin (sender_chat is this chat). Anyone may post "as" their own channel, which is
    # NOT this, and carries no admin rights (2026-11-09 review: channel senders could /warn anyone)
    as_group: bool = False
    as_channel: bool = False


@dataclass(frozen=True, slots=True)
class MembershipChange:
    """The bot was added to / removed from a chat (used only to help the user find chat ids)."""

    update_id: int
    chat: ChatInfo
    status: str


def _ts(value: Any) -> datetime | None:
    if value is None:
        return None
    return datetime.fromtimestamp(int(value), tz=timezone.utc)


def _chat(raw: dict[str, Any]) -> ChatInfo:
    chat_id = raw.get("id")
    if not isinstance(chat_id, int):
        raise MalformedUpdateError("chat without numeric id")
    title = raw.get("title") or raw.get("username") or " ".join(
        p for p in (raw.get("first_name"), raw.get("last_name")) if p
    )
    return ChatInfo(chat_id, str(title or chat_id)[:256], str(raw.get("type") or "unknown"), raw.get("username"))


def _person_name(user: dict[str, Any]) -> str | None:
    name = " ".join(p for p in (user.get("first_name"), user.get("last_name")) if p) or user.get("username")
    return str(name)[:128] if name else None


def _forward(msg: dict[str, Any]) -> tuple[str | None, datetime | None]:
    origin = msg.get("forward_origin")
    if isinstance(origin, dict):
        kind = origin.get("type")
        if kind == "user":
            name = _person_name(origin.get("sender_user") or {})
        elif kind == "hidden_user":
            name = origin.get("sender_user_name")
        else:  # chat / channel
            name = (origin.get("chat") or origin.get("sender_chat") or {}).get("title")
        return (str(name)[:256] if name else "unknown"), _ts(origin.get("date"))
    # Pre-7.0 Bot API fields
    if "forward_date" in msg:
        name = (
            _person_name(msg.get("forward_from") or {})
            or (msg.get("forward_from_chat") or {}).get("title")
            or msg.get("forward_sender_name")
        )
        return (str(name)[:256] if name else "unknown"), _ts(msg.get("forward_date"))
    return None, None


def parse_update(update: dict[str, Any]) -> ParsedMessage | MembershipChange | None:
    """Return the parsed message, a membership change, or None for updates we don't use."""
    if not isinstance(update, dict) or not isinstance(update.get("update_id"), int):
        raise MalformedUpdateError("update without update_id")
    update_id = update["update_id"]

    if "my_chat_member" in update:
        change = update["my_chat_member"] or {}
        status = ((change.get("new_chat_member") or {}).get("status")) or "unknown"
        return MembershipChange(update_id, _chat(change.get("chat") or {}), str(status))

    key = next((k for k in MESSAGE_KEYS if k in update), None)
    if key is None:
        return None
    msg = update[key]
    if not isinstance(msg, dict) or not isinstance(msg.get("message_id"), int) or "date" not in msg:
        raise MalformedUpdateError(f"{key} without message_id/date")

    text = msg.get("text") or msg.get("caption") or ""
    if not isinstance(text, str):
        raise MalformedUpdateError("non-string text")
    media_type = next((m for m in MEDIA_KEYS if m in msg), None)
    if not text.strip() and media_type is None:
        return None  # service messages (joins, pins, title changes…) carry no community content

    sender = msg.get("from") or {}
    sender_chat = msg.get("sender_chat") or {}
    author_id = sender.get("id") if isinstance(sender.get("id"), int) else sender_chat.get("id")
    author_name = _person_name(sender) if sender else sender_chat.get("title")
    forward_from, forward_date = _forward(msg)
    reply = msg.get("reply_to_message") or {}
    if reply.get("forum_topic_created") or (msg.get("is_topic_message") and reply.get("message_id") == msg.get("message_thread_id")):
        reply = {}  # in a forum, every message "replies" to its topic's opening service message: that's not a reply to a person
    chat_id = (msg.get("chat") or {}).get("id")
    auto = bool(msg.get("is_automatic_forward")) or sender.get("id") == TELEGRAM_SERVICE_ID

    return ParsedMessage(
        update_id=update_id,
        chat=_chat(msg.get("chat") or {}),
        message_id=msg["message_id"],
        edited=key.startswith("edited_"),
        thread_id=msg.get("message_thread_id") if msg.get("is_topic_message") else None,
        reply_to_message_id=(msg.get("reply_to_message") or {}).get("message_id") if isinstance((msg.get("reply_to_message") or {}).get("message_id"), int) else None,
        author_id=author_id if isinstance(author_id, int) else None,
        author_name=author_name,
        author_is_bot=bool(sender.get("is_bot")),
        author_is_anonymous=bool(sender.get("is_bot")) and bool(sender_chat.get("id")),
        sent_at=_ts(msg["date"]) or datetime.now(timezone.utc),
        edited_at=_ts(msg.get("edit_date")),
        text=text[:MAX_TEXT],
        media_type=media_type,
        forward_from=forward_from,
        forward_date=forward_date,
        reply_author_id=(reply.get("from") or {}).get("id") if isinstance((reply.get("from") or {}).get("id"), int) else None,
        reply_author_name=_person_name(reply["from"]) if isinstance(reply.get("from"), dict) else None,
        channel_post=key.endswith("channel_post"),
        automatic_forward=auto,
        as_group=bool(sender_chat.get("id")) and sender_chat.get("id") == chat_id,
        as_channel=bool(sender_chat.get("id")) and sender_chat.get("id") != chat_id and not auto,
    )
