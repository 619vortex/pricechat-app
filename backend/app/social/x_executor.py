"""Performs ONE approved X write through the official API, then records what X confirmed.

Only `approvals.execute` calls this, with an ExecutionPermit from the SocialActionGuard; the
connector write methods refuse to run without one.
"""

from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config.settings import Settings
from app.connectors.state import get_connector_state
from app.connectors.x import XRuntime
from app.connectors.x.errors import XForbiddenError, XNotConfiguredError
from app.db.base import utcnow
from app.models.enums import Platform, SocialAction
from app.models.social import SocialPost
from app.models.social_intel import ApprovalRequest, SocialProfile, SocialRelationship
from app.social.guard import ActionRequest, ExecutionPermit
from app.social.store import topic_words
from app.social.x_service import READ_ONLY_FIX, make_x_client


@dataclass(slots=True)
class XWriteResult:
    confirmed: bool
    x_id: str | None
    url: str | None
    response: dict[str, Any]


def _confirmed(action: SocialAction, data: dict[str, Any]) -> bool:
    if action in (SocialAction.POST, SocialAction.REPLY):
        return bool(data.get("id"))
    if action == SocialAction.LIKE:
        return data.get("liked") is True
    if action == SocialAction.REPOST:
        return data.get("retweeted") is True
    if action == SocialAction.FOLLOW:
        return data.get("following") is True or data.get("pending_follow") is True
    return False


async def perform(db: Session, settings: Settings, runtime: XRuntime, approval: ApprovalRequest, req: ActionRequest,
                  permit: ExecutionPermit) -> XWriteResult:
    state = get_connector_state(db, Platform.X)
    our_id, our_name = state.account_external_id, state.account_username
    if req.action in (SocialAction.LIKE, SocialAction.REPOST, SocialAction.FOLLOW) and not our_id:
        raise XNotConfiguredError("Your X account is not identified yet: run Test connection with the user-context keys")
    if req.action != SocialAction.POST and not req.target_external_id:
        raise XNotConfiguredError(f"No target for {req.action.value}")
    payload = approval.payload or {}
    client = make_x_client(db, settings, runtime)
    try:
        if req.action == SocialAction.POST:
            data = await client.create_post(req.text or "", permit=permit, quote_of=payload.get("quote_post_id"))
        elif req.action == SocialAction.REPLY:
            data = await client.create_post(req.text or "", permit=permit, reply_to=req.target_external_id)
        elif req.action == SocialAction.LIKE:
            data = await client.like(our_id, req.target_external_id, permit=permit)  # type: ignore[arg-type]
        elif req.action == SocialAction.REPOST:
            data = await client.repost(our_id, req.target_external_id, permit=permit)  # type: ignore[arg-type]
        elif req.action == SocialAction.FOLLOW:
            data = await client.follow(our_id, req.target_external_id, permit=permit)  # type: ignore[arg-type]
        else:
            raise XNotConfiguredError(f"{req.action.value} is not supported on X")
    except XForbiddenError as exc:
        _mark_unavailable(db, req.action, exc, client.access_level)
        raise
    finally:
        await client.aclose()

    x_id = str(data["id"]) if data.get("id") else None
    url = f"https://x.com/{our_name or 'i'}/status/{x_id}" if x_id else None
    if req.action in (SocialAction.LIKE, SocialAction.REPOST):
        url = (payload.get("original_post") or {}).get("url") or (
            f"https://x.com/{req.account}/status/{req.target_external_id}" if req.account else None)
    result = XWriteResult(_confirmed(req.action, data), x_id, url, data)
    if result.confirmed:
        _after(db, req, result, context=(payload.get("original_post") or {}).get("text") or "")
    return result


def _mark_unavailable(db: Session, action: SocialAction, exc: Exception, access_level: str | None) -> None:
    """X refused the write: remember it so the guard stops proposing this action as possible."""
    state = get_connector_state(db, Platform.X)
    detail = READ_ONLY_FIX if (access_level == "read" or "permission" in str(exc).lower()) else str(exc)
    caps = dict(state.capabilities or {})
    caps[f"write_{action.value}"] = {"status": "unavailable", "detail": detail, "checked_at": utcnow().isoformat(), "label": f"Write: {action.value}"}
    state.capabilities = caps


def _after(db: Session, req: ActionRequest, result: XWriteResult, context: str = "") -> None:
    now = utcnow()
    if req.action in (SocialAction.POST, SocialAction.REPLY) and result.x_id:
        # Our own new post: stored so later reads attach its performance metrics to it.
        if db.scalar(select(SocialPost.id).where(SocialPost.platform == Platform.X, SocialPost.external_id == result.x_id)) is None:
            db.add(SocialPost(platform=Platform.X, external_id=result.x_id, text=req.text or "", url=result.url, is_own=True,
                              posted_at=now, metrics={}, source_kinds=["price_chat_action"],
                              in_reply_to_external_id=req.target_external_id if req.action == SocialAction.REPLY else None))
    if req.account:
        record_outbound(db, req.account, req.action, req.text or context, now, post_id=req.target_external_id)
    db.flush()


def record_outbound(db: Session, username: str, action: SocialAction, text: str, at, post_id: str | None = None) -> None:  # type: ignore[no-untyped-def]
    """Relationship memory for actions Price Chat performed (public interactions only)."""
    profile = db.scalar(select(SocialProfile).where(SocialProfile.platform == Platform.X, func.lower(SocialProfile.username) == username.lower()))
    if profile is None:
        return
    rel = db.scalar(select(SocialRelationship).where(SocialRelationship.profile_id == profile.id))
    if rel is None:
        rel = SocialRelationship(profile_id=profile.id, topics={}, interaction_count=0, mention_count=0, reply_count=0,
                                 our_reply_count=0, our_like_count=0, our_repost_count=0)
        db.add(rel)
    rel.interaction_count = (rel.interaction_count or 0) + 1
    if action == SocialAction.REPLY:
        rel.our_reply_count = (rel.our_reply_count or 0) + 1
    elif action == SocialAction.LIKE:
        rel.our_like_count = (rel.our_like_count or 0) + 1
        rel.last_liked_at, rel.last_liked_post_id = at, post_id
    elif action == SocialAction.REPOST:
        rel.our_repost_count = (rel.our_repost_count or 0) + 1
    elif action == SocialAction.FOLLOW:
        rel.we_follow_them = True
    rel.first_interaction_at = min(rel.first_interaction_at, at) if rel.first_interaction_at else at
    rel.last_interaction_at = at
    if text:
        topics = dict(rel.topics or {})
        for w in topic_words(text):
            topics[w] = topics.get(w, 0) + 1
        rel.topics = dict(sorted(topics.items(), key=lambda kv: kv[1], reverse=True)[:15])
