import asyncio
from datetime import timedelta
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.tradingos import TsAutoDelete
from app.telegram import client as tg_client
from app.telegram.errors import TelegramRequestError, TelegramUnavailableError
from app.tradingos import autodelete, signals


class FakeTG:
    def __init__(self, fail: Exception | None = None) -> None:
        self.deleted: list[tuple[int, int]] = []
        self.fail = fail

    async def delete_message(self, chat_id: int, message_id: int) -> None:
        if self.fail:
            raise self.fail
        self.deleted.append((chat_id, message_id))


def sent(chat_id: int, mid: int, username: str = "", kind: str = "supergroup") -> dict[str, Any]:
    return {"message_id": mid, "chat": {"id": chat_id, "type": kind, **({"username": username} if username else {})}}


def chats(db: Session, main: str = "@thepricechat", bot: str = "@botpricechat", minutes: int | None = None) -> None:
    s = signals.settings(db)
    s.main_chat, s.publish_chat = main, bot
    if minutes is not None:
        s.strategies = {**(s.strategies or {}), "autodelete": {"minutes": minutes}}
    db.commit()
    autodelete._sent.clear()


def test_only_the_main_chats_messages_are_deleted_after_five_minutes(db: Session) -> None:
    chats(db)
    autodelete.note(sent(-100, 1, "thepricechat"))  # main chat (by @username)
    autodelete.note(sent(-200, 2, "botpricechat"))  # Bot Chat: never
    autodelete.note(sent(42, 3, kind="private"))  # a private chat: never
    autodelete.note(sent(-300, 4))  # some other group: never
    tg = FakeTG()
    assert asyncio.run(autodelete.run(db, tg)) == {"queued": 1, "deleted": 0}
    assert tg.deleted == []  # not before 5 minutes
    assert asyncio.run(autodelete.run(db, tg, utcnow() + timedelta(minutes=5, seconds=1)))["deleted"] == 1
    assert tg.deleted == [(-100, 1)] and db.scalar(select(TsAutoDelete)) is None


def test_main_chat_by_id_and_never_when_it_is_also_bot_chat(db: Session) -> None:
    chats(db, main="-100")
    autodelete.note(sent(-100, 7))
    assert asyncio.run(autodelete.run(db, FakeTG()))["queued"] == 1
    chats(db, main="@thepricechat", bot="@thepricechat")  # one chat for both: it's Bot Chat, so kept
    autodelete.note(sent(-100, 8, "thepricechat"))
    assert asyncio.run(autodelete.run(db, FakeTG()))["queued"] == 0


def test_off_and_telegram_trouble(db: Session) -> None:
    chats(db, minutes=0)
    autodelete.note(sent(-100, 1, "thepricechat"))
    assert asyncio.run(autodelete.run(db, FakeTG()))["queued"] == 0  # 0 = keep everything
    chats(db, minutes=5)
    autodelete.note(sent(-100, 2, "thepricechat"))
    later = utcnow() + timedelta(minutes=6)
    asyncio.run(autodelete.run(db, FakeTG(TelegramUnavailableError("down")), later))
    assert db.scalar(select(TsAutoDelete)) is not None  # Telegram down: tried again next run
    asyncio.run(autodelete.run(db, FakeTG(TelegramRequestError("message to delete not found")), later))
    assert db.scalar(select(TsAutoDelete)) is None  # already gone: dropped, not retried forever


def test_the_client_reports_every_message_it_sends() -> None:
    autodelete._sent.clear()
    autodelete.install()
    autodelete.install()
    assert tg_client.SENT_HOOKS.count(autodelete.note) == 1

    def handle(r: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "result": sent(-100, 99, "thepricechat")})

    c = tg_client.TelegramBotClient("123:abc", transport=httpx.MockTransport(handle))
    asyncio.run(c.send_message(-100, "hi"))
    asyncio.run(c.aclose())
    assert [m[2] for m in autodelete._sent] == [99]
    tg_client.SENT_HOOKS.remove(autodelete.note)
