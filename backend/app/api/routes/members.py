"""Public member API: sign-in (email code; Google / X / Telegram kept for existing accounts and for connecting
accounts after sign-in), session, Kiwi Passport, privacy controls, leaderboard.

Cookies: `pc_session` (signed-in browser; only its hash is stored) and `pc_auth` (binds a sign-in attempt to
the browser that started it). Both httpOnly, SameSite=Lax. No OAuth token ever reaches the browser or the DB.
"""

import logging
from urllib.parse import parse_qs, urlparse
import secrets
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Cookie, HTTPException, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import DbSession, Runtime
from app.api.schemas import StrictInput
from app.config.settings import Settings
from app.members import auth, insights, leaderboard as boards, missions, notify, referrals, reputation, service, telegram_missions, watch, x_actions, x_feed
from app.members.catalog import ensure_catalog
from app.members.service import MemberError
from app.models.members import Member, Mission

router = APIRouter(tags=["members"])
logger = logging.getLogger(__name__)

SESSION_COOKIE = "pc_session"
AUTH_COOKIE = "pc_auth"
REF_COOKIE = "pc_ref"  # an invite code from /website/join/<code>; read only when a NEW member is created
JOIN_PAGE = "/website/join"
PROFILE_PAGE = "/passport"  # the Kiwi Passport (the old /website/profile still works)

SessionToken = Annotated[str | None, Cookie(alias=SESSION_COOKIE)]
AuthToken = Annotated[str | None, Cookie(alias=AUTH_COOKIE)]
RefCode = Annotated[str | None, Cookie(alias=REF_COOKIE)]


def _secure(settings: Settings) -> bool:
    return settings.members_public_url.startswith("https://")


def _set_session(response: Response, settings: Settings, token: str) -> None:
    response.set_cookie(SESSION_COOKIE, token, max_age=settings.member_session_days * 86400, httponly=True, samesite="lax",
                        secure=_secure(settings), path="/")


def _browser_binding(response: Response, settings: Settings, existing: str | None) -> str:
    token = existing or secrets.token_urlsafe(24)
    response.set_cookie(AUTH_COOKIE, token, max_age=1800, httponly=True, samesite="lax", secure=_secure(settings), path="/")
    return token


def _current(db: Session, token: str | None) -> Member | None:
    return service.member_for_token(db, token)


def _require(db: Session, token: str | None) -> Member:
    member = _current(db, token)
    if member is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Please sign in.")
    return member


def _fail(error: Exception) -> HTTPException:
    return HTTPException(status.HTTP_400_BAD_REQUEST, str(error))


# --- session & passport -----------------------------------------------------------------------------------------


@router.get("/members/session")
def session(response: Response, db: DbSession, runtime: Runtime, pc_session: SessionToken = None) -> dict[str, Any]:
    """Who is signed in (for the website header). Counts the daily visit once per UTC day. A session past half its
    life is renewed (same token, new expiry) so active members stay signed in; idle ones expire."""
    member = _current(db, pc_session)
    if member is None:
        return {"signed_in": False, "member": None}
    if pc_session and service.renew_session(db, pc_session, runtime.settings.member_session_days):
        _set_session(response, runtime.settings, pc_session)
    service.daily_visit(db, member)
    return {"signed_in": True, "member": {**service.header_out(db, member), "username_chosen": member.username_chosen}}


def _links(db: Session) -> dict[str, str | None]:
    from app.api.routes.website import _links as site_links, _settings

    return site_links(db, _settings(db))


@router.get("/members/me")
def me(db: DbSession, runtime: Runtime, pc_session: SessionToken = None) -> dict[str, Any]:
    """The Kiwi Passport: rank and progress, up to 3 missions for today (+ the rest), recent XP, linked accounts.
    The extra blocks (activity, breakdown, notifications) stay available for the Details views."""
    member = _require(db, pc_session)
    links = _links(db)
    x_status = x_actions.status(db, member, runtime.settings.x_member_write)
    mission_list = missions.for_member(db, member, links.get("telegram"), x_can_act=x_status["can_act"], x_write=runtime.settings.x_member_write)
    first, more = missions.today(mission_list)
    out = service.profile(db, member)
    from app.members import streak

    out["streak"] = streak.status(db, member)
    from app.members import packs

    out["level_packs"] = packs.level_packs(db, member)  # one Pokémon pack per Kiwi Passport level reached
    from app.workers.service import public_roles

    out["roles"] = public_roles(db, member.id)  # Worker System: WORKER / MANAGER (next to the rank, never instead of it)
    notes = notify.unread(db, member.id)
    rank_up = next((n for n in notes if n.kind == "rank_up" and n.read_at is None), None)
    out.update({
        "missions": mission_list,
        "today": first,
        "more_missions": more,
        "x": x_status,
        "next_steps": insights.next_steps(mission_list),
        "activity": insights.activity(db, member),
        "breakdown": insights.breakdown(db, member),
        "telegram": insights.telegram_activity(db, member),
        "public_profile": member.public_profile,
        "reputation": reputation.explain(db, member),
        "notifications": {"unread": sum(1 for n in notes if n.read_at is None),
                          "items": [{"id": n.id, "kind": n.kind, "title": n.title, "body": n.body, "at": n.created_at.isoformat(),
                                     "read": n.read_at is not None} for n in notes]},
        "rank_up": {"id": rank_up.id, "title": rank_up.title, "level": rank_up.data.get("level")} if rank_up else None,
        "standing": boards.standing(db, member),  # the member's all-time place, for "moved up to #N"
    })
    return out


@router.get("/members/feed")
def member_feed(db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    """Latest from Price Chat (X posts, conversations, videos, Telegram), with official platform links."""
    _require(db, pc_session)
    return insights.feed(db, _links(db))


@router.post("/members/missions/{mission_id}/open")
def open_mission(mission_id: int, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    member = _require(db, pc_session)
    m = db.get(Mission, mission_id)
    if m is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Mission not found")
    return missions.open_mission(db, member, m)


@router.post("/members/missions/{mission_id}/claim")
def claim_mission(mission_id: int, db: DbSession, runtime: Runtime, pc_session: SessionToken = None) -> dict[str, Any]:
    """The member says they did it: Price Chat looks for proof. Nothing is paid until it is verified."""
    member = _require(db, pc_session)
    m = db.get(Mission, mission_id)
    if m is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Mission not found")
    try:
        return missions.claim_mission(db, member, m, runtime.scheduler)
    except ValueError as exc:
        raise _fail(exc) from exc


@router.post("/members/missions/{mission_id}/verify")
async def verify_mission(mission_id: int, db: DbSession, runtime: Runtime, pc_session: SessionToken = None) -> dict[str, Any]:
    """"Join the channel": ask Telegram (via our bot) whether the member's linked account is in it. XP only on a yes."""
    member = _require(db, pc_session)
    m = db.get(Mission, mission_id)
    if m is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Mission not found")
    from app.telegram.errors import TelegramError
    from app.telegram.service import make_client

    try:
        client = make_client(runtime.settings, runtime.telegram.transport)
    except TelegramError:
        return {"ok": False, "code": "unavailable", "message": "Telegram isn't connected right now."}
    try:
        out = await telegram_missions.verify_join(db, member, m, client)
    finally:
        await client.aclose()
    return out


class XAction(StrictInput):
    post_id: str = Field(min_length=1, max_length=32, pattern=r"^\d+$")
    text: str | None = Field(None, max_length=1000)


@router.post("/members/me/x/{action}")
async def x_action(action: Literal["reply", "repost"], payload: XAction, db: DbSession, runtime: Runtime,
                   pc_session: SessionToken = None) -> dict[str, Any]:
    """A reply or repost the member pressed, sent as them (their own X token). Never pays XP (X's developer policy).
    The member is always the session's member: a request can never act with anyone else's token."""
    member = _require(db, pc_session)
    if action == "reply" and not (payload.text or "").strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Write your reply first.")
    result = await x_actions.perform(db, runtime.settings, member, f"x_{action}", payload.post_id, payload.text,
                                     transport=runtime.members_transport)
    return result.as_dict()


class SiteAction(StrictInput):
    kind: Literal["open", "like_intent", "reply_intent", "repost_intent", "share", "comment_intent", "subscribe_intent"]
    platform: Literal["x", "youtube", "telegram", "website"]
    target: str = Field(min_length=1, max_length=128)


@router.post("/members/me/actions")
def site_action(payload: SiteAction, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    """A click on this site (tracked separately from anything verified on the platform). Never pays XP by itself."""
    member = _current(db, pc_session)
    if member is None:
        return {"recorded": False}
    recorded = missions.record_action(db, member, payload.kind, payload.platform, payload.target)
    missions.sync_event_missions(db, member)
    db.commit()
    return {"recorded": recorded}


class ReadNotifications(StrictInput):
    ids: list[int] | None = None


@router.post("/members/me/notifications/read")
def read_notifications(payload: ReadNotifications, db: DbSession, pc_session: SessionToken = None) -> dict[str, bool]:
    notify.mark_read(db, _require(db, pc_session).id, payload.ids)
    return {"ok": True}


@router.get("/members/public/{username}")
def public_profile(username: str, db: DbSession) -> dict[str, Any]:
    """A member's public page, only if they switched it on. Unknown and private look the same (404)."""
    out = insights.public_profile(db, username)
    if out is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No public profile")
    return out


@router.get("/members/me/xp")
def my_xp(db: DbSession, pc_session: SessionToken = None, limit: int = Query(200, ge=1, le=1000)) -> dict[str, Any]:
    """Every XP change on the member's account, newest first, each with its source and reason."""
    return {"events": service.profile(db, _require(db, pc_session), recent=limit)["recent"]}


class ProfileUpdate(StrictInput):
    username: str | None = Field(None, max_length=32)
    display_name: str | None = Field(None, max_length=64)
    leaderboard_opt_out: bool | None = None
    public_profile: bool | None = None


@router.patch("/members/me")
def update_me(payload: ProfileUpdate, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    member = _require(db, pc_session)
    try:
        service.update_profile(db, member, username=payload.username, display_name=payload.display_name,
                               leaderboard_opt_out=payload.leaderboard_opt_out, public_profile=payload.public_profile)
    except MemberError as exc:
        raise _fail(exc) from exc
    return service.profile(db, member)


class DeleteRequest(StrictInput):
    confirm_username: str


@router.delete("/members/me")
async def delete_me(payload: DeleteRequest, response: Response, db: DbSession, runtime: Runtime,
                    pc_session: SessionToken = None) -> dict[str, Any]:
    member = _require(db, pc_session)
    if payload.confirm_username != member.username:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Type your username exactly to confirm.")
    x_tokens = x_actions.tokens_to_revoke(db, member)
    service.delete_member(db, member)
    await x_actions.revoke(runtime.settings, x_tokens, runtime.members_transport)  # after the local copy is gone
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"deleted": True}


@router.post("/members/me/disconnect/{platform}")
async def disconnect(platform: Literal["telegram", "x", "google"], db: DbSession, runtime: Runtime,
                     pc_session: SessionToken = None) -> dict[str, Any]:
    member = _require(db, pc_session)
    x_tokens = x_actions.tokens_to_revoke(db, member) if platform == "x" else []
    try:
        service.disconnect(db, member, platform)
    except MemberError as exc:
        raise _fail(exc) from exc
    await x_actions.revoke(runtime.settings, x_tokens, runtime.members_transport)  # after the local copy is gone
    return service.profile(db, member)


class ContentView(StrictInput):
    external_id: str = Field(min_length=1, max_length=64)


@router.post("/members/me/content-view")
def content_view(payload: ContentView, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    """The member opened a Price Chat video from the website (not "watched": YouTube does not tell us that)."""
    member = _current(db, pc_session)
    if member is None:
        return {"awarded": False, "xp": 0, "refused": "signed_out"}
    try:
        r = service.content_view(db, member, payload.external_id)
    except MemberError as exc:
        raise _fail(exc) from exc
    return {"awarded": r.awarded, "xp": r.xp, "refused": r.refused}


class WatchStart(StrictInput):
    external_id: str = Field(min_length=1, max_length=64)


class WatchDone(StrictInput):
    external_id: str = Field(min_length=1, max_length=64)
    played_seconds: float = Field(ge=0, le=86_400)  # what the embedded player measured in this browser


@router.get("/members/me/watch")
def watch_list(db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    """Watch & Earn: the newest Price Chat videos, playable in the Passport, with what each can still pay."""
    return watch.videos(db, _require(db, pc_session))


@router.post("/members/me/watch/start")
def watch_start(payload: WatchStart, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    member = _require(db, pc_session)
    try:
        return watch.start(db, member, payload.external_id)
    except watch.WatchError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.post("/members/me/watch/complete")
def watch_complete(payload: WatchDone, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    """Pays once the player has played the configured seconds here AND that much time passed since Watch was pressed."""
    member = _require(db, pc_session)
    try:
        return watch.complete(db, member, payload.external_id, payload.played_seconds)
    except watch.WatchError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


class FeedPost(StrictInput):
    post_id: str = Field(min_length=1, max_length=32, pattern=r"^\d+$")


@router.get("/members/me/x-feed")
def x_feed_list(db: DbSession, runtime: Runtime, pc_session: SessionToken = None) -> dict[str, Any]:
    """Read & Earn: Price Chat's latest X posts and replies, one at a time (reading here earns; actions on X don't)."""
    member = _require(db, pc_session)
    return {**x_feed.feed(db, member), "x": x_actions.status(db, member, runtime.settings.x_member_write)}


def _feed(fn, *args):  # type: ignore[no-untyped-def]
    try:
        return fn(*args)
    except x_feed.FeedError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.post("/members/me/x-feed/shown")
def x_feed_shown(payload: FeedPost, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    return _feed(x_feed.shown, db, _require(db, pc_session), payload.post_id)


@router.post("/members/me/x-feed/read")
def x_feed_read(payload: FeedPost, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    """Pays once the post has been shown here for the read time (server clock), once per post, within the daily cap."""
    return _feed(x_feed.read, db, _require(db, pc_session), payload.post_id)


@router.post("/members/me/x-feed/like-opened")
def x_feed_like_opened(payload: FeedPost, db: DbSession, pc_session: SessionToken = None) -> dict[str, bool]:
    _feed(x_feed.like_opened, db, _require(db, pc_session), payload.post_id)
    return {"ok": True}


@router.post("/members/logout")
def logout(response: Response, db: DbSession, pc_session: SessionToken = None) -> dict[str, bool]:
    service.end_session(db, pc_session)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"signed_in": False}


# --- public reference -------------------------------------------------------------------------------------------


@router.get("/members/leaderboard")
def leaderboard(db: DbSession, period: Literal["week", "month", "all"] = "all",
                metric: Literal["score", "xp", "legacy", "telegram", "referrals"] = "score", limit: int = Query(100, ge=1, le=100),
                pc_session: SessionToken = None) -> dict[str, Any]:
    """Top members plus, when signed in, the member's own place even outside the list (computed here, never in the browser).
    score = Kiwi Rank Score (all time) / XP earned in the period; referrals = qualified invites."""
    return {"period": period, "metric": metric, **boards.board(db, period, metric, limit, _current(db, pc_session))}


# --- invites ----------------------------------------------------------------------------------------------------


@router.get("/members/me/referrals")
def my_referrals(db: DbSession, runtime: Runtime, pc_session: SessionToken = None) -> dict[str, Any]:
    member = _require(db, pc_session)
    code = referrals.code_for(db, member)
    return {"code": code, "link": referrals.invite_link(runtime.settings.members_public_url, code), **referrals.stats(db, member)}


@router.get("/members/me/crew")
def my_crew(db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    """Invite your crew: the member's personal Telegram bot invite, Telegram's share link, and verified friends / XP."""
    from app.members import crew
    from app.telegram.ingestion import get_bot_state

    member = _require(db, pc_session)
    bot = get_bot_state(db).bot_username or "cryptopricechatbot"
    out = crew.stats(db, member, bot)
    db.commit()
    return out


class ReferralVisit(StrictInput):
    code: str = Field(min_length=1, max_length=24)


@router.post("/members/referrals/visit")
def referral_visit(payload: ReferralVisit, request: Request, response: Response, db: DbSession, runtime: Runtime,
                   pc_session: SessionToken = None) -> dict[str, Any]:
    """Someone opened an invite link: remember the code in this browser for when they create their Passport. Never XP."""
    code = referrals.normalise(payload.code)
    owner = referrals.owner_of(db, code)
    if code is None or owner is None or not referrals.program(db).referrals_enabled:
        return {"valid": False}
    me = _current(db, pc_session)
    if me is None:  # a signed-in member opening an invite changes nothing
        from app.security import rate_limit

        if rate_limit.retry_after("referral", request) is None:  # a script replaying the link isn't counted (like /api/r/ clicks)
            referrals.record_visit(db, code)
        response.set_cookie(REF_COOKIE, code, max_age=30 * 86400, httponly=True, samesite="lax", secure=_secure(runtime.settings), path="/")
    return {"valid": True, "welcome_xp": referrals.stats(db, owner)["welcome_xp"], "signed_in": me is not None}


@router.get("/members/ranks")
def ranks(db: DbSession) -> dict[str, Any]:
    ensure_catalog(db)
    from app.members import xp as ledger

    return {"ranks": [service.rank_out(r) for r in ledger.ranks(db)]}


@router.get("/members/rules")
def rules(db: DbSession) -> dict[str, Any]:
    """How XP is earned, including what is not available and why. Public, for transparency."""
    ensure_catalog(db)
    return {"rules": [r for r in service.rules_out(db) if r["category"] == "reputation"]}


@router.get("/members/auth/providers")
def providers(db: DbSession, runtime: Runtime) -> dict[str, Any]:
    from app.services import mailer

    return {**auth.provider_status(db, runtime.settings),
            "email": {"available": mailer.available(runtime.settings), "code_minutes": runtime.settings.email_code_minutes,
                      "note": "" if mailer.available(runtime.settings) else "Email sign-in isn't set up yet (Resend API key)."}}


# --- sign-in: email code (the website's sign-in) -----------------------------------------------------------------


def _client_ip(request: Request) -> str:
    """The visitor's rate-limit address (app.security.client_ip; an IPv6 visitor counts as its /64)."""
    from app.security import client_ip

    return client_ip.client_key(request)


class EmailStart(StrictInput):
    email: str = Field(min_length=3, max_length=254)
    purpose: Literal["login", "link"] = "login"


class EmailVerify(StrictInput):
    email: str = Field(min_length=3, max_length=254)
    code: str = Field(min_length=6, max_length=16)


@router.post("/members/auth/email/start")
async def email_start(payload: EmailStart, request: Request, response: Response, db: DbSession, runtime: Runtime,
                      pc_session: SessionToken = None, pc_auth: AuthToken = None) -> Any:
    """Email a 6-digit code. The same answer whether or not the address has an account."""
    from fastapi.responses import JSONResponse

    from app.members import email_login

    browser = _browser_binding(response, runtime.settings, pc_auth)
    current = _current(db, pc_session) if payload.purpose == "link" else None
    try:
        out = await email_login.start(db, runtime.settings, payload.email, _client_ip(request), browser, payload.purpose, current,
                                      getattr(runtime, "members_transport", None))
    except email_login.TooSoon as exc:
        r = JSONResponse(status_code=429, content={"detail": str(exc), "retry_after": exc.retry_after}, headers={"Retry-After": str(exc.retry_after)})
        r.set_cookie(AUTH_COOKIE, browser, max_age=1800, httponly=True, samesite="lax", secure=_secure(runtime.settings), path="/")
        return r
    except MemberError as exc:
        raise _fail(exc) from exc
    return out


@router.post("/members/auth/email/verify")
def email_verify(payload: EmailVerify, response: Response, db: DbSession, runtime: Runtime, pc_session: SessionToken = None,
                 pc_auth: AuthToken = None, pc_ref: RefCode = None) -> dict[str, Any]:
    """Check the code. Returning member: signed in. New address: a new Kiwi Passport (choose a username next).
    purpose "link": the address is added to the signed-in member."""
    from app.members import email_login

    current = _current(db, pc_session)
    try:
        member, created = email_login.verify(db, runtime.settings, payload.email, payload.code, pc_auth, current, referral_code=pc_ref)
    except MemberError as exc:
        raise _fail(exc) from exc
    if current is None or current.id != member.id:
        if pc_session:
            service.end_session(db, pc_session)  # a different account in this browser: the old session ends
        _set_session(response, runtime.settings, service.new_session(db, member, runtime.settings.member_session_days))
    response.delete_cookie(AUTH_COOKIE, path="/")
    if created:
        response.delete_cookie(REF_COOKIE, path="/")
    return {"signed_in": True, "created": created, "needs_username": not member.username_chosen,
            "member": {**service.header_out(db, member), "username_chosen": member.username_chosen}}


class ReviewerSignIn(StrictInput):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=200)


@router.post("/members/auth/reviewer")
def reviewer_sign_in(payload: ReviewerSignIn, request: Request, response: Response, db: DbSession, runtime: Runtime, pc_session: SessionToken = None) -> dict[str, bool]:
    """The store-review account's email and password (app/vpn/reviewer.py). Refuses everything unless it's configured."""
    from app.security.rate_limit import LIMITS
    from app.vpn import reviewer

    wait = LIMITS["vpn_reviewer"].hit(_client_ip(request))
    if wait is not None:
        raise HTTPException(429, "Too many tries. Please wait a few minutes.", headers={"Retry-After": str(wait)})
    try:
        member = reviewer.sign_in(db, runtime.settings, payload.email, payload.password, current=_current(db, pc_session))  # the same browser keeps its seat
    except MemberError as exc:
        raise HTTPException(401, str(exc)) from exc
    if pc_session:
        service.end_session(db, pc_session)
    _set_session(response, runtime.settings, service.new_session(db, member, 30, reviewer.REVIEW_TOKEN_PREFIX, kind="reviewer"))  # scoped: reviewer.review_token_may
    return {"signed_in": True}


class UsernameCheck(StrictInput):
    username: str = Field(min_length=1, max_length=32)


@router.post("/members/username/check")
def username_check(payload: UsernameCheck, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    """Is this username free? (For the signed-in member choosing theirs; an answer about one name at a time.)"""
    member = _require(db, pc_session)
    problem = service.username_problem(db, payload.username.strip(), member)
    return {"ok": problem is None, "problem": problem}


# --- sign-in: Google & X (redirect flows) -----------------------------------------------------------------------


def _finish(db: Session, settings: Settings, identity: dict[str, Any], purpose: str, pc_session: str | None,
            x_token: dict[str, Any] | None = None, started_by: int | None = None, ref: str | None = None,
            browser: str | None = None) -> RedirectResponse:
    current = _current(db, pc_session)
    platform = identity.get("platform", "")
    if purpose == "link" and (current is None or current.id != started_by):
        # The callback must come back to the member who pressed Connect, in the same signed-in browser.
        logger.warning("Member %s link refused: callback session does not match the member who started it", platform)
        return _error_page(purpose, "Please connect from your own Kiwi Passport, signed in, in this browser.", "session_mismatch", platform)
    try:
        member = service.sign_in_with(db, identity, purpose, current, referral_code=ref, browser=browser)
    except MemberError as exc:
        code = "identity_conflict" if "already linked to another" in str(exc) else "error"
        return _error_page(purpose, str(exc), code, platform)
    if x_token and identity.get("platform") == "x":
        x_actions.store_tokens(db, member, identity["id"], x_token)  # the member's own token, encrypted
    resp = RedirectResponse(f"{PROFILE_PAGE}?{'linked' if purpose == 'link' else 'welcome'}={identity['platform']}", status_code=303)
    if purpose == "login":
        _set_session(resp, settings, service.new_session(db, member, settings.member_session_days))
    resp.delete_cookie(AUTH_COOKIE, path="/")
    if purpose == "login":
        resp.delete_cookie(REF_COOKIE, path="/")
    return resp


def _q(text: str) -> str:
    from urllib.parse import quote

    return quote(text[:200])


def _error_page(purpose: str, message: str, code: str = "error", platform: str = "") -> RedirectResponse:
    """Back to the page the member came from, with a plain message the page shows next to the right button.
    Technical details go to the server log only."""
    if code not in ("cancelled",):
        logger.warning("Member sign-in (%s, %s) failed: code=%s message=%s", platform or "?", purpose, code, message)
    return RedirectResponse(f"{PROFILE_PAGE if purpose == 'link' else JOIN_PAGE}?error={_q(message)}&code={_q(code)}&platform={_q(platform)}",
                            status_code=303)


SLOW_DOWN = "Too many sign-in attempts from here. Please wait a minute and try again."


def _signin_flood(request: Request) -> bool:
    """Over the "signin" rate limit? Each start stores a pending attempt, so they are limited per visitor."""
    from app.security import rate_limit

    return rate_limit.retry_after("signin", request) is not None


def _purpose_of(db: Session, provider: str, state: str) -> str:
    """Which page to send an error back to (the attempt's purpose), without consuming the attempt."""
    if not state:
        return "login"
    from app.models.members import MemberAuthRequest

    req = db.scalar(select(MemberAuthRequest).where(MemberAuthRequest.state_hash == auth._h(state), MemberAuthRequest.provider == provider))
    return req.purpose if req else "login"


@router.get("/members/auth/google/start")
def google_start(request: Request, db: DbSession, runtime: Runtime, purpose: Literal["login", "link"] = "login",
                 pc_session: SessionToken = None, pc_auth: AuthToken = None) -> RedirectResponse:
    if _signin_flood(request):
        return _error_page(purpose, SLOW_DOWN, "rate_limited", "google")
    resp = RedirectResponse("about:blank", status_code=303)
    browser = _browser_binding(resp, runtime.settings, pc_auth)
    try:
        url = auth.google_start(db, runtime.settings, purpose, browser, _current(db, pc_session))
    except MemberError as exc:
        return _error_page(purpose, str(exc), "start_failed", "google")
    resp.headers["location"] = url
    return resp


@router.get("/members/auth/google/callback")
async def google_callback(db: DbSession, runtime: Runtime, state: str = "", code: str = "", error: str | None = None,
                          pc_session: SessionToken = None, pc_auth: AuthToken = None, pc_ref: RefCode = None) -> RedirectResponse:
    purpose = _purpose_of(db, "google", state)
    if error == "access_denied":
        return _error_page(purpose, "You cancelled connecting Google. Nothing was linked.", "cancelled", "google")
    if error or not code:
        return _error_page(purpose, "Google didn't complete the sign-in. Please try again.", error or "google_error", "google")
    try:
        req, identity = await auth.google_finish(db, runtime.settings, state, code, pc_auth, runtime.members_transport)
    except MemberError as exc:
        return _error_page(purpose, str(exc), "callback_failed", "google")
    return _finish(db, runtime.settings, identity, req.purpose, pc_session, started_by=req.member_id, ref=pc_ref, browser=pc_auth)


@router.get("/members/auth/x/start")
def x_start(request: Request, db: DbSession, runtime: Runtime, purpose: Literal["login", "link"] = "login",
            pc_session: SessionToken = None, pc_auth: AuthToken = None) -> RedirectResponse:
    """Member sign-in with X (OAuth 2.0 + PKCE). Not the @pricechat brand connector."""
    if _signin_flood(request):
        return _error_page(purpose, SLOW_DOWN, "rate_limited", "x")
    resp = RedirectResponse("about:blank", status_code=303)
    browser = _browser_binding(resp, runtime.settings, pc_auth)
    try:
        url = auth.x_start(db, runtime.settings, purpose, browser, _current(db, pc_session))
    except MemberError as exc:
        logger.warning("X sign-in start refused: purpose=%s code=%s", purpose, getattr(exc, "code", "start_failed"))
        return _error_page(purpose, str(exc), getattr(exc, "code", "start_failed"), "x")
    # diagnostics only: never the state, verifier, code or tokens
    q = parse_qs(urlparse(url).query)
    ua = request.headers.get("user-agent", "")
    logger.info("X sign-in start: purpose=%s redirect_uri=%s scopes=%s pkce=%s device=%s", purpose, q.get("redirect_uri", ["?"])[0],
                q.get("scope", ["?"])[0], q.get("code_challenge_method", ["?"])[0],
                "android" if "Android" in ua else "ios" if ("iPhone" in ua or "iPad" in ua) else "desktop")
    resp.headers["location"] = url
    return resp


@router.get("/members/auth/x/callback")
async def x_callback(db: DbSession, runtime: Runtime, state: str = "", code: str = "", error: str | None = None,
                     pc_session: SessionToken = None, pc_auth: AuthToken = None, pc_ref: RefCode = None) -> RedirectResponse:
    purpose = _purpose_of(db, "x", state)
    logger.info("X sign-in callback: purpose=%s state_given=%s has_code=%s error=%s", purpose, bool(state), bool(code), error or "-")
    if error == "access_denied":
        return _error_page(purpose, "You cancelled connecting X. Nothing was linked.", "cancelled", "x")
    if error or not code or not state:
        return _error_page(purpose, "X didn't complete the sign-in. Please try again.", error or "x_error", "x")
    try:
        req, identity, token = await auth.x_finish(db, runtime.settings, state, code, pc_auth, runtime.members_transport)
    except MemberError as exc:
        return _error_page(purpose, str(exc), getattr(exc, "code", "callback_failed"), "x")
    return _finish(db, runtime.settings, identity, req.purpose, pc_session, x_token=token, started_by=req.member_id, ref=pc_ref,
                   browser=pc_auth)


# --- sign-in: Telegram ------------------------------------------------------------------------------------------


class TelegramStart(StrictInput):
    purpose: Literal["login", "link"] = "login"


@router.post("/members/auth/telegram/start")
def telegram_start(payload: TelegramStart, response: Response, request: Request, db: DbSession, runtime: Runtime,
                   pc_session: SessionToken = None, pc_auth: AuthToken = None) -> dict[str, Any]:
    if _signin_flood(request):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, SLOW_DOWN)
    browser = _browser_binding(response, runtime.settings, pc_auth)
    try:
        out = auth.telegram_start(db, runtime.settings, payload.purpose, browser, _current(db, pc_session),
                                  device=auth.device_label(request.headers.get("user-agent")))
    except MemberError as exc:
        raise _fail(exc) from exc
    if runtime.scheduler is not None:
        runtime.scheduler.request_run("telegram.ingest", 3)  # look for the Start press soon
    return out


@router.get("/members/auth/telegram/status/{request_id}")
def telegram_status(request_id: int, response: Response, db: DbSession, runtime: Runtime,
                    pc_session: SessionToken = None, pc_auth: AuthToken = None, pc_ref: RefCode = None) -> dict[str, Any]:
    state, req, identity = auth.telegram_claim(db, request_id, pc_auth)
    if state != "verified" or req is None or identity is None:
        if state == "pending" and runtime.scheduler is not None:
            runtime.scheduler.request_run("telegram.ingest", 2)
        if state == "cancelled":
            return {"status": state, "error": "You tapped No in Telegram, so nothing was signed in."}
        return {"status": state}
    current = _current(db, pc_session)
    if req.purpose == "link" and (current is None or current.id != req.member_id):
        logger.warning("Member telegram link refused: session does not match the member who started it")
        return {"status": "failed", "error": "Please connect from your own Kiwi Passport, signed in, in this browser."}
    try:
        member = service.sign_in_with(db, identity, req.purpose, current, referral_code=pc_ref, browser=pc_auth)
    except MemberError as exc:
        return {"status": "failed", "error": str(exc)}
    if req.purpose == "login":
        _set_session(response, runtime.settings, service.new_session(db, member, runtime.settings.member_session_days))
        response.delete_cookie(REF_COOKIE, path="/")
    return {"status": "done", "purpose": req.purpose, "username": identity.get("username")}


class TelegramWidget(StrictInput):
    id: int
    first_name: str | None = None
    last_name: str | None = None
    username: str | None = None
    photo_url: str | None = None
    auth_date: int
    hash: str
    purpose: Literal["login", "link"] = "login"


@router.post("/members/auth/telegram/widget")
def telegram_widget(payload: TelegramWidget, request: Request, response: Response, db: DbSession, runtime: Runtime,
                    pc_session: SessionToken = None, pc_auth: AuthToken = None, pc_ref: RefCode = None) -> dict[str, Any]:
    """Telegram Login Widget (public domain only). The signature is checked with the bot token. Refused while the
    deeplink mode is on: the site doesn't use the widget then, and a leaked bot token must not mint sessions (SEC-1)."""
    if runtime.settings.members_telegram_deeplink:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    if _signin_flood(request):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, SLOW_DOWN)
    data = payload.model_dump(exclude={"purpose"}, exclude_none=True)
    try:
        identity = auth.telegram_widget_verify(runtime.settings, data)
        member = service.sign_in_with(db, identity, payload.purpose, _current(db, pc_session), referral_code=pc_ref, browser=pc_auth)
    except MemberError as exc:
        raise _fail(exc) from exc
    if payload.purpose == "login":
        _set_session(response, runtime.settings, service.new_session(db, member, runtime.settings.member_session_days))
        response.delete_cookie(REF_COOKIE, path="/")
    return {"status": "done"}



# --- Pack Giveaway (just for fun: virtual packs, no money, no real cards) ------------------------------------------------


@router.get("/members/packs")
def packs_shelf(db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    from app.members import packs

    return packs.shelf(db, _current(db, pc_session))


@router.post("/members/packs/{key}/open")
def packs_open(key: str, db: DbSession, bonus: bool = False, pc_session: SessionToken = None) -> dict[str, Any]:
    from app.members import packs

    member = _require(db, pc_session)
    try:
        return packs.open_pack(db, member, key, bonus=bonus)
    except PermissionError as exc:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.get("/members/packs/binder")
def packs_binder(db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    from app.members import packs

    return packs.binder(db, _require(db, pc_session))


class PacksInPortfolio(StrictInput):
    show: bool


@router.post("/members/packs/portfolio")
def packs_in_portfolio(payload: PacksInPortfolio, db: DbSession, pc_session: SessionToken = None) -> dict[str, bool]:
    """Opt-in: show the member's virtual giveaway pulls in their Pokémon Portfolio, as a separate virtual section. They
    never become holdings: never in the collection's value, the collectors' leaderboard, snapshots or the marketplace."""
    from app.members import packs

    return {"in_portfolio": packs.set_in_portfolio(db, _require(db, pc_session), payload.show)}



# --- Play Market (virtual: Play USDT is a test currency, not real USDT) -------------------------------------------------


class PlayCard(StrictInput):
    card_id: int = Field(ge=1)
    price_cents: int | None = Field(None, ge=1, le=1_000_000_000)


class PlayToggle(StrictInput):
    on: bool


def _play(fn):  # type: ignore[no-untyped-def]
    from app.members.play_market import PlayError

    try:
        return fn()
    except PlayError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.get("/members/play")
def play_overview(db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    from app.members import play_market

    return play_market.overview(db, _require(db, pc_session))


@router.post("/members/play/sell")
def play_sell(payload: PlayCard, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    """Sell one copy of a giveaway card instantly, at its market estimate, for Play USDT (virtual)."""
    from app.members import play_market

    member = _require(db, pc_session)
    return _play(lambda: play_market.sell_now(db, member, payload.card_id))


@router.post("/members/play/list")
def play_list(payload: PlayCard, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    from app.members import play_market

    member = _require(db, pc_session)
    return _play(lambda: play_market.list_card(db, member, payload.card_id, payload.price_cents))


@router.post("/members/play/listings/{listing_id}/cancel")
def play_cancel(listing_id: int, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    from app.members import play_market

    member = _require(db, pc_session)
    return _play(lambda: play_market.cancel(db, member, listing_id))


@router.post("/members/play/listings/{listing_id}/buy")
def play_buy(listing_id: int, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    from app.members import play_market

    member = _require(db, pc_session)
    return _play(lambda: play_market.buy(db, member, listing_id))


@router.post("/members/play/auto-sell")
def play_auto_sell(payload: PlayToggle, db: DbSession, pc_session: SessionToken = None) -> dict[str, bool]:
    from app.members import play_market

    return {"auto_sell_dupes": play_market.set_auto_sell(db, _require(db, pc_session), payload.on)}



@router.get("/members/play/shop")
def play_shop(db: DbSession, q: str = Query("", max_length=60), pc_session: SessionToken = None) -> dict[str, Any]:
    """Cards to buy instantly at their market estimate, with Play USDT (virtual copies)."""
    from app.members import play_market

    _require(db, pc_session)
    return {"cards": play_market.shop(db, q)}


@router.post("/members/play/buy-card")
def play_buy_card(payload: PlayCard, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    from app.members import play_market

    member = _require(db, pc_session)
    return _play(lambda: play_market.buy_card(db, member, payload.card_id))



class PlayBidIn(StrictInput):
    card_id: int = Field(ge=1)
    price_cents: int = Field(ge=1, le=1_000_000_000)


@router.post("/members/play/bid")
def play_bid(payload: PlayBidIn, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    """Bid for a card (Play USDT, held while open). Fills at once against a cheaper member listing."""
    from app.members import play_market

    member = _require(db, pc_session)
    return _play(lambda: play_market.place_bid(db, member, payload.card_id, payload.price_cents))


@router.post("/members/play/bids/{bid_id}/cancel")
def play_cancel_bid(bid_id: int, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    from app.members import play_market

    member = _require(db, pc_session)
    return _play(lambda: play_market.cancel_bid(db, member, bid_id))


@router.get("/members/play/quote/{card_id}")
def play_quote(card_id: int, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    from app.members import play_market

    member = _require(db, pc_session)
    return _play(lambda: play_market.quote(db, member, card_id))



@router.post("/members/play/sell-all")
def play_sell_all(db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    """Sell every priced card at once, 10% below market (Play USDT)."""
    from app.members import play_market

    member = _require(db, pc_session)
    return _play(lambda: play_market.dump_all(db, member))
