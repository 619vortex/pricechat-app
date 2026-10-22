"""The research engine must be honest: indicators are causal (no look-ahead), higher timeframes only use CLOSED candles,
entries happen at the NEXT open, the stop wins when a candle touches both levels, and costs are charged.
TEST FIXTURES only: synthetic candles."""

import math

import pytest

from app.tradingos import backtest as bt
from app.tradingos import intraday as ix
from app.tradingos.market import Candle

M15 = 900_000


def wave(n: int, start: int = 0, step: int = M15, drift: float = 0.0) -> list[Candle]:
    out, px = [], 100.0
    for i in range(n):
        o = px
        px = 100 + drift * i + 3 * math.sin(i / 9) + 0.5 * math.sin(i / 2.3)
        out.append(Candle(start + i * step, o, max(o, px) + 0.2, min(o, px) - 0.2, px, 1000 + 300 * math.sin(i / 5) ** 2))
    return out


def test_indicator_series_are_causal() -> None:
    cs = wave(400)
    a = ix.series(cs, M15)
    changed = cs[:300] + [Candle(c.t, c.o * 1.5, c.h * 1.6, c.l * 1.4, c.c * 1.5, c.v * 9) for c in cs[300:]]  # rewrite the future
    b = ix.series(changed, M15)
    for name in ("e20", "e50", "e200", "rsi", "atr", "adx", "vwap", "relvol"):
        assert getattr(a, name)[:300] == getattr(b, name)[:300], name  # nothing before candle 300 moved


def test_higher_timeframe_uses_only_closed_candles() -> None:
    h1 = ix.series(wave(10, step=3_600_000), 3_600_000)
    assert ix.last_closed(h1, 3_600_000 * 3) == 2  # the candle opening at 3h hasn't closed yet at 3h
    assert ix.last_closed(h1, 3_600_000 * 3 - 1) == 1
    assert ix.last_closed(h1, 0) == -1


def test_entry_is_the_next_open_the_stop_wins_ties_and_costs_are_charged() -> None:
    cs = [Candle(i * M15, 100, 100.5, 99.5, 100, 1) for i in range(5)]
    cs[1] = Candle(M15, 100.2, 100.4, 99.9, 100.1, 1)  # next open = 100.2
    cs[2] = Candle(2 * M15, 100.1, 103.0, 98.0, 100, 1)  # touches BOTH the stop and the targets
    f = ix.series(cs, M15)
    r = bt.simulate(f, 0, "LONG", 99.0, 1.0, 2.0, 180, "BTC", [])
    assert r is not None and r["entry"] == pytest.approx(100.2 * (1 + bt.slip("BTC")))  # not the signal candle's close
    assert r["exit"] == "stop" and r["r_net"] < -1.0  # stop first; fees and slippage make it worse than -1R
    assert r["cost_r"] > 0


def test_funding_is_paid_by_longs_and_received_by_shorts() -> None:
    cs = [Candle(i * M15, 100, 100.3, 99.7, 100, 1) for i in range(30)]
    f = ix.series(cs, M15)
    fund = [(i * 3_600_000, 0.001) for i in range(1, 8)]  # a large positive funding rate every hour
    lng = bt.simulate(f, 0, "LONG", 98.0, 1.0, 2.0, 360, "BTC", fund)
    sht = bt.simulate(f, 0, "SHORT", 102.0, 1.0, 2.0, 360, "BTC", fund)
    assert lng and sht and lng["exit"] == "time" and lng["r_net"] < sht["r_net"]


def test_regime_needs_history_and_unstable_means_no_setups() -> None:
    h1 = ix.series(wave(100, step=3_600_000), 3_600_000)
    h4 = ix.series(wave(100, step=14_400_000), 14_400_000)
    assert ix.regime(h1, h4, 50, 20)[0] == "UNSTABLE"  # not enough history: never trade on it
    f = ix.series(wave(400), M15)
    found, why = ix.setups(f, 300, "UNSTABLE", "up")
    assert found == [] and "unstable" in why[0]


def test_the_live_strategy_never_decides_on_the_candle_still_forming() -> None:
    import time

    from app.tradingos import signals

    now = int(time.time() * 1000)
    hour = 3_600_000
    cs = [Candle(now - 3 * hour, 1, 1, 1, 1, 1), Candle(now - 2 * hour, 1, 1, 1, 1, 1), Candle(now - hour // 2, 1, 9, 1, 9, 1)]  # last: half an hour in
    assert [c.t for c in signals.closed(cs, "1h")] == [cs[0].t, cs[1].t]


def test_quality_is_bounded_and_says_nothing_about_probability() -> None:
    s = ix.Setup("A_pullback", "LONG", 100, 99, 1, 2, 180, quality={"htf": 1, "regime": 1, "relvol": 1, "ext": 1})
    assert ix.quality(s, 0, 0) == 100 and 0 <= ix.quality(s, 50, 5) <= 100
    assert "NOT a probability" in (ix.quality.__doc__ or "")
