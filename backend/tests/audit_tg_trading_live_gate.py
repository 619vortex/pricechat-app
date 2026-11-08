"""AUDIT (2026-11-08, TR findings). Run from C:/AI/pc-wt/tg-trade/backend with
  python -m pytest -p tests.conftest <this file>   (it needs feat/tg-trading's app.tradingos.tgtrade).
Shows that a manual /open on a LIVE account skips the LIVE-enabled check that execution._plan enforces."""
from typing import Any

import pytest
from sqlalchemy import select

from app.models.trading import TrPosition
from app.tradingos import execution, market, signals, tgtrade
from tests.test_trading_execution import FakeExchange
from tests.test_trading_os import FakeMD, uptrend_pullback
from tests.test_tg_trading import banter


@pytest.fixture
def md(monkeypatch: pytest.MonkeyPatch) -> FakeMD:
    f = FakeMD({"BTC": uptrend_pullback()})
    monkeypatch.setattr(market, "_shared", f)
    return f


def test_manual_live_open_ignores_live_enabled_at(db: Any, md: FakeMD) -> None:
    m = banter(db, "991")
    acc = execution.account(db, m)
    acc.mode, acc.master_address, acc.live_enabled_at = "LIVE", "0x" + "1" * 40, None  # LIVE NOT enabled
    acc.mainnet_agent, acc.mainnet_agent_key = "0x" + "2" * 40, "unused"
    s = signals.settings(db)
    s.strategies = {**(s.strategies or {}), "telegram_trading": {"live": True}}
    db.commit()
    ex = FakeExchange(mark=md.market("BTC").mark)
    it = tgtrade.preview_open(db, m, tgtrade.OpenRequest("BTC", "LONG", 50.0, 3), ex)
    pos = tgtrade.confirm_open(db, m, it.token, ex)
    assert pos.mode == "LIVE" and pos.status == "OPEN"  # traded although live_enabled_at is None
    assert any(o["type"] == "market" and not o["reduce"] for o in ex.orders)
