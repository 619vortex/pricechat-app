"""The website's chat bubble: a read-only preview of the PUBLIC Telegram group. TEST FIXTURES only (test database)."""

from datetime import timedelta

from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.social_intel import GrowthSnapshot
from app.models.telegram import TelegramMessage, TelegramSource


def _msg(db: Session, src: TelegramSource, n: int, text: str, name: str = "Alice Smith", **kw) -> None:  # type: ignore[no-untyped-def]
    db.add(TelegramMessage(source_id=src.id, chat_id=src.chat_id, message_id=n, author_id=100 + n, author_name=name,
                           sent_at=utcnow() - timedelta(minutes=60 - n), text=text, **kw))


def test_chat_bubble_shows_the_public_group_only_first_names_no_bots(client: TestClient, db: Session, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.security.admin_auth import hash_password

    runtime = client.app.state.runtime  # type: ignore[attr-defined]
    monkeypatch.setattr(runtime.settings, "admin_username", "admin")
    monkeypatch.setattr(runtime.settings, "admin_password_hash", SecretStr(hash_password("pw")))  # production-like: admin login on
    assert client.get("/api/website/chat").json() == {"enabled": False}  # no public group yet: no bubble

    private = TelegramSource(chat_id=-100111, display_name="Team only", username=None, enabled=True)
    public = TelegramSource(chat_id=-100222, display_name="Price Chat", username="thepricechat", enabled=True)
    db.add_all([private, public])
    db.flush()
    _msg(db, private, 1, "internal note")
    _msg(db, public, 2, "gm everyone", "Alice Smith")
    _msg(db, public, 3, "/start@bot", "Bob")
    _msg(db, public, 4, "I am a bot", "Rose", author_is_bot=True)
    _msg(db, public, 5, "", "Carl Jones", media_type="photo")
    _msg(db, public, 6, "btc looking strong", "Dana", reply_to_message_id=2)
    db.add(GrowthSnapshot(kind="telegram_chat", subject="-100222", label="Price Chat", taken_at=utcnow(), members=119, source="bot_api"))
    db.commit()
    from app.pokemon import history

    history.invalidate()
    chat = client.get("/api/website/chat").json()  # public: no admin login needed
    assert chat["enabled"] and chat["link"] == "https://t.me/thepricechat" and chat["members"] == 119
    assert [(m["name"], m["text"]) for m in chat["messages"]] == [("Alice", "gm everyone"), ("Carl", "📷 Photo"), ("Dana", "btc looking strong")]
    assert chat["messages"][-1]["reply_to"] == 2 and "internal note" not in str(chat) and "Smith" not in str(chat)
