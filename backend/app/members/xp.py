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
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import event as sa_event, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.members import Member, MemberAuditLog, ModerationFlag, Rank, ReputationEvent, XPClaim, XPRule

# Sources whose XP is earned by activity (not given by staff): they share the overall daily cap.
AUTOMATIC_SOURCES = {"website", "telegram", "x", "google", "youtube", "mission"}
STAFF_SOURCES = {"moderator", "admin"}
DAILY_XP_CAP = 60  # overall automatic XP per member per UTC day
# Event types stored under an automatic source label (for the member's breakdown) whose rule is NOT under the overall cap:
# they must not count towards it either. Pack Giveaway XP is shown as "website" but has its own 100/day cap ("packs" rule
# source): counting it let packs use up the 60/day that Read & Earn, Watch, missions and the daily visit share (audit PP-3).
OUTSIDE_OVERALL_CAP = frozenset({"pack.open"})
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


BATCH_DAYS = 16  # a batch reads this many days of each member's ledger at once (the verify job looks back 14)


class _DayTotals:
    """One member's positive ledger amounts per (day, event type, source) from `since_day` on: the daily caps
    are checked against it instead of up to three SUM queries per award."""

    __slots__ = ("since_day", "rows")

    def __init__(self, db: Session, member_id: int, since_day: str) -> None:
        self.since_day = since_day
        plain = (ReputationEvent.reversed_at.is_(None) & ReputationEvent.reverses_id.is_(None)).label("plain")
        self.rows: dict[tuple[str, str, str, bool], int] = defaultdict(int)
        for day, event_type, source, is_plain, xp in db.execute(
                select(ReputationEvent.day, ReputationEvent.event_type, ReputationEvent.source, plain, func.sum(ReputationEvent.xp))
                .where(ReputationEvent.member_id == member_id, ReputationEvent.day >= since_day, ReputationEvent.xp > 0)
                .group_by(ReputationEvent.day, ReputationEvent.event_type, ReputationEvent.source, plain)):
            self.rows[(day, event_type, source, bool(is_plain))] += int(xp or 0)

    def covers(self, day: str) -> bool:
        return day >= self.since_day

    def paid(self, day: str, event_type: str | None = None, sources: set[str] | None = None) -> int:
        """What _paid_today would return."""
        return sum(v for (d, t, s, plain), v in self.rows.items()
                   if plain and d == day and (event_type is None or t == event_type) and (not sources or (s in sources and t not in OUTSIDE_OVERALL_CAP)))

    def telegram(self, day: str) -> int:
        """What telegram_paid_today would return."""
        return sum(v for (d, t, s, _), v in self.rows.items()
                   if d == day and t.startswith("telegram.") and t != "telegram.message" and s == "telegram")

    def add(self, event: ReputationEvent) -> None:
        if event.xp > 0:
            self.rows[(event.day, event.event_type, event.source, True)] += event.xp


class _Batch:
    """Per-transaction memo for many awards in a row (the members.verify job): rules, ranks, settings, each member's
    Combot record, ledger totals and daily amounts are read once and kept up to date as awards are added, instead of
    ~25 queries per award. It is emptied at every commit and rollback, so it never outlives the transaction whose
    snapshot it was read from (on SQLite no other writer can change the ledger while this transaction writes)."""

    def __init__(self) -> None:
        self.clear()

    def clear(self) -> None:
        self.rules: dict[str, XPRule] = {}
        self.ranks: list[Rank] | None = None
        self.rep_settings: Any = None
        self.records: dict[int, Any] = {}
        self.totals: dict[int, dict[str, int]] = {}
        self.days: dict[int, _DayTotals] = {}
        self.last_at: dict[tuple[int, str], datetime | None] = {}
        self.auto_badges: list[Any] | None = None
        self.owned_badges: dict[int, set[int]] = {}
        self.no_referral: set[int] = set()

    def settings(self, db: Session) -> Any:
        if self.rep_settings is None:
            from app.members.reputation import settings as rep_settings

            self.rep_settings = rep_settings(db)
        return self.rep_settings

    def day_totals(self, db: Session, member_id: int, day: str) -> _DayTotals | None:
        t = self.days.get(member_id)
        if t is None:
            t = self.days[member_id] = _DayTotals(db, member_id, day_of(utcnow() - timedelta(days=BATCH_DAYS)))
        return t if t.covers(day) else None


def _batch(db: Session) -> _Batch | None:
    return db.info.get("xp_batch")


@contextmanager
def batch(db: Session) -> Iterator[None]:
    """Many awards in a row: see _Batch. The results are exactly those of unbatched awards."""
    if _batch(db) is not None:
        yield
        return
    b = db.info["xp_batch"] = _Batch()

    def _drop(*_a: object) -> None:
        b.clear()

    def _ended(_session: Session, transaction: Any) -> None:
        if transaction.parent is None:  # the transaction committed or rolled back (not a flush's or a savepoint's)
            b.clear()

    sa_event.listen(db, "after_transaction_end", _ended)
    sa_event.listen(db, "after_soft_rollback", _drop)  # any rollback, a savepoint's too
    try:
        yield
    finally:
        sa_event.remove(db, "after_transaction_end", _ended)
        sa_event.remove(db, "after_soft_rollback", _drop)
        db.info.pop("xp_batch", None)


def is_live(when: datetime) -> bool:
    return abs((utcnow() - when).total_seconds()) < LIVE_SECONDS


def known_capped(db: Session, member: Member, event_type: str, when: datetime) -> bool:
    """In a batch: True when awarding the rule's own XP for activity at `when` would certainly be refused (rule off, or a
    daily cap already reached), so a job re-reading past activity can skip it. Never for live activity: its refusals count
    towards the rapid-activity flag. Only past refusals are skipped, and those have no side effects."""
    b = _batch(db)
    if b is None or is_live(when):
        return False
    try:
        rule = get_rule(db, event_type)
    except ValueError:
        return False
    if rule.availability != "available" or not rule.enabled:
        return True
    if rule.xp <= 0:
        return False
    day = day_of(when)
    totals = b.day_totals(db, member.id, day)
    if totals is None:
        return False
    if rule.daily_cap is not None and rule.daily_cap - totals.paid(day, event_type=event_type) <= 0:
        return True
    if event_type.startswith("telegram.") and rule.verification_source == "telegram" and \
            b.settings(db).telegram_daily_cap - totals.telegram(day) <= 0:
        return True
    return rule.verification_source in AUTOMATIC_SOURCES and DAILY_XP_CAP - totals.paid(day, sources=AUTOMATIC_SOURCES) <= 0


def reset_for_tests() -> None:
    with _refusals_lock:
        _refusals.clear()


def day_of(ts: datetime) -> str:
    """The UTC calendar day an activity happened on (daily caps and streaks use it)."""
    return (ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).strftime("%Y-%m-%d")


def claim_hash(event_type: str, key: str) -> str:
    return hashlib.sha256(f"{_CLAIM_SALT}|{event_type}|{key}".encode()).hexdigest()


def get_rule(db: Session, event_type: str) -> XPRule:
    b = _batch(db)
    rule = b.rules.get(event_type) if b else None
    if rule is None:
        rule = db.scalar(select(XPRule).where(XPRule.event_type == event_type))
    if rule is None:
        raise ValueError(f"unknown XP event type: {event_type}")
    if b:
        b.rules[event_type] = rule
    return rule


def _paid_today(db: Session, member_id: int, day: str, event_type: str | None = None, sources: set[str] | None = None) -> int:
    stmt = select(func.coalesce(func.sum(ReputationEvent.xp), 0)).where(
        ReputationEvent.member_id == member_id, ReputationEvent.day == day, ReputationEvent.xp > 0,
        ReputationEvent.reversed_at.is_(None), ReputationEvent.reverses_id.is_(None),
    )
    if event_type:
        stmt = stmt.where(ReputationEvent.event_type == event_type)
    if sources:  # the overall cap's sum: automatic sources, minus the event types that aren't under that cap
        stmt = stmt.where(ReputationEvent.source.in_(sources), ReputationEvent.event_type.not_in(OUTSIDE_OVERALL_CAP))
    return int(db.scalar(stmt) or 0)


def telegram_paid_today(db: Session, member_id: int, day: str) -> int:
    """All automatic Telegram XP (telegram.*) paid to the member on `day`, for the overall Telegram daily cap."""
    return int(db.scalar(select(func.coalesce(func.sum(ReputationEvent.xp), 0)).where(
        ReputationEvent.member_id == member_id, ReputationEvent.day == day, ReputationEvent.xp > 0,
        ReputationEvent.event_type.like("telegram.%"), ReputationEvent.event_type != "telegram.message",  # it has its own 50/day cap
        ReputationEvent.source == "telegram")) or 0)


def _lock_member(db: Session, member: Member) -> None:
    """A no-op UPDATE of the member's row: it takes the database write lock (SQLite) or the row lock (PostgreSQL) until the
    caller's transaction ends, so awards to one member can't interleave their cap checks with their inserts."""
    if member.id is None:
        return
    t = Member.__table__
    db.execute(t.update().where(t.c.id == member.id).values(updated_at=t.c.updated_at))  # (updated_at kept: no onupdate)


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

    def limits() -> tuple[AwardResult | None, int, bool]:
        """The per-member limit, cooldown and daily caps: (refusal or None, amount to pay, capped)."""
        if rule.max_per_member is not None:
            done = db.scalar(select(func.count()).select_from(ReputationEvent).where(
                ReputationEvent.member_id == member.id, ReputationEvent.event_type == event_type, ReputationEvent.reversed_at.is_(None),
                ReputationEvent.reverses_id.is_(None))) or 0
            if done >= rule.max_per_member:
                return AwardResult(False, 0, "max_reached"), 0, False
        b = _batch(db)
        if rule.cooldown_seconds:
            if b and (member.id, event_type) in b.last_at:
                last = b.last_at[(member.id, event_type)]
            else:
                last = db.scalar(select(ReputationEvent.occurred_at).where(
                    ReputationEvent.member_id == member.id, ReputationEvent.event_type == event_type, ReputationEvent.reverses_id.is_(None),
                ).order_by(ReputationEvent.occurred_at.desc()).limit(1))
                if b:
                    b.last_at[(member.id, event_type)] = last
            if last is not None and abs((when - last).total_seconds()) < rule.cooldown_seconds:
                return _refuse(db, member, rule, "cooldown", day, live), 0, False

        amount = xp if xp is not None else rule.xp
        if amount == 0:
            return AwardResult(False, 0, "zero"), 0, False
        capped = False
        totals = b.day_totals(db, member.id, day) if b and amount > 0 else None
        if amount > 0 and rule.daily_cap is not None:
            room = rule.daily_cap - (totals.paid(day, event_type=event_type) if totals else _paid_today(db, member.id, day, event_type=event_type))
            if room <= 0:
                return _refuse(db, member, rule, "daily_cap", day, live), 0, False
            if amount > room:
                amount, capped = room, True
        if amount > 0 and event_type.startswith("telegram.") and rule.verification_source == "telegram":
            from app.members.reputation import settings as rep_settings

            room = (b.settings(db) if b else rep_settings(db)).telegram_daily_cap - (
                totals.telegram(day) if totals else telegram_paid_today(db, member.id, day))
            if room <= 0:
                return _refuse(db, member, rule, "telegram_cap", day, live), 0, False
            if amount > room:
                amount, capped = room, True
        if amount > 0 and rule.verification_source in AUTOMATIC_SOURCES:
            room = DAILY_XP_CAP - (totals.paid(day, sources=AUTOMATIC_SOURCES) if totals else
                                   _paid_today(db, member.id, day, sources=AUTOMATIC_SOURCES))
            if room <= 0:
                return _refuse(db, member, rule, "overall_cap", day, live), 0, False
            if amount > room:
                amount, capped = room, True
        return None, amount, capped

    refused, amount, capped = limits()
    if refused:
        return refused
    # The checks above are read-then-insert: parallel requests could all pass them before any of them inserts (audit PP-6:
    # 30 parallel awards paid 30 XP against a daily cap of 10). So take the write lock now and check again: on SQLite a
    # write is the only way to hold it until commit (BEGIN IMMEDIATE semantics; SELECT ... FOR UPDATE is a no-op there), on
    # PostgreSQL it locks this member's row. Parallel awards for the member then run one at a time and the second pass sees
    # what the others paid. Refusals above (the common case: repeats, caps already reached) never take the lock.
    _lock_member(db, member)
    refused, amount, capped = limits()
    if refused:
        return refused

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
    b = _batch(db)  # (emptied if the savepoint rolled back)
    if b:
        if member.id in b.totals:
            b.totals[member.id][event.category] = b.totals[member.id].get(event.category, 0) + event.xp
        if member.id in b.days:
            b.days[member.id].add(event)
        key = (member.id, event_type)
        if key in b.last_at:
            prev = b.last_at[key]
            b.last_at[key] = when if prev is None or when > prev else prev
    recalc(db, member)
    from app.members.badges import evaluate_auto_badges
    from app.members.notify import NOTIFY_XP_SOURCES, notify

    if event.source in NOTIFY_XP_SOURCES:
        notify(db, member.id, "xp", f"{amount:+d} XP", event.reason, {"event_id": event.id})
    if b:
        _batch_badges(db, b, member, event_type)
    else:
        evaluate_auto_badges(db, member)
    if not event_type.startswith("referral."):
        from app.members.referrals import on_award

        on_award(db, member, event, b.no_referral if b else None)
    if commit:
        db.commit()
    return AwardResult(True, amount, None, event, capped)


def _batch_badges(db: Session, b: _Batch, member: Member, event_type: str) -> None:
    """evaluate_auto_badges within a batch: the auto badges and what the member owns are read once. After the member's
    first award in the batch only badges counting this award's event type can newly qualify (join-date and member-number
    criteria cannot change within one transaction)."""
    from app.members.badges import auto_badges, evaluate_auto_badges, owned_badge_ids

    if b.auto_badges is None:
        b.auto_badges = auto_badges(db)
    owned = b.owned_badges.get(member.id)
    if owned is None:
        owned = b.owned_badges[member.id] = owned_badge_ids(db, member)
        candidates = b.auto_badges
    else:
        candidates = [bd for bd in b.auto_badges if not {"joined_before", "member_number_max"} & set(bd.criteria or {})
                      and (bd.criteria or {}).get("event_type") == event_type]
    if candidates:
        evaluate_auto_badges(db, member, candidates, owned)


def _category_totals(db: Session, member_id: int | None) -> dict[str, int]:
    return {c: int(x or 0) for c, x in db.execute(select(ReputationEvent.category, func.sum(ReputationEvent.xp))
                                                  .where(ReputationEvent.member_id == member_id,
                                                         ReputationEvent.category.in_(("reputation", "premium")))
                                                  .group_by(ReputationEvent.category))}


def recalc(db: Session, member: Member) -> None:
    """Totals and rank always come from the ledger itself (in a batch: from its running totals, read once)."""
    b = _batch(db) if member.id is not None else None
    if b is not None:
        totals = b.totals.get(member.id)
        if totals is None:
            totals = b.totals[member.id] = _category_totals(db, member.id)
    else:
        totals = _category_totals(db, member.id)
    member.xp_total = max(0, totals.get("reputation", 0))
    member.bonus_xp = max(0, totals.get("premium", 0))
    from app.members.reputation import combot_record_for, compute, legacy_points

    if b is not None:  # the same formula as compute(), with the settings and the Combot record read once
        rs = b.settings(db)
        if member.id not in b.records:
            b.records[member.id] = combot_record_for(db, member)
        member.legacy_points = legacy_points(rs, b.records[member.id])
        member.rank_score = round(member.xp_total * rs.price_chat_weight) + member.legacy_points
        if b.ranks is None:
            b.ranks = ranks(db)
        new = rank_for(db, member.rank_score, b.ranks)
    else:
        member.legacy_points, member.rank_score = compute(db, member)  # rank follows the Kiwi Rank Score
        new = rank_for(db, member.rank_score)
    if member.rank_level is not None and new.level > member.rank_level and member.id is not None:
        from app.members.notify import notify

        notify(db, member.id, "rank_up", f"{new.name} unlocked", new.description, {"level": new.level, "key": new.key})
    member.rank_level = new.level


def ranks(db: Session) -> list[Rank]:
    return list(db.scalars(select(Rank).order_by(Rank.level)))


def rank_for(db: Session, xp: int, all_ranks: list[Rank] | None = None) -> Rank:
    """The highest rank whose threshold the Kiwi Rank Score has reached. Premium XP never counts. Lists of members pass
    `all_ranks` (from `ranks`) so the table is read once, not once per row."""
    all_ranks = all_ranks or ranks(db)
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
