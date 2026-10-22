"""Invite your crew: members invite friends to the existing Price Chat Telegram community, with a personal bot invite.

Flow (no contact scraping, no messages to anyone who didn't write to the bot first):
  1. The member shares t.me/<bot>?start=crew_<code> through Telegram's own share sheet (they pick the chats).
  2. The friend presses Start: the bot records "this Telegram account was invited by <member>" (one row per Telegram
     account EVER) and answers privately with the community link. If Telegram says they're ALREADY in the community,
     or they've posted there before, it's recorded as an existing member (no reward).
  3. The members.verify job checks with Telegram (getChatMember) whether they joined; once they've stayed for the check
     period (crew_hold_hours) and are still there, the inviter is paid +referral.telegram through the XP ledger
     (idempotent: source_ref "crew:<id>", claim "tg:<id>"), and +referral.crew_bonus once at crew_bonus_at friends.
Abuse limits: self-invites (the inviter's own Telegram), bots, existing members, one reward per Telegram account ever (no
join/leave farming, no reassignment), the program's daily referral limit, and the existing same-pattern review flags.
"""

import urllib.parse
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.members import referrals
from app.members import xp as ledger
from app.models.members import CrewReferral, LinkedIdentity, Member, ReputationEvent

START_PREFIX = "crew_"
REWARD_RULE = "referral.telegram"
BONUS_RULE = "referral.crew_bonus"
MEMBER_STATUSES = {"member", "administrator", "creator"}
INVITE_TEXT = "Come join me on Price Chat 🥝\n\nEarn XP, level up your Kiwi, and join our community."


def invite_link(db: Session, member: Member, bot_username: str) -> str:
    return f"https://t.me/{bot_username.lstrip('@')}?start={START_PREFIX}{referrals.code_for(db, member)}"


def share_url(link: str) -> str:
    """Telegram's own share sheet: the member picks which chats get the invitation."""
    return "https://t.me/share/url?" + urllib.parse.urlencode({"url": link, "text": INVITE_TEXT}, quote_via=urllib.parse.quote)


def community_url(db: Session) -> str:
    chat = referrals.program(db).crew_chat or "@thepricechat"
    return f"https://t.me/{chat.lstrip('@')}" if chat.startswith("@") else "https://t.me/thepricechat"


def _in(info: dict[str, Any]) -> bool:
    st = info.get("status")
    return st in MEMBER_STATUSES or (st == "restricted" and bool(info.get("is_member")))


async def on_start(db: Session, bot: Any, code: str, tg_user_id: int, is_bot: bool) -> str:
    """'/start crew_<code>' in a private chat with the bot. Records the attribution; returns the private reply text."""
    prog = referrals.program(db)
    referrer = referrals.owner_of(db, code)
    join = community_url(db)
    welcome = (f"👋 Welcome to Price Chat!\n\nA friend invited you. Join the community here:\n{join}\n\n"
               "Then create your free Kiwi Passport at pricechat.live to earn XP and level up your Kiwi 🥝")
    if referrer is None or not prog.referrals_enabled:
        return f"👋 Welcome to Price Chat! Join the community here:\n{join}"
    if db.scalar(select(CrewReferral.id).where(CrewReferral.tg_user_id == tg_user_id)):
        return welcome  # this Telegram account was already invited once: the first invite stands
    reason = None
    if is_bot:
        reason = "bot"
    elif db.scalar(select(LinkedIdentity.id).where(LinkedIdentity.member_id == referrer.id, LinkedIdentity.platform == "telegram",
                                                   LinkedIdentity.platform_user_id == str(tg_user_id))):
        reason = "self"
    else:
        from app.models.telegram import TelegramMessage

        if db.scalar(select(TelegramMessage.id).where(TelegramMessage.author_id == tg_user_id).limit(1)):
            reason = "existing_member"  # already took part in Price Chat's Telegram
    if reason is None and bot is not None:
        try:
            if _in(await bot.get_chat_member(prog.crew_chat, tg_user_id)):
                reason = "existing_member"
        except Exception:  # noqa: BLE001 - can't see the community right now: the verify job decides later
            pass
    row = CrewReferral(referrer_id=referrer.id, tg_user_id=tg_user_id, code=referrals.normalise(code) or code[:24],
                       status="rejected" if reason else "pending", reason=reason)
    db.add(row)
    db.flush()
    ledger.audit(db, referrer.id, "system", "crew_invite_opened", {"crew_referral_id": row.id, "status": row.status, "reason": reason})
    db.commit()
    if reason == "self":
        return "That's your own invite link 🙂 Share it with friends: you earn XP when they join."
    if reason == "existing_member":
        return f"👋 Welcome back! You're already part of Price Chat:\n{join}"
    return welcome


async def verify(db: Session, bot: Any) -> dict[str, int]:
    """Check pending / joined invites with Telegram and pay the ones that stayed for the check period."""
    prog = referrals.program(db)
    out = {"checked": 0, "joined": 0, "verified": 0, "left": 0, "errors": 0}
    if bot is None:
        return out
    now = utcnow()
    hold = timedelta(hours=prog.crew_hold_hours)
    for row in db.scalars(select(CrewReferral).where(CrewReferral.status.in_(("pending", "joined"))).order_by(CrewReferral.id).limit(200)):
        if row.status == "pending" and now - _aware(row.created_at) > timedelta(days=14):
            row.status, row.reason = "rejected", "never_joined"
            continue
        try:
            inside = _in(await bot.get_chat_member(prog.crew_chat, row.tg_user_id))
        except Exception:  # noqa: BLE001 - bot can't see the community right now: try again next run
            out["errors"] += 1
            continue
        out["checked"] += 1
        row.checked_at = now
        if row.status == "pending":
            if inside:
                row.status, row.joined_at = "joined", now
                out["joined"] += 1
            continue
        if not inside:  # joined, then left before the check period ended: back to pending, never paid
            row.status, row.joined_at = "pending", None
            out["left"] += 1
            continue
        if now - _aware(row.joined_at) >= hold and _pay(db, row):
            out["verified"] += 1
    db.commit()
    return out


def _aware(d: Any) -> Any:
    from datetime import timezone

    return d if d is None or d.tzinfo else d.replace(tzinfo=timezone.utc)


def _pay(db: Session, row: CrewReferral) -> bool:
    referrer = db.get(Member, row.referrer_id) if row.referrer_id else None
    if referrer is None or referrer.status != "active":
        row.status, row.reason = "rejected", "referrer_gone"
        return False
    prog = referrals.program(db)
    today = ledger.day_of(utcnow())
    paid_today = db.scalar(select(func.count()).select_from(ReputationEvent).where(
        ReputationEvent.member_id == referrer.id, ReputationEvent.event_type == REWARD_RULE, ReputationEvent.day == today,
        ReputationEvent.reverses_id.is_(None))) or 0
    if paid_today >= prog.referral_daily_limit:
        return False  # waits for tomorrow's run (still joined)
    r = ledger.award(db, referrer, REWARD_RULE, f"crew:{row.id}", reason="A friend you invited joined the Price Chat Telegram",
                     claim_key=f"tg:{row.tg_user_id}", commit=False)
    if not r.awarded and r.refused in ("already_claimed", "duplicate"):
        row.status, row.reason = "rejected", "already_counted"
        return False
    if not r.awarded and r.refused not in ("rule_off", "zero"):
        return False  # a cap or limit: try again later
    row.status, row.verified_at, row.xp, row.event_id = "verified", utcnow(), r.xp, r.event.id if r.event else None
    ledger.audit(db, referrer.id, "system", "crew_verified", {"crew_referral_id": row.id, "xp": r.xp})
    n = db.scalar(select(func.count()).select_from(CrewReferral).where(CrewReferral.referrer_id == referrer.id,
                                                                      CrewReferral.status == "verified")) or 0
    if n >= prog.crew_bonus_at:
        ledger.award(db, referrer, BONUS_RULE, "crew:bonus", reason=f"{prog.crew_bonus_at} friends joined the Price Chat Telegram", commit=False)
    db.flush()
    return True


def stats(db: Session, member: Member, bot_username: str) -> dict[str, Any]:
    prog = referrals.program(db)
    rows = list(db.scalars(select(CrewReferral).where(CrewReferral.referrer_id == member.id)))
    rule, bonus = ledger.get_rule(db, REWARD_RULE), ledger.get_rule(db, BONUS_RULE)
    verified = sum(1 for r in rows if r.status == "verified")
    bonus_paid = db.scalar(select(func.coalesce(func.sum(ReputationEvent.xp), 0)).where(
        ReputationEvent.member_id == member.id, ReputationEvent.event_type == BONUS_RULE)) or 0
    link = invite_link(db, member, bot_username)
    milestones = [("First Friend", 1), ("Kiwi Recruiter", 3), ("Community Builder", 5), ("Kiwi Ambassador", 10)]
    return {"enabled": prog.referrals_enabled, "link": link, "share_url": share_url(link), "community": community_url(db),
            "reward_xp": rule.xp if rule.enabled else 0, "bonus_xp": bonus.xp if bonus.enabled else 0, "bonus_at": prog.crew_bonus_at,
            "hold_hours": prog.crew_hold_hours,
            "verified": verified, "pending": sum(1 for r in rows if r.status in ("pending", "joined")),
            "joined_waiting": sum(1 for r in rows if r.status == "joined"),
            "rejected": sum(1 for r in rows if r.status == "rejected"),
            "xp_earned": sum(r.xp for r in rows if r.status == "verified") + int(bonus_paid), "bonus_paid": int(bonus_paid) > 0,
            "milestones": [{"name": n, "at": k, "done": verified >= k} for n, k in milestones]}
