"""Kiwi Passport › burner wallet. The wallet is made in the member's browser; the browser encrypts its recovery phrase with
the member's own password (PBKDF2-SHA256, AES-GCM) before anything is sent. This API only stores and returns that
ciphertext and the public address: Price Chat can never read the phrase, and can't recover it if the password is lost."""

import base64
import binascii
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.deps import DbSession
from app.api.routes.members import SessionToken, _require
from app.api.schemas import StrictInput
from app.models.members import MemberAuditLog, MemberBurnerWallet

router = APIRouter(tags=["members"])


def _b64(value: str, lo: int, hi: int) -> str:
    try:
        raw = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("must be base64") from None
    if not lo <= len(raw) <= hi:
        raise ValueError(f"must decode to {lo}-{hi} bytes")
    return value


class VaultIn(StrictInput):
    v: Literal[1]
    kdf: Literal["PBKDF2-SHA256"]
    cipher: Literal["AES-GCM"]
    iterations: int = Field(..., ge=200_000, le=5_000_000)
    salt: str = Field(..., max_length=128)
    iv: str = Field(..., max_length=64)
    ct: str = Field(..., max_length=1024)  # a 12-24 word phrase + the GCM tag; anything bigger isn't a phrase

    @field_validator("salt")
    @classmethod
    def _salt(cls, v: str) -> str:
        return _b64(v, 16, 64)

    @field_validator("iv")
    @classmethod
    def _iv(cls, v: str) -> str:
        return _b64(v, 12, 16)

    @field_validator("ct")
    @classmethod
    def _ct(cls, v: str) -> str:
        return _b64(v, 32, 700)


class BurnerIn(StrictInput):
    address: str = Field(..., pattern=r"^0x[0-9a-fA-F]{40}$")
    vault: VaultIn


class BurnerDeleteIn(StrictInput):
    address: str = Field(..., pattern=r"^0x[0-9a-fA-F]{40}$")  # the member confirms by sending the address they're removing


def _out(w: MemberBurnerWallet | None) -> dict[str, Any]:
    return {"burner": None if w is None else {"address": w.address, "vault": w.vault, "created_at": w.created_at.isoformat() if w.created_at else None}}


@router.get("/members/me/burner")
def get_burner(response: Response, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    member = _require(db, pc_session)
    response.headers["Cache-Control"] = "no-store"
    return _out(db.scalar(select(MemberBurnerWallet).where(MemberBurnerWallet.member_id == member.id)))


@router.post("/members/me/burner")
def create_burner(payload: BurnerIn, response: Response, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    member = _require(db, pc_session)
    response.headers["Cache-Control"] = "no-store"
    taken = "You already have a burner wallet. Remove it first to make a new one."
    if db.scalar(select(MemberBurnerWallet.id).where(MemberBurnerWallet.member_id == member.id)):
        raise HTTPException(status.HTTP_409_CONFLICT, taken)
    w = MemberBurnerWallet(member_id=member.id, address=payload.address, vault=payload.vault.model_dump())
    db.add(w)
    db.add(MemberAuditLog(member_id=member.id, actor="member", action="burner_created", details={"address": payload.address}))
    try:
        db.commit()
    except IntegrityError:  # a second create raced past the check (double click, two tabs): the unique constraint caught it
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, taken) from None
    return _out(w)


@router.delete("/members/me/burner")
def delete_burner(payload: BurnerDeleteIn, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    member = _require(db, pc_session)
    w = db.scalar(select(MemberBurnerWallet).where(MemberBurnerWallet.member_id == member.id))
    if w is None or w.address.lower() != payload.address.lower():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No burner wallet with that address on your Passport.")
    db.delete(w)
    db.add(MemberAuditLog(member_id=member.id, actor="member", action="burner_removed", details={"address": w.address}))
    db.commit()
    return _out(None)
