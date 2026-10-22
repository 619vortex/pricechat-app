"""Short Market. TEST FIXTURES only: fake news feeds and market APIs (httpx.MockTransport) and a tiny fake MP4.
No real network; nothing is published anywhere (Short Market never publishes: the owner downloads)."""

import io
import json
from datetime import timedelta
from email.utils import format_datetime
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.short_market import SmIdea, SmShort
from app.short_market import defaults, ideas, queue
from tests.test_brand_manager import _mp4, _worker

P = "/api/short-market"


def _sources(request: httpx.Request) -> httpx.Response:
    """TEST FIXTURE: every source Short Market reads, with made-up numbers."""
    url = str(request.url)
    now = utcnow()
    if "federalreserve.gov" in url:
        return httpx.Response(500)  # one source down must not hide the others
    if "onthisday" not in url and ("rss" in url or "feed" in url):
        return httpx.Response(200, text=(
            "<?xml version='1.0'?><rss><channel>"
            f"<item><title>Central bank holds rates at 4.5%</title><link>https://news.test/rates?{request.url.host}</link>"
            f"<pubDate>{format_datetime(now - timedelta(hours=2))}</pubDate><description>Officials kept the rate at 4.5% for a third meeting.</description></item>"
            f"<item><title>Headline with no summary</title><link>https://news.test/bare?{request.url.host}</link>"
            f"<pubDate>{format_datetime(now - timedelta(hours=1))}</pubDate><description></description></item></channel></rss>"))
    if url.endswith("/global"):
        return httpx.Response(200, json={"data": {"total_market_cap": {"usd": 3_410_000_000_000}, "market_cap_change_percentage_24h_usd": -2.31,
                                                  "market_cap_percentage": {"btc": 57.4}}})
    if "alternative.me" in url:
        return httpx.Response(200, json={"data": [{"value": "31", "value_classification": "Fear"}, {"value": "52", "value_classification": "Neutral"}]})
    if "search/trending" in url:
        return httpx.Response(200, json={"coins": [{"item": {"id": "testcoin", "name": "Testcoin", "symbol": "tst", "market_cap_rank": 42,
                                                             "data": {"price_change_percentage_24h": {"usd": 18.4}}}}]})
    if "coins/markets" in url:
        return httpx.Response(200, json=[{"id": "bitcoin", "name": "Bitcoin", "symbol": "btc", "current_price": 67412.33, "market_cap_rank": 1,
                                          "price_change_percentage_24h_in_currency": 3.2, "price_change_percentage_7d_in_currency": -1.1,
                                          "total_volume": 31_000_000_000}])
    if "onthisday" in url:
        return httpx.Response(200, json={"events": [
            {"text": "The stock market crashes, wiping out billions of dollars in a single day.", "year": 1929,
             "pages": [{"title": "Crash", "normalizedtitle": "The Crash", "extract": "The crash was a major stock market crash. It started in autumn.",
                        "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Crash"}}}]},
            {"text": "A famous painter is born.", "year": 1881, "pages": []}]})
    return httpx.Response(404)


@pytest.fixture
def sm(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(client.app.state.runtime, "bm_transport", httpx.MockTransport(_sources), raising=False)  # type: ignore[attr-defined]
    return client


def _script(n: int = 7) -> dict[str, Any]:
    """A story that sticks to the mood brief (3.41 trillion, -2.31%, 31/100 Fear, 52 yesterday, 57.4% Bitcoin)."""
    lines = ["The crypto market just lost its nerve, and the numbers show it.",
             "Right now the whole market is worth about 3.41 trillion dollars.",
             "In the last day its value slipped by 2.31 percent.",
             "The Fear and Greed Index reads 31 out of 100, and that means fear.",
             "Yesterday that same index sat at 52, right in the middle.",
             "Bitcoin alone makes up 57.4 percent of everything out there.",
             "So the mood changed fast. Follow for the next chapter of this story."]
    segs = [{"text": t, "screen": "MOOD: FEAR" if k == 0 else f"Scene {k + 1}", "visual": "a nervous cartoon robot watches a giant glowing gauge swing to the left"}
            for k, t in enumerate(lines[:n])]
    return {"hook": lines[0], "segments": segs, "voiceover": " ".join(s["text"] for s in segs), "title": "Crypto's mood just flipped to fear",
            "description": "The Fear & Greed Index reads 31 today (alternative.me, CoinGecko).", "hashtags": ["#crypto", "bitcoin"],
            "storyboard": [{"scene": "story", "text": s["screen"]} for s in segs], "sources": [{"name": "CoinGecko"}], "engine": "test"}


def test_ideas_come_from_every_source_with_facts_and_survive_a_source_being_down(sm: TestClient, db: Session) -> None:
    r = sm.get(f"{P}/ideas").json()
    by_cat: dict[str, list[dict[str, Any]]] = {}
    for i in r["items"]:
        by_cat.setdefault(i["category"], []).append(i)
    assert {"markets", "news", "history", "facts"} <= set(by_cat)
    assert all(len(i["facts"]) >= 2 and i["sources"] for i in r["items"])  # a bare headline is never a story
    assert not any("no summary" in i["topic"] for i in r["items"])
    mood = next(i for i in by_cat["markets"] if "mood" in i["topic"])
    assert "Fear (31/100)" in mood["topic"] and any("$3.41 trillion" in f["text"] for f in mood["facts"])
    assert any("Testcoin" in i["topic"] for i in by_cat["markets"]) and any("Bitcoin is up 3.2%" in i["topic"] for i in by_cat["markets"])
    hist = by_cat["history"]
    assert len(hist) == 1 and "1929" in hist[0]["topic"] and any("wikipedia.org/wiki/Crash" in s["url"] for s in hist[0]["sources"])
    assert {i["sources"][0]["name"] for i in by_cat["news"]} >= {"CNBC", "MarketWatch", "CoinDesk"}  # the Federal Reserve feed was down
    scores = [i["score"] for i in r["items"]]
    assert scores == sorted(scores, reverse=True)
    assert sm.get(f"{P}/ideas?category=history").json()["items"] == hist
    assert sm.get(f"{P}/ideas?category=nope").status_code == 400
    # a second look within half an hour reads the stored list; old stories are dropped after three days
    n = db.query(SmIdea).count()
    assert sm.get(f"{P}/ideas").json()["refreshed_at"] == r["refreshed_at"] and db.query(SmIdea).count() == n
    row = db.query(SmIdea).first()
    row.found_at = utcnow() - timedelta(days=4)
    db.commit()
    ideas.store(db, [])
    assert db.query(SmIdea).count() == n - 1


def test_a_short_goes_from_idea_to_download_without_any_social_network(sm: TestClient, db: Session) -> None:
    w = _worker(sm)
    ov = sm.get(f"{P}/overview").json()
    assert ov["worker"]["registered"] == 1 and not ov["worker"]["online"] and len(ov["options"]["styles"]) == len(defaults.STYLES)
    assert sm.post(f"{P}/worker/claim", json={}, headers={"Authorization": "Bearer pcw_wrong"}).status_code == 401
    assert sm.post(f"{P}/worker/claim", json={}, headers=w).status_code == 204  # nothing to do yet

    mood = next(i for i in sm.get(f"{P}/ideas").json()["items"] if "mood" in i["topic"])
    assert sm.post(f"{P}/shorts", json={"idea_id": mood["id"], "style": "nope"}).status_code == 400
    s = sm.post(f"{P}/shorts", json={"idea_id": mood["id"], "style": "clay", "voice": "bm_george", "seconds": 30}).json()
    assert s["status"] == "queued" and s["stages"]["idea"]["status"] == "done" and s["stages"]["visuals"]["status"] == "waiting"

    job = sm.post(f"{P}/worker/claim", json={"info": {"video_ai": {"model": "test"}, "tts": ["kokoro"], "llm": ["test-llm"]}}, headers=w).json()
    assert job["kind"] == "short" and job["job_id"] == s["id"] and job["style"]["key"] == "clay" and "claymation" in job["style"]["prompt"]
    assert job["voice"] == "bm_george" and job["seconds"] == 30 and job["brief"]["numbers"]["fear_greed"] == 31 and job["angle"]
    ov = sm.get(f"{P}/overview").json()
    assert ov["worker"]["online"] and ov["worker"]["video_ai"] == {"model": "test"} and ov["counts"]["rendering"] == 1
    assert sm.post(f"{P}/worker/claim", json={}, headers=w).status_code == 204  # one worker, one short at a time

    # the script is checked like Brand Manager's: no invented numbers, no financial claims; plus the story's shape
    bad = _script()
    bad["voiceover"] += " It will hit 9 trillion next week, guaranteed."
    r = sm.post(f"{P}/worker/jobs/{s['id']}/script", json={"script": bad}, headers=w).json()
    assert not r["ok"] and any("9000000000000" in p for p in r["problems"]) and any("guaranteed" in p for p in r["problems"])
    r = sm.post(f"{P}/worker/jobs/{s['id']}/script", json={"script": _script(1)}, headers=w).json()
    assert not r["ok"] and any("scenes" in p for p in r["problems"])
    blind = _script()
    blind["segments"][2]["visual"] = ""
    assert any("Scene 3 has no picture" in p for p in sm.post(f"{P}/worker/jobs/{s['id']}/script", json={"script": blind}, headers=w).json()["problems"])
    r = sm.post(f"{P}/worker/jobs/{s['id']}/script", json={"script": _script()}, headers=w).json()
    assert r["ok"] and r["script"]["hashtags"] == ["#crypto", "#bitcoin"] and r["script"]["description"].endswith("Not financial advice.")
    assert sm.post(f"{P}/worker/jobs/{s['id']}/progress", json={"stage": "visuals", "status": "running", "note": "scene 2 of 7"}, headers=w).json() == {"ok": True, "cancel": False}
    assert sm.get(f"{P}/shorts/{s['id']}").json()["stages"]["visuals"]["note"] == "scene 2 of 7"

    data, meta = _mp4(31.0)
    wrong = sm.post(f"{P}/worker/jobs/{s['id']}/result", headers=w, data={"meta": json.dumps({**meta, "width": 720, "height": 1280})},
                    files={"video": ("v.mp4", io.BytesIO(data), "video/mp4")}).json()
    assert wrong["status"] == "queued"  # failed the final check: back in the queue, to be made again
    db.query(SmShort).update({SmShort.run_after: None})
    db.commit()
    assert sm.post(f"{P}/worker/claim", json={}, headers=w).json()["job_id"] == s["id"]
    assert sm.post(f"{P}/worker/jobs/{s['id']}/script", json={"script": _script()}, headers=w).json()["ok"]
    done = sm.post(f"{P}/worker/jobs/{s['id']}/result", headers=w, data={"meta": json.dumps({**meta, "renderer": "wan2.2+ffmpeg", "ai_scenes": 7, "scenes": 7})},
                   files={"video": ("v.mp4", io.BytesIO(data), "video/mp4"), "thumb": ("t.jpg", io.BytesIO(b"\xff\xd8jpg"), "image/jpeg")}).json()
    assert done == {"ok": True, "status": "ready"}

    v = sm.get(f"{P}/shorts/{s['id']}").json()
    assert v["status"] == "ready" and v["has_video"] and v["has_thumb"] and v["duration"] == 31.0 and v["downloaded_at"] is None
    assert v["render_info"]["renderer"] == "wan2.2+ffmpeg" and v["render_info"]["ai_scenes"] == 7 and v["stages"]["quality"]["status"] == "done"
    assert v["caption"].startswith("Crypto's mood just flipped to fear\n\n") and v["caption"].endswith("#crypto #bitcoin")
    assert sm.get(f"{P}/shorts/{s['id']}/media/video").content == data
    assert sm.get(f"{P}/shorts/{s['id']}/media/video", headers={"Range": "bytes=0-9"}).status_code == 206
    dl = sm.get(f"{P}/shorts/{s['id']}/media/video?download=1")
    assert dl.headers["content-disposition"] == f'attachment; filename="crypto-s-mood-just-flipped-to-fear-{s["id"]}.mp4"' and dl.content == data
    assert sm.get(f"{P}/shorts/{s['id']}").json()["downloaded_at"] is not None
    # the story is now marked as made, and "make me one" picks a different one
    listed = sm.get(f"{P}/ideas").json()["items"]
    assert next(i for i in listed if i["id"] == mood["id"])["used"] and listed[-1]["used"] and not listed[0]["used"]
    other = sm.post(f"{P}/shorts", json={"category": "markets"}).json()
    assert other["topic"] != s["topic"] and other["style"] == defaults.auto_style(s["id"])  # one button: the look rotates by itself
    assert defaults.MIN_SECONDS <= other["seconds"] <= defaults.MAX_SECONDS and other["seconds"] % 5 == 0  # and the length follows the story
    assert defaults.auto_seconds({"facts": [{"text": "one two three"}]}) == 10 and defaults.auto_seconds({"facts": [{"text": "w " * 400}]}) == 50
    assert sm.post(f"{P}/shorts", json={"idea_id": mood["id"], "seconds": 55}).status_code == 400

    # remake: the same story again in another look; delete removes the files
    again = sm.post(f"{P}/shorts/{s['id']}/remake", json={"style": "neon"}).json()
    assert again["status"] == "queued" and again["generation"] == 2 and again["style"] == "neon" and again["script"] == {}
    assert sm.delete(f"{P}/shorts/{s['id']}").json() == {"deleted": True}
    assert sm.get(f"{P}/shorts/{s['id']}/media/video").status_code == 404
    assert [x["id"] for x in sm.get(f"{P}/shorts").json()["items"]] == [other["id"]]


def test_a_crashed_worker_never_loses_a_short_and_a_remake_takes_it_away(sm: TestClient, db: Session) -> None:
    w = _worker(sm)
    idea = sm.get(f"{P}/ideas").json()["items"][0]
    s = sm.post(f"{P}/shorts", json={"idea_id": idea["id"]}).json()
    for attempt in range(1, defaults.MAX_ATTEMPTS + 1):
        assert sm.post(f"{P}/worker/claim", json={}, headers=w).json()["job_id"] == s["id"]
        row = db.get(SmShort, s["id"])
        db.refresh(row)
        assert row.status == "rendering" and row.attempts == attempt
        row.lease_until = utcnow() - timedelta(seconds=1)  # the worker went silent
        db.commit()
        queue.expire_leases(db, utcnow())
        db.commit()
    assert sm.get(f"{P}/shorts/{s['id']}").json()["status"] == "failed"  # three silent workers: it stops and says so
    assert sm.post(f"{P}/worker/claim", json={}, headers=w).status_code == 204

    sm.post(f"{P}/shorts/{s['id']}/remake", json={})
    assert sm.post(f"{P}/worker/claim", json={}, headers=w).json()["generation"] == 2
    sm.post(f"{P}/shorts/{s['id']}/remake", json={"seconds": 20})  # asked again while the worker is busy with the old take
    assert sm.post(f"{P}/worker/jobs/{s['id']}/progress", json={"stage": "voice", "status": "running"}, headers=w).status_code == 409
    assert sm.post(f"{P}/worker/jobs/{s['id']}/fail", json={"error": "x"}, headers=w).status_code == 409
    assert sm.post(f"{P}/shorts/{s['id']}/remake", json={"seconds": 7}).status_code == 400

    for i in sm.get(f"{P}/ideas").json()["items"][1:defaults.MAX_OPEN]:
        assert sm.post(f"{P}/shorts", json={"idea_id": i["id"]}).status_code == 200
    assert sm.post(f"{P}/shorts", json={"idea_id": idea["id"]}).status_code == 409  # the GPU makes one at a time: the queue is capped


def test_short_market_is_private_except_for_workers_with_a_token() -> None:
    from app.security.admin_auth import is_public

    assert is_public("POST", "/api/short-market/worker/claim")  # workers carry their own token
    assert not is_public("GET", "/api/short-market/shorts") and not is_public("GET", "/api/short-market/shorts/1/media/video")
    assert not is_public("POST", "/api/short-market/shorts")


def test_a_malformed_worker_report_is_refused_and_a_bad_thumbnail_dropped(sm: TestClient, db: Session) -> None:
    w = _worker(sm)
    mood = next(i for i in sm.get(f"{P}/ideas").json()["items"] if "mood" in i["topic"])
    s = sm.post(f"{P}/shorts", json={"idea_id": mood["id"], "style": "clay", "seconds": 30}).json()
    sm.post(f"{P}/worker/claim", json={"info": {}}, headers=w)
    assert sm.post(f"{P}/worker/jobs/{s['id']}/script", json={"script": _script()}, headers=w).json()["ok"]
    data, meta = _mp4(31.0)
    video = {"video": ("v.mp4", io.BytesIO(data), "video/mp4")}
    assert sm.post(f"{P}/worker/jobs/{s['id']}/result", headers=w, data={"meta": "[]"}, files=video).status_code == 400
    odd = sm.post(f"{P}/worker/jobs/{s['id']}/result", headers=w, data={"meta": json.dumps({**meta, "duration": "thirty"})},
                  files={"video": ("v.mp4", io.BytesIO(data), "video/mp4")})
    assert odd.status_code == 200 and odd.json()["status"] == "queued"  # failed the final check, not a crash
    db.query(SmShort).update({SmShort.run_after: None})
    db.commit()
    sm.post(f"{P}/worker/claim", json={}, headers=w)
    assert sm.post(f"{P}/worker/jobs/{s['id']}/script", json={"script": _script()}, headers=w).json()["ok"]
    done = sm.post(f"{P}/worker/jobs/{s['id']}/result", headers=w, data={"meta": json.dumps(meta)},
                   files={"video": ("v.mp4", io.BytesIO(data), "video/mp4"), "thumb": ("t.jpg", io.BytesIO(b"GIF89a..."), "image/jpeg")})
    assert done.json() == {"ok": True, "status": "ready"}
    v = sm.get(f"{P}/shorts/{s['id']}").json()
    assert v["has_video"] and not v["has_thumb"]
