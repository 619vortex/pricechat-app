"""Production observability (app/observability.py): request log + X-Request-ID, the 5xx-rate alert, slow / stalled job
alerts, the owner's deep /api/health and the startup self-check."""

import asyncio
import json
import logging
from datetime import timedelta
from typing import Any

import httpx
from fastapi.testclient import TestClient

from app import alerts, observability
from app.config.settings import Settings
from app.db.base import utcnow
from app.db.session import SessionLocal
from app.main import create_app
from app.models.alerts import OwnerAlert
from app.models.routing import ScheduledJob
from app.models.vpn import VpnServer
from app.observability import ErrorWindow, Watchdog
from app.scheduler.scheduler import JobSpec, Scheduler

SETTINGS = Settings(telegram_bot_token="123:abc", telegram_api_base="https://api.telegram.test")


class Bot:
    def __init__(self) -> None:
        self.sent: list[str] = []

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.sent.append(json.loads(request.content)["text"])
        return httpx.Response(200, json={"ok": True, "result": {"message_id": len(self.sent)}})

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)


def _owner() -> None:
    with SessionLocal() as db:
        alerts.settings_row(db).telegram_chat = "424242"
        db.commit()


def _is_open(key: str) -> bool:
    with SessionLocal() as db:
        row = db.get(OwnerAlert, key)
        return bool(row and row.open)


# --- 1. request log + X-Request-ID -------------------------------------------------------------------------------------


def _capture(caplog: Any, name: str = "pricechat.http") -> None:
    """create_app() re-configures logging (force=True drops caplog's root handler): listen on the logger itself."""
    caplog.set_level(logging.DEBUG, logger=name)
    log = logging.getLogger(name)
    for h in [h for h in log.handlers if type(h).__name__ == "LogCaptureHandler"]:  # an earlier test's
        log.removeHandler(h)
    log.addHandler(caplog.handler)


def _app_with_test_routes(fake_provider: Any, fake_embedder: Any) -> Any:
    app = create_app(provider=fake_provider, embedder=fake_embedder, background_jobs=False)

    async def item(item_id: int) -> dict[str, int]:
        return {"id": item_id}

    async def boom(item_id: int) -> dict[str, int]:
        raise RuntimeError("kaboom secret-ish detail")

    async def slow() -> dict[str, bool]:
        await asyncio.sleep(0.05)
        return {"ok": True}

    app.add_api_route("/api/obs-test/items/{item_id}", item)
    app.add_api_route("/api/obs-test/boom/{item_id}", boom)
    app.add_api_route("/api/obs-test/slow", slow)
    return app


def test_request_id_header_and_compact_log_line(fake_provider: Any, fake_embedder: Any, caplog: Any, monkeypatch: Any) -> None:
    # "fast" must not depend on the machine: GitHub's runner took over SLOW_REQUEST_MS (1 s) on the first request after start-up
    # (CI 2026-11-22) and logged it at INFO. The slow path keeps its own test (test_5xx_and_slow_requests_log_at_info).
    monkeypatch.setattr(observability, "SLOW_REQUEST_MS", 60_000.0)
    app = _app_with_test_routes(fake_provider, fake_embedder)
    _capture(caplog)
    with TestClient(app) as c:
        r = c.get("/api/obs-test/items/987654?token=hunter2", headers={"X-Request-ID": "abc-12345", "Cookie": "pc_admin=zzz",
                                                                     "Authorization": "Bearer sekret"})
        assert r.status_code == 200 and r.headers["x-request-id"] == "abc-12345"  # a sane incoming id is kept
        bad = c.get("/api/obs-test/items/1", headers={"X-Request-ID": "x y<script>"})
        assert bad.headers["x-request-id"] != "x y<script>" and len(bad.headers["x-request-id"]) == 16
        assert c.get("/api/obs-test/items/2").headers["x-request-id"]  # generated when absent
    lines = [rec for rec in caplog.records if rec.name == "pricechat.http"]
    first = lines[0].getMessage()
    assert first.startswith("GET /api/obs-test/items/{item_id} 200 ") and "rid=abc-12345" in first
    assert lines[0].levelno == logging.DEBUG  # fast 2xx: debug only
    text = "\n".join(rec.getMessage() for rec in lines)
    for secret in ("987654", "hunter2", "token", "zzz", "sekret", "Bearer", "testclient"):
        assert secret not in text


def test_5xx_and_slow_requests_log_at_info(fake_provider: Any, fake_embedder: Any, caplog: Any, monkeypatch: Any) -> None:
    app = _app_with_test_routes(fake_provider, fake_embedder)
    monkeypatch.setattr(observability, "SLOW_REQUEST_MS", 10.0)
    _capture(caplog)
    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.get("/api/obs-test/boom/5", headers={"X-Request-ID": "req-500-abc"})
        assert r.status_code == 500 and r.json()["request_id"] == "req-500-abc" and r.headers["x-request-id"] == "req-500-abc"
        assert c.get("/api/obs-test/slow").status_code == 200
    info = [rec.getMessage() for rec in caplog.records if rec.name == "pricechat.http" and rec.levelno == logging.INFO]
    assert any(m.startswith("GET /api/obs-test/boom/{item_id} 500 ") and "error=RuntimeError" in m and "rid=req-500-abc" in m for m in info)
    assert any(m.startswith("GET /api/obs-test/slow 200 ") and "SLOW" in m for m in info)
    assert not any("kaboom" in m for m in info)  # the exception text stays in the traceback log only
    snap = app.state.http_errors.snapshot()
    assert snap["errors"] == 1 and snap["routes"] == [("/api/obs-test/boom/{item_id}", 1)] and snap["types"] == [("RuntimeError", 1)]


def test_unmatched_paths_are_not_logged_raw(client: TestClient, caplog: Any) -> None:
    _capture(caplog)
    client.get("/api/no-such-thing/user@example.com")
    msgs = [rec.getMessage() for rec in caplog.records if rec.name == "pricechat.http"]
    assert msgs and msgs[-1].startswith("GET unmatched 404 ") and "example.com" not in msgs[-1]


# --- 2. error-rate alert -----------------------------------------------------------------------------------------------


def test_error_window_thresholds() -> None:
    w = ErrorWindow()
    for _ in range(19):
        w.record("/api/a", 500, "RuntimeError", now=1000.0)
    assert not ErrorWindow.tripped(w.snapshot(1000.0))
    w.record("/api/b", 502, now=1000.0)
    snap = w.snapshot(1000.0)
    assert ErrorWindow.tripped(snap) and snap["routes"][0] == ("/api/a", 19) and ("HTTP 502", 1) in snap["types"]
    assert w.snapshot(1000.0 + 301)["errors"] == 0  # 5-minute window

    ratio = ErrorWindow()  # 10% of >= 50 requests
    for i in range(60):
        ratio.record("/api/x", 500 if i < 6 else 200, now=50.0)
    assert ErrorWindow.tripped(ratio.snapshot(50.0))
    few = ErrorWindow()  # 3 of 10 is a lot, but too few requests to judge
    for i in range(10):
        few.record("/api/x", 500 if i < 3 else 200, now=50.0)
    assert not ErrorWindow.tripped(few.snapshot(50.0))
    w2 = ErrorWindow()
    w2.record("/api/health/public", 503, now=1.0)  # the uptime check's own 503 isn't counted
    assert w2.snapshot(1.0)["requests"] == 0


async def test_error_rate_alerts_once_then_recovers() -> None:
    bot = Bot()
    _owner()
    w = ErrorWindow()
    dog = Watchdog(SessionLocal, w, settings=SETTINGS, transport=bot.transport)
    for _ in range(25):
        w.record("/api/pokemon/cards/{card_id}", 500, "OperationalError", now=100.0)
    for _ in range(3):  # three passes while it lasts: ONE message
        await dog.check(mono=110.0)
    assert len(bot.sent) == 1
    assert "25 of 25 requests failed" in bot.sent[0] and "/api/pokemon/cards/{card_id}: 25" in bot.sent[0]
    assert "OperationalError x25" in bot.sent[0] and _is_open(observability.KEY_HTTP)
    await dog.check(mono=100.0 + 400)  # window empty again
    assert len(bot.sent) == 2 and bot.sent[1].startswith("Price Chat: recovered") and not _is_open(observability.KEY_HTTP)
    await dog.check(mono=100.0 + 500)
    assert len(bot.sent) == 2


async def test_unsent_alert_is_not_retried_every_pass(caplog: Any) -> None:
    w = ErrorWindow()  # no owner chat: logged only, and at most once per RETRY_AFTER
    dog = Watchdog(SessionLocal, w, settings=SETTINGS, transport=Bot().transport)
    for _ in range(30):
        w.record("/api/a", 500, now=10.0)
    caplog.set_level(logging.WARNING, logger="app.alerts")
    for _ in range(4):
        await dog.check(mono=20.0)
    assert sum("ops:http_errors" in r.getMessage() for r in caplog.records) == 1


# --- 3. slow and stalled jobs ------------------------------------------------------------------------------------------


async def _ok(ctx):  # type: ignore[no-untyped-def]
    return {}


def _sched(name: str = "pokemon.sync", interval: int = 60) -> Scheduler:
    s = Scheduler(SessionLocal, [JobSpec(name, "Test job", "testing", interval, _ok, initial_delay_seconds=0)])
    s.sync_jobs()
    return s


async def test_scheduler_records_durations_per_run() -> None:
    s = _sched()
    for _ in range(2):
        assert (await s.run_job("pokemon.sync")).status == "success"
    assert len(s.durations["pokemon.sync"]) == 2
    with SessionLocal() as db:
        assert db.get(ScheduledJob, "pokemon.sync").last_duration_ms is not None  # type: ignore[union-attr]


def test_slow_streak_needs_three_slow_runs_over_3x_median() -> None:
    s = _sched()
    for _ in range(6):
        s.note_duration("pokemon.sync", 3000.0)
    s.note_duration("pokemon.sync", 9500.0)
    s.note_duration("pokemon.sync", 9800.0)
    assert s.slow_jobs() == {}  # two in a row: not yet
    s.note_duration("pokemon.sync", 12000.0)
    assert s.slow_jobs()["pokemon.sync"]["streak"] == 3 and s.slow_jobs()["pokemon.sync"]["median_ms"] == 3000.0
    s.note_duration("pokemon.sync", 3100.0)  # a normal run ends the streak
    assert s.slow_jobs() == {}
    fast = _sched("pokemon.fast")
    for _ in range(6):
        fast.note_duration("pokemon.fast", 20.0)
    for _ in range(4):
        fast.note_duration("pokemon.fast", 900.0)  # 45x, but under a second or two: not news
    assert fast.slow_jobs() == {}


async def test_slow_job_alert_and_recovery() -> None:
    bot = Bot()
    _owner()
    s = _sched()
    dog = Watchdog(SessionLocal, ErrorWindow(), settings=SETTINGS, transport=bot.transport, scheduler=s)
    for ms in [2500.0] * 6 + [30000.0] * 3:
        s.note_duration("pokemon.sync", ms)
    await dog.check()
    await dog.check()
    assert len(bot.sent) == 1 and "running slow" in bot.sent[0] and "Test job (pokemon.sync): 30.0s, usually 2.5s" in bot.sent[0]
    s.note_duration("pokemon.sync", 2600.0)
    await dog.check()
    assert len(bot.sent) == 2 and "usual speed again" in bot.sent[1] and not _is_open(observability.KEY_SLOW)


async def test_stalled_jobs_and_dead_scheduler_alert_then_recover() -> None:
    bot = Bot()
    _owner()
    s = _sched(interval=600)
    loop = asyncio.get_running_loop()
    s._loop_task = loop.create_future()  # type: ignore[assignment]  # "running"
    now = utcnow()
    s.started_at = now - timedelta(hours=3)
    with SessionLocal() as db:
        row = db.get(ScheduledJob, "pokemon.sync")
        row.last_run_at = row.last_finished_at = now - timedelta(minutes=25)  # type: ignore[union-attr]  # < 3 x 10 min
        db.commit()
    dog = Watchdog(SessionLocal, ErrorWindow(), settings=SETTINGS, transport=bot.transport, scheduler=s)
    await dog.check(now=now)
    assert bot.sent == []
    with SessionLocal() as db:
        assert observability.stalled_jobs(db, s, now + timedelta(minutes=10)) and not observability.stalled_jobs(db, s, now)
    await dog.check(now=now + timedelta(minutes=10))  # 35 min > 3 x 10 min
    assert len(bot.sent) == 1 and "not running" in bot.sent[0] and "Test job (pokemon.sync): last ran 35 min ago" in bot.sent[0]
    with SessionLocal() as db:  # a disabled job is never "stalled"
        db.get(ScheduledJob, "pokemon.sync").enabled = False  # type: ignore[union-attr]
        db.commit()
    await dog.check(now=now + timedelta(minutes=11))
    assert len(bot.sent) == 2 and "on schedule again" in bot.sent[1]
    s._loop_task.set_result(None)  # the loop died
    await dog.check(now=now + timedelta(minutes=12))
    assert len(bot.sent) == 3 and "scheduler loop has stopped" in bot.sent[2]


def test_fresh_process_gets_a_grace_period() -> None:
    s = _sched(interval=60)
    s._loop_task = type("T", (), {"done": lambda self: False})()  # type: ignore[assignment]
    now = utcnow()
    s.started_at = now - timedelta(minutes=5)  # never ran (fresh database), but the process only just started
    with SessionLocal() as db:
        assert observability.stalled_jobs(db, s, now) == []
        assert observability.stalled_jobs(db, s, now + timedelta(minutes=30))


# --- 4. deep health ----------------------------------------------------------------------------------------------------


def test_deep_health_checks(client: TestClient) -> None:
    body = client.get("/api/health").json()
    checks = body["checks"]
    assert body["status"] == "ok" and checks["database"]["status"] == "ok" and "ms" in checks["database"]
    assert checks["migrations"]["status"] == "ok" and checks["migrations"]["code"] == checks["migrations"]["database"]
    assert checks["scheduler"]["status"] == "off"  # tests run without the loop
    for name in ("job_backlog", "telegram_ingest", "vortex_assist", "vpn_servers", "http_errors", "watchdog"):
        assert checks[name]["status"] in ("ok", "warn", "fail", "off"), name
    assert checks["vpn_servers"]["status"] == "off"
    with SessionLocal() as db:
        db.add(VpnServer(name="nl-1", agent_url="https://1.2.3.4:8443", agent_token_enc="x", cert_sha256="0" * 64, dns=[], online=False))
        db.add(VpnServer(name="de-1", agent_url="https://1.2.3.5:8443", agent_token_enc="x", cert_sha256="0" * 64, dns=[], online=True))
        db.commit()
    body = client.get("/api/health").json()
    assert body["checks"]["vpn_servers"] == {"status": "warn", "detail": "1 of 2 online", "online": 1, "total": 2}
    assert body["checks_status"] == "warn"


def test_deep_health_scheduler_and_migration_drift(client: TestClient, monkeypatch: Any) -> None:
    from app.db import migrate

    runtime = client.app.state.runtime  # type: ignore[attr-defined]
    loop_task = type("T", (), {"done": lambda self: False})()
    runtime.scheduler._loop_task = loop_task
    runtime.scheduler.last_tick_at = utcnow() - timedelta(seconds=3)
    checks = client.get("/api/health").json()["checks"]
    assert checks["scheduler"]["status"] == "ok" and checks["scheduler"]["last_tick_age_s"] < 60
    runtime.scheduler.last_tick_at = utcnow() - timedelta(minutes=10)
    assert client.get("/api/health").json()["checks"]["scheduler"]["status"] == "fail"
    runtime.scheduler._loop_task = None
    monkeypatch.setattr(migrate, "_HEADS", ("9999",))
    body = client.get("/api/health").json()
    assert body["checks"]["migrations"]["status"] == "fail" and body["status"] == "degraded"


def test_public_health_stays_minimal(client: TestClient) -> None:
    body = client.get("/api/health/public").json()
    assert set(body) == {"ok", "db", "scheduler", "jobs", "backup", "version", "time"}  # words only (jobs/backup: AR-19)
    assert body["jobs"] in ("ok", "failing", "off") and body["backup"] in ("ok", "old", "off") and isinstance(body["version"], str)


# --- 5. startup self-check ---------------------------------------------------------------------------------------------


def test_startup_self_check(caplog: Any, monkeypatch: Any) -> None:
    names = ["pokemon.sync", "vpn.sync", "tradingbot.affiliate", "system.alerts", "system.backup"]
    caplog.set_level(logging.INFO, logger="pricechat.ops")
    with SessionLocal() as db:
        assert observability.startup_self_check(Settings(scheduler_only_jobs=""), names, db) == [] or observability.DPAPI is False
        db.add(VpnServer(name="nl-1", agent_url="https://1.2.3.4:8443", agent_token_enc="x", cert_sha256="0" * 64, dns=[]))
        db.commit()
        caplog.clear()  # without DPAPI (Linux) the first check above already warned about APP_ENCRYPTION_KEY
        monkeypatch.setattr(observability, "DPAPI", False)
        problems = observability.startup_self_check(Settings(scheduler_only_jobs="pokemon.,system.", app_encryption_key=None), names, db)
    text = "; ".join(problems)
    assert "APP_ENCRYPTION_KEY" in text and "vpn.sync" in text and "'vpn.'" in text and "tradingbot.affiliate" in text
    assert "system.alerts" not in text  # covered by "system."
    line = [r for r in caplog.records if r.name == "pricechat.ops" and r.levelno == logging.WARNING]
    assert len(line) == 1 and line[0].getMessage().startswith("Startup self-check: 3 problem(s)")
    with SessionLocal() as db:  # all fine: one INFO line, nothing raised
        ok = observability.startup_self_check(Settings(scheduler_only_jobs="", app_encryption_key="k" * 44), names, db)
    assert ok == []
