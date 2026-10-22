"""In-site member notifications (no email / push). Personal events are stored; "new mission" is derived."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.members import MemberNotification

# XP from these sources is worth telling the member about (daily visits and video opens would only be noise).
NOTIFY_XP_SOURCES = {"telegram", "x", "youtube", "mission", "moderator", "admin", "referral", "streak", "wallet", "earn"}


def notify(db: Session, member_id: int, kind: str, title: str, body: str = "", data: dict[str, Any] | None = None) -> None:
    db.add(MemberNotification(member_id=member_id, kind=kind, title=title[:128], body=body, data=data or {}))


def unread(db: Session, member_id: int, limit: int = 30) -> list[MemberNotification]:
    return list(db.scalars(select(MemberNotification).where(MemberNotification.member_id == member_id)
                           .order_by(MemberNotification.id.desc()).limit(limit)))


def mark_read(db: Session, member_id: int, ids: list[int] | None = None) -> None:
    stmt = select(MemberNotification).where(MemberNotification.member_id == member_id, MemberNotification.read_at.is_(None))
    if ids:
        stmt = stmt.where(MemberNotification.id.in_(ids))
    for n in db.scalars(stmt):
        n.read_at = utcnow()
    db.commit()
