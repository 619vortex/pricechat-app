"""The strategy lab replays trades the way execution.protect manages them: stop for the whole size, half at TP1, the rest at TP2,
stop first when one candle touches both, costs and funding included. Synthetic candles only."""

import pytest

from app.tradingos import intraday as ix
from app.tradingos import lab
from app.tradingos.market import Candle

H = 3_600_000


def series(bars: list[tuple[float, float, float, float]]) -> ix.Series:
    return ix.series([Candle(i * H, o, h, lo, c, 1.0) for i, (o, h, lo, c) in enumerate(bars)], H)


NO_FUNDING = lab.Funding([], [])


def test_half_at_tp1_rest_at_tp2_like_the_live_orders() -> None:
    # signal close 100, stop 98 (R=2): TP1 104 (2R) for half, TP2 106 (3R) for the rest
    f = series([(100, 100, 100, 100), (100, 101, 99.5, 100.5), (100.5, 104.5, 100, 104), (104, 106.5, 103, 106)])
    r = lab.simulate(f, 0, "LONG", 98.0, lab.LIVE_EXIT, "BTC", NO_FUNDING)
    assert r is not None and r["exit"] == "tp1+tp2"
    assert 2.0 < r["r_net"] < 2.5  # (0.5*2R + 0.5*3R) minus fees and slippage


def test_stop_first_when_a_candle_touches_both_and_costs_are_charged() -> None:
    f = series([(100, 100, 100, 100), (100, 105, 97, 100)])
    r = lab.simulate(f, 0, "LONG", 98.0, lab.LIVE_EXIT, "BTC", NO_FUNDING)
    assert r is not None and r["exit"] == "stop" and r["r_net"] < -1.0  # a full loss plus costs, never a win


def test_breakeven_variant_and_funding_direction() -> None:
    f = series([(100, 100, 100, 100), (100, 104.5, 99.5, 104), (104, 104, 99.9, 100)])
    be = lab.simulate(f, 0, "LONG", 98.0, lab.Exit(2.0, 3.0, be=True), "BTC", NO_FUNDING)
    assert be is not None and be["exit"] == "tp1+be"
    flat = series([(100, 100, 100, 100)] + [(100, 100.5, 99.5, 100)] * 5 + [(100, 100, 97, 97)])
    paid = lab.Funding([i * H for i in range(1, 8)], [0.001] * 7)
    lng = lab.simulate(flat, 0, "LONG", 98.0, lab.LIVE_EXIT, "BTC", paid)
    sht = lab.simulate(series([(100, 100, 100, 100)] + [(100, 100.5, 99.5, 100)] * 5 + [(100, 103, 100, 103)]), 0, "SHORT", 102.0,
                       lab.LIVE_EXIT, "BTC", paid)
    assert lng is not None and sht is not None
    assert lng["r_net"] < sht["r_net"]  # positive funding: longs pay, shorts receive
    assert lab.Funding([10, 20, 30], [1.0, 2.0, 4.0]).between(10, 30) == pytest.approx(6.0)


def test_an_entry_beyond_the_stop_or_the_target_is_not_a_trade() -> None:
    assert lab.simulate(series([(100, 100, 100, 100), (97, 98, 96, 97)]), 0, "LONG", 98.0, lab.LIVE_EXIT, "BTC", NO_FUNDING) is None
    assert lab.simulate(series([(100, 100, 100, 100), (105, 106, 104, 105)]), 0, "LONG", 98.0, lab.LIVE_EXIT, "BTC", NO_FUNDING) is None
