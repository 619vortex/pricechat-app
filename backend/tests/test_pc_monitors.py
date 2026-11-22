"""The owner's PC (2026-11-22): the World Monitor bridge pushed "signals: 0" for hours (an orphaned sidecar answered every feed with
401) and nobody was told, because the pushes stayed fresh. Now: World Monitor reports stale (Dubai daytime) / every feed failing or zero
signals for 30 min, and the News Desk GPU worker offline for 2 h in Dubai daytime, each alert the owner once and say "recovered" once."""

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi.testclient import TestClient

from app import observability
from app.db.session import SessionLocal
from app.models.brand_manager import BmWorker
from app.models.exchange import ExSetting
from app.models.newsdesk import NdSettings
from app.newsdesk import wm_app
from app.observability import ErrorWindow, Watchdog
from tests.test_audit_ops import _monitor, _sched
from tests.test_brand_manager import _worker
from tests.test_newsdesk_wm_app import SNAP, W
from tests.test_observability import SETTINGS, Bot, _is_open, _owner

# 12:00 Dubai (08:00 UTC): daytime. 02:00 Dubai (22:00 UTC the day before): night.
NOON = datetime(2026, 11, 22, 8, 0, tzinfo=timezone.utc)
NIGHT = datetime(2026, 11, 21, 22, 0, tzinfo=timezone.utc)
GOOD = {"feeds": {"earthquakes": {"ok": True, "count": 2}}, "signals": [{"id": "a", "title": "M5 quake"}], "feeds_ok": 7, "feeds_failed": 0}
ALL_401 = {"feeds": {"earthquakes": {"ok": False, "count": 0, "note": "HTTP 401"}, "risk-scores": {"ok": False, "count": 0, "note": "HTTP 401"}},
           "signals": [], "feeds_ok": 0, "feeds_failed": 10}


def _push(payload: dict[str, Any], at: datetime) -> None:
    with SessionLocal() as db:
        wm_app.save(db, "pc", payload, at)


def _gpu_worker(last_seen: datetime | None) -> None:
    with SessionLocal() as db:
        db.add(BmWorker(name="pc", token_hash="h" * 64, enabled=True, last_seen_at=last_seen, info={"newsdesk": {"model": "qwen"}}))
        db.commit()


def test_monitors_are_off_until_the_pc_has_reported() -> None:
    s = _sched()
    for key in ("wm_reports", "wm_feeds", "gpu_worker"):
        assert _monitor(s, key, NOON)["status"] == "off"


def test_new_fields_are_accepted_and_old_bridges_still_work(client: TestClient) -> None:
    h = _worker(client)
    assert client.post(W, json=SNAP, headers=h).status_code == 200  # a 1.0.0 bridge: no feeds_ok / feeds_failed
    with SessionLocal() as db:
        v = db.get(ExSetting, wm_app.KEY).value  # type: ignore[union-attr]
    assert v["feeds_ok"] == 1 and v["feeds_failed"] == 1 and v["bad"] is False and v["bad_since"] is None  # counted from `feeds`
    r = client.post(W, json={**SNAP, "bridge_version": "1.1.0", "feeds_ok": 6, "feeds_failed": 1}, headers=h)
    assert r.status_code == 200
    with SessionLocal() as db:
        v = db.get(ExSetting, wm_app.KEY).value  # type: ignore[union-attr]
    assert v["feeds_ok"] == 6 and v["feeds_failed"] == 1
    assert client.post(W, json={**SNAP, "feeds_ok": -1}, headers=h).status_code == 422


async def test_every_feed_failing_for_30_min_alerts_once_then_recovers() -> None:
    bot = Bot()
    _owner()
    s = _sched()
    dog = Watchdog(SessionLocal, ErrorWindow(), settings=SETTINGS, transport=bot.transport, scheduler=s)
    _push(GOOD, NOON)
    for i in range(1, 8):  # the 11-22 incident: fresh pushes every 5 min, every feed 401, zero signals
        _push(ALL_401, NOON + timedelta(minutes=5 * i))
    assert _monitor(s, "wm_feeds", NOON + timedelta(minutes=20))["status"] == "hold"  # broken for 15 min: not yet
    await dog.check(now=NOON + timedelta(minutes=36))
    await dog.check(now=NOON + timedelta(minutes=37))
    feeds_alerts = [t for t in bot.sent if "no working feed" in t]
    assert len(feeds_alerts) == 1 and "HTTP 401" in feeds_alerts[0] and "0 feeds answering" in feeds_alerts[0]
    assert _is_open(observability.KEY_SILENCE + "wm_feeds")
    assert not any("sent no report" in t for t in bot.sent)  # the pushes themselves were fresh
    _push(GOOD, NOON + timedelta(minutes=40))
    await dog.check(now=NOON + timedelta(minutes=41))
    assert "recovered" in bot.sent[-1] and "World Monitor bridge: feeds" in bot.sent[-1]
    assert not _is_open(observability.KEY_SILENCE + "wm_feeds")


def test_zero_signals_counts_as_broken_even_if_a_feed_answered() -> None:
    s = _sched()
    _push({**GOOD, "signals": []}, NOON - timedelta(minutes=40))
    _push({**GOOD, "signals": []}, NOON - timedelta(minutes=1))
    assert _monitor(s, "wm_feeds", NOON)["status"] == "alert"


def test_stale_reports_alert_by_day_and_hold_at_night() -> None:
    s = _sched()
    _push(GOOD, NOON - timedelta(minutes=45))
    m = _monitor(s, "wm_reports", NOON)
    assert m["status"] == "alert" and "sent no report" in m["alert"]
    assert _monitor(s, "wm_feeds", NOON)["status"] == "hold"  # nothing fresh to judge: the reports monitor covers it
    assert _monitor(s, "wm_reports", NOON - timedelta(minutes=20))["status"] == "ok"  # 25 min quiet: not yet
    _push(GOOD, NIGHT - timedelta(hours=3))
    assert _monitor(s, "wm_reports", NIGHT)["status"] == "hold"  # the PC is off at night: no page


def test_a_pc_switched_on_in_the_morning_never_pages_the_owner() -> None:
    s = _sched()
    _push(GOOD, NIGHT - timedelta(hours=2))  # last report the evening before
    morning = datetime(2026, 11, 22, 4, 20, tzinfo=timezone.utc)  # 08:20 Dubai: judged from 08:00 only
    assert _monitor(s, "wm_reports", morning)["status"] == "hold"
    assert _monitor(s, "wm_reports", morning + timedelta(minutes=15))["status"] == "alert"  # 08:35, still nothing


def test_gpu_worker_offline_2h_by_day_alerts_and_holds_at_night() -> None:
    s = _sched()
    _gpu_worker(NOON - timedelta(hours=3))
    m = _monitor(s, "gpu_worker", NOON)
    assert m["status"] == "alert" and "GPU worker" in m["alert"] and "offline for over 2 h" in m["alert"]
    assert _monitor(s, "gpu_worker", NOON + timedelta(hours=14))["status"] == "hold"  # 02:00 Dubai
    with SessionLocal() as db:
        db.query(BmWorker).update({"last_seen_at": NIGHT})
        db.commit()
    assert _monitor(s, "gpu_worker", datetime(2026, 11, 22, 5, 0, tzinfo=timezone.utc))["status"] == "hold"  # 09:00 Dubai: 1 h of day
    with SessionLocal() as db:
        db.query(BmWorker).update({"last_seen_at": NOON - timedelta(minutes=1)})
        db.commit()
    assert _monitor(s, "gpu_worker", NOON)["status"] == "ok"


def test_gpu_worker_monitor_is_off_when_pictures_are_switched_off() -> None:
    s = _sched()
    _gpu_worker(NOON - timedelta(hours=5))
    with SessionLocal() as db:
        db.add(NdSettings(id=1, state={"gpu_render": False}))
        db.commit()
    assert _monitor(s, "gpu_worker", NOON)["status"] == "off"


async def test_gpu_worker_alert_is_sent_once_and_recovers() -> None:
    bot = Bot()
    _owner()
    s = _sched()
    _gpu_worker(NOON - timedelta(hours=3))
    dog = Watchdog(SessionLocal, ErrorWindow(), settings=SETTINGS, transport=bot.transport, scheduler=s)
    await dog.check(now=NOON)
    await dog.check(now=NOON + timedelta(minutes=1))
    assert sum("GPU worker" in t for t in bot.sent) == 1
    with SessionLocal() as db:
        db.query(BmWorker).update({"last_seen_at": NOON + timedelta(minutes=2)})
        db.commit()
    await dog.check(now=NOON + timedelta(minutes=3))
    assert "recovered" in bot.sent[-1] and "GPU worker" in bot.sent[-1]
