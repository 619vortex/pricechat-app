"""Nightly off-volume backup of the SQLite database (the `system.backup` job).

Everything (members, XP, wallets, trading accounts, the market) lives in one SQLite file on one Railway volume, so a lost
or corrupted volume would lose it all. Once a day this takes a consistent copy with SQLite's online backup API (the app
keeps running), checks the copy is sound (PRAGMA quick_check), and streams it gzip-compressed to the private bucket
(the one Brand Manager and the portfolio photos use, which is separate storage from the volume), under backups/db/.
The newest KEEP copies are kept. The copy is built in the container's temporary disk, never on the nearly full volume,
and deleted afterwards. Secrets inside stay encrypted (the key is an environment variable, not in the file).

Restore: download the newest backups/db/*.db.gz from the bucket, gunzip it, and put it in place of the volume's
database file while the app is stopped (or GET /api/system/db-backup for a fresh copy any time).

Checks (2026-11-14, after the backups failed silently from 2026-10-22 to 2026-11-12 on a wrong bucket secret):
- `newest()`: the newest copy in the bucket and its age; the hourly `system.backup_check` job alerts the owner when it is
  older than STALE_AFTER (or the bucket can't be read), and says "recovered" once a fresh copy lands.
- `restore_test()`: the weekly `system.restore_test` job downloads the newest copy into a temporary directory, gunzips it,
  runs PRAGMA integrity_check, reads its alembic version and counts key rows. It only ever opens that downloaded copy,
  read-only, never the live database; the temporary directory is deleted afterwards."""

import gzip
import io
import os
import re
import shutil
import sqlite3
import tempfile
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.config.settings import Settings

PREFIX = "backups/db/"
KEEP = 14
STALE_AFTER = timedelta(hours=26)  # a daily job plus its retry window: older than this, a night was missed
KEY_TABLES = ("members", "vpn_servers", "vpn_accounts", "vpn_devices", "vpn_payments")  # counted by the restore test
_STAMP = re.compile(r"price_chat-(\d{8}-\d{6})\.db\.gz$")


class _Gzip(io.RawIOBase):
    """Reads a file as a gzip stream, compressing on the fly (no second temporary copy)."""

    def __init__(self, f: Any) -> None:
        self.f, self.z, self.buf, self.done, self.raw = f, zlib.compressobj(6, zlib.DEFLATED, 31), b"", False, 0

    def readable(self) -> bool:
        return True

    def read(self, n: int = -1) -> bytes:
        while not self.done and (n is None or n < 0 or len(self.buf) < n):
            chunk = self.f.read(4 << 20)
            self.raw += len(chunk)
            self.buf += self.z.compress(chunk) if chunk else self.z.flush()
            self.done = not chunk
        if n is None or n < 0:
            out, self.buf = self.buf, b""
        else:
            out, self.buf = self.buf[:n], self.buf[n:]
        return out


def _client(settings: Settings) -> Any:
    from app.brand_manager.store import MediaStore

    return MediaStore(settings)._s3()  # the same private bucket and credentials


def run(settings: Settings, db_path: str, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    fd, tmp = tempfile.mkstemp(prefix="price_chat-nightly-", suffix=".db")
    os.close(fd)
    try:
        src, dst = sqlite3.connect(db_path), sqlite3.connect(tmp)
        try:
            # one pass over a read snapshot: the app's writes go on meanwhile (WAL). A stepped copy would restart at every write.
            src.backup(dst)
            ok = dst.execute("PRAGMA quick_check").fetchone()[0]
        finally:
            dst.close()
            src.close()
        if ok != "ok":
            raise RuntimeError(f"The backup copy failed SQLite's integrity check: {ok}")
        key = f"{PREFIX}price_chat-{now.strftime('%Y%m%d-%H%M%S')}.db.gz"
        s3 = _client(settings)
        with open(tmp, "rb") as f:
            body = _Gzip(f)
            s3.upload_fileobj(body, settings.portfolio_bucket, key, ExtraArgs={"ContentType": "application/gzip"})
        size = s3.head_object(Bucket=settings.portfolio_bucket, Key=key)["ContentLength"]
        # keep the newest KEEP (names sort by time)
        listed = s3.list_objects_v2(Bucket=settings.portfolio_bucket, Prefix=PREFIX).get("Contents") or []
        copies = sorted((o["Key"] for o in listed if o["Key"].endswith(".db.gz")), reverse=True)
        for k in copies[KEEP:]:
            s3.delete_object(Bucket=settings.portfolio_bucket, Key=k)
        kept = len(copies[:KEEP])
        return {"key": key, "database_mb": round(body.raw / 1e6, 1), "compressed_mb": round(size / 1e6, 1),
                "kept": kept, "removed": len(copies[KEEP:]),
                "summary": f"Database backed up: {body.raw / 1e6:,.0f} MB → {size / 1e6:,.0f} MB in the bucket ({kept} kept)"}
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def _taken_at(obj: dict[str, Any]) -> datetime | None:
    """When a copy was taken: from its name (price_chat-YYYYmmdd-HHMMSS.db.gz, written by run()), else the bucket's date."""
    m = _STAMP.search(obj.get("Key", ""))
    if m:
        return datetime.strptime(m.group(1), "%Y%m%d-%H%M%S").replace(tzinfo=timezone.utc)
    lm = obj.get("LastModified")
    if isinstance(lm, datetime):
        return lm if lm.tzinfo else lm.replace(tzinfo=timezone.utc)
    return None


def newest(settings: Settings, now: datetime | None = None, s3: Any = None) -> dict[str, Any]:
    """The newest non-empty backup in the bucket: {"key", "taken_at", "age_hours", "copies", "stale", "empty_newer"}; key None
    when there is none. `empty_newer`: zero-byte objects newer than it (an upload that wrote nothing), newest first: they
    don't count as a backup. Raises whatever the bucket client raises (wrong secret, timeout): the caller alerts."""
    now = now or datetime.now(timezone.utc)
    s3 = s3 or _client(settings)
    listed = s3.list_objects_v2(Bucket=settings.portfolio_bucket, Prefix=PREFIX).get("Contents") or []
    every = sorted(((t, o) for o in listed if o.get("Key", "").endswith(".db.gz") and (t := _taken_at(o)) is not None),
                   key=lambda c: c[0], reverse=True)
    copies = [(t, o) for t, o in every if o.get("Size", 1) != 0]  # Size is in every real listing; absent = unknown, kept
    newest_at = copies[0][0] if copies else None
    empty = [o["Key"] for t, o in every if o.get("Size", 1) == 0 and (newest_at is None or t > newest_at)]
    if not copies:
        return {"key": None, "taken_at": None, "age_hours": None, "copies": 0, "stale": True, "empty_newer": empty}
    taken, obj = copies[0]
    age = now - taken
    return {"key": obj["Key"], "taken_at": taken.isoformat(), "age_hours": round(age.total_seconds() / 3600, 1),
            "copies": len(copies), "stale": age > STALE_AFTER, "empty_newer": empty}


class RestoreTestFailed(RuntimeError):
    """The newest backup could not be restored into a sound database (the message says why; never a secret)."""


def restore_test(settings: Settings, now: datetime | None = None, s3: Any = None, expected_head: str | None = None) -> dict[str, Any]:
    """Download the newest backup to a temporary directory, gunzip it and check it as a restore would need it:
    PRAGMA integrity_check is "ok", alembic_version holds a revision, the key tables exist and members isn't empty.
    Opens only the downloaded copy (read-only). Raises RestoreTestFailed with the reason; returns the figures."""
    now = now or datetime.now(timezone.utc)
    s3 = s3 or _client(settings)
    info = newest(settings, now, s3)
    if info["empty_newer"]:
        raise RestoreTestFailed(f"The newest backup object is empty (0 bytes): {info['empty_newer'][0]}")
    if not info["key"]:
        raise RestoreTestFailed("No backup in the bucket to restore (backups/db/ is empty)")
    with tempfile.TemporaryDirectory(prefix="price_chat-restore-test-") as tmp:
        gz, db_file = Path(tmp) / "backup.db.gz", Path(tmp) / "restored.db"
        with open(gz, "wb") as f:
            s3.download_fileobj(settings.portfolio_bucket, info["key"], f)
        try:
            with gzip.open(gz, "rb") as src, open(db_file, "wb") as dst:
                shutil.copyfileobj(src, dst, 4 << 20)
        except (OSError, EOFError, zlib.error) as e:
            raise RestoreTestFailed(f"{info['key']} doesn't gunzip: {type(e).__name__}: {str(e)[:120]}") from e
        gz.unlink()
        size = db_file.stat().st_size
        if size == 0:
            raise RestoreTestFailed(f"{info['key']} unpacks to 0 bytes")
        try:
            con = sqlite3.connect(db_file.as_uri() + "?mode=ro", uri=True)
        except sqlite3.Error as e:
            raise RestoreTestFailed(f"{info['key']} doesn't open as SQLite: {e}") from e
        try:
            try:
                integrity = [r[0] for r in con.execute("PRAGMA integrity_check").fetchmany(5)]
            except sqlite3.DatabaseError as e:
                raise RestoreTestFailed(f"{info['key']} is not a sound SQLite database: {e}") from e
            if integrity != ["ok"]:
                raise RestoreTestFailed(f"{info['key']} failed PRAGMA integrity_check: {'; '.join(map(str, integrity))[:200]}")
            try:
                versions = [r[0] for r in con.execute("SELECT version_num FROM alembic_version")]
            except sqlite3.DatabaseError as e:
                raise RestoreTestFailed(f"{info['key']} has no alembic_version table ({e})") from e
            if len(versions) != 1:
                raise RestoreTestFailed(f"{info['key']} has {len(versions)} alembic versions, expected one")
            tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            missing = [t for t in KEY_TABLES if t not in tables]
            if missing:
                raise RestoreTestFailed(f"{info['key']} is missing table(s): {', '.join(missing)}")
            counts = {t: int(con.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]) for t in KEY_TABLES}  # names from KEY_TABLES only
        finally:
            con.close()
    if counts["members"] == 0:
        raise RestoreTestFailed(f"{info['key']} restored with 0 members: the copy is empty or the wrong database")
    version = versions[0]
    return {"key": info["key"], "taken_at": info["taken_at"], "age_hours": info["age_hours"], "database_mb": round(size / 1e6, 1),
            "integrity": "ok", "alembic_version": version, "matches_code_head": (version == expected_head) if expected_head else None,
            "counts": counts,
            "summary": (f"Restore test passed: {info['key'].rsplit('/', 1)[-1]} ({info['age_hours']} h old, {size / 1e6:,.0f} MB) "
                        f"integrity ok, alembic {version}, {counts['members']} members, {counts['vpn_devices']} VPN devices")}
