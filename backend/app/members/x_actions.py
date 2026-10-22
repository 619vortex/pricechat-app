"""X actions a member presses inside Price Chat: a reply or a repost, sent as them with their own X user token.

Rules (X Developer Policy, "Pay to engage": no monetary or virtual compensation for any action on X):
- these actions never earn XP and never touch the XP ledger; a mission card only shows "Done";
- every action is one press by the signed-in member, on one of Price Chat's own posts, and nothing else: no
  automation, no scheduling, no generated text (the member writes every reply);
- Likes are not possible through the X API any more (removed from self-serve tiers in April 2026), so Like opens
  the post on X instead.

Tokens: OAuth 2.0 user context from the member's own "Connect X" sign-in (scopes in auth.X_SCOPES), encrypted at
rest with the app's secret key, refreshed when they expire, never sent to the browser and never logged. A token is
only ever used for the member it belongs to, and only while it still matches their linked X account.
"""

import base64
import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config.settings import Settings
from app.db.base import utcnow
from app.members import xp as ledger
from app.models.enums import Platform
from app.models.members import LinkedIdentity, Member, MemberAction, MemberXToken
from app.models.social import SocialPost
from app.security.credentials import resolve
from app.security.secrets import protect, unprotect

logger = logging.getLogger(__name__)

WRITE_SCOPE = "tweet.write"
MAX_REPLY_CHARS = 280
DAILY_LIMIT = {"x_reply": 10, "x_repost": 20}  # per member per UTC day: a person, not a bot
REFRESH_MARGIN = timedelta(seconds=60)


@dataclass
class ActionResult:
    ok: bool
    code: str  # sent | not_connected | reconnect | rate_limited | limit | not_allowed | invalid | unavailable | failed
    message: str  # plain language, shown to the member
    url: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "code": self.code, "message": self.message, "url": self.url,
                "reconnect": self.code == "reconnect"}


def _enc(value: str) -> str:
    return base64.b64encode(protect(value.encode())).decode()


def _dec(value: str) -> str:
    return unprotect(base64.b64decode(value)).decode()


# --- tokens -------------------------------------------------------------------------------------------------------


def store_tokens(db: Session, member: Member, x_user_id: str, token: dict[str, Any]) -> None:
    """Keep the member's X token after "Connect X" (encrypted). Replaces any earlier one."""
    access = token.get("access_token")
    if not access:
        return
    row = db.scalar(select(MemberXToken).where(MemberXToken.member_id == member.id))
    if row is None:
        row = MemberXToken(member_id=member.id, x_user_id=str(x_user_id), access_token="")
        db.add(row)
    row.x_user_id = str(x_user_id)
    row.access_token = _enc(access)
    row.refresh_token = _enc(token["refresh_token"]) if token.get("refresh_token") else None
    row.expires_at = utcnow() + timedelta(seconds=int(token["expires_in"])) if token.get("expires_in") else None
    row.scopes = str(token.get("scope") or "")[:256]
    row.status, row.last_error = "active", None
    db.commit()


def forget_tokens(db: Session, member: Member) -> None:
    db.query(MemberXToken).filter(MemberXToken.member_id == member.id).delete()
    db.commit()


def tokens_to_revoke(db: Session, member: Member) -> list[tuple[str, str]]:
    """The member's X tokens in plain form, read before the row is deleted, so they can be revoked at X afterwards."""
    row = db.scalar(select(MemberXToken).where(MemberXToken.member_id == member.id))
    if row is None:
        return []
    out = [(_dec(row.refresh_token), "refresh_token")] if row.refresh_token else []
    return out + [(_dec(row.access_token), "access_token")] if row.access_token else out


async def revoke(settings: Settings, tokens: list[tuple[str, str]], transport: httpx.AsyncBaseTransport | None = None) -> bool:
    """Tell X to invalidate the member's tokens (disconnect / account deletion). Best effort: the local copy is
    already gone, so a network problem never blocks a disconnect. Returns True if X confirmed every token."""
    if not tokens:
        return True
    cid, headers = _client_auth(settings)
    ok = True
    try:
        async with httpx.AsyncClient(timeout=10.0, transport=transport) as client:
            for token, hint in tokens:
                r = await client.post(settings.x_oauth2_revoke_url, headers=headers,
                                      data={"token": token, "token_type_hint": hint, "client_id": cid})
                ok = ok and r.status_code == 200
    except httpx.HTTPError:
        ok = False
    if not ok:
        logger.warning("Member X token revoke was not confirmed by X; the local token is deleted regardless")
    return ok


def _linked_x(db: Session, member: Member) -> LinkedIdentity | None:
    return db.scalar(select(LinkedIdentity).where(LinkedIdentity.member_id == member.id, LinkedIdentity.platform == "x",
                                                  LinkedIdentity.status == "active"))


def status(db: Session, member: Member, write_enabled: bool = True) -> dict[str, Any]:
    """What the Passport needs to know: is X linked, and can Price Chat send a reply / repost the member presses. With write
    access off (settings.x_member_write), a read-only link is complete: nobody is asked to reconnect for it."""
    ident = _linked_x(db, member)
    row = db.scalar(select(MemberXToken).where(MemberXToken.member_id == member.id))
    ok = bool(write_enabled and ident and row and row.status == "active" and row.x_user_id == ident.platform_user_id
              and WRITE_SCOPE in row.scopes.split())
    return {"connected": ident is not None, "username": ident.username if ident else None, "can_act": ok,
            "needs_reconnect": bool(ident) and write_enabled and not ok}


def admin_status(db: Session, member: Member) -> dict[str, Any] | None:
    """For the team: connection state and scopes. Never the tokens."""
    row = db.scalar(select(MemberXToken).where(MemberXToken.member_id == member.id))
    if row is None:
        return None
    return {"x_user_id": row.x_user_id, "scopes": row.scopes.split(), "status": row.status, "last_error": row.last_error,
            "expires_at": row.expires_at.isoformat() if row.expires_at else None,
            "has_refresh_token": bool(row.refresh_token), "connected_at": row.created_at.isoformat(),
            "last_used_at": row.last_used_at.isoformat() if row.last_used_at else None}


def _client_auth(settings: Settings) -> tuple[str, dict[str, str]]:
    cid = resolve("x_member_client_id", settings) or ""
    secret = resolve("x_member_client_secret", settings)
    headers = {"Authorization": "Basic " + base64.b64encode(f"{cid}:{secret}".encode()).decode()} if secret else {}
    return cid, headers


def _needs_reconnect(db: Session, row: MemberXToken, why: str,
                     message: str = "X needs you to reconnect your account before Price Chat can do this.") -> ActionResult:
    row.status, row.last_error = "reconnect", why[:256]
    db.commit()
    return ActionResult(False, "reconnect", message)


async def _refresh(db: Session, settings: Settings, row: MemberXToken, client: httpx.AsyncClient) -> bool:
    if not row.refresh_token:
        return False
    cid, headers = _client_auth(settings)
    try:
        r = await client.post(settings.x_oauth2_token_url, headers=headers, data={
            "grant_type": "refresh_token", "refresh_token": _dec(row.refresh_token), "client_id": cid})
    except httpx.HTTPError:
        return False
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if r.status_code != 200 or not body.get("access_token"):
        return False
    row.access_token = _enc(body["access_token"])
    if body.get("refresh_token"):  # X rotates refresh tokens
        row.refresh_token = _enc(body["refresh_token"])
    row.expires_at = utcnow() + timedelta(seconds=int(body["expires_in"])) if body.get("expires_in") else None
    if body.get("scope"):
        row.scopes = str(body["scope"])[:256]
    db.commit()
    return True


def _explain(resp: httpx.Response) -> tuple[str, str]:
    """X's answer, in plain language. (code, message). Details stay in the server log, never tokens."""
    try:
        body = resp.json()
    except ValueError:
        body = {}
    text = f"{body.get('title', '')} {body.get('detail', '')} {body.get('reason', '')} {body.get('type', '')}".lower()
    if resp.status_code == 429:
        return "rate_limited", "X is limiting how fast this can happen. Please try again in a few minutes."
    if resp.status_code == 401:
        return "reconnect", "X needs you to reconnect your account before Price Chat can do this."
    if resp.status_code == 403 and "duplicate" in text:
        return "invalid", "X didn't accept it: it's the same as something you already posted."
    if resp.status_code == 403 and ("scope" in text or "permission" in text and "oauth" in text):
        return "reconnect", "X needs permission to post for you. Reconnect X and allow it."
    if resp.status_code == 403 and any(k in text for k in ("client-not-enrolled", "credits", "access level", "not permitted to access")):
        return "unavailable", "Price Chat can't do this on X right now. You can still do it on X directly."
    if resp.status_code in (403, 404) and ("deleted" in text or "not found" in text or resp.status_code == 404):
        return "not_allowed", "That post isn't available on X any more."
    return "failed", "X didn't confirm it, so nothing was marked as done. Please try again."


def _sent_today(db: Session, member: Member, kind: str) -> int:
    day = ledger.day_of(utcnow())
    return int(db.scalar(select(func.count()).select_from(MemberAction).where(
        MemberAction.member_id == member.id, MemberAction.kind == kind, MemberAction.day == day)) or 0)


def _own_post(db: Session, post_id: str) -> SocialPost | None:
    """Only Price Chat's own posts can be the target of an action from this site."""
    return db.scalar(select(SocialPost).where(SocialPost.platform == Platform.X, SocialPost.external_id == post_id,
                                              SocialPost.is_own.is_(True)))


async def perform(db: Session, settings: Settings, member: Member, kind: str, post_id: str, text: str | None = None,
                  transport: httpx.AsyncBaseTransport | None = None) -> ActionResult:
    """One action the signed-in member pressed. `member` always comes from the session, never from the request."""
    if kind not in DAILY_LIMIT:
        return ActionResult(False, "invalid", "Unknown action.")
    ident = _linked_x(db, member)
    row = db.scalar(select(MemberXToken).where(MemberXToken.member_id == member.id))
    if ident is None:
        return ActionResult(False, "not_connected", "Connect X first.")
    if row is None or row.x_user_id != ident.platform_user_id or WRITE_SCOPE not in row.scopes.split():
        return ActionResult(False, "reconnect", "Reconnect X to let Price Chat send this for you.")
    if row.status != "active":
        return ActionResult(False, "reconnect", "X needs you to reconnect your account before Price Chat can do this.")
    post = _own_post(db, post_id)
    if post is None:
        return ActionResult(False, "not_allowed", "Only Price Chat's own posts can be replied to or reposted from here.")
    body = None
    if kind == "x_reply":
        body = (text or "").strip()
        if not body:
            return ActionResult(False, "invalid", "Write your reply first.")
        if len(body) > MAX_REPLY_CHARS:
            return ActionResult(False, "invalid", f"Replies can be up to {MAX_REPLY_CHARS} characters.")
    already = db.scalar(select(MemberAction.id).where(MemberAction.member_id == member.id, MemberAction.kind == kind,
                                                      MemberAction.target == post_id))
    if kind == "x_repost" and already:
        return ActionResult(True, "sent", "Reposted", post.url)  # already: X keeps one repost per account
    if _sent_today(db, member, kind) >= DAILY_LIMIT[kind]:
        return ActionResult(False, "limit", "That's enough for today. You can do more tomorrow.")

    base = settings.x_api_base.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=20.0, transport=transport) as client:
            if row.expires_at and row.expires_at - REFRESH_MARGIN < utcnow() and not await _refresh(db, settings, row, client):
                return _needs_reconnect(db, row, "token expired and could not be refreshed")
            for attempt in (1, 2):
                headers = {"Authorization": f"Bearer {_dec(row.access_token)}"}
                if kind == "x_reply":
                    resp = await client.post(f"{base}/2/tweets", headers=headers,
                                             json={"text": body, "reply": {"in_reply_to_tweet_id": post_id}})
                else:
                    resp = await client.post(f"{base}/2/users/{row.x_user_id}/retweets", headers=headers, json={"tweet_id": post_id})
                if resp.status_code == 401 and attempt == 1 and await _refresh(db, settings, row, client):
                    continue  # the token had expired early: refreshed, try once more
                break
    except httpx.HTTPError:
        return ActionResult(False, "failed", "Could not reach X. Nothing was sent. Please try again.")

    data = (resp.json() or {}).get("data") if resp.headers.get("content-type", "").startswith("application/json") else None
    confirmed = resp.status_code in (200, 201) and isinstance(data, dict) and (
        data.get("id") if kind == "x_reply" else data.get("retweeted") is True)
    if not confirmed:
        code, message = _explain(resp)
        logger.info("Member X %s refused: member=%s status=%s code=%s", kind, member.id, resp.status_code, code)
        if code == "reconnect":
            return _needs_reconnect(db, row, f"X answered {resp.status_code} to a {kind}", message)
        row.last_error = f"{kind}: X answered {resp.status_code} ({code})"
        db.commit()
        return ActionResult(False, code, message)

    # Confirmed by X: remember it (for "Done" and the daily limit) and audit it. No XP: see the module docstring.
    row.last_used_at, row.last_error = utcnow(), None
    db.add(MemberAction(member_id=member.id, kind=kind, platform="x", target=post_id[:128], day=ledger.day_of(utcnow())))
    ledger.audit(db, member.id, "member", kind, {"post_id": post_id, "result_id": data.get("id") if kind == "x_reply" else None})
    from app.members.missions import mark_x_action_done

    mark_x_action_done(db, member, kind, post_id)
    db.commit()
    if kind == "x_reply":
        handle = ident.username or "i"
        return ActionResult(True, "sent", "Replied", f"https://x.com/{handle}/status/{data['id']}")
    return ActionResult(True, "sent", "Reposted", post.url)
