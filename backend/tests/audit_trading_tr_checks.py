"""AUDIT (2026-11-08, TR findings): read-only checks of trading assumptions. No network, no orders."""
from sqlalchemy import select
from sqlalchemy.dialects import sqlite

from app.models.trading import TrAccount
from app.tradingos import risk


def test_sqlite_ignores_with_for_update() -> None:
    """execution.open_position relies on SELECT ... FOR UPDATE to serialize racing entries; SQLite renders no lock at all."""
    sql = str(select(TrAccount.id).where(TrAccount.id == 1).with_for_update().compile(dialect=sqlite.dialect()))
    assert "FOR UPDATE" not in sql.upper()


def test_min_bumped_position_gets_take_profit_orders_under_10_usd() -> None:
    """Any position under $20 notional: protect() splits it into two TP orders, at least one under $10."""
    p = risk.plan_trade(coin="SOL", side="LONG", mark=150.0, entry_zone=(149.0, 151.0), stop=140.0, target=165.0, sig_lev_hi=3,
                        market_max_lev=20, sz_decimals=2, equity=40.0, max_leverage=3, max_loss_usd=5.0, max_allocation_pct=10.0,
                        max_positions=2, open_positions=0, daily_pnl=0.0, daily_loss_limit=15.0, allowed=[], paused=False,
                        global_paused=False, min_bump=True)
    assert p.ok and p.notional < 20  # a small but valid position (default 10% allocation of a $40 account)
    half = risk.round_size(p.size / 2, 2)
    rest = risk.round_size(p.size - half, 2)
    assert half * p.tp1 < 10 and rest * p.tp2 < 15  # each target order is under Hyperliquid's $10 minimum order value or close to it
    print("size", p.size, "notional", p.notional, "tp1 order $", half * p.tp1, "tp2 order $", rest * p.tp2)
