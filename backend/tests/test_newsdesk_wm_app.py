"""The World Monitor desktop app on the owner's PC, shown in Admin › World Monitor (app/newsdesk/wm_app.py): only the PC worker's token
can push, the snapshot is cleaned and bounded, it shows as offline once the PC stops reporting, and it never touches the desk's state."""
from datetime import timedelta
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.newsdesk import engine
from tests.test_brand_manager import _worker

W = "/api/newsdesk-worker/world-monitor"
A = "/api/x/newsdesk/editorial/monitor/app"
SNAP = {
    "app_version": "2.3.8", "bridge_version": "1.0.0", "collected_at": "2026-11-12T05:20:00Z",
    "feeds": {"earthquakes": {"ok": True, "count": 2, "ms": 410}, "acled-conflict": {"ok": False, "count": 0, "note": "needs an ACLED key"}},
    "signals": [
        {"id": "us1", "layer": "earthquake", "severity": "elevated", "title": "M5.4 - 155 km E of Noda, Japan", "at": "2026-11-12T04:00:00Z",
         "url": "https://earthquake.usgs.gov/earthquakes/eventpage/us1", "lat": 35.9, "lon": 141.6},
        {"id": "us2", "layer": "earthquake", "severity": "critical", "title": "M7.1 - Kermadec Islands (tsunami flag)", "at": "2026-11-12T03:00:00Z",
         "url": "javascript:alert(1)", "lat": 999, "lon": 10},
        {"id": "x", "layer": "made-up", "severity": "apocalyptic", "title": "Unknown layer and severity"},
        {"id": "empty", "title": "   "},
    ],
    "summary": {"macro": {"verdict": "CASH", "fear_greed": 28}},
}


def test_only_the_pc_worker_can_push(client: TestClient) -> None:
    assert client.post(W, json=SNAP).status_code == 401
    assert client.post(W, json=SNAP, headers={"Authorization": "Bearer pcw_nope"}).status_code == 401


def test_admin_sees_the_app_cleaned_and_most_severe_first(client: TestClient, db: Session) -> None:
    before = client.get(A).json()
    assert before["connected"] is False and before["seen"] is False and before["signals"] == []
    desk = engine.settings_row(db)
    desk.state = {"last_prices": {"BTC": 1}}
    db.commit()
    r = client.post(W, json=SNAP, headers=_worker(client))
    assert r.status_code == 200 and r.json() == {"ok": True, "signals": 3, "feeds": 2}
    got = client.get(A).json()
    assert got["connected"] is True and got["app_version"] == "2.3.8" and got["feeds"]["acled-conflict"]["note"] == "needs an ACLED key"
    assert [s["id"] for s in got["signals"]] == ["us2", "us1", "x"]  # critical, elevated, then low
    bad = got["signals"][0]
    assert bad["url"] == "" and bad["lat"] is None and bad["lon"] == 10  # no javascript: links, no impossible coordinates
    assert got["signals"][2]["layer"] == "other" and got["signals"][2]["severity"] == "low"
    assert got["summary"]["macro"]["verdict"] == "CASH"
    db.expire_all()
    assert engine.settings_row(db).state == {"last_prices": {"BTC": 1}}  # the desk's own state is never touched


def test_it_shows_offline_when_the_pc_stops_reporting(client: TestClient) -> None:
    assert client.post(W, json=SNAP, headers=_worker(client)).status_code == 200
    with patch("app.api.routes.newsdesk_editorial.utcnow", return_value=utcnow() + timedelta(minutes=20)):
        got = client.get(A).json()
    assert got["seen"] is True and got["connected"] is False and "15 minutes" in got["note"]


def test_a_huge_snapshot_is_refused(client: TestClient) -> None:
    big = {**SNAP, "signals": [{"id": str(i), "title": "x" * 200, "detail": "y" * 300, "url": "https://e.com/" + "z" * 480} for i in range(400)]}
    r = client.post(W, json=big, headers=_worker(client))
    assert r.status_code in (200, 413)
    if r.status_code == 200:
        assert r.json()["signals"] == 200  # capped
