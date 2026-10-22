"""TikTok, official APIs only (developers.tiktok.com): Login Kit OAuth 2.0 v2 + Content Posting API + Display API.

- OAuth: the owner signs in on tiktok.com and grants scopes; Price Chat never sees a TikTok password. Access tokens
  (24 h) and refresh tokens (365 d) are encrypted in the secret store; refreshed automatically; revoked on disconnect.
- Publishing: inbox upload (video.upload: the video lands in the creator's TikTok inbox to finish posting in the app)
  or direct post (video.publish: needs the creator's choices from TikTok's required posting screen, and, until TikTok
  audits the app, private SELF_ONLY visibility only). Videos are pulled by TikTok from our verified domain (PULL_FROM_URL).
- Nothing is reported as published unless TikTok's status endpoint says PUBLISH_COMPLETE.
"""

import hashlib
import logging
import secrets
import time
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.settings import Settings
from app.db.base import utcnow
from app.models.brand_manager import BmAccount
from app.security.credentials import resolve, secret_store

log = logging.getLogger("pricechat")

STATE_TTL = 600
USER_FIELDS = "open_id,union_id,avatar_url,display_name,username,bio_description,profile_deep_link,is_verified,follower_count,following_count,likes_count,video_count"
VIDEO_FIELDS = "id,title,video_description,create_time,cover_image_url,share_url,duration,view_count,like_count,comment_count,share_count"
VERIFIER_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"  # RFC 7636 unreserved characters
VERIFIER_LENGTH = 64  # TikTok: 43-128
# What to do about the Content Posting API errors an owner can fix (TikTok's own message is kept after it)
HINTS = {
    "unaudited_client_can_only_post_to_private_accounts": "Until TikTok audits the app, direct posts only work when the TikTok account itself is set to private.",
    "url_ownership_unverified": "TikTok won't fetch the video: verify the domain the video comes from in the TikTok app (Content Posting API › URL properties).",
    "spam_risk_too_many_posts": "This TikTok account has hit its daily posting limit; try again later.",
    "spam_risk_user_banned_from_posting": "TikTok has stopped this account from posting.",
    "privacy_level_option_mismatch": "The chosen privacy option isn't available on this account any more; open the posting screen again.",
    "reached_active_user_cap": "The TikTok app has reached its daily limit of posting users.",
}


@dataclass(frozen=True)
class PendingLogin:
    """One started TikTok sign-in, kept only in server memory (never sent to the browser except as `state`)."""
    brand_id: int
    verifier: str
    session_hash: str  # sha256 of the admin session cookie that started it
    created: float


_pending: dict[str, PendingLogin] = {}  # state -> the sign-in it belongs to


class TikTokError(Exception):
    def __init__(self, message: str, code: str = "error") -> None:
        super().__init__(message)
        self.code = code


def _redirect(settings: Settings) -> str:
    return f"{settings.members_public_url.rstrip('/')}/api/brand-manager/tiktok/callback"


def scopes(settings: Settings) -> list[str]:
    """The scopes to request, from TIKTOK_SCOPES: TikTok wants them comma-separated with no spaces, and rejects the whole
    sign-in (error "scope") if any one isn't enabled on the app or Sandbox. user.info.basic is always required."""
    out: list[str] = []
    for s in settings.tiktok_scopes.replace(" ", ",").split(","):
        if s.strip() and s.strip() not in out:
            out.append(s.strip())
    return out if "user.info.basic" in out else ["user.info.basic", *out]


def app_configured(settings: Settings) -> bool:
    return bool(resolve("tiktok_client_key", settings) and resolve("tiktok_client_secret", settings))


def _tok(brand_id: int, kind: str) -> str:
    return f"bm_tiktok_{brand_id}_{kind}"


def account(db: Session, brand_id: int) -> BmAccount:
    a = db.scalar(select(BmAccount).where(BmAccount.brand_id == brand_id, BmAccount.platform == "tiktok"))
    if a is None:
        a = BmAccount(brand_id=brand_id, platform="tiktok", status="disconnected", scopes=[])
        db.add(a)
        db.commit()
    return a


# --- OAuth -------------------------------------------------------------------------------------------------------------


def pkce_pair() -> tuple[str, str]:
    """(code_verifier, code_challenge) as TikTok's Login Kit requires: a fresh 64-character verifier from the unreserved
    alphabet, and code_challenge = HEX(SHA256(verifier)) — hex, not the base64url most PKCE libraries produce —
    with code_challenge_method S256 (the only method TikTok supports)."""
    verifier = "".join(secrets.choice(VERIFIER_ALPHABET) for _ in range(VERIFIER_LENGTH))
    return verifier, hashlib.sha256(verifier.encode("ascii")).hexdigest()


def _session_hash(session_token: str | None) -> str:
    return hashlib.sha256((session_token or "").encode()).hexdigest()


def authorize_url(settings: Settings, brand_id: int, session_token: str | None = None) -> str:
    if not app_configured(settings):
        raise TikTokError("Add the TikTok app's Client key and Client secret on Settings › API keys first.", "not_configured")
    now = time.monotonic()
    for s, p in list(_pending.items()):
        if now - p.created > STATE_TTL:
            _pending.pop(s, None)
    state = secrets.token_urlsafe(24)
    verifier, challenge = pkce_pair()
    _pending[state] = PendingLogin(brand_id, verifier, _session_hash(session_token), now)
    params = {"client_key": resolve("tiktok_client_key", settings), "scope": ",".join(scopes(settings)), "response_type": "code",
              "redirect_uri": _redirect(settings), "state": state, "code_challenge": challenge, "code_challenge_method": "S256"}
    # Safe to log: the client key is public (it is in every sign-in link); state, PKCE values and secrets are never logged
    log.info("TikTok sign-in started: client_key=%s redirect_uri=%s scopes=%s", params["client_key"], params["redirect_uri"], params["scope"])
    return f"{settings.tiktok_auth_url}?{urlencode(params)}"


async def _post_form(settings: Settings, path: str, data: dict[str, str], transport: Any) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=25.0, transport=transport) as c:
            r = await c.post(f"{settings.tiktok_api_base}{path}", data=data,
                             headers={"Content-Type": "application/x-www-form-urlencoded", "Cache-Control": "no-cache"})
    except httpx.HTTPError as exc:
        raise TikTokError(f"TikTok didn't answer ({type(exc).__name__}).", "unreachable") from None
    try:
        body = r.json()
    except ValueError:
        body = {}
    if r.status_code >= 400 or body.get("error"):
        raise TikTokError(f"TikTok refused: {body.get('error_description') or body.get('error') or r.status_code}", str(body.get("error") or ("unreachable" if r.status_code >= 500 else "auth")))  # a TikTok outage isn't a revoked sign-in
    return body


def _save_tokens(settings: Settings, a: BmAccount, body: dict[str, Any]) -> None:
    store = secret_store(settings)
    store.set(_tok(a.brand_id, "access"), body["access_token"])
    if body.get("refresh_token"):
        store.set(_tok(a.brand_id, "refresh"), body["refresh_token"])
    now = utcnow()
    a.access_expires_at = now + timedelta(seconds=int(body.get("expires_in") or 86400))
    if body.get("refresh_expires_in"):
        a.refresh_expires_at = now + timedelta(seconds=int(body["refresh_expires_in"]))
    if body.get("scope"):
        a.scopes = sorted({s.strip() for s in str(body["scope"]).split(",") if s.strip()})
    if body.get("open_id"):
        a.open_id = body["open_id"]


async def finish(db: Session, settings: Settings, code: str, state: str, transport: Any = None, session_token: str | None = None) -> BmAccount:
    pending = _pending.pop(state, None)  # single use: a replayed callback finds nothing
    if pending is None or time.monotonic() - pending.created > STATE_TTL:
        raise TikTokError("This sign-in link expired or was already used. Start again from Brand Manager › TikTok.", "state")
    if not secrets.compare_digest(pending.session_hash, _session_hash(session_token)):
        raise TikTokError("This TikTok sign-in was started from another dashboard session. Start again from Brand Manager › TikTok.", "state")
    a = account(db, pending.brand_id)
    body = await _post_form(settings, "/v2/oauth/token/", {
        "client_key": resolve("tiktok_client_key", settings) or "", "client_secret": resolve("tiktok_client_secret", settings) or "",
        "code": code, "grant_type": "authorization_code", "redirect_uri": _redirect(settings), "code_verifier": pending.verifier}, transport)
    _save_tokens(settings, a, body)
    log.info("TikTok sign-in finished: scopes granted=%s", ",".join(a.scopes))
    a.status, a.last_error, a.connected_at = "connected", None, utcnow()
    db.commit()
    await refresh_profile(db, settings, a, transport)
    return a


async def access_token(db: Session, settings: Settings, a: BmAccount, transport: Any = None) -> str:
    """A valid access token, refreshing it when it is about to expire. Marks the account expired/revoked if TikTok says so."""
    store = secret_store(settings)
    token = store.get(_tok(a.brand_id, "access"))
    if token and a.access_expires_at and a.access_expires_at > utcnow() + timedelta(minutes=5):
        return token
    refresh = store.get(_tok(a.brand_id, "refresh"))
    if not refresh:
        a.status, a.last_error = "disconnected", "Not connected to TikTok."
        db.commit()
        raise TikTokError("TikTok isn't connected.", "not_connected")
    try:
        body = await _post_form(settings, "/v2/oauth/token/", {
            "client_key": resolve("tiktok_client_key", settings) or "", "client_secret": resolve("tiktok_client_secret", settings) or "",
            "grant_type": "refresh_token", "refresh_token": refresh}, transport)
    except TikTokError as exc:
        if exc.code != "unreachable":
            a.status, a.last_error = "expired", f"TikTok sign-in expired or was revoked ({exc}). Reconnect TikTok."
            db.commit()
        raise
    _save_tokens(settings, a, body)
    if a.status != "connected":
        a.status, a.last_error = "connected", None
    db.commit()
    return body["access_token"]


async def disconnect(db: Session, settings: Settings, brand_id: int, transport: Any = None) -> None:
    a = account(db, brand_id)
    store = secret_store(settings)
    token = store.get(_tok(brand_id, "access"))
    if token and app_configured(settings):
        try:
            await _post_form(settings, "/v2/oauth/revoke/", {"client_key": resolve("tiktok_client_key", settings) or "",
                                                            "client_secret": resolve("tiktok_client_secret", settings) or "", "token": token}, transport)
        except TikTokError:
            pass  # already revoked or unreachable: the local tokens go anyway
    store.delete(_tok(brand_id, "access"))
    store.delete(_tok(brand_id, "refresh"))
    a.status, a.scopes, a.access_expires_at, a.refresh_expires_at, a.creator_info = "disconnected", [], None, None, None
    a.last_error = None
    db.commit()


# Sign-in errors TikTok sends back to the callback that the owner can fix (TikTok's own code is kept after it)
AUTH_HINTS = {
    "non_sandbox_target": "The TikTok account you signed in with isn't a target user of this Sandbox. In the TikTok developer portal open "
                          "the Sandbox › Target users, add that account, accept the invite in the TikTok app, then connect again.",
    "scope": "A requested scope isn't enabled on this TikTok app or Sandbox (check Login Kit, Content Posting API and Display API).",
    "redirect_uri": "The callback address isn't in the app's Login Kit Redirect URI list.",
    "client_key": "TikTok doesn't recognise this client key for sign-in (an unapproved production key, or a Sandbox key with Login Kit missing).",
    "access_denied": "The TikTok sign-in was cancelled.",
}


def auth_error_message(error: str | None, description: str | None) -> str:
    code = (error or "cancelled").strip()
    hint = next((h for k, h in AUTH_HINTS.items() if k in code), None)
    tiktok_says = description or code
    return f"{hint} (TikTok: {tiktok_says})" if hint else tiktok_says


# --- setup diagnostics (owner only; never returns the secret) ----------------------------------------------------------


async def diagnostics(settings: Settings, transport: Any = None) -> dict[str, Any]:
    """Where the client key comes from, what it looks like, and whether TikTok accepts the key + secret as a pair.
    The client key is public (it is in every sign-in link); the secret is reported only as configured / length."""
    import re

    from app.security.credentials import info

    key_raw = resolve("tiktok_client_key", settings) or ""
    secret_raw = resolve("tiktok_client_secret", settings) or ""
    quotes = "\"'"
    key, secret = key_raw.strip().strip(quotes), secret_raw.strip().strip(quotes)
    k_info, s_info = info("tiktok_client_key", settings), info("tiktok_client_secret", settings)
    env_key = settings.tiktok_client_key.get_secret_value().strip() if settings.tiktok_client_key else ""
    problems: list[str] = []
    if not key:
        problems.append("No client key configured.")
    if key_raw != key:
        problems.append("The client key has spaces or quotes around it.")
    if key and secret and key == secret:
        problems.append("The client key and the client secret are the same value: the secret was pasted as the key.")
    if ".apps.googleusercontent.com" in key or key.startswith("GOCSPX"):
        problems.append("The client key looks like a Google OAuth credential, not TikTok's.")
    if key and not re.fullmatch(r"[a-z0-9]{12,40}", key):
        problems.append("The client key doesn't look like a TikTok client key (lowercase letters and digits).")
    if k_info.source == "dashboard" and env_key and env_key != key:
        problems.append("A client key saved on Settings › API keys overrides the TIKTOK_CLIENT_KEY variable, and they differ.")
    pair: dict[str, Any] = {"checked": False}
    if key and secret:
        try:
            async with httpx.AsyncClient(timeout=20.0, transport=transport) as c:
                r = await c.post(f"{settings.tiktok_api_base}/v2/oauth/token/", headers={"Content-Type": "application/x-www-form-urlencoded"},
                                 data={"client_key": key, "client_secret": secret, "grant_type": "client_credentials"})
            body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            pair = {"checked": True, "valid": bool(body.get("access_token")), "tiktok_error": body.get("error"),
                    "tiktok_error_description": body.get("error_description"), "log_id": body.get("log_id")}
            if not pair["valid"]:
                problems.append(f"TikTok rejects this client key + secret pair: {body.get('error_description') or body.get('error') or r.status_code}.")
        except httpx.HTTPError as exc:
            pair = {"checked": False, "error": type(exc).__name__}
    kind = "sandbox app (by prefix)" if key.startswith("sbaw") else "production app (by prefix)" if key.startswith("aw") else "unknown"
    return {
        "client_key": {"configured": bool(key), "source": k_info.source, "value": key or None, "length": len(key), "kind_hint": kind,
                       "env_variable_set": bool(env_key), "dashboard_value_saved": k_info.source == "dashboard"},
        "client_secret": {"configured": bool(secret), "source": s_info.source, "length": len(secret)},
        "pair": pair, "authorize_endpoint": settings.tiktok_auth_url, "token_endpoint": f"{settings.tiktok_api_base}/v2/oauth/token/",
        "redirect_uri": _redirect(settings), "scopes": scopes(settings), "environment": settings.environment,
        "pkce": "S256, hex SHA-256 challenge", "problems": problems,
        "app_side_checks": [
            "Login Kit is added to this app, and its Redirect URI list contains exactly: " + _redirect(settings),
            "If the app is not yet approved (live), only its Sandbox can sign people in: use the Sandbox's own client key/secret "
            "and add your TikTok account under Sandbox › Target users.",
            "The scopes above are enabled on the app (Content Posting API for video.upload/video.publish, Display API for video.list).",
        ],
    }


# --- API calls ------------------------------------------------------------------------------------------------------------


async def _api(db: Session, settings: Settings, a: BmAccount, method: str, path: str, transport: Any = None, *,
               params: dict[str, str] | None = None, json: dict[str, Any] | None = None) -> dict[str, Any]:
    token = await access_token(db, settings, a, transport)
    try:
        async with httpx.AsyncClient(timeout=30.0, transport=transport) as c:
            r = await c.request(method, f"{settings.tiktok_api_base}{path}", params=params, json=json,
                                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8"})
    except httpx.HTTPError as exc:
        raise TikTokError(f"TikTok didn't answer ({type(exc).__name__}).", "unreachable") from None
    try:
        body = r.json()
    except ValueError:
        body = {}
    err = body.get("error") or {}
    code = str(err.get("code") or ("ok" if r.status_code < 400 else r.status_code))
    if code != "ok":
        if code in ("access_token_invalid", "scope_not_authorized", "token_expired"):
            a.status = "expired" if code != "scope_not_authorized" else a.status
            a.last_error = f"{code}: {err.get('message') or ''}".strip()
            db.commit()
        hint = f"{HINTS[code]} " if code in HINTS else ""
        raise TikTokError(f"{hint}TikTok: {err.get('message') or code}", code)
    return body.get("data") or {}


async def refresh_profile(db: Session, settings: Settings, a: BmAccount, transport: Any = None) -> BmAccount:
    fields = USER_FIELDS if "user.info.stats" in a.scopes or not a.scopes else "open_id,union_id,avatar_url,display_name"
    data = await _api(db, settings, a, "GET", "/v2/user/info/", transport, params={"fields": fields})
    u = data.get("user") or {}
    a.open_id = u.get("open_id") or a.open_id
    a.username, a.display_name, a.avatar_url = u.get("username"), u.get("display_name"), u.get("avatar_url")
    a.profile = {k: u.get(k) for k in ("bio_description", "profile_deep_link", "is_verified", "follower_count", "following_count",
                                       "likes_count", "video_count") if k in u}
    db.commit()
    return a


async def creator_info(db: Session, settings: Settings, a: BmAccount, transport: Any = None) -> dict[str, Any]:
    """TikTok requires fresh creator info before every direct post (privacy options, interaction settings, max duration)."""
    data = await _api(db, settings, a, "POST", "/v2/post/publish/creator_info/query/", transport, json={})
    a.creator_info, a.creator_info_at = data, utcnow()
    db.commit()
    return data


async def upload_to_inbox(db: Session, settings: Settings, a: BmAccount, video_url: str, transport: Any = None) -> str:
    data = await _api(db, settings, a, "POST", "/v2/post/publish/inbox/video/init/", transport,
                      json={"source_info": {"source": "PULL_FROM_URL", "video_url": video_url}})
    return str(data["publish_id"])


async def direct_post(db: Session, settings: Settings, a: BmAccount, video_url: str, post: dict[str, Any], transport: Any = None) -> str:
    post_info = {"title": post["title"][:2200], "privacy_level": post["privacy_level"], "disable_comment": not post.get("allow_comment"),
                 "disable_duet": not post.get("allow_duet"), "disable_stitch": not post.get("allow_stitch"),
                 "video_cover_timestamp_ms": 1000, "brand_content_toggle": bool(post.get("branded_content")),
                 "brand_organic_toggle": bool(post.get("your_brand")), "is_aigc": bool(post.get("is_aigc", True))}
    data = await _api(db, settings, a, "POST", "/v2/post/publish/video/init/", transport,
                      json={"post_info": post_info, "source_info": {"source": "PULL_FROM_URL", "video_url": video_url}})
    return str(data["publish_id"])


async def publish_status(db: Session, settings: Settings, a: BmAccount, publish_id: str, transport: Any = None) -> dict[str, Any]:
    return await _api(db, settings, a, "POST", "/v2/post/publish/status/fetch/", transport, json={"publish_id": publish_id})


async def list_videos(db: Session, settings: Settings, a: BmAccount, transport: Any = None, max_pages: int = 3) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    cursor = None
    for _ in range(max_pages):
        body: dict[str, Any] = {"max_count": 20}
        if cursor:
            body["cursor"] = cursor
        data = await _api(db, settings, a, "POST", "/v2/video/list/", transport, params={"fields": VIDEO_FIELDS}, json=body)
        out += data.get("videos") or []
        if not data.get("has_more"):
            break
        cursor = data.get("cursor")
    return out


def account_out(a: BmAccount, settings: Settings) -> dict[str, Any]:
    needed = scopes(settings)
    return {"status": a.status, "username": a.username, "display_name": a.display_name, "avatar_url": a.avatar_url, "open_id": a.open_id,
            "profile": a.profile, "scopes": a.scopes, "missing_scopes": [s for s in needed if a.status == "connected" and s not in a.scopes],
            "can_upload_drafts": "video.upload" in a.scopes, "can_direct_post": "video.publish" in a.scopes,
            "can_read_stats": "video.list" in a.scopes, "access_expires_at": a.access_expires_at.isoformat() if a.access_expires_at else None,
            "refresh_expires_at": a.refresh_expires_at.isoformat() if a.refresh_expires_at else None, "creator_info": a.creator_info,
            "last_error": a.last_error, "connected_at": a.connected_at.isoformat() if a.connected_at else None,
            "app_configured": app_configured(settings), "redirect_uri": _redirect(settings)}
