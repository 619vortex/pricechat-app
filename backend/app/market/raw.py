"""RAW (ungraded) cards on the marketplace: AI condition, members' own grades, and listing comments.

A raw listing is made from a raw portfolio card that the AI condition assistant has reviewed (front + back photos, see
`app/portfolio/condition.py`). Its condition label (NM / LP / MP / HP / DMG) comes from that estimate, never from the
seller's say-so, and the full report is shown on the listing. Members can add their own 1-10 grade and comment. None of
it is a professional grade: a raw card is only ever described as an estimate.
"""

from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.members.service import MemberError
from app.models.market import MarketComment, MarketConditionVote, MarketListing
from app.models.members import Member

COMMENTS_PER_DAY = 30
REPORT_KEYS = ("low", "high", "confidence", "centering", "corners", "edges", "surface", "issues", "label", "at")


def label_for(low: float, high: float) -> str:
    """The AI's 1-10 range as a raw condition: by its midpoint (phone photos can't show a 10, so the scale tops at 9)."""
    m = (low + high) / 2
    return "NM" if m >= 8 else "LP" if m >= 6 else "MP" if m >= 4 else "HP" if m >= 2 else "DMG"


def report_out(report: dict[str, Any] | None) -> dict[str, Any] | None:
    return {k: report.get(k) for k in REPORT_KEYS} if report else None


def _listing(db: Session, listing_id: int) -> MarketListing:
    l = db.get(MarketListing, listing_id)
    if l is None or l.status not in ("active", "reserved", "sold"):
        raise MemberError("Not found.")
    return l


def community(db: Session, listing_id: int, viewer: Member | None) -> dict[str, Any]:
    rows = db.scalars(select(MarketConditionVote).where(MarketConditionVote.listing_id == listing_id)).all()
    mine = next((v for v in rows if viewer is not None and v.member_id == viewer.id), None)
    return {"count": len(rows), "average": round(sum(v.estimate for v in rows) / len(rows), 1) if rows else None,
            "mine": {"estimate": mine.estimate, "note": mine.note} if mine else None}


def vote(db: Session, member: Member, listing_id: int, estimate: int, note: str = "") -> dict[str, Any]:
    l = _listing(db, listing_id)
    if l.seller_id == member.id:
        raise MemberError("Sellers can't grade their own card.")
    if not 1 <= estimate <= 10:
        raise MemberError("Pick a grade from 1 to 10.")
    v = db.scalar(select(MarketConditionVote).where(MarketConditionVote.listing_id == l.id, MarketConditionVote.member_id == member.id))
    if v is None:
        db.add(MarketConditionVote(listing_id=l.id, member_id=member.id, estimate=estimate, note=note.strip()[:280]))
    else:
        v.estimate, v.note = estimate, note.strip()[:280]
    db.commit()
    return community(db, l.id, member)


def comments(db: Session, listing_id: int, viewer: Member | None) -> list[dict[str, Any]]:
    l = _listing(db, listing_id)
    rows = db.execute(select(MarketComment, Member).join(Member, Member.id == MarketComment.member_id)
                      .where(MarketComment.listing_id == l.id).order_by(MarketComment.id).limit(300)).all()
    return [{"id": c.id, "parent_id": c.parent_id, "at": c.created_at.isoformat(), "username": m.username, "seller": m.id == l.seller_id,
             "body": "" if c.deleted_at else c.body, "deleted": c.deleted_at is not None,
             "mine": viewer is not None and viewer.id == c.member_id} for c, m in rows]


def add_comment(db: Session, member: Member, listing_id: int, body: str, parent_id: int | None = None) -> list[dict[str, Any]]:
    l = _listing(db, listing_id)
    body = body.strip()
    if not 2 <= len(body) <= 1000:
        raise MemberError("Write between 2 and 1,000 characters.")
    since = utcnow() - timedelta(days=1)
    if (db.scalar(select(func.count()).select_from(MarketComment).where(MarketComment.member_id == member.id,
                                                                        MarketComment.created_at >= since)) or 0) >= COMMENTS_PER_DAY:
        raise MemberError(f"Up to {COMMENTS_PER_DAY} comments a day.")
    if parent_id is not None:
        parent = db.get(MarketComment, parent_id)
        if parent is None or parent.listing_id != l.id or parent.parent_id is not None:
            raise MemberError("You can reply to a comment, not to a reply.")
    db.add(MarketComment(listing_id=l.id, member_id=member.id, parent_id=parent_id, body=body))
    db.commit()
    return comments(db, l.id, member)


def delete_comment(db: Session, member: Member | None, comment_id: int) -> None:
    """The author, or an admin (member None), removes a comment; replies stay, the text is gone."""
    c = db.get(MarketComment, comment_id)
    if c is None or (member is not None and c.member_id != member.id):
        raise MemberError("Not found.")
    c.deleted_at = utcnow()
    db.commit()
