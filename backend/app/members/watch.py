"""Watch & Earn: Price Chat videos played inside the Kiwi Passport.

What Price Chat can and cannot know (shown to members as such):
  - Pressing Watch here is recorded server-side (a "watch" action, like any open on this site).
  - The embedded YouTube player reports how many seconds actually played in this browser. The browser sends that
    number; the server only accepts it once at least that much time has really passed since Watch was pressed here,
    so the count cannot be sped up. It still cannot prove a person was looking at the screen, so the reward is for
    "played N seconds in Price Chat", never for "watched the video".
  - YouTube does not tell a channel who watched, liked, commented or subscribed. Member Google sign-in is "openid"
    only (no YouTube scope), so comments and subscriptions are neither performed nor rewarded here.

Rewards reuse what already exists, so a video never pays twice: the latest video is the "Watch the latest Short"
mission (its own XP), every other video is `website.content_view` (once per video, daily cap), the same ledger
entry the homepage uses when a video is opened.
"""

from datetime import timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.members import missions
from app.members import xp as ledger
from app.models.content_items import ContentItem
from app.models.members import Member, MemberAction, MemberMission, Mission, ReputationEvent

WATCH_KIND = "watch"
PAID_KIND = "watch_paid"  # one per video that paid today: the position in today's combo
VIDEOS = 4  # one featured + three recent
COMBO_EXTRA = 5  # Watch combo: the day's 1st video pays its normal XP, the 2nd double, the 3rd and later double + 5
START_WINDOW = timedelta(hours=6)  # how long pressing Watch stays valid for completing the video
CLOCK_SLACK = 2  # seconds of network / player start-up tolerated between "pressed Watch" and the threshold


class WatchError(ValueError):
    pass


def threshold(db: Session) -> int:
    from app.members.reputation import settings

    return max(1, int(settings(db).watch_seconds or 30))


def _needed(db: Session, item: ContentItem) -> int:
    """Play time that completes a video: the setting, or the whole video when it is shorter (a 20-second Short)."""
    t = threshold(db)
    if item.duration_seconds:
        t = min(t, max(1, item.duration_seconds - 1))  # players stop a moment before the reported end
    return t


def combo_bonus(position: int, base: int) -> int:
    """Extra XP on top of a video's own XP for the day's n-th paid video: 0, then +base (double), then +base+5."""
    if base <= 0 or position <= 1:
        return 0
    return base if position == 2 else base + COMBO_EXTRA


def paid_today(db: Session, member: Member) -> int:
    from sqlalchemy import func

    return int(db.scalar(select(func.count()).select_from(MemberAction).where(
        MemberAction.member_id == member.id, MemberAction.kind == PAID_KIND, MemberAction.day == ledger.day_of(utcnow()))) or 0)


def _combo(db: Session, member: Member, item: ContentItem, base: int, title: str) -> tuple[int, int]:
    """Record this paid video and pay its combo bonus. (bonus paid, position today)."""
    from app.members.catalog import ensure_catalog

    ensure_catalog(db)  # the "watch.combo" rule exists before the first bonus (catalogs are filled in lazily)
    missions.record_action(db, member, PAID_KIND, item.platform.value, _target(item))
    n = paid_today(db, member)
    extra = combo_bonus(n, base)
    if not extra:
        return 0, n
    r = ledger.award(db, member, "watch.combo", f"combo:{_target(item)}", xp=extra, commit=False,
                     reason=f"Watch combo #{n}: {'double' if n == 2 else 'double +5'} for “{title}”")
    return (r.xp if r.awarded else 0), n


def _public(db: Session, limit: int) -> list[ContentItem]:
    return list(db.scalars(select(ContentItem).where(
        ContentItem.website_visible.is_(True), ContentItem.available.is_(True), ContentItem.privacy_status == "public",
    ).order_by(ContentItem.published_at.desc(), ContentItem.id.desc()).limit(limit)))


def _item(db: Session, external_id: str) -> ContentItem:
    item = db.scalar(select(ContentItem).where(
        ContentItem.external_id == external_id, ContentItem.website_visible.is_(True), ContentItem.available.is_(True),
        ContentItem.privacy_status == "public"))
    if item is None:
        raise WatchError("This video isn't available.")
    return item


def _target(item: ContentItem) -> str:
    return f"content:{item.platform.value}:{item.external_id}"


def _mission_for(db: Session, item: ContentItem) -> Mission | None:
    m = db.scalar(select(Mission).where(Mission.kind == "open_short", Mission.status == "active"))
    return m if m is not None and m.target.get("external_id") == item.external_id else None


def _state(db: Session, member: Member, item: ContentItem) -> tuple[int, str]:
    """(XP this video can pay, "available" | "completed" | "no_xp")."""
    mission = _mission_for(db, item)
    if mission is not None:
        mm = db.scalar(select(MemberMission).where(MemberMission.member_id == member.id, MemberMission.mission_id == mission.id,
                                                   MemberMission.period == missions.period_for(mission.cadence)))
        if mm is not None and mm.status == "completed":
            return mm.xp_awarded, "completed"
        return (0, "no_xp") if member.xp_suspended else (mission.xp, "available")
    paid = db.scalar(select(ReputationEvent.xp).where(ReputationEvent.member_id == member.id,
                                                      ReputationEvent.event_type == "website.content_view",
                                                      ReputationEvent.source_ref == _target(item)))
    if paid is not None:
        return int(paid), "completed"
    rule = missions._rule(db, "website.content_view")
    if member.xp_suspended or rule is None or not rule.enabled or rule.availability != "available" or rule.xp <= 0:
        return 0, "no_xp"
    return rule.xp, "available"


def videos(db: Session, member: Member) -> dict[str, Any]:
    """The newest public Price Chat videos (featured first) with what each can still pay this member."""
    out = []
    nxt = paid_today(db, member) + 1  # the next video's place in today's combo
    for item in _public(db, VIDEOS):
        xp, status = _state(db, member, item)
        if status == "available":
            xp += combo_bonus(nxt, xp)  # what it pays now, combo included
        out.append({
            "external_id": item.external_id, "platform": item.platform.value, "type": item.content_type,
            "title": missions.clean_title(item.title), "url": item.url, "thumbnail": item.thumbnail_url,
            "duration_seconds": item.duration_seconds, "published_at": item.published_at.isoformat() if item.published_at else None,
            "xp": xp, "status": status, "needed_seconds": _needed(db, item), "latest": _mission_for(db, item) is not None,
        })
    return {"threshold_seconds": threshold(db), "videos": out,
            "combo": {"today": nxt - 1, "next": "normal" if nxt == 1 else "double" if nxt == 2 else "double +5"}}


def _started(db: Session, member: Member, item: ContentItem) -> MemberAction | None:
    return db.scalar(select(MemberAction).where(MemberAction.member_id == member.id, MemberAction.kind == WATCH_KIND,
                                                MemberAction.target == _target(item), MemberAction.created_at >= utcnow() - START_WINDOW)
                     .order_by(MemberAction.created_at.desc()).limit(1))  # not "today": a video playing across 00:00 UTC counts


def start(db: Session, member: Member, external_id: str) -> dict[str, Any]:
    """The member pressed Watch in the Passport. Recorded (it counts as an open for weekly goals); pays nothing yet."""
    item = _item(db, external_id)
    missions.record_action(db, member, WATCH_KIND, item.platform.value, _target(item))
    missions.record_action(db, member, "open", item.platform.value, _target(item))
    mission = _mission_for(db, item)
    if mission is not None:
        mm = missions._row(db, member, mission, missions.period_for(mission.cadence))
        mm.opened_at = mm.opened_at or utcnow()
    missions.sync_event_missions(db, member)
    db.commit()
    return {"ok": True, "needed_seconds": _needed(db, item)}


def complete(db: Session, member: Member, external_id: str, played_seconds: float) -> dict[str, Any]:
    """The player says `played_seconds` played here. Paid once, if Watch was pressed here at least that long ago."""
    item = _item(db, external_id)
    needed = _needed(db, item)
    started = _started(db, member, item)
    if started is None:
        return {"ok": False, "code": "not_started", "message": "Press Watch to start this video here first.", "xp": 0}
    at = started.created_at if started.created_at.tzinfo else started.created_at.replace(tzinfo=timezone.utc)
    elapsed = (utcnow() - at).total_seconds()
    if played_seconds < needed or elapsed + CLOCK_SLACK < needed:
        return {"ok": False, "code": "too_soon", "message": f"Keep watching: {needed} seconds count.", "xp": 0,
                "needed_seconds": needed}
    xp, status = _state(db, member, item)
    if status == "completed":
        return {"ok": True, "code": "already", "message": "Already counted.", "xp": 0}
    secs = f"{needed} seconds" if needed != 1 else "1 second"
    title = missions.clean_title(item.title or "a Price Chat video")[:100]
    mission = _mission_for(db, item)
    if mission is not None:
        period = missions.period_for(mission.cadence)
        mm = missions._row(db, member, mission, period)
        paid = missions._reward(db, member, mission, period, mm, reason=f"Played {secs} of “{title}” in Price Chat")
        extra, n = _combo(db, member, item, mm.xp_awarded, title) if paid else (0, 0)
        missions.sync_event_missions(db, member)
        db.commit()
        return {"ok": True, "code": "paid" if paid else "refused", "xp": mm.xp_awarded + extra if paid else 0, "combo": n, "bonus": extra,
                "message": ("Counted." + (f" Combo #{n}: +{extra} bonus XP!" if extra else "")) if paid else "Played here. No XP this time: today's XP limit is reached."}
    r = ledger.award(db, member, "website.content_view", _target(item), reason=f"Played {secs} of “{title}” in Price Chat",
                     commit=False)
    extra, n = _combo(db, member, item, r.xp, title) if r.awarded else (0, 0)
    missions.sync_event_missions(db, member)
    db.commit()
    if r.awarded:
        return {"ok": True, "code": "paid", "xp": r.xp + extra, "combo": n, "bonus": extra,
                "message": "Counted." + (f" Combo #{n}: +{extra} bonus XP!" if extra else "")}
    why = {"daily_cap": "today's video XP is used up", "overall_cap": "today's XP limit is reached"}.get(r.refused or "", "")
    return {"ok": True, "code": "refused", "xp": 0, "message": f"Played here. No XP this time{': ' + why if why else ''}."}
