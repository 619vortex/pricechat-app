"""G2 additions (2026-11-27): weekly losing / winning streaks and the distance-from-the-highs ranking. Made-up prices only."""

from __future__ import annotations

from datetime import date, timedelta

from app.growth.detectors import anomalies, insights
from app.growth.memory import Memory
from tests.test_growth_g2_anomalies import all_pass, walk

AS_OF = date(2026, 11, 25)  # a Wednesday: the last completed week closed on Sunday Nov 22


def weekly_run(points: list[tuple[date, float]], weeks: int, step: float, last_sunday: date = date(2026, 11, 22)) -> list[tuple[date, float]]:
    """Scales the closes so each of the last `weeks` Sunday closes is `step` from the previous Sunday's (days in between follow)."""
    out = dict(points)
    start = last_sunday - timedelta(days=7 * weeks)
    level = out[start]
    for w in range(1, weeks + 1):
        d0, d1 = start + timedelta(days=7 * (w - 1)), start + timedelta(days=7 * w)
        target = level * (1 + step)
        for k in range(1, 8):
            day = d0 + timedelta(days=k)
            out[day] = level + (target - level) * k / 7
        level = target
    for k in range(1, (AS_OF - last_sunday).days + 1):
        out[last_sunday + timedelta(days=k)] = level * (1 + 0.001 * k)
    return sorted(out.items())


def test_weekly_streak_longest_in_the_stored_history() -> None:
    px = weekly_run(walk(1000, end=AS_OF), 9, -0.03)
    fs = anomalies.weekly_streak(Memory.from_points({"px:BTC:close": px}), AS_OF, ("BTC",))
    assert len(fs) == 1
    f = fs[0]
    assert f.numbers["weeks"]["text"] == "9" and f.key == "weekly_streak:BTC:down:2026-11-22"
    assert "9 weeks in a row" in f.text(0) and "No losing run this long in Hyperliquid data, which begins in" in f.text(0)
    assert f.required_phrases() == ["Hyperliquid", f.numbers["since"]["text"]]
    assert f.chart["kind"] == "seasonality_bars" and len(f.chart["options"]["highlight"]) == 9
    all_pass(fs)


def test_weekly_streak_short_or_in_progress_week_is_not_a_post() -> None:
    assert anomalies.weekly_streak(Memory.from_points({"px:BTC:close": walk(1000, end=AS_OF)}), AS_OF, ("BTC",)) == []
    px = weekly_run(walk(1000, end=AS_OF), 3, -0.03)  # 3 weeks: below WEEK_MIN_STREAK
    assert anomalies.weekly_streak(Memory.from_points({"px:BTC:close": px}), AS_OF, ("BTC",)) == []


def _crash(coin_seed: int, depth: float) -> list[tuple[date, float]]:
    px = walk(800, start=100.0, seed=coin_seed, end=AS_OF)
    peak = len(px) - 300
    return [(d, v * (1 + depth * max(0, i - peak) / (len(px) - peak))) for i, (d, v) in enumerate(px)]


def test_drawdown_table_ranks_distance_from_the_highs() -> None:
    data = {f"px:{c}:close": _crash(k, -0.4 - 0.05 * k) for k, c in enumerate(("BTC", "ETH", "SOL", "XRP", "DOGE", "LINK", "AVAX"))}
    fs = insights.drawdown_table(Memory.from_points(data), AS_OF)
    assert len(fs) == 1
    f = fs[0]
    rows = f.chart["rows"]
    assert [r["value"] for r in rows] == sorted(r["value"] for r in rows)[::-1]  # shallowest first, deepest last
    assert rows[0]["label"] == "Bitcoin" and rows[0]["highlight"]
    assert f.numbers["count"]["text"] == "7"
    all_pass(fs)


def test_drawdown_table_needs_a_market_wide_drawdown() -> None:
    calm = {f"px:{c}:close": walk(800, seed=k, end=AS_OF) for k, c in enumerate(("BTC", "ETH", "SOL", "XRP", "DOGE", "LINK", "AVAX"))}
    assert insights.drawdown_table(Memory.from_points(calm), AS_OF) == []
    assert insights.drawdown_table(Memory.from_points({"px:BTC:close": _crash(0, -0.6)}), AS_OF) == []  # one coin isn't a table


def _year(start: float, end: float, first: date = date(2025, 12, 20)) -> list[tuple[date, float]]:
    days = (AS_OF - first).days
    return [(first + timedelta(days=i), start + (end - start) * i / days) for i in range(days + 1)]


def test_cross_asset_scoreboard_names_markets_from_last_years_close() -> None:
    data = {"px:BTC:close": _year(90000, 60000), "px:ETH:close": _year(3000, 1700), "px:XYZ-GOLD:close": _year(3000, 3600),
            "px:XYZ-NDX:close": _year(25000, 26000)}
    fs = insights.cross_asset(Memory.from_points(data), AS_OF)
    assert len(fs) == 1
    f = fs[0]
    assert f.text(0).startswith("Gold +") and "Bitcoin: −" in f.text(0)
    assert set(f.series) == {"px:BTC:close", "px:ETH:close", "px:XYZ-GOLD:close", "px:XYZ-NDX:close"}
    assert "trade.xyz" not in " ".join(f.text(i) for i in range(len(f.templates)))  # read as a link on X
    all_pass(fs)


def test_cross_asset_leaves_out_a_market_without_last_years_close() -> None:
    data = {"px:BTC:close": _year(90000, 60000), "px:ETH:close": _year(3000, 1700), "px:XYZ-GOLD:close": _year(3000, 3600),
            "px:XYZ-SPX:close": _year(6000, 6500, first=date(2026, 3, 18))}
    assert insights.cross_asset(Memory.from_points(data), AS_OF) == []  # only 3 markets measure the whole year: no scoreboard


def test_btc_in_gold_counts_ounces() -> None:
    data = {"px:BTC:close": _year(90000, 60000), "px:XYZ-GOLD:close": _year(3000, 3600)}
    f = insights.btc_in_gold(Memory.from_points(data), AS_OF)[0]
    assert f.numbers["oz_now"]["text"] == "16.7 oz" and f.numbers["ch"]["value"] < -15
    all_pass([f])


def test_funding_is_never_an_original() -> None:
    from app.growth import drafts

    assert {"funding_extremes", "funding_streak"} <= drafts.EXCLUDED
