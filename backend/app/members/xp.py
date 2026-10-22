"""The Kiwi XP rule engine and ledger.

Every XP change goes through `award` (or `reverse` / `adjust`, which are awards too). Nothing else writes XP.
An award must name a rule, a dedupe reference and a reason; it is refused, with the reason recorded in the
result, when the rule is off or unavailable, the member is banned / suspended / too new, the same activity was
already paid, a one-time reward was already claimed by that external account, the per-member limit, cooldown,
daily cap or overall daily cap is reached. Refusals are counted so bursts can be flagged for a person to review.
"""

import hashlib
import threading
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.members import Member, MemberAuditLog, ModerationFlag, Rank, ReputationEvent, XPClaim, XPRule

# Sources whose XP is earned by activity (not given by staff): they share the overall daily cap.
AUTOMATIC_SOURCES = {"website", "telegram", "x", "google", "youtube", "mission"}
STAFF_SOURCES = {"moderator", "admin"}
DAILY_XP_CAP = 60  # overall automatic XP per member per UTC day
# X's developer policy forbids rewards for actions on X (posts, replies, likes, reposts): refused here whatever the
# rule settings say, and so are missions that are actions on X (see missions.X_ACTION_KINDS).
X_ENGAGEMENT_EVENTS = frozenset({"x.mention_pricechat", "x.reply_pricechat", "x.like_pricechat", "x.repost_pricechat"})
FLAG_AFTER_REFUSALS = 30  # refused attempts in one day before a "rapid activity" flag is raised
_CLAIM_SALT = "price-chat-xp-claim-v1"
LIVE_SECONDS = 600  # an award for activity this recent is the member acting now (not a job re-checking the past)

_refusals: dict[tuple[str, int], int] = defaultdict(int)
_refusals_lock = threading.Lock()


@dataclass(slots=True)
class AwardResult:
    awarded: bool
    xp: int = 0
    refused: str | None = None  # why nothing (or less) was paid
    event: ReputationEvent | None = None
    capped: bool = False  # paid, but reduced by a cap


def reset_for_tests() -> None:
    with _refusals_lock:
        _refusals.clear()


def day_of(ts: datetime) -> str:
    """The UTC calendar day an activity happened on (daily caps and streaks use it)."""
    return (ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).strftime("%Y-%m-%d")


def claim_hash(event_type: str, key: str) -> str:
    return hashlib.sha256(f"{_CLAIM_SALT}|{event_type}|{key}".encode()).hexdigest()


def get_rule(db: Session, event_type: str) -> XPRule:
    rule = db.scalar(select(XPRule).where(XPRule.event_type == event_type))
    if rule is None:
        raise ValueError(f"unknown XP event type: {event_type}")
    return rule


def _paid_today(db: Session, member_id: int, day: str, event_type: str | None = None, sources: set[str] | None = None) -> int:
    stmt = select(func.coalesce(func.sum(ReputationEvent.xp), 0)).where(
        ReputationEvent.member_id == member_id, ReputationEvent.day == day, ReputationEvent.xp > 0,
        ReputationEvent.reversed_at.is_(None), ReputationEvent.reverses_id.is_(None),
    )
    if event_type:
        stmt = stmt.where(ReputationEvent.event_type == event_type)
    if sources:
        stmt = stmt.where(ReputationEvent.source.in_(sources))
    return int(db.scalar(stmt) or 0)


def telegram_paid_today(db: Session, member_id: int, day: str) -> int:
    """All automatic Telegram XP (telegram.*) paid to the member on `day`, for the overall Telegram daily cap."""
    return int(db.scalar(select(func.coalesce(func.sum(ReputationEvent.xp), 0)).where(
        ReputationEvent.member_id == member_id, ReputationEvent.day == day, ReputationEvent.xp > 0,
        ReputationEvent.event_type.like("telegram.%"), ReputationEvent.event_type != "telegram.message",  # it has its own 50/day cap
        ReputationEvent.source == "telegram")) or 0)


def _refuse(db: Session, member: Member, rule: XPRule, why: str, day: str, live: bool = True) -> AwardResult:
    """Refuse an award. Only live attempts count towards the rapid-activity flag: a verification job re-checking
    activity from earlier (already paid, or over a past day's cap) is not the member doing anything now."""
    if live and rule.verification_source in AUTOMATIC_SOURCES and why in {"daily_cap", "overall_cap", "cooldown", "duplicate"}:
        with _refusals_lock:
            if (day, member.id) not in _refusals:  # a new key: forget the days that can no longer be flagged
                oldest = day_of(utcnow() - timedelta(days=1))
                for k in [k for k in _refusals if k[0] < oldest]:
                    del _refusals[k]
            _refusals[(day, member.id)] += 1
            hits = _refusals[(day, member.id)]
        if hits == FLAG_AFTER_REFUSALS:
            raise_flag(db, member, "rapid_activity", f"{hits} XP attempts refused on {day} (caps / cooldowns / repeats)",
                       {"day": day, "last_rule": rule.event_type})
    return AwardResult(False, 0, why)


def award(
    db: Session,
    member: Member,
    event_type: str,
    source_ref: str,
    *,
    reason: str | None = None,
    occurred_at: datetime | None = None,
    xp: int | None = None,
    actor: str = "system",
    claim_key: str | None = None,
    commit: bool = True,
    source: str | None = None,
) -> AwardResult:
    """Pay XP for one verified activity, within the rule's limits. `xp` may only be given for staff rules and missions
    (a mission sets its own reward). `source` labels mission XP with the mission's platform for the member's breakdown."""
    if event_type in X_ENGAGEMENT_EVENTS:
        return AwardResult(False, 0, "x_policy")
    rule = get_rule(db, event_type)
    when = occurred_at or utcnow()
    day = day_of(when)
    live = occurred_at is None or abs((utcnow() - when).total_seconds()) < LIVE_SECONDS
    if rule.availability != "available" or not rule.enabled:
        return AwardResult(False, 0, "rule_off")
    staff = rule.verification_source in STAFF_SOURCES
    if xp is not None and not staff and rule.verification_source not in ("mission", "streak", "wallet", "watch", "earn", "arena"):  # wallet: one level; watch: combo; arena: counted kills
        raise ValueError("only staff rules and missions accept a custom XP amount")
    if member.status != "active":
        return AwardResult(False, 0, "banned")
    if member.xp_suspended and not staff:
        return AwardResult(False, 0, "suspended")
    if rule.min_member_age_hours and utcnow() - member.created_at < timedelta(hours=rule.min_member_age_hours):
        return AwardResult(False, 0, "too_new")
    source_ref = source_ref[:128]  # as stored: the duplicate check must compare what the unique constraint compares
    if db.scalar(select(ReputationEvent.id).where(ReputationEvent.member_id == member.id, ReputationEvent.event_type == event_type,
                                                 ReputationEvent.source_ref == source_ref)):
        return _refuse(db, member, rule, "duplicate", day, live)
    if claim_key and db.scalar(select(XPClaim.id).where(XPClaim.claim_hash == claim_hash(event_type, claim_key))):
        return AwardResult(False, 0, "already_claimed")
    if rule.max_per_member is not None:
        done = db.scalar(select(func.count()).select_from(ReputationEvent).where(
            ReputationEvent.member_id == member.id, ReputationEvent.event_type == event_type, ReputationEvent.reversed_at.is_(None),
            ReputationEvent.reverses_id.is_(None))) or 0
        if done >= rule.max_per_member:
            return AwardResult(False, 0, "max_reached")
    if rule.cooldown_seconds:
        last = db.scalar(select(ReputationEvent.occurred_at).where(
            ReputationEvent.member_id == member.id, ReputationEvent.event_type == event_type, ReputationEvent.reverses_id.is_(None),
        ).order_by(ReputationEvent.occurred_at.desc()).limit(1))
        if last is not None and abs((when - last).total_seconds()) < rule.cooldown_seconds:
            return _refuse(db, member, rule, "cooldown", day, live)

    amount = xp if xp is not None else rule.xp
    if amount == 0:
        return AwardResult(False, 0, "zero")
    capped = False
    if amount > 0 and rule.daily_cap is not None:
        room = rule.daily_cap - _paid_today(db, member.id, day, event_type=event_type)
        if room <= 0:
            return _refuse(db, member, rule, "daily_cap", day, live)
        if amount > room:
            amount, capped = room, True
    if amount > 0 and event_type.startswith("telegram.") and rule.verification_source == "telegram":
        from app.members.reputation import settings as rep_settings

        room = rep_settings(db).telegram_daily_cap - telegram_paid_today(db, member.id, day)
        if room <= 0:
            return _refuse(db, member, rule, "telegram_cap", day, live)
        if amount > room:
            amount, capped = room, True
    if amount > 0 and rule.verification_source in AUTOMATIC_SOURCES:
        room = DAILY_XP_CAP - _paid_today(db, member.id, day, sources=AUTOMATIC_SOURCES)
        if room <= 0:
            return _refuse(db, member, rule, "overall_cap", day, live)
        if amount > room:
            amount, capped = room, True

    event = ReputationEvent(
        member_id=member.id, event_type=event_type, xp=amount, category=rule.category, source=(source or rule.verification_source)[:16],
        source_ref=source_ref, reason=(reason or rule.label)[:256], occurred_at=when, day=day, actor=actor[:64],
    )
    try:  # a savepoint: losing a race to a parallel award must not undo the caller's other, uncommitted work
        with db.begin_nested():
            db.add(event)
            if claim_key:
                db.add(XPClaim(claim_hash=claim_hash(event_type, claim_key)))
    except IntegrityError:
        return AwardResult(False, 0, "duplicate")
    recalc(db, member)
    from app.members.badges import evaluate_auto_badges
    from app.members.notify import NOTIFY_XP_SOURCES, notify

    if event.source in NOTIFY_XP_SOURCES:
        notify(db, member.id, "xp", f"{amount:+d} XP", event.reason, {"event_id": event.id})
    evaluate_auto_badges(db, member)
    if not event_type.startswith("referral."):
        from app.members.referrals import on_award

        on_award(db, member, event)
    if commit:
        db.commit()
    return AwardResult(True, amount, None, event, capped)


def recalc(db: Session, member: Member) -> None:
    """Totals and rank always come from the ledger itself."""
    def total(category: str) -> int:
        return int(db.scalar(select(func.coalesce(func.sum(ReputationEvent.xp), 0)).where(
            ReputationEvent.member_id == member.id, ReputationEvent.category == category)) or 0)

    member.xp_total = max(0, total("reputation"))
    member.bonus_xp = max(0, total("premium"))
    from app.members.reputation import compute

    member.legacy_points, member.rank_score = compute(db, member)  # rank follows the Kiwi Rank Score
    new = rank_for(db, member.rank_score)
    if member.rank_level is not None and new.level > member.rank_level and member.id is not None:
        from app.members.notify import notify

        notify(db, member.id, "rank_up", f"{new.name} unlocked", new.description, {"level": new.level, "key": new.key})
    member.rank_level = new.level


def ranks(db: Session) -> list[Rank]:
    return list(db.scalars(select(Rank).order_by(Rank.level)))


def rank_for(db: Session, xp: int) -> Rank:
    """The highest rank whose threshold the Kiwi Rank Score has reached. Premium XP never counts."""
    all_ranks = ranks(db)
    current = all_ranks[0]
    for r in all_ranks:
        if xp >= r.min_xp:
            current = r
    return current


def reverse(db: Session, event: ReputationEvent, *, actor: str, reason: str) -> ReputationEvent:
    """Undo an award with a new negative ledger row. The original stays, marked as reversed."""
    if event.reverses_id is not None:
        raise ValueError("a reversal cannot be reversed; make a manual adjustment instead")
    if event.reversed_at is not None:
        raise ValueError("this XP was already reversed")
    member = db.get(Member, event.member_id)
    assert member is not None
    now = utcnow()
    rev = ReputationEvent(
        member_id=member.id, event_type=event.event_type, xp=-event.xp, category=event.category, source="admin",
        source_ref=f"reversal:{event.id}", reason=f"Reversed: {reason}"[:256], occurred_at=now, day=day_of(now),
        actor=actor[:64], reverses_id=event.id,
    )
    event.reversed_at = now
    db.add(rev)
    db.flush()
    recalc(db, member)
    audit(db, member.id, actor, "xp_reversed", {"event_id": event.id, "xp": event.xp, "event_type": event.event_type, "reason": reason})
    db.commit()
    return rev


def adjust(db: Session, member: Member, amount: int, *, actor: str, reason: str, ref: str) -> AwardResult:
    """A staff adjustment (positive or negative), always with a reason and an audit entry."""
    if not reason.strip():
        raise ValueError("a reason is required")
    result = award(db, member, "manual.award", f"manual:{ref}", reason=reason.strip(), xp=amount, actor=actor, commit=False)
    if result.awarded:
        audit(db, member.id, actor, "xp_adjusted", {"xp": result.xp, "reason": reason.strip(), "event_id": result.event.id if result.event else None})
    db.commit()
    return result


def raise_flag(db: Session, member: Member, kind: str, reason: str, details: dict | None = None, created_by: str = "system") -> ModerationFlag | None:
    """Open a flag for a person to review (at most one open flag of a kind per member per day). Never acts by itself."""
    today = utcnow().strftime("%Y-%m-%d")
    for f in db.scalars(select(ModerationFlag).where(ModerationFlag.member_id == member.id, ModerationFlag.kind == kind,
                                                    ModerationFlag.status == "open")):
        if f.created_at.strftime("%Y-%m-%d") == today and created_by == "system":
            return None
    flag = ModerationFlag(member_id=member.id, kind=kind, reason=reason[:256], details=details or {}, created_by=created_by)
    db.add(flag)
    db.flush()
    return flag


def audit(db: Session, member_id: int | None, actor: str, action: str, details: dict) -> None:
    db.add(MemberAuditLog(member_id=member_id, actor=actor[:64], action=action[:48], details=details))
