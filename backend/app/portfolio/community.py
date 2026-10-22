"""Public-portfolio community: comments (one reply level), reports, and community condition opinions on raw cards.

Abuse protection: members only; a few comments a minute and a daily cap per member; links and duplicate text refused;
the portfolio owner can delete any comment on their portfolio; reports go to the owner of Price Chat (admin).
Condition opinions: one per member per card (changeable), never from the card's owner, shown as a distribution only after
MIN_VOTES independent members answered, and always labelled "community opinion", never a grade.
"""

import re
from collections import Counter
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.members.service import MemberError
from app.models.members import Member
from app.models.portfolio import PokemonConditionVote, PokemonHolding, PokemonPortfolio, PokemonPortfolioComment, PokemonPortfolioReport

PER_MINUTE = 3
PER_DAY = 60
MIN_VOTES = 5
MIN_ACCOUNT_AGE = timedelta(hours=1)  # brand-new accounts can read but not post yet
_LINK = re.compile(r"(https?://|www\.|\b[\w-]+\.(com|net|io|xyz|ru|gg|me)\b)", re.I)


def _public_portfolio(db: Session, username: str) -> tuple[PokemonPortfolio, Member]:
    m = db.scalar(select(Member).where(Member.username_key == username.lower(), Member.status == "active"))
    p = db.scalar(select(PokemonPortfolio).where(PokemonPortfolio.member_id == m.id)) if m else None
    if m is None or p is None or p.visibility != "public" or p.review_status == "hidden":
        raise MemberError("This portfolio is private, or doesn't exist.")
    return p, m


def _eligible(member: Member) -> None:
    now = utcnow()
    created = member.created_at if member.created_at.tzinfo else member.created_at.replace(tzinfo=now.tzinfo)
    if now - created < MIN_ACCOUNT_AGE:
        raise MemberError("New accounts can take part after their first hour.")
    if member.xp_suspended:
        raise MemberError("Taking part is paused on this account.")


def _can_post(db: Session, member: Member) -> None:
    now = utcnow()
    _eligible(member)
    recent = db.scalar(select(func.count()).select_from(PokemonPortfolioComment).where(
        PokemonPortfolioComment.member_id == member.id, PokemonPortfolioComment.created_at >= now - timedelta(minutes=1))) or 0
    today = db.scalar(select(func.count()).select_from(PokemonPortfolioComment).where(
        PokemonPortfolioComment.member_id == member.id, PokemonPortfolioComment.created_at >= now - timedelta(days=1))) or 0
    if recent >= PER_MINUTE or today >= PER_DAY:
        raise MemberError("You're commenting very fast. Please wait a little.")


def comment_out(c: PokemonPortfolioComment, author: Member, viewer: Member | None, owner_id: int) -> dict[str, Any]:
    return {"id": c.id, "parent_id": c.parent_id, "holding_id": c.holding_id, "author": author.username,
            "body": c.body if c.status == "visible" else None, "deleted": c.status != "visible", "at": c.created_at.isoformat(),
            "can_delete": viewer is not None and c.status == "visible" and viewer.id in (c.member_id, owner_id)}


def list_comments(db: Session, username: str, viewer: Member | None, limit: int = 50, before: int | None = None) -> dict[str, Any]:
    p, owner = _public_portfolio(db, username)
    q = select(PokemonPortfolioComment, Member).join(Member, Member.id == PokemonPortfolioComment.member_id).where(
        PokemonPortfolioComment.portfolio_id == p.id, PokemonPortfolioComment.parent_id.is_(None), PokemonPortfolioComment.status != "hidden")
    if before:
        q = q.where(PokemonPortfolioComment.id < before)
    tops = db.execute(q.order_by(PokemonPortfolioComment.id.desc()).limit(min(limit, 100))).all()
    ids = [c.id for c, _ in tops]
    replies: dict[int, list[dict[str, Any]]] = {}
    for c, a in db.execute(select(PokemonPortfolioComment, Member).join(Member, Member.id == PokemonPortfolioComment.member_id).where(
            PokemonPortfolioComment.parent_id.in_(ids or [0]), PokemonPortfolioComment.status != "hidden").order_by(PokemonPortfolioComment.id)):
        replies.setdefault(c.parent_id, []).append(comment_out(c, a, viewer, owner.id))
    return {"enabled": p.comments_enabled, "comments": [{**comment_out(c, a, viewer, owner.id), "replies": replies.get(c.id, [])} for c, a in tops]}


def add_comment(db: Session, username: str, member: Member, body: str, parent_id: int | None = None, holding_id: int | None = None) -> dict[str, Any]:
    p, owner = _public_portfolio(db, username)
    if not p.comments_enabled:
        raise MemberError("Comments are turned off on this portfolio.")
    text = re.sub(r"\s+", " ", (body or "")).strip()
    if not 1 <= len(text) <= 1000:
        raise MemberError("Comments are 1-1000 characters.")
    if _LINK.search(text):
        raise MemberError("Links aren't allowed in comments.")
    _can_post(db, member)
    if parent_id is not None:
        parent = db.get(PokemonPortfolioComment, parent_id)
        if parent is None or parent.portfolio_id != p.id or parent.status != "visible":
            raise MemberError("That comment is gone.")
        if parent.parent_id is not None:
            parent_id = parent.parent_id  # replies are one level deep: a reply to a reply joins the same thread
    if holding_id is not None:
        h = db.get(PokemonHolding, holding_id)
        if h is None or h.portfolio_id != p.id or h.removed_at is not None or not h.is_public:
            raise MemberError("That card isn't in this portfolio.")
    dup = db.scalar(select(PokemonPortfolioComment.id).where(PokemonPortfolioComment.member_id == member.id, PokemonPortfolioComment.body == text,
                                                            PokemonPortfolioComment.created_at >= utcnow() - timedelta(days=1)).limit(1))
    if dup:
        raise MemberError("You already posted that.")
    c = PokemonPortfolioComment(portfolio_id=p.id, member_id=member.id, parent_id=parent_id, holding_id=holding_id, body=text)
    db.add(c)
    db.commit()
    return comment_out(c, member, member, owner.id)


def delete_comment(db: Session, comment_id: int, member: Member) -> None:
    c = db.get(PokemonPortfolioComment, comment_id)
    p = db.get(PokemonPortfolio, c.portfolio_id) if c else None
    if c is None or p is None or c.status != "visible" or member.id not in (c.member_id, p.member_id):
        raise MemberError("You can't delete that comment.")
    c.status = "deleted"  # the thread keeps its shape; the text is gone
    db.commit()


def report(db: Session, member: Member, target: str, target_id: int, reason: str) -> None:
    if target not in ("comment", "portfolio", "holding"):
        raise MemberError("Unknown report.")
    reason = (reason or "").strip()[:280]
    if len(reason) < 3:
        raise MemberError("Tell us briefly what's wrong.")
    open_ = db.scalar(select(func.count()).select_from(PokemonPortfolioReport).where(
        PokemonPortfolioReport.reporter_id == member.id, PokemonPortfolioReport.created_at >= utcnow() - timedelta(days=1))) or 0
    if open_ >= 20:
        raise MemberError("You've sent a lot of reports today. Thank you: we'll look at them.")
    db.add(PokemonPortfolioReport(reporter_id=member.id, target=target, target_id=target_id, reason=reason))
    db.commit()


# --- community condition opinions -------------------------------------------------------------------------------------


def condition_summary(db: Session, h: PokemonHolding, viewer: Member | None) -> dict[str, Any] | None:
    if h.kind != "raw" or not h.community_review:
        return None
    votes = list(db.scalars(select(PokemonConditionVote).where(PokemonConditionVote.holding_id == h.id)))
    mine = next((v for v in votes if viewer is not None and v.member_id == viewer.id), None)
    out: dict[str, Any] = {"open": True, "responses": len(votes), "min_responses": MIN_VOTES, "mine": mine.estimate if mine else None,
                           "label": "Community opinion: not an official grade"}
    if len(votes) >= MIN_VOTES:
        counts = Counter(v.estimate for v in votes)
        out["distribution"] = [{"estimate": k, "share": round(n / len(votes) * 100)} for k, n in sorted(counts.items())]
        out["notes"] = [v.note for v in votes if v.note][-10:]
    return out


def vote_condition(db: Session, holding_id: int, member: Member, estimate: int, note: str = "") -> dict[str, Any]:
    h = db.get(PokemonHolding, holding_id)
    p = db.get(PokemonPortfolio, h.portfolio_id) if h else None
    if h is None or p is None or h.removed_at is not None or not h.community_review or h.kind != "raw" or p.visibility != "public" or not h.is_public:
        raise MemberError("This card isn't asking for condition opinions.")
    if p.member_id == member.id:
        raise MemberError("Owners can't rate their own card.")
    if not 1 <= int(estimate) <= 10:
        raise MemberError("Choose 1-10.")
    _eligible(member)  # one opinion per member per card: no rate needed beyond that
    v = db.scalar(select(PokemonConditionVote).where(PokemonConditionVote.holding_id == h.id, PokemonConditionVote.member_id == member.id))
    if v is None:
        v = PokemonConditionVote(holding_id=h.id, member_id=member.id, estimate=int(estimate))
        db.add(v)
    v.estimate, v.note = int(estimate), (note or "").strip()[:280]
    if _LINK.search(v.note):
        raise MemberError("Links aren't allowed.")
    db.commit()
    return condition_summary(db, h, member) or {}
