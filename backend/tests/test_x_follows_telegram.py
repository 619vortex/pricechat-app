"""Owner, 2026-11-09: every Telegram post also goes to X. X waits for Telegram's outcome on each event and posts exactly what Telegram
delivered, whatever X's own daily limit or category switches say. No test reaches a real service (X stays in dry run here)."""

from datetime import timedelta

import pytest
from sqlalchemy.orm import Session

from app.config.settings import get_settings
from app.newsdesk import engine, events, recap, sources, telegram as lane

from tests.test_newsdesk_events import T0, decide, hack, released
from tests.test_newsdesk_telegram import Tg


@pytest.fixture(autouse=True)
def plan(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(events, "DEFAULT_SCHEDULE", "windows")
    monkeypatch.setattr(sources, "EXTRA_FEEDS", [])
    monkeypatch.setattr(lane, "FORMAT", "classic")  # no picture fetching in tests
    monkeypatch.setattr(engine, "X_FORMAT", "classic")
    monkeypatch.setattr(recap, "VISUAL", False)
    assert events.X_FOLLOWS_TELEGRAM is True  # production default


async def test_x_waits_for_telegram_then_posts_what_telegram_posted_even_past_its_own_limits(db: Session) -> None:
    hack(db, "Bybaz", T0, "$300 million", hours=0)
    await decide(db, T0)
    desk = engine.settings_row(db)
    desk.target_per_day, desk.max_breaking_per_day = 0, 0  # X's own limits would drop everything
    desk.categories = {**(desk.categories or {}), "crypto": False}  # and so would its category switch
    db.commit()
    ev = released(db)[0]

    assert await engine.deliver_x(db, desk, get_settings(), None, 0, T0) is None  # Telegram hasn't posted yet: X waits
    db.refresh(ev)
    assert ev.x_status == "pending"

    tg = Tg()
    await lane.tick(db, get_settings(), tg, T0, quotes={})
    db.refresh(ev)
    assert ev.tg_status == "delivered" and len(tg.sent) == 1

    assert await engine.deliver_x(db, desk, get_settings(), None, 0, T0 + timedelta(minutes=3)) == "dry_run"
    db.refresh(ev)
    assert ev.x_status == "dry_run"


async def test_x_skips_what_telegram_did_not_post(db: Session) -> None:
    hack(db, "Qorvex", T0, "$300 million", hours=0)
    await decide(db, T0)
    ev = released(db)[0]
    events.mark(ev, "telegram", "skipped", "failed the checks: test")
    db.commit()
    desk = engine.settings_row(db)
    # owner, 2026-11-18: each platform on its own. Telegram refusing its caption no longer silences X: X writes its own checked text
    assert await engine.deliver_x(db, desk, get_settings(), None, 0, T0) == "dry_run"
    db.refresh(ev)
    assert ev.x_status == "dry_run"
    assert await engine.deliver_x(db, desk, get_settings(), None, 0, T0) is None  # once


async def test_x_still_follows_a_telegram_post_made_late_in_its_window(db: Session) -> None:
    hack(db, "Zentra", T0, "$300 million", hours=0)
    await decide(db, T0)
    ev = released(db)[0]
    events.mark(ev, "telegram", "delivered", "", message_id=999)
    db.commit()
    late = T0 + events.POLICY.deliver_news + timedelta(minutes=20)  # past the shared window, inside X's extra time after Telegram
    desk = engine.settings_row(db)
    assert await engine.deliver_x(db, desk, get_settings(), None, 0, late) == "dry_run"
