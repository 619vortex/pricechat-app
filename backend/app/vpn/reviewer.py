"""One store-review account for the Kiwi VPN browser extension. Off unless all three VPN_REVIEWER_* variables are set.

Store reviewers need a plain email and password, and need the VPN to work while it's closed to everyone else. This account gets
exactly that: until VPN_REVIEWER_UNTIL (UTC), no payment needed, one browser login, one server, no WireGuard devices. It has no Admin
rights, and Admin's block still wins. Only a scrypt hash of the password is on the server. Remove the variables to end it sooner.

The rights belong to one stored member (vpn_settings.reviewer_member_id, security audit L9), bound on the first password
sign-in: never to whoever holds the address, so no other way of setting a member's email can make someone the reviewer.
Its free trial is marked used (and its email remembered as having had one), so the account is worth nothing once the
variables are removed.
"""

import base64
import hashlib
import hmac
import secrets
from datetime import date, datetime, time, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.settings import Settings, get_settings
from app.db.base import utcnow
from app.members import xp as ledger
from app.members.service import MemberError, create_member
from app.models.members import Member, MemberAuditLog
from app.models.vpn import VpnSettings

SERVER = "Helsinki 1"
FAIL = "That email or password isn't right, or this account has ended."
_N, _R, _P = 2**14, 8, 1


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    raw = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P)
    return f"scrypt${base64.b64encode(salt).decode()}${base64.b64encode(raw).decode()}"


def check_password(stored: str, password: str) -> bool:
    try:
        kind, salt, want = stored.split("$")
        if kind != "scrypt":
            return False
        raw = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=_N, r=_R, p=_P)
        return hmac.compare_digest(raw, base64.b64decode(want))
    except (ValueError, TypeError):
        return False


def _email(s: Settings) -> str:
    return (s.vpn_reviewer_email or "").strip().lower()


def _stored(s: Settings) -> str:
    return s.vpn_reviewer_password_hash.get_secret_value() if s.vpn_reviewer_password_hash else ""


def _ends(s: Settings) -> datetime | None:
    """The end of the last day (UTC). None if unset or not a date: the account stays off."""
    try:
        return datetime.combine(date.fromisoformat((s.vpn_reviewer_until or "").strip()), time.max, tzinfo=timezone.utc)
    except ValueError:
        return None


def configured(s: Settings | None = None) -> bool:
    s = s or get_settings()
    return bool(_email(s)) and bool(_stored(s)) and _ends(s) is not None


def bound_id(db: Session) -> int | None:
    row = db.get(VpnSettings, 1)
    return row.reviewer_member_id if row is not None else None


def is_reviewer(db: Session, member: Member | None, s: Settings | None = None) -> bool:
    """The configured review account: the member bound on its first sign-in, whatever its email says now."""
    if member is None or not configured(s):
        return False
    bound = bound_id(db)
    return bound is not None and member.id == bound


def in_time(now: datetime | None = None, s: Settings | None = None) -> bool:
    ends = _ends(s or get_settings())
    return ends is not None and (now or utcnow()) <= ends


def sign_in(db: Session, s: Settings, email: str, password: str) -> Member:
    """The review account's member, created on its first sign-in. One answer for every failure."""
    addr = (email or "").strip().lower()
    good = check_password(_stored(s), password) if _stored(s) else False  # the hash is checked first, so a wrong address costs the same
    if not good or not addr or addr != _email(s) or not in_time(s=s):
        raise MemberError(FAIL)
    bound = bound_id(db)
    member = db.get(Member, bound) if bound is not None else None
    if member is None:
        member = db.scalar(select(Member).where(Member.email == addr))
        if member is not None and not _made_here(db, member):
            # someone else's Passport holds the review address: never hand it the rights (and never say why)
            raise MemberError(FAIL)
        if member is None:
            member = create_member(db)
            member.email, member.leaderboard_opt_out = addr, True  # never on the public leaderboard
            ledger.audit(db, member.id, "member", "vpn_reviewer_created", {})
    if member.status != "active":
        raise MemberError(FAIL)
    _bind(db, member)  # idempotent: also uses up the trial of a review account bound by migration 0113
    db.commit()
    return member


def _made_here(db: Session, member: Member) -> bool:
    """The member was created by this sign-in (before members were bound by id), not a person's own Passport."""
    return db.scalar(select(MemberAuditLog.id).where(MemberAuditLog.member_id == member.id, MemberAuditLog.action == "vpn_reviewer_created").limit(1)) is not None


def _bind(db: Session, member: Member) -> None:
    """Store the member as the review account and use up its free trial (and its email's)."""
    from app.vpn import service, trial_guard

    service.settings(db).reviewer_member_id = member.id
    acct = service.account_of(db, member)
    if acct.trial_started_at is None:
        now = utcnow()
        acct.trial_started_at = acct.trial_ends_at = now  # used, and already over
    trial_guard.mark(db, member)
    db.flush()
