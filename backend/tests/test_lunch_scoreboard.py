"""Earn › Lunch Bot scoreboard: the 30-day daily P&L series per strategy and the graduation-gap stats (paper trades only)."""

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.arbitrage import lunch
from app.models.arbitrage import ArbLunchGraduation, ArbLunchTrade

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)


def trade(db: Session, i: int, strategy: str, closed_at: datetime, pnl: float, status: str = "closed") -> None:
    db.add(ArbLunchTrade(strategy=strategy, mint=f"m{i}", bonding_curve=f"c{i}", symbol="X", name="x", launched_at=closed_at, status=status,
                         opened_at=closed_at, closed_at=closed_at if status == "closed" else None, entry_sol=0.1, tokens=1.0, entry_price=1.0,
                         entry_mc_sol=1.0, exit_sol=0.1 + pnl, pnl_sol=pnl, exit_reason="target"))


def test_daily_series_groups_closed_trades_by_utc_day_with_a_running_total(db: Session) -> None:
    trade(db, 1, "starter", NOW - timedelta(hours=1), 0.05)
    trade(db, 2, "starter", NOW - timedelta(hours=2), -0.02)
    trade(db, 3, "starter", NOW - timedelta(days=2), 0.01)
    trade(db, 4, "graduation", NOW - timedelta(days=1), -0.03)
    trade(db, 5, "starter", NOW - timedelta(days=40), 9.0)  # outside the window
    trade(db, 6, "starter", NOW, 1.0, status="open")  # open trades never count
    db.commit()
    s = lunch.daily_series(db, NOW)
    st, gr = s["starter"], s["graduation"]
    assert len(st) == 30 and len(gr) == 30 and st[0]["day"] == "2026-09-22" and st[-1]["day"] == "2026-10-21"
    assert st[-3] == {"day": "2026-10-19", "trades": 1, "wins": 1, "pnl_sol": 0.01, "cumulative": 0.01}
    assert st[-2]["trades"] == 0 and st[-2]["cumulative"] == 0.01
    assert st[-1] == {"day": "2026-10-21", "trades": 2, "wins": 1, "pnl_sol": 0.03, "cumulative": 0.04}
    assert gr[-2]["pnl_sol"] == -0.03 and gr[-1]["cumulative"] == -0.03 and gr[-2]["wins"] == 0


def test_gap_stats_split_last_7_days_from_all_time(db: Session) -> None:
    for i, (gap, age) in enumerate([(10.0, 1), (-4.0, 2), (2.0, 3), (30.0, 20), (None, 1)]):
        db.add(ArbLunchGraduation(mint=f"g{i}", symbol="G", curve_price=1.0, pool_price=1.0, gap_pct=gap, priced_at=NOW - timedelta(days=age)))
    db.commit()
    g = lunch.gap_stats(db, NOW)
    assert g["all"] == {"count": 4, "avg_gap_pct": 9.5, "median_gap_pct": 6.0, "positive": 3}
    assert g["last_7d"] == {"count": 3, "avg_gap_pct": 2.67, "median_gap_pct": 2.0, "positive": 2}
    assert g["costs_pct"] == round((lunch.CURVE_FEE + lunch.POOL_FEE) * 100, 2)


def test_the_public_page_carries_the_scoreboard(client: TestClient, db: Session) -> None:
    trade(db, 1, "graduation", datetime.now(timezone.utc) - timedelta(minutes=5), 0.02)
    db.commit()
    sb = client.get("/api/members/arbitrage/lunch").json()["scoreboard"]
    assert sb["days"] == 30 and set(sb["series"]) == set(lunch.STRATEGIES)
    assert sb["series"]["graduation"][-1]["cumulative"] == 0.02 and sb["gaps"]["all"]["count"] == 0
