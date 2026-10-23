"""Committee backtest (app/committee/backtest.py): leakage, anonymisation, metrics math, gate / WAIT handling, buy-and-hold,
the owner API. No network: the market snapshot is a fake, every candle / funding row / Fear & Greed value comes from a synthetic
dataset served by the backtest's TimeMachine. The synthetic future is extreme (x10 prices, 5%/h funding, F&G 99) so any leak
would show."""

import asyncio
import json
import math
import random
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.committee import backtest as cbt
from app.committee.evidence import common
from app.tradingos import backtest as bt
from app.tradingos import history, market, venues
from app.tradingos.market import Candle, MarketData
from app.tradingos.venues import Venues

H = 3_600_000
DAY = 86_400_000
T0 = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
HOURS = 24 * 160
CUT = T0 + 24 * 130 * H  # after this the synthetic future is poisoned
BASE = {"BTC": 43_210.0, "ETH": 2_345.0}


# ---------------------------------------------------------------- synthetic world
def _hourly(base: float, seed: int, poison: bool) -> list[Candle]:
    rnd = random.Random(seed)
    px, out, drift = base, [], 0.0
    for k in range(HOURS):
        if k % 240 == 0:
            drift = rnd.choice((-1, 1)) * rnd.uniform(0.0004, 0.0012)  # trending stretches, so the committee sometimes acts
        o = px
        c = o * (1 + drift + rnd.gauss(0, 0.004))
        h, lo = max(o, c) * (1 + abs(rnd.gauss(0, 0.002))), min(o, c) * (1 - abs(rnd.gauss(0, 0.002)))
        t = T0 + k * H
        m = 10.0 if poison and t + H > CUT else 1.0
        out.append(Candle(t, o * m, h * m, lo * m, c * m, 100.0 + k % 9))
        px = c
    return out


def _agg(cs: list[Candle], n: int) -> list[Candle]:
    out = []
    for k in range(0, len(cs) - n + 1, n):
        w = cs[k:k + n]
        out.append(Candle(w[0].t, w[0].o, max(x.h for x in w), min(x.l for x in w), w[-1].c, sum(x.v for x in w)))
    return out


def make_dataset(d: Path, poison: bool = True) -> cbt.Dataset:
    ds = cbt.Dataset(d)
    for n, (coin, base) in enumerate(BASE.items()):
        h1 = _hourly(base, 11 + n, poison)
        ds.candles[(coin, "1h")] = h1
        ds.candles[(coin, "4h")] = _agg(h1, 4)
        ds.candles[(coin, "1d")] = _agg(h1, 24)
        ds.funding[coin] = [(T0 + k * H, 0.05 if poison and T0 + k * H > CUT else 0.0000125 * (1 + (k % 5))) for k in range(HOURS)]
    ds.fng = [((T0 + k * DAY) // 1000, 99 if poison and T0 + k * DAY > CUT else 30 + k % 40, "Extreme Greed" if poison and T0 + k * DAY > CUT else "Fear")
              for k in range(HOURS // 24)]
    ds.save()
    return ds


def _hl(req: httpx.Request) -> httpx.Response:
    if req.url.host != "api.hyperliquid.xyz":
        return httpx.Response(404, text="no network in tests")
    body = json.loads(req.content)
    if body["type"] == "metaAndAssetCtxs":
        uni = [{"name": c, "szDecimals": 4, "maxLeverage": 40} for c in BASE]
        ctx = [{"markPx": str(b), "midPx": str(b), "oraclePx": str(b), "prevDayPx": str(b), "funding": "0.0000125", "openInterest": "1000",
                "dayNtlVlm": "3000000000", "impactPxs": [str(b), str(b)]} for b in BASE.values()]
        return httpx.Response(200, json=[{"universe": uni}, ctx])
    return httpx.Response(404, json={"error": "the live snapshot is the only network answer here"})


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> cbt.Dataset:
    t = httpx.MockTransport(_hl)
    monkeypatch.setattr(market, "_shared", MarketData(transport=t))
    monkeypatch.setattr(venues, "_shared", Venues(transport=t))
    monkeypatch.setattr(history, "DIR", tmp_path / "none")  # no stored research history: the TimeMachine answers
    common.reset()
    return make_dataset(tmp_path / "full")


def _cfg(**kw: Any) -> cbt.Config:
    return cbt.Config.parse({"coins": ["BTC"], "timeframe": "4h", "every": 6, **kw})


def _decide(ds: cbt.Dataset, points: list[tuple[str, int, int]]) -> tuple[list[cbt.Decision], dict[Any, Any], cbt.TimeMachine]:
    gate = cbt._gate_db()
    try:
        return asyncio.run(cbt.rules_decisions(_cfg(mode="llm"), ds, points, gate, cbt.Regimes(ds.candles[("BTC", "1d")])))
    finally:
        gate.close()


# ---------------------------------------------------------------- leakage
def test_decisions_at_t_are_identical_with_or_without_the_future(world: cbt.Dataset, tmp_path: Path) -> None:
    cut_ds = world.truncated(CUT, tmp_path / "cut")
    pts_full = [p for p in cbt.grid(_cfg(), world) if p[2] <= CUT][-24:]
    pts_cut = [p for p in cbt.grid(_cfg(), cut_ds) if p[2] <= CUT][-24:]
    # the truncated history has no "next candle" for the very last point; compare the decisions both histories can make
    common_pts = [p for p in pts_full if p in pts_cut]
    assert len(common_pts) >= 20
    full, packs_full, tm = _decide(world, common_pts)
    cut, packs_cut, _ = _decide(cut_ds, common_pts)
    key = lambda d: (d.coin, d.t, d.action, d.rating, d.exec_status, tuple(d.exec_reasons), json.dumps(d.plan, sort_keys=True), d.regime)  # noqa: E731
    assert [key(d) for d in full] == [key(d) for d in cut]
    assert all(d.action != "ERROR" for d in full), [d.note for d in full if d.action == "ERROR"]
    strip = lambda p: {k: v for k, v in p.items() if k not in ("built_at", "refs")}  # noqa: E731 - fetched_at stamps differ
    assert [json.dumps(strip(packs_full[k]), sort_keys=True) for k in sorted(packs_full)] == \
           [json.dumps(strip(packs_cut[k]), sort_keys=True) for k in sorted(packs_cut)]
    # the source itself never handed out anything after the latest decision time
    last = max(p[2] for p in common_pts)
    assert tm.served_max["candles"] <= last and tm.served_max["funding"] <= last and tm.served_max["fear_greed"] <= last
    assert tm.requests["candleSnapshot"] > 0 and tm.requests["fundingHistory"] > 0 and tm.requests["fear_greed"] > 0


def test_poisoned_future_never_reaches_the_evidence(world: cbt.Dataset) -> None:
    pts = [p for p in cbt.grid(_cfg(), world) if p[2] <= CUT][-3:]
    ds, packs, _ = _decide(world, pts)
    for ev in packs.values():
        s = ev["sections"]["sentiment"]["facts"]
        assert s["funding_1h_pct"] < 1 and s["funding_24h_avg_pct"] < 1  # 5%/h after the cut
        assert s["fear_greed"]["value"] != 99
        assert ev["price"]["value"] < BASE["BTC"] * 3  # x10 after the cut
        assert ev["sections"]["news"]["status"] == "unavailable" and ev["sections"]["fundamental"]["status"] in ("unavailable", "partial")


def test_time_machine_refuses_everything_after_the_cutoff(world: cbt.Dataset) -> None:
    tm = cbt.TimeMachine(world)
    cut = T0 + 100 * 4 * H + 2 * H  # in the middle of a 4h candle
    tm.cutoff = cut
    with httpx.Client(transport=tm.transport()) as c:
        rows = c.post("https://api.hyperliquid.xyz/info", json={"type": "candleSnapshot", "req": {"coin": "BTC", "interval": "4h",
                                                                                                "startTime": 0, "endTime": cut + 50 * DAY}}).json()
        assert rows and max(r["t"] for r in rows) + 4 * H <= cut  # the candle still forming at the cutoff is not served
        f = c.post("https://api.hyperliquid.xyz/info", json={"type": "fundingHistory", "coin": "BTC", "startTime": cut - DAY, "endTime": cut + DAY}).json()
        assert f and max(r["time"] for r in f) <= cut
        g = c.get("https://api.alternative.me/fng/", params={"limit": 0}).json()["data"]
        assert g and max(int(r["timestamp"]) * 1000 for r in g) <= cut
        assert c.post("https://api.hyperliquid.xyz/info", json={"type": "l2Book", "coin": "BTC"}).status_code == 404
    tm.cutoff = None
    with httpx.Client(transport=tm.transport()) as c:
        assert c.get("https://api.alternative.me/fng/").status_code == 503  # no cutoff, no data


# ---------------------------------------------------------------- LLM anonymisation
def test_llm_evidence_is_anonymised(world: cbt.Dataset) -> None:
    pts = [p for p in cbt.grid(_cfg(), world) if p[2] <= CUT][-2:]
    _, packs, _ = _decide(world, pts)
    for (coin, t), ev in packs.items():
        anon, k = cbt.anonymise(ev, world, coin, "4h", t)
        text = json.dumps(anon)
        assert cbt.leak_check(anon, coin, cbt._real_levels(ev)) == []
        assert "BTC" not in text and "itcoin" not in text and "2025" not in text and "2026" not in text
        assert anon["asset"]["symbol"] == "ASSET" and anon["as_of"] == "t-0" and anon["price"]["ts"] == "t-0"
        first = [c for c in world.candles[(coin, "4h")] if c.t + 4 * H <= t][-300]
        assert k == pytest.approx(100 / first.c)
        assert anon["price"]["value"] == pytest.approx(ev["price"]["value"] * k, rel=1e-5)
        fr = anon["sections"]["technical"]["facts"]["frames"]["4h"]
        assert fr["last_closed"] == "t-0" and fr["close"] == pytest.approx(ev["sections"]["technical"]["facts"]["frames"]["4h"]["close"] * k, rel=1e-5)
        # the same deterministic plan, just rebased: the committee decides on the same shape it would see for real
        sa, sr = anon["sections"]["technical"]["facts"]["scenarios"], ev["sections"]["technical"]["facts"]["scenarios"]
        for side in ("long", "short"):
            if sr.get(side):
                assert sa[side]["trigger"] == pytest.approx(sr[side]["trigger"] * k, rel=1e-4)
        assert anon["sections"]["news"]["status"] == "unavailable" and anon["sections"]["fundamental"]["status"] == "unavailable"
        # the check bites when something slips through
        assert cbt.leak_check({**anon, "note": "BTC on 2025-03-01"}, coin, []) == ["name BTC", "calendar date"]
        assert cbt.leak_check({**anon, "x": round(ev["price"]["value"], 1)}, coin, cbt._real_levels(ev))[0].startswith("real price")
        probe, _ = cbt.anonymise(ev, world, coin, "4h", t, cbt.PROBE_BASE)
        planted = round(ev["price"]["value"], 1)
        assert cbt.leak_check({**anon, "x": planted}, coin, cbt._real_levels(ev), {**probe, "x": planted})[0].startswith("real price")
        # a rebased number that happens to equal a real level (an asset trading near 100) moves with the base: not a leak
        coincidence = anon["price"]["value"]
        assert cbt.leak_check(anon, coin, [coincidence], probe) == [] and cbt.leak_check(anon, coin, [coincidence]) != []


class _ScriptedLLM:
    """A model backend that answers nothing usable: every role falls back to rules, and the backtest still counts the calls."""

    name, paid = "router", False

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def models(self) -> dict[str, str]:
        return {"fast": "fake", "deep": "fake", "synthesis": "fake"}

    async def complete(self, call: Any, run_id: int | None = None) -> Any:
        from app.committee.llm import Reply

        self.prompts.append(call.system + "\n" + call.prompt)
        return Reply("{}", "fake", 120, 30)


def test_llm_mode_sees_only_anonymised_prompts_and_records_calls(world: cbt.Dataset) -> None:
    be = _ScriptedLLM()
    cfg = _cfg(mode="llm", llm_max_decisions=3, end=datetime.fromtimestamp(CUT / 1000, tz=timezone.utc).isoformat())
    r = asyncio.run(cbt.run_backtest(cfg, world, backend=be))
    assert be.prompts, "the scripted model was never asked"
    for p in be.prompts:
        assert "BTC" not in p and "Bitcoin" not in p and "2025-" not in p and "2026-" not in p
        assert "43,2" not in p and "4321" not in p  # the synthetic BTC trades around 43,210: no absolute price reaches the model
    assert r["llm"]["calls"] > 0 and r["llm"]["tokens_in"] > 0 and r["llm"]["cost_usd"] == 0.0
    assert r["decisions"]["committee_llm"]["total"] == 3 and "memorisation" in r["llm"]["contamination_note"]


# ---------------------------------------------------------------- metrics math (hand-computed)
def test_sharpe_drawdown_profit_factor_by_hand() -> None:
    daily = [1.0, -0.5, 0.0, 2.0] * 6  # 24 days
    m, sd = statistics.fmean(daily), statistics.stdev(daily)
    assert m == pytest.approx(0.625) and cbt.sharpe(daily) == pytest.approx(round(m / sd * math.sqrt(365), 3))
    assert cbt.sharpe([1.0] * 30) is None and cbt.sharpe([1.0, 2.0]) is None  # flat / too short
    assert cbt.max_drawdown([1, 2, -1, -2, 0.5, 3, -4]) == pytest.approx(4.0)  # peak 4.0 (after +3? no): 0,1,3,2,0,0.5,3.5,-0.5 -> 4.0
    assert cbt.max_drawdown([-1, -1]) == pytest.approx(2.0) and cbt.max_drawdown([1, 1]) == 0.0
    assert cbt.max_drawdown_pct([100, 120, 90, 130, 65]) == pytest.approx(50.0)
    assert cbt.profit_factor([2.0, -1.0, 1.5, -0.5]) == pytest.approx(3.5 / 1.5, abs=1e-3)
    assert cbt.profit_factor([1.0, 2.0]) is None
    ci = cbt.bootstrap_ci([1.0, -1.0, 0.5, 0.2, -0.3] * 8)
    assert ci is not None and ci[0] < statistics.fmean([1.0, -1.0, 0.5, 0.2, -0.3]) < ci[1] and ci == cbt.bootstrap_ci([1.0, -1.0, 0.5, 0.2, -0.3] * 8)


def _trade(r: float, day: int, coin: str = "BTC", entry: float = 100.0, stop: float = 98.0) -> bt.Trade:
    t = T0 + day * DAY
    return bt.Trade(coin, "x", "LONG", "range", t, t + H, t + 2 * H, entry, stop, r, r + 0.1, 0.1, "stop" if r < 0 else "tp1+tp2", 60.0)


def test_trade_metrics_and_verdicts() -> None:
    ts = [_trade(2.0, 1), _trade(-1.0, 2), _trade(1.0, 3), _trade(-1.0, 3)]
    m = cbt.trade_metrics(ts, T0, T0 + 30 * DAY)
    assert m["trades"] == 4 and m["net_r"] == 1.0 and m["avg_r"] == 0.25 and m["win_rate"] == 50.0
    assert m["net_pnl_pct"] == 0.5 and m["profit_factor"] == 1.5 and m["max_dd_pct"] == 0.5
    daily = [0.0] * 31
    daily[1], daily[2], daily[3] = 1.0, -0.5, 0.0
    assert m["sharpe_daily_ann"] == cbt.sharpe(daily)
    fees_r = 2 * bt.FEE * 100 / 2  # per trade, R = 2
    assert m["fees_pct"] == pytest.approx(round(4 * fees_r * 0.5, 3))
    assert m["turnover_x_per_year"] == pytest.approx(round(4 * 2 * (0.005 / 0.02) / (30 / 365), 2))
    pos = {"trades": 40, "avg_r": 0.3, "ci95_mean_r": [0.1, 0.5]}
    assert cbt.verdict({"avg_r": 0.2}, pos, {"avg_r": 0.25, "trades": 100})[0] == "positive in sample and out of sample with CI excluding zero"
    assert cbt.verdict({"avg_r": 0.2}, {**pos, "ci95_mean_r": [-0.1, 0.5]}, {"avg_r": 0.25, "trades": 100})[0] == "edge not distinguishable from zero"
    assert cbt.verdict({"avg_r": 0.2}, {**pos, "trades": 10}, {"avg_r": 0.25, "trades": 100})[0] == "edge not distinguishable from zero"
    assert cbt.verdict({"avg_r": -0.2}, pos, {"avg_r": -0.01, "trades": 100})[0] == "no evidence of edge"
    assert cbt.verdict({}, {}, {"trades": 0})[0] == "no evidence of edge"
    assert all("profitable" not in v for v in cbt.VERDICTS)


# ---------------------------------------------------------------- gate, WAIT, simulation
def _book(ds: cbt.Dataset) -> cbt.Book:
    return cbt.Book({"BTC": ds.series("BTC", "4h")}, {"BTC": ds.funding["BTC"]})


def test_wait_makes_no_trades_and_gate_rejections_are_counted(world: cbt.Dataset) -> None:
    book = _book(world)
    f = book.series["BTC"]
    i = 300
    px = f.c[i]
    plan = {"entry_zone": [px, px], "stop": px * 0.97, "targets": [px * 1.03, px * 1.06]}
    t = f.t[i] + 4 * H
    waits = [cbt.Decision("BTC", t + k * DAY, i + 6 * k, "WAIT") for k in range(5)]
    assert cbt.committee_trades(waits, book, "c")[0] == [] and cbt.committee_trades(waits, book, "c", gated=False)[0] == []
    rej = cbt.Decision("BTC", t, i, "LONG", "Buy", "REJECTED", ["price 43210.5 is outside the entry zone"], plan)
    ok = cbt.Decision("BTC", f.t[i + 60] + 4 * H, i + 60, "LONG", "Buy", "APPROVED", [], {**plan, "stop": f.c[i + 60] * 0.97,
                                                                                           "targets": [f.c[i + 60] * 1.03, f.c[i + 60] * 1.06]})
    gated, _ = cbt.committee_trades([rej, ok], book, "c")
    ungated, _ = cbt.committee_trades([rej, ok], book, "c", gated=False)
    assert [x.t_signal for x in gated] == [ok.t] and len(ungated) == 2
    s = cbt._summary([*waits, rej, ok], __import__("collections").Counter())
    assert s["actions"] == {"WAIT": 5, "LONG": 2} and s["proposals_execution"] == {"REJECTED": 1, "APPROVED": 1}
    assert s["risk_gate_rejections"] == {"price # is outside the entry zone": 1}


def test_committee_trade_uses_next_open_stop_first_and_skips_beyond_stop(world: cbt.Dataset) -> None:
    book = _book(world)
    f = book.series["BTC"]
    i = 400
    nxt = f.o[i + 1]
    long_ok = cbt.Decision("BTC", f.t[i] + 4 * H, i, "LONG", exec_status="APPROVED",
                           plan={"entry_zone": [nxt, nxt], "stop": nxt * 0.9, "targets": [nxt * 1.5]})
    (tr,), _ = cbt.committee_trades([long_ok], book, "c")
    assert tr.entry == pytest.approx(nxt * (1 + bt.slip("BTC"))) and tr.t_entry == f.t[i + 1]
    ref = bt.simulate(f, i, "LONG", nxt * 0.9, None, 0.0, cbt.MAX_HOLD_MIN, "BTC", book.funding["BTC"], fixed_targets=(nxt * 1.5, nxt * 1.5))
    assert tr.r_net == pytest.approx(ref["r_net"])  # the shared simulator, unchanged
    beyond = cbt.Decision("BTC", f.t[i] + 4 * H, i, "LONG", exec_status="APPROVED", plan={"stop": nxt * 1.01, "targets": [nxt * 1.2]})
    past_tp1 = cbt.Decision("BTC", f.t[i] + 4 * H, i, "LONG", exec_status="APPROVED", plan={"stop": nxt * 0.9, "targets": [nxt * 0.99]})
    trs, skipped = cbt.committee_trades([beyond], book, "c")
    trs2, skipped2 = cbt.committee_trades([past_tp1], book, "c")
    assert trs == [] and skipped == {"opened beyond the stop": 1} and trs2 == [] and skipped2 == {"opened beyond TP1": 1}


# ---------------------------------------------------------------- buy and hold
def test_buy_and_hold_math(tmp_path: Path) -> None:
    ds = cbt.Dataset(tmp_path)
    span = 4 * H
    cs = [Candle(T0 + k * span, 100 + k, 100 + k, 100 + k, 100 + k + 1, 1) for k in range(60)]  # opens 100, last close 160
    ds.candles[("BTC", "4h")] = cs
    ds.funding["BTC"] = [(T0 + k * H, 0.0001) for k in range(60 * 4 + 1)]
    regimes = cbt.Regimes([Candle(T0 - (100 - k) * DAY, 1, 1, 1, 1, 1) for k in range(100)])
    p = cbt.Period(T0, T0 + 60 * span)
    r = cbt.buy_hold(ds, ["BTC"], "4h", {"BTC": T0}, p, regimes)["BTC"]
    sl = bt.slip("BTC")
    assert r["return_pct"] == pytest.approx(60.0)
    assert r["net_return_pct"] == pytest.approx(round(((160 * (1 - sl)) / (100 * (1 + sl)) - 1 - 2 * bt.FEE) * 100, 2))
    n_fund = sum(1 for t, _ in ds.funding["BTC"] if T0 < t <= T0 + 60 * span)
    assert r["funding_paid_pct"] == pytest.approx(round(n_fund * 0.0001 * 100, 2))
    assert r["net_return_perp_pct"] == pytest.approx(round(r["net_return_pct"] - n_fund * 0.0001 * 100, 2), abs=0.011)
    assert r["max_dd_pct"] == 0.0


def _daily(step: float, n: int = 200, spike_last: bool = False) -> list[Candle]:
    out = []
    for k in range(n):
        o, c = 100 * step ** k, 100 * step ** (k + 1)
        r = 0.02 * (1 + 0.6 * math.sin(k / 2.0))  # the daily range swings, so ATR% sits mid-distribution
        if spike_last and k >= n - 3:
            r = 0.2
        out.append(Candle(T0 + k * DAY, o, max(o, c) * (1 + r), min(o, c) * (1 - r), c, 1))
    return out


def test_regimes_follow_the_predeclared_rule() -> None:
    end = T0 + 200 * DAY
    assert cbt.Regimes(_daily(1.01)).at(T0 + 10 * DAY) == "unknown"  # warm-up
    assert cbt.Regimes(_daily(1.01)).at(end) == "trend-up"  # EMA50 up > 1% in 5 days
    assert cbt.Regimes(_daily(0.99)).at(end) == "trend-down"
    assert cbt.Regimes(_daily(1.0)).at(end) == "range"
    assert cbt.Regimes(_daily(1.01, spike_last=True)).at(end) == "high-vol"  # ATR% rank >= 0.8 wins over the trend
    assert cbt.Regimes(_daily(1.01)).at(end - DAY // 2) == cbt.Regimes(_daily(1.01)).at(end - DAY)  # only CLOSED daily candles


# ---------------------------------------------------------------- whole run + API
def test_full_rules_run_reports_every_strategy(world: cbt.Dataset) -> None:
    cfg = _cfg(coins=["BTC", "ETH"], start=(datetime.fromtimestamp(T0 / 1000, tz=timezone.utc) + timedelta(days=120)).isoformat())
    r = asyncio.run(cbt.run_backtest(cfg, world))
    assert set(r["strategies"]) >= {"committee_rules", "committee_rules_ungated", "kiwi_breakout_4h_v1", "kiwi_trend_pullback_v1_1h", "ema_cross"}
    assert all(v["verdict"] in cbt.VERDICTS for v in r["strategies"].values())
    assert set(r["buy_hold"]) >= {"BTC", "ETH", "basket_equal_weight"}
    d = r["decisions"]["committee_rules"]
    assert d["total"] == sum(d["actions"].values()) > 0 and not d["errors"]
    assert len(r["strategies"]["committee_rules"]["folds"]) == cbt.FOLDS and "inference_usd" in r["strategies"]["committee_rules"]["all"]
    md = cbt.markdown(r)
    assert "| committee_rules |" in md and "profitable" not in md.replace("not evidence of future profitability", "")
    json.dumps(r)  # storable in cm_backtests.result


def test_backtest_api_is_owner_only_and_runs_in_the_background(client: TestClient, db: Session, world: cbt.Dataset,
                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    from app.models.admin import AdminSession
    from app.security import admin_auth

    assert client.get("/api/committee/backtests").status_code == 401
    assert client.post("/api/committee/backtests", json={"coins": ["BTC"]}).status_code == 401
    db.add(AdminSession(token_hash=admin_auth._digest("owner-token"), username="owner",
                        expires_at=datetime.now(timezone.utc) + timedelta(hours=1)))
    db.commit()
    client.cookies.set(admin_auth.COOKIE, "owner-token")
    monkeypatch.setattr(cbt, "fetch_dataset", lambda *a, **k: world)
    assert client.post("/api/committee/backtests", json={"coins": ["BTC"], "timeframe": "3h"}).status_code == 422
    start = (datetime.fromtimestamp(T0 / 1000, tz=timezone.utc) + timedelta(days=140)).isoformat()
    r = client.post("/api/committee/backtests", json={"coins": ["BTC"], "start": start})
    assert r.status_code == 202, r.text
    bid = r.json()["id"]
    import time

    for _ in range(600):
        got = client.get(f"/api/committee/backtests/{bid}").json()
        if got["status"] in ("done", "failed"):
            break
        time.sleep(0.1)
    assert got["status"] == "done", got.get("result")
    assert got["config"]["coins"] == ["BTC"] and "committee_rules" in got["result"]["strategies"]
    lst = client.get("/api/committee/backtests").json()["backtests"]
    assert lst[0]["id"] == bid and lst[0]["verdicts"]["committee_rules"] in cbt.VERDICTS
    assert client.get("/api/committee/backtests/999").status_code == 404


def test_importing_the_backtest_loads_no_execution_module() -> None:
    import os
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[1]
    code = ("import sys, app.committee.backtest; bad = [m for m in sys.modules if m in ('app.tradingos.execution', 'app.tradingos.exchange', "
            "'app.tradingos.accounts', 'app.tradingos.terminal') or m.startswith(('app.exchange', 'hyperliquid'))]; print(bad)")
    out = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=120,
                         env={**os.environ, "PYTHONPATH": os.pathsep.join([os.environ.get("PYTHONPATH", ""), str(root)])})
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.strip() == "[]"
