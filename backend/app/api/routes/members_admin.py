"""Dashboard › Members: community overview, member moderation, XP rules, ranks, badges, entitlements.

Every change here writes a MemberAuditLog row (and an Activity line). Staff XP goes through the same ledger
as everything else: adjustments and reversals are new rows with a reason, never silent edits.
"""

from datetime import timedelta
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import Field
from sqlalchemy import distinct, func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import DbSession, Runtime
from app.api.schemas import StrictInput
from app.db.base import utcnow
from app.members import service
from app.members import xp as ledger
from app.members.badges import give_badge
from app.members.catalog import ensure_catalog
from app.members.service import MemberError
from app.models.content_items import ContentItem
from app.models.members import (
    Badge, Entitlement, LinkedIdentity, Member, MemberAuditLog, MemberBadge, MemberEntitlement, MemberMission, Mission,
    ModerationFlag, Rank, ReputationEvent, XPRule,
)
from app.models.social import SocialPost
from app.services.activity import ActivityLogger
from app.workers import service as wk

router = APIRouter(tags=["members-admin"])
ACTOR = "dashboard"
STAFF_AWARDS = ("community.event", "telegram.helpful_reply")


def _member(db: Session, public_id: str) -> Member:
    m = db.scalar(select(Member).where(Member.public_id == public_id))
    if m is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Member not found")
    return m


def _log(db: Session, member: Member | None, action: str, details: dict[str, Any], summary: str) -> None:
    ledger.audit(db, member.id if member else None, ACTOR, action, details)
    db.commit()
    ActivityLogger(db).log("member_admin_action", summary, actor=ACTOR, details={"action": action})


def _row(db: Session, m: Member) -> dict[str, Any]:
    platforms = list(db.scalars(select(LinkedIdentity.platform).where(LinkedIdentity.member_id == m.id)))
    flags = db.scalar(select(func.count()).select_from(ModerationFlag).where(ModerationFlag.member_id == m.id,
                                                                             ModerationFlag.status == "open")) or 0
    return {"id": m.public_id, "username": m.username, "display_name": m.display_name, "xp": m.xp_total, "bonus_xp": m.bonus_xp,
            "rank_level": m.rank_level, "rank": ledger.rank_for(db, m.rank_score).name, "linked": sorted(platforms),
            "rank_score": m.rank_score, "legacy_points": m.legacy_points,
            "last_active_at": m.last_active_at.isoformat() if m.last_active_at else None, "joined_at": m.created_at.isoformat(),
            "status": m.status, "xp_suspended": m.xp_suspended, "open_flags": int(flags), "leaderboard_opt_out": m.leaderboard_opt_out,
            "roles": sorted(wk.roles_of(db, m.id))}


# --- overview ---------------------------------------------------------------------------------------------------


@router.get("/members-admin/overview")
def overview(db: DbSession) -> dict[str, Any]:
    ensure_catalog(db)
    now = utcnow()
    week, month = now - timedelta(days=7), now - timedelta(days=30)
    count = lambda *w: int(db.scalar(select(func.count()).select_from(Member).where(*w)) or 0)  # noqa: E731
    today = ledger.day_of(now)
    ranks = ledger.ranks(db)
    dist = dict(db.execute(select(Member.rank_level, func.count()).group_by(Member.rank_level)).all())
    linked = dict(db.execute(select(LinkedIdentity.platform, func.count()).group_by(LinkedIdentity.platform)).all())
    days = [(now - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(29, -1, -1)]
    joined: dict[str, int] = {}
    for created in db.scalars(select(Member.created_at).where(Member.created_at >= month)):
        joined[ledger.day_of(created)] = joined.get(ledger.day_of(created), 0) + 1
    over_time, running = [], count(Member.created_at < month)
    for d in days:
        running += joined.get(d, 0)
        over_time.append({"day": d, "members": running})
    active = dict(db.execute(select(ReputationEvent.day, func.count(distinct(ReputationEvent.member_id)))
                             .where(ReputationEvent.day.in_(days)).group_by(ReputationEvent.day)).all())
    by_source = db.execute(select(ReputationEvent.source, func.sum(ReputationEvent.xp)).where(
        ReputationEvent.occurred_at >= month, ReputationEvent.category == "reputation").group_by(ReputationEvent.source)).all()
    return {
        "totals": {
            "members": count(), "active_7d": count(Member.last_active_at >= week), "new_7d": count(Member.created_at >= week),
            "banned": count(Member.status == "banned"), "xp_suspended": count(Member.xp_suspended.is_(True)),
            "xp_today": int(db.scalar(select(func.coalesce(func.sum(ReputationEvent.xp), 0)).where(
                ReputationEvent.day == today, ReputationEvent.xp > 0, ReputationEvent.reverses_id.is_(None))) or 0),
            "open_flags": int(db.scalar(select(func.count()).select_from(ModerationFlag).where(ModerationFlag.status == "open")) or 0),
        },
        "linked": {p: int(linked.get(p, 0)) for p in service.PLATFORMS},
        "rank_distribution": [{"level": r.level, "name": r.name, "members": int(dist.get(r.level, 0))} for r in ranks],
        "members_over_time": over_time,
        "active_per_day": [{"day": d, "members": int(active.get(d, 0))} for d in days],
        "xp_by_source": sorted(({"source": s, "xp": int(x or 0)} for s, x in by_source), key=lambda r: -r["xp"]),
    }


@router.get("/members-admin/members")
def members(db: DbSession, q: str = "", filter: Literal["all", "flagged", "suspended", "banned"] = "all",
            limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0)) -> dict[str, Any]:
    stmt = select(Member)
    if q.strip():
        like = f"%{q.strip().lower()}%"
        stmt = stmt.where(or_(Member.username_key.like(like), func.lower(Member.display_name).like(like)))
    if filter == "flagged":
        stmt = stmt.where(Member.id.in_(select(ModerationFlag.member_id).where(ModerationFlag.status == "open")))
    elif filter == "suspended":
        stmt = stmt.where(Member.xp_suspended.is_(True))
    elif filter == "banned":
        stmt = stmt.where(Member.status == "banned")
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(Member.xp_total.desc(), Member.created_at).limit(limit).offset(offset))
    return {"total": int(total), "members": [_row(db, m) for m in rows]}


@router.get("/members-admin/members/{public_id}")
def member_detail(public_id: str, db: DbSession) -> dict[str, Any]:
    m = _member(db, public_id)
    idents = db.scalars(select(LinkedIdentity).where(LinkedIdentity.member_id == m.id))
    flags = db.scalars(select(ModerationFlag).where(ModerationFlag.member_id == m.id).order_by(ModerationFlag.created_at.desc()))
    audit = db.scalars(select(MemberAuditLog).where(MemberAuditLog.member_id == m.id).order_by(MemberAuditLog.id.desc()).limit(100))
    events = db.scalars(select(ReputationEvent).where(ReputationEvent.member_id == m.id)
                        .order_by(ReputationEvent.occurred_at.desc(), ReputationEvent.id.desc()).limit(500))
    from app.members import reputation, x_actions
    from app.members.insights import telegram_activity

    return {
        "member": _row(db, m),
        "rank": service.rank_progress(db, m),
        # what members never see: X connection scopes / state (never the tokens) and Telegram statistics
        "x_connection": x_actions.admin_status(db, m),
        "telegram": telegram_activity(db, m),
        "reputation": reputation.explain(db, m),  # the exact rank-score math
        "identities": [{"platform": i.platform, "username": i.username, "display_name": i.display_name,
                        "platform_user_id": i.platform_user_id, "verified_at": i.verified_at.isoformat(), "status": i.status} for i in idents],
        "badges": service.badges_of(db, m),
        "entitlements": service.entitlements_of(db, m),
        "flags": [{"id": f.id, "kind": f.kind, "reason": f.reason, "status": f.status, "created_by": f.created_by,
                   "created_at": f.created_at.isoformat(), "resolution": f.resolution, "details": f.details} for f in flags],
        "events": [service.event_out(e) | {"actor": e.actor, "source_ref": e.source_ref} for e in events],
        "audit": [{"actor": a.actor, "action": a.action, "details": a.details, "at": a.created_at.isoformat()} for a in audit],
    }


# --- moderation actions -----------------------------------------------------------------------------------------


class XPAdjust(StrictInput):
    amount: int = Field(ge=-100000, le=100000)
    reason: str = Field(min_length=3, max_length=200)


@router.post("/members-admin/members/{public_id}/xp")
def adjust_xp(public_id: str, payload: XPAdjust, db: DbSession) -> dict[str, Any]:
    m = _member(db, public_id)
    if payload.amount == 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Amount cannot be zero")
    import uuid

    r = ledger.adjust(db, m, payload.amount, actor=ACTOR, reason=payload.reason, ref=str(uuid.uuid4()))
    if not r.awarded:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Not applied ({r.refused})")
    ActivityLogger(db).log("member_admin_action", f"{m.username}: {payload.amount:+d} XP ({payload.reason})", actor=ACTOR)
    return member_detail(public_id, db)


class StaffAward(StrictInput):
    event_type: Literal["community.event", "telegram.helpful_reply"]
    reason: str = Field(min_length=3, max_length=200)
    reference: str = Field(min_length=1, max_length=96)  # e.g. the event name or the Telegram message link: one award each


@router.post("/members-admin/members/{public_id}/award")
def staff_award(public_id: str, payload: StaffAward, db: DbSession) -> dict[str, Any]:
    m = _member(db, public_id)
    r = ledger.award(db, m, payload.event_type, f"staff:{payload.reference}", reason=payload.reason, actor=ACTOR)
    if not r.awarded:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Not awarded ({r.refused})")
    _log(db, m, "xp_staff_award", {"event_type": payload.event_type, "xp": r.xp, "reason": payload.reason, "reference": payload.reference},
         f"{m.username}: +{r.xp} XP ({payload.reason})")
    return member_detail(public_id, db)


class Reason(StrictInput):
    reason: str = Field(min_length=3, max_length=200)


@router.post("/members-admin/events/{event_id}/reverse")
def reverse_event(event_id: int, payload: Reason, db: DbSession) -> dict[str, Any]:
    e = db.get(ReputationEvent, event_id)
    if e is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "XP event not found")
    try:
        ledger.reverse(db, e, actor=ACTOR, reason=payload.reason)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    m = db.get(Member, e.member_id)
    assert m is not None
    ActivityLogger(db).log("member_admin_action", f"{m.username}: reversed {e.xp:+d} XP ({payload.reason})", actor=ACTOR)
    return member_detail(m.public_id, db)


class BadgeGrant(StrictInput):
    badge: str = Field(min_length=1, max_length=32)
    reason: str = Field(min_length=3, max_length=200)


@router.post("/members-admin/members/{public_id}/badges")
def grant_badge(public_id: str, payload: BadgeGrant, db: DbSession) -> dict[str, Any]:
    m = _member(db, public_id)
    try:
        give_badge(db, m, payload.badge, actor=ACTOR, reason=payload.reason)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    _log(db, m, "badge_awarded", {"badge": payload.badge, "reason": payload.reason}, f"{m.username}: badge {payload.badge}")
    return member_detail(public_id, db)


@router.post("/members-admin/members/{public_id}/badges/{key}/remove")
def remove_badge(public_id: str, key: str, payload: Reason, db: DbSession) -> dict[str, Any]:
    m = _member(db, public_id)
    mb = db.scalar(select(MemberBadge).join(Badge, Badge.id == MemberBadge.badge_id).where(MemberBadge.member_id == m.id, Badge.key == key))
    if mb is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "The member does not have this badge")
    db.delete(mb)
    _log(db, m, "badge_removed", {"badge": key, "reason": payload.reason}, f"{m.username}: badge {key} removed")
    return member_detail(public_id, db)


class StatusChange(StrictInput):
    status: Literal["active", "banned"] | None = None
    xp_suspended: bool | None = None
    reason: str = Field(min_length=3, max_length=200)


@router.post("/members-admin/members/{public_id}/status")
def change_status(public_id: str, payload: StatusChange, db: DbSession) -> dict[str, Any]:
    m = _member(db, public_id)
    changes: dict[str, Any] = {}
    if payload.status is not None and payload.status != m.status:
        m.status = payload.status
        changes["status"] = payload.status
    if payload.xp_suspended is not None and payload.xp_suspended != m.xp_suspended:
        m.xp_suspended = payload.xp_suspended
        changes["xp_suspended"] = payload.xp_suspended
    if not changes:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Nothing to change")
    _log(db, m, "status_changed", {**changes, "reason": payload.reason}, f"{m.username}: {changes} ({payload.reason})")
    return member_detail(public_id, db)


class RoleChange(StrictInput):
    role: Literal["WORKER", "MANAGER"]
    grant: bool


@router.post("/members-admin/members/{public_id}/roles")
def change_role(public_id: str, payload: RoleChange, db: DbSession) -> dict[str, Any]:
    """One-click team roles from the members list: the same grant / revoke as Admin → Workers (history kept there)."""
    m = _member(db, public_id)
    try:
        if payload.grant:
            wk.grant(db, m.id, payload.role, ACTOR)
        else:
            wk.revoke(db, m.id, payload.role, ACTOR)
    except wk.WorkerError as e:
        raise HTTPException(e.status, str(e)) from e
    _log(db, m, "role_granted" if payload.grant else "role_removed", {"role": payload.role},
         f"{m.username}: {payload.role.title()} role {'given' if payload.grant else 'removed'}")
    return _row(db, m)


@router.post("/members-admin/members/{public_id}/flags")
def flag_member(public_id: str, payload: Reason, db: DbSession) -> dict[str, Any]:
    m = _member(db, public_id)
    ledger.raise_flag(db, m, "manual", payload.reason, created_by=ACTOR)
    _log(db, m, "flagged", {"reason": payload.reason}, f"{m.username}: flagged ({payload.reason})")
    return member_detail(public_id, db)


class FlagResolve(StrictInput):
    status: Literal["resolved", "dismissed"]
    resolution: str = Field(min_length=3, max_length=500)


@router.post("/members-admin/flags/{flag_id}/resolve")
def resolve_flag(flag_id: int, payload: FlagResolve, db: DbSession) -> dict[str, Any]:
    f = db.get(ModerationFlag, flag_id)
    if f is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Flag not found")
    f.status, f.resolution, f.resolved_by, f.resolved_at = payload.status, payload.resolution, ACTOR, utcnow()
    m = db.get(Member, f.member_id)
    assert m is not None
    _log(db, m, "flag_" + payload.status, {"flag_id": flag_id, "resolution": payload.resolution}, f"{m.username}: flag {payload.status}")
    return member_detail(m.public_id, db)


class EntitlementGrant(StrictInput):
    entitlement: str = Field(min_length=1, max_length=32)
    grant: bool = True
    reason: str = Field(min_length=3, max_length=200)


@router.post("/members-admin/members/{public_id}/entitlements")
def grant_entitlement(public_id: str, payload: EntitlementGrant, db: DbSession) -> dict[str, Any]:
    m = _member(db, public_id)
    e = db.scalar(select(Entitlement).where(Entitlement.key == payload.entitlement))
    if e is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Entitlement not found")
    existing = db.scalar(select(MemberEntitlement).where(MemberEntitlement.member_id == m.id, MemberEntitlement.entitlement_id == e.id))
    if payload.grant and existing is None:
        db.add(MemberEntitlement(member_id=m.id, entitlement_id=e.id, granted_by=ACTOR))
    elif not payload.grant and existing is not None:
        db.delete(existing)
    _log(db, m, "entitlement_" + ("granted" if payload.grant else "revoked"), {"entitlement": e.key, "reason": payload.reason},
         f"{m.username}: {e.name} {'granted' if payload.grant else 'revoked'}")
    return member_detail(public_id, db)


@router.post("/members-admin/members/{public_id}/reset-earn")
def reset_earn(public_id: str, payload: Reason, db: DbSession) -> dict[str, Any]:
    """Start the Earn funnel over for one member (e.g. to re-test it). Keeps the Passport, other XP, linked accounts and
    the verified wallet; removes the Earn XP events (and their one-time claims), applications and the trading account."""
    from app.tradingbot.service import reset_earn as do_reset

    m = _member(db, public_id)
    try:
        out = do_reset(db, m, ACTOR, payload.reason)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    ActivityLogger(db).log("member_admin_action", f"{m.username}: Earn progress reset ({payload.reason})", actor=ACTOR)
    return out


@router.delete("/members-admin/members/{public_id}")
def delete_member(public_id: str, payload: Reason, db: DbSession) -> dict[str, Any]:
    """Delete on the member's request (or for legal reasons). Banning is the normal moderation tool."""
    m = _member(db, public_id)
    username = m.username
    ledger.audit(db, None, ACTOR, "member_deleted_by_staff", {"member_number": m.id, "reason": payload.reason})
    from app.exchange.ledger import detach_member

    detach_member(db, m.id)  # the paper exchange keeps its ledger whole (an anonymous account)
    db.delete(m)
    db.commit()
    ActivityLogger(db).log("member_admin_action", f"Member account deleted ({payload.reason})", actor=ACTOR, details={"username": username})
    return {"deleted": True}


# --- configuration ----------------------------------------------------------------------------------------------


@router.get("/members-admin/config")
def config(db: DbSession) -> dict[str, Any]:
    ensure_catalog(db)
    return {
        "rules": service.rules_out(db),
        "ranks": [service.rank_out(r) for r in ledger.ranks(db)],
        "badges": [{"key": b.key, "name": b.name, "description": b.description, "kind": b.kind, "enabled": b.enabled,
                    "criteria": b.criteria} for b in db.scalars(select(Badge).order_by(Badge.id))],
        "entitlements": [{"key": e.key, "name": e.name, "description": e.description, "required_rank": e.required_rank,
                          "enabled": e.enabled} for e in db.scalars(select(Entitlement).order_by(Entitlement.id))],
        "daily_xp_cap": ledger.DAILY_XP_CAP,
    }


class RuleUpdate(StrictInput):
    xp: int | None = Field(None, ge=0, le=1000)
    daily_cap: int | None = Field(None, ge=0, le=10000)
    cooldown_seconds: int | None = Field(None, ge=0, le=30 * 86400)
    enabled: bool | None = None


@router.patch("/members-admin/rules/{event_type}")
def update_rule(event_type: str, payload: RuleUpdate, db: DbSession) -> dict[str, Any]:
    rule = db.scalar(select(XPRule).where(XPRule.event_type == event_type))
    if rule is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Rule not found")
    if payload.enabled and rule.availability != "available":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"This rule cannot be switched on: {rule.availability_note or rule.availability}")
    changes = payload.model_dump(exclude_none=True)
    for k, v in changes.items():
        setattr(rule, k, v)
    _log(db, None, "rule_changed", {"event_type": event_type, **changes}, f"XP rule {event_type}: {changes}")
    return config(db)


class RankUpdate(StrictInput):
    name: str | None = Field(None, min_length=2, max_length=64)
    min_xp: int | None = Field(None, ge=0, le=10_000_000)


@router.patch("/members-admin/ranks/{level}")
def update_rank(level: int, payload: RankUpdate, db: DbSession) -> dict[str, Any]:
    rank = db.scalar(select(Rank).where(Rank.level == level))
    if rank is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Rank not found")
    if payload.min_xp is not None:
        if level == 1 and payload.min_xp != 0:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "The first rank always starts at 0 XP")
        prev = db.scalar(select(Rank.min_xp).where(Rank.level == level - 1))
        nxt = db.scalar(select(Rank.min_xp).where(Rank.level == level + 1))
        if (prev is not None and payload.min_xp <= prev) or (nxt is not None and payload.min_xp >= nxt):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Thresholds must increase from rank to rank")
    changes = payload.model_dump(exclude_none=True)
    for k, v in changes.items():
        setattr(rank, k, v)
    db.flush()
    if "min_xp" in changes:  # keep every member's stored rank in line with the new ladder
        for m in db.scalars(select(Member)):
            m.rank_level = ledger.rank_for(db, m.rank_score).level
    _log(db, None, "rank_changed", {"level": level, **changes}, f"Kiwi rank {level}: {changes}")
    return config(db)


class EntitlementUpdate(StrictInput):
    enabled: bool | None = None
    required_rank: int | None = Field(None, ge=1, le=10)


@router.patch("/members-admin/entitlements/{key}")
def update_entitlement(key: str, payload: EntitlementUpdate, db: DbSession) -> dict[str, Any]:
    e = db.scalar(select(Entitlement).where(Entitlement.key == key))
    if e is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Entitlement not found")
    changes = payload.model_dump(exclude_none=True)
    for k, v in changes.items():
        setattr(e, k, v)
    _log(db, None, "entitlement_changed", {"key": key, **changes}, f"Entitlement {key}: {changes}")
    return config(db)


@router.get("/members-admin/audit")
def audit_log(db: DbSession, limit: int = Query(200, ge=1, le=1000)) -> dict[str, Any]:
    rows = db.execute(select(MemberAuditLog, Member.username).outerjoin(Member, Member.id == MemberAuditLog.member_id)
                      .order_by(MemberAuditLog.id.desc()).limit(limit))
    return {"entries": [{"id": a.id, "member": u, "actor": a.actor, "action": a.action, "details": a.details,
                         "at": a.created_at.isoformat()} for a, u in rows]}



# --- missions ---------------------------------------------------------------------------------------------------


def _mission_out(db: Session, m: Mission) -> dict[str, Any]:
    from app.members.missions import EVENT_RULE, VERIFICATION_LABEL, X_ACTION_KINDS, _xp_for

    rule = db.scalar(select(XPRule).where(XPRule.event_type == EVENT_RULE.get(m.kind, "mission.complete")))

    stats = db.execute(select(MemberMission.status, func.count(), func.coalesce(func.sum(MemberMission.xp_awarded), 0))
                       .where(MemberMission.mission_id == m.id).group_by(MemberMission.status)).all()
    by = {st: (int(n), int(x)) for st, n, x in stats}
    return {"id": m.id, "key": m.key, "kind": m.kind, "cadence": m.cadence, "title": m.title, "description": m.description,
            "platform": m.platform, "xp": _xp_for(db, m), "xp_from_rule": m.kind in EVENT_RULE, "verification": m.verification,
            "verification_label": VERIFICATION_LABEL[m.verification], "verification_method": m.verification_method,
            "status": m.status, "goal": m.goal, "target": m.target or {}, "created_by": m.created_by,
            "expires_at": m.expires_at.isoformat() if m.expires_at else None, "created_at": m.created_at.isoformat(),
            "completed": by.get("completed", (0, 0))[0], "xp_issued": by.get("completed", (0, 0))[1],
            "pending": by.get("pending", (0, 0))[0], "rejected": by.get("rejected", (0, 0))[0],
            "daily_cap": rule.daily_cap if rule else None, "pays_xp": m.kind not in X_ACTION_KINDS,
            "no_xp_reason": "X developer policy: no rewards for actions on X" if m.kind in X_ACTION_KINDS else None}


@router.get("/members-admin/missions")
def list_missions(db: DbSession, include_expired: bool = False) -> dict[str, Any]:
    stmt = select(Mission).order_by(Mission.status, Mission.priority.desc(), Mission.id.desc())
    if not include_expired:
        stmt = stmt.where(Mission.status != "expired")
    rule = db.scalar(select(XPRule).where(XPRule.event_type == "mission.complete"))
    return {"missions": [_mission_out(db, m) for m in db.scalars(stmt.limit(200))],
            "mission_daily_cap": rule.daily_cap if rule else None}


def _price_chat_url(db: Session, url: str) -> bool:
    """Missions may only point at Price Chat's own content (never at third-party accounts)."""
    from app.api.routes.website import _links, _settings

    if url.startswith("/website/"):
        return True
    own = set(db.scalars(select(ContentItem.url))) | set(db.scalars(select(SocialPost.url).where(SocialPost.is_own.is_(True))))
    own |= {u for u in _links(db, _settings(db)).values() if u}
    return url in own


class MissionCreate(StrictInput):
    title: str = Field(min_length=3, max_length=128)
    description: str = Field("", max_length=500)
    url: str = Field(min_length=1, max_length=512)
    platform: Literal["youtube", "x", "telegram", "website"]
    xp: int = Field(ge=0, le=50)
    cadence: Literal["once", "daily"] = "once"
    days: int = Field(7, ge=1, le=60)


@router.post("/members-admin/missions")
def create_mission(payload: MissionCreate, db: DbSession) -> dict[str, Any]:
    """A manual 'open this' mission. It can only be verified as an open from the site, and only for Price Chat's own links."""
    if not _price_chat_url(db, payload.url.strip()):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Missions can only point at Price Chat's own content (a synced video, "
                                                         "a Price Chat X post, the Price Chat Telegram or a website page).")
    import uuid

    m = Mission(key=f"manual:{uuid.uuid4().hex[:12]}", kind="open_link", cadence=payload.cadence, title=payload.title.strip(),
                description=payload.description.strip(), platform=payload.platform, xp=payload.xp, verification="verified_click",
                verification_method="Opening it from this site. That is all Price Chat can verify for this mission.",
                target={"url": payload.url.strip()}, priority=80, status="active", created_by=ACTOR, starts_at=utcnow(),
                expires_at=utcnow() + timedelta(days=payload.days))
    db.add(m)
    db.flush()
    _log(db, None, "mission_created", {"mission_id": m.id, "title": m.title, "xp": m.xp, "url": payload.url}, f"Mission created: {m.title}")
    return _mission_out(db, m)


class MissionUpdate(StrictInput):
    status: Literal["active", "paused", "disabled"] | None = None
    xp: int | None = Field(None, ge=0, le=50)
    goal: int | None = Field(None, ge=1, le=7)
    title: str | None = Field(None, min_length=3, max_length=128)


@router.patch("/members-admin/missions/{mission_id}")
def update_mission(mission_id: int, payload: MissionUpdate, db: DbSession) -> dict[str, Any]:
    from app.members.missions import EVENT_RULE

    m = db.get(Mission, mission_id)
    if m is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Mission not found")
    if m.status == "expired" and payload.status == "active":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "An expired mission can't be reactivated; create a new one")
    if payload.xp is not None and m.kind in EVENT_RULE:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This mission pays the XP of its rule: change it under XP rules")
    changes = payload.model_dump(exclude_none=True)
    for k, v in changes.items():
        setattr(m, k, v)
    _log(db, None, "mission_changed", {"mission_id": m.id, "title": m.title, **changes}, f"Mission {m.title}: {changes}")
    return _mission_out(db, m)


@router.post("/members-admin/missions/generate")
def generate_missions(db: DbSession) -> dict[str, Any]:
    from app.api.routes.website import _links, _settings
    from app.members import missions

    out = missions.generate(db, _links(db, _settings(db)).get("telegram"))
    return {**out, **list_missions(db)}


# --- identity conflicts -----------------------------------------------------------------------------------------


class IdentityTransfer(StrictInput):
    platform: Literal["telegram", "x", "google"]
    to_member: str = Field(min_length=1, max_length=36)  # public id of the member who proved ownership
    reason: str = Field(min_length=3, max_length=300)


@router.post("/members-admin/flags/{flag_id}/transfer-identity")
def transfer_identity(flag_id: int, payload: IdentityTransfer, db: DbSession) -> dict[str, Any]:
    """Resolve an identity conflict: move the external account to the member it belongs to (after a person checked).
    The numeric platform id is what moves; one-time link rewards are never paid twice (XP claims)."""
    f = db.get(ModerationFlag, flag_id)
    if f is None or f.kind != "identity_conflict":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Identity conflict not found")
    uid = str(f.details.get("platform_user_id", ""))
    ident = db.scalar(select(LinkedIdentity).where(LinkedIdentity.platform == payload.platform, LinkedIdentity.platform_user_id == uid))
    target = _member(db, payload.to_member)
    if db.scalar(select(LinkedIdentity).where(LinkedIdentity.member_id == target.id, LinkedIdentity.platform == payload.platform)):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "That member already has an account linked for this platform")
    from_id = ident.member_id if ident else None
    if ident is not None:
        db.delete(ident)
        db.flush()
    db.add(LinkedIdentity(member_id=target.id, platform=payload.platform, platform_user_id=uid,
                          username=f.details.get("username"), verified_at=utcnow()))
    f.status, f.resolution, f.resolved_by, f.resolved_at = "resolved", f"Identity moved: {payload.reason}", ACTOR, utcnow()
    for mid in {from_id, target.id} - {None}:
        m = db.get(Member, mid)
        if m is not None:
            ledger.recalc(db, m)
    _log(db, target, "identity_transferred", {"platform": payload.platform, "from_member_id": from_id, "reason": payload.reason},
         f"{payload.platform} account moved to {target.username} ({payload.reason})")
    return member_detail(target.public_id, db)


# --- member sign-in with X: credentials (separate from the @pricechat brand connector) -------------------------


class XLoginClient(StrictInput):
    client_id: str = Field(min_length=10, max_length=200)
    client_secret: str | None = Field(None, max_length=200)  # leave empty for a public (native) client


@router.put("/members-admin/x-login")
def save_x_login(payload: XLoginClient, db: DbSession, runtime: Runtime) -> dict[str, Any]:
    from app.members import auth
    from app.security.credentials import secret_store

    store = secret_store(runtime.settings)
    store.set("x_member_client_id", payload.client_id.strip())
    if payload.client_secret and payload.client_secret.strip():
        store.set("x_member_client_secret", payload.client_secret.strip())
    _log(db, None, "x_login_client_saved", {"confidential": bool(payload.client_secret)}, "Member X sign-in client saved (encrypted)")
    return auth.provider_status(db, runtime.settings)


# --- reputation sources: Price Chat Telegram + Combot ------------------------------------------------------------


def _rep_out(db: Session, settings: Any) -> dict[str, Any]:
    from app.members import reputation
    from app.models.members import CombotImport, CombotRecord
    from app.security.credentials import info

    rs = reputation.settings(db)
    db.commit()
    linked_tg = list(db.scalars(select(LinkedIdentity.platform_user_id).where(LinkedIdentity.platform == "telegram")))
    have = set(db.scalars(select(CombotRecord.telegram_user_id)))
    tg_events = db.scalar(select(func.count()).select_from(ReputationEvent).where(ReputationEvent.event_type.like("telegram.%"))) or 0
    imports = db.scalars(select(CombotImport).order_by(CombotImport.id.desc()).limit(10))
    key = info("combot_api_key", settings)
    return {
        "settings": {"price_chat_weight": rs.price_chat_weight, "combot_enabled": rs.combot_enabled, "combot_weight": rs.combot_weight,
                     "legacy_cap": rs.legacy_cap, "combot_mode": rs.combot_mode, "combot_chat_id": rs.combot_chat_id,
                     "telegram_daily_cap": rs.telegram_daily_cap, "watch_seconds": rs.watch_seconds},
        "price_chat_telegram": {"status": "active", "linked_members": len(linked_tg), "xp_events": int(tg_events)},
        "combot": {
            "mode": rs.combot_mode, "api_key": key.configured, "status": ("connected" if rs.combot_mode == "api" and key.configured
                                                                          else "manual import" if rs.combot_mode == "manual" else "off"),
            "records": len(have), "linked_members": len(linked_tg),
            "matched": sum(1 for u in linked_tg if u in have), "unmatched": sum(1 for u in linked_tg if u not in have),
            "last_sync_at": rs.last_sync_at.isoformat() if rs.last_sync_at else None,
            "last_success_at": rs.last_success_at.isoformat() if rs.last_success_at else None,
            "last_error": rs.last_error, "last_records": rs.last_records,
        },
        "imports": [{"id": i.id, "source_type": i.source_type, "status": i.status, "filename": i.filename, "stats": i.stats,
                     "created_at": i.created_at.isoformat(), "applied_at": i.applied_at.isoformat() if i.applied_at else None,
                     "created_by": i.created_by} for i in imports],
        "formula": "rank score = round(Price Chat XP x price_chat_weight) + min(round(Combot XP x combot_weight), legacy_cap)",
    }


@router.get("/members-admin/x-feed")
def x_feed_stats(db: DbSession, days: int = Query(7, ge=1, le=90)) -> dict[str, Any]:
    """Read & Earn usage: posts in the feed, shown, read, XP paid, likes opened on X, reposts sent from the Passport."""
    from app.members import x_feed

    return x_feed.stats(db, days)


@router.get("/members-admin/reputation")
def reputation_sources(db: DbSession, runtime: Runtime) -> dict[str, Any]:
    return _rep_out(db, runtime.settings)


class ReputationUpdate(StrictInput):
    price_chat_weight: float | None = Field(None, ge=0, le=10)
    combot_enabled: bool | None = None
    combot_weight: float | None = Field(None, ge=0, le=10)
    legacy_cap: int | None = Field(None, ge=0, le=1_000_000)
    combot_mode: Literal["api", "manual", "off"] | None = None
    combot_chat_id: str | None = Field(None, max_length=32, pattern=r"^-?\d{5,20}$|^$")
    telegram_daily_cap: int | None = Field(None, ge=0, le=1000)  # all automatic Telegram XP per member per day
    watch_seconds: int | None = Field(None, ge=5, le=600)  # Watch & Earn: seconds played in the Passport before XP


@router.put("/members-admin/reputation")
def update_reputation(payload: ReputationUpdate, db: DbSession, runtime: Runtime) -> dict[str, Any]:
    """Change how the Kiwi Rank Score is built. Every member is recalculated straight away."""
    from app.members import reputation

    rs = reputation.settings(db)
    changes = payload.model_dump(exclude_none=True)
    for k, v in changes.items():
        setattr(rs, k, (v or None) if k == "combot_chat_id" else v)
    db.flush()
    n = reputation.recalc_all(db)
    _log(db, None, "reputation_changed", {**changes, "members_recalculated": n}, f"Rank score settings: {changes}")
    return _rep_out(db, runtime.settings)


class CombotKey(StrictInput):
    api_key: str = Field(min_length=10, max_length=200)
    chat_id: str = Field(pattern=r"^-?\d{5,20}$")


@router.put("/members-admin/reputation/combot")
async def connect_combot(payload: CombotKey, db: DbSession, runtime: Runtime) -> dict[str, Any]:
    """Save the Combot API key (encrypted) and check it against the group with the official getChats method."""
    from app.members import combot, reputation
    from app.security.credentials import secret_store

    client = combot.CombotClient(payload.api_key.strip(), runtime.settings.combot_api_base, runtime.members_transport)
    try:
        await client.call("getChats", chat_id=payload.chat_id)
    except combot.CombotError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Combot didn't accept the key for this group: {exc}") from exc
    finally:
        await client.aclose()
    secret_store(runtime.settings).set("combot_api_key", payload.api_key.strip())
    rs = reputation.settings(db)
    rs.combot_mode, rs.combot_chat_id, rs.last_error = "api", payload.chat_id, None
    _log(db, None, "combot_connected", {"chat_id": payload.chat_id}, "Combot API connected (key encrypted)")
    if runtime.scheduler is not None:
        runtime.scheduler.request_run("combot.sync", 2)
    return _rep_out(db, runtime.settings)


@router.delete("/members-admin/reputation/combot")
def disconnect_combot(db: DbSession, runtime: Runtime) -> dict[str, Any]:
    """Remove the API key. Imported records stay (switch 'Count towards rank' off to stop them contributing)."""
    from app.members import reputation
    from app.security.credentials import secret_store

    secret_store(runtime.settings).delete("combot_api_key")
    rs = reputation.settings(db)
    rs.combot_mode = "manual"
    _log(db, None, "combot_disconnected", {}, "Combot API disconnected")
    return _rep_out(db, runtime.settings)


@router.post("/members-admin/reputation/combot/sync")
async def sync_combot(db: DbSession, runtime: Runtime) -> dict[str, Any]:
    from app.members import combot

    try:
        result = await combot.sync(db, runtime.settings, runtime.members_transport)
    except combot.CombotError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    _log(db, None, "combot_synced", result, f"Combot sync: {result.get('written', 0)} records")
    return {"result": result, **_rep_out(db, runtime.settings)}


class CombotFile(StrictInput):
    filename: str = Field(min_length=1, max_length=256)
    format: Literal["csv", "json"]
    content: str = Field(min_length=1, max_length=20_000_000)
    source_date: str | None = None  # when the export was taken (ISO date); default now


@router.post("/members-admin/reputation/import")
def import_preview(payload: CombotFile, db: DbSession) -> dict[str, Any]:
    """Validate an uploaded Combot export and show what would happen. Nothing is written until 'Apply'."""
    from datetime import datetime, timezone

    from app.members import combot

    try:
        rows = combot.parse_file(payload.content, payload.format)
        ts = datetime.fromisoformat(payload.source_date).replace(tzinfo=timezone.utc) if payload.source_date else utcnow()
    except (ValueError, combot.CombotError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Could not read the file: {exc}") from exc
    if ts > utcnow():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The export date can't be in the future")
    imp = combot.preview(db, rows, payload.format, ts, filename=payload.filename)
    _log(db, None, "combot_import_previewed", {"import_id": imp.id, **{k: v for k, v in imp.stats.items() if isinstance(v, int)}},
         f"Combot import previewed: {imp.stats['valid']} valid rows")
    return {"id": imp.id, "status": imp.status, "stats": imp.stats}


@router.post("/members-admin/reputation/import/{import_id}/apply")
def import_apply(import_id: int, db: DbSession, runtime: Runtime) -> dict[str, Any]:
    from app.members import combot
    from app.models.members import CombotImport

    imp = db.get(CombotImport, import_id)
    if imp is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Import not found")
    try:
        result = combot.apply(db, imp)
    except combot.CombotError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    rs_mode = _rep_out(db, runtime.settings)["settings"]["combot_mode"]
    if rs_mode == "off":
        from app.members import reputation

        reputation.settings(db).combot_mode = "manual"
    _log(db, None, "combot_import_applied", {"import_id": import_id, **result}, f"Combot import applied: {result['written']} records")
    return {"result": result, **_rep_out(db, runtime.settings)}


@router.post("/members-admin/reputation/import/{import_id}/discard")
def import_discard(import_id: int, db: DbSession, runtime: Runtime) -> dict[str, Any]:
    from app.models.members import CombotImport

    imp = db.get(CombotImport, import_id)
    if imp is None or imp.status != "preview":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Only a previewed import can be discarded")
    imp.status, imp.rows = "discarded", []
    _log(db, None, "combot_import_discarded", {"import_id": import_id}, "Combot import discarded")
    return _rep_out(db, runtime.settings)


# --- growth: referrals, announcement channel, Telegram discussion missions ------------------------------------------


def _program_out(db: Session) -> dict[str, Any]:
    from app.members import referrals, telegram_missions
    from app.models.members import Referral

    s = referrals.program(db)
    counts = dict(db.execute(select(Referral.status, func.count()).group_by(Referral.status)).all())
    return {
        "settings": {
            "referrals_enabled": s.referrals_enabled, "referral_qualifying_events": s.referral_qualifying_events,
            "referral_min_age_hours": s.referral_min_age_hours, "referral_daily_limit": s.referral_daily_limit,
            "updates_channel": s.updates_channel, "channel_join_xp": s.channel_join_xp, "discussion_xp": s.discussion_xp,
            "discussion_window_days": s.discussion_window_days, "discussion_daily_limit": s.discussion_daily_limit,
            "leaderboard_periods": s.leaderboard_periods,
        },
        "referral_rules": [{"event_type": r.event_type, "xp": r.xp, "enabled": r.enabled}
                           for r in (ledger.get_rule(db, referrals.REFERRER_RULE), ledger.get_rule(db, referrals.WELCOME_RULE))],
        "referrals": {"joined": counts.get("joined", 0), "qualified": counts.get("qualified", 0), "rejected": counts.get("rejected", 0)},
        "channel": telegram_missions.status(db),
    }


class ProgramUpdate(StrictInput):
    referrals_enabled: bool | None = None
    referral_qualifying_events: list[str] | None = Field(None, max_length=20)
    referral_min_age_hours: int | None = Field(None, ge=0, le=24 * 30)
    referral_daily_limit: int | None = Field(None, ge=0, le=100)
    updates_channel: str | None = Field(None, max_length=64)
    channel_join_xp: int | None = Field(None, ge=0, le=500)
    discussion_xp: int | None = Field(None, ge=0, le=500)
    discussion_window_days: int | None = Field(None, ge=1, le=30)
    discussion_daily_limit: int | None = Field(None, ge=0, le=20)
    leaderboard_periods: list[Literal["all", "week", "month"]] | None = None


@router.get("/members-admin/program")
def program_get(db: DbSession) -> dict[str, Any]:
    ensure_catalog(db)
    return _program_out(db)


@router.patch("/members-admin/program")
def program_update(payload: ProgramUpdate, db: DbSession) -> dict[str, Any]:
    from app.members import referrals

    ensure_catalog(db)
    s = referrals.program(db)
    changes = payload.model_dump(exclude_none=True)
    if "referral_qualifying_events" in changes:
        known = set(db.scalars(select(XPRule.event_type)))
        bad = [e for e in changes["referral_qualifying_events"] if e not in known or e.startswith("referral.")]
        if bad:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Not usable as a qualifying action: {', '.join(bad)}")
    for k, v in changes.items():
        setattr(s, k, v)
    _log(db, None, "program_updated", changes, "Growth settings updated")
    return _program_out(db)


@router.post("/members-admin/program/check-channel")
async def program_check_channel(db: DbSession, runtime: Runtime) -> dict[str, Any]:
    """Ask Telegram what the bot can see of the announcement channel (read-only), then refresh the missions."""
    from app.members import missions, telegram_missions
    from app.telegram.errors import TelegramError
    from app.telegram.service import make_client

    try:
        client = make_client(runtime.settings, runtime.telegram.transport)
    except TelegramError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    try:
        await telegram_missions.check_channel(db, client)
    finally:
        await client.aclose()
    telegram_missions.generate(db)
    missions.expire(db)
    db.commit()
    return _program_out(db)


class DiscussionMissionCreate(StrictInput):
    message_url: str = Field(min_length=8, max_length=256)  # a link to the post in the discussion group (t.me/<group>/<id> or t.me/c/<id>/<msg>)
    title: str = Field("Join the discussion", min_length=3, max_length=128)
    description: str = Field("Comment on this Price Chat update.", max_length=500)
    xp: int | None = Field(None, ge=0, le=500)
    days: int = Field(3, ge=1, le=30)


@router.post("/members-admin/missions/telegram-discussion")
def create_discussion_mission(payload: DiscussionMissionCreate, db: DbSession) -> dict[str, Any]:
    """A mission tied to one post in an authorised Price Chat Telegram chat: completed automatically when a linked member
    comments under it in their own words (members/telegram_missions.py). The post must already be ingested."""
    import re

    from app.members import referrals, telegram_missions
    from app.models.members import Mission
    from app.models.telegram import TelegramMessage, TelegramSource

    m = re.match(r"^(?:https?://)?t\.me/(?:c/(\d+)|([A-Za-z0-9_]{4,}))/(\d+)/?$", payload.message_url.strip())
    if not m:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Use a Telegram message link like https://t.me/<group>/<number>.")
    internal, username, message_id = m.group(1), m.group(2), int(m.group(3))
    sources = list(db.scalars(select(TelegramSource).where(TelegramSource.enabled.is_(True))))
    src = next((x for x in sources if (internal and str(x.chat_id) == f"-100{internal}")
                or (username and (x.username or "").lower() == username.lower())), None)
    if src is None and username:  # a public username we don't store: match the post itself
        src = next((x for x in sources if db.scalar(select(TelegramMessage.id).where(
            TelegramMessage.chat_id == x.chat_id, TelegramMessage.message_id == message_id))), None)
    post = db.scalar(select(TelegramMessage).where(TelegramMessage.chat_id == src.chat_id, TelegramMessage.message_id == message_id)) if src else None
    if post is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "That post isn't in an authorised Price Chat chat that the bot has read yet.")
    key = f"tg-discuss:{post.chat_id}:{post.message_id}"
    if db.scalar(select(Mission.id).where(Mission.key == key)):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "There is already a mission for this post.")
    now = utcnow()
    mission = Mission(key=key, kind=telegram_missions.DISCUSSION_KIND, cadence="once", platform="telegram",
                      xp=payload.xp if payload.xp is not None else referrals.program(db).discussion_xp, verification="verified",
                      requires_platform="telegram", priority=94, title=payload.title.strip(), description=payload.description.strip(),
                      verification_method="A comment in your own words from your linked Telegram account under this post. Counted automatically.",
                      target=telegram_missions.discussion_target(db, post), starts_at=post.sent_at or now,
                      expires_at=now + timedelta(days=payload.days), created_by="dashboard")
    db.add(mission)
    _log(db, None, "mission_created", {"key": key, "kind": mission.kind}, f"Telegram discussion mission created: {mission.title}")
    return {"id": mission.id, "key": key}
