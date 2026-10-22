"""Read & Earn: Price Chat's latest X posts and replies, one at a time, in the Kiwi Passport.

What earns XP (and what doesn't), by the user's decisions of 2026-10-06 and 2026-10-17 and X's Developer Policy ("no
compensation for actions on X"):
  - Read & Earn needs X connected through Price Chat's own "Connect X" (OAuth; never a password, never X's login in a
    frame). Without it the feed is empty and reading pays nothing: checked here, not only hidden in the browser.
  - Reading a post HERE, on Price Chat's own site, earns a little XP (`website.x_post_read`, +1, daily cap in the
    dashboard's XP rules). It's an action on our site, verified like Watch & Earn: the server records when the post was
    shown and only pays once that much time (READ_SECONDS) has really passed. Once per post, ever.
  - Liking, reposting or replying on X never earns anything. Like opens the post on X (X removed likes from its
    self-serve API in April 2026); Repost runs in Price Chat with the member's OWN X token (members/x_actions.py),
    never the @pricechat brand credentials. Both are support, shown as such.

Posts come from what the X integration already stores (social_posts, is_own): Price Chat's originals, quotes and its
replies under other people's posts. A reply carries the post it answers (text + author) in `referenced`, included by X
with our own timeline or looked up once by the X reading job, so the member sees the conversation.
"""

import re
from datetime import timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.members import missions
from app.members import xp as ledger
from app.models.enums import ContentStatus, Platform
from app.models.members import Member, MemberAction, ReputationEvent
from app.models.social import SocialPost

RULE = "website.x_post_read"
READ_SECONDS = 6  # on screen, in this tab, before reading a post counts
FEED_DAYS = 14  # how far back the feed reaches
MIN_CHARS = 15  # what's left after mentions and links: "yo", "GM" or a bare link isn't worth featuring
SHOWN, READ, LIKE_OPENED = "x_view", "x_read", "x_like_open"
CLOCK_SLACK = 1

_LEAD = re.compile(r"^(?:@\w+\s+)+")
_LINK = re.compile(r"https?://t\.co/\w+")


class FeedError(ValueError):
    pass


def _clean(text: str) -> str:
    """What the member reads: X hides the leading @mentions of a reply and the t.co link of attached media."""
    return re.sub(r"\s+\n", "\n", _LINK.sub("", _LEAD.sub("", text or ""))).strip()


def _ref(p: SocialPost) -> dict[str, Any] | None:
    return next((r for r in p.referenced or [] if r.get("type") in ("replied_to", "quoted")), None)


def _eligible(p: SocialPost) -> bool:
    if (p.text or "").startswith("RT @") or any(r.get("type") == "retweeted" for r in p.referenced or []):
        return False
    return len(_clean(p.text)) >= MIN_CHARS


def candidates(db: Session) -> list[SocialPost]:
    """Price Chat's recent posts worth featuring, newest first."""
    rows = db.scalars(select(SocialPost).where(
        SocialPost.platform == Platform.X, SocialPost.is_own.is_(True), SocialPost.status == ContentStatus.PUBLISHED,
        SocialPost.external_id.is_not(None), SocialPost.posted_at >= utcnow() - timedelta(days=FEED_DAYS),
    ).order_by(SocialPost.posted_at.desc()))
    return [p for p in rows if _eligible(p)]


def card(p: SocialPost) -> dict[str, Any]:
    ref = _ref(p)
    kind = "reply" if ref and ref["type"] == "replied_to" else "quote" if ref else "post"
    parent = None
    if ref and ref.get("text"):
        parent = {"text": _clean(ref["text"]), "name": ref.get("name"), "username": ref.get("username")}
    lead = _LEAD.match(p.text or "")
    replying_to = (parent or {}).get("username") or (lead.group(0).split()[0].lstrip("@") if lead and kind == "reply" else None)
    m = p.metrics or {}
    return {
        "id": p.external_id, "kind": kind, "text": _clean(p.text), "url": p.url or f"https://x.com/i/status/{p.external_id}",
        "posted_at": p.posted_at.isoformat() if p.posted_at else None, "parent": parent, "replying_to": replying_to,
        "author": {"name": "Price Chat", "username": p.author_handle or "pricechat"},
        "media": [x for x in (p.media or []) if str(x.get("url", "")).startswith("https://pbs.twimg.com/")][:4],
        "likes": m.get("likes"), "replies": m.get("replies"), "reposts": m.get("reposts"),
    }


def _done(db: Session, member: Member) -> set[str]:
    return {t.removeprefix("x:") for t in db.scalars(select(MemberAction.target).where(
        MemberAction.member_id == member.id, MemberAction.kind == READ))}


def _today(db: Session, member: Member) -> tuple[int, int, int]:
    """(XP earned from reading today, the daily cap, XP per post) from the rule the dashboard edits."""
    rule = missions._rule(db, RULE)
    on = bool(rule and rule.enabled and rule.availability == "available") and not member.xp_suspended
    earned = int(db.scalar(select(func.coalesce(func.sum(ReputationEvent.xp), 0)).where(
        ReputationEvent.member_id == member.id, ReputationEvent.event_type == RULE,
        ReputationEvent.day == ledger.day_of(utcnow()), ReputationEvent.xp > 0)) or 0)
    return earned, (rule.daily_cap or 0) if rule else 0, (rule.xp if rule and on else 0)


def x_linked(db: Session, member: Member) -> bool:
    """Read & Earn is for members who connected X with Price Chat's own "Connect X" sign-in (OAuth): the server checks."""
    from app.models.members import LinkedIdentity

    return db.scalar(select(LinkedIdentity.id).where(LinkedIdentity.member_id == member.id, LinkedIdentity.platform == "x",
                                                     LinkedIdentity.status == "active")) is not None


def _actions(db: Session, member: Member, ids: list[str]) -> dict[str, set[str]]:
    """What the member already did from here, per post (X confirmed replies / reposts; Like only ever opens X)."""
    targets = ids + [f"x:{i}" for i in ids]
    out: dict[str, set[str]] = {i: set() for i in ids}
    for kind, target in db.execute(select(MemberAction.kind, MemberAction.target).where(
            MemberAction.member_id == member.id, MemberAction.kind.in_(("x_reply", "x_repost", LIKE_OPENED)),
            MemberAction.target.in_(targets))):
        out.setdefault(target.removeprefix("x:"), set()).add({"x_reply": "reply", "x_repost": "repost"}.get(kind, "like"))
    return out


def feed(db: Session, member: Member, limit: int = 4) -> dict[str, Any]:
    """What's left for this member: the next post first, then a few to come. Nothing until X is connected."""
    earned, cap, per = _today(db, member)
    read_today = int(db.scalar(select(func.count()).select_from(MemberAction).where(
        MemberAction.member_id == member.id, MemberAction.kind == READ, MemberAction.day == ledger.day_of(utcnow()))) or 0)
    base = {"read_seconds": READ_SECONDS, "today": {"earned": earned, "cap": cap, "read": read_today, "per_post": per}}
    if not x_linked(db, member):
        return {**base, "items": [], "remaining": 0, "needs_x": True}
    done = _done(db, member)
    left = [p for p in candidates(db) if p.external_id not in done]
    xp = per if per and (not cap or earned + per <= cap) else 0
    items = [{**card(p), "xp": xp} for p in left[:limit]]
    acts = _actions(db, member, [i["id"] for i in items])
    for i in items:
        i["done"] = sorted(acts.get(i["id"], set()))
    return {**base, "items": items, "remaining": len(left), "needs_x": False}


def _post(db: Session, post_id: str) -> SocialPost:
    p = db.scalar(select(SocialPost).where(SocialPost.platform == Platform.X, SocialPost.external_id == post_id,
                                           SocialPost.is_own.is_(True)))
    if p is None or not _eligible(p):
        raise FeedError("This post isn't in the feed.")
    return p


def shown(db: Session, member: Member, post_id: str) -> dict[str, Any]:
    """The post is on the member's screen now (the reading clock starts). Pays nothing."""
    if not x_linked(db, member):
        return {"ok": False, "code": "connect_x", "message": "Connect X first."}
    p = _post(db, post_id)
    missions.record_action(db, member, SHOWN, "x", f"x:{p.external_id}")
    db.commit()
    return {"ok": True, "read_seconds": READ_SECONDS}


def read(db: Session, member: Member, post_id: str) -> dict[str, Any]:
    """Read here for READ_SECONDS: marks it done and pays (once per post, within the daily cap). X must be connected."""
    if not x_linked(db, member):
        return {"ok": False, "code": "connect_x", "xp": 0, "message": "Connect X first."}
    p = _post(db, post_id)
    target = f"x:{p.external_id}"
    if p.external_id in _done(db, member):
        return {"ok": True, "code": "already", "xp": 0, "message": "Already read."}
    started = db.scalar(select(MemberAction).where(MemberAction.member_id == member.id, MemberAction.kind == SHOWN,
                                                   MemberAction.target == target, MemberAction.day == ledger.day_of(utcnow())))
    if started is None:
        return {"ok": False, "code": "not_started", "xp": 0, "message": "Open the post here first."}
    at = started.created_at if started.created_at.tzinfo else started.created_at.replace(tzinfo=timezone.utc)
    if (utcnow() - at).total_seconds() + CLOCK_SLACK < READ_SECONDS:
        return {"ok": False, "code": "too_soon", "xp": 0, "message": "Keep reading."}
    missions.record_action(db, member, READ, "x", target)
    snippet = card(p)["text"].replace("\n", " ")
    snippet = snippet if len(snippet) <= 60 else snippet[:57].rstrip() + "…"
    r = ledger.award(db, member, RULE, f"x-read:{p.external_id}", reason=f"Read Price Chat on X: “{snippet}”", commit=False)
    db.commit()
    if r.awarded:
        return {"ok": True, "code": "paid", "xp": r.xp, "message": "Counted."}
    why = {"daily_cap": "today's reading XP is used up", "overall_cap": "today's XP limit is reached"}.get(r.refused or "", "")
    return {"ok": True, "code": "refused", "xp": 0, "message": f"Read. No XP this time{': ' + why if why else ''}."}


def like_opened(db: Session, member: Member, post_id: str) -> None:
    """The member opened the post on X to like it (support only: never XP, and we can't know if they liked it)."""
    p = _post(db, post_id)
    missions.record_action(db, member, LIKE_OPENED, "x", f"x:{p.external_id}")
    db.commit()


def stats(db: Session, days: int = 7) -> dict[str, Any]:
    """Dashboard only: how the feed is used (counts, no member details)."""
    since = ledger.day_of(utcnow() - timedelta(days=days - 1))
    count = lambda kind: int(db.scalar(select(func.count()).select_from(MemberAction).where(  # noqa: E731
        MemberAction.kind == kind, MemberAction.day >= since)) or 0)
    xp = db.execute(select(func.count(), func.coalesce(func.sum(ReputationEvent.xp), 0)).where(
        ReputationEvent.event_type == RULE, ReputationEvent.day >= since, ReputationEvent.xp > 0)).one()
    reposts = int(db.scalar(select(func.count()).select_from(MemberAction).where(
        MemberAction.kind == "x_repost", MemberAction.day >= since)) or 0)
    return {"days": days, "posts_in_feed": len(candidates(db)), "shown": count(SHOWN), "read": count(READ),
            "xp_awards": int(xp[0]), "xp_total": int(xp[1]), "like_opened": count(LIKE_OPENED), "reposted_from_here": reposts,
            "read_seconds": READ_SECONDS}
