"""Kiwi ML: a machine-learning filter on top of the Kiwi Kitchen's 15m setups (meta-labelling).

The entry models in intraday.py decide WHEN there is a setup. This model answers a second question about each setup: given
what the market looked like at that closed candle, how likely is it to finish in profit after fees, slippage and funding?
It can only REMOVE trades the rules found; it never adds one, moves a stop or changes a size.

  data      2 years of Binance USDⓈ-M perpetual history for the 12 research coins (history.fetch_binance: Hyperliquid itself
            only serves 52 days of 15m candles, far too little to learn from). Every closed 15m candle where a kitchen entry
            model fired gives one sample: the best setup, as the live kitchen picks it, replayed with backtest.simulate
            (entry at the next open, stop first inside a candle, Hyperliquid's fees, slippage, real funding).
            Label: the trade's net result > 0R.
  features  FEATURES, computed by features(), the SAME function the live kitchen calls, on closed candles only. Every
            direction-dependent value is signed so that positive means "in the trade's favour".
  model     gradient-boosted decision trees (numpy, depth 3, 150 rounds, learning rate 0.05, 70% row subsampling, quantile
            bins): small and deterministic. Hyper-parameters were picked on TRAIN's inner validation only.
  output    p = the estimated probability that the trade ends in profit. The kitchen's "ml" check passes when p >= the
            model's threshold.

Validation (the rules were fixed before the holdout was ever scored; train() only reports, nothing is tuned on it):
  · chronological split: TRAIN = the first 60% of the period, HOLDOUT = the last 40%
  · the threshold is chosen inside TRAIN only: fit on its first 70%, pick the threshold with the best expectancy on its
    last 30% among those that keep at least half the trades (the degen mode is meant to trade often)
  · on HOLDOUT the kitchen's degen rules are replayed twice, without and with the filter (one trade per coin at a time,
    at most 2 open, daily stop). The filter is VALIDATED when, on HOLDOUT, it improves the expectancy by >= 0.03R, does
    better in at least 3 of 4 chronological windows, keeps >= 100 trades and has an AUC >= 0.53
  · VALIDATED means "it removes losing trades out of sample", NOT "the strategy is profitable": "profitable" is reported
    separately (holdout expectancy with the filter > 0)
  · also reported: the TRAIN model on Hyperliquid's own recent 52 days (another venue, later dates)
  · the shipped model is refit on ALL the data with the chosen threshold

Owner switch (TsSettings.strategies["intraday"]["ml"]): "auto" (the default) filters when the shipped model is VALIDATED,
otherwise it is advisory (the probability is shown, nothing is blocked); "filter" always filters; "shadow" is advisory;
"off" doesn't run it.

Run: python -m app.tradingos.history binance   (once, ~2 min)   then   python -m app.tradingos.ml
"""

import json
import math
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

from app.tradingos import intraday as ix
from app.tradingos.market import INTERVALS

MODEL_PATH = Path(__file__).with_name("ml_model.json")
FEATURES = ("long", "m_A_pullback", "m_B_retest", "m_C_sweep", "m_F_scalp", "trend_regime", "range_regime", "vol_regime",
            "rsi", "ext_atr", "vwap_atr", "relvol", "adx", "atr_pct", "atr_rank", "stop_atr", "ret4_atr", "ret16_atr",
            "range16_atr", "room48_atr", "from48_atr", "body", "upper_wick", "h1_slope", "h1_rsi", "h1_ret4", "h4_trend",
            "hour_sin", "hour_cos", "funding_against", "cost_r", "quality", "r1")
MODES = ("auto", "filter", "shadow", "off")
GBM = {"rounds": 150, "lr": 0.05, "depth": 3, "lam": 10.0, "min_h": 30.0, "sub": 0.7, "bins": 32}
SPLIT = 0.6
MIN_KEEP = 0.5
SOURCE = "binance"  # training history (history.fetch_binance); "" = the Hyperliquid cache


def _n(x: float | None, d: float = 0.0) -> float:
    return d if x is None or not math.isfinite(x) else float(x)


def features(f: ix.Series, i: int, s: ix.Setup, reg: str, h1: ix.Series, j: int, h4: ix.Series, k: int, funding_h: float,
             cost_r: float, q: int) -> list[float]:
    """One setup on the closed 15m candle i (j / k = the last closed 1h / 4h candles at that moment)."""
    sg = 1.0 if s.side == "LONG" else -1.0
    c, atr = f.c[i], _n(f.atr[i], abs(s.ref - s.stop)) or 1e-9
    rng = f.h[i] - f.l[i]
    body = (c - f.o[i]) / rng * sg if rng > 0 else 0.0
    wick = ((f.h[i] - max(c, f.o[i])) if sg > 0 else (min(c, f.o[i]) - f.l[i])) / rng if rng > 0 else 0.0
    past = [a / x for a, x in zip(f.atr[max(0, i - 96):i], f.c[max(0, i - 96):i]) if a]
    rank = sum(1 for a in past if a <= atr / c) / len(past) if past else 0.5
    hi48, lo48 = max(f.h[max(0, i - 48):i] or [c]), min(f.l[max(0, i - 48):i] or [c])
    room, came = ((hi48 - c), (c - lo48)) if sg > 0 else ((c - lo48), (hi48 - c))
    h1_atr = _n(h1.atr[j] if j >= 0 else None, 1e-9) or 1e-9
    h1_slope = (_n(h1.e20[j]) - _n(h1.e20[j - 3], _n(h1.e20[j]))) / h1_atr * sg if j >= 3 else 0.0
    h1_ret = (h1.c[j] - h1.c[j - 4]) / h1_atr * sg if j >= 4 else 0.0
    h4_e50 = h4.e50[k] if k >= 0 else None
    h4_trend = (1.0 if h4.c[k] > h4_e50 else -1.0) * sg if h4_e50 else 0.0
    hour = ((f.t[i] + f.interval_ms) // 3_600_000) % 24
    trend = (reg == "STRONG_BULL" and sg > 0) or (reg == "STRONG_BEAR" and sg < 0)
    return [sg, *(1.0 if s.strategy == m else 0.0 for m in ("A_pullback", "B_retest", "C_sweep", "F_scalp")),
            1.0 if trend else -1.0 if reg in ("STRONG_BULL", "STRONG_BEAR") else 0.0, 1.0 if reg == "RANGE" else 0.0,
            1.0 if reg in ("VOL_EXPANSION", "UNSTABLE") else -1.0 if reg == "VOL_CONTRACTION" else 0.0,
            (_n(f.rsi[i], 50) - 50) / 50 * sg, (c - _n(f.e20[i], c)) / atr * sg, (c - f.vwap[i]) / atr * sg,
            math.log(max(_n(f.relvol[i], 1), 0.05)), _n(f.adx[i], 20) / 50, atr / c * 100, rank, abs(s.ref - s.stop) / atr,
            (c - f.c[i - 4]) / atr * sg if i >= 4 else 0.0, (c - f.c[i - 16]) / atr * sg if i >= 16 else 0.0,
            (max(f.h[max(0, i - 15):i + 1]) - min(f.l[max(0, i - 15):i + 1])) / atr, room / atr, came / atr, body, wick,
            h1_slope, (_n(h1.rsi[j] if j >= 0 else None, 50) - 50) / 50 * sg, h1_ret, h4_trend,
            math.sin(2 * math.pi * hour / 24), math.cos(2 * math.pi * hour / 24), funding_h * sg * 1e4, cost_r, q / 100, s.r1]


# ---------------------------------------------------------------- the model: gradient-boosted trees (numpy)
def _binarize(x: np.ndarray, bins: list[np.ndarray]) -> np.ndarray:
    return np.stack([np.searchsorted(b, x[:, n], side="right") for n, b in enumerate(bins)], 1)


def _tree(xb: np.ndarray, g: np.ndarray, h: np.ndarray, p: dict[str, Any]) -> Any:
    """One regression tree on the gradient / hessian of the log loss: a leaf is a float, a split is [feature, bin, left, right]."""
    nb = p["bins"] + 1

    def grow(idx: np.ndarray, depth: int) -> Any:
        gs_, hs_ = g[idx].sum(), h[idx].sum()
        leaf = float(-gs_ / (hs_ + p["lam"]))
        if depth == p["depth"] or len(idx) < 2 * p["min_h"]:
            return leaf
        base, best, at = gs_ * gs_ / (hs_ + p["lam"]), 1e-9, None
        for n in range(xb.shape[1]):
            col = xb[idx, n]
            gl, hl = np.cumsum(np.bincount(col, g[idx], nb))[:-1], np.cumsum(np.bincount(col, h[idx], nb))[:-1]
            gr, hr = gs_ - gl, hs_ - hl
            gain = np.where((hl >= p["min_h"]) & (hr >= p["min_h"]), gl * gl / (hl + p["lam"]) + gr * gr / (hr + p["lam"]) - base, 0)
            b = int(gain.argmax())
            if gain[b] > best:
                best, at = float(gain[b]), (n, b)
        if at is None:
            return leaf
        n, b = at
        go_left = xb[idx, n] <= b
        return [n, b, grow(idx[go_left], depth + 1), grow(idx[~go_left], depth + 1)]

    return grow(np.arange(len(g)), 0)


def _apply(tree: Any, xb: np.ndarray) -> np.ndarray:
    out = np.empty(len(xb))

    def walk(t: Any, idx: np.ndarray) -> None:
        if not isinstance(t, list):
            out[idx] = t
            return
        left = xb[idx, t[0]] <= t[1]
        walk(t[2], idx[left])
        walk(t[3], idx[~left])

    walk(tree, np.arange(len(xb)))
    return out


def fit(x: np.ndarray, y: np.ndarray, params: dict[str, Any] | None = None, seed: int = 7) -> dict[str, Any]:
    p = {**GBM, **(params or {})}
    rng = np.random.default_rng(seed)
    bins = [np.unique(np.quantile(x[:, n], np.linspace(0, 1, p["bins"] + 1)[1:-1])) for n in range(x.shape[1])]
    xb = _binarize(x, bins)
    rate = float(np.clip(y.mean(), 0.01, 0.99))
    f0 = math.log(rate / (1 - rate))
    f = np.full(len(y), f0)
    trees = []
    for _ in range(p["rounds"]):
        pr = 1 / (1 + np.exp(-f))
        rows = rng.random(len(y)) < p["sub"]
        t = _tree(xb[rows], (pr - y)[rows], (pr * (1 - pr))[rows], p)
        trees.append(t)
        f += p["lr"] * _apply(t, xb)
    return {"kind": "gbm", "params": p, "bins": [b.tolist() for b in bins], "f0": f0, "trees": trees}


def predict(m: dict[str, Any], x: Any) -> np.ndarray:
    xb = _binarize(np.atleast_2d(np.asarray(x, dtype=float)), [np.asarray(b) for b in m["bins"]])
    f = np.full(len(xb), m["f0"])
    for t in m["trees"]:
        f += m["params"]["lr"] * _apply(t, xb)
    return 1 / (1 + np.exp(-f))


def importance(m: dict[str, Any]) -> list[tuple[str, int]]:
    """How often each feature is used for a split (a rough, honest view of what the trees look at)."""
    counts = [0] * len(FEATURES)

    def walk(t: Any) -> None:
        if isinstance(t, list):
            counts[t[0]] += 1
            walk(t[2])
            walk(t[3])

    for t in m["trees"]:
        walk(t)
    return sorted(((FEATURES[n], c) for n, c in enumerate(counts) if c), key=lambda kv: -kv[1])


def auc(p: np.ndarray, y: np.ndarray) -> float | None:
    pos, neg = p[y == 1], p[y == 0]
    if not len(pos) or not len(neg):
        return None
    order = np.argsort(np.concatenate([pos, neg]), kind="stable")
    ranks = np.empty(len(order))
    ranks[order] = np.arange(1, len(order) + 1)
    return float((ranks[:len(pos)].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))


# ---------------------------------------------------------------- live use
@lru_cache(maxsize=1)
def model() -> dict[str, Any] | None:
    try:
        m = json.loads(MODEL_PATH.read_text())
    except (OSError, ValueError):
        return None
    return m if m.get("features") == list(FEATURES) and m.get("kind") == "gbm" else None  # trained on other features: ignored


def mode(cfg: dict[str, Any]) -> str:
    v = str(cfg.get("ml") or "auto").lower()
    return v if v in MODES else "auto"


def filtering(cfg: dict[str, Any], m: dict[str, Any] | None = None) -> bool:
    m = m if m is not None else model()
    md = mode(cfg)
    return m is not None and (md == "filter" or (md == "auto" and bool(m.get("validated"))))


def check(cfg: dict[str, Any], x: list[float] | None) -> tuple[str, str, float | None]:
    """The kitchen's "ml" check: (status, detail, p). Advisory = "skip" with the probability in the detail."""
    md, m = mode(cfg), model()
    if md == "off":
        return "skip", "off (owner setting)", None
    if m is None:
        return "skip", "no model trained yet", None
    if x is None:
        return "skip", "", None
    p = float(predict(m, x)[0])
    thr = float(m["threshold"])
    text = f"{p * 100:.0f}% estimated chance of profit (needs {thr * 100:.0f}%)"
    if filtering(cfg, m):
        return ("pass" if p >= thr else "fail"), text, p
    return "skip", f"{text} · advisory only ({'not validated on the holdout' if not m.get('validated') else 'shadow mode'})", p


def summary() -> dict[str, Any] | None:
    """What the Earn page / admin show about the model: its validation, never the weights."""
    m = model()
    if m is None:
        return None
    keys = ("trained_at", "period", "split", "samples", "threshold", "validated", "profitable", "holdout", "hyperliquid", "top_features", "source")
    return {k: m.get(k) for k in keys}


# ---------------------------------------------------------------- research: dataset, validation, training
@dataclass
class Sample:
    coin: str
    t_signal: int
    t_entry: int
    t_exit: int
    r_net: float
    x: list[float]
    gates: bool  # passed the kitchen's quality / cost / funding gates
    trade: Any  # the backtest.Trade


def dataset(coins: list[str], cfg: dict[str, Any], source: str = SOURCE) -> list[Sample]:
    """One sample per closed 15m candle where an enabled model fired (the best setup, as the kitchen picks it)."""
    from app.tradingos import backtest as bt
    from app.tradingos import history, kitchen

    out = []
    for coin in coins:
        try:
            c15 = history.candles(coin, "15m", source)
            h1, h4 = (ix.series(history.candles(coin, iv, source), INTERVALS[iv] * 1000) for iv in ("1h", "4h"))
        except OSError:
            continue  # no history for this coin from this source
        f15 = ix.series(c15, kitchen.M15)
        fund = history.funding(coin, source)
        for i in range(210, len(f15.t) - 2):
            close_t = f15.t[i] + kitchen.M15
            j, k = ix.last_closed(h1, close_t), ix.last_closed(h4, close_t)
            reg, _ = ix.regime(h1, h4, j, k)
            h1_dir = "up" if (h1.e20[j] or 0) > (h1.e50[j] or 0) else "down"
            found, _ = ix.setups(f15, i, reg, h1_dir, cfg["models"], float(cfg["min_stop_pct"]))
            if not found:
                continue
            rate = bt._funding_rate_at(fund, close_t)
            sp = kitchen.slip(coin) * 2e4
            best = max(found, key=lambda s: ix.quality(s, sp, kitchen.cost_r(s.ref, s.stop, coin, sp, rate, s.side, s.max_hold_min)))
            c_r = kitchen.cost_r(best.ref, best.stop, coin, sp, rate, best.side, best.max_hold_min)
            q = ix.quality(best, sp, c_r)
            against = rate if best.side == "LONG" else -rate
            gates = q >= cfg["min_quality"] and c_r <= cfg["max_cost_r"] and best.r1 - c_r >= cfg["min_net_r"] and against <= cfg["max_funding_h"]
            r = bt.simulate(f15, i, best.side, best.stop, best.r1, best.r2, best.max_hold_min, coin, fund)
            if r is None:
                continue
            tr = bt.Trade(coin, best.strategy, best.side, reg, close_t, r["t_entry"], r["t_exit"], r["entry"], best.stop, r["r_net"],
                          r["r_gross"], r["cost_r"], r["exit"], r["hold_min"], q)
            out.append(Sample(coin, close_t, r["t_entry"], r["t_exit"], r["r_net"], features(f15, i, best, reg, h1, j, h4, k, rate, c_r, q),
                              gates, tr))
    return sorted(out, key=lambda s: s.t_signal)


def replay(samples: list[Sample], keep: np.ndarray | None = None) -> list[Any]:
    """The kitchen's live rules on these samples: gates passed (and the filter, when given), one trade per coin at a time,
    then the portfolio limits of backtest.py."""
    from app.tradingos import backtest as bt

    busy: dict[str, int] = {}
    cands = []
    for n, s in enumerate(samples):
        if not s.gates or (keep is not None and not keep[n]) or s.t_signal < busy.get(s.coin, 0):
            continue
        busy[s.coin] = s.t_exit
        cands.append(s.trade)
    return bt.portfolio(cands)


def _xy(samples: list[Sample]) -> tuple[np.ndarray, np.ndarray]:
    return np.array([s.x for s in samples], dtype=float), np.array([1.0 if s.r_net > 0 else 0.0 for s in samples])


def _exp(ts: list[Any]) -> float | None:
    return round(float(np.mean([t.r_net for t in ts])), 3) if ts else None


def choose_threshold(train: list[Sample]) -> tuple[float, list[dict[str, Any]]]:
    """Inside TRAIN only: fit on its first 70%, try thresholds on its last 30%."""
    cut = int(len(train) * 0.7)
    a, b = train[:cut], train[cut:]
    p = predict(fit(*_xy(a)), _xy(b)[0])
    base = len(replay(b))
    grid = []
    for thr in np.round(np.arange(0.30, 0.56, 0.01), 2):
        ts = replay(b, p >= thr)
        grid.append({"threshold": float(thr), "trades": len(ts), "expectancy_r": _exp(ts)})
    ok = [g for g in grid if g["trades"] >= MIN_KEEP * base and g["expectancy_r"] is not None]
    best = max(ok, key=lambda g: g["expectancy_r"]) if ok else {"threshold": 0.0}
    return float(best["threshold"]), grid


def _windows(base: list[Any], filt: list[Any], start: int, end: int, n: int = 4) -> list[dict[str, Any]]:
    step = (end - start) / n
    out = []
    for w in range(n):
        lo, hi = start + w * step, start + (w + 1) * step
        b, f = [t for t in base if lo <= t.t_entry < hi], [t for t in filt if lo <= t.t_entry < hi]
        out.append({"window": w + 1, "without_ml": {"trades": len(b), "expectancy_r": _exp(b)}, "with_ml": {"trades": len(f), "expectancy_r": _exp(f)}})
    return out


def train(coins: list[str], cfg: dict[str, Any] | None = None, source: str = SOURCE) -> dict[str, Any]:
    from app.tradingos import backtest as bt
    from app.tradingos import kitchen

    cfg = cfg or {**kitchen.DEFAULTS, **kitchen.DEGEN}
    samples = dataset(coins, cfg, source)
    if len(samples) < 2000:
        raise RuntimeError(f"only {len(samples)} samples: download the history first (python -m app.tradingos.history binance)")
    start, end = samples[0].t_signal, samples[-1].t_signal
    cut = int(start + (end - start) * SPLIT)
    tr, ho = [s for s in samples if s.t_signal < cut], [s for s in samples if s.t_signal >= cut]
    thr, grid = choose_threshold(tr)
    m_train = fit(*_xy(tr))
    xh, yh = _xy(ho)
    ph = predict(m_train, xh)
    base, filt = replay(ho), replay(ho, ph >= thr)
    a = auc(ph, yh)
    eb, ef = _exp(base), _exp(filt)
    wins = _windows(base, filt, cut, end)
    better = sum(1 for w in wins if (w["with_ml"]["expectancy_r"] or -9) > (w["without_ml"]["expectancy_r"] or -9))
    validated = bool(eb is not None and ef is not None and ef - eb >= 0.03 and better >= 3 and len(filt) >= 100 and (a or 0) >= 0.53)
    hl: dict[str, Any] = {}
    hl_samples = dataset(coins, cfg, "")  # Hyperliquid's own recent history: another venue, later dates
    if hl_samples:
        xl, yl = _xy(hl_samples)
        pl = predict(m_train, xl)
        hb, hf = replay(hl_samples), replay(hl_samples, pl >= thr)
        hl = {"samples": len(hl_samples), "auc": round(auc(pl, yl) or 0, 3), "without_ml": bt.metrics(hb), "with_ml": bt.metrics(hf)}
    final = fit(*_xy(samples))
    day = lambda ms: datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")  # noqa: E731
    return {**final, "features": list(FEATURES), "threshold": thr, "validated": validated, "profitable": bool(ef is not None and ef > 0),
            "trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "source": f"{source or 'hyperliquid'} perps, 15m",
            "coins": coins, "models": cfg["models"], "period": [day(start), day(end)], "split": day(cut), "samples": len(samples),
            "train_samples": len(tr), "holdout_samples": len(ho), "base_win_rate": round(float(_xy(samples)[1].mean()), 3),
            "holdout": {"auc": round(a, 3) if a is not None else None, "without_ml": bt.metrics(base), "with_ml": bt.metrics(filt),
                        "windows": wins, "windows_better": better},
            "hyperliquid": hl, "threshold_grid": grid, "top_features": [{"feature": k, "splits": v} for k, v in importance(final)[:10]]}


if __name__ == "__main__":
    from app.tradingos import history

    res = train([c.upper() for c in sys.argv[1:]] or history.COINS)
    MODEL_PATH.write_text(json.dumps(res, separators=(",", ":")))
    for where, h in (("holdout", res["holdout"]), ("hyperliquid", res["hyperliquid"])):
        for name in ("without_ml", "with_ml"):
            x = h.get(name) or {}
            print(f"{where:11s} {name:10s} trades {x.get('trades')} · win {x.get('win_rate')}% · expectancy {x.get('expectancy_r')}R "
                  f"{x.get('expectancy_r_90ci')} · {x.get('trades_per_day')}/day")
        print(f"{where:11s} AUC {h.get('auc')}")
    print("windows:", [(w["without_ml"]["expectancy_r"], w["with_ml"]["expectancy_r"]) for w in res["holdout"]["windows"]])
    print(f"threshold {res['threshold']} · VALIDATED {res['validated']} · profitable {res['profitable']} · {res['samples']} samples {res['period']}")
    print("top features:", ", ".join(f"{f['feature']} {f['splits']}" for f in res["top_features"]))
    print(f"written to {MODEL_PATH} ({MODEL_PATH.stat().st_size // 1024} KB)")
