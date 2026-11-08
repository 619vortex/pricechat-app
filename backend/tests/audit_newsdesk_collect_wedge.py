"""AUDIT (2026-11-08, ND-1): reproduces the production wedge in engine.collect.

A link stored 4-7 days ago is outside collect's 4-day `seen` window but still in nd_stories (purged only after 7 days, in the same
transaction). When the feed repeats that URL with a fresh date (Cointelegraph's daily "what happened in crypto today" does), the insert
violates the UNIQUE(link) constraint and the whole commit rolls back with every other new story; every run fails the same way until
the old row turns 7 days old (about 3 days of no ingestion, repeating weekly while the feed recycles the URL). This test asserts the CURRENT (buggy) behaviour; when the bug is fixed, flip the expectations.
"""

from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.newsdesk import NdStory
from app.newsdesk import engine
from tests.test_newsdesk import Web, rss

FEED = {"name": "Cointelegraph", "url": "https://ct.test/rss", "tier": 2, "category": "crypto"}
DAILY = "https://ct.test/news/what-happened-in-crypto-today"
FRESH = "https://ct.test/news/bitcoin-exchange-hacked"


@pytest.mark.asyncio
async def test_a_recycled_link_wedges_collect_and_drops_every_new_story(db: Session) -> None:
    now = utcnow()
    s = engine.settings_row(db)
    s.feeds = [FEED]
    db.add(NdStory(link=DAILY, title="Here's what happened in crypto today", summary="", source="Cointelegraph", tier=2, category="crypto",
                   published_at=now - timedelta(days=5), fingerprint="crypto happened here today", score=2.5, created_at=now - timedelta(days=5), updated_at=now - timedelta(days=5)))
    db.commit()
    web = Web()
    web.feeds[FEED["url"]] = rss([("Here's what happened in crypto today", DAILY, now - timedelta(hours=2)),
                                   ("Bitcoin exchange hacked for $200 million, withdrawals halted", FRESH, now - timedelta(minutes=10))])
    for _ in range(2):  # every run fails the same way until the old row is 7 days old
        with pytest.raises(IntegrityError):
            async with httpx.AsyncClient(transport=web.transport) as client:
                await engine.collect(db, s, client, now)
        db.rollback()
    assert db.scalar(select(NdStory).where(NdStory.link == FRESH)) is None  # the real story never reached the desk
    # it heals only when the old row turns 7 days old: the purge's DELETE runs before the inserts flush, in the same transaction
    later = now + timedelta(days=2, hours=1)
    web.feeds[FEED["url"]] = rss([("Here's what happened in crypto today", DAILY, later - timedelta(hours=2))])
    async with httpx.AsyncClient(transport=web.transport) as client:
        await engine.collect(db, s, client, later)
