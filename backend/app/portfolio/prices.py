"""On-demand prices for a card someone is adding: when the stored data has no price for the chosen grade / raw condition,
fetch that one card's current Scrydex data (every grade + the raw conditions; 1 credit) and value it again.

Bounded: a card is fetched at most once per CHECK_EVERY (remembered on the card), per-member and site-wide daily caps
(recorded as PokemonScan rows of kind "prices"), one card per call, short timeouts. A provider failure is recorded as the
holding's reason ("provider_error") and never blocks saving the card.
"""

import logging
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config.settings import Settings
from app.db.base import utcnow
from app.models.members import Member
from app.models.pokemon import PokemonCard
from app.models.portfolio import PokemonScan

logger = logging.getLogger(__name__)
CHECK_EVERY = timedelta(hours=24)
PER_MEMBER_DAY = 60
TOTAL_DAY = 600
NEEDS_FETCH = {"grade_price_unavailable", "raw_prices_unavailable", "comparable", "grade_equivalent", "provider_error"}


def _checked_recently(card: PokemonCard) -> bool:
    at = (card.external_ids or {}).get("prices_checked_at")
    if not at:
        return False
    from datetime import datetime

    try:
        return utcnow() - datetime.fromisoformat(at) < CHECK_EVERY
    except ValueError:
        return False


def _within_caps(db: Session, member: Member | None) -> bool:
    since = utcnow() - timedelta(days=1)
    total = db.scalar(select(func.count()).select_from(PokemonScan).where(PokemonScan.kind == "prices", PokemonScan.at >= since)) or 0
    if total >= TOTAL_DAY:
        return False
    if member is not None:
        mine = db.scalar(select(func.count()).select_from(PokemonScan).where(PokemonScan.kind == "prices", PokemonScan.member_id == member.id,
                                                                             PokemonScan.at >= since)) or 0
        if mine >= PER_MEMBER_DAY:
            return False
    return True


async def ensure(db: Session, settings: Settings, card_id: int, member: Member | None, transport: Any = None, force: bool = False) -> str:
    """Fetch the card's current provider prices if allowed. Returns fetched | recent | no_link | capped | error."""
    from app.pokemon import history, scrydex_sync
    from app.portfolio.scan import configured

    card = db.get(PokemonCard, card_id)
    if card is None or not (card.external_ids or {}).get("scrydex"):
        return "no_link"
    if not configured(settings):
        return "error"
    if not force and _checked_recently(card):
        return "recent"
    if not _within_caps(db, member):
        return "capped"
    if member is not None:
        db.add(PokemonScan(member_id=member.id, at=utcnow(), kind="prices", image_sha256=f"card:{card.id}", status="ok", credits=1, result={}))
    card.external_ids = {**(card.external_ids or {}), "prices_checked_at": utcnow().isoformat()}
    db.commit()
    try:
        log = await scrydex_sync.sync(db, settings, [card.id], with_history=False, transport=transport, max_requests=2, invalidate=False)
        if log.get("error"):
            logger.warning("Price fetch for card %s: %s", card.id, str(log.get("error"))[:200])
            return "error"
    except Exception as exc:  # noqa: BLE001 - provider down: the card is still saved, the reason says why
        logger.warning("Price fetch for card %s failed: %s", card.id, exc)
        db.rollback()
        return "error"
    history.evict(lambda k: isinstance(k, tuple) and k and k[0] in ("chart", "market"))  # this card's market views refresh
    return "fetched"
