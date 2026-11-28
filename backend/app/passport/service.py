"""Kiwi Passport 2.0: the feature flag, the badge collection (families, tiers, numbered Founding Kiwi, rarity), the member's public
identity (title, bio, featured badges, sections, invite link, the one-time invitation) and the community reputation score.

Reads the existing ledger, badges, streaks, linked accounts, referral codes and Predictions. Writes only the member's own identity
choices and badge rows; never an XP balance, a rank or a reward (app/passport/rules.py).
"""

from __future__ import annotations

import re
import time
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config.settings import get_settings
from app.db.base import utcnow
from app.members import xp as ledger
from app.models.members import (Badge, LinkedIdentity, Member, MemberBadge, MemberStreak, ModerationFlag, ReputationEvent)
from app.passport import rules

_rarity_cache: tuple[float, dict[int, int], int] | None = None
RARITY_TTL = 600.0


class PassportError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


# --- the flag ---------------------------------------------------------------------------------------------------------------------


def mode(settings: Any = None) -> str:
    return str(getattr(settings or get_settings(), "passport2_mode", "off") or "off")


def preview_members(settings: Any = None) -> set[str]:
    raw = str(getattr(settings or get_settings(), "passport2_preview_members", "") or "")
    return {u.strip().lower().lstrip("@") for u in raw.split(",") if u.strip()}


def visible(member: Member | None, settings: Any = None) -> bool:
    """Whether Passport 2.0 exists for this member (their editor, their public page in the new layout, their /passport card)."""
    m = mode(settings)
    if m == "live":
        return member is not None
    if m == "preview" and member is not None:
        return (member.username or "").lower() in preview_members(settings)
    return False


def may_earn_new_badges(db: Session, member: Member) -> bool:
    return member.status == "active" and not member.review_seat and visible(member)


def _aware(d: datetime | None) -> datetime | None:
    from datetime import timezone

    return d if d is None or d.tzinfo else d.replace(tzinfo=timezone.utc)


# --- the collection -----------------------------------------------------------------------------------------------------------------


def ensure_catalog(db: Session) -> None:
    """Add the new badges and put the existing ones in their collection. Existing names, rules, holders and dashboard edits are kept:
    a family / tier / title is only filled in where it is still empty."""
    changed = False
    have = {b.key: b for b in db.scalars(select(Badge))}
    for key, meta in rules.EXISTING.items():
        b = have.get(key)
        if b is not None and b.family is None:
            b.family, b.tier, b.title, b.sort = meta["family"], meta["tier"], meta["title"], meta["sort"]
            changed = True
    for spec in rules.NEW:
        if spec["key"] not in have:
            db.add(Badge(**spec))
            changed = True
    from app.community_intel import rules as ci_rules
    from app.community_intel import service as ci

    if ci.mode() != "off":  # the Researcher family exists only once Community Intelligence is on
        for spec in ci_rules.BADGES:
            if spec["key"] not in have:
                db.add(Badge(**spec))
                changed = True
    if changed:
        db.commit()


def _eligible():
    return (Member.status == "active", Member.review_seat.is_(False))


def eligible_count(db: Session) -> int:
    return int(db.scalar(select(func.count()).select_from(Member).where(*_eligible())) or 0)


def award_founding(db: Session, now: datetime | None = None, only: list[Member] | None = None) -> list[tuple[int, int]]:
    """Number Founding Kiwis in join order: the first FOUNDING_CAP eligible members, until FOUNDING_UNTIL, whichever comes first.
    Numbers are never reused or moved. In preview only the preview members get theirs. Returns [(member id, number)]."""
    now = now or utcnow()
    if now >= rules.FOUNDING_UNTIL:
        return []
    badge = db.scalar(select(Badge).where(Badge.key == rules.FOUNDING_KEY))
    if badge is None or not badge.enabled:
        return []
    out: list[tuple[int, int]] = []
    holders = set(db.scalars(select(MemberBadge.member_id).where(MemberBadge.badge_id == badge.id)))
    top = int(db.scalar(select(func.max(MemberBadge.serial)).where(MemberBadge.badge_id == badge.id)) or 0)
    q = select(Member).where(*_eligible()).order_by(Member.created_at, Member.id)
    for m in (only if only is not None else db.scalars(q)):
        if top >= rules.FOUNDING_CAP:
            break
        if m.id in holders or not may_earn_new_badges(db, m):
            continue
        top += 1
        db.add(MemberBadge(member_id=m.id, badge_id=badge.id, awarded_by="system", serial=top,
                           reason=f"Founding Kiwi No. {top:03d} of {rules.FOUNDING_CAP}"))
        try:
            db.flush()
        except IntegrityError:  # another process numbered someone at the same moment: stop, the next run carries on
            db.rollback()
            break
        from app.members.notify import notify

        notify(db, m.id, "badge", f"Badge earned: Founding Kiwi No. {top:03d}", badge.description, {"badge": badge.key, "serial": top})
        out.append((m.id, top))
    if out:
        db.commit()
        invalidate_rarity()
    return out


def evaluate(db: Session, members: list[Member]) -> int:
    """Give the auto badges (old and new) the members now meet. Never removes one."""
    from app.members.badges import evaluate_auto_badges

    n = 0
    for m in members:
        n += len(evaluate_auto_badges(db, m))
    if n:
        db.commit()
        invalidate_rarity()
    return n


def invalidate_rarity() -> None:
    global _rarity_cache
    _rarity_cache = None


def holders(db: Session) -> tuple[dict[int, int], int]:
    """{badge id: holders among eligible members}, eligible members. Cached for RARITY_TTL."""
    global _rarity_cache
    if _rarity_cache is not None and time.monotonic() - _rarity_cache[0] < RARITY_TTL:
        return _rarity_cache[1], _rarity_cache[2]
    rows = db.execute(select(MemberBadge.badge_id, func.count()).join(Member, Member.id == MemberBadge.member_id)
                      .where(*_eligible()).group_by(MemberBadge.badge_id)).all()
    counts = {int(b): int(n) for b, n in rows}
    total = eligible_count(db)
    _rarity_cache = (time.monotonic(), counts, total)
    return counts, total


def _badge_out(b: Badge, mb: MemberBadge | None, counts: dict[int, int], total: int) -> dict[str, Any]:
    fam = rules.FAMILIES.get(b.family or "", rules.FAMILIES["honours"])
    out = {"key": b.key, "name": b.name, "description": b.description, "family": b.family or "honours", "family_name": fam["name"],
           "color": fam["color"], "tier": b.tier or "bronze", "title": b.title, "earned": mb is not None,
           "awarded_at": mb.created_at.isoformat() if mb is not None else None, "serial": mb.serial if mb is not None else None,
           "featured_slot": mb.featured_slot if mb is not None else None, "rarity": rules.rarity(counts.get(b.id, 0), total)}
    if b.key == rules.FOUNDING_KEY:
        out["limit"] = {"cap": rules.FOUNDING_CAP, "until": rules.FOUNDING_UNTIL.isoformat(), "claimed": counts.get(b.id, 0)}
        if mb is not None and mb.serial:
            out["name"] = f"Founding Kiwi No. {mb.serial:03d}"
    return out


def _sort_key(b: Badge) -> tuple[int, int, str]:
    return (rules.FAMILIES.get(b.family or "", {"sort": 99})["sort"], b.sort, b.key)


def collection(db: Session, member: Member) -> list[dict[str, Any]]:
    """Every enabled badge with the member's copy (or locked), in collection order."""
    counts, total = holders(db)
    mine = {mb.badge_id: mb for mb in db.scalars(select(MemberBadge).where(MemberBadge.member_id == member.id))}
    from app.community_intel import service as ci

    research_on = ci.mode() != "off"
    out = []
    for b in sorted(db.scalars(select(Badge).where(Badge.enabled.is_(True))), key=_sort_key):
        mb = mine.get(b.id)
        if b.kind == "manual" and mb is None:  # staff honours are shown only to those who have them
            continue
        if b.family == "researcher" and mb is None and not research_on:  # Community Intelligence is off
            continue
        out.append(_badge_out(b, mb, counts, total))
    return out


def earned(db: Session, member: Member) -> list[dict[str, Any]]:
    return [b for b in collection(db, member) if b["earned"]]


# --- predictor summary (read only) ----------------------------------------------------------------------------------------------------


def predictor_summary(db: Session, member: Member) -> dict[str, Any]:
    from app.predictions import service as pred

    pairs = pred._settled_votes(db, f"m:{member.id}")
    decided = [(v, q) for v, q in pairs if q.outcome != "tie"]
    right = sum(1 for v, q in decided if v.option == q.outcome)
    cur, best, _ = pred.right_streak(pairs)
    last = [None if q.outcome == "tie" else v.option == q.outcome for v, q in pairs[-7:]]
    return {"calls": len(pairs), "decided": len(decided), "right": right, "streak": cur, "best_streak": best, "last": last,
            "accuracy": round(right / len(decided), 4) if len(decided) >= 10 else None}


# --- community reputation score -----------------------------------------------------------------------------------------------------


def community_score(db: Session, member: Member, now: datetime | None = None) -> dict[str, Any]:
    """Genuine participation over the last WINDOW_DAYS (rules.SCORE). Computed from the ledger and Predictions; stores nothing and
    changes no XP, rank or reward."""
    now = now or utcnow()
    since = now - timedelta(days=rules.WINDOW_DAYS)
    events = {s["event"]: s for s in rules.SCORE if s["event"]}
    rows = db.execute(select(ReputationEvent.event_type, ReputationEvent.day, func.count()).where(
        ReputationEvent.member_id == member.id, ReputationEvent.event_type.in_(list(events)), ReputationEvent.occurred_at >= since,
        ReputationEvent.reversed_at.is_(None), ReputationEvent.reverses_id.is_(None), ReputationEvent.xp >= 0)
        .group_by(ReputationEvent.event_type, ReputationEvent.day)).all()
    per: dict[str, dict[str, int]] = defaultdict(lambda: {"count": 0, "points": 0})
    for et, _day, n in rows:
        s = events[et]
        kept = min(int(n), s["day_cap"]) if s["day_cap"] else int(n)
        per[s["key"]]["count"] += kept
        per[s["key"]]["points"] += kept * s["points"]
    days = _prediction_days(db, member, since)
    s = next(x for x in rules.SCORE if x["key"] == "predictions")
    per["predictions"] = {"count": days, "points": days * s["points"]}
    from app.community_intel import service as ci

    for part in ci.score_parts(db, member, since):  # Community Intelligence: accepted tips, published stories, upheld spam
        per[part["key"]] = {"count": part["count"], "points": part["points"]}
    flags = int(db.scalar(select(func.count()).select_from(ModerationFlag).where(
        ModerationFlag.member_id == member.id, ModerationFlag.status == "resolved", ModerationFlag.created_at >= since)) or 0)
    gross = sum(v["points"] for v in per.values())
    score = max(0, gross - flags * rules.FLAG_PENALTY)
    parts = [{"key": x["key"], "label": x["label"], "count": per[x["key"]]["count"], "points": per[x["key"]]["points"]} for x in rules.SCORE]
    parts += [{"key": p["key"], "label": p["label"], "count": p["count"], "points": p["points"]} for p in ci.score_parts(db, member, since)
              if p["key"] != "tips_penalty" or p["count"]]
    return {"score": score, "level": rules.level_for(score), "window_days": rules.WINDOW_DAYS, "parts": parts,
            "penalty": flags * rules.FLAG_PENALTY}


def _prediction_days(db: Session, member: Member, since: datetime) -> int:
    from app.models.predictions import PredVote

    return int(db.scalar(select(func.count(func.distinct(func.date(PredVote.voted_at)))).where(
        PredVote.member_id == member.id, PredVote.status == "counted", PredVote.voted_at >= since)) or 0)


# --- the public identity ------------------------------------------------------------------------------------------------------------


def sections(member: Member) -> dict[str, bool]:
    out = dict(rules.DEFAULT_SECTIONS)
    for k, v in (member.profile_sections or {}).items():
        if k in out:
            out[k] = bool(v)
    return out


def invite_path(db: Session, member: Member) -> str:
    from app.members import referrals

    return f"/join/{referrals.code_for(db, member)}"


def public_v2(db: Session, member: Member, now: datetime | None = None, everything: bool = False) -> dict[str, Any]:
    """The Passport 2.0 part of the public page: only what the member chose to show (sections). Never a handle, an email, a wallet
    address, an XP history or an id. everything=True (the member's own editor only) fills every section, so the live preview can
    switch one on without a round trip; "sections" still says what the public sees."""
    from app.members import rewards

    now = now or utcnow()
    chosen = sections(member)
    sec = {k: True for k in chosen} if everything else chosen
    mine = earned(db, member)
    featured = sorted((b for b in mine if b["featured_slot"]), key=lambda b: b["featured_slot"])
    title = next((b["title"] for b in mine if b["key"] == member.title_badge and b["title"]), None)
    if title and member.title_badge == rules.FOUNDING_KEY:
        serial = next((b["serial"] for b in mine if b["key"] == rules.FOUNDING_KEY), None)
        title = f"Founding Kiwi No. {serial:03d}" if serial else title
    out: dict[str, Any] = {"v": 2, "display_name": rewards.custom_display_name(member), "accent": member.passport_accent or "green",
                           "title": title, "bio": member.bio or None, "featured": featured, "badge_count": len(mine), "sections": chosen,
                           "card": f"/api/members/public/{member.username}/card.png"}
    if sec["badges"]:
        out["collection"] = mine
    from app.community_intel import service as ci

    if sec.get("research") and ci.visible(member):
        rec = ci.record(db, member)
        if rec["sent"]:
            out["research"] = {k: rec[k] for k in ("sent", "accepted", "published", "first_spotted", "acceptance", "points")}
    if sec["community"]:
        cs = community_score(db, member, now)
        out["community"] = {"score": cs["score"], "level": cs["level"]["name"]}
    if sec["predictions"] and get_settings().predictions_mode == "live":
        st = predictor_summary(db, member)
        if st["calls"]:
            out["predictions"] = st
    if sec["streak"]:
        row = db.scalar(select(MemberStreak).where(MemberStreak.member_id == member.id))
        out["streak"] = {"current": row.current if row else 0, "best": row.best if row else 0}
    if sec["platforms"]:
        have = set(db.scalars(select(LinkedIdentity.platform).where(LinkedIdentity.member_id == member.id)))
        if db.scalar(select(ReputationEvent.id).where(ReputationEvent.member_id == member.id, ReputationEvent.event_type == "wallet.verified",
                                                      ReputationEvent.reversed_at.is_(None)).limit(1)):
            have.add("wallet")
        out["platforms"] = [p for p in (*rules.PLATFORMS, "wallet") if p in have]
    if member.profile_ref_link or everything:
        out["invite"] = invite_path(db, member)
    return out


_LINK = re.compile(r"(https?://|www\.|t\.me/|\b[\w-]+\.(com|io|xyz|net|org|gg|me|app|live|co)\b|@\w{3,})", re.I)
_BIO_STAFF = {"admin", "administrator", "moderator", "mod", "staff", "official", "support", "helpdesk", "owner", "ceo"}


def bio_problem(text: str) -> str | None:
    from app.members import rewards

    if len(text) > rules.BIO_MAX:
        return f"Keep it to {rules.BIO_MAX} characters."
    if _LINK.search(text):
        return "Links and @handles can't go in the bio."
    if set(re.findall(r"[a-z0-9]+", rewards._skeleton(text))) & _BIO_STAFF:
        return "The bio can't suggest you're Price Chat staff."
    return None


def identity(db: Session, member: Member) -> dict[str, Any]:
    """The editor: everything the member can choose, what they chose, the collection, the score and a live preview."""
    from app.members import rewards
    from app.members.service import rank_out

    mine = earned(db, member)
    rank = ledger.rank_for(db, member.rank_score)
    preview = {"username": member.username, "joined_at": member.created_at.isoformat(), "rank": rank_out(rank),
               **public_v2(db, member, everything=True)}
    from app.community_intel import service as ci

    return {"public_profile": member.public_profile, "invited": member.profile_invited_at is not None, "research_on": ci.visible(member),
            "title_badge": member.title_badge, "bio": member.bio or "", "accent": member.passport_accent or "green",
            "accents": list(rewards.ACCENTS), "accent_unlocked": rewards.unlocked(db, member, "kiwi"),
            "featured": [b["key"] for b in sorted((b for b in mine if b["featured_slot"]), key=lambda b: b["featured_slot"])],
            "sections": sections(member), "ref_link": member.profile_ref_link,
            "titles": [{"badge": b["key"], "title": (b["name"] if b["key"] == rules.FOUNDING_KEY else b["title"])} for b in mine if b["title"]],
            "collection": collection(db, member), "community": community_score(db, member), "preview": preview,
            "share_url": f"/u/{member.username}", "invite": invite_path(db, member) if member.profile_ref_link else None,
            "rules": {"bio_max": rules.BIO_MAX, "featured_slots": rules.FEATURED_SLOTS, "score_window_days": rules.WINDOW_DAYS,
                      "score": [{"label": s["label"], "points": s["points"], "day_cap": s["day_cap"]} for s in rules.SCORE],
                      "levels": [{"at": a, "name": n} for a, n in rules.LEVELS]}}


def update_identity(db: Session, member: Member, *, public_profile: bool | None = None, title_badge: str | None = None,
                    clear_title: bool = False, bio: str | None = None, accent: str | None = None, featured: list[str] | None = None,
                    sections_in: dict[str, bool] | None = None, ref_link: bool | None = None) -> None:
    from app.members import rewards

    mine = {b["key"]: b for b in earned(db, member)}
    if clear_title:
        member.title_badge = None
    elif title_badge is not None:
        if title_badge not in mine or not mine[title_badge]["title"]:
            raise PassportError("Pick a title from a badge you've earned.")
        member.title_badge = title_badge
    if bio is not None:
        text = rewards.clean_display_name(bio)
        problem = bio_problem(text)
        if problem:
            raise PassportError(problem)
        member.bio = text or None
    if accent is not None and accent != (member.passport_accent or "green"):
        try:
            rewards.set_accent(db, member, accent)
        except rewards.RewardError as exc:
            raise PassportError(str(exc), getattr(exc, "status", 400)) from exc
    if featured is not None:
        keys = list(dict.fromkeys(featured))
        if len(keys) > rules.FEATURED_SLOTS:
            raise PassportError(f"Feature up to {rules.FEATURED_SLOTS} badges.")
        if any(k not in mine for k in keys):
            raise PassportError("You can only feature badges you've earned.")
        rows = {b.key: mb for b, mb in db.execute(select(Badge, MemberBadge).join(MemberBadge, MemberBadge.badge_id == Badge.id)
                                                   .where(MemberBadge.member_id == member.id))}
        for mb in rows.values():
            mb.featured_slot = None
        db.flush()
        for i, k in enumerate(keys, start=1):
            rows[k].featured_slot = i
    if sections_in is not None:
        cur = sections(member)
        cur.update({k: bool(v) for k, v in sections_in.items() if k in cur})
        member.profile_sections = cur
    if ref_link is not None:
        member.profile_ref_link = bool(ref_link)
    if public_profile is not None:
        member.public_profile = bool(public_profile)
        member.profile_invited_at = member.profile_invited_at or utcnow()
    ledger.audit(db, member.id, "member", "passport_identity_changed",
                 {k: v for k, v in {"public": public_profile, "title": title_badge, "featured": featured, "ref_link": ref_link}.items() if v is not None})
    db.commit()


def answer_invite(db: Session, member: Member, accept: bool) -> None:
    """The one-time invitation: shown until it is answered either way, never again after."""
    member.profile_invited_at = member.profile_invited_at or utcnow()
    if accept:
        member.public_profile = True
    ledger.audit(db, member.id, "member", "passport_invite_answered", {"accept": accept})
    db.commit()


# --- the periodic job ----------------------------------------------------------------------------------------------------------------


def tick(db: Session, now: datetime | None = None) -> dict[str, Any]:
    """passport.badges (every 15 minutes while PASSPORT2_MODE isn't off): the collection, Founding Kiwi numbers, and the auto badges
    of members active in the last 3 days (everyone on the first run of a day)."""
    now = now or utcnow()
    if mode() == "off":
        return {"mode": "off"}
    ensure_catalog(db)
    only = None
    if mode() == "preview":
        names = preview_members()
        only = list(db.scalars(select(Member).where(func.lower(Member.username).in_(names)).order_by(Member.created_at, Member.id))) if names else []
    founding = award_founding(db, now, only)
    if only is not None:
        members = only
    elif now.hour == 0 and now.minute < 15:
        members = list(db.scalars(select(Member).where(*_eligible())))
    else:
        members = list(db.scalars(select(Member).where(*_eligible(), Member.last_active_at >= now - timedelta(days=3))))
    return {"mode": mode(), "founding": len(founding), "badges": evaluate(db, members), "checked": len(members)}
