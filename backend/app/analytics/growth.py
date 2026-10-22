"""PRICE CHAT → Analytics → Growth: is the social activity producing growth?

Everything here is computed from data Price Chat actually collected. A metric X doesn't provide (or
that we haven't collected yet) is returned as None with a note, never estimated. Activity and growth
are shown side by side; nothing here claims that one caused the other.
"""

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config.settings import Settings
from app.connectors.state import get_connector_state
from app.db.base import utcnow
from app.models.community import CommunityQuestion, CommunityTopic, ContentOpportunity
from app.models.enums import ApprovalStatus, Platform, SocialAction, SocialOpportunityStatus, SuggestedAction, TopicStatus
from app.models.social import SocialPost
from app.models.social_intel import (
    ApprovalRequest,
    GrowthSnapshot,
    SocialActionLog,
    SocialOpportunity,
    SocialPostMetric,
    SocialProfile,
    SocialRelationship,
)
from app.models.telegram import TelegramMessage, TelegramSource
from app.social.budget import PlatformBudget

PERIODS: dict[str, timedelta | None] = {"24h": timedelta(hours=24), "7d": timedelta(days=7), "30d": timedelta(days=30), "all": None}
ENGAGEMENT_KEYS = ("likes", "replies", "reposts", "quotes", "bookmarks")
CHECKPOINTS = (1, 6, 24)

NOT_PROVIDED = [
    "Follower history before Price Chat started taking snapshots (X does not provide historical follower counts).",
    "Who followed or unfollowed, and why (not available through the X API).",
    "Account-level impressions and profile visits: X only reports them per post, and private metrics only for your own posts from the last 30 days.",
    "Likes, reposts and follows you made outside Price Chat (only actions Price Chat executed are counted).",
    "Telegram member history before snapshots started; member counts for chats the bot or account cannot query.",
    "Exact X charges: costs are Price Chat's estimate from X's published prices (the X developer console is the authority).",
]


def _aware(dt: datetime | None) -> datetime | None:
    return None if dt is None else (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc))


@dataclass(slots=True)
class Window:
    period: str
    start: datetime | None
    end: datetime
    prev_start: datetime | None

    def has(self, dt: datetime | None) -> bool:
        dt = _aware(dt)
        return dt is not None and (self.start is None or dt >= self.start) and dt <= self.end

    def in_prev(self, dt: datetime | None) -> bool:
        dt = _aware(dt)
        return dt is not None and self.prev_start is not None and self.start is not None and self.prev_start <= dt < self.start


def window(period: str, now: datetime | None = None) -> Window:
    if period not in PERIODS:
        raise ValueError(f"period must be one of {', '.join(PERIODS)}")
    now = now or utcnow()
    span = PERIODS[period]
    if span is None:
        return Window(period, None, now, None)
    return Window(period, now - span, now, now - 2 * span)


def change(current: float | int | None, previous: float | int | None) -> dict[str, Any]:
    """Absolute and relative change. Missing data on either side → no change shown."""
    if current is None or previous is None:
        return {"delta": None, "pct": None}
    delta = current - previous
    pct = None if previous == 0 else round(100.0 * delta / previous, 1)
    return {"delta": round(delta, 4) if isinstance(delta, float) else delta, "pct": pct}


def kpi(key: str, label: str, value: Any, previous: Any = None, *, unit: str = "", available: bool = True, note: str = "") -> dict[str, Any]:
    return {"key": key, "label": label, "value": value if available else None, "previous": previous if available else None,
            "change": change(value, previous) if available else {"delta": None, "pct": None}, "unit": unit,
            "available": available, "note": note}


# --- followers ---------------------------------------------------------------------------------------------


def follower_points(db: Session, account_id: str | None) -> list[tuple[datetime, int]]:
    if not account_id:
        return []
    rows = db.execute(select(GrowthSnapshot.taken_at, GrowthSnapshot.followers).where(
        GrowthSnapshot.kind == "x_account", GrowthSnapshot.subject == account_id, GrowthSnapshot.followers.is_not(None),
    ).order_by(GrowthSnapshot.taken_at))
    return [(_aware(t), int(f)) for t, f in rows]  # type: ignore[misc]


def value_at(points: list[tuple[datetime, int]], at: datetime | None) -> int | None:
    """Last known value at or before `at` (None if nothing was collected by then)."""
    if at is None:
        return points[0][1] if points else None
    best = None
    for t, v in points:
        if t <= at:
            best = v
        else:
            break
    return best


def follower_change(points: list[tuple[datetime, int]], w: Window) -> tuple[int | None, int | None, str]:
    """(change in window, change in previous window, note). Needs a snapshot at or before each boundary,
    otherwise the change is measured from the first snapshot inside the window (and says so)."""
    if not points:
        return None, None, "No follower snapshots yet"
    current = value_at(points, w.end)
    start_val = value_at(points, w.start)
    note = ""
    if start_val is None:  # snapshots started inside this window
        inside = [v for t, v in points if w.has(t)]
        if len(inside) < 2:
            return None, None, "Needs at least two follower snapshots"
        start_val = inside[0]
        note = f"since snapshots began ({points[0][0]:%d %b %H:%M} UTC)"
    prev = None
    if w.prev_start is not None:
        a, b = value_at(points, w.prev_start), value_at(points, w.start)
        if a is not None and b is not None:
            prev = b - a
    return (current - start_val) if current is not None else None, prev, note


# --- own posts & actions -----------------------------------------------------------------------------------


def _engagements(m: dict[str, Any]) -> int | None:
    vals = [m.get(k) for k in ENGAGEMENT_KEYS]
    if all(v is None for v in vals):
        return None
    return sum(int(v or 0) for v in vals)


def _own_posts(db: Session) -> list[SocialPost]:
    return list(db.scalars(select(SocialPost).where(SocialPost.platform == Platform.X, SocialPost.is_own.is_(True))))


def post_totals(posts: list[SocialPost], w: Window, prev: bool = False) -> dict[str, Any]:
    sel = [p for p in posts if (w.in_prev(p.posted_at) if prev else w.has(p.posted_at))]
    imps = [int(p.metrics["impressions"]) for p in sel if (p.metrics or {}).get("impressions") is not None]
    eng = [e for p in sel if (e := _engagements(p.metrics or {})) is not None]
    eng_with_imps = [(_engagements(p.metrics) or 0, int(p.metrics["impressions"])) for p in sel
                     if (p.metrics or {}).get("impressions")]
    visits = [int(p.metrics["profile_visits"]) for p in sel if (p.metrics or {}).get("profile_visits") is not None]
    return {
        "posts": sum(1 for p in sel if not p.in_reply_to_external_id),
        "replies": sum(1 for p in sel if p.in_reply_to_external_id),
        "impressions": sum(imps) if imps else None,
        "engagements": sum(eng) if eng else None,
        "engagement_rate": round(sum(e for e, _ in eng_with_imps) / sum(i for _, i in eng_with_imps), 4) if eng_with_imps else None,
        "profile_visits": sum(visits) if visits else None,
        "count": len(sel),
    }


def _actions(db: Session, w: Window, prev: bool = False, outcomes: tuple[str, ...] = ("executed",)) -> Counter:
    lo, hi = (w.prev_start, w.start) if prev else (w.start, w.end)
    if prev and lo is None:
        return Counter()
    stmt = select(SocialActionLog.action, func.count()).where(SocialActionLog.platform == Platform.X, SocialActionLog.outcome.in_(outcomes))
    if lo is not None:
        stmt = stmt.where(SocialActionLog.created_at >= lo)
    stmt = stmt.where(SocialActionLog.created_at < hi) if prev else stmt.where(SocialActionLog.created_at <= hi)
    return Counter({a.value: int(n) for a, n in db.execute(stmt.group_by(SocialActionLog.action))})


# --- cost --------------------------------------------------------------------------------------------------


def _usage_days(db: Session, settings: Settings) -> dict[str, dict[str, float]]:
    return PlatformBudget(db, settings)._usage()


def cost_between(usage: dict[str, dict[str, float]], first_day: str | None, last_day: str) -> dict[str, float]:
    tot = {"cost": 0.0, "requests": 0, "writes": 0}
    for day, u in usage.items():
        if (first_day is None or day >= first_day) and day <= last_day:
            tot["cost"] += float(u.get("cost", 0) or 0)
            tot["requests"] += int(u.get("requests", 0) or 0)
            tot["writes"] += int(u.get("writes", 0) or 0)
    tot["cost"] = round(tot["cost"], 4)
    return tot


def _days_back(now: datetime, n: int) -> str:
    return (now - timedelta(days=n - 1)).strftime("%Y-%m-%d")


def cost_section(db: Session, settings: Settings, now: datetime) -> dict[str, Any]:
    usage = _usage_days(db, settings)
    today = now.strftime("%Y-%m-%d")
    d7, d30 = cost_between(usage, _days_back(now, 7), today), cost_between(usage, _days_back(now, 30), today)
    since = now - timedelta(days=30)
    useful = int(db.scalar(select(func.count()).select_from(SocialOpportunity).where(
        SocialOpportunity.platform == Platform.X, SocialOpportunity.created_at >= since,
        SocialOpportunity.status != SocialOpportunityStatus.DISMISSED)) or 0)
    approved = int(db.scalar(select(func.count()).select_from(ApprovalRequest).where(
        ApprovalRequest.platform == Platform.X, ApprovalRequest.decided_at >= since,
        ApprovalRequest.status.in_((ApprovalStatus.APPROVED, ApprovalStatus.EXECUTED)))) or 0)
    executed = int(db.scalar(select(func.count()).select_from(SocialActionLog).where(
        SocialActionLog.platform == Platform.X, SocialActionLog.outcome == "executed", SocialActionLog.created_at >= since)) or 0)
    budget = PlatformBudget(db, settings)
    return {
        "today": cost_between(usage, today, today), "d7": d7, "d30": d30,
        "per_useful_opportunity_30d": round(d30["cost"] / useful, 4) if useful else None,
        "useful_opportunities_30d": useful,
        "per_approved_action_30d": round(d30["cost"] / approved, 4) if approved else None,
        "approved_actions_30d": approved,
        "per_executed_action_30d": round(d30["cost"] / executed, 4) if executed else None,
        "executed_actions_30d": executed,
        "budget": budget.limits(), "month": budget.month(),
        "note": "Estimated from X's published per-request prices; the X developer console shows the actual charges. "
                "Usage history is kept for 62 days.",
    }


# --- chart ---------------------------------------------------------------------------------------------------


def buckets(w: Window, first: datetime | None) -> tuple[list[datetime], timedelta]:
    if w.period == "24h":
        step = timedelta(hours=1)
        start = w.end.replace(minute=0, second=0, microsecond=0) - timedelta(hours=23)
    else:
        step = timedelta(days=1)
        days = 7 if w.period == "7d" else 30 if w.period == "30d" else None
        end_day = w.end.replace(hour=0, minute=0, second=0, microsecond=0)
        if days is None:
            first_day = (first or w.end).replace(hour=0, minute=0, second=0, microsecond=0)
            days = max(1, min(365, (end_day - first_day).days + 1))
        start = end_day - timedelta(days=days - 1)
    out, t = [], start
    while t <= w.end:
        out.append(t)
        t += step
    return out, step


def chart(db: Session, w: Window, posts: list[SocialPost], points: list[tuple[datetime, int]]) -> dict[str, Any]:
    action_rows = list(db.execute(select(SocialActionLog.action, SocialActionLog.created_at).where(
        SocialActionLog.platform == Platform.X, SocialActionLog.outcome == "executed")))
    firsts = [t for t, _ in points] + [_aware(p.posted_at) for p in posts if p.posted_at] + [_aware(t) for _, t in action_rows]
    first = min(firsts) if firsts else None
    bks, step = buckets(w, first)
    idx = lambda dt: next((i for i, b in enumerate(bks) if b <= dt < b + step), None)  # noqa: E731

    series: dict[str, list[Any]] = {k: [0] * len(bks) for k in ("posts", "replies", "likes", "reposts", "follows")}
    imps: list[int | None] = [None] * len(bks)
    eng: list[int | None] = [None] * len(bks)
    for p in posts:
        i = idx(_aware(p.posted_at)) if p.posted_at else None
        if i is None:
            continue
        series["replies" if p.in_reply_to_external_id else "posts"][i] += 1
        m = p.metrics or {}
        if m.get("impressions") is not None:
            imps[i] = (imps[i] or 0) + int(m["impressions"])
        if (e := _engagements(m)) is not None:
            eng[i] = (eng[i] or 0) + e
    for action, at in action_rows:
        i = idx(_aware(at))  # type: ignore[arg-type]
        key = {SocialAction.LIKE: "likes", SocialAction.REPOST: "reposts", SocialAction.FOLLOW: "follows"}.get(action)
        if i is not None and key:
            series[key][i] += 1
    # Followers: the last snapshot inside each bucket; buckets before the first snapshot stay empty (never back-filled).
    followers: list[int | None] = []
    for b in bks:
        inside = [v for t, v in points if b <= t < b + step]
        followers.append(inside[-1] if inside else None)
    return {
        "buckets": [b.isoformat() for b in bks], "step": "hour" if step == timedelta(hours=1) else "day",
        "followers": followers, "follower_points": [{"t": t.isoformat(), "v": v} for t, v in points if w.has(t)],
        "impressions": imps, "engagements": eng, **series,
        "note": "Impressions and engagements are counted on the posts published in each interval (latest known numbers).",
    }


# --- timeline, content, AI, relationships, Telegram --------------------------------------------------------------

ACTION_TEXT = {SocialAction.LIKE: "Liked", SocialAction.REPLY: "Replied to", SocialAction.POST: "Published post",
               SocialAction.REPOST: "Reposted", SocialAction.FOLLOW: "Followed"}


def timeline(db: Session, w: Window, limit: int = 60) -> list[dict[str, Any]]:
    stmt = select(SocialActionLog, ApprovalRequest).outerjoin(ApprovalRequest, ApprovalRequest.id == SocialActionLog.approval_id).where(
        SocialActionLog.platform == Platform.X, SocialActionLog.outcome == "executed")
    if w.start is not None:
        stmt = stmt.where(SocialActionLog.created_at >= w.start)
    out = []
    for log, appr in db.execute(stmt.order_by(SocialActionLog.created_at.desc()).limit(limit)):
        payload = (appr.payload if appr else None) or {}
        res = (appr.execution_result if appr else None) or {}
        acc = log.target_account
        verb = ACTION_TEXT.get(log.action, log.action.value)
        title = f"{verb} @{acc}" if acc and log.action != SocialAction.POST else verb
        if log.action in (SocialAction.LIKE, SocialAction.REPOST) and acc:
            title = f"{verb} @{acc}'s post"
        out.append({"at": _aware(log.created_at).isoformat(), "action": log.action.value, "title": title,  # type: ignore[union-attr]
                    "account": acc, "topic": payload.get("topic"), "text": (payload.get("text") or "")[:200] or None,
                    "url": res.get("url"), "approval_id": log.approval_id})
    return out


def _approval_by_post(db: Session) -> dict[str, ApprovalRequest]:
    out = {}
    for a in db.scalars(select(ApprovalRequest).where(ApprovalRequest.status == ApprovalStatus.EXECUTED,
                                                      ApprovalRequest.action.in_((SocialAction.POST, SocialAction.REPLY)))):
        x_id = (a.execution_result or {}).get("x_id")
        if x_id:
            out[str(x_id)] = a
    return out


def content_performance(db: Session, w: Window, posts: list[SocialPost], limit: int = 20) -> list[dict[str, Any]]:
    sel = [p for p in posts if w.has(p.posted_at)]
    by_post = _approval_by_post(db)
    cps: dict[int, dict[str, dict[str, Any]]] = defaultdict(dict)
    if sel:
        for m in db.scalars(select(SocialPostMetric).where(SocialPostMetric.post_id.in_([p.id for p in sel]),
                                                           SocialPostMetric.source.like("checkpoint_%"))):
            cps[m.post_id][m.source.removeprefix("checkpoint_")] = {k: getattr(m, k) for k in ("impressions", "likes", "replies", "reposts", "bookmarks")}
    rows = []
    for p in sel:
        m = p.metrics or {}
        appr = by_post.get(p.external_id or "")
        payload = (appr.payload if appr else None) or {}
        e, i = _engagements(m), m.get("impressions")
        rows.append({
            "id": p.id, "text": p.text, "url": p.url, "posted_at": _aware(p.posted_at).isoformat() if p.posted_at else None,  # type: ignore[union-attr]
            "kind": "reply" if p.in_reply_to_external_id else "post", "topic": payload.get("topic"),
            "via_price_chat": appr is not None, "ai_drafted": bool(payload.get("model")) if appr else None,
            "human_edited": ("original_text" in payload) if appr else None,
            "impressions": i, "likes": m.get("likes"), "replies": m.get("replies"), "reposts": m.get("reposts"),
            "bookmarks": m.get("bookmarks"), "engagements": e,
            "engagement_rate": round(e / i, 4) if e is not None and i else None,
            "checkpoints": {f"{h}h": cps[p.id].get(f"{h}h") for h in CHECKPOINTS},
        })
    rows.sort(key=lambda r: (r["impressions"] if r["impressions"] is not None else -1, r["engagements"] or 0), reverse=True)
    return rows[:limit]


def ai_performance(db: Session, w: Window) -> dict[str, Any]:
    def in_w(col):  # type: ignore[no-untyped-def]
        return (col >= w.start) if w.start is not None else (col.is_not(None))

    opps = list(db.scalars(select(SocialOpportunity).where(SocialOpportunity.platform == Platform.X, in_w(SocialOpportunity.created_at))))
    tg_opps = int(db.scalar(select(func.count()).select_from(ContentOpportunity).where(in_w(ContentOpportunity.created_at))) or 0)
    approvals = list(db.scalars(select(ApprovalRequest).where(ApprovalRequest.platform == Platform.X, in_w(ApprovalRequest.created_at))))
    decided = [a for a in approvals if a.decided_at is not None]
    # Human-approved = approved, executed, or approved but then failed/blocked at execution.
    accepted = [a for a in decided if a.status in (ApprovalStatus.APPROVED, ApprovalStatus.EXECUTED, ApprovalStatus.FAILED)]
    rejected = [a for a in decided if a.status == ApprovalStatus.REJECTED]
    ai = [a for a in decided if (a.payload or {}).get("model") and a.action in (SocialAction.POST, SocialAction.REPLY)]
    ai_ok = [a for a in ai if a.status != ApprovalStatus.REJECTED]
    ai_edited = [a for a in ai_ok if "original_text" in (a.payload or {})]

    def rec_vs(action: SocialAction, recommended: int) -> dict[str, Any]:
        approved = sum(1 for a in accepted if a.action == action)
        return {"recommended": recommended, "proposed": sum(1 for a in approvals if a.action == action), "approved": approved}

    outcomes = _actions(db, w, outcomes=("executed",))
    failed = _actions(db, w, outcomes=("failed", "blocked"))
    return {
        "opportunities_x": len(opps), "opportunities_telegram": tg_opps,
        "opportunities_dismissed": sum(1 for o in opps if o.status == SocialOpportunityStatus.DISMISSED),
        "proposals": len(approvals), "approved": len(accepted), "rejected": len(rejected),
        "pending": sum(1 for a in approvals if a.status == ApprovalStatus.PENDING),
        "expired": sum(1 for a in approvals if a.status == ApprovalStatus.EXPIRED),
        "ai_drafts_reviewed": len(ai), "ai_acceptance_rate": round(len(ai_ok) / len(ai), 3) if ai else None,
        "ai_unchanged_rate": round((len(ai_ok) - len(ai_edited)) / len(ai), 3) if ai else None,
        "human_edit_rate": round(len(ai_edited) / len(ai_ok), 3) if ai_ok else None,
        "likes": rec_vs(SocialAction.LIKE, sum(1 for o in opps if ((o.engagement or {}).get("suggestions") or {}).get("like"))),
        "replies": rec_vs(SocialAction.REPLY, sum(1 for o in opps if o.suggested_action == SuggestedAction.REPLY)),
        "successful_actions": sum(outcomes.values()), "failed_or_blocked": sum(failed.values()),
        "trust_trend": trust_trend(db),
    }


def trust_trend(db: Session, weeks: int = 8, now: datetime | None = None) -> list[dict[str, Any]]:
    """Per week: share of AI-written drafts you approved without changing them."""
    now = now or utcnow()
    start = now - timedelta(weeks=weeks)
    rows = list(db.scalars(select(ApprovalRequest).where(ApprovalRequest.decided_at >= start,
                                                         ApprovalRequest.action.in_((SocialAction.POST, SocialAction.REPLY)))))
    out = []
    for i in range(weeks):
        lo, hi = start + timedelta(weeks=i), start + timedelta(weeks=i + 1)
        wk = [a for a in rows if (a.payload or {}).get("model") and lo <= _aware(a.decided_at) < hi]  # type: ignore[operator]
        ok = [a for a in wk if a.status != ApprovalStatus.REJECTED and "original_text" not in (a.payload or {})]
        out.append({"week_start": lo.date().isoformat(), "reviewed": len(wk), "approved_unchanged_rate": round(len(ok) / len(wk), 3) if wk else None})
    return out


def relationships(db: Session, limit: int = 12) -> list[dict[str, Any]]:
    """Accounts with real two-way or Price Chat-initiated interaction, most recent first (not a leaderboard)."""
    rows = db.execute(select(SocialRelationship, SocialProfile).join(SocialProfile, SocialProfile.id == SocialRelationship.profile_id).where(
        SocialProfile.platform == Platform.X, SocialProfile.is_self.is_(False),
        (SocialRelationship.our_reply_count > 0) | (SocialRelationship.our_like_count > 0) | (SocialRelationship.our_repost_count > 0)
        | (SocialRelationship.we_follow_them.is_(True)),
    ).order_by(SocialRelationship.last_interaction_at.desc()).limit(limit))
    out = []
    for rel, prof in rows:
        hist = db.scalars(select(SocialActionLog).where(SocialActionLog.target_account == prof.username.lower(),
                                                        SocialActionLog.outcome == "executed").order_by(SocialActionLog.created_at.desc()).limit(5))
        out.append({
            "account": prof.username, "name": prof.display_name, "followers": prof.followers_count,
            "likes_from_us": rel.our_like_count, "replies_from_us": rel.our_reply_count, "reposts_from_us": rel.our_repost_count,
            "we_follow": rel.we_follow_them, "their_mentions": rel.mention_count, "their_replies": rel.reply_count,
            "last_interaction_at": _aware(rel.last_interaction_at).isoformat() if rel.last_interaction_at else None,  # type: ignore[union-attr]
            "first_interaction_at": _aware(rel.first_interaction_at).isoformat() if rel.first_interaction_at else None,  # type: ignore[union-attr]
            "topics": list((rel.topics or {}).keys())[:5],
            "history": [{"at": _aware(h.created_at).isoformat(), "action": h.action.value} for h in hist],  # type: ignore[union-attr]
        })
    return out


def telegram(db: Session, w: Window) -> dict[str, Any]:
    def cnt(model, col, prev=False):  # type: ignore[no-untyped-def]
        stmt = select(func.count()).select_from(model)
        if prev:
            if w.prev_start is None:
                return None
            stmt = stmt.where(col >= w.prev_start, col < w.start)
        elif w.start is not None:
            stmt = stmt.where(col >= w.start)
        return int(db.scalar(stmt) or 0)

    active = int(db.scalar(select(func.count()).select_from(CommunityTopic).where(
        CommunityTopic.status == TopicStatus.ACTIVE, *( [CommunityTopic.last_seen_at >= w.start] if w.start else []))) or 0)
    snaps = list(db.scalars(select(GrowthSnapshot).where(GrowthSnapshot.kind == "telegram_chat").order_by(GrowthSnapshot.taken_at)))
    chats: dict[str, dict[str, Any]] = {}
    for s in snaps:
        c = chats.setdefault(s.subject, {"chat": s.label, "points": []})
        c["chat"] = s.label or c["chat"]
        if s.members is not None and w.has(s.taken_at):
            c["points"].append({"t": _aware(s.taken_at).isoformat(), "v": s.members})  # type: ignore[union-attr]
        c["latest_members"] = s.members if s.members is not None else c.get("latest_members")
    return {
        "connected_sources": int(db.scalar(select(func.count()).select_from(TelegramSource).where(TelegramSource.enabled.is_(True))) or 0),
        "messages": cnt(TelegramMessage, TelegramMessage.sent_at), "messages_prev": cnt(TelegramMessage, TelegramMessage.sent_at, True),
        "active_discussions": active,
        "topics_detected": cnt(CommunityTopic, CommunityTopic.first_seen_at), "topics_prev": cnt(CommunityTopic, CommunityTopic.first_seen_at, True),
        "questions_detected": cnt(CommunityQuestion, CommunityQuestion.first_asked_at),
        "questions_prev": cnt(CommunityQuestion, CommunityQuestion.first_asked_at, True),
        "content_opportunities": cnt(ContentOpportunity, ContentOpportunity.created_at),
        "member_history": list(chats.values()),
        "member_note": "Member counts are recorded every 6 hours from now on." if not any(c["points"] for c in chats.values())
        else "Member counts recorded every 6 hours; earlier history is not available.",
    }


# --- the whole dashboard -----------------------------------------------------------------------------------------


def dashboard(db: Session, settings: Settings, period: str = "7d", now: datetime | None = None) -> dict[str, Any]:
    now = now or utcnow()
    w = window(period, now)
    state = get_connector_state(db, Platform.X)
    points = follower_points(db, state.account_external_id)
    posts = _own_posts(db)
    cur, prev = post_totals(posts, w), post_totals(posts, w, prev=True)
    acts, acts_prev = _actions(db, w), _actions(db, w, prev=True)
    has_prev = w.prev_start is not None
    fchange, fprev, fnote = follower_change(points, w)
    current_followers = value_at(points, w.end)

    usage = _usage_days(db, settings)
    first_day = w.start.strftime("%Y-%m-%d") if w.start else None
    spend = cost_between(usage, first_day, now.strftime("%Y-%m-%d"))
    spend_prev = None
    if has_prev and period != "24h":
        lo, hi = w.prev_start.strftime("%Y-%m-%d"), (w.start - timedelta(days=1)).strftime("%Y-%m-%d")  # type: ignore[union-attr]
        if usage and min(usage) <= lo:  # only if usage was already being tracked for the whole previous period
            spend_prev = cost_between(usage, lo, hi)
    # Post-based comparisons need collected history before the period started (we only hold what was read).
    earliest_post = min((_aware(x.posted_at) for x in posts if x.posted_at), default=None)
    posts_covered = has_prev and earliest_post is not None and earliest_post <= w.prev_start  # type: ignore[operator]
    p = (lambda v: v if posts_covered else None)  # noqa: E731
    pa = (lambda v: v if has_prev else None)  # noqa: E731  (Price Chat's own actions are fully recorded)
    no_imps = cur["impressions"] is None
    kpis = [
        kpi("followers", "X followers", current_followers, available=current_followers is not None,
            note="Snapshots start " + (f"{points[0][0]:%d %b %Y %H:%M} UTC" if points else "with the next X read")),
        kpi("follower_change", "Follower change", fchange, fprev, available=fchange is not None, note=fnote),
        kpi("impressions", "Impressions", cur["impressions"], p(prev["impressions"]), available=not no_imps,
            note="On your posts published in this period" if not no_imps else "Needs impressions from X (user-context keys)"),
        kpi("engagements", "Engagements", cur["engagements"], p(prev["engagements"]), available=cur["engagements"] is not None,
            note="Likes, replies, reposts, quotes and bookmarks on your posts"),
        kpi("engagement_rate", "Engagement rate", cur["engagement_rate"], p(prev["engagement_rate"]), unit="%",
            available=cur["engagement_rate"] is not None, note="Engagements ÷ impressions"),
        kpi("posts", "Posts published", cur["posts"], p(prev["posts"])),
        kpi("replies", "Replies sent", cur["replies"], p(prev["replies"])),
        kpi("likes", "Likes performed", acts.get("like", 0), pa(acts_prev.get("like", 0)), note="Executed by Price Chat"),
        kpi("reposts", "Reposts performed", acts.get("repost", 0), pa(acts_prev.get("repost", 0)), note="Executed by Price Chat"),
        kpi("follows", "Accounts followed", acts.get("follow", 0), pa(acts_prev.get("follow", 0)), note="Executed by Price Chat"),
        kpi("profile_visits", "Profile visits", cur["profile_visits"], p(prev["profile_visits"]), available=cur["profile_visits"] is not None,
            note="From X private metrics on your own posts (last 30 days only)" if cur["profile_visits"] is not None
            else "Not provided by X for this period (needs private metrics on recent own posts)"),
        kpi("api_spend", "X API spend", spend["cost"], spend_prev["cost"] if spend_prev else None, unit="$",
            note=f"{spend['requests']} requests · estimate, UTC days"),
    ]
    return {
        "period": period, "generated_at": now.isoformat(), "account": state.account_username,
        "kpis": kpis, "chart": chart(db, w, posts, points), "timeline": timeline(db, w),
        "content": content_performance(db, w, posts), "ai": ai_performance(db, w), "relationships": relationships(db),
        "telegram": telegram(db, w), "cost": cost_section(db, settings, now),
        "snapshots": {"x_follower_snapshots": len(points), "first_snapshot_at": points[0][0].isoformat() if points else None,
                      "schedule": "X: from every X response that includes your account, plus at most one lookup per 6 h; "
                                  "Telegram: every 6 h (free)."},
        "not_provided": NOT_PROVIDED,
        "disclaimer": "Activity and growth are shown side by side. Growth after an action does not show the action caused it.",
    }
