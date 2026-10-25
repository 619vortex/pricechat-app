"""Kalshi bot API.

The bot reports under /api/kalshi/bot/... (public prefix; its own bearer token, made in the admin page). The admin page
reads under /api/kalshi/admin/... (owner login: every non-public /api route). Read-only: there is no route that sends the
bot anything, and the bot places no orders.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException
from pydantic import Field

from app.api.deps import DbSession
from app.api.schemas import StrictInput
from app.kalshi import service
from app.kalshi.service import KalshiError

router = APIRouter(tags=["kalshi"])
Bearer = Annotated[str | None, Header(alias="Authorization")]


def _fail(e: KalshiError) -> HTTPException:
    return HTTPException(e.status, str(e))


class ReportIn(StrictInput):
    kind: str = Field(max_length=16)
    payload: dict[str, Any]
    bot: dict[str, Any] = Field(default_factory=dict)


@router.post("/kalshi/bot/report")
def bot_report(body: ReportIn, db: DbSession, authorization: Bearer = None) -> dict[str, Any]:
    try:
        bot = service.authorize(db, authorization)
        return service.save_report(db, bot, body.kind, body.payload, body.bot)
    except KalshiError as e:
        raise _fail(e) from e


@router.get("/kalshi/admin/overview")
def admin_overview(db: DbSession) -> dict[str, Any]:
    return service.overview(db)


@router.post("/kalshi/admin/token")
def admin_new_token(db: DbSession) -> dict[str, Any]:
    """A new bot token, shown once. Put it in the bot's .env as PRICECHAT_KALSHI_TOKEN."""
    return {"token": service.new_token(db)}


@router.delete("/kalshi/admin/token")
def admin_disconnect(db: DbSession) -> dict[str, Any]:
    service.disconnect(db)
    return {"connected": False}
