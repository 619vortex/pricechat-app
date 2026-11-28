"""Kiwi Passport 2.0 API (under the public /api/members/ prefix; member session). 404 while PASSPORT2_MODE is off, and in preview for
anyone not on PASSPORT2_PREVIEW_MEMBERS.

GET   /members/passport2             the editor: choices, titles, the collection (earned and locked), community score, live preview
PATCH /members/passport2             save choices (public page, title, bio, frame, featured badges, sections, invite link)
POST  /members/passport2/invite      answer the one-time "make your Passport public" invitation
GET   /members/passport2/card.png    the member's own share card (also while the page is private: it's their own)
GET   /members/public/{name}/card.png  a public Passport's share card (link previews, Telegram); 404 unless the page is public
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import Field
from sqlalchemy import select

from app.api.deps import DbSession
from app.api.routes.members import SessionToken, _require
from app.api.schemas import StrictInput
from app.models.members import Member
from app.passport import cards, rules, service

router = APIRouter(tags=["passport"])


def _member(db: DbSession, token: str | None) -> Member:
    member = _require(db, token)
    if not service.visible(member):
        raise HTTPException(404, "Not found.")
    service.ensure_catalog(db)
    return member


@router.get("/members/passport2")
def editor(db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    return service.identity(db, _member(db, pc_session))


class IdentityIn(StrictInput):
    public_profile: bool | None = None
    title_badge: str | None = Field(None, max_length=32)  # "" clears the title
    bio: str | None = Field(None, max_length=200)  # checked to BIO_MAX after clean-up
    accent: str | None = Field(None, max_length=16)
    featured: list[str] | None = Field(None, max_length=rules.FEATURED_SLOTS)
    sections: dict[str, bool] | None = None
    ref_link: bool | None = None


@router.patch("/members/passport2")
def save(body: IdentityIn, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    member = _member(db, pc_session)
    try:
        service.update_identity(db, member, public_profile=body.public_profile, title_badge=body.title_badge or None,
                                clear_title=body.title_badge == "", bio=body.bio, accent=body.accent, featured=body.featured,
                                sections_in=body.sections, ref_link=body.ref_link)
    except service.PassportError as exc:
        db.rollback()
        raise HTTPException(exc.status, str(exc)) from exc
    return service.identity(db, member)


class InviteIn(StrictInput):
    action: Literal["accept", "later"]


@router.post("/members/passport2/invite")
def invite(body: InviteIn, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    member = _member(db, pc_session)
    service.answer_invite(db, member, body.action == "accept")
    return service.identity(db, member)


def _png(data: bytes, cache: str) -> Response:
    return Response(data, media_type="image/png", headers={"Cache-Control": cache})


def _payload(db: DbSession, member: Member) -> dict[str, Any]:
    from app.members import xp as ledger
    from app.members.service import rank_out

    return {"username": member.username, "joined_at": member.created_at.isoformat(), "rank": rank_out(ledger.rank_for(db, member.rank_score)),
            **service.public_v2(db, member)}


@router.get("/members/passport2/card.png")
def my_card(db: DbSession, fmt: Literal["link", "story"] = Query("link"), pc_session: SessionToken = None) -> Response:
    member = _member(db, pc_session)
    p = _payload(db, member)
    return _png(cards.story_card(p) if fmt == "story" else cards.link_card(p), "private, max-age=60")


@router.get("/members/public/{username}/card.png")
def public_card(username: str, db: DbSession, fmt: Literal["link", "story"] = Query("link")) -> Response:
    m = db.scalar(select(Member).where(Member.username_key == username.lower()[:32]))
    if m is None or not m.public_profile or m.status != "active" or not service.visible(m):
        raise HTTPException(404, "No public profile")
    service.ensure_catalog(db)
    p = _payload(db, m)
    return _png(cards.story_card(p) if fmt == "story" else cards.link_card(p), "public, max-age=600")
