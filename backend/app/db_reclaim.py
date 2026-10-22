"""Pre-start housekeeping (runs before uvicorn): free space in the SQLite database when the volume is full.

The simulated market maker's re-quotes leave cancelled, never-filled orders behind; they once filled the volume and the
app could no longer start (startup writes). Before anything else is running, this deletes those dead system quotes in
small committed batches (each needs only a little WAL space) and truncates the WAL, so SQLite has free pages to reuse.
Members' orders, trades, balances and the ledger are never touched. Always exits 0: the app starts either way.

    python -m app.db_reclaim
"""

import os
import sqlite3
import sys
import time

BATCH = 2000
BUDGET_SECONDS = 60


def _path() -> str | None:
    url = os.environ.get("DATABASE_URL", "")
    return url[len("sqlite:///"):] if url.startswith("sqlite:///") else None


def main() -> None:
    path = _path()
    if not path or not os.path.exists(path):
        return
    d = os.path.dirname(path)
    st = os.statvfs(d)
    print(f"db_reclaim: {st.f_bavail * st.f_frsize / 1e6:.1f} MB free in {d}", flush=True)
    # expired API cache files first (Scrydex 12 h, TCGdex 7 days): already stale, re-fetching them costs nothing extra
    freed = 0
    for sub, hours in (("scrydex", 12), ("tcgdex", 7 * 24)):
        cache = os.path.join(d, "cache", sub)
        for f in os.listdir(cache) if os.path.isdir(cache) else []:
            p = os.path.join(cache, f)
            try:
                if f.endswith(".json") and time.time() - os.path.getmtime(p) > hours * 3600:
                    freed += os.path.getsize(p)
                    os.remove(p)
            except OSError:
                pass
    st = os.statvfs(d)
    print(f"db_reclaim: removed {freed / 1e6:.1f} MB of expired cache; {st.f_bavail * st.f_frsize / 1e6:.1f} MB free", flush=True)
    # what's on the volume (sizes only), so space problems can be diagnosed from the deploy log
    for name in sorted(os.listdir(d)):
        p = os.path.join(d, name)
        size = sum(os.path.getsize(os.path.join(r, f)) for r, _, fs in os.walk(p) for f in fs) if os.path.isdir(p) else os.path.getsize(p)
        print(f"db_reclaim: volume {name}: {size / 1e6:.1f} MB", flush=True)
    # emergency: under 50 MB free, SQLite can't grow its write-ahead log and every write fails. The API cache is only a copy
    # of provider answers (re-fetched on demand), so all of it goes before the app starts.
    if st.f_bavail * st.f_frsize < 50e6:
        cache, gone = os.path.join(d, "cache"), 0
        for root, _, files in os.walk(cache) if os.path.isdir(cache) else []:
            for f in files:
                p = os.path.join(root, f)
                try:
                    gone += os.path.getsize(p)
                    os.remove(p)
                except OSError:
                    pass
        st = os.statvfs(d)
        print(f"db_reclaim: low space: cleared {gone / 1e6:.1f} MB of API cache; {st.f_bavail * st.f_frsize / 1e6:.1f} MB free", flush=True)
    c = sqlite3.connect(path, timeout=30, isolation_level=None)
    try:
        try:
            print("db_reclaim: checkpoint", c.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone(), flush=True)
        except sqlite3.Error as e:
            print(f"db_reclaim: checkpoint failed: {e}", flush=True)
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "ex_accounts" not in tables:
            return
        pages, free = (c.execute(f"PRAGMA {p}").fetchone()[0] for p in ("page_count", "freelist_count"))
        started, total = time.monotonic(), 0
        for table in ("ex_orders", "perp_orders"):
            if table not in tables:
                continue
            while time.monotonic() - started < BUDGET_SECONDS:
                n = c.execute(
                    f"DELETE FROM {table} WHERE id IN (SELECT id FROM {table} WHERE status='cancelled' AND filled=0"
                    f" AND account_id IN (SELECT id FROM ex_accounts WHERE member_id IS NULL) LIMIT {BATCH})").rowcount
                c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                total += n
                if n < BATCH:
                    break
        after = c.execute("PRAGMA freelist_count").fetchone()[0]
        print(f"db_reclaim: deleted {total} dead maker quotes; free pages {free} -> {after} of {pages}", flush=True)
    except sqlite3.Error as e:
        print(f"db_reclaim: stopped: {e}", file=sys.stderr, flush=True)
    finally:
        c.close()


if __name__ == "__main__":
    main()
