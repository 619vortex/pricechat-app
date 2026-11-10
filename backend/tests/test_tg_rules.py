"""Price Chat's Telegram group rules (app/telegram/moderation.py): /rules, links caught automatically, admins' /warn, the
12 h / 24 h / permanent ladder, /unmute and /strikes. No network: a fake Telegram client."""

import asyncio
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.telegram import TgStrike
from app.telegram import moderation as mod
from app.telegram.parser import parse_update

CHAT, ADMIN, MEMBER, BOT = -1001, 10_001, 20_002, 999_999


class FakeClient:
    def __init__(self, admins: tuple[int, ...] = (ADMIN,), can_restrict: bool = True, admins_fail: bool = False) -> None:
        self.admins, self.can_restrict, self.admins_fail = admins, can_restrict, admins_fail
        self.restricted: list[tuple[int, int, bool]] = []
        self.deleted: list[int] = []

    async def get_chat_administrators(self, chat_id: Any) -> list[dict[str, Any]]:
        if self.admins_fail:
            raise RuntimeError("Telegram unavailable")
        return [{"user": {"id": a}, "status": "administrator"} for a in self.admins]

    async def restrict_member(self, chat_id: Any, user_id: int, until: int, can_send: bool) -> None:
        if not self.can_restrict:
            raise RuntimeError("not enough rights")
        self.restricted.append((user_id, until, can_send))

    async def delete_message(self, chat_id: Any, message_id: int) -> None:
        self.deleted.append(message_id)


@pytest.fixture(autouse=True)
def _fresh_admin_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    mod._admins.clear()
    monkeypatch.setattr(mod, "NO_MUTES", False)  # the old ladder, kept behind the switch; tests/test_tg_no_mutes.py covers today's rule


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def warn(db: Session, c: FakeClient, words: list[str], target: int | None = MEMBER, issuer: int = ADMIN, anonymous: bool = False,
         cmd: str = "/warn") -> str | None:
    return run(mod.command(db, c, CHAT, issuer, anonymous, cmd, words, target, "Spammy" if target else None, 77, 78, BOT))


# ---------------------------------------------------------------- the rules text
def test_the_rules_say_what_the_owner_wrote() -> None:
    t = mod.RULES_TEXT
    for part in ("advertisements or promotions without admin permission", "sexual, porn or disturbing images",
                 "political discussion, harassment or abuse", "Messages that break the rules are removed", "@jacktheblack",
                 "No links or scams. English only"):
        assert part in t, part


def test_rules_command_answers_in_groups_and_private_chats(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.tradingos import bot

    from tests.test_trading_os import FakeTG

    tg = FakeTG()
    asyncio.run(bot.handle_command(db, tg, -100, 5, "/rules", group=True))
    assert "Price Chat group rules" in tg.sent[-1][2]
    asyncio.run(bot.handle_command(db, tg, 5, 5, "/rules"))
    assert "@jacktheblack" in tg.sent[-1][2]
    text, _ = bot.welcome_group(["Ann"], "cryptopricechatbot")
    assert "/rules" in text


# ---------------------------------------------------------------- links
def test_links_are_found_and_price_chats_own_are_allowed() -> None:
    for bad in ["join https://scam.example/pump", "www.freecoins.biz", "t.me/airdrop_now", "go to binance-bonus.com now", "telegram.me/x",
                "visit https://pricechat.live.evil.com/x"]:
        assert mod.links_in(bad), bad
    for ok in ["BTC broke 1.5x resistance", "e.g. ETH at 3.14k", "see https://pricechat.live/vpn", "news on t.me/Pricechatupdates",
               "ask t.me/cryptopricechatbot", "no link here", "www.pricechat.live is ours"]:
        assert not mod.links_in(ok, "cryptopricechatbot"), ok


def test_a_members_link_is_deleted_and_muted_and_an_admins_isnt(db: Session) -> None:
    c = FakeClient()
    msg = run(mod.check_links(db, c, CHAT, MEMBER, "Spammy", 55, "free money https://scam.example", "cryptopricechatbot"))
    assert "muted for 12 hours" in msg and "rule 5" in msg and "@jacktheblack" in msg
    assert c.deleted == [55] and c.restricted[0][0] == MEMBER and not c.restricted[0][2]
    assert run(mod.check_links(db, c, CHAT, ADMIN, "Boss", 56, "https://partner.example", "cryptopricechatbot")) is None
    assert 56 not in c.deleted
    mod._admins.clear()  # no cached admin list either
    down = FakeClient(admins_fail=True)  # Telegram didn't answer: never punish on a guess
    assert run(mod.check_links(db, down, CHAT, MEMBER, "Spammy", 57, "https://scam.example", None)) is None


# ---------------------------------------------------------------- the ladder
def test_the_ladder_is_12_hours_then_24_then_for_good(db: Session) -> None:
    c = FakeClient()
    t0 = utcnow().timestamp()
    first = warn(db, c, ["1", "promo"])
    second = warn(db, c, ["3"])
    third = warn(db, c, [])
    assert "for 12 hours" in first and "Next time: 24 hours" in first
    assert "for 24 hours" in second and "Next time: a permanent mute" in second
    assert "for good" in third and "Next time" not in third
    untils = [u for _, u, _ in c.restricted]
    assert abs(untils[0] - (t0 + 12 * 3600)) < 60 and abs(untils[1] - (t0 + 24 * 3600)) < 60 and untils[2] == 0
    rows = db.scalars(select(TgStrike).order_by(TgStrike.id)).all()
    assert [r.rule for r in rows] == [1, 3, None] and rows[2].permanent and rows[0].by == f"admin:{ADMIN}"
    assert 77 in c.deleted and 78 in c.deleted  # the offending message and the /warn itself


def test_only_admins_warn_admins_are_never_muted_and_a_reply_is_needed(db: Session) -> None:
    c = FakeClient(admins=(ADMIN, 30_003))
    assert "Only group admins" in warn(db, c, ["1"], issuer=MEMBER)
    assert "Admins aren't muted" in warn(db, c, ["1"], target=30_003)
    assert "Reply to the message" in warn(db, c, ["1"], target=None)
    assert warn(db, c, ["1"], target=BOT) is None
    assert not c.restricted
    assert "muted" in warn(db, c, ["2"], issuer=0, anonymous=True)  # an anonymous admin posting as the group


def test_without_rights_the_bot_says_so(db: Session) -> None:
    c = FakeClient(can_restrict=False)
    assert warn(db, c, ["1"]) == mod.NEEDS_RIGHTS
    assert not db.scalars(select(TgStrike)).all()  # no strike without a mute


def test_unmute_and_reset_and_strikes(db: Session) -> None:
    c = FakeClient()
    warn(db, c, ["5"])
    warn(db, c, ["5"])
    assert "2 strike(s)" in warn(db, c, [], cmd="/strikes") and "Next: a permanent mute" in warn(db, c, [], cmd="/strikes")
    assert "can write again" in warn(db, c, [], cmd="/unmute") and c.restricted[-1][2] is True
    assert mod.strikes(db, CHAT, MEMBER) == 2  # lifted, still counted
    assert "strikes were cleared" in warn(db, c, ["reset"], cmd="/unmute")
    assert mod.strikes(db, CHAT, MEMBER) == 0
    assert "for 12 hours" in warn(db, c, ["5"])  # a fresh start after a reset
    assert "can write again" in run(mod.command(db, c, CHAT, ADMIN, False, "/unmute", [str(MEMBER)], None, None, None, 80, BOT))


def test_replies_carry_who_wrote_the_original() -> None:
    upd = {"update_id": 1, "message": {"message_id": 9, "date": 1_790_000_000, "chat": {"id": CHAT, "type": "supergroup"},
                                       "from": {"id": ADMIN, "first_name": "Boss"}, "text": "/warn 1",
                                       "reply_to_message": {"message_id": 5, "from": {"id": MEMBER, "first_name": "Spammy"}, "text": "buy my coin"}}}
    p = parse_update(upd)
    assert p.reply_author_id == MEMBER and p.reply_author_name == "Spammy" and p.reply_to_message_id == 5


# ---------------------------------------------------------------- end to end through the real message handling
@pytest.mark.asyncio
async def test_a_link_in_an_allow_listed_group_is_deleted_not_stored_and_its_author_muted(db: Session, fake_telegram: Any) -> None:
    from app.models.telegram import TelegramMessage
    from app.telegram.ingestion import TelegramIngestor
    from app.telegram.state import TelegramRuntime

    from tests.test_telegram import _client, _source

    _source(db, -100111)
    fake_telegram.members[(-100111, 500)] = "administrator"
    fake_telegram.message(-100111, "gm everyone", user_id=7)
    spam = fake_telegram.message(-100111, "free airdrop https://scam.example/claim", user_id=8, name="Spammer")
    fake_telegram.message(-100111, "our partner https://partner.example", user_id=500, name="Boss")
    fake_telegram.message(-100222, "a link in someone else's group https://x.example", user_id=9)  # not allow-listed: never touched
    await TelegramIngestor(db, _client(fake_telegram), TelegramRuntime(configured=True)).sync()
    texts = set(db.scalars(select(TelegramMessage.text)))
    assert "gm everyone" in texts
    assert not any("scam.example" in t or "partner.example" in t for t in texts)  # links stay off the website's chat feed, admins' too
    assert not any(m == "deleteMessage" and b.get("message_id") != spam for m, b in fake_telegram.calls)  # the admin's message stays in the group
    calls = fake_telegram.calls
    assert ("deleteMessage", {"chat_id": -100111, "message_id": spam}) in calls
    mutes = [b for m, b in calls if m == "restrictChatMember"]
    assert len(mutes) == 1 and mutes[0]["user_id"] == 8 and mutes[0]["permissions"]["can_send_messages"] is False
    assert any(m == "sendMessage" and "muted for 12 hours" in b.get("text", "") for m, b in calls)
    assert not any(m == "restrictChatMember" and b.get("chat_id") == -100222 for m, b in calls)
