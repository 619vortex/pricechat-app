"""The bot never mutes or bans anyone (owner, 2026-11-10): a rule-breaking message is only removed and a warning recorded,
and every mute the bot gave before is lifted once."""

import asyncio
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.telegram import TgStrike
from app.telegram import moderation as mod
from tests.test_tg_rules import ADMIN, BOT, CHAT, MEMBER, FakeClient


@pytest.fixture(autouse=True)
def _fresh() -> None:
    mod._admins.clear()
    mod._no_rights_logged.clear()


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_the_switch_is_on() -> None:
    assert mod.NO_MUTES is True


def test_a_link_is_removed_and_nobody_is_muted(db: Session) -> None:
    c = FakeClient()
    for i in range(4):  # far past the old ladder: still never a mute
        db.query(TgStrike).update({TgStrike.created_at: utcnow() - timedelta(hours=1)})
        db.commit()
        text = run(mod.check_links(db, c, CHAT, MEMBER, "Spammy", 50 + i, "join https://scam.example.xyz", "pricechatbot"))
        assert text and "removed" in text and "muted" not in text.lower()
    assert c.restricted == []
    assert c.deleted == [50, 51, 52, 53]
    rows = list(db.scalars(select(TgStrike)))
    assert len(rows) == 4 and all(not r.permanent and r.muted_until is None for r in rows)


def test_an_admin_warn_removes_the_message_without_a_mute(db: Session) -> None:
    c = FakeClient()
    text = run(mod.command(db, c, CHAT, ADMIN, False, "/warn", ["3", "abuse"], MEMBER, "Spammy", 77, 78, BOT))
    assert text and "removed" in text and "rule 3" in text
    assert c.restricted == [] and 77 in c.deleted


def test_strikes_never_promise_a_mute(db: Session) -> None:
    c = FakeClient()
    text = run(mod.command(db, c, CHAT, ADMIN, False, "/strikes", [], MEMBER, "Spammy", 77, 78, BOT))
    assert text and "permanent" not in text and "doesn't mute" in text


def test_every_old_mute_is_lifted_once(db: Session) -> None:
    now = utcnow()
    db.add_all([
        TgStrike(chat_id=CHAT, user_id=MEMBER, rule=5, reason="link", by="auto", muted_until=now + timedelta(hours=10)),
        TgStrike(chat_id=CHAT, user_id=MEMBER, rule=5, reason="link", by="auto", muted_until=now - timedelta(hours=30)),
        TgStrike(chat_id=CHAT, user_id=30_003, rule=5, reason="link", by="auto", muted_until=None, permanent=True),
        TgStrike(chat_id=CHAT, user_id=40_004, rule=5, reason="link", by="auto", muted_until=now - timedelta(hours=1)),
    ])
    db.commit()
    c = FakeClient()
    assert run(mod.lift_all_mutes(db, c)) == 2
    assert sorted(c.restricted) == [(MEMBER, 0, True), (30_003, 0, True)]
    rows = list(db.scalars(select(TgStrike)))
    assert all(r.cleared and not r.permanent for r in rows)
    assert all(r.muted_until is None or r.muted_until <= utcnow() for r in rows)
    assert run(mod.lift_all_mutes(db, c)) == 0  # nothing left to lift


def test_a_failed_unmute_is_kept_for_the_next_start(db: Session) -> None:
    db.add(TgStrike(chat_id=CHAT, user_id=MEMBER, rule=5, reason="link", by="auto", muted_until=None, permanent=True))
    db.commit()
    assert run(mod.lift_all_mutes(db, FakeClient(can_restrict=False))) == 0
    row = db.scalar(select(TgStrike))
    assert row.permanent and not row.cleared
