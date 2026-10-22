"""Supervised social growth pilot: one opportunity feed from Telegram + X, ranked with visible signals,
filtered for quality, reviewed by a human in short sessions, measured afterwards.

Nothing here executes anything: X actions go through the approval queue and the SocialActionGuard.
"""

import math
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.community import CommunityQuestion, CommunityTopic, ContentOpportunity
from app.models.enums import (
    ApprovalStatus,
    CrossSignalStatus,
    FeedbackDecision,
    FeedbackTarget,
    OpportunityStatus,
    Platform,
    SocialAction,
    SocialOpportunityStatus,
)
from app.models.social import SocialPost
from app.models.social_intel import (
    ApprovalRequest,
    CrossSignal,
    DiscoveryIgnore,
    FeedbackEvent,
    SocialActionLog,
    SocialOpportunity,
    SocialProfile,
    SocialRelationship,
    WatchedAccount,
)

# Ranking weights: separate signals, combined into one priority that is shown next to them.
WEIGHTS = {
    "price_chat_relevance": 0.16,
    "telegram_relevance": 0.10,
    "x_relevance": 0.08,
    "cross_platform": 0.10,
    "recency": 0.10,
    "conversation_activity": 0.07,
    "unique_participants": 0.06,
    "author_relevance": 0.06,
    "community_interest": 0.07,
    "novelty": 0.06,
    "relationship_history": 0.04,
    "contribution": 0.06,
    "confidence": 0.04,
}
HIGH, MEDIUM = 0.55, 0.38
STALE_X_HOURS = 48
STALE_TELEGRAM_DAYS = 7
RECENT_TARGET_HOURS = 24
SESSION_MIN, SESSION_MAX = 5, 10

X_ACTIONS = {"reply": "X reply", "standalone_post": "X standalone post", "quote_candidate": "Repost/quote candidate"}


@dataclass(slots=True)
class FeedItem:
    key: str  # "x:12" or "telegram:7"
    kind: str  # x | telegram
    id: int
    source: str  # "X" | "Telegram" | "Telegram + X"
    topic: str
    title: str
    why: str
    evidence: list[str]
    recency_hours: float | None
    relationship: str | None
    community: str | None
    knowledge: list[str]
    suggested_actions: list[str]
    signals: dict[str, float]
    priority: float
    label: str
    reasons: list[str]
    problems: list[str] = field(default_factory=list)
    post: dict[str, Any] | None = None
    pending_approval_id: int | None = None

    @property
    def passed(self) -> bool:
        return not self.problems

    def out(self) -> dict[str, Any]:
        d = asdict(self)
        d["passed"] = self.passed
        return d


def _aware(dt: datetime | None) -> datetime | None:
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _hours(dt: datetime | None, now: datetime) -> float | None:
    dt = _aware(dt)
    return round(max(0.0, (now - dt).total_seconds() / 3600), 1) if dt else None


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]{4,}", text.lower())}


def _similar(a: str, b: str) -> float:
    wa, wb = _words(a), _words(b)
    return len(wa & wb) / max(1, len(wa | wb))


def _label(p: float) -> str:
    return "high" if p >= HIGH else "medium" if p >= MEDIUM else "low"


def _priority(signals: dict[str, float]) -> float:
    return round(max(0.0, min(1.0, sum(WEIGHTS[k] * float(signals.get(k, 0.0) or 0.0) for k in WEIGHTS))), 3)


def _recent_own_texts(db: Session, now: datetime) -> list[str]:
    return [p.text for p in db.scalars(select(SocialPost).where(SocialPost.platform == Platform.X, SocialPost.is_own.is_(True),
                                                                 SocialPost.posted_at >= now - timedelta(days=7)))]


def _recently_targeted(db: Session, account: str | None, now: datetime) -> int:
    if not account:
        return 0
    return int(db.scalar(select(func.count()).select_from(SocialActionLog).where(
        SocialActionLog.target_account == account.lower(), SocialActionLog.outcome == "executed",
        SocialActionLog.created_at >= now - timedelta(hours=RECENT_TARGET_HOURS))) or 0)


def _pending_for(db: Session, kind: str, oid: int) -> int | None:
    source_kind = "social_opportunity" if kind == "x" else "opportunity"
    return db.scalar(select(ApprovalRequest.id).where(ApprovalRequest.source_kind == source_kind, ApprovalRequest.source_id == oid,
                                                      ApprovalRequest.status == ApprovalStatus.PENDING).order_by(ApprovalRequest.id.desc()))


def x_item(db: Session, o: SocialOpportunity, now: datetime, cross_by_post: dict[int, CrossSignal], own_texts: list[str]) -> FeedItem | None:
    post = db.get(SocialPost, o.post_id)
    if post is None:
        return None
    author = db.get(SocialProfile, post.author_profile_id) if post.author_profile_id else None
    rel = db.scalar(select(SocialRelationship).where(SocialRelationship.profile_id == author.id)) if author else None
    sig = o.signals or {}
    cross = cross_by_post.get(post.id)
    hours = _hours(post.posted_at, now)
    targeted = _recently_targeted(db, author.username if author else None, now)
    eng = sig.get("engagement")
    signals = {
        "price_chat_relevance": float(o.relevance or 0),
        "telegram_relevance": float(sig.get("community_overlap") or 0),
        "x_relevance": float(o.relevance or 0),
        "cross_platform": 1.0 if cross is not None else float(sig.get("community_overlap") or 0) * 0.5,
        "recency": round(math.exp(-(hours or 0) / 24), 3) if hours is not None else 0.3,
        "conversation_activity": float(eng) if eng is not None else 0.3,
        "unique_participants": 0.3,
        "author_relevance": max(float(sig.get("author_reach") or 0), float(sig.get("watchlist_priority") or 0)),
        "community_interest": float(sig.get("community_overlap") or 0),
        "novelty": 1.0 - max((_similar(o.topic + " " + o.contribution, t) for t in own_texts), default=0.0),
        "relationship_history": 0.0 if targeted else (0.7 if rel and rel.interaction_count else 0.5),
        "contribution": 1.0 if o.contribution and len(o.contribution) > 20 else 0.2,
        "confidence": float(o.relevance or 0),
    }
    signals = {k: round(max(0.0, min(1.0, v)), 3) for k, v in signals.items()}
    reasons = list(o.reasons or [])[:4]
    if cross is not None:
        reasons.insert(0, f"also discussed in the Telegram community ({cross.telegram_participants} members)"
                       + (" — early community signal" if cross.early_signal else ""))
    problems = []
    if hours is not None and hours > STALE_X_HOURS:
        problems.append(f"conversation is stale ({int(hours)} h old)")
    if targeted:
        problems.append(f"@{author.username} was already engaged in the last {RECENT_TARGET_HOURS} h")  # type: ignore[union-attr]
    if not o.contribution or len(o.contribution) < 20:
        problems.append("nothing specific to contribute")
    if (o.relevance or 0) < 0.5:
        problems.append("low relevance to Price Chat")
    if signals["novelty"] < 0.4:
        problems.append("a very similar idea was posted recently")
    if not o.why:
        problems.append("context is unclear")
    actions = [X_ACTIONS.get(o.suggested_action.value, "Ignore")]
    s = (o.engagement or {}).get("suggestions") or {}
    actions += [a for a, on in (("Like", s.get("like")), ("Repost", s.get("repost")), ("Follow", s.get("follow_author"))) if on]
    priority = _priority(signals)
    return FeedItem(
        key=f"x:{o.id}", kind="x", id=o.id, source="Telegram + X" if cross is not None else "X", topic=o.topic or "X conversation",
        title=(post.text[:120] + ("…" if len(post.text) > 120 else "")), why=o.why,
        evidence=[f"@{author.username if author else post.author_handle}: “{post.text[:200]}”"]
        + ([f"Telegram: {cross.telegram_messages_total} messages from {cross.telegram_participants} members on '{cross.topic_name}'"] if cross else []),
        recency_hours=hours,
        relationship=(f"{rel.interaction_count} past interactions ({rel.our_reply_count} replies from us)" if rel and rel.interaction_count else "no previous interaction"),
        community=(f"members are discussing '{cross.topic_name}'" if cross else None),
        knowledge=[str(k.get("title") or k.get("snippet") or "")[:120] for k in (o.knowledge or [])[:3]],
        suggested_actions=actions, signals=signals, priority=priority, label=_label(priority), reasons=reasons, problems=problems,
        post={"id": post.id, "external_id": post.external_id, "url": post.url, "text": post.text, "author": author.username if author else post.author_handle,
              "followers": author.followers_count if author else None, "posted_at": post.posted_at.isoformat() if post.posted_at else None},
        pending_approval_id=_pending_for(db, "x", o.id),
    )


def telegram_item(db: Session, o: ContentOpportunity, now: datetime, cross_by_topic: dict[int, CrossSignal], own_texts: list[str]) -> FeedItem:
    sig = o.signals or {}
    ev = o.evidence or {}
    topic = db.get(CommunityTopic, o.origin_id) if o.origin_kind == "topic" and o.origin_id else None
    question = db.get(CommunityQuestion, o.origin_id) if o.origin_kind == "question" and o.origin_id else None
    cross = cross_by_topic.get(topic.id) if topic else None
    last = topic.last_seen_at if topic else (question.last_asked_at if question else o.created_at)
    hours = _hours(last, now)
    people = topic.participant_count if topic else (question.asker_count if question else int(ev.get("participants", 0) or 0))
    msgs = topic.message_count if topic else (question.ask_count if question else int(ev.get("messages", 0) or 0))
    signals = {
        "price_chat_relevance": float(sig.get("brand_relevance", 0.5) or 0.5),
        "telegram_relevance": float(sig.get("community_relevance", 0.5) or 0.5),
        "x_relevance": float(sig.get("x_relevance", 0.0) or 0.0),
        "cross_platform": 1.0 if cross is not None else 0.0,
        "recency": float(sig.get("recency", round(math.exp(-(hours or 0) / 48), 3))),
        "conversation_activity": float(sig.get("volume", 0.0) or 0.0),
        "unique_participants": float(sig.get("participants", min(1.0, people / 15)) or 0.0),
        "author_relevance": 0.5,
        "community_interest": float(sig.get("community_relevance", 0.5) or 0.5),
        "novelty": float(sig.get("novelty", 1.0 - max((_similar(o.title + " " + o.angle, t) for t in own_texts), default=0.0))),
        "relationship_history": 0.5,
        "contribution": float(o.content_potential if o.content_potential is not None else o.confidence),
        "confidence": float(o.confidence),
    }
    signals = {k: round(max(0.0, min(1.0, v)), 3) for k, v in signals.items()}
    problems = []
    if people < 2:
        problems.append("community evidence is weak (fewer than 2 members)")
    if hours is not None and hours > STALE_TELEGRAM_DAYS * 24:
        problems.append(f"no community activity for {int(hours // 24)} days")
    if signals["novelty"] < 0.3:
        problems.append("a very similar idea was covered recently")
    if o.status == OpportunityStatus.REJECTED:
        problems.append("you rejected this before")
    reasons = list(o.reasons or [])[:4] or [f"{msgs} messages from {people} members"]
    if cross is not None:
        reasons.insert(0, f"also active on X ({cross.x_posts} monitored posts)"
                       + (f"; Telegram was {cross.lead_minutes // 60} h earlier" if cross.early_signal and cross.lead_minutes else ""))
    actions = ["X standalone post", "X thread", "Telegram content idea"]
    if "youtube" in (o.platforms or []) or "youtube_shorts" in (o.platforms or []):
        actions.append("YouTube / short video idea")
    priority = _priority(signals)
    return FeedItem(
        key=f"telegram:{o.id}", kind="telegram", id=o.id, source="Telegram + X" if cross is not None else "Telegram",
        topic=topic.name if topic else (question.text[:80] if question else o.title), title=o.title, why=o.why,
        evidence=[f"Telegram: {msgs} {'messages' if topic else 'asks'} from {people} members"
                  + (f", last {int(hours)} h ago" if hours is not None else "")]
        + ([f"X: {cross.x_posts} monitored posts on '{cross.topic_name}'"] if cross else []),
        recency_hours=hours, relationship=None, community=(f"recurring question: “{question.text[:120]}”" if question else
                                                            (f"topic: {topic.name}" if topic else None)),
        knowledge=[], suggested_actions=actions, signals=signals, priority=priority, label=_label(priority), reasons=reasons, problems=problems,
        pending_approval_id=_pending_for(db, "telegram", o.id),
    )


def opportunity_feed(db: Session, include_filtered: bool = True, limit: int = 60) -> list[FeedItem]:
    now = utcnow()
    crosses = list(db.scalars(select(CrossSignal).where(CrossSignal.status == CrossSignalStatus.ACTIVE)))
    cross_by_post = {pid: c for c in crosses for pid in (c.x_post_ids or [])}
    cross_by_topic = {c.telegram_topic_id: c for c in crosses if c.telegram_topic_id}
    own_texts = _recent_own_texts(db, now)
    items: list[FeedItem] = []
    for o in db.scalars(select(SocialOpportunity).where(SocialOpportunity.platform == Platform.X,
                                                        SocialOpportunity.status.in_([SocialOpportunityStatus.NEW, SocialOpportunityStatus.REVIEWING]))):
        if (it := x_item(db, o, now, cross_by_post, own_texts)) is not None:
            items.append(it)
    for o in db.scalars(select(ContentOpportunity).where(ContentOpportunity.status.in_(
            [OpportunityStatus.DETECTED, OpportunityStatus.REVIEWING, OpportunityStatus.APPROVED]))):
        items.append(telegram_item(db, o, now, cross_by_topic, own_texts))
    items.sort(key=lambda i: (i.passed, i.priority), reverse=True)
    if not include_filtered:
        items = [i for i in items if i.passed]
    return items[:limit]


def review_session(db: Session, size: int = 8) -> list[FeedItem]:
    """The 5–10 strongest opportunities that passed the quality filter, at most 2 per topic or account."""
    size = max(SESSION_MIN, min(SESSION_MAX, size))
    picked: list[FeedItem] = []
    seen: dict[str, int] = {}
    for it in opportunity_feed(db, include_filtered=False, limit=200):
        keys = [f"topic:{it.topic.lower()}"] + ([f"author:{it.post['author']}"] if it.post and it.post.get("author") else [])
        if any(seen.get(k, 0) >= 2 for k in keys):
            continue
        for k in keys:
            seen[k] = seen.get(k, 0) + 1
        picked.append(it)
        if len(picked) >= size:
            break
    return picked


# --- measurement ----------------------------------------------------------------------------------------


def _since_day(now: datetime) -> datetime:
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def decision_counts(db: Session, since: datetime | None = None) -> dict[str, int]:
    """Human decisions on proposed X actions: the core trust measurement."""
    stmt = select(FeedbackEvent).where(FeedbackEvent.target_kind == FeedbackTarget.SOCIAL_ACTION)
    if since is not None:
        stmt = stmt.where(FeedbackEvent.created_at >= since)
    c = {"approved_unchanged": 0, "approved_after_edit": 0, "rejected": 0, "skipped": 0}
    for ev in db.scalars(stmt):
        if ev.decision == FeedbackDecision.APPROVE:
            c["approved_unchanged"] += 1
        elif ev.decision == FeedbackDecision.EDIT:
            c["approved_after_edit"] += 1
        elif ev.decision == FeedbackDecision.REJECT:
            c["rejected"] += 1
        elif ev.decision == FeedbackDecision.SKIP:
            c["skipped"] += 1
    expired = select(func.count()).select_from(ApprovalRequest).where(ApprovalRequest.status == ApprovalStatus.EXPIRED)
    if since is not None:
        expired = expired.where(ApprovalRequest.updated_at >= since)
    c["ignored"] = int(db.scalar(expired) or 0)
    return c


def today_stats(db: Session) -> dict[str, int]:
    from app.models.telegram import TelegramMessage

    now = utcnow()
    day = _since_day(now)
    n = lambda stmt: int(db.scalar(stmt) or 0)  # noqa: E731
    approvals = select(func.count()).select_from(ApprovalRequest).where(ApprovalRequest.platform == Platform.X)
    c = decision_counts(db, day)
    return {
        "telegram_messages": n(select(func.count()).select_from(TelegramMessage).where(TelegramMessage.created_at >= day)),
        "x_posts_read": n(select(func.count()).select_from(SocialPost).where(SocialPost.platform == Platform.X, SocialPost.is_own.is_(False), SocialPost.created_at >= day)),
        "cross_signals": n(select(func.count()).select_from(CrossSignal).where(CrossSignal.status == CrossSignalStatus.ACTIVE, CrossSignal.last_updated_at >= day)),
        "early_signals": n(select(func.count()).select_from(CrossSignal).where(CrossSignal.status == CrossSignalStatus.ACTIVE, CrossSignal.early_signal.is_(True))),
        "opportunities_found": n(select(func.count()).select_from(SocialOpportunity).where(SocialOpportunity.created_at >= day))
        + n(select(func.count()).select_from(ContentOpportunity).where(ContentOpportunity.created_at >= day)),
        "drafts_generated": n(approvals.where(ApprovalRequest.created_at >= day, ApprovalRequest.source_kind.in_(["social_opportunity", "opportunity", "draft"]))),
        "approved": c["approved_unchanged"], "edited": c["approved_after_edit"], "rejected": c["rejected"], "skipped": c["skipped"],
        "executed": n(approvals.where(ApprovalRequest.status == ApprovalStatus.EXECUTED, ApprovalRequest.executed_at >= day)),
        "failed": n(approvals.where(ApprovalRequest.status == ApprovalStatus.FAILED, ApprovalRequest.updated_at >= day)),
    }


def _origin(a: ApprovalRequest) -> str:
    return str((a.payload or {}).get("origin") or ("x" if a.source_kind == "social_opportunity" else "telegram" if a.source_kind in ("opportunity", "draft") else "other"))


def analytics(db: Session, days: int = 30) -> dict[str, Any]:
    since = utcnow() - timedelta(days=days)
    approvals = list(db.scalars(select(ApprovalRequest).where(ApprovalRequest.platform == Platform.X, ApprovalRequest.created_at >= since)))
    decided = [a for a in approvals if a.status not in (ApprovalStatus.PENDING,)]
    c = decision_counts(db, since)
    reviewed = c["approved_unchanged"] + c["approved_after_edit"] + c["rejected"]
    by_origin: dict[str, dict[str, int]] = {}
    for a in decided:
        o = by_origin.setdefault(_origin(a), {"reviewed": 0, "accepted": 0, "executed": 0})
        if a.status in (ApprovalStatus.APPROVED, ApprovalStatus.EXECUTED, ApprovalStatus.FAILED, ApprovalStatus.REJECTED):
            o["reviewed"] += 1
        if a.status in (ApprovalStatus.APPROVED, ApprovalStatus.EXECUTED):
            o["accepted"] += 1
        if a.status == ApprovalStatus.EXECUTED:
            o["executed"] += 1
    topics: dict[str, int] = {}
    for a in decided:
        if a.status in (ApprovalStatus.APPROVED, ApprovalStatus.EXECUTED) and (t := (a.payload or {}).get("topic")):
            topics[str(t)] = topics.get(str(t), 0) + 1
    ai = [a for a in decided if (a.payload or {}).get("model")]
    ai_unchanged = sum(1 for a in ai if a.status in (ApprovalStatus.APPROVED, ApprovalStatus.EXECUTED) and "original_text" not in (a.payload or {}))
    ai_edited = sum(1 for a in ai if a.status in (ApprovalStatus.APPROVED, ApprovalStatus.EXECUTED) and "original_text" in (a.payload or {}))
    replied_accounts = list(db.scalars(select(SocialRelationship).where(SocialRelationship.our_reply_count > 0)))
    return {
        "days": days, "reviewed": reviewed, **c,
        "approval_rate": round((c["approved_unchanged"] + c["approved_after_edit"]) / reviewed, 3) if reviewed else None,
        "edit_rate": round(c["approved_after_edit"] / reviewed, 3) if reviewed else None,
        "rejection_rate": round(c["rejected"] / reviewed, 3) if reviewed else None,
        "executed": sum(1 for a in approvals if a.status == ApprovalStatus.EXECUTED),
        "by_origin": by_origin,
        "best_origin": max(by_origin, key=lambda k: (by_origin[k]["accepted"] / max(1, by_origin[k]["reviewed"]), by_origin[k]["reviewed"]))
        if any(v["reviewed"] >= 3 for v in by_origin.values()) else None,
        "top_topics": sorted(topics.items(), key=lambda kv: -kv[1])[:5],
        "ai_drafts": {"reviewed": len(ai), "approved_unchanged": ai_unchanged, "approved_after_edit": ai_edited,
                      "rejected": sum(1 for a in ai if a.status == ApprovalStatus.REJECTED)},
        "relationships": {"accounts_replied_to": len(replied_accounts),
                          "of_which_interacted_since": sum(1 for r in replied_accounts if (r.mention_count or 0) + (r.reply_count or 0) > 0)},
        "note": "Counts only. A later interaction does not show that our reply caused it.",
    }


def daily_summary(db: Session) -> dict[str, Any]:
    """PRICE CHAT SOCIAL PILOT — TODAY. Internal only; never posted anywhere."""
    from app.learning.operator import operator_signals
    from app.models.telegram import TelegramMessage

    now = utcnow()
    day = _since_day(now)
    t = today_stats(db)
    processed = int(db.scalar(select(func.count()).select_from(TelegramMessage).where(TelegramMessage.processed_at >= day)) or 0)
    topics = list(db.scalars(select(CommunityTopic).where(CommunityTopic.count_24h > 0).order_by(CommunityTopic.count_24h.desc()).limit(6)))
    questions = list(db.scalars(select(CommunityQuestion).where(CommunityQuestion.last_asked_at >= day, CommunityQuestion.ask_count >= 2)))
    xo = int(db.scalar(select(func.count()).select_from(SocialOpportunity).where(SocialOpportunity.created_at >= day,
                                                                                  SocialOpportunity.status != SocialOpportunityStatus.DISMISSED)) or 0)
    lines = [
        "PRICE CHAT SOCIAL PILOT — TODAY",
        "",
        "Telegram:",
        f"  {processed} messages processed",
        f"  {len(topics)} active topics" + (f" (top: {', '.join(x.name for x in topics[:3])})" if topics else ""),
        f"  {len(questions)} repeated questions",
        "",
        "X:",
        f"  {t['x_posts_read']} posts read",
        f"  {xo} useful opportunities",
        "",
        "Cross-platform:",
        f"  {t['cross_signals']} shared topics" + (f" ({t['early_signals']} early community signals)" if t["early_signals"] else ""),
        "",
        "Human review:",
        f"  {t['approved'] + t['edited'] + t['rejected']} drafts reviewed",
        f"  {t['approved']} approved unchanged, {t['edited']} edited, {t['rejected']} rejected, {t['skipped']} skipped",
        f"  {t['executed']} executed on X, {t['failed']} failed",
    ]
    learned = [s.text for s in operator_signals(db)]
    if learned:
        lines += ["", "Learning:"] + [f"  - {s}" for s in learned[:5]]
    return {"text": "\n".join(lines), "stats": t, "generated_at": now.isoformat(), "internal_only": True}


# --- creator discovery --------------------------------------------------------------------------------------


def discover_creators(db: Session, days: int = 14, limit: int = 20) -> list[dict[str, Any]]:
    """Accounts that keep appearing in monitored X data, not yet on the watchlist. Suggestions only; never auto-follow."""
    from app.models.social_intel import MonitorRule

    now = utcnow()
    watched = set(db.scalars(select(WatchedAccount.profile_id)))
    ignored = set(db.scalars(select(DiscoveryIgnore.profile_id)))
    self_ids = {p.id for p in db.scalars(select(SocialProfile).where(SocialProfile.is_self.is_(True)))} if hasattr(SocialProfile, "is_self") else set()
    rules = {r.id: r.name for r in db.scalars(select(MonitorRule))}
    crosses = list(db.scalars(select(CrossSignal).where(CrossSignal.status == CrossSignalStatus.ACTIVE)))
    cross_posts = {pid: c.topic_name for c in crosses for pid in (c.x_post_ids or [])}
    opp_posts = set(db.scalars(select(SocialOpportunity.post_id).where(SocialOpportunity.status != SocialOpportunityStatus.DISMISSED)))
    stats: dict[int, dict[str, Any]] = {}
    for p in db.scalars(select(SocialPost).where(SocialPost.platform == Platform.X, SocialPost.is_own.is_(False),
                                                 SocialPost.author_profile_id.is_not(None), SocialPost.created_at >= now - timedelta(days=days))):
        pid = p.author_profile_id
        if pid in watched or pid in ignored or pid in self_ids:
            continue
        s = stats.setdefault(pid, {"posts": 0, "rule_hits": 0, "topics": set(), "opportunities": 0, "community": set()})  # type: ignore[arg-type]
        s["posts"] += 1
        for rid in p.rule_ids or []:
            s["rule_hits"] += 1
            if rid in rules:
                s["topics"].add(rules[rid])
        if p.id in opp_posts:
            s["opportunities"] += 1
        if p.id in cross_posts:
            s["community"].add(cross_posts[p.id])
    out = []
    for pid, s in stats.items():
        appearances = s["rule_hits"] + s["opportunities"] + 2 * len(s["community"])
        if s["posts"] < 2 and not s["community"]:
            continue
        prof = db.get(SocialProfile, pid)
        if prof is None or getattr(prof, "is_self", False):
            continue
        rel = db.scalar(select(SocialRelationship).where(SocialRelationship.profile_id == pid))
        why = [f"{s['posts']} monitored posts in {days} days"]
        if s["topics"]:
            why.append(f"on {', '.join(sorted(s['topics']))}")
        if s["opportunities"]:
            why.append(f"{s['opportunities']} became opportunities")
        if s["community"]:
            why.append(f"overlaps with Telegram discussion ({', '.join(sorted(s['community']))})")
        score = appearances + math.log10((prof.followers_count or 0) + 1) / 2  # reach counts a little; never the main reason
        out.append({"profile_id": pid, "username": prof.username, "name": prof.display_name, "followers": prof.followers_count,
                    "why": "; ".join(why), "topics": sorted(s["topics"]), "appearances": s["posts"],
                    "community_overlap": sorted(s["community"]),
                    "relationship": (f"{rel.interaction_count} interactions" if rel and rel.interaction_count else "none"),
                    "score": round(score, 2)})
    return sorted(out, key=lambda x: -x["score"])[:limit]
