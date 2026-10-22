"""X likes pilot: AI-recommended likes, reviewed and approved by a human one at a time.

The AI only *suggests* a like (the `like` flag on an X opportunity). This module turns those
suggestions into a short list of the strongest few candidates, filtering out anything that would
look like engagement farming: stale posts, posts that need a reply rather than a like, accounts
liked recently, repeated content, off-brand topics. Proposing a candidate puts a normal LIKE
request into the approval queue; execution still goes approval → SocialActionGuard → permit →
official X API, exactly like every other write. Nothing here can like a post by itself.
"""

import re
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config.settings import Settings
from app.connectors.state import get_connector_state
from app.db.base import utcnow
from app.models.enums import ApprovalStatus, Platform, SocialAction, SocialOpportunityKind, SocialOpportunityStatus
from app.models.safety import ActionLimit
from app.models.social import SocialPost
from app.models.social_intel import ApprovalRequest, SocialActionLog, SocialOpportunity, SocialProfile, WatchedAccount
from app.security import credentials
from app.services.brand_service import get_brand
from app.services.safety import get_or_create_policy
from app.social.guard import COUNTED, LIKE_ACCOUNT_COOLDOWN_HOURS

MAX_AGE_HOURS = 72  # older posts are not recommended
MIN_RELEVANCE = 0.5
ACCOUNT_WEEKLY_LIKES = 3  # an account liked this often in 7 days is "over-targeted"
DEFAULT_CANDIDATES = 5
NEEDS_REPLY = (SocialOpportunityKind.QUESTION, SocialOpportunityKind.MENTION)  # a like would dodge a real question
FARMING = re.compile(r"\b(giveaway|airdrop|follow back|f4f|like and retweet|like & rt|rt to win|retweet to win|drop your wallet|tag \d+ friends)\b", re.I)


def _norm(text: str) -> str:
    return " ".join(re.sub(r"https?://\S+|@\w+", "", text.lower()).split())


@dataclass(slots=True)
class LikeCandidate:
    opportunity_id: int
    topic: str
    reason: str
    score: float
    signals: list[str]
    post: dict[str, Any]
    relationship: dict[str, Any]
    pending_approval_id: int | None = None
    problems: list[str] = field(default_factory=list)

    def out(self) -> dict[str, Any]:
        return {"opportunity_id": self.opportunity_id, "suggested_action": "like", "topic": self.topic, "reason": self.reason,
                "score": round(self.score, 3), "signals": self.signals, "post": self.post, "relationship": self.relationship,
                "pending_approval_id": self.pending_approval_id}


def _limit(db: Session, agent_id: int) -> ActionLimit | None:
    policy = get_or_create_policy(db, agent_id)
    return db.scalar(select(ActionLimit).where(ActionLimit.policy_id == policy.id, ActionLimit.platform == Platform.X,
                                               ActionLimit.action == SocialAction.LIKE))


def _likes_since(db: Session, since, account: str | None = None) -> int:  # type: ignore[no-untyped-def]
    stmt = select(func.count()).select_from(SocialActionLog).where(
        SocialActionLog.platform == Platform.X, SocialActionLog.action == SocialAction.LIKE,
        SocialActionLog.outcome == "executed", SocialActionLog.created_at >= since)
    if account:
        stmt = stmt.where(SocialActionLog.target_account == account.lower())
    return int(db.scalar(stmt) or 0)


def like_status(db: Session, agent_id: int, settings: Settings) -> dict[str, Any]:
    """What the dashboard shows before any like: auth, capability, limits used, dry run."""
    creds = credentials.x_credentials(settings)
    state = get_connector_state(db, Platform.X)
    policy = get_or_create_policy(db, agent_id)
    limit = _limit(db, agent_id)
    cap = (state.capabilities or {}).get("write_like") or {}
    now = utcnow()
    if not creds.configured:
        capability, detail = "not_configured", "X is not connected"
    elif not creds.user_context:
        capability, detail = "needs_user_auth", "Liking needs the Access Token and Access Token Secret of an app with Read and write permission"
    else:
        capability = cap.get("status") or "unknown"
        detail = cap.get("detail") or ("X reported write access" if capability == "available" else "Run Test connection on X / Twitter › Connection")
    problems = []
    if capability != "available":
        problems.append(detail)
    if policy.kill_switch:
        problems.append("Social actions are paused")
    if limit is None or not limit.enabled:
        problems.append("Likes are disabled in Approvals › Permissions & safety")
    return {
        "capability": capability, "capability_detail": detail, "user_auth": creds.user_context,
        "account": state.account_username, "endpoint": "POST /2/users/{your user id}/likes (OAuth 1.0a user context)",
        "permission": "approval_required" if limit is not None and limit.enabled else "disabled",
        "automatic": "locked", "dry_run": policy.dry_run, "social_actions_active": not policy.kill_switch,
        "used_today": _likes_since(db, now - timedelta(days=1)), "daily_limit": limit.max_per_day if limit else 0,
        "used_this_hour": _likes_since(db, now - timedelta(hours=1)), "hourly_limit": limit.max_per_hour if limit else 0,
        "account_cooldown_hours": LIKE_ACCOUNT_COOLDOWN_HOURS, "cooldown_minutes": policy.cooldown_minutes,
        "ready_to_execute": not problems and not policy.dry_run, "problems": problems,
    }


def _pending_likes(db: Session) -> dict[str, int]:
    rows = db.execute(select(ApprovalRequest.target_external_id, ApprovalRequest.id).where(
        ApprovalRequest.platform == Platform.X, ApprovalRequest.action == SocialAction.LIKE,
        ApprovalRequest.status == ApprovalStatus.PENDING))
    return {str(t): i for t, i in rows if t}


def _evaluate(db: Session, o: SocialOpportunity, post: SocialPost, author: SocialProfile | None, *, avoid: list[str],
              liked_posts: set[str], liked_texts: set[str], watched: set[int]) -> LikeCandidate:
    now = utcnow()
    acc = (author.username if author else post.author_handle or "").lower()
    s = (o.engagement or {}).get("suggestions") or {}
    problems = []
    if post.is_own:
        problems.append("our own post")
    if not post.external_id or not acc:
        problems.append("post or author unknown")
    hours = (now - post.posted_at).total_seconds() / 3600 if post.posted_at else None
    if hours is None or hours > MAX_AGE_HOURS:
        problems.append("stale post (older than 3 days)" if hours is not None else "post time unknown")
    if o.relevance < MIN_RELEVANCE:
        problems.append("not clearly relevant to Price Chat")
    if o.kind in NEEDS_REPLY:
        problems.append("needs a reply, not a like")
    if FARMING.search(post.text or ""):
        problems.append("engagement farming")
    text = (post.text or "").lower()
    hit = next((t for t in avoid if t and t.lower() in text), None)
    if hit:
        problems.append(f"off-brand topic ({hit})")
    if post.external_id in liked_posts:
        problems.append("already liked")
    if _norm(post.text or "") in liked_texts:
        problems.append("duplicate content")
    recent = _likes_since(db, now - timedelta(hours=LIKE_ACCOUNT_COOLDOWN_HOURS), acc) if acc else 0
    week = _likes_since(db, now - timedelta(days=7), acc) if acc else 0
    if recent:
        problems.append(f"@{acc} was liked in the last {LIKE_ACCOUNT_COOLDOWN_HOURS} h")
    elif week >= ACCOUNT_WEEKLY_LIKES:
        problems.append(f"@{acc} already liked {week} times this week")

    is_watched = bool(author and author.id in watched)
    signals = [f"relevance {o.relevance:.2f}"]
    if is_watched:
        signals.append("monitored account")
    if author and author.followers_count:
        signals.append(f"{author.followers_count:,} followers")
    if hours is not None:
        signals.append("under 1 h old" if hours < 1 else f"{round(hours)} h old")
    if week:
        signals.append(f"liked {week}× this week")
    score = (0.55 * o.relevance + 0.2 * (1.0 if is_watched else 0.0) + 0.15 * max(0.0, 1 - (hours or MAX_AGE_HOURS) / MAX_AGE_HOURS)
             + 0.1 * min(1.0, (o.priority or 0.0)))
    who = "a monitored creator" if is_watched else f"@{acc}"
    reason = (s.get("reason") or "").strip().rstrip(".")
    reason = (f"{reason} from {who}." if reason else f"Relevant {o.topic or 'market'} discussion from {who}.") + \
        " Useful to acknowledge without adding a low-value reply."
    return LikeCandidate(
        opportunity_id=o.id, topic=o.topic, reason=reason, score=score, signals=signals, problems=problems,
        post={"id": post.id, "external_id": post.external_id, "url": post.url or (f"https://x.com/{acc}/status/{post.external_id}" if acc else None),
              "text": post.text, "author": acc or None, "author_id": author.external_id if author else None,
              "followers": author.followers_count if author else None, "posted_at": post.posted_at.isoformat() if post.posted_at else None},
        relationship={"likes_this_week": week, "watched": is_watched},
    )


def like_candidates(db: Session, agent_id: int, limit: int = DEFAULT_CANDIDATES, include_filtered: bool = False) -> dict[str, Any]:
    """The strongest few AI-recommended likes. Zero is a valid answer."""
    rows = db.execute(
        select(SocialOpportunity, SocialPost, SocialProfile)
        .join(SocialPost, SocialPost.id == SocialOpportunity.post_id)
        .outerjoin(SocialProfile, SocialProfile.id == SocialPost.author_profile_id)
        .where(SocialOpportunity.platform == Platform.X,
               SocialOpportunity.status.in_((SocialOpportunityStatus.NEW, SocialOpportunityStatus.REVIEWING)))
        .order_by(SocialOpportunity.priority.desc(), SocialOpportunity.id.desc()).limit(300)
    ).all()
    brand = get_brand(db, agent_id)
    avoid = list(brand.topics_to_avoid or []) + list(getattr(brand, "x_avoid", None) or [])
    liked = list(db.execute(select(SocialActionLog.target_external_id).where(
        SocialActionLog.action == SocialAction.LIKE, SocialActionLog.outcome.in_(COUNTED))).scalars())
    liked_posts = {str(x) for x in liked if x}
    liked_texts = {_norm(t) for t in db.scalars(select(SocialPost.text).where(SocialPost.external_id.in_(liked_posts)))} if liked_posts else set()
    watched = set(db.scalars(select(WatchedAccount.profile_id)))
    pending = _pending_likes(db)

    passed: list[LikeCandidate] = []
    filtered: list[LikeCandidate] = []
    seen_accounts: set[str] = set()
    seen_texts: set[str] = set()
    for o, post, author in rows:
        if not ((o.engagement or {}).get("suggestions") or {}).get("like"):
            continue  # the AI did not recommend a like
        c = _evaluate(db, o, post, author, avoid=avoid, liked_posts=liked_posts, liked_texts=liked_texts, watched=watched)
        c.pending_approval_id = pending.get(post.external_id or "")
        acc, norm = c.post["author"], _norm(post.text or "")
        if not c.problems and acc in seen_accounts:
            c.problems.append("another post by this account is already in the list")
        if not c.problems and norm in seen_texts:
            c.problems.append("duplicate content")
        if c.problems:
            filtered.append(c)
            continue
        seen_accounts.add(acc)
        seen_texts.add(norm)
        passed.append(c)
    passed.sort(key=lambda c: (c.pending_approval_id is None, -c.score))
    out = {"candidates": [c.out() for c in passed[:limit]], "more": max(0, len(passed) - limit), "filtered_out": len(filtered)}
    if include_filtered:
        out["filtered"] = [{**c.out(), "problems": c.problems} for c in filtered[:20]]
    return out


def candidate_for(db: Session, agent_id: int, opportunity_id: int) -> LikeCandidate:
    row = db.execute(
        select(SocialOpportunity, SocialPost, SocialProfile)
        .join(SocialPost, SocialPost.id == SocialOpportunity.post_id)
        .outerjoin(SocialProfile, SocialProfile.id == SocialPost.author_profile_id)
        .where(SocialOpportunity.id == opportunity_id, SocialOpportunity.platform == Platform.X)
    ).first()
    if row is None:
        raise LookupError("X opportunity not found")
    o, post, author = row
    brand = get_brand(db, agent_id)
    avoid = list(brand.topics_to_avoid or []) + list(getattr(brand, "x_avoid", None) or [])
    c = _evaluate(db, o, post, author, avoid=avoid, liked_posts=set(), liked_texts=set(), watched=set(db.scalars(select(WatchedAccount.profile_id))))
    c.pending_approval_id = _pending_likes(db).get(post.external_id or "")
    return c


def like_payload(db: Session, agent_id: int, settings: Settings, c: LikeCandidate) -> dict[str, Any]:
    st = like_status(db, agent_id, settings)
    p = c.post
    return {
        "origin": "like_pilot", "topic": c.topic, "reason": c.reason,
        "original_post": {"id": p["external_id"], "text": p["text"], "url": p["url"], "author": p["author"], "posted_at": p["posted_at"]},
        "target_author_id": p["author_id"], "target_profile": {"username": p["author"], "followers": p["followers"]},
        "signals": c.signals, "daily_likes_used": st["used_today"], "daily_limit": st["daily_limit"],
        "hourly_likes_used": st["used_this_hour"], "hourly_limit": st["hourly_limit"],
        "context": [f"Why: {c.reason}", *[f"Signal: {s}" for s in c.signals]],
    }
