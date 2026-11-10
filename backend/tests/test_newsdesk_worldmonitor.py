"""World Monitor as a source (owner, 2026-11-10): its catalog only pointed us at publishers; each feed is the publisher's own, read
directly, credited to the publisher in posts and to the catalog in Admin, and it supplements our feeds (never replaces them)."""
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from app.newsdesk import editorial, engine, sources
from app.models.newsdesk import NdEvent


def test_world_monitor_feeds_supplement_ours_and_can_be_switched_off() -> None:
    ours = sources.feeds_for([], {})
    urls = {f["url"] for f in ours}
    assert {f["url"] for f in sources.EXTRA_FEEDS} <= urls and {f["url"] for f in sources.WORLDMONITOR_FEEDS} <= urls
    off = {f["url"] for f in sources.feeds_for([], {"worldmonitor_feeds": False})}
    assert {f["url"] for f in sources.EXTRA_FEEDS} <= off and not off & {f["url"] for f in sources.WORLDMONITOR_FEEDS}


def test_every_world_monitor_feed_is_an_original_publisher_feed() -> None:
    for f in sources.WORLDMONITOR_FEEDS:
        u = f["url"]
        assert f["via"] == sources.WORLDMONITOR_VIA
        assert "worldmonitor" not in u and "news.google." not in u and "rsshub" not in u and "hnrss" not in u, u
        assert u.startswith("https://")
    assert len({f["url"] for f in sources.WORLDMONITOR_FEEDS}) == len(sources.WORLDMONITOR_FEEDS)
    # never a duplicate of a feed we already read
    assert not {f["url"] for f in sources.WORLDMONITOR_FEEDS} & {f["url"] for f in [*sources.DEFAULT_FEEDS, *sources.EXTRA_FEEDS]}


def test_via_credits_the_catalog_only_for_its_outlets() -> None:
    assert sources.via("DW") == sources.WORLDMONITOR_VIA and sources.via("Bloomberg") is None


@pytest.mark.asyncio
async def test_feed_health_counts_failure_streaks_and_remembers_the_last_good_check(db: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime(2026, 11, 10, 12, tzinfo=timezone.utc)
    feeds = [{"name": "DW", "url": "https://rss.dw.com/xml/rss-en-all", "tier": 1, "category": "politics", "via": sources.WORLDMONITOR_VIA},
             {"name": "Bloomberg", "url": "https://feeds.bloomberg.com/markets/news.rss", "tier": 1, "category": None}]
    monkeypatch.setattr(sources, "feeds_for", lambda stored, state: feeds)
    up = {"ok": True}

    async def fetch(client: Any, feed: dict[str, Any]) -> tuple[list[dict[str, Any]], str | None]:
        if feed["name"] == "DW" and not up["ok"]:
            return [], "HTTP 503"
        return [{"title": f"{feed['name']} item", "link": f"{feed['url']}#{now.isoformat()}", "at": now - timedelta(minutes=5), "summary": ""}], None

    monkeypatch.setattr(sources, "fetch_feed", fetch)
    s = engine.settings_row(db)
    got = await engine.collect(db, s, None, now)
    dw = got["feed_health"][feeds[0]["url"]]
    assert dw["via"] == sources.WORLDMONITOR_VIA and dw["fails"] == 0 and dw["last_ok"] == now.isoformat()
    assert got["feed_health"][feeds[1]["url"]]["via"] is None
    up["ok"] = False
    for i in range(2):
        s.state = {**(s.state or {}), "feed_health": {"feeds": got["feed_health"]}}
        got = await engine.collect(db, s, None, now + timedelta(minutes=10 * (i + 1)))
    dw = got["feed_health"][feeds[0]["url"]]
    assert dw["problem"] == "HTTP 503" and dw["fails"] == 2 and dw["last_ok"] == now.isoformat()


def test_admin_decisions_name_the_outlets_and_the_catalog(db: Any) -> None:
    now = datetime.now(timezone.utc)
    db.add(NdEvent(key="k", kind="news", title="Missile strike hits Saudi oil terminal", headline="MISSILE STRIKE", topic="geopolitics", evidence="two outlets",
                   status="candidate", virality=60, facts={}, sources=[{"name": "DW", "url": "u1"}, {"name": "Bloomberg", "url": "u2"}]))
    db.commit()
    d = editorial.decisions(db, now + timedelta(hours=1))[0]
    assert d["outlets"] == ["Bloomberg", "DW"] and d["via_worldmonitor"] == ["DW"]
