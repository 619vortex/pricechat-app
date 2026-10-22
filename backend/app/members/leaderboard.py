"""Kiwi leaderboard: top lists plus the signed-in member's OWN position, always computed here (never in the browser).

Ordering is the same everywhere: value descending, then the earlier member first. Opted-out and banned members never
appear and have no position. Metrics: "score" (Kiwi Rank Score all time; XP earned in the period otherwise), "xp",
"legacy", "telegram" (see service.leaderboard_v2) and "referrals" (qualified invites).
"""

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, func, or_, select, union_all
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.members import service
from app.members import xp as ledger
from app.members.catalog import ensure_catalog
from app.models.members import CrewReferral, Member, Referral, ReputationEvent

PERIODS = {"week": timedelta(days=7), "month": timedelta(days=30)}


def _since(period: str) -> datetime | None:
    span = PERIODS.get(period)
    return utcnow() - span if span else None


def _visible():  # type: ignore[no-untyped-def]
    return and_(Member.leaderboard_opt_out.is_(False), Member.status == "active")


def _referral_values(period: str):  # type: ignore[no-untyped-def]
    """Verified invites: qualified Passport referrals plus friends verified in the Price Chat Telegram (Invite your crew)."""
    since = _since(period)
    web = select(Referral.referrer_id.label("mid")).where(Referral.status == "qualified", Referral.referrer_id.is_not(None))
    tg = select(CrewReferral.referrer_id.label("mid")).where(CrewReferral.status == "verified", CrewReferral.referrer_id.is_not(None))
    if since is not None:
        web = web.where(Referral.qualified_at >= since)
        tg = tg.where(CrewReferral.verified_at >= since)
    both = union_all(web, tg).subquery()
    return select(both.c.mid.label("mid"), func.count().label("v")).group_by(both.c.mid).subquery()


def _xp_values(period: str, metric: str):  # type: ignore[no-untyped-def]
    since = _since(period)
    stmt = select(ReputationEvent.member_id.label("mid"), func.coalesce(func.sum(ReputationEvent.xp), 0).label("v")).where(
        ReputationEvent.category == "reputation")
    if metric == "telegram":
        stmt = stmt.where(ReputationEvent.event_type.like("telegram.%"))
    if since is not None:
        stmt = stmt.where(ReputationEvent.occurred_at >= since)
    return stmt.group_by(ReputationEvent.member_id).subquery()


def _value_source(period: str, metric: str):  # type: ignore[no-untyped-def]
    """(value column, extra FROM / join) for the metric, matching service.leaderboard_v2's ordering."""
    if metric == "referrals":
        return _referral_values(period)
    if metric in ("score", "legacy") and (period == "all" or metric == "legacy"):
        return None  # a column on Member
    return _xp_values(period, metric)


def position(db: Session, member: Member, period: str, metric: str) -> dict[str, Any] | None:
    """The member's place on this board (1-based), or None when they're hidden / not on it yet (a value of 0)."""
    if member.leaderboard_opt_out or member.status != "active":
        return None
    sub = _value_source(period, metric)
    if sub is None:
        col = Member.rank_score if metric == "score" else Member.legacy_points
        mine = int(getattr(member, "rank_score" if metric == "score" else "legacy_points") or 0)
        if mine <= 0:
            return None
        ahead = db.scalar(select(func.count()).select_from(Member).where(
            _visible(), or_(col > mine, and_(col == mine, Member.created_at < member.created_at)))) or 0
    else:
        mine = int(db.scalar(select(sub.c.v).where(sub.c.mid == member.id)) or 0)
        if mine <= 0:
            return None
        ahead = db.scalar(select(func.count()).select_from(Member).join(sub, sub.c.mid == Member.id).where(
            _visible(), or_(sub.c.v > mine, and_(sub.c.v == mine, Member.created_at < member.created_at)))) or 0
    return {"position": int(ahead) + 1, "value": mine}


def board(db: Session, period: str, metric: str, limit: int, me: Member | None) -> dict[str, Any]:
    ensure_catalog(db)
    if metric == "referrals":
        sub = _referral_values(period)
        rows = db.execute(select(Member, sub.c.v).join(sub, sub.c.mid == Member.id).where(_visible())
                          .order_by(sub.c.v.desc(), Member.created_at).limit(limit)).all()
        all_ranks = ledger.ranks(db)
        out = {"note": "", "rows": [{"position": i + 1, "username": m.username, "value": int(v), "rank_score": m.rank_score,
                                     "xp": m.xp_total, "legacy_points": m.legacy_points,
                                     "rank": service.rank_out(ledger.rank_for(db, m.rank_score, all_ranks))}
                                    for i, (m, v) in enumerate(rows)]}
    else:
        out = service.leaderboard_v2(db, period, metric, limit)
    mine = None
    if me is not None:
        pos = position(db, me, period, metric)
        mine = {"username": me.username, "rank": service.rank_out(ledger.rank_for(db, me.rank_score)), "hidden": me.leaderboard_opt_out,
                "position": pos["position"] if pos else None, "value": pos["value"] if pos else 0}
    return {**out, "me": mine}


def standing(db: Session, member: Member) -> dict[str, Any] | None:
    """For the Passport: the member's all-time place by Kiwi Rank Score (what "↑ #84" compares)."""
    pos = position(db, member, "all", "score")
    return {"position": pos["position"], "board": "all"} if pos else None
