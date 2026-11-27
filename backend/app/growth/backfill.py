"""One-time Market Memory backfill: full history from the public endpoints (Hyperliquid, Coinbase Exchange, alternative.me), then the
rollups and breadth. Idempotent: running it again rewrites the same days.

    python -m app.growth.backfill                       # the configured DATABASE_URL (migrations are applied first)
    python -m app.growth.backfill --funding-coins 30 --funding-days 365 --max-coins 120

No keys are needed. In production, sources whose licence isn't cleared are skipped (app/growth/sources.py). Nothing is published.
"""

from __future__ import annotations

import argparse
import json
import sys


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="app.growth.backfill")
    ap.add_argument("--funding-coins", type=int, default=40, help="funding history for the N most traded perps (default 40)")
    ap.add_argument("--funding-days", type=int, default=180, help="days of funding history (default 180)")
    ap.add_argument("--max-coins", type=int, default=None, help="cap the Hyperliquid universe (default: every perp with >= $250k 24h volume)")
    ap.add_argument("--steps", default="hyperliquid,funding,fng,oi,liquidations,breadth", help="coinbase is off by default (not cleared; research only, never in production)")
    ap.add_argument("--incremental", action="store_true", help="only from each series' newest stored day (the daily job's mode)")
    a = ap.parse_args(argv)

    from app.db.session import SessionLocal, init_db
    from app.growth import collectors

    init_db()
    with SessionLocal() as db:
        report = collectors.collect(db, full=not a.incremental, funding_coins=a.funding_coins, funding_days=a.funding_days,
                                    max_coins=a.max_coins, steps=tuple(s.strip() for s in a.steps.split(",") if s.strip()),
                                    progress=lambda m: print(m, flush=True))
    print(json.dumps(report, indent=2, default=str))
    return 0 if not report["errors"] else 1


if __name__ == "__main__":
    sys.exit(main())
