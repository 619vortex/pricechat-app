"""Daily consistency streak (Kiwi Passport).

A day counts when the member comes back AND completes at least one task (a mission: watch, read, Telegram activity,
connect an account... not the plain "check in" visit). Consecutive UTC days build the streak; missing a day starts it
again from 1. The first task of each day pays streak XP through the ledger (rule "streak.day", once per day):

    day 1: +5, then +2 more each day in a row (max +25 a day), plus a milestone bonus on days 3, 7, 14, 30, 60, 100.

Nothing is paid for visiting alone, and a day can never be counted twice (one ledger event per member per day).
"""

from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.members import xp as ledger
from app.models.members import Member, MemberStreak, ReputationEvent

MILESTONES = {3: 15, 7: 40, 14: 75, 30: 150, 60: 250, 100: 400}
EXCLUDED_KINDS = {"daily_visit"}  # a visit alone isn't a task


def day_reward(n: int) -> int:
    return min(5 + 2 * (n - 1), 25)


def reward(n: int) -> int:
    return day_reward(n) + MILESTONES.get(n, 0)


def _d(s: str) -> date:
    return date.fromisoformat(s)


def _row(db: Session, member: Member) -> MemberStreak:
    s = db.scalar(select(MemberStreak).where(MemberStreak.member_id == member.id))
    if s is None:
        s = MemberStreak(member_id=member.id, current=0, best=0)
        db.add(s)
        db.flush()
    return s


def on_task(db: Session, member: Member, when: datetime | None = None) -> int | None:
    """Called whenever a task is completed. Counts the day (once) and pays its streak XP. Returns the new streak
    length when this was the first task of the day, else None."""
    when = when or utcnow()
    today = ledger.day_of(when)
    s = _row(db, member)
    if s.last_day == today:
        return None
    yesterday = (_d(today) - timedelta(days=1)).isoformat()
    s.current = s.current + 1 if s.last_day == yesterday else 1
    s.best = max(s.best, s.current)
    s.last_day = today
    n = s.current
    bonus = MILESTONES.get(n)
    why = f"🔥 Day {n} streak" + (f" · {n}-day milestone!" if bonus else "")
    ledger.award(db, member, "streak.day", f"streak:{today}", reason=why, xp=reward(n), occurred_at=when, commit=False)
    db.flush()
    return n


def status(db: Session, member: Member, now: datetime | None = None) -> dict[str, Any]:
    """For the Passport: the live streak (0 once a day was missed), best, today's state, what's next, the last 14 days."""
    now = now or utcnow()
    today = ledger.day_of(now)
    yesterday = (_d(today) - timedelta(days=1)).isoformat()
    s = db.scalar(select(MemberStreak).where(MemberStreak.member_id == member.id))
    last = s.last_day if s else None
    alive = last in (today, yesterday)
    current = s.current if s and alive else 0
    done_today = last == today
    nxt = current + 1 if not done_today else current + 1  # the day the next task (today or tomorrow) would count as
    upcoming = next((m for m in sorted(MILESTONES) if m > current), None)
    start = (_d(today) - timedelta(days=13)).isoformat()
    paid = {e.day: e.xp for e in db.scalars(select(ReputationEvent).where(
        ReputationEvent.member_id == member.id, ReputationEvent.event_type == "streak.day", ReputationEvent.day >= start,
        ReputationEvent.reversed_at.is_(None)))}
    days = []
    for i in range(13, -1, -1):
        d = (_d(today) - timedelta(days=i)).isoformat()
        days.append({"day": d, "done": d in paid, "xp": paid.get(d, 0), "today": d == today})
    return {
        "current": current, "best": s.best if s else 0, "done_today": done_today, "at_risk": not done_today and current > 0,
        "last_day": last, "next_day": nxt, "next_reward": reward(nxt), "next_is_milestone": nxt in MILESTONES,
        "next_milestone": upcoming, "next_milestone_bonus": MILESTONES.get(upcoming, 0) if upcoming else 0,
        "today_xp": paid.get(today, 0), "days": days, "milestones": MILESTONES,
    }
