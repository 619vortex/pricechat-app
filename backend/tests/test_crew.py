"""Invite your crew: a friend joins the Price Chat Telegram through a member's personal bot invite; the inviter gets XP once,
after the check period, through the XP ledger. TEST FIXTURES only: a fake Telegram that answers getChatMember."""

import asyncio
from datetime import timedelta
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.members import crew, referrals
from app.members import service as members
from app.models.members import CrewReferral, MemberBadge, ReputationEvent

from tests.test_trading_os import kiwi


class FakeBot:
    """TEST FIXTURE: who is in the community right now."""

    def __init__(self) -> None:
        self.inside: set[int] = set()
        self.down = False

    async def get_chat_member(self, chat_id: Any, uid: int) -> dict[str, Any]:
        if self.down:
            raise RuntimeError("Bad Request: chat not found")
        return {"status": "member" if uid in self.inside else "left"}


def run(c: Any) -> Any:
    return asyncio.run(c)


def code(db: Session, m: Any) -> str:
    return referrals.code_for(db, m)


def age(db: Session, uid: int, hours: float) -> None:
    row = db.scalar(select(CrewReferral).where(CrewReferral.tg_user_id == uid))
    row.joined_at = row.joined_at - timedelta(hours=hours)
    db.commit()


def test_a_new_friend_who_joins_and_stays_pays_the_inviter_once(db: Session) -> None:
    inviter, bot = kiwi(db, 1, "9001"), FakeBot()
    text = run(crew.on_start(db, bot, code(db, inviter), 5001, False))
    assert "t.me/thepricechat" in text
    row = db.scalar(select(CrewReferral).where(CrewReferral.tg_user_id == 5001))
    assert row.status == "pending" and row.referrer_id == inviter.id
    xp0 = inviter.xp_total
    assert run(crew.verify(db, bot))["joined"] == 0  # not in the community yet: nothing
    bot.inside.add(5001)
    assert run(crew.verify(db, bot))["joined"] == 1 and row.status == "joined"
    assert run(crew.verify(db, bot))["verified"] == 0 and inviter.xp_total == xp0  # joined, but the check period isn't over
    age(db, 5001, 25)
    assert run(crew.verify(db, bot))["verified"] == 1 and row.status == "verified" and inviter.xp_total == xp0 + 100
    run(crew.verify(db, bot))
    assert inviter.xp_total == xp0 + 100  # exactly once
    assert db.scalar(select(MemberBadge.id).where(MemberBadge.member_id == inviter.id)) is not None  # First Friend
    s = crew.stats(db, inviter, "cryptopricechatbot")
    assert s["verified"] == 1 and s["xp_earned"] == 100 and s["link"].endswith(f"?start=crew_{code(db, inviter)}")
    assert s["share_url"].startswith("https://t.me/share/url?url=") and "Price%20Chat" in s["share_url"]


def test_self_invites_bots_existing_members_and_second_invites_never_pay(db: Session) -> None:
    inviter, other, bot = kiwi(db, 1, "9101"), kiwi(db, 1, "9102"), FakeBot()
    run(crew.on_start(db, bot, code(db, inviter), 9101, False))  # the inviter's own Telegram account
    run(crew.on_start(db, bot, code(db, inviter), 6001, True))  # a bot
    bot.inside.add(6002)
    run(crew.on_start(db, bot, code(db, inviter), 6002, False))  # already in the community
    rows = {r.tg_user_id: r for r in db.scalars(select(CrewReferral))}
    assert (rows[9101].reason, rows[6001].reason, rows[6002].reason) == ("self", "bot", "existing_member")
    run(crew.on_start(db, bot, code(db, inviter), 6003, False))
    run(crew.on_start(db, bot, code(db, other), 6003, False))  # a second invite can't take the friend over
    assert db.scalar(select(CrewReferral).where(CrewReferral.tg_user_id == 6003)).referrer_id == inviter.id


def test_join_leave_farming_never_pays_and_a_leaver_is_not_verified(db: Session) -> None:
    inviter, bot = kiwi(db, 1, "9201"), FakeBot()
    run(crew.on_start(db, bot, code(db, inviter), 7001, False))
    bot.inside.add(7001)
    run(crew.verify(db, bot))
    bot.inside.discard(7001)  # left before the check period ended
    assert run(crew.verify(db, bot))["left"] == 1
    row = db.scalar(select(CrewReferral).where(CrewReferral.tg_user_id == 7001))
    assert row.status == "pending" and row.xp == 0
    bot.down = True  # Telegram can't be read: nothing changes, nothing is paid
    assert run(crew.verify(db, bot))["errors"] == 1 and row.status == "pending"


def test_five_friends_pay_the_crew_bonus_once_and_count_on_the_referral_board(db: Session, client: TestClient) -> None:
    inviter, bot = kiwi(db, 1, "9301"), FakeBot()
    referrals.program(db).referral_daily_limit = 10
    db.commit()
    for uid in range(8001, 8007):
        run(crew.on_start(db, bot, code(db, inviter), uid, False))
        bot.inside.add(uid)
    run(crew.verify(db, bot))
    for uid in range(8001, 8007):
        age(db, uid, 25)
    run(crew.verify(db, bot))
    bonus = db.scalars(select(ReputationEvent).where(ReputationEvent.member_id == inviter.id, ReputationEvent.event_type == "referral.crew_bonus")).all()
    assert len(bonus) == 1 and bonus[0].xp == 500
    assert crew.stats(db, inviter, "bot")["xp_earned"] == 6 * 100 + 500
    client.cookies.set("pc_session", members.new_session(db, inviter, 30))
    me = client.get("/api/members/me/crew").json()
    assert me["verified"] == 6 and me["bonus_paid"] and [m["done"] for m in me["milestones"]] == [True, True, True, False]
    board = client.get("/api/members/leaderboard", params={"metric": "referrals"}).json()
    assert board["rows"][0]["username"] == inviter.username and board["rows"][0]["value"] == 6
