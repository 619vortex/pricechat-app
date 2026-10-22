"""Nightly off-volume backup of the SQLite database (the `system.backup` job).

Everything (members, XP, wallets, trading accounts, the market) lives in one SQLite file on one Railway volume, so a lost
or corrupted volume would lose it all. Once a day this takes a consistent copy with SQLite's online backup API (the app
keeps running), checks the copy is sound (PRAGMA quick_check), and streams it gzip-compressed to the private bucket
(the one Brand Manager and the portfolio photos use, which is separate storage from the volume), under backups/db/.
The newest KEEP copies are kept. The copy is built in the container's temporary disk, never on the nearly full volume,
and deleted afterwards. Secrets inside stay encrypted (the key is an environment variable, not in the file).

Restore: download the newest backups/db/*.db.gz from the bucket, gunzip it, and put it in place of the volume's
database file while the app is stopped (or GET /api/system/db-backup for a fresh copy any time)."""

import io
import os
import sqlite3
import tempfile
import zlib
from datetime import datetime, timezone
from typing import Any

from app.config.settings import Settings

PREFIX = "backups/db/"
KEEP = 14


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
