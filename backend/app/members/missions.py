"""Missions: what a member can do now, generated from real Price Chat content and standing goals.

Verification levels (shown to members, enforced here):
  verified        Price Chat can prove it (signed-in visit, linked Telegram messages in authorised chats, an ingested
                  reply from the linked X account, signing in to link an account).
  verified_click  Price Chat can only prove the member opened it from this site (e.g. a YouTube video). Never
                  described as "watched".
  self_reported   The member says they did it. Never pays XP by itself; used only to ask Price Chat to check.
  unverifiable    Price Chat cannot know (likes, reposts, subscriptions, comments, watch time). Never pays XP.

Guardrails: social missions only point at Price Chat's own posts and replies, never at third-party accounts, at
most one active mission per content kind, each expires, and everything goes through the same capped XP ledger.

X missions (reply, repost, like) never pay XP: X's developer policy forbids rewards for actions on X. Reply and
repost run inside Price Chat with the member's own token when they press the button (members/x_actions.py); Like
opens X, because X removed likes from its API. Their cards show "Done", never XP.
"""

import re
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.members import xp as ledger
from app.members.notify import notify
from app.models.content_items import ContentItem
from app.models.enums import Platform
from app.models.members import LinkedIdentity, Member, MemberAction, MemberMission, Mission, ReputationEvent, XPRule
from app.models.social import SocialPost
from app.models.social_intel import SocialProfile

VERIFICATION_LABEL = {
    "verified": "Verified",
    "verified_click": "Verified as an open only",
    "self_reported": "Self-reported (no XP)",
    "unverifiable": "Can't be verified (no XP)",
}
CONTENT_KINDS = {"open_short", "x_reply", "x_conversation", "x_repost", "x_like", "open_link"}
X_ACTION_KINDS = {"x_reply", "x_conversation", "x_repost", "x_like"}  # actions on X: never XP (X developer policy)
X_REPLY_KINDS = {"x_reply", "x_conversation"}
PENDING_TTL = timedelta(hours=24)  # an "I've replied" claim is checked for this long, then rejected
_TAGS = re.compile(r"(?:\s*#\w+)+\s*$")

# Standing missions. XP for event missions comes from the rule that verifies them (one payment, never two).
STANDING: list[dict[str, Any]] = [
    dict(key="daily:visit", kind="daily_visit", cadence="daily", title="Check in on Price Chat", platform="website",
         description="Visit Price Chat while signed in.", verification="verified", priority=90,
         verification_method="Your signed-in visit to this site, once a day."),
    dict(key="daily:telegram", kind="telegram_participation", cadence="daily", title="Be active in Price Chat today",
         platform="telegram", requires_platform="telegram", verification="verified", priority=60, goal=3,
         description="Say something real in the chat: a question, a take, a reply. 3 messages makes it an active day.",
         verification_method="Meaningful messages from your linked Telegram account today in an authorised Price Chat chat "
                             "(15+ characters, your own words). Counted automatically."),
    dict(key="weekly:active-days", kind="weekly_active_days", cadence="weekly", title="3 active community days", platform="telegram",
         requires_platform="telegram", verification="verified", xp=10, goal=3, priority=55,
         description="Take part in the Telegram on 3 different days this week. No need to post more than you want to.",
         verification_method="Days this week (UTC, Monday to Sunday) with a meaningful message from your linked Telegram account."),
    dict(key="weekly:content", kind="weekly_content", cadence="weekly", title="Open 3 Price Chat videos", platform="youtube",
         verification="verified_click", xp=5, goal=3, priority=55,
         description="Catch up on 3 different Price Chat videos this week.",
         verification_method="Opening 3 different videos from this site this week. YouTube doesn't tell us who watched."),
    dict(key="once:link-telegram", kind="link_telegram", cadence="once", title="Connect Telegram", platform="telegram",
         verification="verified", priority=40, description="Link the Telegram account you use in the Price Chat chat.",
         verification_method="Pressing Start on the Price Chat bot from this site proves the account is yours."),
    dict(key="once:link-x", kind="link_x", cadence="once", title="Connect X", platform="x", verification="verified", priority=40,
         description="Link your X account to reply and repost from Price Chat.", verification_method="Signing in with X proves the account is yours."),
    dict(key="once:link-google", kind="link_google", cadence="once", title="Connect Google / YouTube", platform="youtube",
         verification="verified", priority=35, description="Link your Google account.",
         verification_method="Signing in with Google proves the account is yours."),
]
TELEGRAM_GOAL = 3  # meaningful messages in a day = an active day (telegram.active_day)
EVENT_RULE = {"daily_visit": "website.login", "telegram_participation": "telegram.active_day",
              "link_telegram": "identity.telegram_linked", "link_x": "identity.x_linked", "link_google": "identity.google_linked"}
LINK_PLATFORM = {"link_telegram": "telegram", "link_x": "x", "link_google": "google"}


def clean_title(title: str) -> str:
    t = _TAGS.sub("", title or "").strip()
    return (t[:1].upper() + t[1:]) if t else (title or "")


def period_for(cadence: str, when: datetime | None = None) -> str:
    now = when or utcnow()
    if cadence == "daily":
        return ledger.day_of(now)
    if cadence == "weekly":
        y, w, _ = now.isocalendar()
        return f"{y}-W{w:02d}"
    return "once"


def _week_bounds(now: datetime) -> tuple[datetime, datetime]:
    start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=7)


# --- generation -------------------------------------------------------------------------------------------------


def _upsert(db: Session, spec: dict[str, Any]) -> Mission:
    m = db.scalar(select(Mission).where(Mission.key == spec["key"]))
    if m is None:
        m = Mission(**{"xp": 0, "goal": 1, "target": {}, "requires_platform": None, **spec})
        db.add(m)
        db.flush()
    elif m.created_by == "system":
        # keep generated wording current; never touch XP, goal or status (the dashboard owns those)
        for k in ("title", "description", "verification_method"):
            if k in spec:
                setattr(m, k, spec[k])
    return m


def _one_active(db: Session, kind: str, keep: Mission) -> None:
    """At most one active mission per content kind: a newer one replaces the older."""
    for old in db.scalars(select(Mission).where(Mission.kind == kind, Mission.status == "active", Mission.id != keep.id,
                                                Mission.created_by == "system")):
        old.status = "expired"


def generate(db: Session, telegram_url: str | None = None) -> dict[str, int]:
    """Create standing missions and content missions from the newest real content. Idempotent."""
    now = utcnow()
    created = 0
    for spec in STANDING:
        before = db.scalar(select(func.count()).select_from(Mission).where(Mission.key == spec["key"]))
        m = _upsert(db, dict(spec))
        created += 0 if before else 1
        if m.kind == "telegram_participation":
            m.goal = TELEGRAM_GOAL  # older rows were a single message
            if telegram_url:
                m.target = {"url": telegram_url}

    # Newest public Price Chat video (within 14 days) -> "Open the latest Short".
    item = db.scalar(select(ContentItem).where(
        ContentItem.website_visible.is_(True), ContentItem.available.is_(True), ContentItem.privacy_status == "public",
        ContentItem.published_at >= now - timedelta(days=14)).order_by(ContentItem.published_at.desc()).limit(1))
    if item is not None:
        is_short = item.content_type == "short"
        key = f"yt-open:{item.external_id}"
        exists = db.scalar(select(Mission.id).where(Mission.key == key))
        m = _upsert(db, dict(
            key=key, kind="open_short", cadence="once", platform="youtube", xp=3, verification="verified_click", priority=95,
            title=f"Watch the latest {'Short' if is_short else 'video'}",
            description=clean_title(item.title),
            verification_method=("Playing it in your Kiwi Passport for the set number of seconds (Watch & Earn), or opening it from "
                                 "the website. YouTube doesn't tell us who watched, so this never claims you watched it all."),
            target={"url": item.url, "external_id": item.external_id, "thumbnail": item.thumbnail_url, "title": clean_title(item.title),
                    "duration_seconds": item.duration_seconds, "published_at": item.published_at.isoformat() if item.published_at else None},
            starts_at=item.published_at, expires_at=(item.published_at or now) + timedelta(days=7)))
        created += 0 if exists else 1
        _one_active(db, "open_short", m)

    own = list(db.scalars(select(SocialPost).where(SocialPost.platform == Platform.X, SocialPost.is_own.is_(True),
                                                   SocialPost.posted_at >= now - timedelta(days=7)).order_by(SocialPost.posted_at.desc())))
    # Own original posts only: never a reply or a repost (a repost would point members at someone else's post).
    originals = [p for p in own if not any(r.get("type") in ("replied_to", "retweeted") for r in (p.referenced or []))
                 and not (p.text or "").startswith("RT @")]
    replies = [p for p in own if any(r.get("type") == "replied_to" for r in (p.referenced or []))]
    if originals:
        p = originals[0]
        key = f"x-reply:{p.external_id}"
        exists = db.scalar(select(Mission.id).where(Mission.key == key))
        m = _upsert(db, dict(
            key=key, kind="x_reply", cadence="once", platform="x", xp=0, verification="verified", requires_platform="x", priority=92,
            title="Reply to our latest post", description="Add your take under Price Chat's newest post.",
            verification_method="You write it, Price Chat sends it as you when you press Send. No XP: X doesn't allow rewards for posts.",
            target={"url": p.url, "external_id": p.external_id, "text": (p.text or "")[:280],
                    "posted_at": p.posted_at.isoformat() if p.posted_at else None},
            starts_at=p.posted_at, expires_at=(p.posted_at or now) + timedelta(days=3)))
        created += 0 if exists else 1
        _one_active(db, "x_reply", m)
        post_target = {"url": p.url, "external_id": p.external_id, "text": (p.text or "")[:280],
                       "posted_at": p.posted_at.isoformat() if p.posted_at else None, "metrics": p.metrics or {}}
        for kind, title, desc, method, prio, needs in (
            ("x_repost", "Repost our latest post", "Share Price Chat's newest post with your followers.",
             "Price Chat reposts it as you when you press Repost. No XP: X doesn't allow rewards for reposts.", 85, "x"),
            ("x_like", "Like our latest post", "Show some love on X.",
             "Opens the post on X (X no longer lets apps like for you). No XP: X doesn't allow rewards for likes.", 80, None),
        ):
            key = f"{kind.replace('_', '-')}:{p.external_id}"
            exists = db.scalar(select(Mission.id).where(Mission.key == key))
            m = _upsert(db, dict(
                key=key, kind=kind, cadence="once", platform="x", xp=0, verification="verified" if needs else "unverifiable",
                requires_platform=needs, priority=prio, title=title, description=desc, verification_method=method,
                target=post_target, starts_at=p.posted_at, expires_at=(p.posted_at or now) + timedelta(days=3)))
            created += 0 if exists else 1
            _one_active(db, kind, m)
    if replies and replies[0].posted_at and replies[0].posted_at >= now - timedelta(days=3):
        p = replies[0]
        to = _replied_to_handle(db, p)
        key = f"x-conv:{p.external_id}"
        exists = db.scalar(select(Mission.id).where(Mission.key == key))
        m = _upsert(db, dict(
            key=key, kind="x_conversation", cadence="once", platform="x", xp=0, verification="verified", requires_platform="x",
            priority=70, title="Join a conversation Price Chat is in",
            description="Add your own view under Price Chat's reply. Be respectful: never pile on anyone.",
            verification_method="You write it, Price Chat sends it as you when you press Send. No XP: X doesn't allow rewards for posts.",
            target={"url": p.url, "external_id": p.external_id, "text": _strip_leading_mentions(p.text or "")[:280], "replied_to": to,
                    "posted_at": p.posted_at.isoformat() if p.posted_at else None},
            starts_at=p.posted_at, expires_at=(p.posted_at or now) + timedelta(days=2)))
        created += 0 if exists else 1
        _one_active(db, "x_conversation", m)

    from app.members import telegram_missions

    created += telegram_missions.generate(db)
    expired = expire(db)
    db.commit()
    return {"created": created, "expired": expired}


def _strip_leading_mentions(text: str) -> str:
    return re.sub(r"^(?:@\w+\s+)+", "", text).strip()


def _replied_to_handle(db: Session, post: SocialPost) -> str | None:
    if not post.in_reply_to_external_id:
        return None
    prof = db.scalar(select(SocialProfile).where(SocialProfile.platform == Platform.X, SocialProfile.external_id == post.in_reply_to_external_id))
    if prof:
        return prof.username
    m = re.match(r"^@(\w+)", post.text or "")
    return m.group(1) if m else None


def expire(db: Session) -> int:
    """Expire missions past their date; reject "I've done it" claims that were never verified."""
    now = utcnow()
    n = 0
    for m in db.scalars(select(Mission).where(Mission.status == "active", Mission.expires_at.is_not(None), Mission.expires_at < now)):
        m.status = "expired"
        n += 1
    for mm, mission in db.execute(select(MemberMission, Mission).join(Mission, Mission.id == MemberMission.mission_id).where(
            MemberMission.status == "pending")).all():
        if (mm.claimed_at and now - mm.claimed_at > PENDING_TTL) or mission.status == "expired":
            mm.status = "rejected"
            notify(db, mm.member_id, "mission_rejected", f"Not verified: {mission.title}",
                   "We couldn't find a matching reply from your linked account, so no XP was given.", {"mission_id": mission.id})
    return n


# --- member state -----------------------------------------------------------------------------------------------


def _row(db: Session, member: Member, mission: Mission, period: str) -> MemberMission:
    mm = db.scalar(select(MemberMission).where(MemberMission.member_id == member.id, MemberMission.mission_id == mission.id,
                                               MemberMission.period == period))
    if mm is None:
        mm = MemberMission(member_id=member.id, mission_id=mission.id, period=period, status="in_progress", progress=0)
        try:  # a savepoint: a parallel request creating the same row must not undo the caller's uncommitted work
            with db.begin_nested():
                db.add(mm)
        except IntegrityError:
            mm = db.scalar(select(MemberMission).where(MemberMission.member_id == member.id, MemberMission.mission_id == mission.id,
                                                       MemberMission.period == period))
            assert mm is not None
    return mm


def _linked(db: Session, member: Member) -> set[str]:
    return set(db.scalars(select(LinkedIdentity.platform).where(LinkedIdentity.member_id == member.id)))


def _rule(db: Session, event_type: str) -> XPRule | None:
    return db.scalar(select(XPRule).where(XPRule.event_type == event_type))


def _reward(db: Session, member: Member, mission: Mission, period: str, mm: MemberMission, reason: str | None = None,
            occurred_at: datetime | None = None) -> bool:
    """Pay a mission's own XP through the ledger (capped like everything else) and mark it completed."""
    if mm.status == "completed":
        return False
    if mission.kind in X_ACTION_KINDS:  # done, never paid (X developer policy)
        mm.status, mm.progress, mm.xp_awarded, mm.completed_at = "completed", 1, 0, utcnow()
        return True  # never counts toward the streak either: that would be XP for an action on X
    r = ledger.award(db, member, "mission.complete", f"mission:{mission.id}:{period}", reason=reason or f"Mission: {mission.title}",
                     xp=mission.xp, occurred_at=occurred_at, source=mission.platform if mission.platform != "website" else "website",
                     commit=False)
    if not r.awarded:
        return False
    mm.status, mm.xp_awarded, mm.event_id, mm.completed_at = "completed", r.xp, r.event.id if r.event else None, utcnow()
    notify(db, member.id, "mission_verified", f"Mission complete: {mission.title}", f"+{r.xp} XP", {"mission_id": mission.id})
    _streak(db, member, mission, occurred_at)
    return True


def _streak(db: Session, member: Member, mission: Mission, when: datetime | None = None) -> None:
    """A completed task counts toward the daily consistency streak (not the plain check-in visit)."""
    from app.members import streak

    if mission.kind not in streak.EXCLUDED_KINDS and mission.kind not in X_ACTION_KINDS:
        streak.on_task(db, member, when)


def sync_event_missions(db: Session, member: Member) -> None:
    """Record completion of missions verified by other rules (daily visit, Telegram, linking) and pay weekly goals."""
    now = utcnow()
    linked = _linked(db, member)
    today = ledger.day_of(now)
    for mission in db.scalars(select(Mission).where(Mission.status == "active", Mission.kind.in_(
            list(EVENT_RULE) + ["weekly_active_days", "weekly_content"]))):
        period = period_for(mission.cadence, now)
        if mission.kind in LINK_PLATFORM:
            done = LINK_PLATFORM[mission.kind] in linked or bool(db.scalar(select(ReputationEvent.id).where(
                ReputationEvent.member_id == member.id, ReputationEvent.event_type == EVENT_RULE[mission.kind])))
        elif mission.kind in EVENT_RULE:
            done = bool(db.scalar(select(ReputationEvent.id).where(
                ReputationEvent.member_id == member.id, ReputationEvent.event_type == EVENT_RULE[mission.kind], ReputationEvent.day == today)))
        else:
            start, end = _week_bounds(now)
            if mission.kind == "weekly_active_days":
                if mission.requires_platform and mission.requires_platform not in linked:
                    continue
                progress = db.scalar(select(func.count(func.distinct(ReputationEvent.day))).where(
                    ReputationEvent.member_id == member.id, ReputationEvent.event_type == "telegram.first_participation",
                    ReputationEvent.occurred_at >= start, ReputationEvent.occurred_at < end)) or 0
            else:
                progress = db.scalar(select(func.count(func.distinct(MemberAction.target))).where(
                    MemberAction.member_id == member.id, MemberAction.kind == "open", MemberAction.platform == "youtube",
                    MemberAction.created_at >= start, MemberAction.created_at < end)) or 0
            if progress == 0 and not db.scalar(select(MemberMission.id).where(
                    MemberMission.member_id == member.id, MemberMission.mission_id == mission.id, MemberMission.period == period)):
                continue
            mm = _row(db, member, mission, period)
            mm.progress = min(int(progress), mission.goal)
            if progress >= mission.goal:
                _reward(db, member, mission, period, mm)
            continue
        if done:
            mm = _row(db, member, mission, period)
            if mm.status != "completed":
                mm.status, mm.progress, mm.completed_at = "completed", 1, now
                if mission.kind not in ("daily_visit",) and mission.cadence == "daily":
                    _streak(db, member, mission, now)  # e.g. today's Telegram activity
    db.flush()


def record_action(db: Session, member: Member, kind: str, platform: str, target: str) -> bool:
    """A click/open on this site (not a platform action). At most once per target per day."""
    day = ledger.day_of(utcnow())
    if db.scalar(select(MemberAction.id).where(MemberAction.member_id == member.id, MemberAction.kind == kind,
                                               MemberAction.target == target[:128], MemberAction.day == day)):
        return False
    db.add(MemberAction(member_id=member.id, kind=kind[:24], platform=platform[:16], target=target[:128], day=day))
    db.flush()
    return True


def open_mission(db: Session, member: Member, mission: Mission) -> dict[str, Any]:
    """The member opened a mission's link from this site. Completes verified_click missions; starts the others."""
    if mission.status != "active":
        return {"status": "expired", "xp": 0}
    period = period_for(mission.cadence)
    mm = _row(db, member, mission, period)
    mm.opened_at = mm.opened_at or utcnow()
    record_action(db, member, "open", mission.platform, f"mission:{mission.id}" if mission.kind != "open_short"
                  else f"content:youtube:{mission.target.get('external_id', '')}")
    paid = False
    if mission.verification == "verified_click" and mission.kind in CONTENT_KINDS:
        paid = _reward(db, member, mission, period, mm)
    sync_event_missions(db, member)
    db.commit()
    return {"status": mm.status, "xp": mm.xp_awarded if paid else 0}


def claim_mission(db: Session, member: Member, mission: Mission, scheduler: Any = None) -> dict[str, Any]:
    """'I've replied': ask Price Chat to look for the reply. Pays nothing until it is actually verified."""
    if mission.kind not in X_REPLY_KINDS or mission.status != "active":
        raise ValueError("This mission is checked automatically.")
    if "x" not in _linked(db, member):
        raise ValueError("Connect X first, so we can recognise your reply.")
    mm = _row(db, member, mission, period_for(mission.cadence))
    if mm.status in ("completed", "pending"):
        return {"status": mm.status}
    mm.status, mm.claimed_at = "pending", utcnow()
    db.commit()
    if scheduler is not None:
        scheduler.request_run("x.ingest", 5)
        scheduler.request_run("members.verify", 90)
    return {"status": "pending"}


def mark_x_action_done(db: Session, member: Member, kind: str, post_id: str) -> None:
    """A reply / repost the member sent from Price Chat (confirmed by X) completes the matching mission. No XP."""
    kinds = list(X_REPLY_KINDS) if kind == "x_reply" else ["x_repost"]
    for mission in db.scalars(select(Mission).where(Mission.kind.in_(kinds), Mission.status == "active")):
        if str(mission.target.get("external_id")) == str(post_id):
            period = period_for(mission.cadence)
            _reward(db, member, mission, period, _row(db, member, mission, period), reason="Done from Price Chat")


def complete_x_reply_mission(db: Session, member: Member, replied_to: set[str], post: SocialPost, when: datetime) -> str | None:
    """A verified reply from the member's linked X account to the post a mission points at.
    Returns "new" (mission completed now), "done" (already completed: the reply pays nothing more) or None."""
    for mission in db.scalars(select(Mission).where(Mission.kind.in_(X_REPLY_KINDS), Mission.status == "active")):
        if str(mission.target.get("external_id")) in replied_to:
            mm = _row(db, member, mission, period_for(mission.cadence))
            if mm.status == "completed":
                return "done"
            return "new" if _reward(db, member, mission, period_for(mission.cadence), mm, occurred_at=when) else None
    return None


# --- read model -------------------------------------------------------------------------------------------------


def _xp_for(db: Session, mission: Mission, rules: dict[str, XPRule] | None = None) -> int:
    """`rules` (every XP rule by event type, read once) saves a query per mission in lists."""
    def rule_of(event_type: str) -> XPRule | None:
        return rules.get(event_type) if rules is not None else _rule(db, event_type)

    if mission.kind in X_ACTION_KINDS:
        return 0
    if mission.kind == "telegram_participation":  # first message of the day + the active day
        return sum(r.xp for r in (rule_of("telegram.first_participation"), rule_of("telegram.active_day"))
                   if r is not None and r.enabled and r.availability == "available")
    if mission.kind in EVENT_RULE:
        rule = rule_of(EVENT_RULE[mission.kind])
        return rule.xp if rule else 0
    return mission.xp


def _frequency(mission: Mission) -> str:
    return {"daily": "Daily, resets 00:00 UTC", "weekly": "Weekly, resets Monday 00:00 UTC"}.get(mission.cadence, "Once")


def _cta(mission: Mission, status: str, telegram_url: str | None, x_can_act: bool = False, x_write: bool = True) -> dict[str, Any] | None:
    """The ONE obvious action of a mission card."""
    if status in ("completed", "pending"):
        return None
    t = mission.target or {}
    if status == "unavailable" and mission.requires_platform:
        return {"label": "Connect X" if mission.requires_platform == "x" else f"Connect {mission.requires_platform.title()}",
                "action": "link", "platform": mission.requires_platform}
    if mission.kind in LINK_PLATFORM:
        return {"label": "Connect", "action": "link", "platform": LINK_PLATFORM[mission.kind]}
    if mission.kind == "open_short":
        return {"label": "Watch", "action": "open", "url": t.get("url")}
    if mission.kind in ("x_reply", "x_conversation", "x_repost"):
        pid = t.get("external_id")
        if not x_write:  # Connect X is read-only (settings.x_member_write off): the member acts on X itself
            if mission.kind == "x_repost":
                return {"label": "Repost on X", "action": "open", "url": f"https://x.com/intent/retweet?tweet_id={pid}"}
            return {"label": "Reply on X", "action": "open", "url": f"https://x.com/intent/post?in_reply_to={pid}"}
        if not x_can_act:  # linked, but the token is missing / lacks permission / expired
            return {"label": "Reconnect X", "action": "link", "platform": "x"}
        if mission.kind == "x_repost":
            return {"label": "Repost", "action": "x_repost", "post_id": t.get("external_id")}
        return {"label": "Reply", "action": "x_reply", "post_id": t.get("external_id")}
    if mission.kind == "x_like":
        return {"label": "Like on X", "action": "open", "url": f"https://x.com/intent/like?tweet_id={t.get('external_id')}"}
    if mission.kind in ("telegram_participation", "weekly_active_days") and (t.get("url") or telegram_url):
        return {"label": "Open chat", "action": "open", "url": t.get("url") or telegram_url}
    if mission.kind == "open_link":
        return {"label": "Open", "action": "open", "url": t.get("url")}
    if mission.kind == "telegram_join_channel":  # open the channel, then Verify (Telegram confirms membership)
        return {"label": "Join channel", "action": "telegram_join", "url": t.get("url")}
    if mission.kind == "telegram_discussion":  # counted automatically from the discussion group
        return {"label": "Open discussion", "action": "open", "url": t.get("url")}
    return None


def _telegram_today(db: Session, member: Member) -> int:
    """Meaningful messages today from the member's linked Telegram account (what the Telegram mission counts)."""
    from app.members.verified import daily_breakdown, telegram_messages

    ident = db.scalar(select(LinkedIdentity).where(LinkedIdentity.member_id == member.id, LinkedIdentity.platform == "telegram"))
    if ident is None:
        return 0
    now = utcnow()
    day = daily_breakdown(telegram_messages(db, ident, now.replace(hour=0, minute=0, second=0, microsecond=0))).get(ledger.day_of(now))
    return len(day["eligible"]) if day else 0


def for_member(db: Session, member: Member, telegram_url: str | None = None, x_can_act: bool = False, x_write: bool = True) -> list[dict[str, Any]]:
    """The member's missions, in priority order: new content, unfinished, expiring, linking, general."""
    now = utcnow()
    sync_event_missions(db, member)
    db.commit()
    linked = _linked(db, member)
    tg_today = _telegram_today(db, member) if "telegram" in linked else 0
    out = []
    missions = [m for m in db.scalars(select(Mission).where(Mission.status == "active")) if not (m.starts_at and m.starts_at > now)]
    periods = {period_for(c, now) for c in ("daily", "weekly", "once")}
    # the member's rows for these missions' current periods and every XP rule: read once, not once per mission
    mine = {(mm.mission_id, mm.period): mm for mm in db.scalars(select(MemberMission).where(
        MemberMission.member_id == member.id, MemberMission.period.in_(periods),
        MemberMission.mission_id.in_([m.id for m in missions] or [0])))}
    rules = {r.event_type: r for r in db.scalars(select(XPRule))}
    for m in missions:
        period = period_for(m.cadence, now)
        mm = mine.get((m.id, period))
        if m.kind in LINK_PLATFORM and mm is not None and mm.status == "completed" and LINK_PLATFORM[m.kind] in linked:
            continue  # linked: nothing to show
        rule_ok = True
        if m.kind in EVENT_RULE:
            rule = rules.get(EVENT_RULE[m.kind])
            rule_ok = bool(rule and rule.enabled and rule.availability == "available")
        if mm is not None and mm.status == "completed":
            status = "completed"
        elif ((m.requires_platform and m.requires_platform not in linked) or not rule_ok or member.xp_suspended
              or (m.kind in X_ACTION_KINDS and "x" not in linked)):  # no X: the "Connect X" card stands in for X missions
            status = "unavailable"
        elif mm is not None and mm.status == "pending":
            status = "pending"
        elif mm is not None and mm.status == "rejected":
            status = "available"
        elif (mm is not None and (mm.opened_at or mm.progress > 0)) or (m.kind == "telegram_participation" and tg_today > 0):
            status = "in_progress"
        else:
            status = "available"
        expiring = bool(m.expires_at and m.expires_at - now < timedelta(hours=24))
        fresh = m.kind in CONTENT_KINDS and m.starts_at is not None and now - m.starts_at < timedelta(hours=48)
        bucket = (0 if fresh and status != "completed" else 1 if status in ("in_progress", "pending") else 2 if expiring and status == "available"
                  else 3 if m.kind in LINK_PLATFORM else 4) if status not in ("completed", "unavailable") else (6 if status == "completed" else 5)
        out.append({
            "id": m.id, "key": m.key, "kind": m.kind, "cadence": m.cadence, "title": m.title, "description": m.description,
            "platform": m.platform, "xp": _xp_for(db, m, rules) if m.verification in ("verified", "verified_click") else 0,
            "verification": m.verification, "verification_label": VERIFICATION_LABEL[m.verification],
            "verification_method": m.verification_method, "status": status, "frequency": _frequency(m),
            "cta": _cta(m, status, telegram_url, x_can_act, x_write), "target": m.target or {}, "goal": m.goal, "earns_xp": m.kind not in X_ACTION_KINDS,
            "progress": (min(tg_today, m.goal) if m.kind == "telegram_participation" else mm.progress if mm else 0),
            "expires_at": m.expires_at.isoformat() if m.expires_at else None,
            "completed_at": mm.completed_at.isoformat() if mm and mm.completed_at else None,
            "xp_awarded": mm.xp_awarded if mm else 0, "_sort": (bucket, -m.priority, m.id),
        })
    out.sort(key=lambda r: r.pop("_sort"))
    return out


# What "Today" shows first: what earns or unlocks XP, then the rest. One slot is kept for supporting Price Chat on X
# (reply / repost, sent from here) once X is connected; liking (opens X) only fills a free slot.
TODAY_ORDER = ["telegram_discussion", "open_short", "telegram_join_channel", "telegram_participation", "link_telegram", "link_x",
               "link_google", "weekly_active_days", "weekly_content",
               "x_reply", "x_repost", "x_conversation", "x_like", "open_link"]
TODAY_LIMIT = 3


def today(missions: list[dict[str, Any]], limit: int = TODAY_LIMIT) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """At most `limit` missions to show first (the member can act on them now), and the rest. The daily visit is
    automatic, so it is never a card."""
    actionable = [m for m in missions if m["status"] in ("available", "in_progress") and m["cta"] and m["kind"] != "daily_visit"
                  and not (m["kind"] == "x_like" and m["status"] == "in_progress")]  # opened on X: we can't know more
    rank = {k: i for i, k in enumerate(TODAY_ORDER)}
    actionable.sort(key=lambda m: (rank.get(m["kind"], len(rank)), m["status"] != "available"))
    in_app_x = [m for m in actionable if m["kind"] in ("x_reply", "x_repost") and m["cta"]["action"].startswith("x_")]
    earning = [m for m in actionable if m.get("earns_xp", True)]
    first = earning[: limit - 1 if in_app_x else limit] + in_app_x[:1]
    for m in actionable:  # fill any free slot in the usual order
        if len(first) >= limit:
            break
        if m not in first:
            first.append(m)
    first.sort(key=lambda m: actionable.index(m))
    ids = {m["id"] for m in first}
    return first, [m for m in actionable if m["id"] not in ids]


REPOST_CHECK_EVERY = timedelta(hours=2)


async def verify_reposts(db: Session, client: Any) -> dict[str, int]:
    """Complete 'Repost our latest X post' for linked X accounts that X lists as reposters of the mission post.
    Matched by numeric X user id only. Skipped when nobody linked X, the post has no reposts, or it was checked recently."""
    out = {"checked": 0, "completed": 0}
    rule = _rule(db, "x.repost_pricechat")
    if rule is None or not rule.enabled or rule.availability != "available":
        return out
    linked = {uid: mid for uid, mid in db.execute(select(LinkedIdentity.platform_user_id, LinkedIdentity.member_id).where(
        LinkedIdentity.platform == "x", LinkedIdentity.status == "active"))}
    now = utcnow()
    for m in db.scalars(select(Mission).where(Mission.kind == "x_repost", Mission.status == "active")):
        last = m.target.get("checked_at")
        if not linked or (last and now - datetime.fromisoformat(last) < REPOST_CHECK_EVERY):
            continue
        post = db.scalar(select(SocialPost).where(SocialPost.platform == Platform.X, SocialPost.external_id == m.target.get("external_id")))
        reposts = int(((post.metrics if post else {}) or {}).get("reposts") or 0)
        m.target = {**m.target, "checked_at": now.isoformat()}
        if reposts == 0:
            continue
        body = await client.reposted_by(str(m.target["external_id"]), reposts)
        out["checked"] += 1
        for user in body.get("data") or []:
            mid = linked.get(str(user.get("id")))
            member = db.get(Member, mid) if mid else None
            if member is None or member.status != "active":
                continue
            mm = _row(db, member, m, period_for(m.cadence))
            if mm.status != "completed" and _reward(db, member, m, period_for(m.cadence), mm, reason="Reposted Price Chat on X"):
                out["completed"] += 1
    db.commit()
    return out
