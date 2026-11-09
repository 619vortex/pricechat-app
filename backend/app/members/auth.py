"""Official sign-in flows for members. Price Chat never sees a platform password. Each flow ends with a verified
platform user id (and public name). Google and Telegram tokens are discarded at once; the member's own X token is
kept, encrypted, so a reply or repost they press in Price Chat can be sent as them (see members/x_actions.py).

- Google: OpenID Connect authorization code + PKCE, scope "openid" only: an account id, no name, email or YouTube data.
- X: OAuth 2.0 authorization code + PKCE with a member-login Client ID (separate from the brand connector),
  scopes in X_SCOPES; the token is stored encrypted (never in the browser) and deleted on disconnect.
- Telegram: a one-time bot start link (t.me/<bot>?start=<code>). Pressing Start is not enough: the bot asks
  "Sign in to Price Chat on <device>?" and only the [Yes] button from that same Telegram user completes it, so a
  link someone else created can't sign them in by one tap. The Login Widget (HMAC signed by the bot token) is
  only accepted when the deeplink mode is off.

Every attempt is a single-use MemberAuthRequest bound to the browser that started it (hash of an httpOnly
cookie), expiring after 10 minutes (Telegram: 5).
"""

import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
from datetime import timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.settings import Settings
from app.db.base import utcnow
from app.members.service import MemberError
from app.models.members import Member, MemberAuthRequest
from app.security.credentials import resolve
from app.security.secrets import protect, unprotect

logger = logging.getLogger(__name__)

REQUEST_TTL = timedelta(minutes=10)
TELEGRAM_TTL = timedelta(minutes=5)  # a Telegram start code (and its Yes / No question) lives 5 minutes
TELEGRAM_CONFIRM_PREFIX = "pcs:"  # callback data of the bot's [Yes, sign me in] / [No] buttons: pcs:y:<id> / pcs:n:<id>
TELEGRAM_CODE_PREFIX = "pc_"
WIDGET_MAX_AGE = 86400  # Telegram widget data older than this is refused


def _h(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _enc(value: str) -> str:
    return base64.b64encode(protect(value.encode())).decode()


def _dec(value: str) -> str:
    return unprotect(base64.b64decode(value)).decode()


def public_origin(settings: Settings) -> str:
    """The one canonical address of the website (scheme://host:port), e.g. http://127.0.0.1:5173."""
    return settings.members_public_url.rstrip("/")


def callback_url(settings: Settings, provider: str) -> str:
    """The exact redirect URI sent to the provider. Built only here (never by the browser) from one setting."""
    override = {"google": settings.member_google_redirect_uri, "x": settings.member_x_redirect_uri}.get(provider)
    return (override or f"{public_origin(settings)}/api/members/auth/{provider}/callback").strip()


def google_client(settings: Settings) -> tuple[str | None, str | None]:
    """The OAuth client for "Sign in with Google": its own Web client (google_member_client_id / _secret) when set,
    otherwise the YouTube connector's client (as before). Kept separate so a new login client never invalidates the
    YouTube connector's refresh token, which belongs to the connector's client."""
    cid, secret = resolve("google_member_client_id", settings), resolve("google_member_client_secret", settings)
    if cid and secret:
        return cid, secret
    return resolve("youtube_client_id", settings), resolve("youtube_client_secret", settings)


def provider_status(db: Session, settings: Settings) -> dict[str, dict[str, Any]]:
    """Which sign-in options can work right now, and what is missing if not (shown on the join page and dashboard)."""
    from app.telegram.ingestion import get_bot_state

    google_id, google_secret = google_client(settings)
    google = bool(google_id and google_secret)
    x_id = resolve("x_member_client_id", settings)
    bot = get_bot_state(db).bot_username if settings.telegram_configured else None
    return {
        "public_url": public_origin(settings),
        "google": {"available": google, "callback": callback_url(settings, "google"), "javascript_origin": public_origin(settings),
                   "client_id": google_id or None,  # a client id is public; the secret never leaves the server
                   "scopes": "openid",
                   "note": "" if google else "Add the Google OAuth client on the YouTube page first."},
        "x": {"available": bool(x_id), "callback": callback_url(settings, "x"), "website_url": "https://x.com/pricechat",
              "client_id": x_id or None, "scopes": x_link_scopes(settings), "app_type": "Web App",
              "app_permissions": "Read and write" if settings.x_member_write else "Read (or Read and write)",
              "write": settings.x_member_write,
              "method": "OAuth 2.0 + PKCE (sign-in" + (", plus replies / reposts the member presses)" if settings.x_member_write else ", read-only)"),
              "note": "" if x_id else "Sign in with X isn't set up yet: paste the X OAuth 2.0 Client ID and Secret under Members › Sign-in setup."},
        "telegram": {"available": bool(bot), "bot": bot, "mode": "deeplink" if settings.members_telegram_deeplink else "widget",
                     "note": "" if bot else "Connect the Telegram bot first."},
    }


def _new_request(db: Session, provider: str, purpose: str, browser: str, state: str, member: Member | None,
                 secret: str | None = None, ttl: timedelta = REQUEST_TTL, result: dict[str, Any] | None = None) -> MemberAuthRequest:
    if purpose not in ("login", "link"):
        raise MemberError("Unknown purpose.")
    if purpose == "link" and member is None:
        raise MemberError("Sign in first, then connect another account.")
    db.query(MemberAuthRequest).filter(  # housekeeping: expired attempts, and any stale attempt with the same state
        (MemberAuthRequest.expires_at < utcnow()) | (MemberAuthRequest.state_hash == _h(state))).delete()
    req = MemberAuthRequest(provider=provider, purpose=purpose, state_hash=_h(state), browser_hash=_h(browser),
                            member_id=member.id if member else None, secret=_enc(secret) if secret else None,
                            result=result or {}, expires_at=utcnow() + ttl)
    db.add(req)
    db.commit()
    return req


def _take(db: Session, provider: str, state: str, browser: str | None) -> MemberAuthRequest:
    req = db.scalar(select(MemberAuthRequest).where(MemberAuthRequest.state_hash == _h(state), MemberAuthRequest.provider == provider))
    if req is None or req.status != "pending":
        raise MemberError("This sign-in link is no longer valid. Please start again.")
    if req.expires_at < utcnow():
        raise MemberError("This sign-in took too long. Please start again.")
    if not browser or not hmac.compare_digest(req.browser_hash, _h(browser)):
        raise MemberError("Please finish signing in in the same browser you started in.")
    req.status = "done"  # single use, whatever happens next
    db.commit()
    return req


# --- Google -----------------------------------------------------------------------------------------------------


def google_start(db: Session, settings: Settings, purpose: str, browser: str, member: Member | None) -> str:
    cid, _secret = google_client(settings)
    if not cid or not _secret:
        raise MemberError("Google sign-in is not set up yet.")
    state, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    _new_request(db, "google", purpose, browser, state, member, secret=verifier)
    return f"{settings.google_auth_url}?" + urlencode({
        "client_id": cid, "redirect_uri": callback_url(settings, "google"), "response_type": "code", "scope": "openid",
        "state": state, "code_challenge": challenge, "code_challenge_method": "S256", "prompt": "select_account",
    })


def _jwt_claims(token: str) -> dict[str, Any]:
    try:
        payload = token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (IndexError, ValueError) as exc:
        raise MemberError("Google returned an unreadable sign-in.") from exc


async def google_finish(db: Session, settings: Settings, state: str, code: str, browser: str | None,
                        transport: httpx.AsyncBaseTransport | None = None) -> tuple[MemberAuthRequest, dict[str, Any]]:
    req = _take(db, "google", state, browser)
    cid, secret = google_client(settings)
    if not cid or not secret or not req.secret:
        raise MemberError("Google sign-in is not set up.")
    try:
        async with httpx.AsyncClient(timeout=20.0, transport=transport) as client:
            r = await client.post(settings.google_token_url, data={
                "client_id": cid, "client_secret": secret, "code": code, "code_verifier": _dec(req.secret),
                "grant_type": "authorization_code", "redirect_uri": callback_url(settings, "google")})
    except httpx.HTTPError as exc:
        raise MemberError("Could not reach Google. Please try again.") from exc
    if r.status_code != 200 or "id_token" not in r.json():
        raise MemberError("Google did not accept the sign-in. Please try again.")
    # The ID token comes straight from Google's token endpoint over TLS (OpenID Connect Core 3.1.3.7): we check
    # audience, issuer and expiry; the access token is never stored or used.
    claims = _jwt_claims(r.json()["id_token"])
    if claims.get("aud") != cid or claims.get("iss") not in ("accounts.google.com", "https://accounts.google.com"):
        raise MemberError("Google sign-in could not be verified.")
    if int(claims.get("exp", 0)) < time.time() or not claims.get("sub"):
        raise MemberError("Google sign-in expired. Please try again.")
    return req, {"platform": "google", "id": str(claims["sub"]), "username": None, "display_name": None}  # only the account id


# --- X (member sign-in: OAuth 2.0 + PKCE) ------------------------------------------------------------------------
# Separate from the @pricechat brand connector: its own Client ID/secret. The token is the member's own, kept
# encrypted only to send the replies / reposts they press. No like.write: X removed likes from the API (April 2026).

X_SCOPES = "users.read tweet.read tweet.write offline.access"  # connecting X with in-card replies / reposts (settings.x_member_write)
X_READ_SCOPES = "users.read tweet.read offline.access"  # connecting X read-only: identity, reading (to verify missions), refresh


def x_link_scopes(settings: Settings) -> str:
    """What Connect X asks for: write access only when the X app is set up for it (settings.x_member_write)."""
    return X_SCOPES if settings.x_member_write else X_READ_SCOPES
X_LOGIN_SCOPES = "users.read tweet.read"  # signing in: who you are, nothing more (X's consent screen shows read-only)


class XAuthError(MemberError):
    """A sign-in problem with a stable code, so the page can explain exactly what went wrong."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _x_client(settings: Settings) -> tuple[str, str | None]:
    cid = resolve("x_member_client_id", settings)
    if not cid:
        raise XAuthError("not_configured", "Sign in with X is not set up yet.")
    return cid, resolve("x_member_client_secret", settings)


def x_start(db: Session, settings: Settings, purpose: str, browser: str, member: Member | None) -> str:
    cid, _ = _x_client(settings)
    state, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    _new_request(db, "x", purpose, browser, state, member, secret=verifier)
    return f"{settings.x_oauth2_authorize_url}?" + urlencode({
        "response_type": "code", "client_id": cid, "redirect_uri": callback_url(settings, "x"),
        "scope": X_LOGIN_SCOPES if purpose == "login" else x_link_scopes(settings),
        "state": state, "code_challenge": challenge, "code_challenge_method": "S256",
    })


def _x_auth_header(cid: str, secret: str | None) -> dict[str, str]:
    """Confidential clients authenticate with HTTP Basic; public clients send client_id in the body only."""
    if not secret:
        return {}
    return {"Authorization": "Basic " + base64.b64encode(f"{cid}:{secret}".encode()).decode()}


async def x_finish(db: Session, settings: Settings, state: str, code: str, browser: str | None,
                   transport: httpx.AsyncBaseTransport | None = None) -> tuple[MemberAuthRequest, dict[str, Any], dict[str, Any]]:
    """Returns (attempt, verified identity, token response). The caller stores the token for the member."""
    try:
        req = _take(db, "x", state, browser)
    except MemberError as exc:
        text = str(exc)
        raise XAuthError("expired_state" if "too long" in text else "wrong_browser" if "same browser" in text else "invalid_state", text) from exc
    cid, secret = _x_client(settings)
    base = settings.x_api_base.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=20.0, transport=transport) as client:
            r = await client.post(settings.x_oauth2_token_url, headers=_x_auth_header(cid, secret), data={
                "grant_type": "authorization_code", "code": code, "redirect_uri": callback_url(settings, "x"),
                "code_verifier": _dec(req.secret) if req.secret else "", "client_id": cid})
            if r.status_code != 200 or "access_token" not in r.json():
                body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
                desc = f"{body.get('error', '')} {body.get('error_description', '')}".lower()
                if "redirect" in desc:
                    raise XAuthError("callback_mismatch", "X says the callback address doesn't match the one registered on the X app.")
                if body.get("error") in ("unauthorized_client", "invalid_client") or r.status_code == 401:
                    raise XAuthError("client_rejected", "X rejected Price Chat's sign-in app credentials. The team needs to check the X Client ID/secret.")
                raise XAuthError("token_failed", "X didn't confirm the sign-in. Please try again.")
            token_body = r.json()
            me = await client.get(f"{base}/2/users/me", headers={"Authorization": f"Bearer {token_body['access_token']}"})
    except httpx.HTTPError as exc:
        raise XAuthError("unreachable", "Could not reach X. Please try again.") from exc
    if me.status_code in (401, 403):
        raise XAuthError("permission", "X didn't let Price Chat read which account signed in. Please try again and allow access.")
    user = (me.json() or {}).get("data") or {}
    if me.status_code != 200 or not user.get("id"):
        raise XAuthError("token_failed", "X didn't confirm which account signed in. Please try again.")
    return req, {"platform": "x", "id": str(user["id"]), "username": user.get("username"), "display_name": user.get("name")}, token_body


# --- Telegram ---------------------------------------------------------------------------------------------------


def device_label(user_agent: str | None) -> str:
    """'Chrome on Windows' from a User-Agent, for the bot's "Sign in on <device>?" question. Never more detailed."""
    ua = user_agent or ""
    browser = next((name for key, name in (("Edg/", "Edge"), ("OPR/", "Opera"), ("SamsungBrowser", "Samsung Internet"), ("Firefox/", "Firefox"),
                                           ("CriOS", "Chrome"), ("FxiOS", "Firefox"), ("Chrome/", "Chrome"), ("Safari/", "Safari")) if key in ua), "a browser")
    system = next((name for key, name in (("Android", "Android"), ("iPhone", "iPhone"), ("iPad", "iPad"), ("Windows", "Windows"),
                                          ("Mac OS X", "Mac"), ("CrOS", "ChromeOS"), ("Linux", "Linux")) if key in ua), "")
    return f"{browser} on {system}" if system else browser


def telegram_start(db: Session, settings: Settings, purpose: str, browser: str, member: Member | None, device: str | None = None) -> dict[str, Any]:
    if not settings.members_telegram_deeplink:
        raise MemberError("Use the Telegram login button.")
    from app.telegram.ingestion import get_bot_state

    bot = get_bot_state(db).bot_username if settings.telegram_configured else None
    if not bot:
        raise MemberError("Telegram sign-in is not available right now.")
    code = secrets.token_urlsafe(18).replace("-", "x")  # Telegram start parameters: A-Z a-z 0-9 _ - (max 64)
    req = _new_request(db, "telegram", purpose, browser, code, member, ttl=TELEGRAM_TTL, result={"device": (device or "")[:64]})
    return {"request": req.id, "link": f"https://t.me/{bot}?start={TELEGRAM_CODE_PREFIX}{code}", "expires_at": req.expires_at.isoformat()}


def telegram_seen_start(db: Session, code: str, user_id: int, username: str | None, name: str | None) -> dict[str, Any] | None:
    """Called by the bot poller for '/start pc_<code>' in a private chat. Records who pressed Start, but does NOT verify
    the attempt: the bot then asks that person to confirm (telegram_confirm). Returns what the question needs
    ({request, purpose, device, passport}) when the code was valid, else None."""
    req = db.scalar(select(MemberAuthRequest).where(MemberAuthRequest.state_hash == _h(code), MemberAuthRequest.provider == "telegram"))
    if req is None or req.status != "pending" or req.expires_at < utcnow():
        return None
    device = str((req.result or {}).get("device") or "")
    req.status = "awaiting"  # bound to this Telegram user from now on; only their [Yes] completes it
    req.result = {"platform": "telegram", "id": str(user_id), "username": username, "display_name": name, "device": device}
    passport = None
    if req.purpose == "link" and req.member_id:
        m = db.get(Member, req.member_id)
        passport = m.username if m else None
    db.commit()
    return {"request": req.id, "purpose": req.purpose, "device": device, "passport": passport}


def telegram_confirm(db: Session, request_id: int, user_id: int, yes: bool) -> tuple[str, str | None]:
    """The [Yes, sign me in] / [No] button under the bot's question. Only the Telegram user who pressed Start can answer,
    once, before the attempt expires. Returns (outcome, purpose): outcome is "verified", "cancelled" or "invalid"."""
    req = db.get(MemberAuthRequest, request_id)
    if (req is None or req.provider != "telegram" or req.status != "awaiting" or req.expires_at < utcnow()
            or str((req.result or {}).get("id")) != str(user_id)):
        return "invalid", None
    if yes:
        req.status = "verified"
    else:
        req.status, req.result, req.error = "failed", {}, "cancelled"
    db.commit()
    return ("verified" if yes else "cancelled"), req.purpose


def telegram_claim(db: Session, request_id: int, browser: str | None) -> tuple[str, MemberAuthRequest | None, dict[str, Any] | None]:
    """The browser that started the attempt asks whether it was confirmed in Telegram. Returns (status, request, identity)."""
    req = db.get(MemberAuthRequest, request_id)
    if req is None or req.provider != "telegram" or not browser or not hmac.compare_digest(req.browser_hash, _h(browser)):
        return "unknown", None, None
    if req.status in ("pending", "awaiting"):  # awaiting: Start was pressed, the Yes / No question is open
        return ("expired", None, None) if req.expires_at < utcnow() else ("pending", None, None)
    if req.status == "failed" and req.error == "cancelled":
        return "cancelled", None, None
    if req.status != "verified":
        return "used", None, None
    identity = {k: v for k, v in req.result.items() if k != "device"}
    req.status, req.result = "done", {}
    db.commit()
    return "verified", req, identity


def telegram_widget_verify(settings: Settings, data: dict[str, Any]) -> dict[str, Any]:
    """Verify Telegram Login Widget data (https://core.telegram.org/widgets/login#checking-authorization)."""
    from app.security.credentials import telegram_token

    token = telegram_token(settings)
    if not token:
        raise MemberError("Telegram sign-in is not available right now.")
    fields = {k: str(v) for k, v in data.items() if k != "hash" and v is not None}
    check = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    expected = hmac.new(hashlib.sha256(token.encode()).digest(), check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, str(data.get("hash", ""))):
        raise MemberError("Telegram sign-in could not be verified.")
    if time.time() - int(fields.get("auth_date", "0")) > WIDGET_MAX_AGE:
        raise MemberError("Telegram sign-in expired. Please try again.")
    name = " ".join(x for x in (fields.get("first_name"), fields.get("last_name")) if x) or None
    return {"platform": "telegram", "id": fields["id"], "username": fields.get("username"), "display_name": name}
