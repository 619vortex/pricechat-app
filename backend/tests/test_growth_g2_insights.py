"""Growth G2 insight detectors (app.growth.detectors.insights) on SYNTHETIC series: every template of every finding passes the figures gate and
the News Desk post checks, the chart draws, and the guards (too few episodes, gaps, bad prints, no extreme) return nothing. Values are made up."""

import math
from datetime import date, timedelta

import pytest

from app.growth import gate, graphics
from app.growth.detectors import insights
from app.growth.findings import FORMATS, Finding
from app.growth.memory import Memory
from app.newsdesk import checks

AS_OF = date(2026, 11, 25)


def walk(days: int, start: float = 100.0, seed: int = 1, drift: float = 0.0, end: date = AS_OF) -> list[tuple[date, float]]:
    out, v = [], start
    for i in range(days):
        d = end - timedelta(days=days - 1 - i)
        v *= 1 + drift + 0.02 * math.sin(i * 0.37 + seed) + 0.01 * math.sin(i * 1.91 + seed * 3)
        out.append((d, v))
    return out


def all_pass(fs: list[Finding]) -> None:
    assert fs
    for f in fs:
        assert f.format in FORMATS
        for i in range(len(f.templates)):
            t = f.text(i)
            assert gate.problems(t, f) == [], (f.key, i, t, gate.problems(t, f))
            res = checks.check_post(t, source_text=t, source_names=[], max_len=gate.MAX_LEN)
            assert all(c["passed"] for c in res.checks if c["check"] not in ("figures", "figure_units")), (f.key, i, res.checks)
            assert not gate.PREDICTION.search(t), t
        assert f.calculation and f.window and f.chart["source"]
        assert Finding.from_dict(f.to_dict()).text() == f.text()
        for fmt in ("x", "portrait"):
            png = graphics.graphic_png(f, fmt)
            assert png[:8] == b"\x89PNG\r\n\x1a\n"


# --- fear aftermath ----------------------------------------------------------------------------------------------------------------

def fear_series(lows: list[date], today: float = 12.0) -> list[tuple[date, float]]:
    """Neutral 50 everywhere, 10 on each low day (and the 3 days after it), today's reading at the end."""
    out = []
    d = date(2018, 2, 1)
    hot = {x + timedelta(days=k) for x in lows for k in range(4)}
    while d < AS_OF:
        out.append((d, 10.0 if d in hot else 50.0))
        d += timedelta(days=1)
    out.append((AS_OF, today))
    return out


LOWS = [date(2023, 9, 1), date(2024, 3, 1), date(2024, 9, 1), date(2025, 3, 1), date(2025, 9, 1), date(2026, 3, 1)]


def test_fear_aftermath_counts_earlier_episodes_and_their_forward_returns() -> None:
    mem = Memory.from_points({"px:BTC:close": walk(1300, 20000), "fng": fear_series(LOWS)})
    fs = insights.fear_aftermath(mem, AS_OF)
    assert len(fs) == 1
    f = fs[0]
    assert f.numbers["eps"]["text"] == "6"  # 4-day runs count once each; the current reading isn't one of them
    assert f.numbers["fng"]["text"] == "12"
    assert "Hyperliquid data since" in f.text(0)
    assert f.chart["kind"] == "rank_table" and len(f.chart["rows"]) == 6
    assert f.sources == ["hyperliquid", "alternative_me"]
    all_pass(fs)


def test_fear_aftermath_needs_five_episodes_and_extreme_fear() -> None:
    btc = walk(1300, 20000)
    assert insights.fear_aftermath(Memory.from_points({"px:BTC:close": btc, "fng": fear_series(LOWS[:4])}), AS_OF) == []
    assert insights.fear_aftermath(Memory.from_points({"px:BTC:close": btc, "fng": fear_series(LOWS, today=45)}), AS_OF) == []


def test_fear_aftermath_skips_episodes_without_90_days_after_and_refuses_bad_prints() -> None:
    recent = LOWS[:4] + [date(2026, 9, 20)]  # only 4 complete: the September one has no 90 days after it
    btc = walk(1300, 20000)
    assert insights.fear_aftermath(Memory.from_points({"px:BTC:close": btc, "fng": fear_series(recent)}), AS_OF) == []
    bad = [(d, v * (5 if d == date(2024, 3, 20) else 1)) for d, v in btc]
    assert insights.fear_aftermath(Memory.from_points({"px:BTC:close": bad, "fng": fear_series(LOWS)}), AS_OF) == []


# --- year paths ----------------------------------------------------------------------------------------------------------------

def test_year_paths_flags_the_worst_start_of_the_stored_years() -> None:
    pts = walk(1400, 30000, drift=0.002)
    # this year: a steady 40% fall from Dec 31
    base = dict(pts)[date(2025, 12, 31)]
    days = (AS_OF - date(2025, 12, 31)).days
    pts = [(d, v) for d, v in pts if d <= date(2025, 12, 31)] + [(date(2025, 12, 31) + timedelta(days=k), base * 0.6 ** (k / days))
                                                                for k in range(1, days + 1)]
    mem = Memory.from_points({"px:BTC:close": pts})
    fs = insights.year_paths(mem, AS_OF, ("BTC",))
    assert len(fs) == 1
    f = fs[0]
    assert "worst" in f.key and f.numbers["now"]["text"] == "−40.0%"
    assert f.numbers["years"]["text"] == "3"  # 2023 lacks a Dec 31 base: 2024, 2025, 2026
    assert f.chart["kind"] == "drawdown_overlay" and f.chart["options"]["x_dates"]
    all_pass(fs)


def test_year_paths_says_nothing_in_an_ordinary_year_or_with_a_gap() -> None:
    pts = walk(1400, 30000, drift=0.002)
    assert all("worst" not in f.key for f in insights.year_paths(Memory.from_points({"px:BTC:close": pts}), AS_OF, ("BTC",)))
    gappy = [(d, v) for d, v in pts if not (date(2026, 5, 1) <= d <= date(2026, 5, 10))]
    assert insights.year_paths(Memory.from_points({"px:BTC:close": gappy}), AS_OF, ("BTC",)) == []


# --- gold ratio ----------------------------------------------------------------------------------------------------------------

def test_gold_ratio_lowest_since_a_date_with_paxg_named_honestly() -> None:
    btc = walk(700, 80000, seed=2)
    gold = walk(700, 3000, seed=5, drift=0.0)
    btc[-1] = (btc[-1][0], min(v / g for (_, v), (_, g) in zip(btc[:-1], gold[:-1])) * gold[-1][1] * 0.97)
    mem = Memory.from_points({"px:BTC:close": btc, "px:PAXG:close": gold})
    fs = insights.gold_ratio(mem, AS_OF)
    lows = [f for f in fs if "lowest" in f.key]
    assert len(lows) == 1
    f = lows[0]
    assert "PAXG (a gold-backed token, one troy ounce each)" in f.text(0)
    assert f.chart["kind"] == "ratio_line" and f.chart["y_format"] == "oz"
    all_pass(lows)


def test_gold_ratio_needs_a_180_day_extreme_and_clean_prints() -> None:
    btc, gold = walk(700, 80000, seed=2), walk(700, 3000, seed=5)
    mem = Memory.from_points({"px:BTC:close": btc, "px:PAXG:close": gold})
    assert [f for f in insights.gold_ratio(mem, AS_OF) if (AS_OF - date.fromisoformat(f.window[6:16])).days < 180] == []
    btc2 = list(btc)
    btc2[-1] = (btc2[-1][0], 1.0)
    gold2 = [(d, v * (3 if d == date(2026, 6, 1) else 1)) for d, v in gold]
    assert insights.gold_ratio(Memory.from_points({"px:BTC:close": btc2, "px:PAXG:close": gold2}), AS_OF) == []


@pytest.mark.parametrize("fn", [insights.fear_aftermath, insights.year_paths, insights.gold_ratio])
def test_no_data_no_finding(fn) -> None:
    assert fn(Memory.from_points({}), AS_OF) == []
