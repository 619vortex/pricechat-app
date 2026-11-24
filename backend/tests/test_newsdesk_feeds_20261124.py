"""Broken feeds (2026-11-24 production logs: "The Guardian: HTTP 403" ~141 times a day, "GDELT: ConnectTimeout" on 82 of 82 reads).

- The Guardian refuses a client calling itself "Mozilla/5.0" that isn't a browser; the same request without that prefix is answered.
- A feed failing read after read rests instead of being fetched and logged every tick; the owner hears once, after 6 h, from the watchdog.
- Trending sources (GDELT) rest the same way after repeated misses, without alerting for a source that never alerts.
- New world outlets are spelled as the outlet-trust lists spell them, so two-outlet verification counts them.
No network: every answer comes from httpx.MockTransport or a stubbed fetch_feed."""
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import pytest

from app.newsdesk import engine, sources

NOW = datetime(2026, 11, 24, 6, 0, tzinfo=timezone.utc)
RSS = ("<rss><channel><item><title>Markets slide as oil jumps</title><link>https://feed.test/1</link>"
       "<pubDate>Tue, 24 Nov 2026 05:50:00 GMT</pubDate></item></channel></rss>")


def _guardian_like(seen: list[str]) -> httpx.MockTransport:
    """Answers like The Guardian's CDN did: 403 to a non-browser calling itself Mozilla, the feed to an honest reader."""
    def handler(request: httpx.Request) -> httpx.Response:
        ua = request.headers.get("user-agent", "")
        seen.append(ua)
        return httpx.Response(403, text="Forbidden") if ua.startswith("Mozilla/") else httpx.Response(200, text=RSS)
    return httpx.MockTransport(handler)


# --- fetch_feed: the honest User-Agent -------------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_guardian_feeds_are_read_with_the_plain_agent_first_time() -> None:
    guardian = [f for f in sources.EXTRA_FEEDS if f["name"] == "The Guardian"]
    assert len(guardian) == 3 and all(f.get("agent") == "plain" for f in guardian)
    seen: list[str] = []
    async with httpx.AsyncClient(transport=_guardian_like(seen)) as client:
        items, problem = await sources.fetch_feed(client, guardian[0])
    assert problem is None and len(items) == 1
    assert seen == [sources.PLAIN_UA["User-Agent"]] and "Mozilla" not in seen[0] and "PriceChatNewsDesk" in seen[0]


@pytest.mark.asyncio
async def test_a_feed_answering_403_is_asked_once_more_with_the_plain_agent() -> None:
    seen: list[str] = []  # e.g. the Guardian's address stored in Admin, without the "agent" key
    async with httpx.AsyncClient(transport=_guardian_like(seen)) as client:
        items, problem = await sources.fetch_feed(client, {"name": "The Guardian", "url": "https://www.theguardian.com/world/rss"})
    assert problem is None and len(items) == 1
    assert seen == [sources.UA["User-Agent"], sources.PLAIN_UA["User-Agent"]]


@pytest.mark.asyncio
@pytest.mark.parametrize("status,requests", [(403, 2), (406, 2), (503, 1), (404, 1)])
async def test_only_a_refusal_is_retried_and_only_once(status: int, requests: int) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.headers.get("user-agent", ""))
        return httpx.Response(status)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        items, problem = await sources.fetch_feed(client, {"name": "X", "url": "https://down.test/rss"})
    assert items == [] and problem == f"HTTP {status}" and len(calls) == requests


# --- new outlets: consistent with the outlet-trust lists -------------------------------------------------------------------------------


def test_new_world_outlets_are_recognised_wherever_outlets_are_counted() -> None:
    from app.trending import rank, sensitive
    from app.trending import sources as tsources

    new = {"The Independent", "Sky News", "ABC News", "The Washington Post", "South China Morning Post"}
    feeds = {f["name"]: f for f in sources.EXTRA_FEEDS}
    assert new <= set(feeds) and all(feeds[n]["tier"] == 1 for n in new)
    assert "https://feeds.bbci.co.uk/news/rss.xml" in {f["url"] for f in sources.EXTRA_FEEDS if f["name"] == "BBC"}
    for name in new | {"BBC", "The Guardian"}:
        assert tsources.reliable_outlet(name), name
    mainstream = {sensitive.outlet_key(m) for m in rank.MAINSTREAM}
    for name in new - {"South China Morning Post"}:
        assert sensitive.outlet_key(name) in mainstream, name
    # one outlet under one name: "The Washington Post" and "Washington Post" count once
    assert sensitive.outlet_key("The Washington Post") == sensitive.outlet_key("Washington Post")


def test_no_feed_address_is_read_twice() -> None:
    every = [f["url"] for f in [*sources.DEFAULT_FEEDS, *sources.EXTRA_FEEDS, *sources.WORLDMONITOR_FEEDS]]
    extra = [f["url"] for f in [*sources.EXTRA_FEEDS, *sources.WORLDMONITOR_FEEDS]]
    assert len(extra) == len(set(extra))
    assert all(u.startswith("https://") for u in every)
    # feeds_for drops an addition the stored list already has
    got = [f["url"] for f in sources.feeds_for([{"name": "BBC", "url": "https://feeds.bbci.co.uk/news/rss.xml"}], {})]
    assert got.count("https://feeds.bbci.co.uk/news/rss.xml") == 1


# --- feed health: rest, log once, recover ----------------------------------------------------------------------------------------------


def test_feed_resting_schedule() -> None:
    def prev(fails: int, minutes_ago: float) -> dict[str, Any]:
        return {"fails": fails, "checked": (NOW - timedelta(minutes=minutes_ago)).isoformat()}

    assert not engine.feed_resting({}, NOW) and not engine.feed_resting(prev(2, 0), NOW)  # two failures: still read every tick
    assert engine.feed_resting(prev(3, 5), NOW) and not engine.feed_resting(prev(3, 10), NOW)  # 10 min after the third
    assert engine.feed_resting(prev(4, 15), NOW) and not engine.feed_resting(prev(4, 20), NOW)  # then 20 min
    assert engine.feed_resting(prev(30, 59), NOW) and not engine.feed_resting(prev(30, 60), NOW)  # never more than an hour
    assert not engine.feed_resting({"fails": 9}, NOW)  # no record of when it was last read: read it


@pytest.mark.asyncio
async def test_a_dead_feed_rests_logs_once_and_is_seen_again_when_it_answers(db: Any, monkeypatch: pytest.MonkeyPatch,
                                                                            caplog: pytest.LogCaptureFixture) -> None:
    feeds = [{"name": "The Guardian", "url": "https://www.theguardian.com/world/rss", "tier": 1, "category": "politics"},
             {"name": "BBC", "url": "https://feeds.bbci.co.uk/news/world/rss.xml", "tier": 1, "category": "politics"}]
    monkeypatch.setattr(sources, "feeds_for", lambda stored, state: feeds)
    up = {"guardian": False}
    fetched: list[str] = []

    async def fetch(client: Any, feed: dict[str, Any]) -> tuple[list[dict[str, Any]], str | None]:
        fetched.append(feed["name"])
        if feed["name"] == "The Guardian" and not up["guardian"]:
            return [], "HTTP 403"
        return [{"title": f"{feed['name']} {len(fetched)}", "link": f"{feed['url']}#{len(fetched)}", "at": NOW - timedelta(minutes=5), "summary": ""}], None

    monkeypatch.setattr(sources, "fetch_feed", fetch)
    s = engine.settings_row(db)
    caplog.set_level(logging.INFO, logger="app.newsdesk.engine")

    async def tick(minutes: int) -> dict[str, Any]:
        got = await engine.collect(db, s, None, NOW + timedelta(minutes=minutes))
        s.state = {**(s.state or {}), "feed_health": {"feeds": got["feed_health"]}}
        return got

    for m in (0, 5, 10):  # three failures in a row: read every tick
        got = await tick(m)
    g = got["feed_health"][feeds[0]["url"]]
    assert fetched.count("The Guardian") == 3 and g["fails"] == 3 and g["failing_since"] == NOW.isoformat() and g["last_ok"] is None
    for m in (15, 19):  # resting: not fetched, still shown as failing in Admin, BBC read as usual
        got = await tick(m)
    assert fetched.count("The Guardian") == 3 and fetched.count("BBC") == 5
    g = got["feed_health"][feeds[0]["url"]]
    assert g["resting"] and g["problem"] == "HTTP 403" and g["fails"] == 3 and got["feed_problems"][feeds[0]["url"]] == "HTTP 403"
    got = await tick(21)  # rest over: read again (fourth failure: next rest is 20 min)
    assert fetched.count("The Guardian") == 4 and got["feed_health"][feeds[0]["url"]]["fails"] == 4
    assert engine.feed_resting(got["feed_health"][feeds[0]["url"]], NOW + timedelta(minutes=40))
    logged = [r.getMessage() for r in caplog.records if "The Guardian" in r.getMessage()]
    assert len(logged) == 2 and "feed problems: The Guardian: HTTP 403" in logged[0] and "3 reads in a row" in logged[1]  # not one per tick
    up["guardian"] = True
    got = await tick(42)
    g = got["feed_health"][feeds[0]["url"]]
    assert g["problem"] is None and g["fails"] == 0 and g["failing_since"] is None and not g.get("resting") and g["items"] == 1
    assert feeds[0]["url"] not in got["feed_problems"]
    assert any("answering again: The Guardian" in r.getMessage() for r in caplog.records)


def test_the_watchdog_counts_a_never_good_feed_from_its_first_failure(db: Any) -> None:
    from app.observability import DEAD_FEED_AFTER, dead_feeds

    assert DEAD_FEED_AFTER == timedelta(hours=6)
    s = engine.settings_row(db)
    s.automation = True
    s.state = {"feed_health": {"feeds": {
        "https://new.test/rss": {"name": "New outlet", "tier": 1, "problem": "HTTP 403", "fails": 12, "last_ok": None,
                                 "failing_since": (NOW - timedelta(hours=7)).isoformat()},
        "https://blip.test/rss": {"name": "Blip", "tier": 1, "problem": "ReadTimeout", "fails": 5, "last_ok": None,
                                  "failing_since": (NOW - timedelta(hours=2)).isoformat()},
        "https://old.test/rss": {"name": "Old", "tier": 0, "problem": "HTTP 404", "fails": 20, "last_ok": (NOW - timedelta(hours=6, minutes=5)).isoformat()},
    }}}
    db.commit()
    dead = dead_feeds(db, NOW)
    assert len(dead) == 2
    assert dead[0].startswith("New outlet: HTTP 403, failing since") and dead[1].startswith("Old (official source): HTTP 404, last good read")


# --- Trending sources (GDELT): rest after repeated misses, no new alert ----------------------------------------------------------------


def test_a_source_missing_read_after_read_rests() -> None:
    from app.trending import engine as tengine

    def state(misses: int, minutes_ago: int) -> dict[str, Any]:
        return {"health": {"gdelt": {"last_scan": (NOW - timedelta(minutes=minutes_ago)).isoformat(), "misses": misses}}}

    assert tengine._due("gdelt", state(0, 10), NOW) and tengine._due("gdelt", state(2, 10), NOW)  # every 10 min while it answers
    assert not tengine._due("gdelt", state(3, 15), NOW) and tengine._due("gdelt", state(3, 20), NOW)  # then 20, 40 ...
    assert not tengine._due("gdelt", state(4, 39), NOW) and tengine._due("gdelt", state(4, 40), NOW)
    assert not tengine._due("gdelt", state(20, 59), NOW) and tengine._due("gdelt", state(20, 60), NOW)  # never more than an hour
    assert tengine._due("news", {"health": {"news": {"last_scan": NOW.isoformat(), "misses": 9}}}, NOW)  # our own store: never rests


@pytest.mark.asyncio
async def test_gdelt_timeouts_count_as_misses_not_alerts(db: Any) -> None:
    from app.config.settings import get_settings
    from app.trending import engine as tengine

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out", request=request)

    s = tengine.settings_row(db)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        for i in range(3):
            out = await tengine.read_sources(db, s, get_settings(), None, NOW + timedelta(minutes=10 * i), client, only={"gdelt"})
    h = out["health"]["gdelt"]
    assert h["problem"] == "GDELT: ConnectTimeout" and h["misses"] == 3
    assert h["fails"] == 0 and h["status"] == "idle"  # GDELT is momentum only: never an owner alert (check_health alerts on status "error")
    assert not tengine._due("gdelt", s.state, NOW + timedelta(minutes=30)) and tengine._due("gdelt", s.state, NOW + timedelta(minutes=40))
