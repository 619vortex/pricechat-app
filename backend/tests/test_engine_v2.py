"""Kiwi Trading Engine v2, stage 1: structured decisions per kitchen candidate, shadow outcomes (hypothetical, after costs,
with MAE / MFE), the decisions report (approved vs rejected, per filter, calibration), regime confidence, and the risk
engine's portfolio checks (advisory by default, enforced only by the owner's switch). TEST FIXTURES only, no network."""

import itertools
import time
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tradingos import TsDecision
from app.tradingos import decisions, execution, kitchen, market, risk, signals
from app.tradingos.execution import TradeError
from app.tradingos.market import Candle

from tests.test_kiwi_kitchen import BASE, KitchenMD, a_setup, at, kitchen_on
from tests.test_trading_execution import active_signal
from tests.test_trading_os import kiwi, md  # noqa: F401 - the md fixture

M15 = kitchen.M15
T0 = BASE - 3000 * M15


class StableMD(KitchenMD):
    """TEST FIXTURE: a steady uptrend whose price is a function of TIME (so later reads see the same candles), plus funding."""

    def candles(self, coin: str, interval: str, count: int = 200) -> list[Candle]:
        step = market.INTERVALS[interval] * 1000
        last_open = self.now_ms // step * step - step
        out = []
        for k in range(count):
            t = last_open - (count - 1 - k) * step
            o = 100 + 0.02 * (t - T0) / M15
            move = 0.02 * step / M15
            out.append(Candle(t, o, o + move + 0.05, o - 0.03, o + move * 0.9, 1000 + k))
        return out

    def funding_history(self, coin: str, hours: int = 168) -> list[tuple[int, float]]:
        return [((self.now_ms // 3_600_000 - h) * 3_600_000, 0.00001) for h in range(hours, 0, -1)]


@pytest.fixture(autouse=True)
def fixtures(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(kitchen.ml, "model", lambda: None)
    monkeypatch.setattr(kitchen.ix, "setups", a_setup)


def test_every_kitchen_candidate_becomes_one_structured_decision(db: Session) -> None:
    kitchen_on(db, publish=False)
    md = StableMD(BASE + 60_000)
    assert kitchen.tick(db, md, at(md.now_ms))["changed"] == {"BTC": "READY"}
    d = db.scalar(select(TsDecision))
    assert d and d.state == "READY" and d.decision == "TAKE" and d.shadow and d.signal_id is None and d.outcome == "PENDING"
    assert d.direction == "LONG" and d.model == "A_pullback" and d.version == "standard" and d.strategy == kitchen.STRATEGY
    assert d.stop < d.ref < d.tp1 < d.tp2 and d.max_hold_min == 180 and len(d.checks) == len(kitchen.CHECKS)
    assert d.est_fees_r and d.est_slippage_r and d.est_cost_r and d.rr_net == pytest.approx(d.r1 - d.est_cost_r, abs=1e-3)
    rec = decisions.as_record(d)
    assert rec["symbol"] == "BTC" and rec["timeframe"] == "15m" and rec["signal_status"] == "READY" and rec["outcome"]["hypothetical"]
    assert rec["entry_reason"] == ["Uptrend, pulled back to EMA20 and closed back above it"] and rec["risk_checks"][0]["id"] == "setup"
    kitchen.tick(db, md, at(md.now_ms))
    assert len(db.scalars(select(TsDecision)).all()) == 1  # the same candle is one decision
    # a rejected candidate names the check that rejected it
    md2 = StableMD(BASE + M15 * 2 + 60_000, funding=0.0005)
    assert kitchen.tick(db, md2, at(md2.now_ms))["changed"] == {"BTC": "BURNT"}
    r = db.scalars(select(TsDecision).order_by(TsDecision.id)).all()[-1]
    assert r.state == "REJECTED" and r.decision == "REJECT" and r.rejected_by == "crowding" and "crowded" in r.reason


def test_a_published_decision_links_its_signal_and_stays_taken(db: Session) -> None:
    kitchen_on(db, publish=True)
    md = StableMD(BASE + 60_000)
    kitchen.tick(db, md, at(md.now_ms))
    d = db.scalar(select(TsDecision))
    assert d and d.state == "READY" and d.signal_id is not None and not d.shadow


def test_shadow_outcome_waits_for_the_max_hold_then_replays_the_plan_after_costs(db: Session) -> None:
    kitchen_on(db, publish=False)
    md = StableMD(BASE + 60_000)
    kitchen.tick(db, md, at(md.now_ms))
    later = StableMD(BASE + 60_000 + 60 * 60_000)  # an hour later: the 3 h max hold isn't over
    assert decisions.resolve(db, later, at(later.now_ms))["resolved"] == 0
    later = StableMD(BASE + 6 * 3_600_000)
    out = decisions.resolve(db, later, at(later.now_ms))
    assert out["resolved"] == 1 and out["outcomes"] == {"DONE": 1}
    d = db.scalar(select(TsDecision))
    assert d and d.outcome == "DONE" and d.exit_kind == "time" and d.hold_min == pytest.approx(180, abs=15)
    assert d.r_net is not None and d.r_gross is not None and d.r_net < d.r_gross and d.cost_r and d.cost_r > 0  # costs counted
    assert d.mae_r is not None and d.mfe_r is not None and d.mae_r <= 0 <= d.mfe_r and d.resolved_at
    assert decisions.resolve(db, later, at(later.now_ms))["resolved"] == 0  # resolved once


_seq = itertools.count(1)


def _row(db: Session, n: int, state: str, r: float, check: str | None = None, score: int = 50, regime: str = "RANGE") -> None:
    """TEST FIXTURE: n resolved decisions in the last days (one candle each)."""
    now = int(time.time() * 1000)
    for _ in range(n):
        db.add(TsDecision(strategy="intraday_15m_v1", version="degen", coin="BTC", timeframe="15m", candle_t=now - next(_seq) * M15,
                          model="F_scalp", direction="LONG", regime=regime, state=state, decision="TAKE" if state == "READY" else "REJECT",
                          rejected_by=check, reason="", shadow=True, signal_id=None, entry_reasons=[], ref=100, stop=99, tp1=101,
                          tp2=102, r1=1, r2=2, max_hold_min=90, checks=[], market={}, outcome="DONE", r_net=r, r_gross=r + 0.1,
                          cost_r=0.1, mae_r=-0.5, mfe_r=0.8, exit_kind="time", score=score))
    db.commit()


def test_report_is_labelled_hypothetical_and_judges_each_filter_with_enough_data(db: Session) -> None:
    _row(db, 30, "READY", -0.2, score=60, regime="STRONG_BULL")
    _row(db, 30, "REJECTED", 0.5, "quality", score=40)
    _row(db, 30, "REJECTED", -0.8, "crowding", score=50)
    _row(db, 5, "REJECTED", 1.0, "liquidity", score=70)
    rep = decisions.report(db, days=30)
    assert rep["hypothetical"] and "Hypothetical" in rep["note"] and rep["counts"]["REJECTED"] == 65
    f = {x["check"]: x for x in rep["filters"]}
    assert f["quality"]["verdict"].startswith("may be too restrictive") and f["quality"]["n"] == 30 and f["quality"]["avg_r"] == 0.5
    assert f["crowding"]["verdict"].startswith("doing its job")
    assert f["liquidity"]["verdict"].startswith("not enough data")
    assert rep["approved"]["all"]["n"] == 30 and rep["approved"]["all"]["avg_r"] == -0.2 and rep["approved"]["all"]["win_rate"] == 0
    assert {x["regime"] for x in rep["by_regime"]} == {"STRONG_BULL", "RANGE"}
    assert rep["calibration"]["score"]["verdict"] == "not enough data"  # the 65+ bucket has 5


def test_metrics_and_calibration() -> None:
    m = decisions.metrics([1.0, -1.0, 2.0, -1.0])
    assert m["n"] == 4 and m["win_rate"] == 0.5 and m["avg_r"] == 0.25 and m["profit_factor"] == 1.5 and m["max_drawdown_r"] == -1.0
    assert not m["enough"] and m["ci90"][0] < 0.25 < m["ci90"][1]
    assert decisions.metrics([]) == {"n": 0}


def test_regime_confidence_from_the_logged_features() -> None:
    strong = {"adx": 35, "slope50_atr": 0.9, "atr_pct_rank": 0.5}
    edge = {"adx": 22.5, "slope50_atr": 0.32, "atr_pct_rank": 0.5}
    assert decisions.regime_confidence("STRONG_BULL", strong) == 1.0
    assert decisions.regime_confidence("STRONG_BULL", edge) < 0.1
    assert decisions.regime_confidence("RANGE", {"adx": 10, "slope50_atr": 0.0, "atr_pct_rank": 0.5}) == 1.0
    assert decisions.regime_confidence("UNSTABLE", {"why": "not enough history"}) is None


# ---------------------------------------------------------------- risk engine: portfolio checks
BOOK = {"bids": [(99.9, 10.0), (99.8, 10.0)], "asks": [(100.1, 10.0), (100.2, 10.0)]}


def test_book_slippage_walks_the_book() -> None:
    assert risk.book_slippage_bps(BOOK, "LONG", 500) == pytest.approx(10.0, abs=0.01)  # all at 100.1 vs mid 100
    assert risk.book_slippage_bps(BOOK, "LONG", 1500) > 10.0  # into the second level
    assert risk.book_slippage_bps(BOOK, "SHORT", 5000) == float("inf")  # thinner than the order
    assert risk.book_slippage_bps(None, "LONG", 100) is None


def _checks(**over: Any) -> dict[str, dict[str, Any]]:
    kw: dict[str, Any] = {"side": "LONG", "entry": 100.0, "stop": 98.0, "notional": 500.0, "margin": 100.0, "equity": 1000.0,
                          "open_positions": [], "spread_bps": 2.0, "levels": BOOK, "funding_h": 0.00001, "hold_h": 3.0, "week_pnl": 0.0}
    kw |= over
    cfg = risk.v2_settings(over.pop("strategies", None)) if "strategies" in over else risk.V2_DEFAULTS
    kw.pop("strategies", None)
    return {c["id"]: c for c in risk.portfolio_checks(cfg, **kw)}


def test_portfolio_checks_pass_and_fail_with_their_numbers() -> None:
    c = _checks()
    assert {k: v["status"] for k, v in c.items()} == {k: "pass" for k in risk.V2_LABELS}
    long2 = [{"side": "LONG", "notional": 2000.0, "margin": 400.0}] * 2
    c = _checks(open_positions=long2, spread_bps=20.0, levels=None, week_pnl=-150.0, stop=99.95)
    assert c["correlation"]["status"] == "fail" and "2 open long positions" in c["correlation"]["detail"]
    assert c["exposure"]["status"] == "pass" and c["spread"]["status"] == "fail" and c["slippage"]["status"] == "unknown"
    assert c["drawdown"]["status"] == "fail" and c["stop"]["status"] == "fail" and c["costs"]["status"] == "fail"
    c = _checks(open_positions=long2 + long2, notional=3000.0)
    assert c["exposure"]["status"] == "fail" and "11.00x" in c["exposure"]["detail"]
    assert _checks(margin=990.0)["margin"]["status"] == "fail"
    assert risk.v2_settings({"risk_v2": {"mode": "enforce"}})["mode"] == "enforce"
    assert risk.v2_settings({"risk_v2": {"mode": "yolo"}})["mode"] == "advisory"
    assert risk.v2_settings(None)["mode"] == "advisory"


def test_preview_records_the_checks_and_only_the_owner_switch_makes_them_block(db: Session, md: Any) -> None:  # noqa: F811
    sig = active_signal(db, md)
    a = kiwi(db, 4, "7001")
    s = signals.settings(db)
    s.strategies = {**(s.strategies or {}), "risk_v2": {"max_same_side": 0}}  # every entry "fails" correlation
    db.commit()
    it = execution.preview(db, a, sig)  # advisory (the default): the trade is still offered, the failure is shown
    assert it.plan["risk_mode"] == "advisory" and {c["id"] for c in it.plan["checks"]} == set(risk.V2_LABELS)
    assert next(c for c in it.plan["checks"] if c["id"] == "correlation")["status"] == "fail"
    assert "⚠️ Correlated positions" in execution.preview_text(sig, it)
    assert risk.Plan(**it.plan).checks == it.plan["checks"]  # the stored plan still loads (confirm / AUTO path)
    s.strategies = {**s.strategies, "risk_v2": {"max_same_side": 0, "mode": "enforce"}}
    db.commit()
    with pytest.raises(TradeError, match="correlated positions"):
        execution.preview(db, a, sig)
