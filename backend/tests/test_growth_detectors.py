"""Growth Engine G1 detectors on SYNTHETIC series (no network, no database): each format, the licence rules (uncleared Coinbase / liquidation
series are never used) and the history bound (no "record in N years" claim beyond the stored history). All values are made up."""

import math
from datetime import date, timedelta

import pytest

from app.growth import detectors, gate, writer
from app.growth.detectors import breadth, common, crowded, extremes, history, relative
from app.growth.findings import FORMATS, Finding
from app.growth.memory import Memory, Series

AS_OF = date(2026, 11, 24)


def walk(days: int, start: float = 100.0, seed: int = 1, drift: float = 0.0005, end: date = AS_OF) -> list[tuple[date, float]]:
    """A deterministic wiggly series ending on `end`."""
    out, v = [], start
    for i in range(days):
        d = end - timedelta(days=days - 1 - i)
        v *= 1 + drift + 0.02 * math.sin(i * 0.37 + seed) + 0.01 * math.sin(i * 1.91 + seed * 3)
        out.append((d, v))
    return out


def crash_tail(points: list[tuple[date, float]], days: int, total: float) -> list[tuple[date, float]]:
    """Make the last `days` points fall smoothly by `total` (e.g. -0.4) from the value `days` before the end."""
    base = points[-days - 1][1]
    head = points[:-days]
    step = (1 + total) ** (1 / days)
    return head + [(points[len(points) - days + i][0], base * step ** (i + 1)) for i in range(days)]


def all_pass(fs: list[Finding]) -> None:
    for f in fs:
        assert f.format in FORMATS
        for i in range(len(f.templates)):
            assert gate.problems(f.text(i), f) == [], (f.key, i, f.text(i))
        assert f.calculation and f.chart["kind"] and f.chart["series"] and f.chart["source"]
        d = f.to_dict()
        assert Finding.from_dict(d).text() == f.text()


def test_rarity_move_finds_an_extreme_30d_fall_and_says_its_window() -> None:
    pts = crash_tail(walk(1000), 30, -0.40)
    mem = Memory.from_points({"px:BTC:close": pts})
    fs = extremes.rarity_move(mem, AS_OF, ("BTC",))
    assert len(fs) == 1
    f = fs[0]
    assert f.window == "30d" and f.subject == "BTC" and f.sources == ["hyperliquid"]
    assert f.numbers["move"]["text"] == "−40.0%"
    assert f.numbers["move"]["value"] == pytest.approx(-40.0, abs=0.01)
    assert "Hyperliquid data since" in f.text(0) and "Hyperliquid data since" in f.text(1)
    assert f.chart["kind"] == "histogram_marker" and f.rarity > 0.9
    assert "−40.0%" in f.calculation
    all_pass(fs)


def test_rarity_needs_a_year_of_windows() -> None:
    pts = crash_tail(walk(300), 30, -0.40)
    assert extremes.rarity_move(Memory.from_points({"px:BTC:close": pts}), AS_OF, ("BTC",)) == []


def test_ordinary_move_is_not_reported() -> None:
    mem = Memory.from_points({"px:BTC:close": walk(1000)})
    assert extremes.rarity_move(mem, AS_OF, ("BTC",)) == []


def test_record_low_with_short_history_claims_only_the_stored_window() -> None:
    """Three years stored, today the lowest close of all: the claim is 'since <first stored month>', never '10 years' (or any span longer
    than what is stored)."""
    pts = walk(3 * 365 + 10, drift=0.0)
    lowest = min(v for _, v in pts)
    pts[-1] = (pts[-1][0], lowest * 0.9)
    mem = Memory.from_points({"px:BTC:close": pts})
    fs = [f for f in extremes.record_level(mem, AS_OF, ("BTC",)) if "lowest" in f.key]
    assert len(fs) == 1
    f = fs[0]
    first = pts[0][0]
    assert f.numbers["start"]["text"] == f"{['January','February','March','April','May','June','July','August','September','October','November','December'][first.month - 1]} {first.year}"
    for i in range(len(f.templates)):
        t = f.text(i)
        assert "Hyperliquid data since" in t and "10 years" not in t and "years" not in t
    # a "10 years" claim is refused by the gate: 10 isn't a figure of this finding
    assert gate.problems("Bitcoin's lowest close in 10 years.", f)
    all_pass(fs)


def test_record_needs_400_days() -> None:
    pts = walk(300, drift=-0.001)
    assert extremes.record_level(Memory.from_points({"px:BTC:close": pts}), AS_OF, ("BTC",)) == []


def test_uncleared_coinbase_history_is_never_used() -> None:
    """Coinbase isn't cleared (licence research 2026-11-25): a long cb: series alone produces nothing, and next to a Hyperliquid series the
    Hyperliquid one is used even though it is shorter."""
    long_cb = crash_tail(walk(3500), 30, -0.40)
    mem = Memory.from_points({"cb:BTC-USD:close": long_cb})
    assert common.price(mem, "BTC") is None
    assert extremes.rarity_move(mem, AS_OF, ("BTC",)) == [] and history.drawdown_cycle(mem, AS_OF) == []
    mem2 = Memory.from_points({"cb:BTC-USD:close": long_cb, "px:BTC:close": crash_tail(walk(900), 30, -0.40)})
    assert common.price(mem2, "BTC").key == "px:BTC:close"  # type: ignore[union-attr]
    fs = extremes.rarity_move(mem2, AS_OF, ("BTC",))
    assert fs and fs[0].sources == ["hyperliquid"] and fs[0].series == ["px:BTC:close"]


def test_liquidation_series_are_never_read() -> None:
    liq = {f"liq:BTC:{side}": [(AS_OF - timedelta(days=i), 5e7 if side == "long" else 1e6) for i in range(30)] for side in ("long", "short")}
    found, errors = detectors.run_all(Memory.from_points(liq | {"px:BTC:close": walk(50)}), AS_OF)
    assert errors == [] and not any("liq" in s for f in found for s in f.series)
    assert not any("liq" in name for name in detectors.DETECTORS)


def test_sentiment_rarity() -> None:
    vals = [(date(2018, 2, 1) + timedelta(days=i), 50 + 30 * math.sin(i / 40)) for i in range(3000)]
    last = vals[-1][0]
    vals += [(last + timedelta(days=i), 9.0) for i in range(1, 15)]
    mem = Memory.from_points({"fng": vals})
    fs = extremes.sentiment_rarity(mem, vals[-1][0])
    assert len(fs) == 1
    f = fs[0]
    assert f.sources == ["alternative_me"] and f.attribution == ["Fear & Greed Index: alternative.me"]
    assert f.numbers["value"]["text"] == "9" and f.numbers["streak"]["value"] == 14
    all_pass(fs)


def test_drawdown_cycle_from_a_long_cleared_history() -> None:
    """Two prior cycles (record -> -70% -> new record) and a current -45% drawdown."""
    start = AS_OF - timedelta(days=8 * 365)
    pts, v = [], 100.0
    plan = [(400, 3.0), (365, 0.3), (500, 4.0), (380, 0.25), (600, 5.0), (300, 0.55)]
    d = start
    for days, mult in plan:
        step = mult ** (1 / days)
        for _ in range(days):
            v *= step
            pts.append((d, v))
            d += timedelta(days=1)
    pts = [(AS_OF - timedelta(days=len(pts) - 1 - i), x) for i, (_, x) in enumerate(pts)]
    mem = Memory.from_points({"px:BTC:close": pts})
    fs = history.drawdown_cycle(mem, AS_OF, ("BTC",))
    assert len(fs) == 1
    f = fs[0]
    assert f.numbers["c1_days"]["value"] == 365 and f.numbers["c2_days"]["value"] == 380  # top = last day of the rise, bottom = last day of the fall
    assert f.numbers["day"]["value"] == 300 and f.numbers["dd"]["value"] == pytest.approx(-45.0, abs=0.3)
    assert f.chart["kind"] == "drawdown_overlay" and len(f.chart["series"]) == 3
    all_pass(fs)


def test_seasonality_needs_three_prior_years_and_an_extreme_month() -> None:
    short = Memory.from_points({"px:BTC:close": crash_tail(walk(2 * 365 + 30), 24, -0.3)})
    assert history.seasonality(short, AS_OF, ("BTC",)) == []
    mem = Memory.from_points({"px:BTC:close": crash_tail(walk(5 * 365), 24, -0.45)})
    fs = history.seasonality(mem, AS_OF, ("BTC",))
    assert len(fs) == 1 and fs[0].numbers["left"]["value"] == 6 and "worst November" in fs[0].text()
    all_pass(fs)


# fixture coins are real well-known tickers (rankings name only common.WELL_KNOWN since 11-27); prices are made up
UNI_NAMES = [c for c in __import__("app.growth.detectors.common", fromlist=["WELL_KNOWN"]).WELL_KNOWN if c != "BTC"]


def coin_name(k: int) -> str:
    return UNI_NAMES[k] if k < len(UNI_NAMES) else f"C{k}"


def _universe(n: int, days: int, crash: float | None = None) -> dict[str, list[tuple[date, float]]]:
    data = {}
    for k in range(n):
        pts = walk(days, seed=k + 2, drift=0.0003 * ((k % 5) - 2))
        if crash is not None:
            pts = crash_tail(pts, 30, crash - 0.002 * k)
        data[f"px:{coin_name(k)}:close"] = pts
        data[f"px:{coin_name(k)}:vol"] = [(d, 1e6 * (k + 1)) for d, _ in pts]
    return data


def test_breadth_count_everything_down() -> None:
    mem = Memory.from_points(_universe(30, 400, crash=-0.3))
    fs = breadth.breadth_count(mem, AS_OF)
    assert len(fs) == 1
    f = fs[0]
    assert f.numbers["up"]["value"] == 0 and f.numbers["total"]["value"] == 30 and f.chart["kind"] == "heat_grid"
    all_pass(fs)


def test_breadth_ma_from_stored_breadth_series() -> None:
    from app.growth.collectors import compute_breadth

    data = _universe(30, 500, crash=-0.35)
    b = compute_breadth({k: v for k, v in data.items() if k.endswith(":close")})
    assert b["breadth:above200"][-1][1] == 0.0 and b["breadth:n200"][-1][1] == 30.0
    # 0% for weeks already: "lowest since <yesterday>" is no news, so no claim
    assert breadth.breadth_ma(Memory.from_points(b), AS_OF) == []
    days = [AS_OF - timedelta(days=i) for i in range(400)][::-1]
    shaped = {"breadth:above200": [(d, 50 + 20 * math.sin(i / 25)) for i, d in enumerate(days[:-1])] + [(AS_OF, 4.0)],
              "breadth:n200": [(d, 30.0) for d in days]}
    fs = breadth.breadth_ma(Memory.from_points(shaped), AS_OF)
    assert len(fs) == 1 and fs[0].numbers["count"]["value"] == 1 and fs[0].sources == ["derived_hl"]
    assert "since our breadth record starts" in fs[0].text()
    all_pass(fs)


def test_funding_extremes_annualised() -> None:
    data = {f"fund:C{k}": [(AS_OF - timedelta(days=i), 0.00001) for i in range(90)] for k in range(12)}
    data["fund:ADA"] = [(AS_OF - timedelta(days=i), 0.00001 if i >= 3 else -0.0001) for i in range(90)]  # -0.01%/h -> -87.6% a year
    data["fund:XLM"] = [(AS_OF - timedelta(days=i), 0.00001 if i >= 3 else -0.00008) for i in range(90)]
    fs = crowded.funding_extremes(Memory.from_points(data), AS_OF)
    assert len(fs) == 1
    f = fs[0]
    assert f.numbers["list"]["text"] == "ADA −88% · XLM −70%"
    assert "history of" in f.text(0) and "don't always" in f.text(0)
    all_pass(fs)


def test_funding_extremes_skip_one_day_spikes_and_implausible_rates() -> None:
    """11-25 production: BIGTIME's one-day -0.35%/h average annualised to "-3,091%" and topped the queue. A spike (one day) or a rate beyond
    crowded.MAX_PLAUSIBLE is never listed."""
    data = {f"fund:C{k}": [(AS_OF - timedelta(days=i), 0.00001) for i in range(90)] for k in range(12)}
    data["fund:ADA"] = [(AS_OF - timedelta(days=i), 0.00001 if i >= 3 else -0.0001) for i in range(90)]
    data["fund:XLM"] = [(AS_OF - timedelta(days=i), 0.00001 if i >= 3 else -0.00008) for i in range(90)]
    data["fund:BIG"] = [(AS_OF - timedelta(days=i), 0.00001 if i >= 3 else -0.0035) for i in range(90)]  # -3,066% a year: implausible
    data["fund:ONE"] = [(AS_OF - timedelta(days=i), 0.00001 if i else -0.0002) for i in range(90)]  # one day only: a spike
    f = crowded.funding_extremes(Memory.from_points(data), AS_OF)[0]
    assert f.numbers["list"]["text"] == "ADA −88% · XLM −70%"


def test_funding_streak_longest_in_data() -> None:
    vals = [(AS_OF - timedelta(days=i), (-0.00001 if i < 9 else (0.00001 if i % 7 else -0.00001))) for i in range(200)]
    fs = crowded.funding_streak(Memory.from_points({"fund:BTC": vals}), AS_OF, ("BTC",))
    assert len(fs) == 1 and fs[0].numbers["streak"]["value"] == 9 and "longest" in fs[0].text()
    all_pass(fs)


def test_leaderboard_ranks_most_traded() -> None:
    data = _universe(25, 400)
    data["px:BTC:close"] = walk(400, seed=99)
    data["px:BTC:vol"] = [(d, 1e9) for d, _ in data["px:BTC:close"]]
    data["px:BNB:close"] = crash_tail(data["px:BNB:close"], 7, 0.8)
    fs = [f for f in relative.leaderboard(Memory.from_points(data), AS_OF) if f.window == "30d"]
    assert len(fs) == 1
    assert fs[0].numbers["best"]["text"].startswith("BNB +") and "major coins" in fs[0].text(0)
    assert fs[0].chart["kind"] == "rank_table"
    all_pass(fs)


def test_ratio_record_eth_btc_lowest() -> None:
    btc = walk(800, seed=5)
    eth = [(d, v * 0.05 * (1 - 0.0004 * i)) for i, (d, v) in enumerate(btc)]
    eth[-1] = (eth[-1][0], eth[-1][1] * 0.9)
    fs = [f for f in relative.ratio_record(Memory.from_points({"px:BTC:close": btc, "px:ETH:close": eth}), AS_OF, (("ETH", "BTC"),))
          if "lowest" in f.key]
    assert len(fs) == 1 and "Hyperliquid data since" in fs[0].text()
    all_pass(fs)


def test_divergence_and_beta_run_clean() -> None:
    data = _universe(25, 600)
    btc = crash_tail(walk(600, seed=50), 30, -0.25)
    data["px:BTC:close"] = btc
    data["px:BTC:vol"] = [(d, 1e9) for d, _ in btc]
    data["px:SOL:close"] = crash_tail(data["px:SOL:close"], 30, 0.3)
    fs = relative.divergence(Memory.from_points(data), AS_OF)
    assert len(fs) == 1 and fs[0].numbers["coin"]["value"] > 0 > fs[0].numbers["btc"]["value"] and fs[0].numbers["gap"]["value"] >= 25
    all_pass(fs)
    eth = [(d, v * (1 + 0.5 * math.sin(i / 3) * 0.01) ** (1 if i < 500 else 3)) for i, (d, v) in enumerate(btc)]
    all_pass(relative.beta(Memory.from_points({"px:BTC:close": btc, "px:ETH:close": eth}), AS_OF, ("ETH",)))


def test_run_all_isolates_a_broken_detector(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_: object) -> list[Finding]:
        raise RuntimeError("synthetic")

    monkeypatch.setitem(detectors.DETECTORS, "rarity", ("extremes", boom))
    found, errors = detectors.run_all(Memory.from_points({"px:BTC:close": crash_tail(walk(1000), 30, -0.4)}), AS_OF)
    assert any(e.startswith("rarity: RuntimeError") for e in errors)
    assert all(f.detector != "rarity" for f in found)


def test_series_window_returns_skip_gaps() -> None:
    s = Series.of("x", [(date(2026, 1, 1), 1.0), (date(2026, 1, 2), 2.0), (date(2026, 1, 5), 4.0)])
    assert [w[0] for w in s.window_returns(1)] == [date(2026, 1, 2)]
    assert writer.placeholder_map  # imported for the writer tests' fixtures
