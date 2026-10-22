"""Owner login for the internal dashboard and every admin API.

One owner account from the environment: ADMIN_USERNAME and ADMIN_PASSWORD_HASH (a scrypt hash, never the password).
Make the hash on your own machine and paste only the hash into Railway:

    python -m app.security.admin_auth hash

Sessions are server-side (admin_sessions: only a sha256 of the token is stored). The browser holds the token in an
HttpOnly cookie, Secure in production, SameSite=Lax (first-party: Vercel forwards /api to the backend on the same
domain; Lax still sends it on the top-level redirect back from Google's OAuth for the YouTube connection). Failed logins
are rate-limited per client and globally. Required in production; locally it's off unless ADMIN_PASSWORD_HASH is set,
so local development keeps working as before.
"""

import base64
import hashlib
import hmac
import secrets
import sys
import threading
import time
from collections import defaultdict, deque
from datetime import timedelta

from fastapi import Request, Response
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config.settings import Settings
from app.db.base import utcnow
from app.models.admin import AdminSession

COOKIE = "pc_admin"
# scrypt parameters: ~16 MB memory, well under a second on a server
_N, _R, _P, _DKLEN = 2 ** 14, 8, 1, 32

# rate limits on FAILED logins
PER_CLIENT, PER_CLIENT_WINDOW = 5, 15 * 60
GLOBAL, GLOBAL_WINDOW = 30, 15 * 60


# --- password hashing ---------------------------------------------------------------------------------------------


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_N, r=_R, p=_P, dklen=_DKLEN)
    b64 = lambda b: base64.b64encode(b).decode("ascii")  # noqa: E731
    return f"scrypt${_N}${_R}${_P}${b64(salt)}${b64(dk)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algo, n, r, p, salt, expected = encoded.strip().split("$")
        if algo != "scrypt":
            return False
        want = base64.b64decode(expected)
        got = hashlib.scrypt(password.encode("utf-8"), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p),
                             dklen=len(want), maxmem=128 * 1024 * 1024)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got, want)


# --- rate limiting ------------------------------------------------------------------------------------------------

_fails: dict[str, deque[float]] = defaultdict(deque)
_lock = threading.Lock()


def _prune(q: deque[float], window: float, now: float) -> None:
    while q and now - q[0] > window:
        q.popleft()


def client_key(request: Request) -> str:
    """The visitor's address. Behind Vercel → Railway the first X-Forwarded-For hop is the visitor; it can be spoofed
    when calling the backend directly, which is why there is also a global limit."""
    fwd = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    return fwd or (request.client.host if request.client else "unknown")


def retry_after(key: str) -> int | None:
    """Seconds until this client may try again, or None if it may try now."""
    now = time.monotonic()
    with _lock:
        mine, every = _fails[key], _fails["*"]
        _prune(mine, PER_CLIENT_WINDOW, now)
        _prune(every, GLOBAL_WINDOW, now)
        if len(mine) >= PER_CLIENT:
            return int(PER_CLIENT_WINDOW - (now - mine[0])) + 1
        if len(every) >= GLOBAL:
            return int(GLOBAL_WINDOW - (now - every[0])) + 1
    return None


def record_failure(key: str) -> None:
    now = time.monotonic()
    with _lock:
        _fails[key].append(now)
        _fails["*"].append(now)


def reset_for_tests() -> None:
    with _lock:
        _fails.clear()


# --- sessions -----------------------------------------------------------------------------------------------------


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def check_credentials(settings: Settings, username: str, password: str) -> bool:
    want_user = (settings.admin_username or "").strip()
    stored = settings.admin_password_hash.get_secret_value() if settings.admin_password_hash else ""
    if not want_user or not stored.strip():
        hash_password("x")  # same work either way: no timing hint that admin login isn't configured
        return False
    user_ok = hmac.compare_digest(username.strip().encode(), want_user.encode())
    pass_ok = verify_password(password, stored)  # always runs: no timing hint about which part was wrong
    return user_ok and pass_ok


def start_session(db: Session, settings: Settings, response: Response, username: str) -> None:
    token = secrets.token_urlsafe(32)
    now = utcnow()
    db.execute(delete(AdminSession).where(AdminSession.expires_at < now))  # tidy up old ones
    db.add(AdminSession(token_hash=_digest(token), username=username[:64], expires_at=now + timedelta(hours=settings.admin_session_hours),
                        last_seen_at=now))
    db.commit()
    response.set_cookie(COOKIE, token, max_age=settings.admin_session_hours * 3600, httponly=True, samesite="lax",
                        secure=secure_cookies(settings), path="/")


def end_session(db: Session, response: Response, token: str | None) -> None:
    if token:
        db.execute(delete(AdminSession).where(AdminSession.token_hash == _digest(token)))
        db.commit()
    response.delete_cookie(COOKIE, path="/")


def current(db: Session, token: str | None) -> AdminSession | None:
    if not token:
        return None
    row = db.scalar(select(AdminSession).where(AdminSession.token_hash == _digest(token)))
    if row is None:
        return None
    now = utcnow()
    expires = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=now.tzinfo)
    if expires <= now:
        db.delete(row)
        db.commit()
        return None
    return row


def secure_cookies(settings: Settings) -> bool:
    return settings.is_production or settings.members_public_url.startswith("https://")


# --- the "is this request allowed?" rule ----------------------------------------------------------------------------

# Everything under /api is admin-only unless listed here (new routes are private by default).
PUBLIC_PREFIXES = ("/api/members/",
                   # Brand Manager: AI workers authenticate with their own token; TikTok fetches videos by signed URL
                   "/api/brand-manager/worker/", "/api/brand-manager/media/",
                   "/api/short-market/worker/",  # member APIs have their own member sign-in
                   "/api/website/seo/",  # sitemaps and link previews: only what public pages already show
                   "/api/r/")  # tracked referral links: count a click, 302 on (stores no IP / user agent / member)
PUBLIC_EXACT = {
    # the owner login itself (every other /api/admin/... route needs the login)
    ("GET", "/api/admin/session"),
    ("POST", "/api/admin/login"),
    ("POST", "/api/admin/logout"),
    ("GET", "/api/health/live"),
    ("GET", "/api/website/home"),
    ("GET", "/api/website/latest"),
    ("GET", "/api/website/content"),
    ("GET", "/api/website/chat"),  # read-only preview of the public Telegram group (chat bubble)
}


def is_public(method: str, path: str) -> bool:
    if not path.startswith("/api/"):
        return True  # not an API call (the backend serves nothing else)
    if path.startswith(PUBLIC_PREFIXES):
        return True
    return (method.upper() if method.upper() != "HEAD" else "GET", path.rstrip("/") or "/") in PUBLIC_EXACT


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] != "hash":
        print("usage: python -m app.security.admin_auth hash")
        return 2
    import getpass

    first = getpass.getpass("New admin password (min 12 characters): ")
    if len(first) < 12:
        print("Too short: use at least 12 characters.")
        return 1
    if getpass.getpass("Again: ") != first:
        print("The two passwords don't match.")
        return 1
    print("\nADMIN_PASSWORD_HASH (paste this, not the password, into Railway):\n" + hash_password(first))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
