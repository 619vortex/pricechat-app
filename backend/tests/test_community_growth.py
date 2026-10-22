"""Live community growth on the website's join rows: real counts only, history from the first snapshot, changes only
when a snapshot old enough exists, and only for the account the website links to."""

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics import snapshots
from app.analytics.community import growth, summarize
from app.connectors.state import get_connector_state
from app.db.base import utcnow
from app.models.enums import Platform
from app.models.social_intel import GrowthSnapshot
from app.models.telegram import TelegramSource
from tests.fakes import YT_CHANNEL, YT_KEY, FakeYouTube

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
LINKS = {"youtube": "https://www.youtube.com/@pricechat", "x": "https://x.com/pricechat", "telegram": "https://t.me/thepricechat"}


def test_summary_never_invents_history() -> None:
    assert summarize([]) is None
    one = summarize([(NOW - timedelta(hours=2), 1051)], NOW)
    assert one is not None
    assert one["count"] == 1051 and one["history"] == [{"day": "2026-10-20", "value": 1051}]
    assert one["change_24h"] is None and one["change_7d"] is None and one["change_30d"] is None  # nothing old enough
    assert one["tracking_since"] == "2026-10-20"


def test_summary_changes_and_daily_history() -> None:
    pts = [(NOW - timedelta(days=40), 900),  # older than the 30-day window: counts for 30d change, not drawn
           (NOW - timedelta(days=8), 1000), (NOW - timedelta(days=2, hours=3), 1030), (NOW - timedelta(days=2), 1035),
           (NOW - timedelta(hours=30), 1040), (NOW - timedelta(hours=1), 1051)]
    s = summarize(pts, NOW)
    assert s is not None
    assert s["count"] == 1051
    assert s["change_24h"] == 11 and s["change_7d"] == 51 and s["change_30d"] == 151
    # one value per UTC day (the day's last), only the last 30 days
    assert [h["day"] for h in s["history"]] == ["2026-10-12", "2026-10-18", "2026-10-19", "2026-10-20"]
    assert s["history"][1]["value"] == 1035
    assert s["change_window"] == 51 and s["change_window_pct"] == 5.1 and s["window_start"] == "2026-10-12"


def test_growth_only_for_the_linked_accounts(db: Session) -> None:
    yt = get_connector_state(db, Platform.YOUTUBE)
    yt.account_external_id, yt.account_username = "UCpc", "@pricechat"
    x = get_connector_state(db, Platform.X)
    x.account_external_id, x.account_username = "42", "someoneelse"  # not the account the website links to
    db.add(TelegramSource(chat_id=-100123, display_name="Price Chat", chat_type="supergroup", username="thepricechat", enabled=True))
    db.commit()
    t = utcnow() - timedelta(days=2)
    snapshots.record(db, "youtube_channel", "UCpc", source="test", at=t, followers=30)
    snapshots.record(db, "youtube_channel", "UCpc", source="test", followers=36)
    snapshots.record(db, "x_account", "42", source="test", followers=999)
    snapshots.record(db, "telegram_chat", "-100123", source="test", members=1051)
    snapshots.record(db, "telegram_chat", "-100123", source="test", at=t, messages=10)  # members unknown: ignored, not zero
    db.commit()
    g = growth(db, LINKS)
    assert g["youtube"] is not None and g["youtube"]["count"] == 36 and g["youtube"]["unit"] == "subscribers"
    assert [h["value"] for h in g["youtube"]["history"]] == [30, 36]
    assert g["x"] is None  # @someoneelse's 999 followers are never shown on the @pricechat link
    assert g["telegram"] is not None and g["telegram"]["count"] == 1051 and len(g["telegram"]["history"]) == 1
    assert growth(db, {"youtube": None, "x": None, "telegram": None}) == {"youtube": None, "x": None, "telegram": None}


def test_youtube_subscribers_are_recorded_from_channel_reads(yt_client: TestClient, fake_youtube: FakeYouTube, db: Session) -> None:
    assert yt_client.put("/api/youtube/api-key", json={"api_key": YT_KEY, "channel": "@pricechat"}).status_code == 200
    snaps = list(db.scalars(select(GrowthSnapshot).where(GrowthSnapshot.kind == "youtube_channel")))
    assert [(s.subject, s.followers) for s in snaps] == [(YT_CHANNEL, 1500)]
    yt_client.post("/api/youtube/sync")  # same count within the hour: not stored again
    db.expire_all()
    assert len(list(db.scalars(select(GrowthSnapshot).where(GrowthSnapshot.kind == "youtube_channel")))) == 1
    fake_youtube.channels[YT_CHANNEL]["subscribers"] = 1512
    yt_client.post("/api/youtube/sync")  # still the cached channel reading (refreshed hourly): nothing new
    db.expire_all()
    assert len(list(db.scalars(select(GrowthSnapshot).where(GrowthSnapshot.kind == "youtube_channel")))) == 1
    state = get_connector_state(db, Platform.YOUTUBE)  # an hour later: the sync reads the channel again
    state.capabilities = {**state.capabilities, "channel": {**state.capabilities["channel"],
                                                            "checked_at": (utcnow() - timedelta(hours=2)).isoformat()}}
    db.commit()
    yt_client.post("/api/youtube/sync")
    db.expire_all()
    assert [s.followers for s in db.scalars(select(GrowthSnapshot).where(GrowthSnapshot.kind == "youtube_channel")
                                            .order_by(GrowthSnapshot.id))] == [1500, 1512]
    yt_client.put("/api/website/settings", json={"links": {"youtube": "https://www.youtube.com/@pricechat"}})
    home = yt_client.get("/api/website/home").json()
    assert home["growth"]["youtube"]["count"] == 1512


def test_hidden_subscriber_counts_are_never_recorded(db: Session) -> None:
    from app.youtube.service import _channel_summary, _set_caps

    hidden = {"id": "UCpc", "snippet": {"title": "Price Chat", "customUrl": "@pricechat"},
              "statistics": {"hiddenSubscriberCount": True, "videoCount": "12"}, "contentDetails": {}}
    _set_caps(db, channel=_channel_summary(hidden))
    db.commit()
    assert db.scalar(select(GrowthSnapshot).where(GrowthSnapshot.kind == "youtube_channel")) is None
