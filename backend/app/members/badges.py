"""Badges: achievements, separate from rank. Auto badges follow explicit criteria on verified data; the rest are
given by staff (audited). A badge is never removed automatically."""

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.members.notify import notify
from app.models.members import Badge, Member, MemberBadge, ReputationEvent


def _meets(db: Session, member: Member, criteria: dict) -> bool:
    if "joined_before" in criteria:
        return member.created_at < datetime.fromisoformat(criteria["joined_before"])
    if "member_number_max" in criteria:
        number = db.scalar(select(func.count()).select_from(Member).where(Member.id <= member.id)) or 0
        return number <= int(criteria["member_number_max"])
    if "event_type" in criteria:
        n = db.scalar(select(func.count()).select_from(ReputationEvent).where(
            ReputationEvent.member_id == member.id, ReputationEvent.event_type == criteria["event_type"],
            ReputationEvent.reversed_at.is_(None), ReputationEvent.reverses_id.is_(None))) or 0
        return n >= int(criteria.get("count", 1))
    return False


def evaluate_auto_badges(db: Session, member: Member) -> list[str]:
    owned = set(db.scalars(select(MemberBadge.badge_id).where(MemberBadge.member_id == member.id)))
    new = []
    for badge in db.scalars(select(Badge).where(Badge.kind == "auto", Badge.enabled.is_(True))):
        if badge.id not in owned and _meets(db, member, badge.criteria or {}):
            db.add(MemberBadge(member_id=member.id, badge_id=badge.id, awarded_by="system", reason=badge.description))
            notify(db, member.id, "badge", f"Badge earned: {badge.name}", badge.description, {"badge": badge.key})
            new.append(badge.key)
    if new:
        db.flush()
    return new


def give_badge(db: Session, member: Member, key: str, *, actor: str, reason: str) -> MemberBadge:
    badge = db.scalar(select(Badge).where(Badge.key == key))
    if badge is None or not badge.enabled:
        raise ValueError("unknown or disabled badge")
    if badge.kind == "partner":
        raise ValueError("partner badges are not active yet")
    existing = db.scalar(select(MemberBadge).where(MemberBadge.member_id == member.id, MemberBadge.badge_id == badge.id))
    if existing:
        raise ValueError("the member already has this badge")
    mb = MemberBadge(member_id=member.id, badge_id=badge.id, awarded_by=actor[:64], reason=reason[:256])
    db.add(mb)
    notify(db, member.id, "badge", f"Badge earned: {badge.name}", badge.description, {"badge": badge.key})
    db.flush()
    return mb
