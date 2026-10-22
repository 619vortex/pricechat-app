"""YouTube integration (read-only) → content items → website Latest Content.

Every YouTube and Google request goes to the in-memory FakeYouTube: no real quota is used.
"""

from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.content_items import ContentItem, ContentItemMetric
from app.youtube.service import classify, parse_channel_ref, parse_duration
from tests.fakes import YT_CHANNEL, YT_CLIENT_ID, YT_CLIENT_SECRET, YT_KEY, YT_OTHER_CHANNEL, FakeYouTube


def _connect(c: TestClient, channel: str = "@pricechat") -> dict:
    r = c.put("/api/youtube/api-key", json={"api_key": YT_KEY, "channel": channel})
    assert r.status_code == 200, r.text
    return r.json()


def _sync(c: TestClient) -> dict:
    r = c.post("/api/youtube/sync")
    assert r.status_code == 200, r.text
    return r.json()["result"]


def _latest(c: TestClient, limit: int = 3) -> list[dict]:
    return c.get("/api/website/latest", params={"limit": limit}).json()["items"]


# --- units ---------------------------------------------------------------------------------------------------


def test_duration_and_short_detection_rules() -> None:
    assert parse_duration("PT45S") == 45 and parse_duration("PT2M5S") == 125 and parse_duration("PT1H2M") == 3720
    assert parse_duration("P0D") is None and parse_duration(None) is None and parse_duration("bogus") is None
    assert classify(45, 4608, 8192)[0] == "short"  # vertical
    assert classify(170, 8192, 8192)[0] == "short"  # square, up to 3 minutes
    assert classify(50, 14563, 8192)[0] == "long_form"  # horizontal, even if short
    assert classify(181, 4608, 8192)[0] == "long_form"  # longer than 3 minutes
    assert classify(55, None, None)[0] == "short"  # no aspect info: up to 60 s
    assert classify(120, None, None, "My video #shorts")[0] == "short"
    assert classify(120, None, None, "Shorts compilation")[0] == "long_form"  # the word alone is not enough
    assert classify(None, None, None)[0] == "long_form"


def test_channel_reference_parsing() -> None:
    assert parse_channel_ref("@pricechat") == (None, "@pricechat")
    assert parse_channel_ref("pricechat") == (None, "@pricechat")
    assert parse_channel_ref("https://www.youtube.com/@pricechat/shorts") == (None, "@pricechat")
    assert parse_channel_ref(f"https://www.youtube.com/channel/{YT_CHANNEL}") == (YT_CHANNEL, None)
    with pytest.raises(ValueError):
        parse_channel_ref("not a channel!!")


# --- connection ------------------------------------------------------------------------------------------------


def test_connect_with_api_key_and_never_expose_it(yt_client: TestClient, fake_youtube: FakeYouTube) -> None:
    st = _connect(yt_client)
    assert st["status"] == "connected" and st["mode"] == "api_key"
    ch = st["channel"]
    assert ch["id"] == YT_CHANNEL and ch["title"] == "Price Chat" and ch["handle"] == "@pricechat" and ch["subscribers"] == 1500
    assert YT_KEY not in str(st) and YT_KEY not in str(yt_client.get("/api/youtube").json())
    assert st["api_key"]["configured"] is True
    tested = yt_client.post("/api/youtube/test").json()
    assert tested["last_success_at"] and tested["last_error"] is None


def test_bad_key_is_rejected_and_nothing_saved(yt_client: TestClient) -> None:
    r = yt_client.put("/api/youtube/api-key", json={"api_key": "wrong-key", "channel": "@pricechat"})
    assert r.status_code == 401 and "wrong-key" not in r.text
    assert yt_client.get("/api/youtube").json()["status"] == "not_configured"
    assert yt_client.put("/api/youtube/api-key", json={"api_key": YT_KEY, "channel": "@nobody"}).status_code == 422


def test_oauth_flow_with_channel_selection(yt_client: TestClient, fake_youtube: FakeYouTube) -> None:
    fake_youtube.mine = [YT_CHANNEL, YT_OTHER_CHANNEL]  # brand account with two channels
    assert yt_client.post("/api/youtube/oauth/start").status_code == 422  # client not saved yet
    assert yt_client.put("/api/youtube/oauth-client", json={"client_id": "not-a-client", "client_secret": "x"}).status_code == 422
    yt_client.put("/api/youtube/oauth-client", json={"client_id": YT_CLIENT_ID, "client_secret": YT_CLIENT_SECRET})
    url = yt_client.post("/api/youtube/oauth/start").json()["url"]
    q = parse_qs(urlparse(url).query)
    assert q["scope"] == ["https://www.googleapis.com/auth/youtube.readonly"] and q["code_challenge_method"] == ["S256"]
    assert q["redirect_uri"] == ["http://127.0.0.1:8000/api/youtube/oauth/callback"] and q["access_type"] == ["offline"]
    state = q["state"][0]
    bad = yt_client.get("/api/youtube/oauth/callback", params={"code": "good-code", "state": "forged"}, follow_redirects=False)
    assert bad.status_code == 303 and bad.headers["location"].endswith("/youtube?oauth=failed")
    ok = yt_client.get("/api/youtube/oauth/callback", params={"code": "good-code", "state": state}, follow_redirects=False)
    assert ok.status_code == 303 and ok.headers["location"] == "http://127.0.0.1:5173/admin/youtube?oauth=ok"
    st = yt_client.get("/api/youtube").json()
    assert st["mode"] == "oauth" and st["channel"] is None and len(st["channels_available"]) == 2  # explicit choice required
    assert yt_client.post("/api/youtube/sync").status_code == 409
    assert yt_client.post("/api/youtube/channel", json={"channel_id": "UCnotmine0000000000000000"[:24]}).status_code == 422
    chosen = yt_client.post("/api/youtube/channel", json={"channel_id": YT_CHANNEL}).json()
    assert chosen["channel"]["id"] == YT_CHANNEL and "refresh-token-1" not in str(chosen) and "access-" not in str(chosen)
    replay = yt_client.get("/api/youtube/oauth/callback", params={"code": "good-code", "state": state}, follow_redirects=False)
    assert replay.headers["location"].endswith("oauth=failed")  # a state works once


def test_oauth_cancel_and_token_refusal(yt_client: TestClient, fake_youtube: FakeYouTube) -> None:
    yt_client.put("/api/youtube/oauth-client", json={"client_id": YT_CLIENT_ID, "client_secret": YT_CLIENT_SECRET})
    r = yt_client.get("/api/youtube/oauth/callback", params={"error": "access_denied"}, follow_redirects=False)
    assert r.headers["location"].endswith("oauth=cancelled")
    state = parse_qs(urlparse(yt_client.post("/api/youtube/oauth/start").json()["url"]).query)["state"][0]
    r = yt_client.get("/api/youtube/oauth/callback", params={"code": "bad-code", "state": state}, follow_redirects=False)
    assert r.headers["location"].endswith("oauth=failed")
    assert yt_client.get("/api/youtube").json()["oauth_signed_in"] is False


def test_disconnect_keeps_content_and_forgets_credentials(yt_client: TestClient, fake_youtube: FakeYouTube) -> None:
    fake_youtube.upload("v1", "First Short")
    _connect(yt_client)
    _sync(yt_client)
    st = yt_client.delete("/api/youtube").json()
    assert st["status"] == "not_configured" and st["api_key"]["configured"] is False
    assert [i["title"] for i in _latest(yt_client)] == ["First Short"]  # the website keeps working
    assert yt_client.post("/api/jobs/youtube.sync/run").json()["status"] == "skipped"


# --- ingestion -------------------------------------------------------------------------------------------------------


def test_ingestion_detection_and_website(yt_client: TestClient, fake_youtube: FakeYouTube, db: Session) -> None:
    fake_youtube.upload("long1", "Market deep dive", seconds=1260, size=(1920, 1080), published="2026-10-01T10:00:00Z")
    fake_youtube.upload("short1", "Is Bitcoin next?", seconds=40, published="2026-10-02T10:00:00Z", description="#shorts #btc")
    fake_youtube.upload("hshort", "Quick chart", seconds=50, size=(1920, 1080), published="2026-10-02T12:00:00Z")
    fake_youtube.upload("other", "Not ours", channel=YT_OTHER_CHANNEL)
    _connect(yt_client)
    res = _sync(yt_client)
    assert res["new"] == 3 and res["listed"] == 3
    items = {i["external_id"]: i for i in yt_client.get("/api/youtube/videos").json()}
    assert set(items) == {"long1", "short1", "hshort"}  # nothing from the unrelated channel
    s = items["short1"]
    assert s["content_type"] == "short" and s["url"] == "https://www.youtube.com/shorts/short1" and s["duration_seconds"] == 40
    assert s["thumbnail_url"] == "https://i.ytimg.com/vi/short1/hqdefault.jpg" and s["metrics"] == {"views": 100, "likes": 7, "comments": 2}
    assert s["channel_id"] == YT_CHANNEL and s["website_visible"] and s["privacy_status"] == "public"
    assert items["long1"]["content_type"] == "long_form" and items["long1"]["url"] == "https://www.youtube.com/watch?v=long1"
    assert items["hshort"]["content_type"] == "long_form" and "horizontal" in items["hshort"]["type_reason"]

    latest = _latest(yt_client)
    assert [i["external_id"] for i in latest] == ["hshort", "short1", "long1"]  # newest first
    card = latest[1]
    assert card == {"platform": "youtube", "external_id": "short1", "type": "short", "type_label": "Short", "title": "Is Bitcoin next?",
                    "url": "https://www.youtube.com/shorts/short1", "thumbnail": "https://i.ytimg.com/vi/short1/hqdefault.jpg",
                    "duration": "0:40", "published_at": "2026-10-02T10:00:00+00:00", "featured": False,
                    "views": 100, "series": None, "category": None}  # public fields only (views: YouTube's public count)
    st = yt_client.get("/api/youtube").json()
    assert st["content"]["shorts"] == 1 and st["content"]["long_form"] == 2 and st["content"]["latest"]["external_id"] == "hshort"
    assert st["performance"] == {"views": 300, "likes": 21, "comments": 6}


def test_resync_never_duplicates_and_updates_metadata(yt_client: TestClient, fake_youtube: FakeYouTube, db: Session) -> None:
    fake_youtube.upload("v1", "Old title", views=10)
    _connect(yt_client)
    _sync(yt_client)
    fake_youtube.videos["v1"].update(title="New title", views=55, description="updated")
    res = _sync(yt_client)
    assert res["new"] == 0 and res["updated"] == 1
    assert db.scalar(select(func.count()).select_from(ContentItem)) == 1
    item = yt_client.get("/api/youtube/videos").json()[0]
    assert item["title"] == "New title" and item["metrics"]["views"] == 55


def test_new_upload_is_found_incrementally_and_shown(yt_client: TestClient, fake_youtube: FakeYouTube) -> None:
    for n in range(60):  # more than one playlist page
        fake_youtube.upload(f"old{n}", f"Old video {n}", published=f"2026-09-{1 + n % 28:02d}T10:00:00Z")
    _connect(yt_client)
    assert _sync(yt_client)["new"] == 60
    fake_youtube.upload("fresh", "Brand new Short", published="2026-10-03T11:00:00Z")
    fake_youtube.calls.clear()
    res = _sync(yt_client)
    assert res["new"] == 1 and _latest(yt_client)[0]["title"] == "Brand new Short"
    pages = [c for c in fake_youtube.calls if c[0] == "playlistItems"]
    assert len(pages) == 1 and res["requests"] <= 4  # incremental: one playlist page, a couple of videos calls


def test_manual_hide_show_type_and_featured(yt_client: TestClient, fake_youtube: FakeYouTube) -> None:
    fake_youtube.upload("a", "First", published="2026-10-01T10:00:00Z")
    fake_youtube.upload("b", "Second", published="2026-10-02T10:00:00Z")
    _connect(yt_client)
    _sync(yt_client)
    ids = {i["external_id"]: i["id"] for i in yt_client.get("/api/content-items").json()}
    hidden = yt_client.patch(f"/api/content-items/{ids['b']}", json={"website_visible": False}).json()
    assert hidden["website_visible"] is False and hidden["visibility_source"] == "manual"
    _sync(yt_client)  # a sync never overrides a manual choice
    assert [i["external_id"] for i in _latest(yt_client)] == ["a"]
    yt_client.patch(f"/api/content-items/{ids['b']}", json={"website_visible": True})
    assert [i["external_id"] for i in _latest(yt_client)] == ["b", "a"]
    t = yt_client.patch(f"/api/content-items/{ids['a']}", json={"content_type": "long_form", "featured": True}).json()
    assert t["content_type"] == "long_form" and t["type_source"] == "manual" and t["featured"] and t["url"].endswith("watch?v=a")
    _sync(yt_client)
    assert next(i for i in yt_client.get("/api/content-items").json() if i["external_id"] == "a")["content_type"] == "long_form"
    back = yt_client.patch(f"/api/content-items/{ids['a']}", json={"content_type": "auto"}).json()
    assert back["type_source"] == "auto"
    _sync(yt_client)
    assert next(i for i in yt_client.get("/api/content-items").json() if i["external_id"] == "a")["content_type"] == "short"
    assert yt_client.patch("/api/content-items/999", json={"featured": True}).status_code == 404


def test_thumbnail_fallback(yt_client: TestClient, fake_youtube: FakeYouTube) -> None:
    fake_youtube.upload("nothumb", "No thumbnails yet", thumbs=False)
    _connect(yt_client)
    _sync(yt_client)
    assert _latest(yt_client)[0]["thumbnail"] == "https://i.ytimg.com/vi/nothumb/hqdefault.jpg"


def test_deleted_and_private_videos_leave_the_website(yt_client: TestClient, fake_youtube: FakeYouTube, db: Session) -> None:
    fake_youtube.upload("keep", "Keeps going", published="2026-10-01T10:00:00Z")
    fake_youtube.upload("gone", "Will be deleted", published="2026-10-02T10:00:00Z")
    fake_youtube.upload("priv", "Will go private", published="2026-10-02T11:00:00Z")
    _connect(yt_client)
    _sync(yt_client)
    assert len(_latest(yt_client)) == 3
    fake_youtube.delete("gone")
    fake_youtube.videos["priv"]["privacy"] = "private"
    res = _sync(yt_client)
    assert res["unavailable"] == 2 and [i["external_id"] for i in _latest(yt_client)] == ["keep"]
    rows = {i["external_id"]: i for i in yt_client.get("/api/content-items").json()}
    assert rows["gone"]["available"] is False and rows["gone"]["on_website"] is False  # history kept internally
    fake_youtube.videos["priv"]["privacy"] = "public"  # made public again → back on the website
    _sync(yt_client)
    assert [i["external_id"] for i in _latest(yt_client)] == ["priv", "keep"]


def test_oauth_sees_unlisted_and_keeps_it_off_the_website(yt_client: TestClient, fake_youtube: FakeYouTube) -> None:
    fake_youtube.upload("pub", "Public Short")
    fake_youtube.upload("unl", "Unlisted Short", privacy="unlisted")
    yt_client.put("/api/youtube/oauth-client", json={"client_id": YT_CLIENT_ID, "client_secret": YT_CLIENT_SECRET})
    state = parse_qs(urlparse(yt_client.post("/api/youtube/oauth/start").json()["url"]).query)["state"][0]
    yt_client.get("/api/youtube/oauth/callback", params={"code": "good-code", "state": state}, follow_redirects=False)
    assert yt_client.get("/api/youtube").json()["channel"]["id"] == YT_CHANNEL  # one channel: selected automatically
    _sync(yt_client)
    rows = {i["external_id"]: i for i in yt_client.get("/api/content-items").json()}
    assert rows["unl"]["privacy_status"] == "unlisted" and rows["unl"]["website_visible"] is False
    assert [i["external_id"] for i in _latest(yt_client)] == ["pub"]


# --- failure, scheduler, snapshots -----------------------------------------------------------------------------------


def test_api_failure_keeps_serving_last_content(yt_client: TestClient, fake_youtube: FakeYouTube) -> None:
    fake_youtube.upload("v1", "Still here")
    _connect(yt_client)
    _sync(yt_client)
    fake_youtube.error = (500, {"error": {"code": 500, "message": "backendError"}})
    r = yt_client.post("/api/youtube/sync")
    assert r.status_code == 502
    assert [i["title"] for i in _latest(yt_client)] == ["Still here"]  # website unaffected
    st = yt_client.get("/api/youtube").json()
    assert "server error" in st["last_error"] and st["status"] == "connected"
    fake_youtube.error = (403, {"error": {"code": 403, "message": "quota", "errors": [{"reason": "quotaExceeded"}]}})
    assert yt_client.post("/api/youtube/sync").status_code == 429
    status = yt_client.get("/api/website/status").json()
    assert status["source"] == "YouTube" and status["visible_items"] == 1 and status["last_sync_at"]


def test_scheduled_job(yt_client: TestClient, fake_youtube: FakeYouTube) -> None:
    assert yt_client.post("/api/jobs/youtube.sync/run").json()["status"] == "skipped"
    _connect(yt_client)
    fake_youtube.upload("v1", "Uploaded while you were away")
    r = yt_client.post("/api/jobs/youtube.sync/run").json()
    assert r["status"] == "success" and r["result"]["new"] == 1
    assert _latest(yt_client)[0]["title"] == "Uploaded while you were away"
    events = [a["message"] for a in yt_client.get("/api/activity").json()]
    assert any("New YouTube Short detected: Uploaded while you were away. Shown on the website automatically." in m for m in events)


def test_metric_snapshots(yt_client: TestClient, fake_youtube: FakeYouTube, db: Session) -> None:
    fake_youtube.upload("v1", "Growing", published=(utcnow() - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ"), views=10)
    _connect(yt_client)
    assert _sync(yt_client)["metric_snapshots"] == 1
    assert _sync(yt_client)["metric_snapshots"] == 0  # not again within the hour
    for m in db.scalars(select(ContentItemMetric)):
        m.captured_at = utcnow() - timedelta(hours=2)
    db.commit()
    fake_youtube.videos["v1"]["views"] = 40
    assert _sync(yt_client)["metric_snapshots"] == 1
    views = [m.views for m in db.scalars(select(ContentItemMetric).order_by(ContentItemMetric.id))]
    assert views == [10, 40]
    fake_youtube.videos["v1"]["views"] = None  # hidden by YouTube → unknown, never zero
    db.expire_all()
    for m in db.scalars(select(ContentItemMetric)):
        m.captured_at = utcnow() - timedelta(hours=3)
    db.commit()
    _sync(yt_client)
    assert db.scalar(select(ContentItemMetric).order_by(ContentItemMetric.id.desc())).views is None


def test_website_endpoints_are_read_only_and_never_call_youtube(yt_client: TestClient, fake_youtube: FakeYouTube) -> None:
    fake_youtube.upload("v1", "Cached")
    _connect(yt_client)
    _sync(yt_client)
    fake_youtube.calls.clear()
    for _ in range(3):
        _latest(yt_client)
        yt_client.get("/api/website/content")
    assert fake_youtube.calls == []
    assert yt_client.get("/api/website/latest", params={"limit": 50}).status_code == 422


# --- quota and sign-in failures ------------------------------------------------------------------------------------------


def test_quota_resets_at_midnight_pacific_in_utc() -> None:
    from datetime import datetime, timezone

    from app.youtube.service import quota_reset_after

    summer = quota_reset_after(datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc))  # PDT, UTC-7
    assert summer == datetime(2026, 7, 2, 7, 1, tzinfo=timezone.utc)
    winter = quota_reset_after(datetime(2026, 12, 1, 9, 0, tzinfo=timezone.utc))  # 01:00 PST that day, UTC-8
    assert winter == datetime(2026, 12, 2, 8, 1, tzinfo=timezone.utc)


def test_quota_usage_is_counted_in_youtubes_pacific_day() -> None:
    from datetime import datetime, timezone

    from app.youtube.service import quota_day

    assert quota_day(datetime(2026, 10, 23, 3, 0, tzinfo=timezone.utc)) == "2026-10-22"  # 20:00 PDT the day before
    assert quota_day(datetime(2026, 10, 23, 8, 0, tzinfo=timezone.utc)) == "2026-10-23"


def test_a_used_up_daily_quota_pauses_syncing_until_it_resets(yt_client: TestClient, fake_youtube: FakeYouTube, db: Session) -> None:
    from app.connectors.state import get_connector_state
    from app.models.enums import Platform

    fake_youtube.upload("v1", "Before the quota ran out")
    _connect(yt_client)
    _sync(yt_client)
    fake_youtube.error = (403, {"error": {"code": 403, "message": "quota", "errors": [{"reason": "quotaExceeded"}]}})
    assert yt_client.post("/api/youtube/sync").status_code == 429
    calls = len(fake_youtube.calls)
    fake_youtube.error = None
    # until the reset nothing calls YouTube: the scheduled job waits (skipped, not failed) and Sync now says why
    r = yt_client.post("/api/jobs/youtube.sync/run").json()
    assert r["status"] == "skipped" and "quota" in str(r)
    again = yt_client.post("/api/youtube/sync")
    assert again.status_code == 429 and "midnight Pacific" in again.json()["detail"]
    assert len(fake_youtube.calls) == calls
    assert yt_client.get("/api/youtube").json()["quota_today"]["paused_until"]
    # the quota is back after the reset
    state = get_connector_state(db, Platform.YOUTUBE)
    state.capabilities = {**state.capabilities, "quota_paused_until": (utcnow() - timedelta(minutes=1)).isoformat()}
    db.commit()
    assert yt_client.post("/api/jobs/youtube.sync/run").json()["status"] == "success"


def test_a_short_rate_limit_doesnt_pause_for_the_day(yt_client: TestClient, fake_youtube: FakeYouTube) -> None:
    _connect(yt_client)
    fake_youtube.error = (403, {"error": {"code": 403, "message": "slow down", "errors": [{"reason": "rateLimitExceeded"}]}})
    assert yt_client.post("/api/youtube/sync").status_code == 429
    fake_youtube.error = None
    assert yt_client.post("/api/youtube/sync").status_code == 200


def test_a_revoked_google_sign_in_shows_as_an_error_not_connected(yt_client: TestClient, fake_youtube: FakeYouTube) -> None:
    yt_client.put("/api/youtube/oauth-client", json={"client_id": YT_CLIENT_ID, "client_secret": YT_CLIENT_SECRET})
    state = parse_qs(urlparse(yt_client.post("/api/youtube/oauth/start").json()["url"]).query)["state"][0]
    yt_client.get("/api/youtube/oauth/callback", params={"code": "good-code", "state": state}, follow_redirects=False)
    _sync(yt_client)
    assert yt_client.get("/api/youtube").json()["status"] == "connected"
    # the owner removed Price Chat's access in their Google account; the cached access token has expired
    yt_client.app.state.runtime.youtube.access_expires = 0.0  # type: ignore[attr-defined]
    fake_youtube.token_error = "invalid_grant"
    assert yt_client.post("/api/youtube/sync").status_code == 401
    st = yt_client.get("/api/youtube").json()
    assert st["status"] == "error" and "invalid_grant" in st["last_error"]
