"""Charts' price axis (community request, 2026-11-10): logarithmic when the prices shown span more than phi (1.618x), linear otherwise.
Not configurable."""

import inspect
import math

import pytest

from app import price_axis as pa


def test_phi_is_the_golden_ratio() -> None:
    assert abs(pa.PHI - 1.6180339887) < 1e-9


@pytest.mark.parametrize(("lo", "hi", "log"), [
    (100.0, 160.0, False),     # 1.6x: linear
    (100.0, 161.8, False),     # exactly phi-ish (below): linear
    (100.0, 162.0, True),      # just past phi: log
    (20_000.0, 110_000.0, True),  # Bitcoin over a cycle
    (73_000.0, 76_000.0, False),  # a normal day
    (0.0, 5.0, False),         # zero can't be on a log axis
    (-3.0, 5.0, False),
])
def test_log_only_past_one_factor_of_phi(lo: float, hi: float, log: bool) -> None:
    assert pa.needs_log(lo, hi) is log
    assert pa.axis(lo, hi).log is log


def test_a_log_axis_spaces_equal_percentages_equally() -> None:
    ax = pa.axis(100.0, 1000.0)
    assert ax.log
    assert abs(ax.frac(100.0)) < 1e-9 and abs(ax.frac(1000.0) - 1) < 1e-9
    assert abs(ax.frac(math.sqrt(100 * 1000)) - 0.5) < 1e-9  # the geometric middle sits in the middle
    t = ax.ticks(6)
    ratios = [t[i + 1] / t[i] for i in range(5)]
    assert max(ratios) - min(ratios) < 1e-9  # gridlines in equal steps of percent
    assert all(abs(ax.value(ax.frac(p)) - p) < 1e-6 * p for p in (150.0, 420.0, 999.0))


def test_a_linear_axis_is_unchanged_and_padding_works_both_ways() -> None:
    ax = pa.axis(100.0, 120.0, pad=0.05)
    assert not ax.log and abs(ax.lo - 99.0) < 1e-9 and abs(ax.hi - 121.0) < 1e-9
    assert abs(ax.frac(110.0) - 0.5) < 1e-9
    lg = pa.axis(100.0, 400.0, pad=0.05)
    assert lg.log and lg.lo < 100.0 and lg.hi > 400.0 and abs(lg.lo * lg.hi - 100.0 * 400.0) < 1e-6 * 40000  # symmetric in log space


def test_there_is_no_setting() -> None:
    assert list(inspect.signature(pa.axis).parameters) == ["lo", "hi", "pad"]
    assert list(inspect.signature(pa.needs_log).parameters) == ["lo", "hi"]
