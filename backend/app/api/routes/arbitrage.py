"""Earn › Lunch Bot and Earn › Funding Arb (public, read-only). Nothing here trades."""

from typing import Any

from fastapi import APIRouter

from app.api.deps import DbSession
from app.arbitrage import funding, lunch
from app.db.base import utcnow

router = APIRouter(tags=["arbitrage"])


@router.get("/members/arbitrage/lunch")
def lunch_bot(db: DbSession) -> dict[str, Any]:
    return lunch.summary(db, utcnow())


@router.get("/members/arbitrage/funding")
async def funding_board() -> dict[str, Any]:
    """Live funding rates across Hyperliquid, Lighter, Orderly and Backpack, normalized per each venue's own interval."""
    return await funding.board()
