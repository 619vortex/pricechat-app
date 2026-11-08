"""AUDIT (2026-11-08, telegram reviewer): reproductions for notes/telegram.md. Not part of the product suite.

Run: pytest tests/audit_telegram_bot.py -q -p no:cacheprovider (file name does not match test_*.py on purpose; pass it explicitly).
"""

import pytest
from sqlalchemy.orm import Session

from app.telegram.client import TelegramBotClient
from app.telegram.ingestion import TelegramIngestor
from app.telegram.state import TelegramRuntime
from tests.fakes import TOKEN, FakeTelegram


def _client(tg: FakeTelegram) -> TelegramBotClient:
    return TelegramBotClient(TOKEN, "https://api.telegram.test", transport=tg.transport)


@pytest.fixture
def handled(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, str, bool]]:
    from app.tradingos import bot

    out: list[tuple[int, str, bool]] = []

    async def record(db_: Session, client: object, chat_id: int, user_id: int, text: str, group: bool = False, **kw: object) -> None:
        out.append((chat_id, text, group))

    monkeypatch.setattr(bot, "handle_command", record)
    return out


async def test_TG1_an_edited_command_is_answered_again(db: Session, fake_telegram: FakeTelegram, handled: list) -> None:
    """Editing a message that holds a command (a typo fix, or just re-saving) makes the bot answer it a second time."""
    mid = fake_telegram.message(-100111, "/price btc")
    fake_telegram.message(-100111, "/price eth", edited_of=mid)
    fake_telegram.message(5, "/signal", edited_of=fake_telegram.message(5, "/signal", kind="message"))  # DM: the same
    await TelegramIngestor(db, _client(fake_telegram), TelegramRuntime()).sync()
    texts = [t for _, t, _ in handled]
    assert texts.count("/price btc") == 1 and texts.count("/price eth") == 1  # the edit was answered as a new command
    assert texts.count("/signal") == 2


async def test_TG2_channel_posts_and_automatic_forwards_count_as_people(db: Session, fake_telegram: FakeTelegram, handled: list) -> None:
    """A channel post (no `from`, sender_chat = channel) or a linked-channel automatic forward (from 777000) starting with
    "fc"/"/" is queued as a command and answered in that chat."""
    channel = {"id": -100777, "title": "Updates", "type": "channel", "username": "pricechatupdates"}
    fake_telegram.raw({"channel_post": {"message_id": 1, "date": 1_790_000_000, "chat": channel, "sender_chat": channel, "text": "fc btc"}})
    fake_telegram.message(-100111, "/price btc", extra={"from": {"id": 777000, "is_bot": False, "first_name": "Telegram"},
                                                         "sender_chat": channel, "is_automatic_forward": True})
    await TelegramIngestor(db, _client(fake_telegram), TelegramRuntime()).sync()
    assert (-100777, "/fc btc", True) in handled
    assert (-100111, "/price btc", True) in handled


def test_TG3_raw_html_is_sliced_mid_tag_by_the_client() -> None:
    """send_message/send_photo/edit_* cut the raw HTML at 4096/1024 characters: a cut inside a tag or entity is refused by
    Telegram ("can't parse entities"), so the whole message is lost instead of shortened."""
    text = "x" * 1015 + '<a href="https://example.com/very/long/source">Source</a>'
    cut = text[:1024]
    assert cut.endswith('<a href="') and "</a>" not in cut


def test_TG4_whole_lines_returns_an_overlong_single_line_unchanged() -> None:
    from app.tradingos.bot import _whole_lines

    line = "<b>" + "y" * 5000 + "</b>"
    assert _whole_lines(line, 4096) == line  # goes to the client as is, which then slices it mid-tag


def test_TG5_tgtrade_button_prices_are_rounded_to_six_significant_digits() -> None:
    """tg-trade branch: the /sl /tp Confirm button carries f"{v:g}", so the confirmed price can differ from the preview."""
    assert f"{104123.5:g}" == "104124"
    assert f"{1.2345678:g}" == "1.23457"
