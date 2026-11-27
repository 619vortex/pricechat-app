"""Growth G2 anomaly detectors (app.growth.detectors.anomalies) on SYNTHETIC series: streaks, volatility regime, volume spikes. Every
template of every finding passes the figures gate and the News Desk post checks; the guards (gaps, absurd moves, new listings, a recent
"since") hold; every chart renders. All values are made up."""

import math
from datetime import date, timedelta

import pytest

from app.growth import gate, graphics
from app.growth.detectors import anomalies
from app.growth.findings import Finding
from app.growth.memory import Memory
from app.newsdesk import checks

AS_OF = date(2026, 11, 24)
DAYS = 1100


def walk(days: int = DAYS, start: float = 100.0, seed: int = 1, amp: float = 0.02, end: date = AS_OF) -> list[tuple[date, float]]:
    """Deterministic wiggly closes ending on `end`: alternates often enough that runs stay short (at most 3-4 days)."""
    out, v = [], start
    for i in range(days):
        v *= 1 + amp * math.sin(i * 1.7 + seed) + 0.004 * math.sin(i * 0.31 + seed)
        out.append((end - timedelta(days=days - 1 - i), v))
    return out


def with_run(points: list[tuple[date, float]], days: int, step: float) -> list[tuple[date, float]]:
    """The last `days` closes each move by `step` (e.g. -0.02) from the one before."""
    head = points[:-days]
    v = head[-1][1]
    tail = []
    for d, _ in points[-days:]:
        v *= 1 + step
        tail.append((d, v))
    return head + tail


def volumes(days: int = DAYS, base: float = 1e8, end: date = AS_OF, last: float | None = None, seed: int = 0) -> list[tuple[date, float]]:
    out = [(end - timedelta(days=days - 1 - i), base * (1 + 0.3 * math.sin(i * 0.9 + seed))) for i in range(days)]
    if last is not None:
        out[-1] = (out[-1][0], last)
    return out


def all_pass(fs: list[Finding]) -> None:
    for f in fs:
        for i in range(len(f.templates)):
            t = f.text(i)
            assert gate.problems(t, f) == [], (f.key, i, t, gate.problems(t, f))
            src = " ".join(str(v["text"]) for v in f.numbers.values()) + " " + " ".join(f.words.values())
            res = checks.check_post(t, source_text=src, source_names=[], max_len=gate.MAX_LEN)
            assert all(c["passed"] for c in res.checks if c["check"] not in ("figures", "figure_units", "length")), (f.key, t, res.checks)
        assert f.calculation and f.chart["kind"] and f.chart["source"]
        assert Finding.from_dict(f.to_dict()).text() == f.text()
        for fmt in ("x", "portrait"):
            png = graphics.graphic_png(f, fmt)
            assert png[:8] == b"\x89PNG\r\n\x1a\n"


# --------------------------------------------------------------------------------------------------------------------- streak
def test_streak_longest_losing_run_in_the_stored_history() -> None:
    mem = Memory.from_points({"px:BTC:close": with_run(walk(), 9, -0.02)})
    fs = anomalies.streak(mem, AS_OF, ("BTC",))
    assert len(fs) == 1
    f = fs[0]
    assert f.detector == "streak" and f.format == "extremes" and f.subject == "BTC"
    assert f.numbers["streak"]["value"] >= 9
    assert "last" not in f.numbers  # no earlier run as long: "the longest ... since <start>"
    assert "longest" in f.text(1) and "Hyperliquid data since" in f.text(0)
    assert f.chart["kind"] == "seasonality_bars" and f.chart["options"]["highlight"]
    assert len(f.chart["series"][0]["points"]) <= anomalies.STREAK_BARS
    assert f.numbers["move"]["value"] < 0
    all_pass(fs)


def test_streak_winning_run_and_earlier_run_named() -> None:
    pts = walk()
    # an earlier 7-day winning run 400 days ago, then a 7-day run now
    k = len(pts) - 400
    v = pts[k - 1][1]
    for i in range(7):
        v *= 1.02
        pts[k + i] = (pts[k + i][0], v)
    scale = v / pts[k + 7][1]
    pts = pts[:k + 7] + [(d, x * scale) for d, x in pts[k + 7:]]
    mem = Memory.from_points({"px:ETH:close": with_run(pts, 7, 0.015)})
    fs = anomalies.streak(mem, AS_OF, ("ETH",))
    assert len(fs) == 1
    f = fs[0]
    assert f.words["kind"] == "winning" and "last" in f.numbers and f.numbers["move"]["value"] > 0
    all_pass(fs)


def test_streak_short_or_common_runs_are_not_posts() -> None:
    assert anomalies.streak(Memory.from_points({"px:BTC:close": walk()}), AS_OF, ("BTC",)) == []  # today's run is 1-2 days
    # a slow wave: 7-8 day runs every couple of weeks, so the run it ends in is ordinary
    slow, v = [], 100.0
    for i in range(DAYS):
        v *= 1 + 0.01 * math.sin(i * 2 * math.pi / 15 + 0.3)
        slow.append((AS_OF - timedelta(days=DAYS - 1 - i), v))
    assert anomalies.streak(Memory.from_points({"px:BTC:close": slow}), AS_OF, ("BTC",)) == []


def test_streak_guards_gap_and_absurd_move() -> None:
    pts = with_run(walk(), 9, -0.02)
    gappy = [p for p in pts if not (AS_OF - timedelta(days=20) < p[0] < AS_OF - timedelta(days=15))]
    assert anomalies.streak(Memory.from_points({"px:BTC:close": gappy}), AS_OF, ("BTC",)) == []
    crazy = pts[:-12] + [(pts[-12][0], pts[-12][1] * 3)] + pts[-11:]
    assert anomalies.streak(Memory.from_points({"px:BTC:close": crazy}), AS_OF, ("BTC",)) == []


def test_streak_needs_a_year_and_a_close_on_as_of() -> None:
    assert anomalies.streak(Memory.from_points({"px:BTC:close": with_run(walk(200), 9, -0.02)}), AS_OF, ("BTC",)) == []
    assert anomalies.streak(Memory.from_points({"px:BTC:close": with_run(walk(), 9, -0.02)}), AS_OF + timedelta(days=1), ("BTC",)) == []


# --------------------------------------------------------------------------------------------------------------------- volatility
def test_volatility_lowest_in_the_stored_history() -> None:
    pts = walk(amp=0.03)
    calm = pts[:-40]
    v = calm[-1][1]
    for i, (d, _) in enumerate(pts[-40:]):
        v *= 1 + 0.004 * (1 - i / 45) * math.sin(i * 1.3)  # calmer every day: today is the calmest
        calm.append((d, v))
    mem = Memory.from_points({"px:SOL:close": calm})
    fs = anomalies.volatility_regime(mem, AS_OF, ("SOL",))
    assert len(fs) == 1
    f = fs[0]
    assert f.detector == "volatility_regime" and "lowest" in f.text(0) and "since" not in f.numbers
    assert f.chart["kind"] == "rolling_line" and f.numbers["vol"]["value"] < 10
    all_pass(fs)


def test_volatility_highest_since_a_named_month() -> None:
    pts = walk(amp=0.01)
    # a violent stretch 500 days ago, then a slightly less violent one now: "highest since <then>"
    for lo, amp in ((len(pts) - 540, 0.09), (len(pts) - 30, 0.08)):
        v = pts[lo - 1][1]
        new = []
        for i in range(30):
            v *= 1 + amp * (1 if i % 2 else -1)
            new.append((pts[lo + i][0], v))
        scale = v / pts[lo + 29][1]
        pts = pts[:lo] + new + [(d, x * scale) for d, x in pts[lo + 30:]]
    mem = Memory.from_points({"px:BTC:close": pts})
    fs = anomalies.volatility_regime(mem, AS_OF, ("BTC",))
    assert len(fs) == 1
    f = fs[0]
    assert "highest" in f.text(0) and "since" in f.numbers and "then" in f.numbers
    all_pass(fs)


def test_volatility_middle_of_the_range_is_not_a_post() -> None:
    assert anomalies.volatility_regime(Memory.from_points({"px:BTC:close": walk()}), AS_OF, ("BTC",)) == []


# --------------------------------------------------------------------------------------------------------------------- volume
def _universe(spikes: dict[str, float], n: int = 12) -> Memory:
    data, coins = {}, ["BTC", "ETH", "SOL", "XRP", "DOGE", "LINK", "AVAX", "SUI", "AAVE", "UNI", "NEAR", "ADA"][:n]
    for k, c in enumerate(coins):
        base = 1e9 / (k + 1)
        data[f"px:{c}:close"] = walk(seed=k + 2)
        data[f"px:{c}:vol"] = volumes(base=base, seed=k, last=(base * spikes[c]) if c in spikes else None)
    return Memory.from_points(data)


def test_volume_spike_single_btc_and_table() -> None:
    mem = _universe({"BTC": 4.0, "SUI": 9.0, "AAVE": 5.0, "UNI": 3.0})
    fs = anomalies.volume_spike(mem, AS_OF)
    kinds = sorted(f.chart["kind"] for f in fs)
    assert kinds == ["rank_table", "rolling_line"]
    single = next(f for f in fs if f.subject == "BTC")
    assert single.numbers["ratio"]["text"].endswith("x") and single.numbers["ratio"]["value"] > 2.5
    table = next(f for f in fs if f.subject == "HL-volume")
    assert table.words["a1"] == "SUI" and table.numbers["count"]["value"] == 4
    all_pass(fs)


def test_volume_spike_table_needs_a_cluster() -> None:
    fs = anomalies.volume_spike(_universe({"SUI": 3.0, "AAVE": 2.6}), AS_OF)
    assert [f for f in fs if f.subject == "HL-volume"] == []


def test_volume_spike_guards_new_listing_zero_and_absurd() -> None:
    data = {"px:NEW:close": walk(40), "px:NEW:vol": volumes(40, base=5e9, last=5e10),
            "px:BTC:close": walk(), "px:BTC:vol": volumes(base=1e9, last=0.0),
            "px:ETH:close": walk(seed=3), "px:ETH:vol": volumes(base=1e9, last=1e9 * 500)}
    assert anomalies.volume_spike(Memory.from_points(data), AS_OF) == []


def test_volume_single_needs_a_long_since() -> None:
    vol = volumes(base=1e9, last=1e9 * 3.0)
    i = len(vol) - 20  # the same spike 19 days earlier: "the most since 3 weeks ago" is not news
    vol[i] = (vol[i][0], 1e9 * 3.5)
    mem = Memory.from_points({"px:BTC:close": walk(), "px:BTC:vol": vol})
    assert [f for f in anomalies.volume_spike(mem, AS_OF) if f.subject == "BTC"] == []


def test_digit_tickers_are_left_out_of_the_text() -> None:
    assert not anomalies._TICKER_OK.match("0G") and not anomalies._TICKER_OK.match("2Z") and anomalies._TICKER_OK.match("kPEPE")


def test_runs_break_on_missing_days() -> None:
    d0 = date(2026, 1, 1)
    daily = [(d0 + timedelta(days=i), -1.0) for i in range(3)] + [(d0 + timedelta(days=i), -1.0) for i in range(4, 8)]
    assert [r[2] for r in anomalies._runs(daily, True)] == [3, 4]


@pytest.mark.parametrize("fn", [anomalies.streak, anomalies.volatility_regime, anomalies.volume_spike])
def test_empty_memory(fn) -> None:  # type: ignore[no-untyped-def]
    assert fn(Memory.from_points({}), AS_OF) == []
