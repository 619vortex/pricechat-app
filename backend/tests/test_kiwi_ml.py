"""Kiwi ML: the gradient-boosted filter on the kitchen's 15m setups. The trees learn a real pattern and stay deterministic,
the owner modes (auto / filter / shadow / off) decide whether it blocks or only advises, the kitchen shows its estimate and
burns a setup only while it filters, and the shipped model file matches the live features. No network."""

import json
from typing import Any

import numpy as np
import pytest

from app.tradingos import bot, kitchen, ml
from app.tradingos import intraday as ix

from tests.test_kiwi_kitchen import BASE, KitchenMD, a_setup, series


def synthetic(n: int = 3000, seed: int = 1) -> tuple[np.ndarray, np.ndarray]:
    """TEST FIXTURE: wins depend on two features (one of them non-linearly), the rest is noise."""
    rng = np.random.default_rng(seed)
    x = rng.normal(size=(n, len(ml.FEATURES)))
    logit = 1.5 * x[:, 3] - 1.2 * (np.abs(x[:, 7]) > 1)
    return x, (rng.random(n) < 1 / (1 + np.exp(-logit))).astype(float)


def test_trees_learn_a_real_pattern_and_are_deterministic() -> None:
    x, y = synthetic()
    m = ml.fit(x[:2000], y[:2000], {"rounds": 60})
    p = ml.predict(m, x[2000:])
    assert (ml.auc(p, y[2000:]) or 0) > 0.75
    again = ml.fit(x[:2000], y[:2000], {"rounds": 60})
    assert np.allclose(p, ml.predict(again, x[2000:]))
    stored = json.loads(json.dumps(m))  # the shipped file is JSON
    assert np.allclose(p, ml.predict(stored, x[2000:]))
    assert {f for f, _ in ml.importance(m)[:2]} == {ml.FEATURES[3], ml.FEATURES[7]}


def test_auc_is_a_ranking_measure() -> None:
    y = np.array([0, 0, 1, 1.0])
    assert ml.auc(np.array([0.1, 0.2, 0.8, 0.9]), y) == 1.0
    assert ml.auc(np.array([0.9, 0.8, 0.2, 0.1]), y) == 0.0
    assert ml.auc(np.array([0.5, 0.5]), np.array([1.0, 1.0])) is None


def fake_model(monkeypatch: pytest.MonkeyPatch, validated: bool, threshold: float, p: float) -> None:
    """TEST FIXTURE: a model whose estimate is always p."""
    m = {"features": list(ml.FEATURES), "kind": "gbm", "threshold": threshold, "validated": validated, "bins": [[0.0]] * len(ml.FEATURES),
         "f0": float(np.log(p / (1 - p))), "trees": [], "params": {"lr": 0.05}, "holdout": {"auc": 0.55}}
    monkeypatch.setattr(ml, "model", lambda: m)


def test_modes_decide_whether_it_blocks_or_only_advises(monkeypatch: pytest.MonkeyPatch) -> None:
    x = [0.0] * len(ml.FEATURES)
    fake_model(monkeypatch, validated=False, threshold=0.45, p=0.40)
    st, detail, p = ml.check({"ml": "auto"}, x)
    assert st == "skip" and "40%" in detail and "advisory" in detail and p == pytest.approx(0.40)
    assert ml.check({"ml": "filter"}, x)[0] == "fail"
    assert ml.check({"ml": "shadow"}, x)[0] == "skip"
    assert ml.check({"ml": "off"}, x) == ("skip", "off (owner setting)", None)
    assert ml.check({"ml": "nonsense"}, x)[0] == "skip"  # unknown = auto
    fake_model(monkeypatch, validated=True, threshold=0.45, p=0.40)
    assert ml.check({}, x)[0] == "fail" and ml.filtering({})
    fake_model(monkeypatch, validated=True, threshold=0.35, p=0.40)
    assert ml.check({}, x)[0] == "pass"
    monkeypatch.setattr(ml, "model", lambda: None)
    assert ml.check({}, x) == ("skip", "no model trained yet", None) and not ml.filtering({"ml": "filter"})


@pytest.fixture
def setups(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(kitchen.ix, "setups", a_setup)


def test_kitchen_shows_the_estimate_and_burns_only_while_filtering(monkeypatch: pytest.MonkeyPatch, setups: None) -> None:
    md = KitchenMD(BASE + 60_000)
    cfg = dict(kitchen.DEFAULTS)
    fake_model(monkeypatch, validated=False, threshold=0.45, p=0.40)
    ev = kitchen.evaluate("BTC", *series(md), md.market("BTC"), md.now_ms, cfg)
    assert ev.state == "READY" and ev.setup and ev.setup["ml_p"] == pytest.approx(0.40, abs=1e-3)
    c = next(c for c in ev.checks if c["id"] == "ml")
    assert c["status"] == "skip" and "advisory" in c["detail"]
    ev = kitchen.evaluate("BTC", *series(md), md.market("BTC"), md.now_ms, {**cfg, "ml": "filter"})
    assert ev.state == "BURNT" and ev.reason.startswith("Kiwi ML") and "40%" in ev.reason
    fake_model(monkeypatch, validated=True, threshold=0.35, p=0.40)
    ev = kitchen.evaluate("BTC", *series(md), md.market("BTC"), md.now_ms, cfg)
    assert ev.state == "READY" and next(c for c in ev.checks if c["id"] == "ml")["status"] == "pass" and ev.passed == 8


def test_the_owner_switch_reaches_the_kitchen_config() -> None:
    class S:
        strategies = {"intraday": {"ml": "filter"}}

    assert kitchen.config(S())["ml"] == "filter"
    S.strategies = {}
    assert kitchen.config(S())["ml"] == "auto"


def test_the_order_up_card_carries_the_estimate() -> None:
    class Sig:
        number, coin, side, status, last_price = 7, "BTC", "LONG", "ACTIVE", None
        entry_lo = entry_hi = 100.0
        stop, target, strategy = 99.0, 101.0, kitchen.STRATEGY
        analysis: dict[str, Any] = {"model": "F_scalp", "mode": "degen", "market_entry": True, "max_hold_min": 90, "ml_p": 0.473, "ml_filter": False}

    text = bot.kitchen_announcement(Sig(), "WAITING")  # type: ignore[arg-type]
    assert text and "🧠 Kiwi ML: 47% estimated chance of profit (advisory)" in text
    Sig.analysis = {**Sig.analysis, "ml_filter": True}
    assert "(advisory)" not in (bot.kitchen_announcement(Sig(), "WAITING") or "")  # type: ignore[arg-type]
    Sig.analysis = {"model": "F_scalp"}
    assert "Kiwi ML" not in (bot.kitchen_announcement(Sig(), "WAITING") or "")  # type: ignore[arg-type]


def test_features_are_signed_by_side_and_match_the_names(setups: None) -> None:
    md = KitchenMD(BASE + 60_000)
    f15, _, h1, h4 = series(md)
    i, j, k = len(f15.t) - 1, len(h1.t) - 1, len(h4.t) - 1
    c = f15.c[i]
    long = ix.Setup("F_scalp", "LONG", c, c * 0.99, 1.0, 1.8, 90)
    short = ix.Setup("F_scalp", "SHORT", c, c * 1.01, 1.0, 1.8, 90)
    a = ml.features(f15, i, long, "STRONG_BULL", h1, j, h4, k, 0.00001, 0.1, 60)
    b = ml.features(f15, i, short, "STRONG_BULL", h1, j, h4, k, 0.00001, 0.1, 60)
    assert len(a) == len(ml.FEATURES) and all(np.isfinite(a))
    n = ml.FEATURES.index
    assert a[n("long")] == 1 and b[n("long")] == -1 and a[n("ret4_atr")] == -b[n("ret4_atr")] and a[n("trend_regime")] == 1 and b[n("trend_regime")] == -1


def test_the_shipped_model_matches_the_live_features_and_reports_its_holdout() -> None:
    ml.model.cache_clear()
    m = ml.model()
    assert m is not None, "app/tradingos/ml_model.json is missing or was trained on other features: python -m app.tradingos.ml"
    assert len(m["bins"]) == len(ml.FEATURES) and 0 < m["threshold"] < 1
    h = m["holdout"]
    assert h["without_ml"]["trades"] > 100 and h["with_ml"]["trades"] > 0 and m["samples"] > 2000
    assert m["validated"] == (h["with_ml"]["expectancy_r"] - h["without_ml"]["expectancy_r"] >= 0.03 and h["windows_better"] >= 3
                              and h["with_ml"]["trades"] >= 100 and h["auc"] >= 0.53)
    s = ml.summary()
    assert s and "trees" not in s and "bins" not in s
