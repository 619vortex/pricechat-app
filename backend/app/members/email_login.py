"""Passwordless email sign-in: an email address, a 6-digit code sent to it, nothing else.

  start(email)  -> sends a code (or, when limits are hit, says how long to wait). The answer never says whether the
                   address already has an account (no account enumeration).
  verify(code)  -> the member owning that VERIFIED address (returning member), or a new member with that address
                   (they choose a username next), or, purpose "link", the address added to the signed-in member.

Security:
  - 6 digits from `secrets`, valid EMAIL_CODE_MINUTES (10), single use; a new code replaces the previous one.
  - Only keyed hashes are stored (HMAC with a key derived from APP_ENCRYPTION_KEY): address, code, browser, IP.
  - The code works only in the browser that asked for it (httpOnly binding cookie).
  - MAX_ATTEMPTS wrong codes lock that code; limits per address, per IP and overall (see LIMITS).
  - An account is matched ONLY by an address its owner proved by typing a code sent to it. Never by a username, a
    provider's unverified email, or a guess. Existing members (Google / X / Telegram sign-in) keep their accounts:
    they sign in the old way once and add their email from the Kiwi Passport (purpose "link").
"""

import hashlib
import hmac
import logging
import re
import secrets
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config.settings import Settings
from app.db.base import utcnow
from app.members import xp as ledger
from app.members.service import MemberError, create_member
from app.models.members import Member, MemberEmailCode
from app.services import mailer

logger = logging.getLogger(__name__)

EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$")
MAX_ATTEMPTS = 5
RESEND_AFTER = 60  # seconds between two codes to the same address
# (scope, window, max codes sent in that window)
LIMITS = (("email", timedelta(hours=1), 5), ("email", timedelta(days=1), 10), ("ip", timedelta(hours=1), 10), ("ip", timedelta(days=1), 30),
          ("all", timedelta(hours=1), 300))
GENERIC_FAIL = "That code isn't right or has expired. Check the latest email, or send a new code."


class TooSoon(MemberError):
    def __init__(self, message: str, retry_after: int) -> None:
        super().__init__(message)
        self.retry_after = retry_after


def normalise(email: str) -> str:
    e = (email or "").strip().lower()
    if len(e) > 254 or not EMAIL_RE.match(e):
        raise MemberError("Enter a valid email address.")
    return e


def _key(settings: Settings) -> bytes:
    raw = settings.app_encryption_key.get_secret_value().strip() if settings.app_encryption_key else ""
    if not raw and settings.is_production:
        raise MemberError("Email sign-in isn't available right now.")
    return hashlib.sha256(b"pc-email-login-v1|" + raw.encode()).digest()


def _kh(settings: Settings, value: str) -> str:
    return hmac.new(_key(settings), value.encode(), hashlib.sha256).hexdigest()


def _since(db: Session, scope: str, value: str | None, when: datetime) -> list[datetime]:
    col = {"email": MemberEmailCode.email_hash, "ip": MemberEmailCode.ip_hash}.get(scope)
    stmt = select(MemberEmailCode.created_at).where(MemberEmailCode.created_at >= when, MemberEmailCode.status != "unsent")
    if col is not None:
        stmt = stmt.where(col == value)
    return list(db.scalars(stmt))


def _check_limits(db: Session, email_h: str, ip_h: str, now: datetime) -> None:
    last = db.scalar(select(func.max(MemberEmailCode.created_at)).where(MemberEmailCode.email_hash == email_h, MemberEmailCode.status != "unsent"))
    if last is not None:
        last = last if last.tzinfo else last.replace(tzinfo=now.tzinfo)
        wait = RESEND_AFTER - int((now - last).total_seconds())
        if wait > 0:
            raise TooSoon(f"A code was just sent. You can ask for a new one in {wait} seconds.", wait)
    for scope, window, cap in LIMITS:
        times = _since(db, scope, {"email": email_h, "ip": ip_h}.get(scope), now - window)
        if len(times) >= cap:
            oldest = min(t if t.tzinfo else t.replace(tzinfo=now.tzinfo) for t in times)
            wait = max(60, int((oldest + window - now).total_seconds()))
            logger.warning("Email sign-in limit reached (%s per %s)", scope, window)
            raise TooSoon("Too many codes requested. Please wait a little and try again.", wait)


async def start(db: Session, settings: Settings, email: str, ip: str, browser: str, purpose: str = "login",
                member: Member | None = None, transport: Any = None) -> dict[str, Any]:
    """Send a code. Same answer whether or not an account exists for the address."""
    if purpose not in ("login", "link"):
        raise MemberError("Unknown purpose.")
    if purpose == "link" and member is None:
        raise MemberError("Sign in first, then add your email.")
    if not mailer.available(settings):
        raise MemberError("Email sign-in isn't available right now. Please try again later.")
    addr = normalise(email)
    if purpose == "link":
        owner = db.scalar(select(Member).where(Member.email == addr))
        if owner is not None and member is not None and owner.id != member.id:
            # telling the signed-in member is not enumeration: they are adding an address to their own account
            raise MemberError("That email already belongs to another Price Chat account.")
    now = utcnow()
    email_h, ip_h = _kh(settings, addr), _kh(settings, f"ip:{ip}")
    _check_limits(db, email_h, ip_h, now)
    db.query(MemberEmailCode).filter(MemberEmailCode.expires_at < now - timedelta(days=2)).delete()  # housekeeping
    for old in db.scalars(select(MemberEmailCode).where(MemberEmailCode.email_hash == email_h, MemberEmailCode.status == "pending")):
        old.status = "replaced"  # only the newest code works
    code = f"{secrets.randbelow(1_000_000):06d}"
    row = MemberEmailCode(email_hash=email_h, code_hash=_kh(settings, f"code:{addr}:{code}"), purpose=purpose,
                          member_id=member.id if member else None, browser_hash=_kh(settings, f"browser:{browser}"), ip_hash=ip_h,
                          attempts=0, status="pending", expires_at=now + timedelta(minutes=settings.email_code_minutes))
    db.add(row)
    db.commit()
    subject, text, html = mailer.code_email(code, settings.email_code_minutes)
    try:
        await mailer.send(settings, addr, subject, text, html, transport)
    except (mailer.MailError, Exception) as exc:  # noqa: BLE001 - never leave a code that was not delivered usable
        row.status = "unsent"
        db.commit()
        logger.error("Sign-in code could not be sent: %s", exc)
        raise MemberError("We couldn't send the email just now. Please try again in a minute.") from None
    return {"sent": True, "resend_after": RESEND_AFTER, "expires_in": settings.email_code_minutes * 60}


def verify(db: Session, settings: Settings, email: str, code: str, browser: str | None, current: Member | None,
           referral_code: str | None = None) -> tuple[Member, bool]:
    """(member, created). Raises MemberError with one generic message for every wrong / expired / used code."""
    addr = normalise(email)
    code = re.sub(r"\D", "", code or "")
    if len(code) != 6:
        raise MemberError("Enter the 6-digit code from the email.")
    now = utcnow()
    row = db.scalar(select(MemberEmailCode).where(MemberEmailCode.email_hash == _kh(settings, addr), MemberEmailCode.status == "pending")
                    .order_by(MemberEmailCode.created_at.desc()).limit(1))
    if row is None:
        raise MemberError(GENERIC_FAIL)
    expires = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=now.tzinfo)
    if expires < now:
        row.status = "expired"
        db.commit()
        raise MemberError("That code has expired. Send a new one.")
    if not browser or not hmac.compare_digest(row.browser_hash, _kh(settings, f"browser:{browser}")):
        raise MemberError("Please enter the code in the same browser you asked for it in.")
    row.attempts += 1
    if not hmac.compare_digest(row.code_hash, _kh(settings, f"code:{addr}:{code}")):
        if row.attempts >= MAX_ATTEMPTS:
            row.status = "locked"
            db.commit()
            raise MemberError("Too many wrong codes. Send a new code to try again.")
        db.commit()
        raise MemberError(GENERIC_FAIL)
    row.status = "used"  # single use
    owner = db.scalar(select(Member).where(Member.email == addr))
    if row.purpose == "link":
        if current is None or current.id != row.member_id:
            db.commit()
            raise MemberError("Please add your email from your own Kiwi Passport, signed in, in this browser.")
        if owner is not None and owner.id != current.id:
            db.commit()
            raise MemberError("That email already belongs to another Price Chat account.")
        current.email, current.email_verified_at = addr, now
        ledger.audit(db, current.id, "member", "email_verified", {"purpose": "link"})
        from app.vpn import trial_guard

        trial_guard.mark_if_trial_used(db, current)  # had a Kiwi VPN trial: this address has had one too
        db.commit()
        return current, False
    if owner is not None:
        if owner.status == "banned":
            db.commit()
            raise MemberError("This Price Chat account is suspended.")
        db.commit()
        return owner, False
    member = create_member(db)  # a new Kiwi Passport: neutral username until they choose one, starting XP
    member.email, member.email_verified_at = addr, now
    ledger.audit(db, member.id, "member", "email_verified", {"purpose": "signup"})
    if referral_code:
        from app.members.referrals import attribute

        attribute(db, member, referral_code, browser=browser, browser_member=current)
    db.commit()
    return member, True


def mask(email: str | None) -> str | None:
    """j•••@gmail.com: shown on the member's own Passport."""
    if not email or "@" not in email:
        return None
    local, domain = email.split("@", 1)
    return f"{local[:1]}{'•' * max(2, min(len(local) - 1, 6))}@{domain}"
