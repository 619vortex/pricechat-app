"""Public website: homepage payload, automatic featured item, editorial placement, settings.

Every YouTube request goes to the in-memory FakeYouTube: no real quota is used.
"""

from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.connectors.state import get_connector_state
from app.db.base import utcnow
from app.models.community import CommunityTopic
from app.models.enums import Platform, TopicStatus
from tests.fakes import YT_KEY, FakeYouTube


def _connect_and_sync(c: TestClient) -> None:
    assert c.put("/api/youtube/api-key", json={"api_key": YT_KEY, "channel": "@pricechat"}).status_code == 200
    assert c.post("/api/youtube/sync").status_code == 200


def _home(c: TestClient) -> dict:
    r = c.get("/api/website/home")
    assert r.status_code == 200, r.text
    return r.json()


def _topic(db: Session, name: str, review: str = "approved", days_ago: int = 0, count: int = 5) -> None:
    seen = utcnow() - timedelta(days=days_ago)
    db.add(CommunityTopic(name=name, status=TopicStatus.ACTIVE, review_status=review, first_seen_at=seen,
                          last_seen_at=seen, count_24h=count))
    db.commit()


def test_newest_upload_automatically_becomes_featured(yt_client: TestClient, fake_youtube: FakeYouTube) -> None:
    fake_youtube.upload("old", "Older Short #crypto", published="2026-10-01T09:00:00Z")
    fake_youtube.upload("mid", "Middle Short", published="2026-10-02T09:00:00Z")
    _connect_and_sync(yt_client)
    assert [i["external_id"] for i in _home(yt_client)["latest"]] == ["mid", "old"]

    fake_youtube.upload("new", "Brand new upload 🤯 #story #PriceChat", seconds=44, views=1326, published="2026-10-04T09:00:00Z")
    yt_client.post("/api/youtube/sync")
    home = _home(yt_client)
    first = home["latest"][0]
    assert first["external_id"] == "new"  # the featured slot is simply the newest eligible item
    assert first["title"] == "Brand new upload 🤯 #story #PriceChat"  # stored title is sent unchanged
    assert first["type_label"] == "Short" and first["duration"] == "0:44" and first["views"] == 1326
    assert yt_client.get("/api/website/status").json()["featured"]["external_id"] == "new"


def test_empty_sections_come_back_empty_never_invented(yt_client: TestClient) -> None:
    home = _home(yt_client)
    assert home["latest"] == [] and home["trending"] == [] and home["whats_it_worth"] == []
    assert home["originals"] == [] and home["price_board"] == [] and home["categories"] == []
    assert home["links"]["telegram"] is None and home["site_url"] is None
    assert all(home["sections"].values())  # switched on, but nothing to show


def test_trending_only_uses_recent_human_approved_topics(yt_client: TestClient, db: Session) -> None:
    _topic(db, "Bitcoin", count=30)
    _topic(db, "Gold", count=10)
    _topic(db, "Unreviewed rumour", review="pending", count=99)
    _topic(db, "Rejected", review="rejected", count=99)
    _topic(db, "Last month", days_ago=30)
    assert _home(yt_client)["trending"] == ["Bitcoin", "Gold"]


def test_editorial_series_and_category(yt_client: TestClient, fake_youtube: FakeYouTube) -> None:
    fake_youtube.upload("w", "Is this card worth it?")
    fake_youtube.upload("o", "An original story", published="2026-10-02T09:00:00Z")
    _connect_and_sync(yt_client)
    ids = {r["external_id"]: r["id"] for r in yt_client.get("/api/content-items").json()}
    r = yt_client.patch(f"/api/content-items/{ids['w']}", json={"series": "whats_it_worth", "category": "culture"})
    assert r.status_code == 200 and r.json()["series"] == "whats_it_worth" and r.json()["category"] == "culture"
    yt_client.patch(f"/api/content-items/{ids['o']}", json={"series": "originals"})
    home = _home(yt_client)
    assert [i["external_id"] for i in home["whats_it_worth"]] == ["w"]
    assert [i["external_id"] for i in home["originals"]] == ["o"]
    assert home["categories"] == ["culture"]

    yt_client.post("/api/youtube/sync")  # a sync never removes editorial placement
    assert [i["external_id"] for i in _home(yt_client)["whats_it_worth"]] == ["w"]

    yt_client.patch(f"/api/content-items/{ids['w']}", json={"website_visible": False})  # hidden items leave every section
    home = _home(yt_client)
    assert home["whats_it_worth"] == [] and home["categories"] == []
    assert yt_client.patch(f"/api/content-items/{ids['o']}", json={"series": "made_up"}).status_code == 422


def test_social_links_come_from_settings_or_connected_accounts(yt_client: TestClient, db: Session) -> None:
    yt = get_connector_state(db, Platform.YOUTUBE)
    yt.account_username = "@pricechat"
    x = get_connector_state(db, Platform.X)
    x.account_username = "pricechat"
    db.commit()
    links = _home(yt_client)["links"]
    assert links == {"telegram": None, "x": "https://x.com/pricechat", "youtube": "https://www.youtube.com/@pricechat"}

    r = yt_client.put("/api/website/settings", json={"links": {"telegram": "https://t.me/pricechat"}})
    assert r.status_code == 200 and r.json()["links"]["telegram"] == "https://t.me/pricechat"
    yt_client.put("/api/website/settings", json={"links": {"telegram": ""}})
    assert _home(yt_client)["links"]["telegram"] is None
    assert yt_client.put("/api/website/settings", json={"links": {"telegram": "javascript:alert(1)"}}).status_code == 422
    assert yt_client.put("/api/website/settings", json={"links": {"myspace": "https://a.b"}}).status_code == 422


def test_section_switches_and_public_address(yt_client: TestClient) -> None:
    r = yt_client.put("/api/website/settings", json={"sections": {"story": False}, "site_url": "https://pricechat.example/"})
    assert r.status_code == 200
    home = _home(yt_client)
    assert home["sections"]["story"] is False and home["sections"]["latest"] is True
    assert home["site_url"] == "https://pricechat.example"
    assert yt_client.put("/api/website/settings", json={"sections": {"nope": True}}).status_code == 422
    assert yt_client.put("/api/website/settings", json={"site_url": "not a url"}).status_code == 422
    yt_client.put("/api/website/settings", json={"site_url": ""})
    assert _home(yt_client)["site_url"] is None


def test_home_is_read_only_and_never_calls_youtube(yt_client: TestClient, fake_youtube: FakeYouTube) -> None:
    fake_youtube.upload("v1", "Cached")
    _connect_and_sync(yt_client)
    fake_youtube.calls.clear()
    for _ in range(3):
        _home(yt_client)
    assert fake_youtube.calls == []


def test_long_durations_use_hours(yt_client: TestClient, fake_youtube: FakeYouTube) -> None:
    fake_youtube.upload("long", "A long one", seconds=3725, size=(1920, 1080))
    _connect_and_sync(yt_client)
    assert _home(yt_client)["latest"][0]["duration"] == "1:02:05"
