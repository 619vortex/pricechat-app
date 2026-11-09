"""Earn › Lunch Bot and Earn › Funding Arb (public, read-only). Nothing here trades."""

from typing import Any

from fastapi import APIRouter, Depends

from app.api.deps import DbSession
from app.arbitrage import funding, lunch
from app.db.base import utcnow
from app.security import rate_limit

router = APIRouter(tags=["arbitrage"])


LUNCH_TTL = 20.0  # seconds: the summary aggregates every paper trade (~5 s on production); read at most once per window


@router.get("/members/arbitrage/lunch", dependencies=[Depends(rate_limit.limit("earn"))])
def lunch_bot(db: DbSession) -> dict[str, Any]:
    from app.pokemon import history  # the shared single-flight cache (one build at a time; cleared between tests)

    return history.cached(("earn-lunch",), lambda: lunch.summary(db, utcnow()), ttl=LUNCH_TTL)


@router.get("/members/arbitrage/funding", dependencies=[Depends(rate_limit.limit("earn"))])
async def funding_board() -> dict[str, Any]:
    """Live funding rates across Hyperliquid, Lighter, Orderly and Backpack, normalized per each venue's own interval."""
    return await funding.board()
