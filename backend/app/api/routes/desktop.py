"""Signing in to the Kiwi VPN desktop app through the person's own browser, so the app never sees a password.

The app makes a random verifier and opens /vpn/desktop in the browser with its SHA-256 (the challenge) and a loopback port. The signed-in person presses
Allow, and the page hands a one-time code to 127.0.0.1 only. The app swaps the code and the verifier for its own session. A code alone is useless, and works once.
The app then sends that session as the pc_session cookie on the existing VPN routes. It reaches nothing else: see app.members.desktop_scope.
"""

import base64
import hashlib
import hmac
import re
import secrets
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import Field
from sqlalchemy import update

from app.api.deps import DbSession
from app.api.routes.members import SessionToken, _require
from app.api.schemas import StrictInput
from app.db.base import utcnow
from app.members import service
from app.members.desktop_scope import DESKTOP_TOKEN_PREFIX
from app.models.desktop import MemberDesktopCode
from app.models.members import Member, MemberSession
from app.security import rate_limit

router = APIRouter(tags=["desktop"])

CODE_MINUTES = 5
SESSION_DAYS = 90
_B64URL = re.compile(r"^[A-Za-z0-9_-]{43}$")
FAIL = "That sign-in has expired. Please try again from the app."


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class CodeInput(StrictInput):
    challenge: str = Field(min_length=43, max_length=43)


@router.post("/members/desktop/code", dependencies=[Depends(rate_limit.limit("signin"))])
def make_code(payload: CodeInput, request: Request, db: DbSession, pc_session: SessionToken = None) -> dict[str, str]:
    """Called by the website page after the person presses Allow. Same-site cookie and the Origin check keep other sites out."""
    member = _require(db, pc_session)
    if not _B64URL.match(payload.challenge):
        raise HTTPException(400, "That sign-in link isn't valid.")
    code = secrets.token_urlsafe(32)
    now = utcnow()
    db.query(MemberDesktopCode).filter(MemberDesktopCode.expires_at < now - timedelta(days=1)).delete(synchronize_session=False)
    db.add(MemberDesktopCode(code_hash=_hash(code), challenge=payload.challenge, member_id=member.id, expires_at=now + timedelta(minutes=CODE_MINUTES)))
    db.commit()
    return {"code": code}


class TokenInput(StrictInput):
    code: str = Field(min_length=20, max_length=100)
    verifier: str = Field(min_length=43, max_length=128)


@router.post("/members/desktop/token", dependencies=[Depends(rate_limit.limit("signin"))])
def swap_code(payload: TokenInput, db: DbSession) -> dict[str, Any]:
    """Code + verifier → a session for the app. The code is spent by the first attempt, right or wrong."""
    now = utcnow()
    row = db.query(MemberDesktopCode).filter(MemberDesktopCode.code_hash == _hash(payload.code)).one_or_none()
    if row is None:
        raise HTTPException(400, FAIL)
    spent = db.execute(update(MemberDesktopCode).where(MemberDesktopCode.id == row.id, MemberDesktopCode.used_at.is_(None)).values(used_at=now))
    db.commit()
    expires = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=now.tzinfo)
    digest = base64.urlsafe_b64encode(hashlib.sha256(payload.verifier.encode()).digest()).rstrip(b"=").decode()
    if spent.rowcount != 1 or expires < now or not hmac.compare_digest(digest, row.challenge):
        raise HTTPException(400, FAIL)
    member = db.get(Member, row.member_id)
    if member is None or member.status != "active":
        raise HTTPException(400, FAIL)
    return {"token": service.new_session(db, member, SESSION_DAYS, DESKTOP_TOKEN_PREFIX, kind="desktop"), "expires_in_days": SESSION_DAYS}


@router.get("/members/desktop/sessions")
def my_sessions(db: DbSession, pc_session: SessionToken = None) -> dict[str, int]:
    """How many PCs hold a Kiwi VPN app session for this member."""
    member = _require(db, pc_session)
    count = db.query(MemberSession).filter(MemberSession.member_id == member.id, MemberSession.kind == "desktop", MemberSession.expires_at > utcnow()).count()
    return {"count": count}


@router.post("/members/desktop/sessions/revoke")
def revoke_sessions(db: DbSession, pc_session: SessionToken = None) -> dict[str, int]:
    """Signs the Kiwi VPN app out on every PC, leaving browsers signed in. A lost PC is the reason to press this."""
    member = _require(db, pc_session)
    removed = db.query(MemberSession).filter(MemberSession.member_id == member.id, MemberSession.kind == "desktop").delete(synchronize_session=False)
    db.commit()
    return {"removed": removed}
