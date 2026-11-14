"""Monitoring follow-up (2026-11-14): every alert path run repeatedly in a failing state sends exactly one message, then one
"recovered"; and the real owner-alert send path against a local fake Telegram Bot API (127.0.0.1, no real Telegram)."""
import json
import threading
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterator

import botocore.exceptions
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import alerts, observability
from app.config.settings import Settings
from app.db.base import utcnow
from app.db.session import SessionLocal
from app.models.alerts import OwnerAlert
from app.models.routing import ScheduledJob
from app.services import db_backup
from tests.test_backup_monitoring import Bucket, _corrupt_db_bytes, _db_bytes
from tests.test_vpn import FakeNode, fake  # noqa: F401  (fixture)


def _key_at(hours_ago: float) -> str:
    return f"backups/db/price_chat-{(utcnow() - timedelta(hours=hours_ago)).strftime('%Y%m%d-%H%M%S')}.db.gz"


@pytest.fixture
def outbox(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every owner alert that would leave the server (the real notify_owner bookkeeping, only the last hop captured)."""
    sent: list[str] = []

    async def capture(settings: Any, chat: str, text: str, transport: Any = None) -> None:
        sent.append(text)

    monkeypatch.setattr(alerts, "send_text", capture)
    return sent


@pytest.fixture
def jobs(client: TestClient, monkeypatch: pytest.MonkeyPatch, outbox: list[str]) -> Iterator[Any]:
    """Run a real job through the real scheduler (POST /api/jobs/<name>/run), with a bucket configured and a fake client."""
    assert client.put("/api/system/alerts", json={"telegram_chat": "424242", "enabled": True}).status_code == 200
    monkeypatch.setattr(client.app.state.runtime.settings, "portfolio_bucket", "bucket")  # type: ignore[attr-defined]
    holder: dict[str, Any] = {}
    monkeypatch.setattr(db_backup, "_client", lambda settings: holder["s3"])

    def run(name: str, s3: Any) -> dict[str, Any]:
        holder["s3"] = s3
        return client.post(f"/api/jobs/{name}/run").json()

    yield run


# --- 2. deduplication: repeated failing runs, one message, one recovery ------------------------------------------------------


def test_hourly_backup_check_while_stale_sends_once_then_recovers_once(jobs: Any, outbox: list[str]) -> None:
    stale = Bucket({_key_at(40): b"x"})
    for _ in range(6):
        assert jobs("system.backup_check", stale)["status"] == "success"
    assert len(outbox) == 1 and "database backups are STALE" in outbox[0] and "40 h old" in outbox[0]
    fresh = Bucket({_key_at(40): b"x", _key_at(1): b"x"})
    for _ in range(4):
        jobs("system.backup_check", fresh)
    assert len(outbox) == 2 and "Database backups are fresh again" in outbox[1]


class _Raises:
    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    def list_objects_v2(self, **kw: Any) -> Any:
        raise self.exc


@pytest.mark.parametrize("exc,shown", [
    (botocore.exceptions.ClientError({"Error": {"Code": "InvalidAccessKeyId", "Message": "The key you provided does not exist"}}, "ListObjectsV2"),
     "can't be read: ClientError: An error occurred (InvalidAccessKeyId)"),
    (botocore.exceptions.ConnectTimeoutError(endpoint_url="https://bucket.example.invalid"), "can't be read: ConnectTimeoutError: Connect timeout"),
])
def test_unreadable_bucket_auth_error_or_timeout_alerts_once(jobs: Any, outbox: list[str], exc: Exception, shown: str) -> None:
    for _ in range(5):
        out = jobs("system.backup_check", _Raises(exc))
        assert out["status"] == "success" and out["result"]["backup_stale"] in (0, 1)  # the check itself worked: it reports
    assert len(outbox) == 1 and shown in outbox[0]
    jobs("system.backup_check", Bucket({_key_at(2): b"x"}))
    assert len(outbox) == 2 and "fresh again" in outbox[1]


def test_no_backups_at_all_alerts_once(jobs: Any, outbox: list[str]) -> None:
    for _ in range(3):
        jobs("system.backup_check", Bucket())
    assert len(outbox) == 1 and "There is no backup in the bucket" in outbox[0]


def test_stale_backup_reminds_once_a_day_not_every_6_hours(db: Session, outbox: list[str]) -> None:
    import asyncio

    alerts.settings_row(db).telegram_chat = "424242"
    db.commit()
    t0 = utcnow()
    info = {"key": "backups/db/price_chat-x.db.gz", "age_hours": 40.0, "stale": True, "empty_newer": []}
    for h in range(0, 48):
        asyncio.run(alerts.check_backup_freshness(db, info, now=t0 + timedelta(hours=h)))
    assert len(outbox) == 2  # hour 0 and hour 24


def test_backup_check_stays_quiet_while_the_backup_jobs_own_alert_is_open(jobs: Any, outbox: list[str]) -> None:
    with SessionLocal() as db:
        db.add(OwnerAlert(key="job:system.backup", open=True, last_sent_at=utcnow(), sent_count=1))
        db.commit()
    for _ in range(3):
        jobs("system.backup_check", Bucket({_key_at(40): b"x"}))
    assert outbox == []  # the owner already got "Nightly database backup FAILED" for this incident


async def test_watchdog_backup_monitor_stays_quiet_while_backup_check_reported_it(outbox: list[str]) -> None:
    from app.observability import ErrorWindow, Watchdog
    from tests.test_audit_ops import _sched
    from tests.test_observability import SETTINGS, _owner

    _owner()
    s = _sched("system.backup")
    with SessionLocal() as db:
        row = db.get(ScheduledJob, "system.backup")
        row.run_count, row.last_status, row.last_error = 3, "failed", "boom"  # type: ignore[union-attr]
        db.add(OwnerAlert(key=alerts.BACKUP_KEY, open=True, last_sent_at=utcnow(), sent_count=1))
        db.commit()
    dog = Watchdog(SessionLocal, ErrorWindow(), settings=SETTINGS, scheduler=s)
    for i in range(3):
        await dog.check(now=utcnow() + timedelta(minutes=i))
    assert not [t for t in outbox if "no successful database backup" in t]
    with SessionLocal() as db:
        db.get(OwnerAlert, alerts.BACKUP_KEY).open = False  # type: ignore[union-attr]
        db.commit()
    await dog.check(now=utcnow() + timedelta(minutes=5))
    assert len([t for t in outbox if "no successful database backup" in t]) == 1  # without backup_check's alert, it speaks


def test_weekly_restore_test_failing_sends_once_then_working_again_once(jobs: Any, outbox: list[str], tmp_path: Path) -> None:
    bad = Bucket({_key_at(3): _corrupt_db_bytes(tmp_path)})
    for _ in range(5):
        assert jobs("system.restore_test", bad)["status"] == "failed"
    assert len(outbox) == 1 and "Weekly backup restore test FAILED" in outbox[0] and "integrity_check" in outbox[0]
    good = Bucket({_key_at(2): _db_bytes(tmp_path)})
    for _ in range(3):
        out = jobs("system.restore_test", good)
        assert out["status"] == "success" and "Restore test passed" in out["result"]["summary"]
    assert len(outbox) == 2 and "working again" in outbox[1]


def test_restore_test_failures_remind_daily_and_retry_daily(client: TestClient) -> None:
    from app.jobs import build_jobs

    assert alerts.REMIND_JOB["system.restore_test"] == timedelta(hours=24)
    spec = next(s for s in build_jobs(client.app.state.runtime) if s.name == "system.restore_test")  # type: ignore[attr-defined]
    assert spec.retry_failed_seconds == 86400  # a failed test is retried a day later, so a reminder at most once a day


async def test_fleet_alerts_repeated_sync_failures_one_message_each_then_one_recovery(db: Session, outbox: list[str], fake: FakeNode) -> None:  # noqa: F811
    from tests.test_vpn import add_server, open_vpn

    alerts.settings_row(db).telegram_chat = "424242"
    db.commit()
    a = add_server(db, fake, "A")
    add_server(db, fake, "B")
    open_vpn(db)
    run = lambda: alerts.check_vpn_servers(db, settings=Settings(telegram_bot_token="1:x"))  # noqa: E731
    await run()
    assert outbox == []
    a.fail_streak, a.online, a.last_error = 2, False, "timeout"
    db.commit()
    for _ in range(6):
        a.fail_streak += 1
        db.commit()
        await run()
    assert len(outbox) == 2
    assert any("server offline" in t and "Server: A" in t for t in outbox) and any("1 healthy public server" in t for t in outbox)
    a.fail_streak, a.online, a.last_error = 0, True, None
    db.commit()
    for _ in range(4):
        await run()
    assert len(outbox) == 4
    assert any("Kiwi VPN server A is online again" in t for t in outbox[2:]) and any("2 healthy public servers again" in t for t in outbox[2:])


def test_owner_alerts_self_check_at_every_restart_sends_nothing(db: Session, outbox: list[str]) -> None:
    from tests.test_production import prod_settings

    s = prod_settings(telegram_bot_token="123:abc")
    for _ in range(5):
        assert any("OWNER ALERTS GO NOWHERE" in p for p in observability.startup_self_check(s, [], db))
    assert outbox == []  # one log line per start, never a Telegram message (there is nowhere to send it)


# --- 3a. the real send path against a local fake Telegram Bot API --------------------------------------------------------------


class FakeBotApi:
    """A tiny Bot API on 127.0.0.1: records each sendMessage; `reply` sets the next answers (status, body)."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.replies: list[tuple[int, dict[str, Any]]] = []
        api = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 - http.server's name
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                api.requests.append({"path": self.path, "content_type": self.headers.get("Content-Type"), "json": json.loads(body or b"{}")})
                status, reply = api.replies.pop(0) if api.replies else (200, {"ok": True, "result": {"message_id": len(api.requests)}})
                data = json.dumps(reply).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a: Any) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def botapi() -> Iterator[FakeBotApi]:
    api = FakeBotApi()
    yield api
    api.close()


async def test_owner_alert_reaches_a_local_bot_api_with_the_right_shape(db: Session, botapi: FakeBotApi) -> None:
    settings = Settings(telegram_bot_token="123:fake-token-for-tests", telegram_api_base=botapi.base)
    alerts.settings_row(db).telegram_chat = "424242"
    db.commit()
    assert await alerts.notify_owner(db, "test:shape", "Price Chat alert: hello\nline two", settings=settings)
    [r] = botapi.requests
    assert r["path"] == "/bot123:fake-token-for-tests/sendMessage" and r["content_type"].startswith("application/json")
    assert r["json"] == {"chat_id": 424242, "text": "Price Chat alert: hello\nline two", "disable_web_page_preview": True}  # plain text: no parse_mode
    assert db.get(OwnerAlert, "test:shape").sent_count == 1  # type: ignore[union-attr]


@pytest.mark.parametrize("status,reply,error", [
    (429, {"ok": False, "error_code": 429, "description": "Too Many Requests: retry after 7", "parameters": {"retry_after": 7}}, "TelegramRateLimitError"),
    (500, {"ok": False, "error_code": 500, "description": "Internal Server Error"}, "TelegramUnavailableError"),
])
async def test_telegram_429_or_500_is_logged_not_marked_sent_and_retried(db: Session, botapi: FakeBotApi, caplog: Any,
                                                                          status: int, reply: dict[str, Any], error: str) -> None:
    import logging

    caplog.set_level(logging.WARNING, logger="app.alerts")
    settings = Settings(telegram_bot_token="123:fake-token-for-tests", telegram_api_base=botapi.base)
    alerts.settings_row(db).telegram_chat = "424242"
    db.commit()
    botapi.replies.append((status, reply))
    assert await alerts.notify_owner(db, "test:fail", "Price Chat alert: x", settings=settings, open_=True) is False  # never raises
    assert db.get(OwnerAlert, "test:fail") is None  # not counted as sent: no cooldown starts, not marked open
    line = " ".join(r.getMessage() for r in caplog.records)
    assert error in line and "fake-token-for-tests" not in line  # the token is never logged
    assert await alerts.notify_owner(db, "test:fail", "Price Chat alert: x", settings=settings, open_=True) is True  # next try goes out
    assert len(botapi.requests) == 2 and db.get(OwnerAlert, "test:fail").open  # type: ignore[union-attr]


async def test_test_alert_endpoint_uses_the_same_path(client: TestClient, botapi: FakeBotApi, monkeypatch: pytest.MonkeyPatch) -> None:
    rt = client.app.state.runtime  # type: ignore[attr-defined]
    monkeypatch.setattr(rt.settings, "telegram_bot_token", Settings(telegram_bot_token="123:fake-token-for-tests").telegram_bot_token)
    monkeypatch.setattr(rt.settings, "telegram_api_base", botapi.base)
    monkeypatch.setattr(rt.telegram, "transport", None, raising=False)
    client.put("/api/system/alerts", json={"telegram_chat": "424242", "enabled": True})
    r = client.post("/api/system/alerts/test")
    assert r.status_code == 200 and r.json() == {"sent": True}
    assert botapi.requests[-1]["json"]["text"] == "Price Chat: test alert. Owner alerts reach you here."
    botapi.replies.append((500, {"ok": False, "error_code": 500, "description": "Internal Server Error"}))
    r = client.post("/api/system/alerts/test")
    assert r.status_code == 400 and "Telegram didn't deliver it" in r.text and "fake-token" not in r.text
