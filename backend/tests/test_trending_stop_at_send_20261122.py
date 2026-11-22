"""Trend tweets consult the News Desk's emergency stop at the moment of sending (2026-11-22), as the News Desk's X lane (engine.publish) and
its Telegram lane (telegram.send) do: before this, only the queue's waiting_reason (coordinate.desk_blocked) looked, so a stop switched on
after a tweet was queued didn't hold it. No test reaches a real service (X is mocked by tests.trending_fixtures.Web)."""

from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.config.settings import get_settings
from app.models.trending import TtPost
from app.newsdesk import engine as desk, network
from app.services.agent_service import get_agent_config
from app.trending import engine
from tests.test_trending import NOW, hack_story, newsdesk_live, posts, tick
from tests.trending_fixtures import Web


@pytest.fixture
def web() -> Web:
    return Web(NOW)


async def queued_tweet(db: Session, web: Web) -> tuple[TtPost, Any]:
    """A real trend tweet written by the engine (approval mode, then approved: queued), with the News Desk's X lane live."""
    runtime = newsdesk_live(db, web)
    s = engine.settings_row(db)
    s.mode, s.auto_confirmed_at = "approval", None
    db.commit()
    hack_story(db, web)
    await tick(db, web)
    [post] = [p for p in posts(db) if "Bybaz" in p.text]
    post.status = "queued"
    db.commit()
    return post, runtime


async def send(db: Session, post: TtPost, runtime: Any) -> str:
    return await engine.publish(db, engine.settings_row(db), post, get_settings(), runtime, get_agent_config(db).id, NOW)


@pytest.mark.parametrize("stop", ["kill_switch", "network_emergency"])
async def test_under_the_emergency_stop_nothing_reaches_x_and_the_tweet_stays_queued_with_the_reason(db: Session, web: Web, stop: str) -> None:
    post, runtime = await queued_tweet(db, web)
    nd = desk.settings_row(db)
    if stop == "kill_switch":
        nd.kill_switch = True  # Admin / Terminal /stop, switched on after the tweet was queued
    else:
        nd.pause_reason = network.EMERGENCY
    db.commit()
    attempts = post.attempts
    assert await send(db, post, runtime) == desk.STOPPED
    assert web.posted == []  # no X call, nothing spent
    got = posts(db)[[p.id for p in posts(db)].index(post.id)]
    assert got.status == "queued" and got.x_id in (None, "") and got.published_at is None  # never marked sent, never claimed
    assert got.attempts == attempts and got.note == f"Not sent: {desk.emergency_stop(db)}"


async def test_without_the_stop_the_tweet_is_sent_as_before(db: Session, web: Web) -> None:
    post, runtime = await queued_tweet(db, web)
    assert desk.emergency_stop(db) == ""
    assert await send(db, post, runtime) == "published"
    got = db.get(TtPost, post.id)
    assert got.status == "published" and got.x_id and len(web.posted) == 1
