"""Quote history (Module 2): a rolling in-memory window per (venue, symbol) for the persistence checks, and SQLite for the
backtester. Every VenueQuote field is stored, so any decision can be replayed from exactly the inputs it saw."""

import sqlite3
from collections import defaultdict, deque
from dataclasses import asdict, fields
from datetime import datetime, timedelta
from pathlib import Path

from fundarb.models import VenueQuote

COLUMNS = [f.name for f in fields(VenueQuote)]


class QuoteHistory:
    """In-memory, newest last; anything older than `days` (relative to the newest sample) is dropped on append."""

    def __init__(self, days: float = 7.0) -> None:
        self.window = timedelta(days=days)
        self._d: dict[tuple[str, str], deque[VenueQuote]] = defaultdict(deque)

    def add(self, q: VenueQuote) -> None:
        dq = self._d[(q.venue, q.symbol)]
        if dq and q.timestamp_utc < dq[-1].timestamp_utc:
            raise ValueError(f"{q.venue} {q.symbol}: quotes must be added in time order")
        dq.append(q)
        cutoff = q.timestamp_utc - self.window
        while dq and dq[0].timestamp_utc < cutoff:
            dq.popleft()

    def series(self, venue: str, symbol: str) -> list[VenueQuote]:
        return list(self._d.get((venue, symbol), ()))

    def latest(self, venue: str, symbol: str) -> VenueQuote | None:
        dq = self._d.get((venue, symbol))
        return dq[-1] if dq else None

    def keys(self) -> list[tuple[str, str]]:
        return sorted(k for k, v in self._d.items() if v)


class QuoteStore:
    """SQLite persistence. One row per VenueQuote; timestamps stored as ISO-8601 UTC."""

    def __init__(self, path: Path | str) -> None:
        self.path = str(path)
        with self._conn() as c:
            c.execute(f"CREATE TABLE IF NOT EXISTS quotes ({', '.join(self._coltypes())})")
            c.execute("CREATE INDEX IF NOT EXISTS ix_quotes_key_ts ON quotes (venue, symbol, timestamp_utc)")

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    @staticmethod
    def _coltypes() -> list[str]:
        out = []
        for f in fields(VenueQuote):
            t = "TEXT" if f.name in ("venue", "symbol", "timestamp_utc", "funding_interval_source", "funding_kind", "fee_source") \
                else "INTEGER" if f.name == "depth_complete" else "REAL"
            out.append(f"{f.name} {t}")
        return out

    def save(self, quotes: list[VenueQuote]) -> int:
        rows = []
        for q in quotes:
            d = asdict(q)
            d["timestamp_utc"] = q.timestamp_utc.isoformat()
            d["depth_complete"] = int(q.depth_complete)
            rows.append([d[c] for c in COLUMNS])
        with self._conn() as c:
            c.executemany(f"INSERT INTO quotes ({', '.join(COLUMNS)}) VALUES ({', '.join('?' * len(COLUMNS))})", rows)
        return len(rows)

    def load(self, venue: str | None = None, symbol: str | None = None, since: datetime | None = None) -> list[VenueQuote]:
        where, args = [], []
        for col, val in (("venue", venue), ("symbol", symbol)):
            if val is not None:
                where.append(f"{col} = ?")
                args.append(val)
        if since is not None:
            where.append("timestamp_utc >= ?")
            args.append(since.isoformat())
        sql = f"SELECT {', '.join(COLUMNS)} FROM quotes" + (f" WHERE {' AND '.join(where)}" if where else "") + " ORDER BY timestamp_utc"
        with self._conn() as c:
            out = []
            for row in c.execute(sql, args):
                d = dict(zip(COLUMNS, row))
                d["timestamp_utc"] = datetime.fromisoformat(d["timestamp_utc"])
                d["depth_complete"] = bool(d["depth_complete"])
                out.append(VenueQuote(**d))
        return out
