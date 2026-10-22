"""Main-chat tidy-up: the bot's own messages in the main community chat (Admin › Trading OS › main chat) are deleted a few
minutes after they're sent (default 5), so command answers and notices don't pile up there. Bot Chat (the signal group),
private chats and every other chat are never touched, even when the main chat is set to the same chat as Bot Chat.

Every message the Telegram client sends is noted in memory (client.SENT_HOOKS, installed at startup). The job
tradingos.autodelete (every minute) keeps the main chat's ones in ts_autodelete, then deletes those that are due, so a
restart doesn't leave them behind. Telegram lets a bot delete its own group messages for 48 h; older ones are dropped.
Settings: TsSettings.strategies["autodelete"] = {"minutes": int} (0 = off)."""

from collections import deque
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.tradingos import TsAutoDelete
from app.telegram.errors import TelegramRateLimitError, TelegramUnavailableError
from app.tradingos import signals

DEFAULT_MINUTES = 5
MAX_AGE = timedelta(hours=47)  # Telegram refuses deleting a bot's message after 48 h
PER_RUN = 100  # deletions per run (Telegram allows far more; keeps one run short)

_sent: deque[tuple[int, str, int, datetime]] = deque(maxlen=5000)  # (chat id, chat @username, message id, sent at)


def note(result: Any) -> None:
    """client.SENT_HOOKS: remember a group message the bot just sent (sync, never raises)."""
    if not isinstance(result, dict) or not result.get("message_id"):
        return
    chat = result.get("chat") or {}
    if chat.get("type") in ("group", "supergroup") and chat.get("id") is not None:
        _sent.append((int(chat["id"]), str(chat.get("username") or "").lower(), int(result["message_id"]), utcnow()))


def install() -> None:
    from app.telegram import client

    if note not in client.SENT_HOOKS:
        client.SENT_HOOKS.append(note)


def config(db: Session) -> dict[str, Any]:
    c = (signals.settings(db).strategies or {}).get("autodelete") or {}
    return {"minutes": int(c.get("minutes", DEFAULT_MINUTES))}


def _matches(setting: str, chat_id: int, username: str) -> bool:
    s = setting.strip().lower()
    return bool(s) and (s == str(chat_id) or (bool(username) and s.lstrip("@") == username))


def is_main_chat(db: Session, chat_id: int, username: str) -> bool:
    """The main chat, and never Bot Chat (publish_chat), even if both settings name the same chat."""
    st = signals.settings(db)
    return _matches(st.main_chat or "", chat_id, username) and not _matches(st.publish_chat or "", chat_id, username)


async def run(db: Session, client: Any, now: datetime | None = None) -> dict[str, int]:
    now = now or utcnow()
    minutes = config(db)["minutes"]
    queued = 0
    while _sent:
        chat_id, username, message_id, at = _sent.popleft()
        if minutes > 0 and is_main_chat(db, chat_id, username):
            db.add(TsAutoDelete(chat_id=chat_id, message_id=message_id, delete_at=at + timedelta(minutes=minutes)))
            queued += 1
    db.commit()
    deleted = 0
    for row in db.scalars(select(TsAutoDelete).where(TsAutoDelete.delete_at <= now).order_by(TsAutoDelete.delete_at).limit(PER_RUN)).all():
        if now - row.delete_at < MAX_AGE:
            try:
                await client.delete_message(row.chat_id, row.message_id)
                deleted += 1
            except (TelegramUnavailableError, TelegramRateLimitError):
                break  # Telegram down or busy: the rest waits for the next run
            except Exception:  # noqa: BLE001 - already deleted, no rights, too old: nothing to retry
                pass
        db.delete(row)
    db.commit()
    return {"queued": queued, "deleted": deleted}
