"""The Vortex worker API (/api/vortex/*): the owner's home AI long-polls for trade-check jobs and posts its answers.

Outside the admin login (admin_auth.PUBLIC_PREFIXES): every route needs `Authorization: Bearer <vortex_assist_token>`,
compared in constant time; failed tokens are rate-limited. The token is never logged. See app/tradingos/assist.py.
"""

import asyncio
import hmac
import time
from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import Field

from app.api.deps import DbSession, Runtime
from app.api.schemas import StrictInput
from app.security.admin_auth import client_key
from app.security.rate_limit import LIMITS
from app.tradingos import assist

router = APIRouter(tags=["vortex"])
P = "/vortex"
MAX_WAIT = 25
POLL_EVERY = 1.0  # seconds between looks at the queue while long-polling (no database session is held in between)

Bearer = Annotated[str | None, Header(alias="Authorization")]


def _auth(request: Request, authorization: str | None) -> None:
    want = assist.token()
    got = (authorization or "").removeprefix("Bearer ").strip()
    if want and got and hmac.compare_digest(got.encode(), want.encode()):
        return
    wait = LIMITS["vortex_auth"].hit(client_key(request))
    if wait is not None:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many failed attempts.", headers={"Retry-After": str(wait)})
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Unknown token.")


def _tg(runtime: Any) -> Any:
    from app.telegram.service import make_client

    return make_client(runtime.settings, runtime.telegram.transport)


class ClaimIn(StrictInput):
    max: int = Field(3, ge=1, le=10)
    wait: int = Field(MAX_WAIT, ge=0, le=60)


def _claim_once(max_jobs: int, seen: bool) -> list[dict[str, Any]]:
    from app.db import session as dbs

    db = dbs.SessionLocal()
    try:
        if seen:
            assist.mark_seen(db)
            db.commit()
        return assist.claim(db, max_jobs)
    finally:
        db.close()


@router.post(f"{P}/claim")
async def claim(body: ClaimIn, request: Request, authorization: Bearer = None) -> dict[str, Any]:
    """Long-poll up to `wait` seconds (at most 25) for jobs. Each look opens and closes its own short session."""
    _auth(request, authorization)
    deadline = time.monotonic() + min(body.wait, MAX_WAIT)
    jobs = await asyncio.to_thread(_claim_once, body.max, True)
    while not jobs and time.monotonic() + POLL_EVERY <= deadline:
        await asyncio.sleep(POLL_EVERY)
        jobs = await asyncio.to_thread(_claim_once, body.max, False)
    return {"jobs": jobs}


class AnswerIn(StrictInput):
    text: str = Field("", max_length=20_000)


class FailIn(StrictInput):
    reason: str = Field("", max_length=500)


def _job(db: Any, job_id: str) -> Any:
    job = assist.find(db, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such job.")
    return job


@router.post(f"{P}/jobs/{{job_id}}/answer")
async def answer(job_id: str, body: AnswerIn, request: Request, db: DbSession, runtime: Runtime, authorization: Bearer = None) -> dict[str, Any]:
    _auth(request, authorization)
    text = body.text.strip()
    if not text or len(text) > assist.MAX_ANSWER:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"text must be 1-{assist.MAX_ANSWER} characters.")
    job = _job(db, job_id)
    tg = _tg(runtime)
    try:
        await assist.answer(db, tg, job, text)
    except assist.Conflict as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from None
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Nothing left after removing links and markup.") from None
    finally:
        await tg.aclose()
    return {"ok": True}


@router.post(f"{P}/jobs/{{job_id}}/fail")
async def fail(job_id: str, body: FailIn, request: Request, db: DbSession, runtime: Runtime, authorization: Bearer = None) -> dict[str, Any]:
    _auth(request, authorization)
    job = _job(db, job_id)
    tg = _tg(runtime)
    try:
        await assist.fail(db, tg, job)
    except assist.Conflict as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from None
    finally:
        await tg.aclose()
    return {"ok": True}


@router.get(f"{P}/health")
def health(request: Request, db: DbSession, authorization: Bearer = None) -> dict[str, Any]:
    _auth(request, authorization)
    return {"ok": True, "pending": assist.pending_count(db)}
