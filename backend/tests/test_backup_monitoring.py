"""Backup monitoring (2026-11-14): the newest backup in the bucket younger than 26 h (system.backup_check), the weekly
restore test (system.restore_test) that only ever opens a downloaded copy, and owner alerts that go nowhere made loud."""
import gzip
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config.settings import Settings
from app.services import db_backup

NOW = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)
BUCKET = Settings(portfolio_bucket="bucket")


class Bucket:
    """Just the bucket calls the checks make: list, download."""

    def __init__(self, objects: dict[str, bytes] | None = None, broken: bool = False) -> None:
        self.objects = objects or {}
        self.broken = broken
        self.downloads: list[str] = []

    def list_objects_v2(self, Bucket: str, Prefix: str) -> dict[str, Any]:  # noqa: N803 - boto3's names
        if self.broken:
            raise RuntimeError("SignatureDoesNotMatch")
        return {"Contents": [{"Key": k, "Size": len(v)} for k, v in self.objects.items() if k.startswith(Prefix)]}

    def download_fileobj(self, Bucket: str, Key: str, f: Any) -> None:  # noqa: N803
        self.downloads.append(Key)
        f.write(self.objects[Key])


def _key(at: datetime) -> str:
    return f"backups/db/price_chat-{at.strftime('%Y%m%d-%H%M%S')}.db.gz"


def _db_bytes(tmp_path: Path, members: int = 3, version: str | None = "0116_vpn_trial_guards", tables: tuple[str, ...] = db_backup.KEY_TABLES) -> bytes:
    path = tmp_path / f"src-{members}-{len(tables)}-{version}.db"
    con = sqlite3.connect(path)
    for t in tables:
        con.execute(f'CREATE TABLE "{t}" (id INTEGER PRIMARY KEY)')
    if "members" in tables:
        con.executemany("INSERT INTO members (id) VALUES (?)", [(i,) for i in range(1, members + 1)])
    if version is not None:
        con.execute("CREATE TABLE alembic_version (version_num VARCHAR(64) NOT NULL)")
        con.execute("INSERT INTO alembic_version VALUES (?)", (version,))
    con.commit()
    con.close()
    return gzip.compress(path.read_bytes())


# --- freshness ------------------------------------------------------------------------------------------------------------


def test_newest_backup_and_its_age() -> None:
    s3 = Bucket({_key(NOW - timedelta(hours=30)): b"x", _key(NOW - timedelta(hours=5)): b"x", "backups/db/notes.txt": b"x"})
    info = db_backup.newest(BUCKET, NOW, s3)
    assert info["key"] == _key(NOW - timedelta(hours=5)) and info["age_hours"] == 5.0 and info["copies"] == 2 and not info["stale"]
    assert db_backup.newest(BUCKET, NOW + timedelta(hours=22), s3)["stale"]  # 27 h
    assert db_backup.newest(BUCKET, NOW, Bucket())["key"] is None and db_backup.newest(BUCKET, NOW, Bucket())["stale"]
    lm = {"Contents": [{"Key": "backups/db/manual.db.gz", "LastModified": NOW - timedelta(hours=1)}]}
    assert db_backup.newest(BUCKET, NOW, type("S", (), {"list_objects_v2": lambda self, **kw: lm})())["age_hours"] == 1.0


async def test_stale_backup_alerts_once_then_recovers(db: Session) -> None:
    from app import alerts
    from tests.test_owner_alerts import SETTINGS, Bot

    tg = Bot()
    alerts.settings_row(db).telegram_chat = "424242"
    db.commit()
    run = lambda info, error=None, now=NOW: alerts.check_backup_freshness(db, info, error, settings=SETTINGS, transport=tg.transport, now=now)  # noqa: E731
    fresh = db_backup.newest(BUCKET, NOW, Bucket({_key(NOW - timedelta(hours=3)): b"x"}))
    assert await run(fresh) == {"backup_stale": 0, "backup_recovered": 0} and tg.sent == []
    old = db_backup.newest(BUCKET, NOW, Bucket({_key(NOW - timedelta(hours=40)): b"x"}))
    assert (await run(old))["backup_stale"] == 1 and "STALE" in tg.sent[-1]["text"] and "40 h old" in tg.sent[-1]["text"]
    assert (await run(old, now=NOW + timedelta(hours=1)))["backup_stale"] == 0  # 6 h cooldown
    assert (await run(fresh))["backup_recovered"] == 1 and "fresh again" in tg.sent[-1]["text"]
    assert (await run(fresh))["backup_recovered"] == 0
    # the 2026-10-22 incident: a wrong bucket secret; the alert names the error, never a secret
    assert (await run(None, "ClientError: SignatureDoesNotMatch", now=NOW + timedelta(days=1)))["backup_stale"] == 1
    assert "can't be read" in tg.sent[-1]["text"] and "SignatureDoesNotMatch" in tg.sent[-1]["text"]
    empty = db_backup.newest(BUCKET, NOW, Bucket())
    assert (await run(empty, now=NOW + timedelta(days=2)))["backup_stale"] == 1 and "no backup" in tg.sent[-1]["text"]


# --- weekly restore test ---------------------------------------------------------------------------------------------------


def test_restore_test_passes_on_a_sound_copy_and_never_touches_the_live_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    key = _key(NOW - timedelta(hours=4))
    s3 = Bucket({_key(NOW - timedelta(days=3)): b"older", key: _db_bytes(tmp_path, members=7)})
    opened: list[str] = []
    real = sqlite3.connect
    monkeypatch.setattr(db_backup.sqlite3, "connect", lambda p, *a, **k: (opened.append(str(p)), real(p, *a, **k))[1])
    out = db_backup.restore_test(BUCKET, NOW, s3, expected_head="0116_vpn_trial_guards")
    assert s3.downloads == [key] and out["integrity"] == "ok" and out["alembic_version"] == "0116_vpn_trial_guards"
    assert out["matches_code_head"] is True and out["counts"]["members"] == 7 and "Restore test passed" in out["summary"]
    assert len(opened) == 1 and opened[0].startswith("file:") and opened[0].endswith("?mode=ro") and "restore-test" in opened[0]
    assert not Path(opened[0][len("file:///"):-len("?mode=ro")]).exists()  # the temporary copy is gone
    assert db_backup.restore_test(BUCKET, NOW, s3, expected_head="0113_other")["matches_code_head"] is False  # reported, not a failure


def _corrupt_db_bytes(tmp_path: Path) -> bytes:
    """A real SQLite file whose index no longer matches its table: opens and reads fine, PRAGMA integrity_check
    reports the mismatch (rows missing from the index)."""
    path = tmp_path / "corrupt.db"
    con = sqlite3.connect(path)
    con.execute("PRAGMA page_size = 1024")
    for t in db_backup.KEY_TABLES:
        con.execute(f'CREATE TABLE "{t}" (id INTEGER PRIMARY KEY, name TEXT)')
    con.execute("CREATE INDEX ix_members_name ON members (name)")
    con.executemany("INSERT INTO members (name) VALUES (?)", [(f"kiwi {i:05d}",) for i in range(3000)])
    con.execute("CREATE TABLE alembic_version (version_num VARCHAR(64) NOT NULL)")
    con.execute("INSERT INTO alembic_version VALUES ('0116_vpn_trial_guards')")
    con.commit()
    # the index's stored definition no longer describes what is in it (what a torn write to the schema page looks like)
    con.execute("PRAGMA writable_schema = ON")
    con.execute("UPDATE sqlite_master SET sql = 'CREATE INDEX ix_members_name ON members (id)' WHERE name = 'ix_members_name'")
    con.commit()
    con.close()
    raw = path.read_bytes()
    return gzip.compress(bytes(raw))


@pytest.mark.parametrize("make,reason", [
    (lambda tmp: b"not gzip at all", "doesn't gunzip"),
    (lambda tmp: _db_bytes(tmp)[:200], "doesn't gunzip"),  # truncated gzip (an upload cut short)
    (lambda tmp: gzip.compress(b"hello, this is not a database " * 50), "not a sound SQLite"),  # gzip of non-SQLite bytes
    (lambda tmp: gzip.compress(b"SQLite format 3\x00" + b"\x00" * 200), "not a sound SQLite"),
    (lambda tmp: _corrupt_db_bytes(tmp), "failed PRAGMA integrity_check"),
    (lambda tmp: gzip.compress(b""), "unpacks to 0 bytes"),
    (lambda tmp: _db_bytes(tmp, version=None), "no alembic_version"),
    (lambda tmp: _db_bytes(tmp, tables=("members", "vpn_servers")), "missing table(s): vpn_accounts"),
    (lambda tmp: _db_bytes(tmp, members=0), "0 members"),
])
def test_restore_test_fails_loudly(tmp_path: Path, make: Any, reason: str) -> None:
    s3 = Bucket({_key(NOW - timedelta(hours=4)): make(tmp_path)})
    with pytest.raises(db_backup.RestoreTestFailed, match=reason if "|" in reason else reason.replace("(", r"\(").replace(")", r"\)")):
        db_backup.restore_test(BUCKET, NOW, s3)
    with pytest.raises(db_backup.RestoreTestFailed, match="No backup"):
        db_backup.restore_test(BUCKET, NOW, Bucket())


async def test_a_failed_restore_test_alerts_at_the_first_failure(db: Session) -> None:
    from app import alerts
    from tests.test_owner_alerts import SETTINGS, Bot

    tg = Bot()
    alerts.settings_row(db).telegram_chat = "424242"
    db.commit()
    sent = await alerts.job_outcome(db, "system.restore_test", "Weekly backup restore test", "failed", "RestoreTestFailed: 0 members", 0, 1,
                                    settings=SETTINGS, transport=tg.transport)
    assert sent and "Weekly backup restore test FAILED" in tg.sent[-1]["text"]
    assert await alerts.job_outcome(db, "system.restore_test", "Weekly backup restore test", "success", None, 1, 0,
                                    settings=SETTINGS, transport=tg.transport)
    assert "working again" in tg.sent[-1]["text"]


def test_backup_jobs_are_registered_under_the_production_prefix(client: TestClient) -> None:
    from app.jobs import build_jobs

    specs = {s.name: s for s in build_jobs(client.app.state.runtime)}
    assert specs["system.backup_check"].interval_seconds == 3600
    assert specs["system.restore_test"].interval_seconds == 7 * 86400
    prod = Settings(scheduler_only_jobs="pokemon.,telegram.ingest,members.verify,growth.snapshots,invites.,vault.,brand.,youtube.sync,tradingos.,x.own,system.,vpn.")
    off = prod.jobs_off(specs)
    assert "system.backup_check" not in off and "system.restore_test" not in off


def test_backup_jobs_skip_without_a_bucket(client: TestClient) -> None:
    r = client.post("/api/jobs/system.restore_test/run")
    if r.status_code == 404:
        pytest.skip("no manual run route")
    assert r.json()["status"] == "skipped"
    assert client.post("/api/jobs/system.backup_check/run").json()["status"] == "skipped"


# --- owner alerts that go nowhere ------------------------------------------------------------------------------------------


def test_admin_says_alerts_go_nowhere_until_a_chat_is_set(client: TestClient) -> None:
    r = client.get("/api/system/alerts").json()
    assert r["delivers"] is False and r["problem"].startswith("Owner alerts are not being delivered")
    r = client.put("/api/system/alerts", json={"telegram_chat": "424242"}).json()
    assert ("bot token" in (r["problem"] or "")) or r["delivers"] is True  # tests may run without a bot token
    checks = client.get("/api/health").json()["checks"]
    assert checks["owner_alerts"]["status"] in ("warn", "ok")


def test_the_startup_self_check_says_alerts_go_nowhere(db: Session, caplog: Any) -> None:
    from app import observability
    from tests.test_production import prod_settings

    caplog.set_level(logging.INFO, logger="pricechat.ops")
    s = prod_settings(telegram_bot_token="123:abc")
    text = "; ".join(observability.startup_self_check(s, [], db))
    assert "OWNER ALERTS ARE NOT BEING DELIVERED" in text and "no owner Telegram chat" in text and "kept" in text
    from app import alerts

    alerts.settings_row(db).telegram_chat = "424242"
    db.commit()
    assert "NOT BEING DELIVERED" not in "; ".join(observability.startup_self_check(s, [], db))
    alerts.settings_row(db).enabled = False
    db.commit()
    assert "turned off" in "; ".join(observability.startup_self_check(s, [], db))


def test_the_test_alert_endpoint_says_why_it_cannot_send(client: TestClient) -> None:
    r = client.post("/api/system/alerts/test")
    assert r.status_code == 400 and "Telegram user id" in r.text



def test_a_zero_byte_object_is_not_a_backup(tmp_path: Path) -> None:
    """An upload that wrote nothing: freshness says so (not "fresh"), and the restore test fails on it."""
    good, empty = _key(NOW - timedelta(hours=30)), _key(NOW - timedelta(hours=2))
    s3 = Bucket({good: _db_bytes(tmp_path), empty: b""})
    info = db_backup.newest(BUCKET, NOW, s3)
    assert info["key"] == good and info["empty_newer"] == [empty] and info["stale"]
    with pytest.raises(db_backup.RestoreTestFailed, match=r"empty \(0 bytes\)"):
        db_backup.restore_test(BUCKET, NOW, s3)
    only_empty = db_backup.newest(BUCKET, NOW, Bucket({empty: b""}))
    assert only_empty["key"] is None and only_empty["empty_newer"] == [empty]


async def test_a_zero_byte_object_alerts(db: Session) -> None:
    from app import alerts
    from tests.test_owner_alerts import SETTINGS, Bot

    tg = Bot()
    alerts.settings_row(db).telegram_chat = "424242"
    db.commit()
    info = db_backup.newest(BUCKET, NOW, Bucket({_key(NOW - timedelta(hours=1)): b""}))
    assert (await alerts.check_backup_freshness(db, info, settings=SETTINGS, transport=tg.transport, now=NOW))["backup_stale"] == 1
    assert "is empty (0 bytes)" in tg.sent[-1]["text"]
