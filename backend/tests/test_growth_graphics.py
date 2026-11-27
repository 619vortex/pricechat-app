"""The adapter between the G1 detectors' chart dicts and the branded renderer (app.growth.graphics): every detector's chart -> ChartSpec ->
PNG in both formats, every "Other chart" variant too; the words and the marked figures come from the Finding's numbers (no recomputation),
the source line carries the attribution, and a chart that disagrees with its Finding is refused. SYNTHETIC data."""

import math
from datetime import timedelta
from io import BytesIO

import pytest
from PIL import Image

from app.growth import graphics
from app.growth.chart_spec import SpecError
from app.growth.detectors import DETECTORS, breadth, crowded, extremes, history, relative
from app.growth.findings import Finding
from app.growth.memory import Memory
from tests.test_growth_detectors import AS_OF, _universe, crash_tail, walk


def _cycle_history() -> list:
    pts, v, d = [], 100.0, AS_OF - timedelta(days=8 * 365)
    for days, mult in [(400, 3.0), (365, 0.3), (500, 4.0), (380, 0.25), (600, 5.0), (300, 0.55)]:
        step = mult ** (1 / days)
        for _ in range(days):
            v *= step
            pts.append((d, v))
            d += timedelta(days=1)
    return [(AS_OF - timedelta(days=len(pts) - 1 - i), x) for i, (_, x) in enumerate(pts)]


def _findings() -> dict[str, Finding]:
    out: dict[str, list[Finding]] = {}
    crash = crash_tail(walk(1000), 30, -0.40)
    out["rarity"] = extremes.rarity_move(Memory.from_points({"px:BTC:close": crash}), AS_OF, ("BTC",))
    low = walk(3 * 365 + 10, drift=0.0)
    low[-1] = (low[-1][0], min(v for _, v in low) * 0.9)
    out["record"] = [f for f in extremes.record_level(Memory.from_points({"px:BTC:close": low}), AS_OF, ("BTC",)) if "lowest" in f.key]
    fng = [(AS_OF - timedelta(days=3013 - i), 50 + 30 * math.sin(i / 40)) for i in range(3000)] + [(AS_OF - timedelta(days=13 - i), 9.0) for i in range(14)]
    out["sentiment"] = extremes.sentiment_rarity(Memory.from_points({"fng": fng}), AS_OF)
    out["drawdown_cycle"] = history.drawdown_cycle(Memory.from_points({"px:BTC:close": _cycle_history()}), AS_OF, ("BTC",))
    out["seasonality"] = history.seasonality(Memory.from_points({"px:BTC:close": crash_tail(walk(5 * 365), 24, -0.45)}), AS_OF, ("BTC",))
    bt = crash_tail(walk(800, seed=3), 365, -0.35)
    fg = [(d, 50 + 30 * math.sin(i / 30)) for i, (d, _) in enumerate(bt)]
    fg[-366], fg[-1] = (fg[-366][0], 70.0), (fg[-1][0], 12.0)
    out["then_now"] = history.then_now(Memory.from_points({"px:BTC:close": bt, "fng": fg}), AS_OF)
    out["breadth_count"] = breadth.breadth_count(Memory.from_points(_universe(30, 400, crash=-0.3)), AS_OF)
    days = [AS_OF - timedelta(days=i) for i in range(400)][::-1]
    shaped = {"breadth:above200": [(d, 50 + 20 * math.sin(i / 25)) for i, d in enumerate(days[:-1])] + [(AS_OF, 4.0)],
              "breadth:n200": [(d, 30.0) for d in days]}
    out["breadth_ma"] = breadth.breadth_ma(Memory.from_points(shaped), AS_OF)
    fund = {f"fund:C{k}": [(AS_OF - timedelta(days=i), 0.00001) for i in range(90)] for k in range(12)}
    fund["fund:ADA"] = [(AS_OF - timedelta(days=i), 0.00001 if i >= 3 else -0.0001) for i in range(90)]
    fund["fund:XLM"] = [(AS_OF - timedelta(days=i), 0.00001 if i >= 3 else -0.00008) for i in range(90)]
    out["funding_extremes"] = crowded.funding_extremes(Memory.from_points(fund), AS_OF)
    streak = [(AS_OF - timedelta(days=i), (-0.00001 if i < 9 else (0.00001 if i % 7 else -0.00001))) for i in range(200)]
    out["funding_streak"] = crowded.funding_streak(Memory.from_points({"fund:BTC": streak}), AS_OF, ("BTC",))
    px = crash_tail(walk(400, seed=8), 30, -0.08)
    oi = [(d, 5e8 * (1 + 0.6 * max(0, i - 369) / 30)) for i, (d, _) in enumerate(px)]
    out["oi_buildup"] = crowded.oi_buildup(Memory.from_points({"px:SOL:close": px, "oi:SOL": oi}), AS_OF)
    lead = _universe(25, 400)
    lead["px:BTC:close"] = walk(400, seed=99)
    lead["px:BTC:vol"] = [(d, 1e9) for d, _ in lead["px:BTC:close"]]
    lead["px:C3:close"] = crash_tail(lead["px:C3:close"], 7, 0.8)
    out["leaderboard"] = [f for f in relative.leaderboard(Memory.from_points(lead), AS_OF) if f.window == "7d"]
    btc = walk(800, seed=5)
    eth = [(d, v * 0.05 * (1 - 0.0004 * i)) for i, (d, v) in enumerate(btc)]
    eth[-1] = (eth[-1][0], eth[-1][1] * 0.9)
    out["ratio_record"] = [f for f in relative.ratio_record(Memory.from_points({"px:BTC:close": btc, "px:ETH:close": eth}), AS_OF, (("ETH", "BTC"),))
                           if "lowest" in f.key]
    data = _universe(25, 600)
    b2 = crash_tail(walk(600, seed=50), 30, -0.25)
    data["px:BTC:close"], data["px:BTC:vol"] = b2, [(d, 1e9) for d, _ in b2]
    data["px:C1:close"] = crash_tail(data["px:C1:close"], 30, 0.3)
    out["divergence"] = relative.divergence(Memory.from_points(data), AS_OF)
    e2 = [(d, v * (1 + 0.5 * math.sin(i / 3) * 0.01) ** (1 if i < 500 else 3)) for i, (d, v) in enumerate(b2)]
    out["beta"] = relative.beta(Memory.from_points({"px:BTC:close": b2, "px:ETH:close": e2}), AS_OF, ("ETH",))
    for k, fs in out.items():
        assert fs, f"fixture for {k} found nothing"
    return {k: fs[0] for k, fs in out.items()}


FINDINGS = _findings()


# G2 detectors whose own test files render every finding in both formats (tests/test_growth_g2_anomalies.py, test_growth_g2_insights.py)
G2_RENDERED_ELSEWHERE = {"streak", "volatility_regime", "volume_spike", "fear_aftermath", "year_paths", "gold_ratio", "weekly_streak", "drawdown_table"}


def test_every_detector_has_a_fixture() -> None:
    assert set(FINDINGS) | G2_RENDERED_ELSEWHERE == set(DETECTORS)


def _variant(f: Finding, kind: str) -> dict:
    return dict(f.chart) | ({"kind": kind, "variant_of": f.chart["kind"]} if kind != f.chart["kind"] else {})


CASES = [(det, kind) for det, f in FINDINGS.items() for kind in graphics.variant_kinds(f.chart)]


@pytest.mark.parametrize("det,kind", CASES, ids=[f"{d}-{k}" for d, k in CASES])
@pytest.mark.parametrize("fmt", ["x", "portrait"])
def test_every_detector_chart_and_variant_renders_in_both_formats(det: str, kind: str, fmt: str) -> None:
    f = FINDINGS[det]
    png, meta = graphics.render_graphic(_variant(f, kind), fmt, f)
    assert Image.open(BytesIO(png)).size == graphics.FORMATS[fmt]
    assert meta["template"] == f"intel/{kind}" and meta["version"] and meta["kind"] == kind and len(meta["sha256"]) == 64
    assert meta["label_overlaps"] == 0, meta
    # the draft path (a stored finding dict, as in gr_drafts.finding) draws the same picture
    assert graphics.render_graphic(_variant(f, kind), fmt, f.to_dict())[1]["sha256"] == meta["sha256"]


def test_every_detector_offers_an_other_chart() -> None:
    no_alt = {det for det, f in FINDINGS.items() if len(graphics.variant_kinds(f.chart)) < 2}
    assert no_alt <= {"drawdown_cycle", "seasonality"}, no_alt


@pytest.mark.parametrize("det", sorted(FINDINGS))
def test_hero_and_headline_come_from_the_finding_numbers(det: str) -> None:
    f = FINDINGS[det]
    spec = graphics.to_spec(f.chart, "x", f)
    texts = {k: v["text"] for k, v in f.numbers.items()}
    hero_key = f.chart["hero"]
    if hero_key in f.numbers:
        assert spec.hero == texts[hero_key]
    else:
        assert spec.hero == f.fill(hero_key)
    assert spec.headline == f.fill(f.chart["headline"])
    assert "{" not in spec.headline + spec.dek + spec.hero
    assert spec.kicker == graphics.DETECTOR_LABEL[det]
    for a in spec.annotations:
        assert "{" not in a.text
    # the source line is the Finding's attribution + as-of
    for line in f.attribution:
        assert line in spec.source
    assert "as of Nov 24, 2026" in spec.source


def test_adapter_reads_figures_from_the_finding_not_the_chart() -> None:
    """Change a figure's formatted text in the Finding: the graphic follows it (the adapter never formats a printed number itself)."""
    f = FINDINGS["rarity"]
    d = f.to_dict()
    d["numbers"] = {**d["numbers"], "move": {**d["numbers"]["move"], "text": "MOVE-TEXT"}, "share": {**d["numbers"]["share"], "text": "SHARE-TEXT"}}
    spec = graphics.to_spec(f.chart, "x", d)
    assert spec.hero == "MOVE-TEXT" and "SHARE-TEXT" in spec.headline
    assert any(a.text.startswith("MOVE-TEXT") for a in spec.annotations)


def test_fear_and_greed_attribution_is_next_to_the_number_and_in_the_source() -> None:
    for det in ("sentiment", "then_now"):
        spec = graphics.to_spec(FINDINGS[det].chart, "portrait", FINDINGS[det])
        assert "Fear & Greed Index: alternative.me" in spec.source
        assert spec.options["hero_note"] == "Fear & Greed Index: alternative.me"


def test_marked_values_equal_the_finding_numbers() -> None:
    """The self-check: a marker tied to a Finding number sits on a plotted value equal to it."""
    for det, f in FINDINGS.items():
        for kind in graphics.variant_kinds(f.chart):
            ch = graphics._merge(_variant(f, kind))
            tied = [a for a in ch.get("annotations") or [] if a.get("number")]
            spec = graphics.to_spec(_variant(f, kind), "x", f)
            for a in tied:
                want = f.numbers[a["number"]]["value"]
                if kind == "histogram_marker":
                    assert a["x"] == pytest.approx(want, abs=1e-4)
                    continue
                s = spec.series[int(a.get("series", 0))]
                i = [str(x)[:10] if isinstance(x, str) else x for x in s.x].index(str(a["x"])[:10] if isinstance(a["x"], str) else a["x"])
                got = s.values[i] if kind != "rebased_lines" else (s.values[i] / s.values[0] - 1) * 100
                assert got == pytest.approx(want, rel=1e-3, abs=0.01), (det, kind, a["number"])
    assert any(a.get("number") for f in FINDINGS.values() for a in f.chart["annotations"])


def test_a_chart_that_disagrees_with_its_finding_is_refused() -> None:
    f = FINDINGS["ratio_record"]
    bad = dict(f.chart)
    bad["series"] = [dict(bad["series"][0], points=[[x, v * 1.1] for x, v in bad["series"][0]["points"]])]
    with pytest.raises(SpecError, match="finding's 'v'"):
        graphics.to_spec(bad, "x", f)
    g = FINDINGS["divergence"]
    worse = dict(g.chart)
    worse["series"] = list(reversed(worse["series"]))  # the lines swapped: Bitcoin's end value isn't the coin's return
    with pytest.raises(SpecError):
        graphics.to_spec(worse, "x", g)


def test_unknown_placeholder_is_an_error_not_an_invented_figure() -> None:
    f = FINDINGS["beta"]
    with pytest.raises(SpecError, match="placeholder"):
        graphics.to_spec(dict(f.chart, headline="beta {nonexistent}"), "x", f)


def test_graphic_png_accepts_a_finding() -> None:
    assert graphics.graphic_png(FINDINGS["seasonality"], "x")[:4] == b"\x89PNG"
    with pytest.raises(ValueError):
        graphics.graphic_png(FINDINGS["seasonality"], "square")


def test_a_long_far_tail_goes_into_one_edge_bin_and_every_window_still_counts() -> None:
    pts = walk(1000)
    pts = [(d, v * (4.0 if 400 <= i < 440 else 1.0)) for i, (d, v) in enumerate(pts)]  # a short +300% spike: a long, thin right tail
    f = extremes.rarity_move(Memory.from_points({"px:BTC:close": crash_tail(pts, 30, -0.40)}), AS_OF, ("BTC",))[0]
    opts = f.chart["options"]
    assert opts.get("overflow_above") is not None and opts["overflow_above"] < max(v for _, v in f.chart["series"][0]["points"])
    assert len(f.chart["series"][0]["points"]) == f.numbers["total"]["value"]  # every window is an observation
    for fmt in ("x", "portrait"):
        assert graphics.render_graphic(f.chart, fmt, f)[1]["label_overlaps"] == 0
