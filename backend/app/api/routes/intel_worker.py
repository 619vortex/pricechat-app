"""The owner's PC side of the intel channel (app/intel/channel.py). Public prefix (admin_auth.PUBLIC_PREFIXES "/api/intel-worker/"): every
call carries the PC's own pci_ bearer token (only its SHA-256 is configured on Railway), never the admin cookie. Results and failures are
also HMAC-signed with the job's single-use nonce. The PC only ever calls out; nothing here reaches it."""

from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException, Response, status
from pydantic import Field

from app.api.deps import DbSession
from app.api.schemas import StrictInput
from app.db.base import utcnow
from app.intel import channel
from app.models.intel import IntelWorker

router = APIRouter(prefix="/intel-worker", tags=["intel"])
Bearer = Annotated[str | None, Header(alias="Authorization")]


def _token(authorization: str | None) -> str:
    return (authorization or "").removeprefix("Bearer ").strip()


def _worker(db: DbSession, authorization: str | None) -> IntelWorker:
    w = channel.worker_for(db, _token(authorization))
    if w is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Unknown or disabled intel worker token.")
    return w


class HeartbeatIn(StrictInput):
    models: list[Annotated[str, Field(max_length=80)]] = Field(default_factory=list, max_length=50)
    info: dict[str, Any] = Field(default_factory=dict)


@router.post("/heartbeat")
def heartbeat(body: HeartbeatIn, db: DbSession, authorization: Bearer = None) -> dict[str, Any]:
    return channel.heartbeat(db, _worker(db, authorization), body.models, body.info, utcnow())


class ClaimIn(StrictInput):
    max: int = Field(1, ge=1, le=channel.MAX_CLAIM)


@router.post("/claim")
def claim(body: ClaimIn, db: DbSession, authorization: Bearer = None) -> Any:
    w = _worker(db, authorization)
    try:
        jobs = channel.claim(db, w, body.max, utcnow())
    except channel.ChannelError as e:
        raise HTTPException(e.status, str(e)) from e
    if not jobs:
        return Response(status_code=204)
    return {"jobs": jobs}


class ResultIn(StrictInput):
    nonce: str = Field(..., min_length=8, max_length=64)
    signature: str = Field(..., min_length=64, max_length=64)
    result: dict[str, Any]


@router.post("/jobs/{job_id}/result")
def result(job_id: int, body: ResultIn, db: DbSession, authorization: Bearer = None) -> dict[str, Any]:
    w = _worker(db, authorization)
    try:
        return channel.complete(db, w, job_id, body.nonce, body.signature, body.result, _token(authorization), utcnow())
    except channel.ChannelError as e:
        raise HTTPException(e.status, str(e)) from e


class FailIn(StrictInput):
    nonce: str = Field(..., min_length=8, max_length=64)
    signature: str = Field(..., min_length=64, max_length=64)
    error: str = Field(..., max_length=500)


@router.post("/jobs/{job_id}/fail")
def fail(job_id: int, body: FailIn, db: DbSession, authorization: Bearer = None) -> dict[str, Any]:
    w = _worker(db, authorization)
    try:
        return channel.fail(db, w, job_id, body.nonce, body.signature, body.error, _token(authorization), utcnow())
    except channel.ChannelError as e:
        raise HTTPException(e.status, str(e)) from e
