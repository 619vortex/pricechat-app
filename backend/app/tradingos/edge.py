"""Edge research: four new ideas, declared BEFORE any of them was run (2026-10-18), judged by one fixed rule.

Why: on 2 years of history every kitchen 15m mode loses after costs. Before costs its setups average ~0.00R, so the
question is whether ANY simple, known effect survives costs on these markets. Nothing here trades: it reports.

Data: Binance USDⓈ-M perps, 2024-10 → 2026-09 (history.fetch_binance), 12 coins; daily candles are built from complete 4h
candles; funding is the real funding (spread hourly). Second venue check: Hyperliquid's own 4h candles (2.3 years) and its
hourly funding where cached.

The ideas (parameters fixed here, never tuned after a run):
  maker_degen    the live degen 15m setups, but entered with a LIMIT order at the setup close (filled only if price comes
                 back to it within 2 candles: the trades that run away are missed, as they would be), take-profits as limit
                 orders (maker 0.015%, no slippage), stop and time exit at market (taker 0.045% + slippage). Judged in R.
  tsmom_30d      daily time-series momentum: each coin long if its 30-day return is positive, short if negative; positions
                 sized by inverse 30-day volatility, gross exposure 1; rebalanced daily at the UTC close.
  xsmom_14d      weekly cross-sectional momentum: every 7 days, long the 3 coins with the best 14-day return and short the
                 3 worst, equal weight (market-neutral), held for the week.
  funding_fade   crowding fade: a coin whose 3-day average funding is more than 2 standard deviations above its trailing
                 90-day mean (crowded longs) is shorted, more than 2 below is bought; daily, equal weight among active.

Costs on every change of position: taker fee 0.045% + slippage (0.02% BTC/ETH, 0.05% others) per side; funding paid or
received on every open position, every day.

Decision rule: an idea PASSES only if, on BOTH the first 60% (TRAIN) and the last 40% (HOLDOUT) of the period, it is
profitable after costs and funding and its annualised Sharpe is above 0.5 (for maker_degen: expectancy > 0 on both, with
at least 100 holdout trades), AND it is not negative on Hyperliquid's own history. Anything else: FAIL.
Run: python -m app.tradingos.edge
"""

import json
import math
import sys
from typing import Any

import numpy as np

from app.tradingos import history
from app.tradingos.market import Candle

DAY = 86_400_000
FEE, MAKER = 0.00045, 0.00015
SLIP = {"BTC": 0.0002, "ETH": 0.0002}
SPLIT = 0.6


def slip(coin: str) -> float:
    return SLIP.get(coin, 0.0005)


# ---------------------------------------------------------------- daily panel
def daily(coin: str, source: str) -> list[Candle]:
    from app.tradingos import kitchen

    return kitchen.aggregate(history.candles(coin, "4h", source), DAY)


def panel(coins: list[str], source: str) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[str]]:
    """(days, close[d, c], funding[d, c] = the sum of that day's hourly funding) with NaN where a coin has no candle."""
    series, fund = {}, {}
    for c in coins:
        try:
            series[c] = {x.t: x.c for x in daily(c, source)}
        except OSError:
            continue
        f: dict[int, float] = {}
        for t, r in history.funding(c, source):
            f[t // DAY * DAY] = f.get(t // DAY * DAY, 0.0) + r
        fund[c] = f
    have = [c for c in coins if c in series]
    days = np.array(sorted(set().union(*[set(series[c]) for c in have])))
    close = np.array([[series[c].get(int(d), np.nan) for c in have] for d in days])
    fr = np.array([[fund[c].get(int(d), 0.0) for c in have] for d in days])
    return days, close, fr, have


def run(weights: np.ndarray, close: np.ndarray, fr: np.ndarray, coins: list[str]) -> np.ndarray:
    """Daily portfolio returns: weights decided at the close of day d earn day d+1's return, minus costs on the change
    and the funding of day d+1 (longs pay positive funding)."""
    ret = np.nan_to_num(close[1:] / close[:-1] - 1)
    w = np.nan_to_num(weights[:-1])
    prev = np.vstack([np.zeros((1, w.shape[1])), w[:-1]])
    cost = np.abs(w - prev) @ np.array([FEE + slip(c) for c in coins])
    return (w * ret).sum(1) - cost - (w * fr[1:]).sum(1)


def stats(r: np.ndarray) -> dict[str, Any]:
    if len(r) < 20:
        return {"days": len(r)}
    eq = np.cumprod(1 + r)
    sd = r.std()
    return {"days": len(r), "total_return_pct": round((eq[-1] - 1) * 100, 1), "annual_return_pct": round((eq[-1] ** (365 / len(r)) - 1) * 100, 1),
            "sharpe": round(float(r.mean() / sd * math.sqrt(365)), 2) if sd > 0 else None,
            "max_dd_pct": round(float((1 - eq / np.maximum.accumulate(eq)).max() * 100), 1)}


def _norm(w: np.ndarray) -> np.ndarray:
    g = np.abs(w).sum(1, keepdims=True)
    return np.divide(w, g, out=np.zeros_like(w), where=g > 0)


def tsmom(close: np.ndarray, look: int = 30) -> np.ndarray:
    w = np.zeros_like(close)
    rets = close[1:] / close[:-1] - 1
    for d in range(look, len(close)):
        past = close[d - look]
        sig = np.where(np.isnan(past) | np.isnan(close[d]), 0.0, np.sign(close[d] / past - 1))
        vol = np.nanstd(rets[d - look:d], axis=0)
        w[d] = np.where(vol > 0, sig / np.where(vol > 0, vol, 1), 0.0)
    return _norm(w)


def xsmom(close: np.ndarray, look: int = 14, hold: int = 7, n: int = 3) -> np.ndarray:
    w = np.zeros_like(close)
    cur = np.zeros(close.shape[1])
    for d in range(look, len(close)):
        if (d - look) % hold == 0:
            r = close[d] / close[d - look] - 1
            ok = np.where(~np.isnan(r))[0]
            cur = np.zeros(close.shape[1])
            if len(ok) >= 2 * n:
                order = ok[np.argsort(r[ok])]
                cur[order[-n:]], cur[order[:n]] = 1.0, -1.0
        w[d] = cur
    return _norm(w)


def funding_fade(close: np.ndarray, fr: np.ndarray, avg: int = 3, base: int = 90, z: float = 2.0) -> np.ndarray:
    w = np.zeros_like(close)
    for d in range(base + avg, len(close)):
        now = fr[d - avg + 1:d + 1].mean(0)
        hist = np.array([fr[x - avg + 1:x + 1].mean(0) for x in range(d - base, d - avg + 1)])
        mu, sd = hist.mean(0), hist.std(0)
        score = np.divide(now - mu, sd, out=np.zeros_like(mu), where=sd > 0)
        w[d] = np.where(np.isnan(close[d]), 0.0, np.where(score > z, -1.0, np.where(score < -z, 1.0, 0.0)))
    return _norm(w)


# ---------------------------------------------------------------- maker entries on the degen setups
TARGETS = {"A_pullback": (1.0, 2.0, 180), "B_retest": (1.0, 2.0, 180), "C_sweep": (1.5, 2.5, 180), "F_scalp": (1.0, 1.8, 90)}


def maker_trade(f: Any, i: int, side: str, stop: float, model: str, coin: str, fund: list[tuple[int, float]], wait: int = 2) -> float | None:
    """One degen setup entered with a limit at the setup close. Returns the net R, or None when never filled."""
    r1, r2, hold = TARGETS[model]
    long, sg, limit = side == "LONG", (1 if side == "LONG" else -1), f.c[i]
    R = abs(limit - stop)
    if R <= 0:
        return None
    fill = next((b for b in range(i + 1, min(i + 1 + wait, len(f.t))) if (f.l[b] <= limit if long else f.h[b] >= limit)), None)
    if fill is None:
        return None
    tp1, tp2 = limit + sg * r1 * R, limit + sg * r2 * R
    rem, done1, legs, t0 = 1.0, False, [], f.t[fill]
    for b in range(fill, len(f.t)):
        end = f.t[b] + f.interval_ms
        if (f.l[b] <= stop) if long else (f.h[b] >= stop):  # the stop first inside a candle (also on the fill candle)
            px = min(stop, f.o[b]) if long and b > fill else max(stop, f.o[b]) if b > fill else stop
            legs.append((end, px * (1 - sg * slip(coin)), rem, FEE))
            rem = 0
            break
        if b > fill and not done1 and ((f.h[b] >= tp1) if long else (f.l[b] <= tp1)):
            legs.append((end, tp1, 0.5, MAKER))
            rem, done1 = 0.5, True
        if b > fill and done1 and ((f.h[b] >= tp2) if long else (f.l[b] <= tp2)):
            legs.append((end, tp2, rem, MAKER))
            rem = 0
            break
        if (end - t0) / 60_000 >= hold:
            legs.append((end, f.c[b] * (1 - sg * slip(coin)), rem, FEE))
            rem = 0
            break
    if rem > 0:
        return None
    gross = sum((px - limit) * sg * fr for _, px, fr, _ in legs)
    fees = limit * MAKER + sum(px * fee * fr for _, px, fr, fee in legs)
    funding = sum(rate * limit * sg * sum(fr for ft, _, fr, _ in legs if ft >= t) for t, rate in fund if t0 < t <= legs[-1][0])
    return (gross - fees - funding) / R


def maker_degen(source: str = "binance") -> dict[str, Any]:
    from app.tradingos import backtest as bt
    from app.tradingos import intraday as ix
    from app.tradingos import kitchen, ml

    cfg = {**kitchen.DEFAULTS, **kitchen.DEGEN}
    samples = ml.dataset(cfg["coins"], cfg, source)
    by_coin: dict[str, Any] = {}
    taken, busy = [], {}
    for s in samples:
        if not s.gates or s.t_signal < busy.get(s.coin, 0):
            continue
        if s.coin not in by_coin:
            f = ix.series(history.candles(s.coin, "15m", source), kitchen.M15)
            by_coin[s.coin] = (f, {t: n for n, t in enumerate(f.t)}, history.funding(s.coin, source))
        f, idx, fund = by_coin[s.coin]
        i = idx[s.t_signal - kitchen.M15]
        r = maker_trade(f, i, s.trade.side, s.trade.stop, s.trade.strategy, s.coin, fund)
        busy[s.coin] = s.t_exit
        if r is not None:
            taken.append(bt.Trade(s.coin, s.trade.strategy, s.trade.side, s.trade.regime, s.t_signal, s.t_entry, s.t_exit, f.c[i],
                                  s.trade.stop, r, r, 0.0, "maker", s.trade.hold_min, s.trade.score))
    port = bt.portfolio(taken)
    start, end = samples[0].t_signal, samples[-1].t_signal
    a, b = bt.split(port, int(start + (end - start) * SPLIT))
    keep = lambda m: {k: m.get(k) for k in ("trades", "win_rate", "expectancy_r", "expectancy_r_90ci", "trades_per_day")}  # noqa: E731
    return {"all": keep(bt.metrics(port)), "train": keep(bt.metrics(a)), "holdout": keep(bt.metrics(b)),
            "taker_baseline": keep(bt.metrics(ml.replay(samples)))}


# ---------------------------------------------------------------- report
def report(coins: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for source in ("binance", ""):
        days, close, fr, have = panel(coins, source)
        if len(days) < 200:
            continue
        cut = int(len(days) * SPLIT)
        name = source or "hyperliquid"
        for idea, w in (("tsmom_30d", tsmom(close)), ("xsmom_14d", xsmom(close)), ("funding_fade", funding_fade(close, fr))):
            r = run(w, close, fr, have)
            out.setdefault(idea, {})[name] = {"all": stats(r), "train": stats(r[:cut]), "holdout": stats(r[cut:]),
                                              "active_days_pct": round(float((np.abs(w).sum(1) > 0).mean() * 100))}
        out.setdefault("_period", {})[name] = [str(np.datetime64(int(days[0]), "ms"))[:10], str(np.datetime64(int(days[-1]), "ms"))[:10]]
    out["maker_degen"] = {"binance": maker_degen("binance"), "hyperliquid": maker_degen("")}
    for idea in ("tsmom_30d", "xsmom_14d", "funding_fade"):
        b, h = out[idea].get("binance", {}), out[idea].get("hyperliquid", {})
        ok = all((b.get(p, {}).get("sharpe") or 0) > 0.5 and (b.get(p, {}).get("total_return_pct") or 0) > 0 for p in ("train", "holdout"))
        out[idea]["verdict"] = "PASS" if ok and (h.get("all", {}).get("total_return_pct") or 0) >= 0 else "FAIL"
    md = out["maker_degen"]
    ok = all((md["binance"][p].get("expectancy_r") or -1) > 0 for p in ("train", "holdout")) and (md["binance"]["holdout"].get("trades") or 0) >= 100
    md["verdict"] = "PASS" if ok and (md["hyperliquid"]["all"].get("expectancy_r") or -1) >= 0 else "FAIL"
    return out


if __name__ == "__main__":
    res = report([c.upper() for c in sys.argv[1:]] or history.COINS)
    (history.DIR / "edge.json").write_text(json.dumps(res, indent=1, default=str))
    print(json.dumps(res, indent=1, default=str))
