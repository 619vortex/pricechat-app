"""Committee production isolation: no order / execution / exchange code is imported anywhere in app/committee, and a run
changes no table outside the committee's own (tr_positions, member accounts, API wallets, settings all untouched)."""

import asyncio
import re
from pathlib import Path

from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from app.committee import orchestrator
from tests.committee_fixtures import FakeLLM, btc, doge

ROOT = Path(__file__).resolve().parents[1] / "app" / "committee"
FORBIDDEN = [
    re.compile(r"^\s*(from|import)\s+app\.tradingos\.(execution|exchange|accounts|terminal|bot)\b", re.M),
    re.compile(r"^\s*from\s+app\.tradingos\s+import\s+[^\n]*\b(execution|exchange|accounts|terminal)\b", re.M),
    re.compile(r"^\s*(from|import)\s+app\.(exchange|tradingbot)\b", re.M),
    re.compile(r"^\s*(from|import)\s+hyperliquid\b", re.M),
    re.compile(r"\b(TrPosition|TrOrder|TrIntent|TrAccount|open_position|place_order|market_open|bulk_orders|cancel_order)\b"),
]


def test_no_order_placement_code_is_imported_in_app_committee() -> None:
    files = list(ROOT.rglob("*.py"))
    assert len(files) >= 8
    bad = [f"{f.relative_to(ROOT)}: {m.group(0).strip()}" for f in files for p in FORBIDDEN for m in p.finditer(f.read_text(encoding="utf-8"))]
    assert bad == []


def _counts(db: Session) -> dict[str, int]:
    names = inspect(db.get_bind()).get_table_names()
    return {n: db.execute(text(f'SELECT COUNT(*) FROM "{n}"')).scalar_one() for n in names}


def test_a_run_touches_only_committee_tables(db: Session) -> None:
    before = _counts(db)
    asyncio.run(orchestrator.run_committee(db, "BTC", evidence=btc(), backend=FakeLLM()))
    asyncio.run(orchestrator.run_committee(db, "DOGE", evidence=doge()))
    after = _counts(db)
    changed = {n for n in after if after[n] != before.get(n)}
    assert changed <= {"cm_runs", "cm_usage", "usage_counters"}, changed
    assert after["tr_positions"] == before["tr_positions"] and after["tr_orders"] == before["tr_orders"]
    assert after["tr_accounts"] == before["tr_accounts"] and after["ts_settings"] == before["ts_settings"]


def test_importing_the_committee_loads_no_execution_module() -> None:
    """The same, at run time: a fresh interpreter importing the committee never loads execution / exchange code."""
    import os
    import subprocess
    import sys

    code = ("import sys, app.committee.orchestrator, app.committee.outcomes, app.committee.remote, app.committee.risk_gate, "
            "app.committee.llm; bad = [m for m in sys.modules if m in ('app.tradingos.execution', 'app.tradingos.exchange', "
            "'app.tradingos.accounts', 'app.tradingos.terminal') or m.startswith(('app.exchange', 'hyperliquid'))]; print(bad)")
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT.parents[1], capture_output=True, text=True, timeout=120,
                         env={**os.environ, "PYTHONPATH": os.pathsep.join([os.environ.get("PYTHONPATH", ""), str(ROOT.parents[1])])})
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.strip() == "[]"
