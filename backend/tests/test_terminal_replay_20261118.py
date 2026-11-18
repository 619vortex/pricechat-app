"""REPLAY (simulated, owner 2026-11-18): five real events from 2026-11-16/17 through the complete News Desk + Trending pipeline, with the
breaking lane in the state production was in (a post 30 minutes earlier, two breaking posts already that day). Feeds, Telegram and X are
faked; the code path is the real one: collect -> decide -> Telegram lane -> X lane -> Trending scan. Headlines are the real ones (News Desk
logs and the ground-truth list); the outlets are World Monitor-catalog feeds standing in for the wires that carried them.

Each case prints a "replay:" line with the simulated first report -> release -> Telegram -> X minutes."""

from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.settings import get_settings
from app.models.newsdesk import NdEvent, NdTelegramPost
from app.models.trending import TtPost
from app.newsdesk import engine, events, telegram as lane
from app.services.agent_service import get_agent_config
from tests.test_newsdesk_breaking_e2e import BETWEEN, DW, NATIONAL, live  # noqa: F401  (the fixture)
from tests.trending_fixtures import Web

REPLAYS = [
    ("qeshm", "US jets strike IRGC missile launch site on Iran's Qeshm Island", "US jets strike IRGC missile site on Iran's Qeshm Island in 'self-defence'",
     "US fighter jets struck an IRGC missile launch site on Qeshm Island, US officials said."),
    ("tankers", "Iranian mines hit five ships in Strait of Hormuz", "Mines hit five ships in the Strait of Hormuz, shipping officials say",
     "Five ships were hit by mines in the Strait of Hormuz, maritime officials said."),
    ("correction", "Wall Street sells off as Nasdaq enters correction", "Nasdaq enters correction as Wall Street sells off",
     "The Nasdaq fell 2.5% on Monday, entering a correction, as chip stocks slid."),
    ("nvidia", "Nvidia revenue jumps 103% to $115.8 billion, beats estimates", "Nvidia beats estimates as revenue jumps 103% to $115.8 billion",
     "Nvidia reported revenue of $115.8 billion, up 103%, beating estimates."),
    ("apple", "Apple outage hits iCloud, App Store and Apple Pay for users", "Apple Pay, iCloud and App Store hit by widespread Apple outage",
     "Apple confirmed an outage affecting iCloud, the App Store and Apple Pay for users."),
]


def _closed_lane(db: Session) -> None:
    """Production 2026-11-17 21:30: a post 30 minutes earlier and two breaking posts in the last 24 hours."""
    for i, (title, mins) in enumerate((("Fed's Warsh says inflation fight is not over", 30), ("Gold price climbs to a record high above $4,500", 300),
                                       ("Bank of Japan raises interest rates to 1%", 600))):
        db.add(NdEvent(key=f"story:past{i}", kind="news", title=title, headline=title, status="released", slot="breaking",
                       released_at=BETWEEN - timedelta(minutes=mins), first_seen=BETWEEN - timedelta(minutes=mins + 5), x_status="published", tg_status="delivered"))
    db.commit()


def _feed(live: dict[str, Any], a: str, b: str, says: str, first: int = 14) -> None:
    live["feeds"]["items"] = {
        DW["url"]: [{"title": a, "link": f"{DW['url']}#{abs(hash(a))}", "at": BETWEEN - timedelta(minutes=first), "summary": says}],
        NATIONAL["url"]: [{"title": b, "link": f"{NATIONAL['url']}#{abs(hash(b))}", "at": BETWEEN - timedelta(minutes=first - 6), "summary": says}],
    }


async def _desk(db: Session, at: Any) -> None:
    desk = engine.settings_row(db)
    await engine.collect(db, desk, None, at)
    await events.decide(db, at, quotes={}, watchlist=[], categories=desk.categories or {}, desk_state=desk.state, liquidations=False)


@pytest.mark.parametrize("case,a,b,says", REPLAYS, ids=[r[0] for r in REPLAYS])
async def test_replay_major_event_through_the_whole_pipeline(case: str, a: str, b: str, says: str, client: Any, db: Session, live: dict[str, Any]) -> None:
    _closed_lane(db)
    _feed(live, a, b, says)
    first_report = BETWEEN - timedelta(minutes=14)
    await _desk(db, BETWEEN)
    ev = db.scalars(select(NdEvent).where(NdEvent.status == "released", NdEvent.key.notlike("story:past%"))).first()
    assert ev is not None, [(e.title, e.status, e.why) for e in db.scalars(select(NdEvent).where(NdEvent.key.notlike("story:past%")))]
    assert ev.slot == "breaking" and "major breaking" in ev.why, ev.why
    tg_at = BETWEEN + timedelta(seconds=20)
    sent = await lane.tick(db, get_settings(), live["tg"], tg_at)
    assert sent.get("sent"), sent
    x_at = BETWEEN + timedelta(seconds=50)
    result = await engine.deliver_x(db, engine.settings_row(db), get_settings(), None, 0, x_at)
    assert result == "published", result
    db.expire_all()
    ev = db.get(NdEvent, ev.id)
    assert ev.tg_status == "delivered" and ev.x_status == "published"
    print(f"replay: {case} | first report -> release {int((BETWEEN - first_report).total_seconds() // 60)} min | -> Telegram "
          f"{int((tg_at - first_report).total_seconds() // 60)} min | -> X {int((x_at - first_report).total_seconds() // 60)} min (SIMULATED)")
    # no duplicate: the same event again, the desk again, both lanes again, and Trending's scan, post nothing more
    live["feeds"]["items"][DW["url"]].append({"title": a + " - update", "link": f"{DW['url']}#again", "at": BETWEEN, "summary": says})
    await _desk(db, BETWEEN + timedelta(minutes=2))
    await lane.tick(db, get_settings(), live["tg"], BETWEEN + timedelta(minutes=3))
    await engine.deliver_x(db, engine.settings_row(db), get_settings(), None, 0, BETWEEN + timedelta(minutes=3))
    web = Web(BETWEEN)
    from app.trending import engine as tt

    await tt.tick(db, get_settings(), web.runtime, get_agent_config(db).id, BETWEEN + timedelta(minutes=4), transport=web.transport)
    assert len(live["tg"].sent) == 1 and len(live["x"].posts) == 1
    assert not list(db.scalars(select(TtPost).where(TtPost.status.in_(("published", "dry_run", "uncertain")))))
    assert len(list(db.scalars(select(NdTelegramPost.id).where(NdTelegramPost.status == "sent")))) <= 1


async def test_x_posts_on_its_own_when_telegram_refuses_its_caption(client: Any, db: Session, live: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """Production #649/#658/#662/#667: Telegram's final check refused the caption and X skipped too. Now X posts its own checked text, once."""
    _feed(live, *REPLAYS[0][1:])
    await _desk(db, BETWEEN)
    ev = db.scalars(select(NdEvent).where(NdEvent.status == "released")).one()

    async def refuse(*a: Any, **k: Any) -> tuple[None, str]:
        return None, "a check on the picture caption"

    monkeypatch.setattr(lane, "compose", refuse)
    await lane.tick(db, get_settings(), live["tg"], BETWEEN + timedelta(seconds=20))
    db.expire_all()
    assert db.get(NdEvent, ev.id).tg_status == "skipped" and live["tg"].sent == []
    result = await engine.deliver_x(db, engine.settings_row(db), get_settings(), None, 0, BETWEEN + timedelta(seconds=50))
    assert result == "published" and len(live["x"].posts) == 1
    assert "qeshm" in live["x"].posts[0]["text"].lower()
    await engine.deliver_x(db, engine.settings_row(db), get_settings(), None, 0, BETWEEN + timedelta(minutes=2))
    assert len(live["x"].posts) == 1  # once


async def test_a_hedged_or_single_source_report_still_never_breaks_through(client: Any, db: Session, live: dict[str, Any]) -> None:
    _closed_lane(db)
    _feed(live, "US jets reportedly strike Qeshm Island, sources say", "US jets reportedly strike Qeshm Island, unconfirmed reports say", "Unconfirmed reports said...")
    await _desk(db, BETWEEN)
    assert db.scalar(select(NdEvent.id).where(NdEvent.status == "released", NdEvent.key.notlike("story:past%"))) is None
    live["feeds"]["items"] = {DW["url"]: [{"title": REPLAYS[0][1], "link": f"{DW['url']}#solo", "at": BETWEEN - timedelta(minutes=5), "summary": REPLAYS[0][3]}]}
    await _desk(db, BETWEEN + timedelta(minutes=1))
    assert db.scalar(select(NdEvent.id).where(NdEvent.status == "released", NdEvent.key.notlike("story:past%"))) is None
