"""Phase 2 signal engine. TEST DATA only: made-up funding series and books (no network)."""

import math
from datetime import datetime, timezone

import pytest

from fundarb.models import FundingRate, OrderBook
from fundarb.signals import Leg, SignalConfig, best_pairs, evaluate, hourly_series

T0 = datetime(2026, 10, 21, tzinfo=timezone.utc)
H0 = 1_792_540_800  # a whole hour, unix seconds


def leg(venue: str, rate: float, interval: float = 1.0, history: list[float] | None = None, taker: float | None = 4.5, book: bool = True,
        cap: tuple[float, float] | None = None) -> Leg:
    """`history`: hourly-normalized rates for the last len(history) hours (expanded per the venue's interval)."""
    hist = history if history is not None else [rate / interval] * 168
    span = max(1, int(interval))
    settlements = [(H0 + (i + 1) * 3600 * span, r * interval) for i, r in enumerate(hist[::span])]
    b = OrderBook.build(venue, "X-PERP", [(99.9 - i * 0.05, 50) for i in range(40)], [(100.1 + i * 0.05, 50) for i in range(40)], T0) if book else None
    return Leg(venue, FundingRate(rate, interval, "test", "predicted", T0), hourly_series(settlements, interval), taker_bps=taker, book=b, cap=cap)


def test_an_8h_rate_is_spread_over_its_8_hours_so_venues_line_up() -> None:
    s = hourly_series([(H0 + 8 * 3600, 0.0008)], 8)
    assert len(s) == 8 and all(v == pytest.approx(0.0001) for v in s.values())


def test_short_the_higher_hourly_rate_even_when_funding_is_negative() -> None:
    o = evaluate("X-PERP", leg("a", -0.0002), leg("b", -0.0010, interval=8))  # a: -0.0002/h; b: -0.000125/h (an 8 h venue)
    assert (o.short, o.long) == ("b", "a")  # long a, where shorts pay longs the most; short b, where shorts pay the least
    assert o.gross_apr == pytest.approx(0.000075 * 8760 * 100)  # 65.7% APR collected in a negative-funding regime


def test_capital_adjusted_apr_counts_both_legs_margin_and_buffer() -> None:
    cfg = SignalConfig(leverage=2.0, buffer=0.25)
    o = evaluate("X-PERP", leg("a", 0.00002), leg("b", 0.00007), cfg)
    assert cfg.capital_per_notional == pytest.approx(1.25)  # 2 legs x 1/2 x 1.25
    assert o.gross_apr == pytest.approx(0.00005 * 8760 * 100) and o.capital_apr == pytest.approx(o.gross_apr / 1.25)
    assert evaluate("X-PERP", leg("a", 0.00002), leg("b", 0.00007), SignalConfig(leverage=1.0, buffer=0.0)).capital_apr == pytest.approx(o.gross_apr / 2)


def test_z_score_and_paid_share_come_from_the_spreads_own_history() -> None:
    calm = [0.00003 + (0.000005 if i % 2 else -0.000005) for i in range(168)]
    o = evaluate("X-PERP", leg("long", 0.0, history=[0.0] * 168), leg("short", 0.00006, history=calm))
    assert o.mean_apr_7d == pytest.approx(0.00003 * 8760 * 100) and o.std_apr_7d == pytest.approx(0.000005 * 8760 * 100)
    assert o.z == pytest.approx(6.0) and o.paid_share == 1.0 and o.persistent
    assert any("extreme reading (+6.0σ)" in f for f in o.flags)


def test_a_steady_spread_outranks_a_fat_noisy_one() -> None:
    steady = [0.000017 + (0.0000017 if i % 2 else -0.0000017) for i in range(168)]  # ~15% ± 1.5% APR
    noisy = [0.0000285 + (0.0000091 if i % 2 else -0.0000091) for i in range(168)]  # ~25% ± 8% APR
    a = best_pairs("STEADY-PERP", [leg("l", 0.0, history=[0.0] * 168), leg("s", 0.000017, history=steady)])[0]
    b = best_pairs("NOISY-PERP", [leg("l", 0.0, history=[0.0] * 168), leg("s", 0.0000285, history=noisy)])[0]
    assert b.gross_apr > a.gross_apr and a.score > b.score  # ranked on mean - 2σ, capital-adjusted


def test_costs_breakeven_and_net_need_real_fees_and_depth() -> None:
    o = evaluate("X-PERP", leg("a", 0.0), leg("b", 0.0001))
    assert o.costs_bps is not None and o.costs_bps > 2 * 2 * 4.5  # fees on both legs, in and out, plus slippage
    assert o.breakeven_hours == pytest.approx(o.costs_bps / 1e4 / 0.0001)
    assert o.net_apr < o.gross_apr and o.net_capital_apr == pytest.approx(o.net_apr / SignalConfig().capital_per_notional)
    unknown = evaluate("X-PERP", leg("a", 0.0, taker=None), leg("b", 0.0001))
    assert unknown.net_apr is None and any("fees unknown on a" in f for f in unknown.flags)  # never a silent zero
    slow = evaluate("X-PERP", leg("a", 0.0), leg("b", 0.000001))
    assert slow.rejected and "break-even" in slow.rejected and slow.breakeven_hours > 72


def test_zero_fee_legs_are_flagged_and_stressed_and_caps_are_a_warning() -> None:
    o = evaluate("X-PERP", leg("lighter", 0.0, taker=0.0), leg("b", 0.0001, cap=(-0.0001, 0.0001)))
    assert any("lighter fees are zero today" in f for f in o.flags) and o.post_subsidy_net_apr < o.net_apr
    assert any("b funding at its cap" in f for f in o.flags)


def test_short_history_is_said_and_no_spread_is_rejected() -> None:
    o = evaluate("X-PERP", leg("a", 0.0, history=[0.0] * 10), leg("b", 0.0001, history=[0.0001] * 10))
    assert o.mean_apr_7d is None and o.score is None and any("only 10 hours" in f for f in o.flags)
    assert evaluate("X-PERP", leg("a", 0.0001), leg("b", 0.0001)).rejected == "no spread"
    assert math.isfinite(evaluate("X-PERP", leg("a", 0.0), leg("b", 0.0001)).breakeven_hours)
