"""Members: accounts, explicit identity linking, sessions, profile, leaderboard, privacy (disconnect / delete)."""

import hashlib
import re
import secrets
import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.members import xp as ledger
from app.members.catalog import ensure_catalog
from app.models.members import (
    Badge, Entitlement, LinkedIdentity, Member, MemberBadge, MemberEntitlement, MemberSession, Rank, ReputationEvent, XPRule,
)

PLATFORMS = ("telegram", "x", "google")
PLATFORM_LABEL = {"telegram": "Telegram", "x": "X", "google": "Google / YouTube"}
USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,20}$")
RESERVED = {"admin", "administrator", "pricechat", "price_chat", "moderator", "mod", "support", "official", "staff", "system",
            "root", "kiwi", "team", "help", "null", "undefined"}


class MemberError(ValueError):
    """A problem the member can act on; the message is shown to them."""


# --- usernames -------------------------------------------------------------------------------------------------


def username_problem(db: Session, name: str, member: Member | None = None) -> str | None:
    if not USERNAME_RE.match(name):
        return "Use 3-20 letters, numbers or underscores."
    if name.lower() in RESERVED:
        return "That username is reserved."
    taken = db.scalar(select(Member.id).where(Member.username_key == name.lower()))
    if taken and (member is None or taken != member.id):
        return "That username is taken."
    return None


def _neutral_username(db: Session) -> str:
    """New members get a neutral name and choose their own. Never a platform name or handle: the public
    leaderboard must not reveal anyone's real name or tie them to another account they didn't choose to show."""
    for _ in range(50):
        name = f"Kiwi{secrets.randbelow(900000) + 100000}"
        if username_problem(db, name) is None:
            return name
    raise RuntimeError("could not find a free username")


# --- members & identities -------------------------------------------------------------------------------------


def create_member(db: Session) -> Member:
    ensure_catalog(db)
    name = _neutral_username(db)
    m = Member(public_id=str(uuid.uuid4()), username=name, username_key=name.lower(), display_name=name, last_active_at=utcnow())
    db.add(m)
    db.flush()
    ledger.audit(db, m.id, "member", "member_created", {"username": name})
    ledger.award(db, m, "website.account_created", "account", commit=False)
    return m


def link_identity(db: Session, member: Member, platform: str, platform_user_id: str, username: str | None,
                  display_name: str | None) -> LinkedIdentity:
    """Link an account the member has just proven they own by signing in to it. Never inferred from names."""
    if platform not in PLATFORMS:
        raise MemberError("Unsupported platform.")
    uid = str(platform_user_id)
    existing = db.scalar(select(LinkedIdentity).where(LinkedIdentity.platform == platform, LinkedIdentity.platform_user_id == uid))
    if existing and existing.member_id != member.id:
        # Blocked: one external account belongs to one member. A person on the team resolves it (Members › flags).
        ledger.raise_flag(db, member, "identity_conflict",
                          f"Tried to link a {PLATFORM_LABEL[platform]} account that is linked to another member",
                          {"platform": platform, "platform_user_id": uid, "username": username, "held_by_member_id": existing.member_id})
        db.commit()
        raise MemberError(f"That {PLATFORM_LABEL[platform]} account is already linked to another Price Chat member. "
                          "We've asked the Price Chat team to look into it.")
    mine = db.scalar(select(LinkedIdentity).where(LinkedIdentity.member_id == member.id, LinkedIdentity.platform == platform))
    if mine and mine.platform_user_id != uid:
        raise MemberError(f"You already linked a different {PLATFORM_LABEL[platform]} account. Disconnect it first.")
    now = utcnow()
    if mine:
        mine.username, mine.display_name, mine.status = username, display_name, "active"
        return mine
    ident = LinkedIdentity(member_id=member.id, platform=platform, platform_user_id=uid, username=(username or None),
                           display_name=(display_name or None), verified_at=now)
    db.add(ident)
    db.flush()
    ledger.audit(db, member.id, "member", "identity_linked", {"platform": platform})
    from app.vpn import trial_guard

    trial_guard.mark_if_trial_used(db, member)  # had a Kiwi VPN trial: this identity has had one too
    ledger.award(db, member, f"identity.{platform}_linked", f"{platform}:{uid}", claim_key=f"{platform}:{uid}",
                 reason=f"Connected {PLATFORM_LABEL[platform]}", commit=False)
    if platform == "telegram":
        ledger.recalc(db, member)  # legacy rank points follow the exact numeric Telegram id
        from app.members.combot import on_telegram_linked

        on_telegram_linked(db, member)
        if uid.lstrip("-").isdigit():  # Price Chat Predictions: the Telegram account's calls (and pending XP) become the member's
            from app.predictions.service import on_telegram_linked as predictions_linked

            predictions_linked(db, member, int(uid))
    return ident


def sign_in_with(db: Session, identity: dict[str, Any], purpose: str, current: Member | None,
                 referral_code: str | None = None, browser: str | None = None) -> Member:
    """Finish a verified provider sign-in: link to the signed-in member, sign in the owner, or create a member.
    A new member created from a browser holding an invite code is attributed to the inviter (members/referrals.py)."""
    platform, uid = identity["platform"], str(identity["id"])
    if purpose == "link":
        if current is None:
            raise MemberError("Sign in first, then connect another account.")
        link_identity(db, current, platform, uid, identity.get("username"), identity.get("display_name"))
        db.commit()
        return current
    owner = db.scalar(select(LinkedIdentity).where(LinkedIdentity.platform == platform, LinkedIdentity.platform_user_id == uid))
    if owner:
        member = db.get(Member, owner.member_id)
        assert member is not None
        if member.status == "banned":
            raise MemberError("This Price Chat account is suspended.")
        owner.username = identity.get("username") or owner.username
        db.commit()
        return member
    member = create_member(db)
    link_identity(db, member, platform, uid, identity.get("username"), identity.get("display_name"))
    if referral_code:
        from app.members.referrals import attribute

        attribute(db, member, referral_code, browser=browser, browser_member=current)
    db.commit()
    return member


# --- sessions ---------------------------------------------------------------------------------------------------


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_session(db: Session, member: Member, days: int, prefix: str = "", kind: str | None = None) -> str:
    token = prefix + secrets.token_urlsafe(32)
    db.add(MemberSession(member_id=member.id, token_hash=_hash(token), kind=kind, expires_at=utcnow() + timedelta(days=days), last_seen_at=utcnow()))
    db.commit()
    return token


def member_for_token(db: Session, token: str | None) -> Member | None:
    if not token:
        return None
    s = db.scalar(select(MemberSession).where(MemberSession.token_hash == _hash(token)))
    if s is None or s.expires_at < utcnow():
        return None
    member = db.get(Member, s.member_id)
    if member is None or member.status != "active":
        return None
    if member.review_seat and s.kind not in ("reviewer", "desktop"):
        # a store-review seat accepts only its scoped sessions (app/vpn/reviewer.py): any other one, such as an unscoped
        # browser session from before the restriction, ends on first use
        db.delete(s)
        db.commit()
        return None
    return member


def renew_session(db: Session, token: str, days: int) -> bool:
    """Sliding renewal: a session past half its life gets a fresh expiry (same token). True when renewed."""
    s = db.scalar(select(MemberSession).where(MemberSession.token_hash == _hash(token)))
    now = utcnow()
    if s is None:
        return False
    exp = s.expires_at if s.expires_at.tzinfo else s.expires_at.replace(tzinfo=now.tzinfo)
    s.last_seen_at = now
    renewed = exp - now < timedelta(days=days) / 2
    if renewed:
        s.expires_at = now + timedelta(days=days)
    db.commit()
    return renewed


def end_session(db: Session, token: str | None) -> None:
    if token:
        s = db.scalar(select(MemberSession).where(MemberSession.token_hash == _hash(token)))
        if s:
            db.delete(s)
            db.commit()


# --- website activity -------------------------------------------------------------------------------------------


def daily_visit(db: Session, member: Member) -> None:
    """Once per UTC day: the daily-visit XP and, after 7 days in a row, the weekly streak bonus."""
    now = utcnow()
    member.last_active_at = now
    today = ledger.day_of(now)
    r = ledger.award(db, member, "website.login", f"day:{today}", occurred_at=now, commit=False)
    if r.awarded:
        days = {ledger.day_of(now - timedelta(days=i)) for i in range(7)}
        seen = set(db.scalars(select(ReputationEvent.day).where(
            ReputationEvent.member_id == member.id, ReputationEvent.event_type == "website.login", ReputationEvent.day.in_(days))))
        if days <= seen:
            ledger.award(db, member, "website.streak", f"streak:{today}", occurred_at=now, commit=False)
    db.commit()


def content_view(db: Session, member: Member, external_id: str) -> ledger.AwardResult:
    """Opening a real, public Price Chat video from the website. Once per video; capped per day."""
    from app.models.content_items import ContentItem

    item = db.scalar(select(ContentItem).where(ContentItem.external_id == external_id, ContentItem.website_visible.is_(True),
                                               ContentItem.available.is_(True), ContentItem.privacy_status == "public"))
    if item is None:
        raise MemberError("Unknown content.")
    from app.members import missions
    from app.models.members import Mission

    # The latest video is a mission: opening it from anywhere on the site completes the mission (paid once, there).
    mission = db.scalar(select(Mission).where(Mission.kind == "open_short", Mission.status == "active"))
    if mission is not None and mission.target.get("external_id") == item.external_id:
        r = missions.open_mission(db, member, mission)
        return ledger.AwardResult(r["xp"] > 0, r["xp"], None if r["xp"] else "mission_done")
    missions.record_action(db, member, "open", "youtube" if item.platform.value == "youtube" else item.platform.value,
                           f"content:{item.platform.value}:{item.external_id}")
    title = (item.title or "a Price Chat video")[:120]
    r = ledger.award(db, member, "website.content_view", f"content:{item.platform.value}:{item.external_id}",
                     reason=f"Opened “{title}”", commit=False)
    missions.sync_event_missions(db, member)
    db.commit()
    return r


def update_profile(db: Session, member: Member, *, username: str | None, display_name: str | None,
                   leaderboard_opt_out: bool | None, public_profile: bool | None = None) -> None:
    if username is not None and username != member.username:
        problem = username_problem(db, username, member)
        if problem:
            raise MemberError(problem)
        member.username, member.username_key = username, username.lower()
        ledger.audit(db, member.id, "member", "username_changed", {"username": username})
        if not member.username_chosen:
            member.username_chosen = True
            ledger.award(db, member, "website.profile_completed", "username", commit=False)
    if display_name is not None:  # the Kiwi rank reward: rank-gated, validated, with a rename cooldown
        from app.members import rewards

        try:
            rewards.set_display_name(db, member, display_name)
        except rewards.RewardError as exc:
            raise MemberError(str(exc)) from exc
    if public_profile is not None and public_profile != member.public_profile:
        member.public_profile = public_profile
        ledger.audit(db, member.id, "member", "public_profile", {"public": public_profile})
    if leaderboard_opt_out is not None:
        member.leaderboard_opt_out = leaderboard_opt_out
        ledger.audit(db, member.id, "member", "leaderboard_visibility", {"hidden": leaderboard_opt_out})
    db.commit()


# --- privacy ----------------------------------------------------------------------------------------------------


def disconnect(db: Session, member: Member, platform: str) -> None:
    """Remove a linked account entirely. The last one cannot be removed (it is how the member signs in)."""
    idents = list(db.scalars(select(LinkedIdentity).where(LinkedIdentity.member_id == member.id)))
    target = next((i for i in idents if i.platform == platform), None)
    if target is None:
        raise MemberError("That platform is not connected.")
    if len(idents) == 1 and not member.email:
        raise MemberError("This is your only sign-in method. Add your email first (you'll sign in with a code), or delete your Price Chat account.")
    db.delete(target)
    if platform == "x":
        from app.models.members import MemberXToken

        db.query(MemberXToken).filter(MemberXToken.member_id == member.id).delete()  # the member's X token goes too
    db.flush()
    ledger.audit(db, member.id, "member", "identity_disconnected", {"platform": platform})
    ledger.recalc(db, member)  # e.g. Telegram legacy points stop counting immediately
    from app.members.notify import notify

    notify(db, member.id, "disconnected", f"{PLATFORM_LABEL[platform]} disconnected",
           "Its activity no longer counts towards your XP. XP you already earned stays."
           + (" Your Telegram legacy points are removed while Telegram is disconnected." if platform == "telegram" else ""))
    db.commit()


def delete_member(db: Session, member: Member, actor: str = "member") -> None:
    """Delete the member and everything tied to them (identities, sessions, XP history, badges). Only an audit line
    without identity and the one-time-reward hashes remain."""
    ledger.audit(db, None, actor, "member_deleted", {"member_number": member.id})
    from app.models.members import MemberXToken

    db.query(MemberXToken).filter(MemberXToken.member_id == member.id).delete()
    from app.exchange.ledger import detach_member

    detach_member(db, member.id)  # the paper exchange keeps its ledger whole (an anonymous account)
    db.delete(member)
    db.commit()


# --- read models ------------------------------------------------------------------------------------------------


def rank_out(r: Rank) -> dict[str, Any]:
    return {"level": r.level, "key": r.key, "name": r.name, "min_xp": r.min_xp, "description": r.description, "art": r.art or {}}


def rank_progress(db: Session, member: Member) -> dict[str, Any]:
    all_ranks = ledger.ranks(db)
    current = ledger.rank_for(db, member.rank_score)
    nxt = next((r for r in all_ranks if r.level == current.level + 1), None)
    if nxt is None:
        return {"current": rank_out(current), "next": None, "score": member.rank_score, "xp_to_next": 0, "progress": 1.0}
    span = nxt.min_xp - current.min_xp
    return {"current": rank_out(current), "next": rank_out(nxt), "score": member.rank_score,
            "xp_to_next": max(0, nxt.min_xp - member.rank_score),  # points remaining (rank score, not raw XP)
            "progress": round(min(1.0, max(0.0, (member.rank_score - current.min_xp) / span)), 4) if span else 1.0}


def event_out(e: ReputationEvent) -> dict[str, Any]:
    return {"id": e.id, "event_type": e.event_type, "xp": e.xp, "category": e.category, "source": e.source, "reason": e.reason,
            "occurred_at": e.occurred_at.isoformat(), "reversed": e.reversed_at is not None, "is_reversal": e.reverses_id is not None}


def badges_of(db: Session, member: Member) -> list[dict[str, Any]]:
    rows = db.execute(select(Badge, MemberBadge).join(MemberBadge, MemberBadge.badge_id == Badge.id)
                      .where(MemberBadge.member_id == member.id).order_by(MemberBadge.created_at))
    return [{"key": b.key, "name": b.name, "description": b.description, "awarded_at": mb.created_at.isoformat(),
             "awarded_by": "Price Chat" if mb.awarded_by != "system" else "automatic"} for b, mb in rows]


def entitlements_of(db: Session, member: Member) -> list[dict[str, Any]]:
    manual = set(db.scalars(select(MemberEntitlement.entitlement_id).where(MemberEntitlement.member_id == member.id)))
    out = []
    for e in db.scalars(select(Entitlement).order_by(Entitlement.required_rank)):
        unlocked = e.enabled and ((e.required_rank is not None and member.rank_level >= e.required_rank) or e.id in manual)
        out.append({"key": e.key, "name": e.name, "description": e.description, "required_rank": e.required_rank,
                    "enabled": e.enabled, "unlocked": unlocked})
    return out


def rules_out(db: Session) -> list[dict[str, Any]]:
    return [{"event_type": r.event_type, "label": r.label, "description": r.description, "xp": r.xp, "daily_cap": r.daily_cap,
             "cooldown_seconds": r.cooldown_seconds, "max_per_member": r.max_per_member, "min_member_age_hours": r.min_member_age_hours,
             "enabled": r.enabled, "availability": r.availability, "availability_note": r.availability_note,
             "verification_source": r.verification_source, "category": r.category}
            for r in db.scalars(select(XPRule).order_by(XPRule.id))]


def profile(db: Session, member: Member, recent: int = 30) -> dict[str, Any]:
    ensure_catalog(db)
    idents = {i.platform: i for i in db.scalars(select(LinkedIdentity).where(LinkedIdentity.member_id == member.id))}
    events = db.scalars(select(ReputationEvent).where(ReputationEvent.member_id == member.id)
                        .order_by(ReputationEvent.occurred_at.desc(), ReputationEvent.id.desc()).limit(recent))
    return {
        "member": {"id": member.public_id, "username": member.username, "display_name": member.display_name,
                   "username_chosen": member.username_chosen, "joined_at": member.created_at.isoformat(),
                   "xp": member.xp_total, "bonus_xp": member.bonus_xp, "leaderboard_opt_out": member.leaderboard_opt_out,
                   "xp_suspended": member.xp_suspended, "email": _mask(member.email), "email_verified": bool(member.email)},
        "rank": rank_progress(db, member),
        "linked": [{"platform": p, "label": PLATFORM_LABEL[p], "connected": p in idents,
                    "username": idents[p].username if p in idents else None,
                    "display_name": idents[p].display_name if p in idents else None,
                    "verified_at": idents[p].verified_at.isoformat() if p in idents else None} for p in PLATFORMS],
        "badges": badges_of(db, member),
        "entitlements": entitlements_of(db, member),
        "recent": [event_out(e) for e in events],
    }


def _mask(email: str | None) -> str | None:
    from app.members.email_login import mask

    return mask(email)


def header_out(db: Session, member: Member) -> dict[str, Any]:
    from app.models.members import MemberNotification

    r = ledger.rank_for(db, member.rank_score)
    unread = db.scalar(select(func.count()).select_from(MemberNotification).where(
        MemberNotification.member_id == member.id, MemberNotification.read_at.is_(None))) or 0
    return {"id": member.public_id, "username": member.username, "xp": member.xp_total, "rank": rank_out(r), "unread": int(unread)}


def leaderboard(db: Session, period: str, limit: int = 50) -> list[dict[str, Any]]:
    """Public: username, rank and XP only. Opted-out and banned members never appear. Reputation XP only."""
    ensure_catalog(db)
    since = {"week": utcnow() - timedelta(days=7), "month": utcnow() - timedelta(days=30)}.get(period)
    xp_sum = func.coalesce(func.sum(ReputationEvent.xp), 0).label("xp")
    stmt = (select(Member, xp_sum).join(ReputationEvent, ReputationEvent.member_id == Member.id)
            .where(Member.leaderboard_opt_out.is_(False), Member.status == "active", ReputationEvent.category == "reputation"))
    if since is not None:
        stmt = stmt.where(ReputationEvent.occurred_at >= since)
    rows = db.execute(stmt.group_by(Member.id).having(xp_sum > 0).order_by(xp_sum.desc(), Member.created_at).limit(limit)).all()
    all_ranks = ledger.ranks(db)
    return [{"position": i + 1, "username": m.username, "display_name": m.display_name, "xp": int(x),
             "rank": rank_out(ledger.rank_for(db, m.rank_score, all_ranks))} for i, (m, x) in enumerate(rows)]


def leaderboard_v2(db: Session, period: str, metric: str, limit: int = 50) -> dict[str, Any]:
    """score: Kiwi Rank Score (all time) / XP earned in the period. xp: Price Chat XP. legacy: Telegram legacy points
    (all time). telegram: Telegram XP in the period. Usernames only; opted-out and banned members never appear."""
    ensure_catalog(db)
    since = {"week": utcnow() - timedelta(days=7), "month": utcnow() - timedelta(days=30)}.get(period)
    base = select(Member).where(Member.leaderboard_opt_out.is_(False), Member.status == "active")
    note = ""
    if metric in ("score", "legacy") and since is None:
        col = Member.rank_score if metric == "score" else Member.legacy_points
        rows = [(m, getattr(m, "rank_score" if metric == "score" else "legacy_points"))
                for m in db.scalars(base.where(col > 0).order_by(col.desc(), Member.created_at).limit(limit))]
    else:
        if metric == "legacy":
            note, since = "Telegram legacy is historical, so it has no weekly or monthly view: showing all time.", None
            return leaderboard_v2(db, "all", "legacy", limit) | {"note": note}
        if metric == "score":
            note = "The rank score is all-time; this period ranks by Price Chat XP earned."
        xp_sum = func.coalesce(func.sum(ReputationEvent.xp), 0).label("v")
        stmt = (select(Member, xp_sum).join(ReputationEvent, ReputationEvent.member_id == Member.id)
                .where(Member.leaderboard_opt_out.is_(False), Member.status == "active", ReputationEvent.category == "reputation"))
        if metric == "telegram":
            stmt = stmt.where(ReputationEvent.event_type.like("telegram.%"))
        if since is not None:
            stmt = stmt.where(ReputationEvent.occurred_at >= since)
        rows = [(m, int(v)) for m, v in db.execute(stmt.group_by(Member.id).having(xp_sum > 0).order_by(xp_sum.desc(), Member.created_at).limit(limit))]
    all_ranks = ledger.ranks(db)
    return {"note": note, "rows": [{"position": i + 1, "username": m.username, "value": int(v), "rank_score": m.rank_score,
                                    "xp": m.xp_total, "legacy_points": m.legacy_points,
                                    "rank": rank_out(ledger.rank_for(db, m.rank_score, all_ranks))}
                                   for i, (m, v) in enumerate(rows)]}
