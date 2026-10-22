"""Refining the live 1h strategy (trend_pullback_v1) with pre-declared variants, judged by walk-forward folds.

Every variant is replayed on closed 1h candles of the same 12 markets (same costs, same portfolio rules as backtest.py).
A variant only counts as an improvement when it beats the current rules in EACH of the three chronological folds (not just
on average) and is positive overall after costs. Research only. Run: python -m app.tradingos.refine
"""

import json
import sys
from dataclasses import dataclass, replace
from typing import Any

from app.tradingos import backtest as bt
from app.tradingos import history
from app.tradingos import intraday as ix


@dataclass(frozen=True)
class Cfg:
    name: str
    entry: str = "near_ema"  # near_ema (live: within 0.6 ATR of EMA20) | resume (touched EMA20, closed back with a strong candle)
    h4: bool = False  # 4h close on the trend side of the 4h EMA50
    adx_min: float = 0.0  # 1h ADX at least this
    stop_atr: float = 1.5
    exit: str = "fixed"  # fixed: one target at target_atr (live) | r: half at r1, rest at r2, time stop
    target_atr: float = 3.0
    r1: float = 1.0
    r2: float = 2.0
    max_hold_h: int = 48
    rsi_band: bool = True
    macd: bool = True


def candidates(coins: list[str], c: Cfg) -> list[bt.Trade]:
    out: list[bt.Trade] = []
    btc = bt.load("BTC", "1h")
    for coin in coins:
        f, h4, fund = bt.load(coin, "1h"), bt.load(coin, "4h"), history.funding(coin)
        macd_line = _macd_hist(f.c)
        last_exit = 0
        for j in range(210, len(f.t) - 1):
            close_t = f.t[j] + f.interval_ms
            if close_t < last_exit:
                continue
            e20, e50, atr, rsi, adx = f.e20[j], f.e50[j], f.atr[j], f.rsi[j], f.adx[j]
            if None in (e20, e50, atr, rsi, f.e50[j - 5]):
                continue
            assert e20 and e50 and atr and rsi is not None
            p = f.c[j]
            up = e20 > e50 and e50 > (f.e50[j - 5] or e50) and p > e50
            down = e20 < e50 and e50 < (f.e50[j - 5] or e50) and p < e50
            if not (up or down):
                continue
            long = up
            side = "LONG" if long else "SHORT"
            if not 0.25 <= atr / p * 100 <= 5:
                continue
            if c.entry == "near_ema":
                if abs(p - e20) > 0.6 * atr:
                    continue
            else:
                touched = any((f.l[x] <= (f.e20[x] or 0) + 0.1 * atr) if long else (f.h[x] >= (f.e20[x] or 1e18) - 0.1 * atr) for x in range(j - 2, j + 1))
                rng = f.h[j] - f.l[j]
                strong = rng > 0 and ((f.c[j] > f.o[j] and (f.c[j] - f.l[j]) / rng >= 0.6) if long else (f.c[j] < f.o[j] and (f.h[j] - f.c[j]) / rng >= 0.6))
                if not (touched and strong and ((p > e20) if long else (p < e20)) and abs(p - e20) <= 1.0 * atr):
                    continue
            if c.rsi_band and (long and not 40 <= rsi <= 65 or not long and not 35 <= rsi <= 60):
                continue
            if c.macd:
                h, hp = macd_line[j], macd_line[j - 1]
                if h is None or hp is None:
                    continue
                turning = (long and h > hp) or (not long and h < hp)
                if not turning and (long and h <= 0 or not long and h >= 0):
                    continue
            if c.adx_min and (adx is None or adx < c.adx_min):
                continue
            if c.h4:
                k = ix.last_closed(h4, close_t)
                if k < 55 or h4.e50[k] is None or (long and h4.c[k] < h4.e50[k]) or (not long and h4.c[k] > h4.e50[k]):  # type: ignore[operator]
                    continue
            fr = bt._funding_rate_at(fund, close_t)
            if long and fr > 0.0001 or not long and fr < -0.0001:
                continue
            if coin != "BTC":
                bj = ix.last_closed(btc, close_t)
                if bj < 60 or btc.e50[bj] is None or (long and btc.c[bj] < btc.e50[bj]) or (not long and btc.c[bj] > btc.e50[bj]):  # type: ignore[operator]
                    continue
            hi, lo = max(f.h[j - 48:j]), min(f.l[j - 48:j])
            sg = 1 if long else -1
            wall = hi if long else lo
            if 0 < sg * (wall - p) < 0.5 * atr:
                continue
            stop = p - sg * c.stop_atr * atr
            if c.exit == "fixed":
                tgt = p + sg * c.target_atr * atr
                r = bt.simulate(f, j, side, stop, None, 0, c.max_hold_h * 60, coin, fund, fixed_targets=(tgt, tgt))
            else:
                r = bt.simulate(f, j, side, stop, c.r1, c.r2, c.max_hold_h * 60, coin, fund)
            if r is None:
                continue
            out.append(bt.Trade(coin, c.name, side, "-", close_t, r["t_entry"], r["t_exit"], r["entry"], stop, r["r_net"], r["r_gross"],
                                r["cost_r"], r["exit"], r["hold_min"]))
            last_exit = r["t_exit"]
    return out


def _macd_hist(closes: list[float]) -> list[float | None]:
    f, s = ix.ema_s(closes, 12), ix.ema_s(closes, 26)
    line = [(a - b) if a is not None and b is not None else None for a, b in zip(f, s)]
    first = next(i for i, x in enumerate(line) if x is not None)
    sig = ix.ema_s([x for x in line[first:] if x is not None], 9)
    out: list[float | None] = [None] * len(closes)
    for i, sv in enumerate(sig):
        lv = line[first + i]
        if sv is not None and lv is not None:
            out[first + i] = lv - sv
    return out


BASE = Cfg("current_v1")
VARIANTS = [
    BASE,
    replace(BASE, name="resume_entry", entry="resume"),
    replace(BASE, name="h4_trend", h4=True),
    replace(BASE, name="adx20", adx_min=20),
    replace(BASE, name="stop2_target4", stop_atr=2.0, target_atr=4.0),
    replace(BASE, name="partial_1R_2R_12h", exit="r", r1=1.0, r2=2.0, max_hold_h=12),
    replace(BASE, name="resume+h4", entry="resume", h4=True),
    replace(BASE, name="resume+h4+partial", entry="resume", h4=True, exit="r", r1=1.0, r2=2.0, max_hold_h=12),
    replace(BASE, name="h4+adx20+stop2", h4=True, adx_min=20, stop_atr=2.0, target_atr=4.0),
]


def breakout_4h(coins: list[str], n_in: int = 20, n_out: int = 10, stop_atr: float = 2.0, trend: bool = True) -> list[bt.Trade]:
    """Trend-following breakout on CLOSED 4h candles: close beyond the n_in-candle high/low (in the EMA50 direction), initial stop
    stop_atr ATR away, exit when price closes back through the n_out-candle channel (a trailing exit) or hits the stop. Entry at
    the next 4h open. Same costs, funding and portfolio rules."""
    out: list[bt.Trade] = []
    for coin in coins:
        f, fund = bt.load(coin, "4h"), history.funding(coin)
        last_exit = 0
        for j in range(60, len(f.t) - 1):
            close_t = f.t[j] + f.interval_ms
            if close_t < last_exit or f.atr[j] is None or f.e50[j] is None:
                continue
            hi, lo, atr = max(f.h[j - n_in:j]), min(f.l[j - n_in:j]), f.atr[j]
            assert atr
            long = f.c[j] > hi and (not trend or f.c[j] > f.e50[j])  # type: ignore[operator]
            short = f.c[j] < lo and (not trend or f.c[j] < f.e50[j])  # type: ignore[operator]
            if not (long or short):
                continue
            sg, sl = (1 if long else -1), bt.slip(coin)
            entry = f.o[j + 1] * (1 + sg * sl)
            stop = f.c[j] - sg * stop_atr * atr
            if (long and entry <= stop) or (short and entry >= stop):
                continue
            R, exit_px, t_exit = abs(entry - stop), None, None
            for b in range(j + 1, len(f.t)):
                if (long and f.l[b] <= stop) or (short and f.h[b] >= stop):
                    exit_px, t_exit = (min(stop, f.o[b]) if long else max(stop, f.o[b])) * (1 - sg * sl), f.t[b] + f.interval_ms
                    break
                ch = min(f.l[b - n_out + 1:b + 1]) if long else max(f.h[b - n_out + 1:b + 1])
                if b > j + 1 and ((long and f.c[b] < min(f.l[b - n_out:b])) or (short and f.c[b] > max(f.h[b - n_out:b]))):
                    exit_px, t_exit = f.c[b] * (1 - sg * sl), f.t[b] + f.interval_ms
                    break
                del ch
            if exit_px is None or t_exit is None:
                continue
            t_entry = f.t[j + 1]
            fund_cost = sum(r * entry * sg for t, r in fund if t_entry < t <= t_exit)
            net = (exit_px - entry) * sg - entry * bt.FEE - exit_px * bt.FEE - fund_cost
            out.append(bt.Trade(coin, f"breakout4h_{n_in}_{n_out}", "LONG" if long else "SHORT", "-", close_t, t_entry, t_exit, entry, stop,
                                net / R, (exit_px - entry) * sg / R, (entry * bt.FEE + exit_px * bt.FEE + fund_cost) / R, "trail", (t_exit - t_entry) / 60_000))
            last_exit = t_exit
    return out


def run_breakout(coins: list[str]) -> dict[str, Any]:
    f = bt.load("BTC", "4h")
    start, end = f.t[60], f.t[-1]
    folds = [start + (end - start) * i // 3 for i in range(4)]
    rep: dict[str, Any] = {"folds": folds}
    for name, kw in (("breakout_20_10", {}), ("breakout_55_20", {"n_in": 55, "n_out": 20}), ("breakout_20_10_no_trend_filter", {"trend": False})):
        p = bt.portfolio(breakout_4h(coins, **kw))
        rep[name] = {"all": bt.metrics(p), "folds": [bt.metrics([t for t in p if folds[i] <= t.t_entry < folds[i + 1]]) for i in range(3)],
                     "by_coin": bt.by(p, "coin"), "by_side": bt.by(p, "side")}
    return rep


def run(coins: list[str]) -> dict[str, Any]:
    f = bt.load("BTC", "1h")
    start, end = f.t[210], f.t[-1]
    folds = [start + (end - start) * i // 3 for i in range(4)]
    rep: dict[str, Any] = {"folds": folds, "variants": {}}
    base_folds: list[float] = []
    for c in VARIANTS:
        p = bt.portfolio(candidates(coins, c))
        per = [bt.metrics([t for t in p if folds[i] <= t.t_entry < folds[i + 1]]) for i in range(3)]
        exp = [m.get("expectancy_r", 0.0) for m in per]
        if c is BASE:
            base_folds = exp
        allm = bt.metrics(p)
        rep["variants"][c.name] = {"all": allm, "folds": [{k: m.get(k) for k in ("trades", "win_rate", "expectancy_r", "net_return_pct")} for m in per],
                                   "beats_current_every_fold": c is not BASE and all(e > b for e, b in zip(exp, base_folds)),
                                   "positive_overall": allm.get("expectancy_r", -1) > 0, "by_coin": bt.by(p, "coin")}
    return rep


if __name__ == "__main__":
    r = run([x.upper() for x in sys.argv[1:]] or history.COINS)
    (history.DIR / "refine.json").write_text(json.dumps(r, indent=1))
    for n, v in r["variants"].items():
        a = v["all"]
        print(f"{n:22s} n={a.get('trades')} win={a.get('win_rate')} exp={a.get('expectancy_r')} ci={a.get('expectancy_r_90ci')} "
              f"net={a.get('net_return_pct')}% dd={a.get('max_dd_pct')}% hold={a.get('median_hold_min')}m pf={a.get('profit_factor')} "
              f"| folds {[f['expectancy_r'] for f in v['folds']]} | beats every fold: {v['beats_current_every_fold']}")
