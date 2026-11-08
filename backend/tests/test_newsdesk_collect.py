"""engine.collect never lets one stored link stop the whole desk (production outage 2026-11-08 10:54 UTC: Cointelegraph's daily
round-up kept its URL, the row from 5 days earlier was outside the 4-day "seen" window, and the UNIQUE(link) violation rolled back
every new story, every run, for days)."""

from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.newsdesk import NdStory
from app.newsdesk import engine
from tests.test_newsdesk import Web, rss

FEED = {"name": "Cointelegraph", "url": "https://ct.test/rss", "tier": 2, "category": "crypto"}
DAILY = "https://ct.test/news/what-happened-in-crypto-today"
FRESH = "https://ct.test/news/bitcoin-exchange-hacked"


def stored(db: Session, link: str, days_ago: float) -> None:
    at = utcnow() - timedelta(days=days_ago)
    db.add(NdStory(link=link, title="Here's what happened in crypto today", summary="", source="Cointelegraph", tier=2, category="crypto",
                   published_at=at, fingerprint="crypto happened here today", score=2.5, created_at=at, updated_at=at))
    db.commit()


@pytest.mark.asyncio
async def test_a_recycled_link_is_skipped_and_new_stories_still_arrive(db: Session) -> None:
    now = utcnow()
    s = engine.settings_row(db)
    s.feeds = [FEED]
    stored(db, DAILY, 5)  # older than the old 4-day window, younger than the 7-day purge
    web = Web()
    web.feeds[FEED["url"]] = rss([("Here's what happened in crypto today", DAILY, now - timedelta(hours=2)),
                                   ("Bitcoin exchange hacked for $200 million, withdrawals halted", FRESH, now - timedelta(minutes=10))])
    async with httpx.AsyncClient(transport=web.transport) as client:
        out = await engine.collect(db, s, client, now)
    assert db.scalar(select(NdStory).where(NdStory.link == FRESH)) is not None
    assert out["new_stories"] == 1
    assert len(db.scalars(select(NdStory).where(NdStory.link == DAILY)).all()) == 1
    async with httpx.AsyncClient(transport=web.transport) as client:  # the next run: nothing new, nothing fails
        assert (await engine.collect(db, s, client, now + timedelta(minutes=5)))["new_stories"] == 0


@pytest.mark.asyncio
async def test_old_rows_are_purged_before_new_ones_go_in(db: Session) -> None:
    now = utcnow()
    s = engine.settings_row(db)
    s.feeds = [FEED]
    stored(db, DAILY, 8)
    web = Web()
    web.feeds[FEED["url"]] = rss([("Bitcoin exchange hacked for $200 million, withdrawals halted", FRESH, now - timedelta(minutes=10))])
    async with httpx.AsyncClient(transport=web.transport) as client:
        await engine.collect(db, s, client, now)
    assert db.scalar(select(NdStory).where(NdStory.link == DAILY)) is None
    assert db.scalar(select(NdStory).where(NdStory.link == FRESH)) is not None
