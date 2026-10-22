"""Edge research (edge.py): the daily portfolio accounting (returns, costs on every change, funding) and the maker-entry
replay (a limit that is never touched is never filled; the stop comes first). TEST FIXTURES only, no network."""

import numpy as np

from app.tradingos import edge
from app.tradingos import intraday as ix
from app.tradingos.market import Candle


def test_daily_returns_pay_costs_on_changes_and_funding_every_day() -> None:
    close = np.array([[100.0], [101.0], [102.01], [103.0301]])  # +1% a day
    w = np.ones_like(close)
    fr = np.full_like(close, 0.0001)  # longs pay 0.01% a day
    r = edge.run(w, close, fr, ["BTC"])
    cost = edge.FEE + edge.slip("BTC")
    assert np.allclose(r, [0.01 - cost - 0.0001, 0.01 - 0.0001, 0.01 - 0.0001])  # the entry costs once, holding doesn't
    short = edge.run(-w, close, fr, ["BTC"])
    assert np.allclose(short[1:], -0.01 + 0.0001)  # shorts receive funding


def test_signals_only_use_the_past_and_gross_exposure_is_one() -> None:
    up = np.cumprod(np.tile([[1.02], [1.0]], (30, 3)), axis=0) * np.array([1.0, 2.0, 3.0])  # rising, with some wobble
    up[:, 2] = up[::-1, 2]  # the third coin falls
    w = edge.tsmom(up)
    assert (w[:30] == 0).all() and np.allclose(np.abs(w[30:]).sum(1), 1)
    assert (w[40, :2] > 0).all() and w[40, 2] < 0
    x = edge.xsmom(np.hstack([up, up[:, :1] * 0.5, up[:, 1:2] * 0.7, up[:, 2:3] * 0.9]), n=1)
    assert np.allclose(np.abs(x[14:]).sum(1), 1) and np.allclose(x[14:].sum(1), 0)  # market-neutral


def bars(rows: list[tuple[float, float, float, float]]) -> ix.Series:
    return ix.series([Candle(n * 900_000, o, h, lo, c, 1000) for n, (o, h, lo, c) in enumerate(rows)], 900_000)


def test_a_limit_never_touched_is_never_filled_and_the_stop_comes_first() -> None:
    runaway = bars([(100, 100, 100, 100), (101, 103, 100.5, 102), (102, 104, 101.5, 103), (103, 105, 102, 104)])
    assert edge.maker_trade(runaway, 0, "LONG", 99.0, "F_scalp", "BTC", []) is None  # price never came back to 100
    stopped = bars([(100, 100, 100, 100), (100.2, 100.3, 98.5, 99), (99, 99.5, 98, 98.5)])
    r = edge.maker_trade(stopped, 0, "LONG", 99.0, "F_scalp", "BTC", [])
    assert r is not None and -1.2 < r < -1.0  # filled at 100 then stopped at 99 in the same candle: -1R and the costs
    win = bars([(100, 100, 100, 100), (100.1, 100.4, 99.9, 100.2), (100.2, 102.2, 100.1, 102)])
    r = edge.maker_trade(win, 0, "LONG", 99.0, "F_scalp", "BTC", [])
    assert r is not None and 1.3 < r < 1.4  # TP1 1R + TP2 1.8R on halves, maker fees: about +1.4R minus fees
