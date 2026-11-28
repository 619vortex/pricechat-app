"""What a signed-in member sees around their Kiwi Passport: the Price Chat feed, their activity, where their XP
came from, their Telegram participation and the next legitimate ways to rank up. Real data only."""

from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.members import xp as ledger
from app.members.missions import _replied_to_handle, _strip_leading_mentions, clean_title
from app.members.verified import daily_breakdown, telegram_messages
from app.models.content_items import ContentItem
from app.models.enums import Platform
from app.models.members import LinkedIdentity, Member, MemberAction, MemberMission, ReputationEvent, XPRule
from app.models.social import SocialPost

SOURCE_LABEL = {"telegram": "Telegram", "x": "X", "youtube": "YouTube", "google": "YouTube", "website": "Website",
                "moderator": "Price Chat team", "admin": "Price Chat team", "mission": "Missions", "referral": "Invites"}


def _x_type(p: SocialPost) -> str:
    kinds = {r.get("type") for r in (p.referenced or [])}
    return "reply" if "replied_to" in kinds else "quote" if "quoted" in kinds else "repost" if "retweeted" in kinds else "post"


def _x_actions(p: SocialPost) -> dict[str, str]:
    """Official X links. Price Chat can't like/reply/repost for a member, so these open X itself."""
    i = p.external_id
    return {"open": p.url or f"https://x.com/i/status/{i}", "like": f"https://x.com/intent/like?tweet_id={i}",
            "reply": f"https://x.com/intent/post?in_reply_to={i}", "repost": f"https://x.com/intent/retweet?tweet_id={i}"}


def feed(db: Session, links: dict[str, str | None]) -> dict[str, Any]:
    now = utcnow()
    own = list(db.scalars(select(SocialPost).where(SocialPost.platform == Platform.X, SocialPost.is_own.is_(True),
                                                   SocialPost.posted_at >= now - timedelta(days=14))
                          .order_by(SocialPost.posted_at.desc()).limit(40)))

    def x_item(p: SocialPost) -> dict[str, Any]:
        m = p.metrics or {}
        return {"id": p.external_id, "type": _x_type(p), "text": p.text, "posted_at": p.posted_at.isoformat() if p.posted_at else None,
                "metrics": {k: m.get(k) for k in ("likes", "replies", "reposts", "impressions") if m.get(k) is not None},
                "actions": _x_actions(p)}

    posts = [x_item(p) for p in own if _x_type(p) != "reply"][:5]
    conversations = []
    for p in [p for p in own if _x_type(p) == "reply"][:5]:
        item = x_item(p)
        item["text"] = _strip_leading_mentions(p.text or "")
        item["replied_to"] = _replied_to_handle(db, p)
        conversations.append(item)
    videos = []
    for i in db.scalars(select(ContentItem).where(ContentItem.website_visible.is_(True), ContentItem.available.is_(True),
                                                  ContentItem.privacy_status == "public")
                        .order_by(ContentItem.published_at.desc()).limit(3)):
        videos.append({"id": i.external_id, "type": i.content_type, "title": clean_title(i.title), "thumbnail": i.thumbnail_url,
                       "duration_seconds": i.duration_seconds, "views": (i.metrics or {}).get("views"),
                       "published_at": i.published_at.isoformat() if i.published_at else None,
                       "actions": {"watch": i.url, "comment": f"https://www.youtube.com/watch?v={i.external_id}", "share": i.url,
                                   "subscribe": f"{links['youtube']}?sub_confirmation=1" if links.get("youtube") else None}})
    return {"x_posts": posts, "x_conversations": conversations, "videos": videos,
            "telegram": {"url": links.get("telegram")} if links.get("telegram") else None}


def activity(db: Session, member: Member) -> dict[str, Any]:
    """Rolling counts from the ledger, site actions and missions. Nothing estimated."""
    now = utcnow()
    out = {}
    for name, days in (("today", 0), ("7d", 7), ("30d", 30)):
        since = now.replace(hour=0, minute=0, second=0, microsecond=0) if days == 0 else now - timedelta(days=days)
        ev = list(db.execute(select(ReputationEvent.source, ReputationEvent.event_type, ReputationEvent.xp, ReputationEvent.day).where(
            ReputationEvent.member_id == member.id, ReputationEvent.occurred_at >= since, ReputationEvent.reverses_id.is_(None))))
        acts = list(db.execute(select(MemberAction.platform, MemberAction.day).where(
            MemberAction.member_id == member.id, MemberAction.created_at >= since)))
        out[name] = {
            # participation, not account linking: Telegram messages that earned XP, verified X replies / mentions
            "telegram": sum(1 for _s, t, _x, _d in ev if t.startswith("telegram.")),
            "x": sum(1 for s, t, _x, _d in ev if t.startswith("x.") or (s == "x" and t == "mission.complete")),
            "youtube": sum(1 for p, _ in acts if p == "youtube"),
            "website": sum(1 for s, *_ in ev if s == "website"),
            "xp": sum(x for _, _, x, _ in ev if x > 0),
            "missions": int(db.scalar(select(func.count()).select_from(MemberMission).where(
                MemberMission.member_id == member.id, MemberMission.status == "completed", MemberMission.completed_at >= since)) or 0),
            "active_days": len({d for *_, d in ev} | {d for _, d in acts}),
        }
    return out


def breakdown(db: Session, member: Member) -> dict[str, Any]:
    rows = db.execute(select(ReputationEvent.source, func.sum(ReputationEvent.xp)).where(
        ReputationEvent.member_id == member.id, ReputationEvent.category == "reputation").group_by(ReputationEvent.source)).all()
    totals: dict[str, int] = {}
    for src, xp in rows:
        label = SOURCE_LABEL.get(src, src.title())
        totals[label] = totals.get(label, 0) + int(xp or 0)
    parts = sorted(({"source": k, "xp": v} for k, v in totals.items() if v), key=lambda r: -r["xp"])
    return {"total": member.xp_total, "sources": parts}


def telegram_activity(db: Session, member: Member) -> dict[str, Any] | None:
    ident = db.scalar(select(LinkedIdentity).where(LinkedIdentity.member_id == member.id, LinkedIdentity.platform == "telegram"))
    if ident is None:
        return None
    now = utcnow()
    today = ledger.day_of(now)
    days = daily_breakdown(telegram_messages(db, ident, now - timedelta(days=7)))
    rules = {r.event_type: r for r in db.scalars(select(XPRule).where(XPRule.event_type.in_(
        ["telegram.first_participation", "telegram.discussion", "telegram.helpful_reply"])))}
    from app.members.reputation import settings as rep_settings

    cap = rep_settings(db).telegram_daily_cap
    xp_today = int(db.scalar(select(func.coalesce(func.sum(ReputationEvent.xp), 0)).where(
        ReputationEvent.member_id == member.id, ReputationEvent.day == today, ReputationEvent.xp > 0,
        ReputationEvent.event_type.like("telegram.%"))) or 0)
    first_done = bool(db.scalar(select(ReputationEvent.id).where(ReputationEvent.member_id == member.id, ReputationEvent.day == today,
                                                                 ReputationEvent.event_type == "telegram.first_participation")))
    disc = rules.get("telegram.discussion")
    disc_today = int(db.scalar(select(func.coalesce(func.sum(ReputationEvent.xp), 0)).where(
        ReputationEvent.member_id == member.id, ReputationEvent.day == today, ReputationEvent.event_type == "telegram.discussion")) or 0)
    first = rules.get("telegram.first_participation")
    if first and first.enabled and not first_done:
        nxt = f"+{first.xp} XP for your first meaningful message today"
    elif disc and disc.enabled and disc.daily_cap and disc_today < disc.daily_cap:
        nxt = f"+{disc.xp} XP for each further real message ({disc.daily_cap - disc_today} XP left today)"
    else:
        nxt = "You've earned today's Telegram XP. It resets at 00:00 UTC."
    last = db.scalar(select(ReputationEvent.occurred_at).where(ReputationEvent.member_id == member.id, ReputationEvent.source == "telegram",
                                                               ReputationEvent.event_type.like("telegram.%"))
                     .order_by(ReputationEvent.occurred_at.desc()).limit(1))
    week_days = int(db.scalar(select(func.count(func.distinct(ReputationEvent.day))).where(
        ReputationEvent.member_id == member.id, ReputationEvent.event_type == "telegram.first_participation",
        ReputationEvent.occurred_at >= now - timedelta(days=7))) or 0)
    t = days.get(today)
    week_xp = int(db.scalar(select(func.coalesce(func.sum(ReputationEvent.xp), 0)).where(
        ReputationEvent.member_id == member.id, ReputationEvent.xp > 0, ReputationEvent.event_type.like("telegram.%"),
        ReputationEvent.occurred_at >= now - timedelta(days=7))) or 0)
    # all time, since the account was linked: every message seen in the authorised chats, and the XP chatting has earned
    from app.models.telegram import TelegramMessage, TelegramSource

    msgs_total = int(db.scalar(select(func.count(TelegramMessage.id)).join(TelegramSource, TelegramSource.id == TelegramMessage.source_id).where(
        TelegramSource.enabled.is_(True), TelegramMessage.author_id == int(ident.platform_user_id), TelegramMessage.author_is_bot.is_(False),
        TelegramMessage.sent_at >= ident.verified_at)) or 0)
    xp_total = int(db.scalar(select(func.coalesce(func.sum(ReputationEvent.xp), 0)).where(
        ReputationEvent.member_id == member.id, ReputationEvent.xp > 0, ReputationEvent.event_type.like("telegram.%"))) or 0)
    from app.members import reputation

    rec = reputation.combot_record_for(db, member)
    rs = reputation.settings(db)
    return {"username": ident.username, "telegram_user_id": ident.platform_user_id,
            "messages_today": t["all"] if t else 0, "eligible_today": len(t["eligible"]) if t else 0,
            "messages_7d": sum(d["all"] for d in days.values()), "xp_7d": week_xp,
            "messages_total": msgs_total, "xp_total": xp_total,
            "historical": ({"combot_xp": rec.combot_xp, "combot_messages": rec.combot_messages, "combot_rep": rec.combot_rep,
                            "imported_at": rec.imported_at.isoformat(), "legacy_points": reputation.legacy_points(rs, rec),
                            "counts": rs.combot_enabled} if rec else None),
            "xp_today": xp_today, "daily_cap": cap, "active_days_7d": week_days,
            "last_verified_at": last.isoformat() if last else None, "next": nxt,
            "note": "Counted from your linked account (by its numeric Telegram id) in authorised Price Chat chats. "
                    "'gm', 'lol', emoji, forwards and repeats don't count."}


def next_steps(missions: list[dict[str, Any]], limit: int = 3) -> list[dict[str, Any]]:
    """The best legitimate ways to earn XP right now: open missions that pay, highest XP first."""
    open_ = [m for m in missions if m["status"] in ("available", "in_progress") and m["xp"] > 0]
    open_.sort(key=lambda m: -m["xp"])
    return [{"mission_id": m["id"], "title": m["title"], "xp": m["xp"], "platform": m["platform"]} for m in open_[:limit]]


def public_profile(db: Session, username: str) -> dict[str, Any] | None:
    """Only what a member chose to show: username, rank, badges, join date. Never accounts, XP history or ids."""
    from app.members.service import badges_of, rank_out

    m = db.scalar(select(Member).where(Member.username_key == username.lower()))
    if m is None or not m.public_profile or m.status != "active":
        return None
    out = {"username": m.username, "joined_at": m.created_at.isoformat(), "rank": rank_out(ledger.rank_for(db, m.rank_score)),
           "badges": [{"key": b["key"], "name": b["name"], "description": b["description"]} for b in badges_of(db, m)],
           "roles": _roles(db, m.id)}
    from app.passport import service as passport

    if passport.visible(m):  # Kiwi Passport 2.0 (PASSPORT2_MODE): what the member chose to show, in the new layout
        passport.ensure_catalog(db)
        out.update(passport.public_v2(db, m))
    return out


def _roles(db: Session, member_id: int) -> list[str]:
    """Worker System roles shown publicly (WORKER / MANAGER): next to the rank, never instead of it."""
    from app.workers.service import public_roles

    return public_roles(db, member_id)

