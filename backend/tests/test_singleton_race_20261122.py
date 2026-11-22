"""Singleton settings rows are created race-safely (app/db/singleton.py).

Production found on a fresh database (2026-11-22): the first admin page load fires several requests at once; each found no
settings row, inserted id 1, and all but one failed with "UNIQUE constraint failed: <table>.id" (HTTP 500 on /api/system/alerts,
/api/terminal, /api/vpn/admin/overview, /api/x/newsdesk, /api/x/newsdesk/network)."""
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import singleton
from app.db.session import SessionLocal
from app.models.alerts import OwnerAlert, OwnerAlertSettings

THREADS = 8


def _helpers() -> list[tuple[str, Callable[[Session], Any], type, Callable[[Any], bool]]]:
    from app import alerts
    from app.kalshi import service as kalshi
    from app.members import referrals, reputation
    from app.models.kalshi import KalshiBot
    from app.models.members import ProgramSettings, ReputationSettings
    from app.models.newsdesk import NdSettings, NdTelegramSettings
    from app.models.trending import TtSettings
    from app.models.vpn import VpnSettings
    from app.models.workers import WkSettings
    from app.newsdesk import engine as nd_engine
    from app.newsdesk import sources as nd_sources
    from app.newsdesk import telegram as nd_telegram
    from app.trending import engine as tt_engine
    from app.vpn import service as vpn
    from app.workers import service as workers

    return [
        ("owner_alert_settings", alerts.settings_row, OwnerAlertSettings, lambda r: r.id == 1),
        ("nd_settings", nd_engine.settings_row, NdSettings,
         lambda r: r.watchlist == nd_sources.DEFAULT_WATCHLIST and r.feeds == nd_sources.DEFAULT_FEEDS and r.state == {}),
        ("nd_telegram_settings", nd_telegram.settings_row, NdTelegramSettings, lambda r: r.state == {}),
        ("tt_settings", tt_engine.settings_row, TtSettings,
         lambda r: r.mode == "automatic" and r.max_per_day == 3 and r.gap_minutes == 180 and r.auto_confirmed_at is not None),
        ("vpn_settings", vpn.settings, VpnSettings,
         lambda r: all(getattr(r, k) == v for k, v in vpn.SETTINGS_DEFAULTS.items())),
        ("wk_settings", workers.settings, WkSettings, lambda r: r.id == 1),
        ("reputation_settings", reputation.settings, ReputationSettings, lambda r: r.id == 1),
        ("program_settings", referrals.program, ProgramSettings,
         lambda r: r.updates_channel == "@Pricechatupdates" and r.leaderboard_periods == ["all", "week"]),
        ("kalshi_bot", kalshi._bot, KalshiBot, lambda r: r.id == 1),
    ]


HELPERS = _helpers()


@pytest.mark.parametrize("name,helper,model,defaults_ok", HELPERS, ids=[h[0] for h in HELPERS])
def test_concurrent_first_access_creates_exactly_one_row(name: str, helper: Callable[[Session], Any], model: type,
                                                         defaults_ok: Callable[[Any], bool]) -> None:
    start = threading.Barrier(THREADS)
    looked = threading.Barrier(THREADS)  # every thread has seen "no row yet" before any of them inserts: the race, every time
    errors: list[str] = []

    def worker() -> None:
        with SessionLocal() as s:
            original_get = s.get
            calls = {"n": 0}

            def get_then_wait(*a: Any, **k: Any) -> Any:
                found = original_get(*a, **k)
                calls["n"] += 1
                if calls["n"] == 1:
                    looked.wait(timeout=10)
                return found

            s.get = get_then_wait  # type: ignore[method-assign]
            try:
                start.wait(timeout=10)
                row = helper(s)
                assert row.id == 1
                s.commit()
            except Exception as e:  # noqa: BLE001 - collected and asserted below
                errors.append(f"{type(e).__name__}: {e}")

    threads = [threading.Thread(target=worker) for _ in range(THREADS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert errors == [], errors
    with SessionLocal() as s:
        assert s.scalar(select(func.count()).select_from(model)) == 1
        assert defaults_ok(s.get(model, 1)), f"{name}: defaults changed"


@pytest.mark.parametrize("name,helper,model,defaults_ok", HELPERS, ids=[h[0] for h in HELPERS])
def test_an_existing_row_is_returned_unchanged(name: str, helper: Callable[[Session], Any], model: type, defaults_ok: Callable[[Any], bool]) -> None:
    with SessionLocal() as s:
        first = helper(s)
        s.commit()
        before = {c.key: getattr(first, c.key) for c in model.__table__.columns}
    with SessionLocal() as s:
        again = helper(s)
        assert {c.key: getattr(again, c.key) for c in model.__table__.columns} == before
        assert s.scalar(select(func.count()).select_from(model)) == 1


class _FirstGetMisses:
    """A session whose first get() misses, as if another request had not committed yet when this one looked: forces the
    lost-race path (the insert hits the UNIQUE constraint) deterministically."""

    def __init__(self, db: Session) -> None:
        self._db, self._calls = db, 0

    def get(self, *a: Any, **k: Any) -> Any:
        self._calls += 1
        return None if self._calls == 1 else self._db.get(*a, **k)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._db, name)


def test_a_lost_race_keeps_the_callers_other_changes() -> None:
    with SessionLocal() as winner:  # the other request: creates and commits the row first
        singleton.get_or_create(winner, OwnerAlertSettings, lambda: OwnerAlertSettings(id=1))
        winner.commit()
    with SessionLocal() as s:
        s.add(OwnerAlert(key="unrelated:pending", last_text="kept"))  # the caller's own work, not yet flushed
        row = singleton.get_or_create(_FirstGetMisses(s), OwnerAlertSettings, lambda: OwnerAlertSettings(id=1))  # type: ignore[arg-type]
        assert row.id == 1
        s.commit()
    with SessionLocal() as s:
        assert s.scalar(select(func.count()).select_from(OwnerAlertSettings)) == 1
        kept = s.get(OwnerAlert, "unrelated:pending")
        assert kept is not None and kept.last_text == "kept"


def test_the_lost_race_path_returns_the_winners_row_values() -> None:
    with SessionLocal() as winner:
        row = singleton.get_or_create(winner, OwnerAlertSettings, lambda: OwnerAlertSettings(id=1))
        winner.commit()
    with SessionLocal() as s:
        got = singleton.get_or_create(_FirstGetMisses(s), OwnerAlertSettings, lambda: OwnerAlertSettings(id=1))  # type: ignore[arg-type]
        assert got.id == row.id == 1


def test_concurrent_first_admin_requests_on_a_fresh_database_all_succeed(client: TestClient) -> None:
    paths = ["/api/vpn/admin/overview", "/api/x/newsdesk", "/api/system/alerts", "/api/x/newsdesk/network"] * 3
    with ThreadPoolExecutor(max_workers=len(paths)) as pool:
        codes = list(pool.map(lambda p: (p, client.get(p).status_code), paths))
    assert all(code == 200 for _, code in codes), codes
