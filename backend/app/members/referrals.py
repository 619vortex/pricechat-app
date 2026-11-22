"""Referrals: a member's invite link, attribution when the invited person creates a Passport, and the two rewards once
that person has done something real.

Flow
  1. Visiting /website/join/<code> stores the code in an httpOnly cookie (pc_ref) and counts a click. Nothing is paid.
  2. When that browser creates a NEW member (any sign-in), a Referral row is written: referrer -> referred, "joined".
     Refused (status "rejected"): unknown / disabled code, a code of a banned member, the referrer's own browser
     session (self-referral). A member can only ever be referred once (unique referred_id).
  3. The referral qualifies on the invited member's first qualifying ledger event AFTER joining (by default: linking
     X or Telegram, or taking part in the Price Chat Telegram; set in the dashboard). Then, through the XP ledger:
     referrer +referral.qualified, invited member +referral.welcome. Both are idempotent ledger rows
     (source_ref "referral:<id>"), and the referrer reward is claimed once per invited EXTERNAL account, so deleting
     an account and joining again through the same friend pays nothing twice.
Limits: rewards per referrer per UTC day (settings.referral_daily_limit; extra qualifications wait for the next day),
the rules' own caps, and a review flag when one referrer's invites keep coming from the same browser.
Identity is always the internal member id, never a username.
"""

import hashlib
import secrets
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.singleton import get_or_create
from app.db.base import utcnow
from app.members import xp as ledger
from app.models.members import LinkedIdentity, Member, ProgramSettings, Referral, ReferralCode, ReputationEvent, XPRule

REFERRER_RULE = "referral.qualified"
WELCOME_RULE = "referral.welcome"
DEFAULT_QUALIFYING = ["identity.telegram_linked", "identity.x_linked", "telegram.first_participation"]
SAME_BROWSER_FLAG_AT = 3  # invites of one referrer from one browser before a person is asked to look
CODE_PREFIX = "kiwi"


def program(db: Session) -> ProgramSettings:
    return get_or_create(db, ProgramSettings, lambda: ProgramSettings(id=1, referral_qualifying_events=list(DEFAULT_QUALIFYING),
                                                                      updates_channel="@Pricechatupdates", leaderboard_periods=["all", "week"]))


def browser_hash(browser: str | None) -> str | None:
    return hashlib.sha256(f"pc-ref|{browser}".encode()).hexdigest() if browser else None


# --- codes ---------------------------------------------------------------------------------------------------------


def code_for(db: Session, member: Member) -> str:
    """The member's invite code, created on first use. Random digits: it says nothing about the member."""
    row = db.scalar(select(ReferralCode).where(ReferralCode.member_id == member.id))
    if row is not None:
        return row.code
    for _ in range(20):
        code = f"{CODE_PREFIX}{secrets.randbelow(900000) + 100000}"
        if db.scalar(select(ReferralCode.id).where(ReferralCode.code == code)) is None:
            row = ReferralCode(member_id=member.id, code=code)
            db.add(row)
            try:
                db.flush()
            except IntegrityError:
                db.rollback()
                continue
            db.commit()
            return code
    raise RuntimeError("could not create a referral code")


def normalise(code: str | None) -> str | None:
    c = (code or "").strip().lower()
    return c if c.isalnum() and 4 <= len(c) <= 24 else None


def owner_of(db: Session, code: str | None) -> Member | None:
    c = normalise(code)
    if not c:
        return None
    row = db.scalar(select(ReferralCode).where(ReferralCode.code == c))
    if row is None:
        return None
    member = db.get(Member, row.member_id)
    return member if member is not None and member.status == "active" else None


def record_visit(db: Session, code: str | None) -> bool:
    """Someone opened an invite link. Counted only; never XP."""
    c = normalise(code)
    row = db.scalar(select(ReferralCode).where(ReferralCode.code == c)) if c else None
    if row is None or not program(db).referrals_enabled:
        return False
    row.clicks += 1
    db.commit()
    return True


# --- attribution ---------------------------------------------------------------------------------------------------


def attribute(db: Session, new_member: Member, code: str | None, *, browser: str | None, browser_member: Member | None) -> Referral | None:
    """Record who invited a member who has just been CREATED. Never pays anything by itself."""
    c = normalise(code)
    if not c or not program(db).referrals_enabled:
        return None
    if db.scalar(select(Referral.id).where(Referral.referred_id == new_member.id)):
        return None  # referred once, ever
    referrer = owner_of(db, c)
    reason = ("unknown_code" if referrer is None else "self_referral" if referrer.id == new_member.id
              or (browser_member is not None and browser_member.id == referrer.id) else None)
    bh = browser_hash(browser)
    ref = Referral(referrer_id=referrer.id if referrer else None, referred_id=new_member.id, code=c,
                   status="rejected" if reason else "joined", reason=reason, browser_hash=bh)
    db.add(ref)
    db.flush()
    ledger.audit(db, new_member.id, "system", "referral_" + ("rejected" if reason else "joined"),
                 {"referral_id": ref.id, "referrer_member_id": ref.referrer_id, "reason": reason})
    if referrer is not None and not reason and bh:
        same = db.scalar(select(func.count()).select_from(Referral).where(Referral.referrer_id == referrer.id, Referral.browser_hash == bh)) or 0
        if same >= SAME_BROWSER_FLAG_AT:
            ledger.raise_flag(db, referrer, "referral_pattern", f"{same} invited members joined from the same browser",
                              {"referral_id": ref.id, "count": same})
    return ref


# --- qualification -------------------------------------------------------------------------------------------------


def on_award(db: Session, member: Member, event: ReputationEvent, no_referral: set[int] | None = None) -> None:
    """Called by the XP ledger after every award: the invited member's first qualifying action qualifies the referral.
    `no_referral` (the ledger's batch) remembers members found without a pending referral, so it is looked up once."""
    if event.event_type.startswith("referral.") or (no_referral is not None and member.id in no_referral):
        return
    ref = db.scalar(select(Referral).where(Referral.referred_id == member.id, Referral.status == "joined"))
    if ref is None and no_referral is not None:
        no_referral.add(member.id)
    if ref is None or event.event_type not in (program(db).referral_qualifying_events or []):
        return
    qualify(db, ref, event.event_type)


def qualify(db: Session, ref: Referral, event_type: str) -> bool:
    """Pay both rewards for one referral, at most once. Returns True when the referral is now qualified."""
    if ref.status != "joined" or ref.referred_id is None:
        return False
    settings = program(db)
    referred = db.get(Member, ref.referred_id)
    referrer = db.get(Member, ref.referrer_id) if ref.referrer_id else None
    if referred is None or referred.status != "active":
        return False
    if settings.referral_min_age_hours and utcnow() - referred.created_at < timedelta(hours=settings.referral_min_age_hours):
        return False  # too new: the sweep tries again later
    if referrer is None or referrer.status != "active":
        ref.status, ref.reason = "rejected", "referrer_gone"
        db.flush()
        return False
    today = ledger.day_of(utcnow())
    paid_today = db.scalar(select(func.count()).select_from(ReputationEvent).where(
        ReputationEvent.member_id == referrer.id, ReputationEvent.event_type == REFERRER_RULE, ReputationEvent.day == today,
        ReputationEvent.reverses_id.is_(None))) or 0
    if paid_today >= settings.referral_daily_limit:
        return False  # waits for tomorrow's sweep
    # one reward per invited EXTERNAL account, ever (survives deleting and re-creating the member)
    ident = db.scalar(select(LinkedIdentity).where(LinkedIdentity.member_id == referred.id).order_by(LinkedIdentity.id))
    claim = f"{ident.platform}:{ident.platform_user_id}" if ident else f"member:{referred.id}"
    r1 = ledger.award(db, referrer, REFERRER_RULE, f"referral:{ref.id}", reason="A friend you invited joined Price Chat",
                      claim_key=claim, commit=False)
    if not r1.awarded and r1.refused not in ("rule_off", "zero"):
        if r1.refused in ("already_claimed", "duplicate", "max_reached"):
            ref.status, ref.reason = "rejected", r1.refused  # this person was already counted once
        db.flush()
        return False  # the welcome bonus waits too: it is paid only together with a referral that qualifies
    r2 = ledger.award(db, referred, WELCOME_RULE, f"referral:{ref.id}", reason="Welcome bonus: joined through a friend's invite",
                      commit=False)
    ref.status, ref.qualified_at, ref.qualifying_event_type = "qualified", utcnow(), event_type
    ref.referrer_xp, ref.referrer_event_id = r1.xp, r1.event.id if r1.event else None
    ref.welcome_xp, ref.welcome_event_id = r2.xp, r2.event.id if r2.event else None
    ledger.audit(db, referred.id, "system", "referral_qualified",
                 {"referral_id": ref.id, "referrer_member_id": referrer.id, "event_type": event_type, "referrer_xp": r1.xp, "welcome_xp": r2.xp})
    db.flush()
    return True


def sweep(db: Session) -> int:
    """Retry referrals that waited (daily limit, minimum age). Uses the invited member's ledger as it is."""
    settings = program(db)
    done = 0
    qualifying = (ReputationEvent.member_id == Referral.referred_id,
                  ReputationEvent.event_type.in_(settings.referral_qualifying_events or []),
                  ReputationEvent.created_at >= Referral.created_at, ReputationEvent.reverses_id.is_(None),
                  ReputationEvent.reversed_at.is_(None))
    # only referrals whose friend did a qualifying action: the ones that never did cost nothing, however many pile up
    waiting = select(Referral).where(Referral.status == "joined", Referral.referred_id.is_not(None),
                                     select(ReputationEvent.id).where(*qualifying).exists())
    for ref in list(db.scalars(waiting)):
        ev = db.scalar(select(ReputationEvent).where(
            ReputationEvent.member_id == ref.referred_id, ReputationEvent.event_type.in_(settings.referral_qualifying_events or []),
            ReputationEvent.created_at >= ref.created_at, ReputationEvent.reverses_id.is_(None), ReputationEvent.reversed_at.is_(None))
            .order_by(ReputationEvent.id))
        if ev is not None and qualify(db, ref, ev.event_type):
            done += 1
    db.commit()
    return done


# --- read models ---------------------------------------------------------------------------------------------------


def stats(db: Session, member: Member) -> dict[str, Any]:
    rows = list(db.scalars(select(Referral).where(Referral.referrer_id == member.id)))
    rules = {r.event_type: r for r in (ledger.get_rule(db, REFERRER_RULE), ledger.get_rule(db, WELCOME_RULE))}
    return {
        "joined": sum(1 for r in rows if r.status in ("joined", "qualified")),
        "qualified": sum(1 for r in rows if r.status == "qualified"),
        "xp_earned": sum(r.referrer_xp for r in rows if r.status == "qualified"),
        "reward_xp": rules[REFERRER_RULE].xp if rules[REFERRER_RULE].enabled else 0,
        "welcome_xp": rules[WELCOME_RULE].xp if rules[WELCOME_RULE].enabled else 0,
        "enabled": program(db).referrals_enabled,
        # what the invited friend has to do (labels of the qualifying rules, as set in the dashboard)
        "qualifying": [r.label for e in (program(db).referral_qualifying_events or [])
                       if (r := db.scalar(select(XPRule).where(XPRule.event_type == e))) is not None],
    }


def invite_link(public_url: str, code: str) -> str:
    return f"{public_url.rstrip('/')}/website/join/{code}"
