"""Owner login for the internal dashboard and every admin API.

One owner account from the environment: ADMIN_USERNAME and ADMIN_PASSWORD_HASH (a scrypt hash, never the password).
Make the hash on your own machine and paste only the hash into Railway:

    python -m app.security.admin_auth hash

Sessions are server-side (admin_sessions: only a sha256 of the token is stored). The browser holds the token in an
HttpOnly cookie, Secure in production, SameSite=Lax (first-party: Vercel forwards /api to the backend on the same
domain; Lax still sends it on the top-level redirect back from Google's OAuth for the YouTube connection). Failed logins
are rate-limited per client and globally. A browser the owner has signed in from before carries a signed "known device"
cookie (pc_admin_device); its attempts skip the global and per-address buckets and have a strict limit of their own, so a
flood of failures from other (or spoofed) addresses can't lock the owner out of that browser. Required in production; locally it's off unless ADMIN_PASSWORD_HASH is set,
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
DEVICE_COOKIE = "pc_admin_device"  # "this browser has signed in as the owner before" (see known_device)
DEVICE_DAYS = 180
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
    """The visitor's rate-limit key (app.security.client_ip: Vercel's report of the visitor only with the shared secret,
    else the address Railway's edge saw; never a caller-supplied X-Forwarded-For entry). The global limit stays as a
    backstop against many real addresses."""
    from app.security import client_ip

    return client_ip.client_key(request)


def retry_after(key: str, device: str | None = None) -> int | None:
    """Seconds until this client may try again, or None if it may try now. A known device (known_device) is limited on
    its own (PER_CLIENT per window) and never by the global bucket a flood from anywhere else fills up."""
    now = time.monotonic()
    with _lock:
        if device:
            mine = _fails["d:" + device]
            _prune(mine, PER_CLIENT_WINDOW, now)
            return int(PER_CLIENT_WINDOW - (now - mine[0])) + 1 if len(mine) >= PER_CLIENT else None
        mine, every = _fails[key], _fails["*"]
        _prune(mine, PER_CLIENT_WINDOW, now)
        _prune(every, GLOBAL_WINDOW, now)
        if len(mine) >= PER_CLIENT:
            return int(PER_CLIENT_WINDOW - (now - mine[0])) + 1
        if len(every) >= GLOBAL:
            return int(GLOBAL_WINDOW - (now - every[0])) + 1
    return None


def record_failure(key: str, device: str | None = None) -> None:
    now = time.monotonic()
    with _lock:
        if device:
            _fails["d:" + device].append(now)
            return
        _fails[key].append(now)
        _fails["*"].append(now)


def reset_for_tests() -> None:
    with _lock:
        _fails.clear()


# --- known devices -------------------------------------------------------------------------------------------------


def _device_key(settings: Settings) -> bytes:
    """Signs device cookies. Derived from the password hash (changing the password revokes every device) and the app's
    encryption key (a leaked password hash alone can't mint one)."""
    stored = settings.admin_password_hash.get_secret_value().strip() if settings.admin_password_hash else ""
    enc = settings.app_encryption_key.get_secret_value().strip() if settings.app_encryption_key else ""
    return hashlib.sha256(f"pc-admin-device\0{stored}\0{enc}".encode()).digest()


def _device_sig(settings: Settings, nonce: str, issued: int) -> str:
    user = (settings.admin_username or "").strip()
    return hmac.new(_device_key(settings), f"{user}\0{nonce}\0{issued}".encode(), hashlib.sha256).hexdigest()


def device_token(settings: Settings) -> str:
    nonce, issued = secrets.token_urlsafe(12), int(time.time())
    return f"{nonce}.{issued}.{_device_sig(settings, nonce, issued)}"


def known_device(settings: Settings, token: str | None) -> str | None:
    """The device id when `token` is a valid device cookie for the current owner username + password, else None."""
    if not token or len(token) > 200 or not (settings.admin_password_hash and settings.admin_password_hash.get_secret_value().strip()):
        return None
    try:
        nonce, issued_s, sig = token.split(".")
        issued = int(issued_s)
    except ValueError:
        return None
    if not nonce or not 0 <= time.time() - issued <= DEVICE_DAYS * 86400:
        return None
    return nonce if hmac.compare_digest(_device_sig(settings, nonce, issued), sig) else None


def remember_device(settings: Settings, response: Response) -> None:
    response.set_cookie(DEVICE_COOKIE, device_token(settings), max_age=DEVICE_DAYS * 86400, httponly=True, samesite="lax",
                        secure=secure_cookies(settings), path="/api/admin")


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
                   "/api/vortex/",
                   "/api/committee/",  # Kiwi AI Investment Committee: public research JSON; starting a run checks member / owner itself  # Vortex trade checks: the owner's home AI, its own bearer token (never the admin cookie)
                   "/api/website/seo/",  # sitemaps and link previews: only what public pages already show
                   "/api/r/")  # tracked referral links: count a click, 302 on (stores no IP / user agent / member)
PUBLIC_EXACT = {
    # the owner login itself (every other /api/admin/... route needs the login)
    ("GET", "/api/admin/session"),
    ("POST", "/api/admin/login"),
    ("POST", "/api/admin/logout"),
    ("GET", "/api/health/live"),
    ("GET", "/api/health/public"),  # uptime monitors (HEAD too): ok/error words only, rate-limited
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
