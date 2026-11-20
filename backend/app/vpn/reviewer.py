"""One store-review account for Kiwi VPN's store reviewers: the browser extension stores, Google Play and the App Store.
Off unless all three VPN_REVIEWER_* variables are set.

Store reviewers need a plain email and password, and need the VPN to work while it's closed to everyone else. This account gets
exactly that: until VPN_REVIEWER_UNTIL (UTC), no payment needed, one server (SERVER), one browser login and one WireGuard device.
It has no Admin rights, and Admin's block still wins. Only a scrypt hash of the password is on the server. Remove the variables
to end it sooner: sync then takes its logins and devices off the servers.

Restricted (2026-11-20, mobile store review):
- The password sign-in gives a "kr." session that reaches only the VPN routes, who's signed in, deleting the account, signing
  out, signing in again and handing the Kiwi VPN app its own session (review_token_may, enforced in main.admin_guard). It can't
  touch XP, rewards, trading, wallets or anything else of Price Chat's.
- A WireGuard device only from a Kiwi VPN app session ("kd.", itself scoped by app.members.desktop_scope), with a key made on
  the phone, on SERVER only, one per seat (app.vpn.service.create_device). Never from the website, so no config with a
  private key is ever handed out for it.

Seats (2026-11-20, before the mobile deploy): every password sign-in gets its OWN review member, a "seat" (Member.review_seat),
so an Android reviewer and an iPhone reviewer reviewing at the same time never share devices, sessions or a deletion. Signing in
again from a browser that already holds a seat's "kr." session keeps that seat. At most MAX_SEATS: the next sign-in first
reclaims seats with no live session, then the least recently used. Seats have no email, so no email-code, Google, X or
Telegram sign-in can ever land on one, and a seat accepts only "kr." and "kd." sessions (members.service.member_for_token):
any other session of a seat, such as an unscoped one from before the restriction, is refused and deleted on first use
(migration 0119 also deletes them). Deleting a seat deletes only that seat.

The rights come only from the seat flag, set only by this sign-in (security audit L9): never from an email address. Each seat's
free trial is marked used, so a seat is worth nothing once the variables are removed. vpn_settings.reviewer_member_id still
names the latest seat, so a rollback to the code before seats keeps one working review account.
"""

import base64
import hashlib
import hmac
import secrets
from datetime import date, datetime, time, timezone

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.config.settings import Settings, get_settings
from app.db.base import utcnow
from app.members import xp as ledger
from app.members.service import MemberError, create_member
from app.models.members import Member, MemberSession

SERVER = "Helsinki 1"
REVIEW_TOKEN_PREFIX = "kr."  # not in token_urlsafe's alphabet either, so no other session can start this way
# never the other sign-in routes: their "link" purpose would attach a Google, X, Telegram or email identity to this shared account
_EXACT = {"/api/members/vpn", "/api/members/me", "/api/members/logout", "/api/members/desktop/code", "/api/members/auth/reviewer",
          "/api/members/auth/providers"}
_UNDER = ("/api/members/vpn/",)


def review_token_may(path: str, method: str = "GET") -> bool:
    """What a review account's browser session may reach (see the module note). Never a profile change: renaming the shared
    account or putting it on the leaderboard (PATCH /api/members/me)."""
    clean = path.rstrip("/") or "/"
    if clean == "/api/members/me" and method.upper() not in ("GET", "HEAD", "DELETE"):
        return False
    return clean in _EXACT or clean.startswith(_UNDER)
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


MAX_SEATS = 10
SESSION_KINDS = ("reviewer", "desktop")  # the only sessions a seat accepts: the scoped "kr." browser session and the app's "kd."


def is_reviewer(db: Session, member: Member | None, s: Settings | None = None) -> bool:
    """A review seat, while the review account is configured."""
    return member is not None and bool(member.review_seat) and configured(s)


def in_time(now: datetime | None = None, s: Settings | None = None) -> bool:
    ends = _ends(s or get_settings())
    return ends is not None and (now or utcnow()) <= ends


def sign_in(db: Session, s: Settings, email: str, password: str, current: Member | None = None) -> Member:
    """A review seat: `current` when it already is one (the same browser signing in again), else a fresh seat. One answer for
    every failure."""
    addr = (email or "").strip().lower()
    good = check_password(_stored(s), password) if _stored(s) else False  # the hash is checked first, so a wrong address costs the same
    if not good or not addr or addr != _email(s) or not in_time(s=s):
        raise MemberError(FAIL)
    member = current if current is not None and current.review_seat and current.status == "active" else None
    if member is None:
        _make_room(db)
        member = create_member(db)
        member.review_seat, member.leaderboard_opt_out = True, True  # never on the public leaderboard, and no email
        ledger.audit(db, member.id, "member", "vpn_reviewer_created", {})
    # a seat accepts only scoped sessions: anything else it holds ends here too (member_for_token refuses them anyway)
    db.query(MemberSession).filter(MemberSession.member_id == member.id,
                                   or_(MemberSession.kind.is_(None), MemberSession.kind.not_in(SESSION_KINDS))).delete(synchronize_session=False)
    _bind(db, member)
    db.commit()
    return member


def _make_room(db: Session) -> None:
    """Keep at most MAX_SEATS - 1 seats before a new one: seats with no live session go first, then the least recently used."""
    from app.members.service import delete_member

    now = utcnow()
    seats = list(db.scalars(select(Member).where(Member.review_seat.is_(True))))
    if len(seats) < MAX_SEATS:
        return
    seen: dict[int, datetime] = {}
    live: set[int] = set()
    for row in db.scalars(select(MemberSession).where(MemberSession.member_id.in_([m.id for m in seats]))):
        exp = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=timezone.utc)
        if exp > now:
            live.add(row.member_id)
        at = row.last_seen_at or row.created_at
        if at is not None:
            at = at if at.tzinfo else at.replace(tzinfo=timezone.utc)
            seen[row.member_id] = max(seen.get(row.member_id, at), at)
    oldest = datetime.min.replace(tzinfo=timezone.utc)
    seats.sort(key=lambda m: (m.id in live, seen.get(m.id, oldest), m.id))
    for m in seats[: len(seats) - MAX_SEATS + 1]:
        delete_member(db, m, actor="system")


def _bind(db: Session, member: Member) -> None:
    """Use up the seat's free trial, and name it in vpn_settings.reviewer_member_id (the latest seat, for a rollback)."""
    from app.vpn import service, trial_guard

    service.settings(db).reviewer_member_id = member.id
    acct = service.account_of(db, member)
    if acct.trial_started_at is None:
        now = utcnow()
        acct.trial_started_at = acct.trial_ends_at = now  # used, and already over
    trial_guard.mark(db, member)
    db.flush()
