"""Fixes from the 2026-11-09 review of the group rules (app/telegram/moderation.py) and the News Desk slots. Each test failed before."""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.telegram import TgStrike
from app.telegram import moderation as mod
from app.telegram.parser import parse_update

from tests.test_tg_rules import ADMIN, BOT, CHAT, MEMBER, FakeClient

CHANNEL_BOT, TELEGRAM = 136817688, 777000


@pytest.fixture(autouse=True)
def _fresh() -> None:
    mod._admins.clear()
    getattr(mod, "_no_rights_logged", set()).clear()


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def update(text: str, *, sender: dict[str, Any], sender_chat: dict[str, Any] | None = None, reply: dict[str, Any] | None = None, **extra: Any) -> Any:
    msg = {"message_id": 9, "date": 1_790_000_000, "chat": {"id": CHAT, "type": "supergroup"}, "from": sender, "text": text, **extra}
    if sender_chat:
        msg["sender_chat"] = sender_chat
    if reply:
        msg["reply_to_message"] = reply
    return parse_update({"update_id": 1, "message": msg})


# ---------------------------------------------------------------- who sent it (Critical: channel senders could /warn anyone)
def test_posting_as_your_own_channel_is_not_an_anonymous_admin() -> None:
    p = update("/warn 3", sender={"id": CHANNEL_BOT, "is_bot": True, "first_name": "Channel"}, sender_chat={"id": -100555, "type": "channel", "title": "Troll"},
               reply={"message_id": 5, "from": {"id": MEMBER, "first_name": "Victim"}, "text": "hi"})
    assert p.as_channel and not p.as_group
    anon = update("/warn 3", sender={"id": 1087968824, "is_bot": True, "first_name": "Group"}, sender_chat={"id": CHAT, "type": "supergroup", "title": "Price Chat"})
    assert anon.as_group and not anon.as_channel
    auto = update("news https://x.example", sender={"id": TELEGRAM, "is_bot": False, "first_name": "Telegram"}, sender_chat={"id": -100777, "type": "channel"}, is_automatic_forward=True)
    assert auto.automatic_forward and not auto.as_channel


@pytest.mark.asyncio
async def test_a_channel_sender_cant_warn_and_a_link_sent_as_a_channel_is_deleted_without_a_strike(db: Session, fake_telegram: Any) -> None:
    from app.telegram.ingestion import TelegramIngestor
    from app.telegram.state import TelegramRuntime

    from tests.test_telegram import _client, _source

    _source(db, -100111)
    fake_telegram.members[(-100111, 500)] = "administrator"
    victim = fake_telegram.message(-100111, "gm", user_id=8, name="Victim")
    # a member posting as their own channel: Telegram sends from @Channel_Bot with sender_chat = that channel
    fake_telegram.updates.append({"update_id": 900, "message": {"message_id": 901, "date": 1_790_000_000, "chat": {"id": -100111, "type": "supergroup"},
                                  "from": {"id": CHANNEL_BOT, "is_bot": True, "first_name": "Channel"}, "sender_chat": {"id": -100555, "type": "channel", "title": "Troll"},
                                  "text": "/warn 3", "reply_to_message": {"message_id": victim, "from": {"id": 8, "first_name": "Victim"}, "text": "gm"}}})
    fake_telegram.updates.append({"update_id": 902, "message": {"message_id": 903, "date": 1_790_000_000, "chat": {"id": -100111, "type": "supergroup"},
                                  "from": {"id": CHANNEL_BOT, "is_bot": True, "first_name": "Channel"}, "sender_chat": {"id": -100555, "type": "channel", "title": "Troll"},
                                  "text": "free coins https://scam.example"}})
    await TelegramIngestor(db, _client(fake_telegram), TelegramRuntime(configured=True)).sync()
    calls = fake_telegram.calls
    assert not any(m == "restrictChatMember" for m, _ in calls)  # nobody muted
    assert ("deleteMessage", {"chat_id": -100111, "message_id": 903}) in calls  # the channel's link is removed
    assert not db.scalars(select(TgStrike)).all()


@pytest.mark.asyncio
async def test_a_linked_channels_automatic_forward_is_never_touched(db: Session, fake_telegram: Any) -> None:
    from app.telegram.ingestion import TelegramIngestor
    from app.telegram.state import TelegramRuntime

    from tests.test_telegram import _client, _source

    _source(db, -100111)
    fake_telegram.updates.append({"update_id": 910, "message": {"message_id": 911, "date": 1_790_000_000, "chat": {"id": -100111, "type": "supergroup"},
                                  "from": {"id": TELEGRAM, "is_bot": False, "first_name": "Telegram"}, "sender_chat": {"id": -100777, "type": "channel", "title": "Price Updates"},
                                  "is_automatic_forward": True, "text": "Bybaz hacked https://www.coindesk.com/x"}})
    await TelegramIngestor(db, _client(fake_telegram), TelegramRuntime(configured=True)).sync()
    assert not any(m in ("deleteMessage", "restrictChatMember") for m, _ in fake_telegram.calls)


def test_warn_on_a_channel_or_group_message_says_so_instead_of_failing(db: Session) -> None:
    c = FakeClient()
    out = run(mod.command(db, c, CHAT, ADMIN, False, "/warn", ["1"], CHANNEL_BOT, "Channel", 77, 78, BOT))
    assert "sent as a channel" in out and 77 in c.deleted and not c.restricted


# ---------------------------------------------------------------- wrongful or excessive mutes
def test_emails_and_ordinary_words_with_dots_are_not_links_but_real_links_are() -> None:
    for ok in ["mail me at john@gmail.com", "Fetch.ai is pumping", "Buy the dip.Top is in", "lol.me too", "BTC.D rising", "ETH.USD", "1.5x leverage"]:
        assert not mod.links_in(ok, "cryptopricechatbot"), ok
    for bad in ["https://fetch.ai/claim", "www.anything.ai", "t.me/airdrop_now", "go to binance-bonus.com now", "claim at drainer.xyz"]:
        assert mod.links_in(bad), bad


def test_a_burst_of_links_is_one_strike_not_a_permanent_mute(db: Session) -> None:
    c = FakeClient()
    first = run(mod.check_links(db, c, CHAT, MEMBER, "Spammy", 55, "https://a.example", None))
    assert "muted for 12 hours" in first
    assert run(mod.check_links(db, c, CHAT, MEMBER, "Spammy", 56, "https://b.example", None)) is None
    assert run(mod.check_links(db, c, CHAT, MEMBER, "Spammy", 57, "https://c.example", None)) is None
    assert mod.strikes(db, CHAT, MEMBER) == 1 and [u for _, u, _ in c.restricted if u == 0] == [] and c.deleted == [55, 56, 57]


def test_without_rights_the_automatic_check_stays_quiet(db: Session) -> None:
    c = FakeClient(can_restrict=False)
    assert run(mod.check_links(db, c, CHAT, MEMBER, "Spammy", 55, "https://a.example", None)) is None
    assert run(mod.check_links(db, c, CHAT, MEMBER + 1, "Other", 56, "https://b.example", None)) is None


def test_unmute_restores_every_permission_so_telegram_lifts_the_restriction() -> None:
    from app.telegram.client import TelegramBotClient

    sent: list[dict[str, Any]] = []

    class C(TelegramBotClient):
        def __init__(self) -> None:
            pass

        async def _call(self, method: str, payload: dict[str, Any]) -> Any:
            sent.append(payload)
            return True

    run(C().restrict_member(CHAT, MEMBER, 0, can_send=True))
    run(C().restrict_member(CHAT, MEMBER, 0, can_send=False))
    lift, mute = sent
    assert all(lift["permissions"].values()) and {"can_invite_users", "can_pin_messages", "can_change_info", "can_manage_topics"} <= set(lift["permissions"])
    assert not any(mute["permissions"].values()) and "can_pin_messages" not in mute["permissions"]


def test_in_a_forum_topic_the_topic_opener_is_not_the_reply_target() -> None:
    p = update("/unmute 123456789", sender={"id": ADMIN, "first_name": "Boss"}, is_topic_message=True, message_thread_id=40,
               reply={"message_id": 40, "from": {"id": 30_003, "first_name": "TopicCreator"}, "forum_topic_created": {"name": "Signals"}})
    assert p.reply_author_id is None


# ---------------------------------------------------------------- News Desk slots
@pytest.mark.asyncio
async def test_alerts_are_handled_once_even_while_the_owner_chat_is_unset(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.newsdesk import events

    events._alerted.clear()
    calls: list[str] = []

    async def notify(db: Session, key: str, text: str, **kw: Any) -> bool:
        calls.append(key)
        return False  # no owner chat: nothing recorded by app.alerts

    monkeypatch.setattr("app.alerts.notify_owner", notify)
    monkeypatch.setattr(events, "DEFAULT_SCHEDULE", "slots")
    now = datetime(2026, 11, 9, 6, 0, tzinfo=timezone.utc)
    p = events.policy({})
    await events.alert_slots(db, now, p)
    n = len(calls)
    await events.alert_slots(db, now + timedelta(minutes=3), p)
    await events.alert_slots(db, now + timedelta(minutes=8), p)
    assert n >= 1 and len(calls) == n


def test_the_schedule_panel_follows_the_desks_own_plan(db: Session) -> None:
    from app.newsdesk import events

    assert events.overview(db, datetime(2026, 11, 9, 6, 0, tzinfo=timezone.utc), {"policy": {"schedule": "windows"}})["schedule"]["schedule"] == "windows"
