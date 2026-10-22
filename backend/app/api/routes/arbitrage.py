"""Earn › Lunch Bot and Earn › Funding Arb (public, read-only). Nothing here trades."""

from typing import Any

from fastapi import APIRouter, Depends

from app.api.deps import DbSession
from app.arbitrage import funding, lunch
from app.db.base import utcnow
from app.security import rate_limit

router = APIRouter(tags=["arbitrage"])


@router.get("/members/arbitrage/lunch", dependencies=[Depends(rate_limit.limit("earn"))])
def lunch_bot(db: DbSession) -> dict[str, Any]:
    return lunch.summary(db, utcnow())


@router.get("/members/arbitrage/funding", dependencies=[Depends(rate_limit.limit("earn"))])
async def funding_board() -> dict[str, Any]:
    """Live funding rates across Hyperliquid, Lighter, Orderly and Backpack, normalized per each venue's own interval."""
    return await funding.board()
