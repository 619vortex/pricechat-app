"""One store-review account for the Kiwi VPN browser extension. Off unless all three VPN_REVIEWER_* variables are set.

Store reviewers need a plain email and password, and need the VPN to work while it's closed to everyone else. This account gets
exactly that: until VPN_REVIEWER_UNTIL (UTC), no payment needed, one browser login, one server, no WireGuard devices. It has no Admin
rights, and Admin's block still wins. Only a scrypt hash of the password is on the server. Remove the variables to end it sooner.
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
from app.models.members import Member

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


def is_reviewer(member: Member | None, s: Settings | None = None) -> bool:
    s = s or get_settings()
    return member is not None and bool(_email(s)) and bool(_stored(s)) and _ends(s) is not None and (member.email or "").lower() == _email(s)


def in_time(now: datetime | None = None, s: Settings | None = None) -> bool:
    ends = _ends(s or get_settings())
    return ends is not None and (now or utcnow()) <= ends


def sign_in(db: Session, s: Settings, email: str, password: str) -> Member:
    """The review account's member, created on its first sign-in. One answer for every failure."""
    addr = (email or "").strip().lower()
    good = check_password(_stored(s), password) if _stored(s) else False  # the hash is checked first, so a wrong address costs the same
    if not good or not addr or addr != _email(s) or not in_time(s=s):
        raise MemberError(FAIL)
    member = db.scalar(select(Member).where(Member.email == addr))
    if member is None:
        member = create_member(db)
        member.email, member.leaderboard_opt_out = addr, True  # never on the public leaderboard
        ledger.audit(db, member.id, "member", "vpn_reviewer_created", {})
        db.commit()
    if member.status != "active":
        raise MemberError(FAIL)
    return member
