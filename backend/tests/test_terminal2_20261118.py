"""Terminal 2.0 (owner, 2026-11-18): one page, one automation switch, News Desk events folded into the one feed, and Telegram from the story
panel. The switch pauses automatic publishing and puts back exactly what was there; it never changes live / dry run or the emergency stops.
The Telegram send goes through the channel lane's own sender: never a rumour, once per story, not with a stop on. Nothing real is contacted."""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.settings import get_settings
from app.db.base import utcnow
from app.models.newsdesk import NdEvent, NdSettings, NdTelegramPost
from app.models.trending import TtTopic
from app.newsdesk import engine as desk
from app.newsdesk import telegram as lane
from app.services.agent_service import get_agent_config
from app.terminal import feed
from app.trending import engine
from tests.test_newsdesk_telegram import CHANNEL_ID, Tg
from tests.trending_fixtures import Web, story

NOW = utcnow().replace(hour=10, minute=10, second=0, microsecond=0)
KYIV = "Russia launches missile strikes on Kyiv, killing 25"


@pytest.fixture(autouse=True)
def quiet_alerts(monkeypatch: pytest.MonkeyPatch) -> None:
    from app import alerts

    async def notify(db: Any, key: str, text: str, **_: Any) -> bool:
        return True

    monkeypatch.setattr(alerts, "notify_owner", notify)


async def verified_topic(db: Session) -> TtTopic:
    web = Web(NOW)
    story(db, KYIV, "Reuters", 1, NOW, 12, category="politics")
    story(db, "Russian missile strikes on Kyiv kill 25, officials say", "Associated Press", 2, NOW, 9, category="politics")
    story(db, "Russia launches missile strikes on Kyiv, killing at least 25", "BBC", 3, NOW, 6, category="politics")
    await engine.tick(db, get_settings(), web.runtime, get_agent_config(db).id, NOW, transport=web.transport)
    return max(db.scalars(select(TtTopic).where(TtTopic.title.contains("Kyiv"))), key=lambda x: x.score)


def rumour_topic(db: Session) -> TtTopic:
    t = TtTopic(key="rumour-bybaz", title="Bybaz reportedly halts withdrawals, sources say", category="crypto", status="new", score=60, peak_score=60,
                first_seen=NOW, last_seen=NOW, sources=[{"source": "google", "outlet": "Google Trends", "title": "bybaz down", "url": "", "reliable": False}],
                parts={"terminal": {"evidence": {"score": 10, "level": "unverified", "rumor": True, "reasons": []}, "rumor": True, "viral": {"score": 80}}})
    db.add(t)
    db.commit()
    return t


# --- the one automation switch ---------------------------------------------------------------------------------------------------------


def test_pause_and_resume_put_back_exactly_what_was_there(db: Session) -> None:
    s = engine.settings_row(db)
    s.mode, s.auto_confirmed_at = "automatic", NOW
    nd = desk.settings_row(db)
    nd.mode, nd.automation = "live", True
    db.commit()
    out = feed.set_automation(db, False)
    assert out["status"] == "paused" and not out["on"]
    assert s.mode == "approval" and not engine.automatic(s) and nd.automation is False
    assert nd.mode == "live" and not nd.kill_switch and not s.kill_switch  # never live / dry run, never the stops
    assert desk.blocked_reason(nd, NOW) == "Automation is off"
    feed.set_automation(db, False)  # pausing twice doesn't forget what was there
    out = feed.set_automation(db, True)
    assert s.mode == "automatic" and engine.automatic(s) and nd.automation is True and nd.mode == "live"
    assert out["on"] and out["status"] in ("running", "attention")


def test_resume_keeps_an_approval_mode_the_owner_chose(db: Session) -> None:
    s = engine.settings_row(db)
    s.mode, s.auto_confirmed_at = "approval", None
    desk.settings_row(db)
    db.commit()
    feed.set_automation(db, False)
    feed.set_automation(db, True)
    assert s.mode == "approval" and not engine.automatic(s)


def test_an_emergency_stop_shows_as_paused_with_the_reason(db: Session) -> None:
    s = engine.settings_row(db)
    nd = desk.settings_row(db)
    nd.kill_switch = True
    db.commit()
    a = feed.automation_state(db, s, NOW)
    assert a["status"] == "paused" and a["stopped"] and "emergency stop" in a["attention"][0]


def test_automation_route(db: Session, client: TestClient) -> None:
    desk.settings_row(db)
    db.commit()
    r = client.post("/api/terminal/automation", json={"on": False})
    assert r.status_code == 200 and r.json()["status"] == "paused"
    assert client.get("/api/terminal").json()["automation"]["status"] == "paused"
    r = client.post("/api/terminal/automation", json={"on": True})
    assert r.status_code == 200 and r.json()["on"]


# --- one feed --------------------------------------------------------------------------------------------------------------------------


async def test_a_news_desk_event_on_the_same_story_is_shown_once(db: Session) -> None:
    t = await verified_topic(db)
    db.add(NdEvent(key="story:kyiv", kind="news", title="Russian missile strikes on Kyiv kill 25, officials say", headline="Russian strikes on Kyiv kill 25",
                   status="released", first_seen=NOW, released_at=NOW, created_at=NOW))
    db.add(NdEvent(key="story:other", kind="news", title="Fed holds rates steady as expected", headline="Fed holds rates", status="candidate", first_seen=NOW, created_at=NOW))
    db.commit()
    body = feed.overview(db, get_settings())
    kyiv = [r for r in body["stories"] if "Kyiv" in r["title"]]
    assert len(kyiv) == 1  # the scan's twin rows for one story show as one card
    row = kyiv[0]
    assert row["desk"]["status"] == "released"
    assert [d["title"] for d in body["desk_only"]] == ["Fed holds rates steady as expected"]
    assert body["desk_only"][0]["lane"] == "watch"
    assert {"status", "attention", "on"} <= set(body["automation"]) and {"x", "live", "telegram"} <= set(body["today"])


# --- Telegram from the story panel -----------------------------------------------------------------------------------------------------


async def test_owner_sends_a_verified_story_to_telegram_once(db: Session) -> None:
    t = await verified_topic(db)
    tg = Tg()
    out = await feed.publish_telegram(db, get_settings(), t, None, client=tg)
    assert out["ok"] and len(tg.sent) == 1 and tg.sent[0]["chat"] == CHANNEL_ID
    assert "Source</a>" in tg.sent[0]["text"]
    row = db.scalar(select(NdTelegramPost).where(NdTelegramPost.story_link == f"terminal:{t.id}"))
    assert row is not None and row.status == "delivered" and row.writer == "owner"
    assert t.parts["telegram"]["message_id"] == out["message_id"]
    with pytest.raises(ValueError, match="Already sent"):
        await feed.publish_telegram(db, get_settings(), t, None, client=tg)
    assert len(tg.sent) == 1


async def test_telegram_refuses_rumours_stops_and_a_paused_lane(db: Session) -> None:
    tg = Tg()
    with pytest.raises(ValueError, match="unverified"):
        await feed.publish_telegram(db, get_settings(), rumour_topic(db), None, client=tg)
    t = await verified_topic(db)
    nd = db.get(NdSettings, 1) or desk.settings_row(db)
    nd.kill_switch = True
    db.commit()
    with pytest.raises(ValueError, match="emergency stop"):
        await feed.publish_telegram(db, get_settings(), t, None, client=tg)
    nd.kill_switch = False
    lane.settings_row(db).enabled = False
    db.commit()
    with pytest.raises(ValueError, match="turned off"):
        await feed.publish_telegram(db, get_settings(), t, None, client=tg)
    assert tg.sent == []


async def test_telegram_refuses_a_story_the_news_desk_already_posted_there(db: Session) -> None:
    t = await verified_topic(db)
    db.add(NdEvent(key="story:kyiv2", kind="news", title=KYIV, headline="Russian strikes on Kyiv kill 25", status="released", tg_status="delivered",
                   first_seen=NOW, released_at=NOW, created_at=utcnow()))
    db.commit()
    tg = Tg()
    with pytest.raises(ValueError, match="already posted"):
        await feed.publish_telegram(db, get_settings(), t, None, client=tg)
    assert tg.sent == []


async def test_telegram_route_reports_refusals_as_409(db: Session, client: TestClient) -> None:
    t = rumour_topic(db)
    r = client.post(f"/api/terminal/stories/{t.id}/telegram", json={"text": None})
    assert r.status_code == 409 and "unverified" in r.json()["detail"]
