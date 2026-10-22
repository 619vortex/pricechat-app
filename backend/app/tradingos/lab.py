"""Strategy lab: the live strategies replayed with the LIVE trade management, plus pre-declared variants, judged out of sample.

Why this exists: backtest.py / refine.py scored the signal model (one exit at the target). Real positions are managed
differently (execution.protect): a stop for the whole size, TP1 for half at the signal's target, TP2 for the rest at 1.5x
the TP1 distance, no time exit. This module replays exactly that, so the numbers describe what a member would have got.

Honest-by-construction (same as backtest.py):
  · decisions on CLOSED candles only; entry at the NEXT candle's open plus slippage; skipped if that opens beyond the stop
  · a candle touching the stop and a target counts as the stop first; a gap through the stop fills at the open
  · taker fee 0.045% on every fill, slippage per side (0.02% BTC/ETH, 0.05% others), real hourly Hyperliquid funding
  · one position per coin; portfolio: at most 2 open, 0.5% of equity risked per trade, no new trades after -2% in a day

Decision rule, written BEFORE running anything (the run only reports; it doesn't tune):
  · data is split chronologically: SELECTION = the first 60%, HOLDOUT = the last 40%. Variants are compared on SELECTION.
  · a variant replaces the live rule only if (1) its SELECTION expectancy beats the live rule's, (2) its HOLDOUT expectancy
    is positive with at least 30 holdout trades, (3) its HOLDOUT expectancy is at least the live rule's, and (4) it is
    implementable with the live execution (fixed stop + TP1/TP2 orders; no trailing-stop engine exists yet).
  · a strategy whose live rule is negative on both parts and has no passing variant is switched off.
Research only. Run: python -m app.tradingos.lab
"""

import bisect
import json
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.tradingos import backtest as bt
from app.tradingos import history
from app.tradingos import intraday as ix
from app.tradingos.refine import _macd_hist

SELECTION_SHARE = 0.6
MIN_HOLDOUT_TRADES = 30


@dataclass(frozen=True)
class Exit:
    """Targets in R from the signal close (R = |close - stop|). Live: TP1 = the signal target (half), TP2 = 1.5x it."""
    tp1_r: float = 2.0
    tp2_r: float = 3.0
    be: bool = False  # stop to the entry once TP1 fills (needs an execution change: listed, not live)
    max_hold_h: float | None = None  # close at market after this (needs an execution change)
    full_at_tp1: bool = False  # one exit for the whole size at TP1


@dataclass
class Funding:
    t: list[int]
    r: list[float]

    def between(self, a: int, b: int) -> float:
        """Sum of the hourly rates paid in (a, b]."""
        i, k = bisect.bisect_right(self.t, a), bisect.bisect_right(self.t, b)
        return sum(self.r[i:k])

    def at(self, t: int) -> float:
        i = bisect.bisect_right(self.t, t) - 1
        return self.r[i] if i >= 0 else 0.0


def funding(coin: str) -> Funding:
    rows = history.funding(coin)
    return Funding([t for t, _ in rows], [r for _, r in rows])


def simulate(f: ix.Series, j: int, side: str, stop: float, ex: Exit, coin: str, fund: Funding) -> dict[str, Any] | None:
    """One trade from the signal on closed candle j, managed like execution.protect (plus the optional variants)."""
    if j + 1 >= len(f.t):
        return None
    long, sg, sl = side == "LONG", (1 if side == "LONG" else -1), bt.slip(coin)
    ref = f.c[j]
    entry = f.o[j + 1] * (1 + sg * sl)
    if (long and entry <= stop) or (not long and entry >= stop) or ref == stop:
        return None
    Rp = abs(ref - stop)
    tp1, tp2 = ref + sg * ex.tp1_r * Rp, ref + sg * ex.tp2_r * Rp
    if (long and tp1 <= entry) or (not long and tp1 >= entry):
        return None  # opened beyond the first target: the live entry-zone check refuses it
    t_entry = f.t[j + 1]
    fills: list[tuple[int, float, float]] = []
    rem, cur, tp1_done, kind = 1.0, stop, False, ""
    first_frac = 1.0 if ex.full_at_tp1 else 0.5
    for b in range(j + 1, len(f.t)):
        tc = f.t[b] + f.interval_ms
        if (long and f.l[b] <= cur) or (not long and f.h[b] >= cur):
            px = min(cur, f.o[b]) if long else max(cur, f.o[b])
            fills.append((tc, px * (1 - sg * sl), rem))
            kind, rem = ("tp1+" if tp1_done else "") + ("be" if tp1_done and ex.be else "stop"), 0.0
            break
        if not tp1_done and ((long and f.h[b] >= tp1) or (not long and f.l[b] <= tp1)):
            fills.append((tc, tp1 * (1 - sg * sl), first_frac))
            rem, tp1_done = rem - first_frac, True
            if rem <= 1e-9:
                kind = "tp1"
                break
            if ex.be:
                cur = entry
        if tp1_done and ((long and f.h[b] >= tp2) or (not long and f.l[b] <= tp2)):
            fills.append((tc, tp2 * (1 - sg * sl), rem))
            kind, rem = "tp1+tp2", 0.0
            break
        if ex.max_hold_h is not None and (tc - t_entry) / 3_600_000 >= ex.max_hold_h:
            fills.append((tc, f.c[b] * (1 - sg * sl), rem))
            kind, rem = ("tp1+" if tp1_done else "") + "time", 0.0
            break
    if rem > 1e-9:
        return None  # data ended first
    R = abs(entry - stop)
    gross = sum((px - entry) * sg * fr for _, px, fr in fills)
    fees = entry * bt.FEE + sum(px * bt.FEE * fr for _, px, fr in fills)
    fund_cost, prev, left = 0.0, t_entry, 1.0
    for ft, _, fr in fills:  # funding on the size still open in each stretch (longs pay positive rates)
        fund_cost += fund.between(prev, ft) * entry * left * sg
        prev, left = ft, left - fr
    net = gross - fees - fund_cost
    return {"entry": entry, "t_entry": t_entry, "t_exit": fills[-1][0], "r_net": net / R, "r_gross": gross / R,
            "cost_r": (fees + fund_cost + entry * sl * 2) / R, "exit": kind, "hold_min": (fills[-1][0] - t_entry) / 60_000}


# ---------------------------------------------------------------- signal generators (closed candle j → side + stop, or None)
Gen = Callable[[ix.Series, int, str, Funding, Any], tuple[str, float] | None]


@dataclass
class Ctx:
    """Per-coin precomputed extras."""
    macd: list[float | None] = field(default_factory=list)
    btc: ix.Series | None = None
    vol20: list[float | None] = field(default_factory=list)


def _crowded(side: str, rate: float) -> bool:
    return side == "LONG" and rate > 0.0001 or side == "SHORT" and rate < -0.0001


def _btc_against(btc: ix.Series | None, t_close: int, side: str, coin: str) -> bool:
    if coin == "BTC" or btc is None:
        return False
    k = ix.last_closed(btc, t_close)
    e = btc.e50[k] if k >= 0 else None
    return e is None or (side == "LONG" and btc.c[k] < e) or (side == "SHORT" and btc.c[k] > e)


def breakout(n: int = 20, stop_atr: float = 2.0, vol_mult: float = 0.0, btc: bool = False, ema200: bool = False) -> Gen:
    """breakout_4h_v1 (signals.analyze_breakout) and its variants."""
    def g(f: ix.Series, j: int, coin: str, fund: Funding, cx: Ctx) -> tuple[str, float] | None:
        atr, e50 = f.atr[j], f.e50[j]
        if j < max(n, 60) or not atr or e50 is None:
            return None
        p = f.c[j]
        if not 0.4 <= atr / p * 100 <= 8:
            return None
        hi, lo = max(f.h[j - n:j]), min(f.l[j - n:j])
        side = "LONG" if p > hi and p > e50 else "SHORT" if p < lo and p < e50 else None
        if side is None:
            return None
        tc = f.t[j] + f.interval_ms
        if _crowded(side, fund.at(tc)):
            return None
        if vol_mult and not (cx.vol20[j] and f.v[j] >= vol_mult * cx.vol20[j]):  # type: ignore[operator]
            return None
        if btc and _btc_against(cx.btc, tc, side, coin):
            return None
        if ema200:
            e2 = f.e200[j]
            if e2 is None or (side == "LONG" and p < e2) or (side == "SHORT" and p > e2):
                return None
        return side, p - (1 if side == "LONG" else -1) * stop_atr * atr
    return g


def pullback(stop_atr: float = 1.5, adx_min: float = 0.0) -> Gen:
    """trend_pullback_v1 (signals.analyze) on whatever timeframe the series is."""
    def g(f: ix.Series, j: int, coin: str, fund: Funding, cx: Ctx) -> tuple[str, float] | None:
        e20, e50, atr, rsi = f.e20[j], f.e50[j], f.atr[j], f.rsi[j]
        if j < 120 or None in (e20, e50, atr, rsi, f.e50[j - 5]) or cx.macd[j] is None or cx.macd[j - 1] is None:
            return None
        assert e20 and e50 and atr and rsi is not None
        p = f.c[j]
        up = e20 > e50 and e50 > f.e50[j - 5] and p > e50  # type: ignore[operator]
        down = e20 < e50 and e50 < f.e50[j - 5] and p < e50  # type: ignore[operator]
        if not (up or down) or not 0.25 <= atr / p * 100 <= 5 or abs(p - e20) > 0.6 * atr:
            return None
        side = "LONG" if up else "SHORT"
        if side == "LONG" and not 40 <= rsi <= 65 or side == "SHORT" and not 35 <= rsi <= 60:
            return None
        h, hp = cx.macd[j], cx.macd[j - 1]
        turning = (up and h > hp) or (down and h < hp)  # type: ignore[operator]
        if not turning and (up and h <= 0 or down and h >= 0):  # type: ignore[operator]
            return None
        if adx_min and (f.adx[j] is None or f.adx[j] < adx_min):  # type: ignore[operator]
            return None
        tc = f.t[j] + f.interval_ms
        if _crowded(side, fund.at(tc)) or _btc_against(cx.btc, tc, side, coin):
            return None
        sg = 1 if up else -1
        wall = max(f.h[j - 48:j]) if up else min(f.l[j - 48:j])
        if 0 < sg * (wall - p) < 0.5 * atr:
            return None
        return side, p - sg * stop_atr * atr
    return g


def meanrev(rsi_lo: float = 25, adx_max: float = 20, stop_atr: float = 1.5) -> Gen:
    """Candidate: fade a Bollinger-band extreme in a NON-trending market (ADX low), target back to the middle band."""
    def g(f: ix.Series, j: int, coin: str, fund: Funding, cx: Ctx) -> tuple[str, float] | None:
        atr, rsi, adx = f.atr[j], f.rsi[j], f.adx[j]
        if j < 60 or not atr or rsi is None or adx is None or adx > adx_max:
            return None
        w = f.c[j - 19:j + 1]
        mid = sum(w) / 20
        sd = (sum((x - mid) ** 2 for x in w) / 20) ** 0.5
        p = f.c[j]
        side = "LONG" if p < mid - 2 * sd and rsi < rsi_lo else "SHORT" if p > mid + 2 * sd and rsi > 100 - rsi_lo else None
        if side is None or _crowded(side, fund.at(f.t[j] + f.interval_ms)):
            return None
        return side, p - (1 if side == "LONG" else -1) * stop_atr * atr
    return g


# ---------------------------------------------------------------- runs
@dataclass(frozen=True)
class Variant:
    name: str
    strategy: str  # which live strategy it would replace
    tf: str
    gen: Gen
    exit: Exit
    implementable: bool = True
    note: str = ""


def candidates(coins: list[str], v: Variant, cache: dict[str, Any]) -> list[bt.Trade]:
    out: list[bt.Trade] = []
    btc = cache.setdefault(("BTC", v.tf), bt.load("BTC", v.tf))
    for coin in coins:
        f = cache.setdefault((coin, v.tf), bt.load(coin, v.tf))
        fund = cache.setdefault(("fund", coin), funding(coin))
        cx = cache.get(("ctx", coin, v.tf))
        if cx is None:
            vol20 = [None] * len(f.v)
            for i in range(20, len(f.v)):
                vol20[i] = sum(f.v[i - 20:i]) / 20
            cx = cache[("ctx", coin, v.tf)] = Ctx(_macd_hist(f.c), btc, vol20)  # type: ignore[arg-type]
        last_exit = 0
        for j in range(1, len(f.t) - 1):
            tc = f.t[j] + f.interval_ms
            if tc < last_exit:
                continue
            s = v.gen(f, j, coin, fund, cx)
            if s is None:
                continue
            r = simulate(f, j, s[0], s[1], v.exit, coin, fund)
            if r is None:
                continue
            out.append(bt.Trade(coin, v.name, s[0], "-", tc, r["t_entry"], r["t_exit"], r["entry"], s[1], r["r_net"], r["r_gross"],
                                r["cost_r"], r["exit"], r["hold_min"]))
            last_exit = r["t_exit"]
    return out


LIVE_EXIT = Exit(2.0, 3.0)
VARIANTS: list[Variant] = [
    # ---- breakout_4h_v1 (live: 20-candle close break with the EMA50, stop 2 ATR, TP1 2R half, TP2 3R)
    Variant("breakout LIVE", "breakout_4h_v1", "4h", breakout(), LIVE_EXIT),
    Variant("breakout TP1 1R / TP2 3R", "breakout_4h_v1", "4h", breakout(), Exit(1.0, 3.0), note="bank half sooner"),
    Variant("breakout TP1 2R / TP2 5R", "breakout_4h_v1", "4h", breakout(), Exit(2.0, 5.0), note="let the runner run"),
    Variant("breakout TP1 1.5R / TP2 4R", "breakout_4h_v1", "4h", breakout(), Exit(1.5, 4.0)),
    Variant("breakout + BTC agrees", "breakout_4h_v1", "4h", breakout(btc=True), LIVE_EXIT, note="alts only with BTC's 4h trend"),
    Variant("breakout + volume 1.5x", "breakout_4h_v1", "4h", breakout(vol_mult=1.5), LIVE_EXIT, note="breakout candle volume"),
    Variant("breakout + EMA200 side", "breakout_4h_v1", "4h", breakout(ema200=True), LIVE_EXIT, note="long-term trend"),
    Variant("breakout 55-candle", "breakout_4h_v1", "4h", breakout(n=55), LIVE_EXIT, note="bigger breakouts only"),
    Variant("breakout stop 1.5 ATR", "breakout_4h_v1", "4h", breakout(stop_atr=1.5), LIVE_EXIT),
    Variant("breakout stop 3 ATR", "breakout_4h_v1", "4h", breakout(stop_atr=3.0), LIVE_EXIT),
    Variant("breakout LIVE + BE after TP1", "breakout_4h_v1", "4h", breakout(), Exit(2.0, 3.0, be=True), implementable=False,
            note="needs a stop move after TP1"),
    # ---- trend_pullback_v1 (live on 1h: stop 1.5 ATR, target 3 ATR = TP1 2R half, TP2 3R)
    Variant("pullback 1h LIVE", "trend_pullback_v1", "1h", pullback(), LIVE_EXIT),
    Variant("pullback 1h ADX 25", "trend_pullback_v1", "1h", pullback(adx_min=25), LIVE_EXIT),
    Variant("pullback 1h TP1 1R / TP2 2R", "trend_pullback_v1", "1h", pullback(), Exit(1.0, 2.0)),
    Variant("pullback 1h stop 2.5 ATR", "trend_pullback_v1", "1h", pullback(stop_atr=2.5), Exit(1.5, 2.5)),
    Variant("pullback on 4h", "trend_pullback_v1", "4h", pullback(), LIVE_EXIT, note="same rules on 4h candles"),
    Variant("pullback on 4h ADX 25", "trend_pullback_v1", "4h", pullback(adx_min=25), LIVE_EXIT),
    # ---- candidate new strategy (mean reversion), only adopted if it passes the same rule
    Variant("meanrev 1h", "meanrev", "1h", meanrev(), Exit(1.0, 1.0, full_at_tp1=True, max_hold_h=24), implementable=False,
            note="needs a time exit"),
    Variant("meanrev 4h", "meanrev", "4h", meanrev(), Exit(1.0, 2.0)),
]


def evaluate(coins: list[str]) -> dict[str, Any]:
    cache: dict[str, Any] = {}
    rep: dict[str, Any] = {"coins": coins, "rule": __doc__.split("Decision rule")[1].split("Research only")[0].strip(), "variants": {}}
    for v in VARIANTS:
        p = bt.portfolio(candidates(coins, v, cache))
        f = cache[("BTC", v.tf)]
        start, end = f.t[60], f.t[-1]
        cut = start + int((end - start) * SELECTION_SHARE)
        sel, hold = bt.split(p, cut)
        folds = [start + (end - start) * i // 3 for i in range(4)]
        rep["variants"][v.name] = {
            "strategy": v.strategy, "tf": v.tf, "exit": v.exit.__dict__, "implementable": v.implementable, "note": v.note,
            "period": [start, end, cut], "all": bt.metrics(p), "selection": bt.metrics(sel), "holdout": bt.metrics(hold),
            "folds": [bt.metrics([t for t in p if folds[i] <= t.t_entry < folds[i + 1]]).get("expectancy_r") for i in range(3)],
            "by_side": bt.by(p, "side"), "by_exit": bt.by(p, "exit"), "by_coin": bt.by(p, "coin")}
    for strat in ("breakout_4h_v1", "trend_pullback_v1", "meanrev"):
        live = next((rep["variants"][v.name] for v in VARIANTS if v.strategy == strat and "LIVE" in v.name), None)
        best, verdicts = None, {}
        for v in VARIANTS:
            if v.strategy != strat or "LIVE" in v.name:
                continue
            r = rep["variants"][v.name]
            s, h = r["selection"].get("expectancy_r", -9), r["holdout"]
            checks = {"beats_live_on_selection": live is None or s > live["selection"].get("expectancy_r", -9),
                      "holdout_positive": h.get("expectancy_r", -9) > 0 and h.get("trades", 0) >= MIN_HOLDOUT_TRADES,
                      "holdout_not_worse": live is None or h.get("expectancy_r", -9) >= live["holdout"].get("expectancy_r", -9),
                      "implementable": v.implementable}
            verdicts[v.name] = checks
            if all(checks.values()) and (best is None or s > rep["variants"][best]["selection"]["expectancy_r"]):
                best = v.name
        live_neg = live is not None and live["selection"].get("expectancy_r", 0) < 0 and live["holdout"].get("expectancy_r", 0) < 0
        rep.setdefault("decisions", {})[strat] = {"checks": verdicts, "adopt": best,
                                                  "switch_off": best is None and live_neg}
    return rep


def _line(n: str, v: dict[str, Any]) -> str:
    a, s, h = v["all"], v["selection"], v["holdout"]
    return (f"{n:32s} n={a.get('trades', 0):4d} win={a.get('win_rate')}% exp={a.get('expectancy_r')}R ci={a.get('expectancy_r_90ci')} "
            f"dd={a.get('max_dd_pct')}% | sel {s.get('trades', 0)}t {s.get('expectancy_r')}R | hold {h.get('trades', 0)}t {h.get('expectancy_r')}R "
            f"| folds {v['folds']}")


if __name__ == "__main__":
    r = evaluate([c.upper() for c in sys.argv[1:]] or history.COINS)
    (history.DIR / "lab.json").write_text(json.dumps(r, indent=1))
    for n, v in r["variants"].items():
        print(_line(n, v))
    print(json.dumps(r["decisions"], indent=1))
