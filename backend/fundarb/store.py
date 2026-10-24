"""Module 6: persistence (SQLite, FUNDARB_UNIVERSE_DB_PATH).

Tables
- markets       one row per market ever seen: identity, class + the rule that set it, collateral, capital bucket, pairing
- quotes        time series: every live market's context at each run (funding, basis, OI, volume, spread, depth, leverage)
- gate_runs     one row per gate run: schema hash, the exact gate config, pass counts, wall-clock cost, API calls
- gate_results  per market per run: passed, first failed gate, every check, flags, and the full input record
- pairs         pair identities (Phase C fills it)
- scan_results  every list a scan produces (capital_apr, score, transient, net_unknown, rejected), each row with its full
                inputs, metrics and the scan config
- cluster_runs / cluster_members   the correlation clustering behind each scan (not printed; inspect with SQL)
Everything a decision used is stored next to it, so a run can be replayed and must reproduce the same result."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from contextlib import closing
from pathlib import Path
from typing import Any

DDL = [
    """CREATE TABLE IF NOT EXISTS markets (raw_name TEXT PRIMARY KEY, namespace TEXT NOT NULL, builder TEXT, asset_class TEXT NOT NULL,
       class_rule TEXT NOT NULL, collateral_token TEXT NOT NULL, capital_bucket TEXT NOT NULL, pairing_eligible INTEGER NOT NULL,
       pairing_note TEXT NOT NULL, listing_live INTEGER NOT NULL, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS quotes (ts TEXT NOT NULL, raw_name TEXT NOT NULL, funding_rate_raw REAL, funding_interval_hours REAL,
       funding_apr REAL, premium REAL, mark_price REAL, oracle_price REAL, basis_bps REAL, open_interest_usd REAL, volume_24h_usd REAL,
       max_leverage REAL, spread_bps REAL, spread_source TEXT, depth_usd_1pct REAL, depth_source TEXT, PRIMARY KEY (raw_name, ts))""",
    "CREATE INDEX IF NOT EXISTS ix_quotes_ts ON quotes (ts)",
    """CREATE TABLE IF NOT EXISTS gate_runs (run_id TEXT PRIMARY KEY, ts TEXT NOT NULL, schema_hash TEXT NOT NULL, config TEXT NOT NULL,
       counts TEXT NOT NULL, elapsed_s REAL NOT NULL, calls INTEGER NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS gate_results (run_id TEXT NOT NULL, raw_name TEXT NOT NULL, passed INTEGER NOT NULL, failed_gate TEXT,
       checks TEXT NOT NULL, flags TEXT NOT NULL, inputs TEXT NOT NULL, PRIMARY KEY (run_id, raw_name))""",
    """CREATE TABLE IF NOT EXISTS pairs (pair_id TEXT PRIMARY KEY, kind TEXT NOT NULL, leg_a TEXT NOT NULL, venue_a TEXT NOT NULL,
       leg_b TEXT NOT NULL, venue_b TEXT NOT NULL, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS scan_results (scan_id TEXT NOT NULL, ts TEXT NOT NULL, list_name TEXT NOT NULL, rank INTEGER NOT NULL,
       pair_id TEXT NOT NULL, metrics TEXT NOT NULL, inputs TEXT NOT NULL, config TEXT NOT NULL, PRIMARY KEY (scan_id, list_name, rank))""",
    """CREATE TABLE IF NOT EXISTS cluster_runs (scan_id TEXT PRIMARY KEY, ts TEXT NOT NULL, threshold REAL NOT NULL, window_days REAL NOT NULL,
       legs INTEGER NOT NULL, clusters INTEGER NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS cluster_members (scan_id TEXT NOT NULL, leg_id TEXT NOT NULL, cluster INTEGER NOT NULL, hours INTEGER NOT NULL,
       PRIMARY KEY (scan_id, leg_id))""",
]
SCAN_LISTS = ("capital_apr", "score", "transient", "unprofitable", "net_unknown", "rejected")
QUOTE_COLS = ["ts", "raw_name", "funding_rate_raw", "funding_interval_hours", "funding_apr", "premium", "mark_price", "oracle_price",
              "basis_bps", "open_interest_usd", "volume_24h_usd", "max_leverage", "spread_bps", "spread_source", "depth_usd_1pct",
              "depth_source"]


class UniverseStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        with closing(self._conn()) as c, c:
            for sql in DDL:
                c.execute(sql)

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path)
        c.row_factory = sqlite3.Row
        return c

    def save_markets(self, ts: str, rows: Iterable[dict[str, Any]]) -> None:
        with closing(self._conn()) as c, c:
            c.executemany(
                """INSERT INTO markets VALUES (:raw_name, :namespace, :builder, :asset_class, :class_rule, :collateral_token, :capital_bucket,
                   :pairing_eligible, :pairing_note, :listing_live, :ts, :ts)
                   ON CONFLICT(raw_name) DO UPDATE SET namespace=excluded.namespace, builder=excluded.builder, asset_class=excluded.asset_class,
                   class_rule=excluded.class_rule, collateral_token=excluded.collateral_token, capital_bucket=excluded.capital_bucket,
                   pairing_eligible=excluded.pairing_eligible, pairing_note=excluded.pairing_note, listing_live=excluded.listing_live,
                   last_seen=excluded.last_seen""",
                [{**r, "ts": ts} for r in rows])

    def save_quotes(self, rows: Iterable[dict[str, Any]]) -> int:
        rows = list(rows)
        with closing(self._conn()) as c, c:
            c.executemany(f"INSERT OR REPLACE INTO quotes ({', '.join(QUOTE_COLS)}) VALUES ({', '.join(':' + k for k in QUOTE_COLS)})", rows)
        return len(rows)

    def basis_history(self, names: Iterable[str], before_ts: str, limit: int = 2000) -> dict[str, list[float]]:
        """Each market's basis samples (bps) strictly before `before_ts`, oldest first, at most `limit` of the latest."""
        out: dict[str, list[float]] = {}
        with closing(self._conn()) as c:
            for n in names:
                rows = c.execute("SELECT basis_bps FROM quotes WHERE raw_name = ? AND ts < ? AND basis_bps IS NOT NULL ORDER BY ts DESC LIMIT ?",
                                 (n, before_ts, limit)).fetchall()
                out[n] = [float(r[0]) for r in reversed(rows)]
        return out

    def save_gate_run(self, run: dict[str, Any], results: Iterable[dict[str, Any]]) -> None:
        with closing(self._conn()) as c, c:
            c.execute("INSERT INTO gate_runs VALUES (:run_id, :ts, :schema_hash, :config, :counts, :elapsed_s, :calls)",
                      {**run, "config": json.dumps(run["config"], sort_keys=True), "counts": json.dumps(run["counts"], sort_keys=True)})
            c.executemany("INSERT INTO gate_results VALUES (:run_id, :raw_name, :passed, :failed_gate, :checks, :flags, :inputs)",
                          [{**r, "run_id": run["run_id"], "checks": json.dumps(r["checks"]), "flags": json.dumps(r["flags"]),
                            "inputs": json.dumps(r["inputs"], sort_keys=True, default=str)} for r in results])

    def load_gate_run(self, run_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        with closing(self._conn()) as c:
            run = c.execute("SELECT * FROM gate_runs WHERE run_id = ?", (run_id,)).fetchone()
            if run is None:
                raise KeyError(f"no gate run {run_id}")
            res = c.execute("SELECT * FROM gate_results WHERE run_id = ? ORDER BY raw_name", (run_id,)).fetchall()
        r = dict(run)
        r["config"], r["counts"] = json.loads(r["config"]), json.loads(r["counts"])
        return r, [{**dict(x), "checks": json.loads(x["checks"]), "flags": json.loads(x["flags"]), "inputs": json.loads(x["inputs"])} for x in res]

    def latest_gate_run(self) -> str | None:
        with closing(self._conn()) as c:
            row = c.execute("SELECT run_id FROM gate_runs ORDER BY ts DESC LIMIT 1").fetchone()
        return row[0] if row else None

    def save_scan(self, scan_id: str, ts: str, config: dict[str, Any], lists: dict[str, list[dict[str, Any]]], clusters: dict[str, Any]) -> None:
        cfg = json.dumps(config, sort_keys=True)
        rows, pairs = [], {}
        for name in SCAN_LISTS:
            for i, r in enumerate(lists.get(name, []), 1):
                rows.append((scan_id, ts, name, i, r["pair_id"], json.dumps(r["metrics"], sort_keys=True, default=str),
                             json.dumps(r["inputs"], sort_keys=True, default=str), cfg))
                p = r["inputs"]
                pairs[r["pair_id"]] = (r["pair_id"], p["kind"], p["short"]["leg_id"], p["short"]["venue"], p["long"]["leg_id"], p["long"]["venue"], ts, ts)
        members = clusters.get("members", [])
        with closing(self._conn()) as c, c:
            c.executemany("INSERT INTO scan_results VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
            c.executemany("INSERT INTO pairs VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(pair_id) DO UPDATE SET last_seen=excluded.last_seen", list(pairs.values()))
            c.execute("INSERT INTO cluster_runs VALUES (?, ?, ?, ?, ?, ?)", (scan_id, ts, clusters.get("threshold"), clusters.get("window_days"),
                                                                            len(members), len({m["cluster"] for m in members})))
            c.executemany("INSERT INTO cluster_members VALUES (?, ?, ?, ?)", [(scan_id, m["leg_id"], m["cluster"], m["hours"]) for m in members])

    def load_scan(self, scan_id: str) -> dict[str, Any]:
        with closing(self._conn()) as c:
            rows = c.execute("SELECT * FROM scan_results WHERE scan_id = ? ORDER BY list_name, rank", (scan_id,)).fetchall()
        if not rows:
            raise KeyError(f"no scan {scan_id}")
        lists: dict[str, list[dict[str, Any]]] = {}
        for r in rows:
            lists.setdefault(r["list_name"], []).append({"pair_id": r["pair_id"], "rank": r["rank"], "metrics": json.loads(r["metrics"]),
                                                        "inputs": json.loads(r["inputs"])})
        return {"scan_id": scan_id, "ts": rows[0]["ts"], "config": json.loads(rows[0]["config"]), "lists": lists}

    def latest_scan(self) -> str | None:
        with closing(self._conn()) as c:
            row = c.execute("SELECT scan_id FROM scan_results ORDER BY ts DESC LIMIT 1").fetchone()
        return row[0] if row else None

    def counts(self) -> dict[str, int]:
        with closing(self._conn()) as c:
            return {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                    for t in ("markets", "quotes", "gate_runs", "gate_results", "pairs", "scan_results", "cluster_runs", "cluster_members")}
