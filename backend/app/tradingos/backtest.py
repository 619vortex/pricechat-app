"""Backtester for the Trading OS: the CURRENT live strategy (baseline) and the intraday entry models, on real Hyperliquid
history (app.tradingos.history). Research only: nothing here changes live trading.

Honest-by-construction rules:
  · signals are evaluated on CLOSED candles only; higher timeframes use their last CLOSED candle at that moment
  · entry = the NEXT candle's open plus slippage (never the signal candle's close); skipped if it opens beyond the stop
  · when one candle touches both the stop and a target, the stop is assumed first (conservative)
  · costs: taker fee 0.045% on every fill, slippage per side (wider for alts), real hourly funding from Hyperliquid
  · portfolio: at most 2 open positions, one per coin, 0.5% of equity risked per trade, daily loss stop at -2%
  · chronological split: the first half (selection) is where decisions are made; the second half (holdout) is only
    looked at to confirm them. Nothing is optimised on the holdout.
Run: python -m app.tradingos.backtest
"""

import json
import random
import statistics
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass
from typing import Any

from app.tradingos import history
from app.tradingos import indicators as ind
from app.tradingos import intraday as ix
from app.tradingos.market import INTERVALS

FEE = 0.00045
SLIP = {"BTC": 0.0002, "ETH": 0.0002}  # per side; everything else 0.0005 (half the spread plus impact, conservative)
RISK_PCT = 0.5  # % of equity per trade
MAX_OPEN, DAILY_STOP_PCT = 2, -2.0


def slip(coin: str) -> float:
    return SLIP.get(coin, 0.0005)


@dataclass
class Trade:
    coin: str
    strategy: str
    side: str
    regime: str
    t_signal: int
    t_entry: int
    t_exit: int
    entry: float
    stop: float
    r_net: float  # net result in R (risk = |entry - stop|), after fees, slippage and funding
    r_gross: float
    cost_r: float
    exit: str  # stop | tp1+stop | tp1+tp2 | tp2 | time | tp1+time
    hold_min: float
    score: int = 0


def _funding_rate_at(fund: list[tuple[int, float]], t: int) -> float:
    lo, hi, ans = 0, len(fund) - 1, 0.0
    while lo <= hi:
        mid = (lo + hi) // 2
        if fund[mid][0] <= t:
            ans, lo = fund[mid][1], mid + 1
        else:
            hi = mid - 1
    return ans


def simulate(f: ix.Series, i: int, side: str, stop: float, r1: float | None, r2: float, max_hold_min: int, coin: str,
             fund: list[tuple[int, float]], be_after_tp1: bool = False, fixed_targets: tuple[float, float] | None = None) -> dict[str, Any] | None:
    """One trade from the signal on closed candle i. r1=None means a single full exit at the (r2 or fixed) target."""
    if i + 1 >= len(f.t):
        return None
    long, sg, sl = side == "LONG", (1 if side == "LONG" else -1), slip(coin)
    entry = f.o[i + 1] * (1 + sg * sl)
    if (long and entry <= stop) or (not long and entry >= stop):
        return None  # opened beyond the stop: no trade
    R = abs(entry - stop)
    if fixed_targets:
        tp1, tp2 = fixed_targets
    else:
        tp1 = entry + sg * (r1 or r2) * R
        tp2 = entry + sg * r2 * R
    t_entry = f.t[i + 1]
    fills: list[tuple[int, float, float]] = []  # (time, price, fraction)
    rem, cur_stop, tp1_done, exit_kind = 1.0, stop, r1 is None, ""
    for b in range(i + 1, len(f.t)):
        tb_close = f.t[b] + f.interval_ms
        if (long and f.l[b] <= cur_stop) or (not long and f.h[b] >= cur_stop):
            px = min(cur_stop, f.o[b]) if long else max(cur_stop, f.o[b])  # a gap through the stop fills at the open
            fills.append((tb_close, px * (1 - sg * sl), rem))
            exit_kind = ("tp1+" if r1 is not None and tp1_done else "") + ("be" if be_after_tp1 and tp1_done else "stop")
            rem = 0
            break
        if not tp1_done and ((long and f.h[b] >= tp1) or (not long and f.l[b] <= tp1)):
            fills.append((tb_close, tp1 * (1 - sg * sl), 0.5))
            rem, tp1_done = 0.5, True
            if be_after_tp1:
                cur_stop = entry
        if tp1_done and ((long and f.h[b] >= tp2) or (not long and f.l[b] <= tp2)):
            fills.append((tb_close, tp2 * (1 - sg * sl), rem))
            exit_kind = "tp1+tp2" if r1 is not None else "target"
            rem = 0
            break
        if (tb_close - t_entry) / 60_000 >= max_hold_min:
            fills.append((tb_close, f.c[b] * (1 - sg * sl), rem))
            exit_kind = ("tp1+" if r1 is not None and tp1_done else "") + "time"
            rem = 0
            break
    if rem > 0:
        return None  # ran out of data before the trade finished
    gross = sum((px - entry) * sg * fr for _, px, fr in fills)
    fees = entry * FEE + sum(px * FEE * fr for _, px, fr in fills)
    slip_cost = entry * sl + sum(px * sl * fr for _, px, fr in fills)  # already inside the prices; reported as part of cost
    funding = 0.0
    for t, rate in fund:
        if t_entry < t <= fills[-1][0]:
            left = sum(fr for ft, _, fr in fills if ft >= t)
            funding += rate * entry * left * sg  # longs pay positive funding, shorts receive it
    net = gross - fees - funding
    t_exit = fills[-1][0]
    return {"entry": entry, "t_entry": t_entry, "t_exit": t_exit, "r_net": net / R, "r_gross": (gross + slip_cost) / R,
            "cost_r": (fees + funding + slip_cost) / R, "exit": exit_kind, "hold_min": (t_exit - t_entry) / 60_000}


# ---------------------------------------------------------------- candidates
def load(coin: str, interval: str) -> ix.Series:
    return ix.series(history.candles(coin, interval), INTERVALS[interval] * 1000)


def intraday_candidates(coins: list[str], enabled: tuple[str, ...], interval: str = "15m", be: bool = False,
                        min_stop_pct: float = ix.MIN_STOP_PCT, min_quality: int = 0) -> list[Trade]:
    out: list[Trade] = []
    for coin in coins:
        f, h1, h4, fund = load(coin, interval), load(coin, "1h"), load(coin, "4h"), history.funding(coin)
        last_exit = 0
        for i in range(210, len(f.t) - 1):
            close_t = f.t[i] + f.interval_ms
            if close_t < last_exit:
                continue  # one position per coin
            j, k = ix.last_closed(h1, close_t), ix.last_closed(h4, close_t)
            reg, _ = ix.regime(h1, h4, j, k)
            h1_dir = "up" if (h1.e20[j] or 0) > (h1.e50[j] or 0) else "down"
            found, _ = ix.setups(f, i, reg, h1_dir, enabled, min_stop_pct)
            if not found:
                continue
            best = None
            for s in found:
                cost_r = (2 * FEE + 2 * slip(coin)) * s.ref / abs(s.ref - s.stop)
                q = ix.quality(s, slip(coin) * 2e4, cost_r)
                if best is None or q > best[1]:
                    best = (s, q)
            s, q = best  # type: ignore[misc]
            if q < min_quality:
                continue
            r = simulate(f, i, s.side, s.stop, s.r1, s.r2, s.max_hold_min, coin, fund, be)
            if r is None:
                continue
            out.append(Trade(coin, s.strategy, s.side, reg, close_t, r["t_entry"], r["t_exit"], r["entry"], s.stop, r["r_net"], r["r_gross"],
                             r["cost_r"], r["exit"], r["hold_min"], q))
            last_exit = r["t_exit"]
    return out


def baseline_candidates(coins: list[str], start_ms: int = 0, regime_filter: bool = False, max_hold_h: int = 48,
                        r_target: float | None = None) -> list[Trade]:
    """The CURRENT live rules (signals.analyze, trend_pullback_v1 on 1h) replayed on closed 1h candles: same EMA/RSI/MACD/
    BTC/funding/volatility/swing-wall conditions, stop 1.5 ATR, target 3 ATR (the signal's TP1, closed in full as the live
    signal tracker does), closed at market after 48h. Liquidity filters (volume/spread/depth) can't be replayed and are
    left out (all test coins are liquid)."""
    out: list[Trade] = []
    btc = load("BTC", "1h")
    for coin in coins:
        f, fund = load(coin, "1h"), history.funding(coin)
        h4 = load(coin, "4h")
        last_exit = 0
        for j in range(120, len(f.t) - 1):
            close_t = f.t[j] + f.interval_ms
            if close_t < start_ms or close_t < last_exit:
                continue
            closes = f.c[max(0, j - 199):j + 1]
            highs, lows = f.h[max(0, j - 199):j + 1], f.l[max(0, j - 199):j + 1]
            e20, e50 = ind.ema(closes, 20), ind.ema(closes, 50)
            rsi, macd, atr = ind.rsi(closes), ind.macd(closes), ind.atr(highs, lows, closes)
            macd_prev = ind.macd(closes[:-1])
            if not (e20 and e50 and rsi is not None and macd and atr):
                continue
            price = closes[-1]
            up = e20[-1] > e50[-1] and e50[-1] > e50[-6] and price > e50[-1]
            down = e20[-1] < e50[-1] and e50[-1] < e50[-6] and price < e50[-1]
            if not (up or down):
                continue
            side = "LONG" if up else "SHORT"
            if not 0.25 <= atr / price * 100 <= 5 or abs(price - e20[-1]) > 0.6 * atr:
                continue
            if side == "LONG" and not 40 <= rsi <= 65 or side == "SHORT" and not 35 <= rsi <= 60:
                continue
            turning = macd_prev is not None and ((side == "LONG" and macd[2] > macd_prev[2]) or (side == "SHORT" and macd[2] < macd_prev[2]))
            if not turning and (side == "LONG" and macd[2] <= 0 or side == "SHORT" and macd[2] >= 0):
                continue
            fr = _funding_rate_at(fund, close_t)
            if side == "LONG" and fr > 0.0001 or side == "SHORT" and fr < -0.0001:
                continue
            if coin != "BTC":
                bj = ix.last_closed(btc, close_t)
                bc = btc.c[max(0, bj - 119):bj + 1]
                b50 = ind.ema(bc, 50)
                if b50 and (side == "LONG" and bc[-1] < b50[-1] or side == "SHORT" and bc[-1] > b50[-1]):
                    continue
            support, resistance = ind.swing_levels(highs[:-1], lows[:-1], 48)
            sg = 1 if side == "LONG" else -1
            wall = resistance if side == "LONG" else support
            if 0 < sg * (wall - price) < 0.5 * atr:
                continue
            reg = "-"
            if regime_filter:
                reg, _ = ix.regime(f, h4, j, ix.last_closed(h4, close_t))
                if reg != ("STRONG_BULL" if side == "LONG" else "STRONG_BEAR"):
                    continue  # only when the regime classifier agrees on a strong trend in this direction
            stop, target = price - sg * 1.5 * atr, price + sg * 3.0 * atr
            if r_target is not None:
                r = simulate(f, j, side, stop, 1.0, r_target, max_hold_h * 60, coin, fund)
            else:
                r = simulate(f, j, side, stop, None, 0, max_hold_h * 60, coin, fund, fixed_targets=(target, target))
            if r is None:
                continue
            out.append(Trade(coin, "baseline_trend_pullback_v1", side, reg, close_t, r["t_entry"], r["t_exit"], r["entry"], stop,
                             r["r_net"], r["r_gross"], r["cost_r"], r["exit"], r["hold_min"]))
            last_exit = r["t_exit"]
    return out


# ---------------------------------------------------------------- portfolio + metrics
def portfolio(cands: list[Trade]) -> list[Trade]:
    """Chronological: at most MAX_OPEN positions, one per coin, and no new trades on a day once -2% is reached."""
    taken: list[Trade] = []
    day_pnl: dict[int, float] = defaultdict(float)
    for t in sorted(cands, key=lambda x: x.t_entry):
        open_now = [x for x in taken if x.t_entry <= t.t_entry < x.t_exit]
        day = t.t_entry // 86_400_000
        closed_today = sum(x.r_net * RISK_PCT for x in taken if x.t_exit // 86_400_000 == day and x.t_exit <= t.t_entry)
        if len(open_now) >= MAX_OPEN or any(x.coin == t.coin for x in open_now) or closed_today <= DAILY_STOP_PCT:
            continue
        taken.append(t)
        day_pnl[day] += t.r_net * RISK_PCT
    return taken


def metrics(ts: list[Trade]) -> dict[str, Any]:
    n = len(ts)
    if not n:
        return {"trades": 0}
    r = [t.r_net for t in sorted(ts, key=lambda x: x.t_exit)]
    wins, losses = [x for x in r if x > 0], [x for x in r if x <= 0]
    eq = peak = mdd = 0.0
    for x in r:
        eq += x * RISK_PCT
        peak = max(peak, eq)
        mdd = max(mdd, peak - eq)
    rnd = random.Random(7)
    boots = sorted(statistics.fmean(rnd.choices(r, k=n)) for _ in range(2000))
    holds = sorted(t.hold_min for t in ts)
    days: dict[int, float] = defaultdict(float)
    for t in ts:
        days[t.t_exit // 86_400_000] += t.r_net * RISK_PCT
    span = (max(t.t_exit for t in ts) - min(t.t_entry for t in ts)) / 86_400_000
    daily = [days.get(d, 0.0) for d in range(min(days), max(days) + 1)]
    out = {"trades": n, "win_rate": round(len(wins) / n * 100, 1), "avg_win_r": round(statistics.fmean(wins), 2) if wins else None,
           "avg_loss_r": round(statistics.fmean(losses), 2) if losses else None,
           "profit_factor": round(sum(wins) / -sum(losses), 2) if losses and sum(losses) else None,
           "expectancy_r": round(statistics.fmean(r), 3), "expectancy_r_90ci": [round(boots[100], 3), round(boots[1899], 3)],
           "net_return_pct": round(sum(r) * RISK_PCT, 2), "max_dd_pct": round(mdd, 2), "avg_cost_r": round(statistics.fmean(t.cost_r for t in ts), 3),
           "median_hold_min": round(holds[n // 2]), "closed_within_h": {h: round(sum(1 for x in holds if x <= h * 60) / n * 100) for h in (1, 2, 3)},
           "days": round(span, 1), "trades_per_day": round(n / span, 2) if span else None}
    if len(daily) >= 20 and statistics.pstdev(daily) > 0:
        out["sharpe_daily_ann"] = round(statistics.fmean(daily) / statistics.pstdev(daily) * 365 ** 0.5, 2)
        neg = [d for d in daily if d < 0]
        if len(neg) > 3:
            out["sortino_daily_ann"] = round(statistics.fmean(daily) / (sum(d * d for d in neg) / len(daily)) ** 0.5 * 365 ** 0.5, 2)
    return out


def by(ts: list[Trade], key: str) -> dict[str, dict[str, Any]]:
    g: dict[str, list[Trade]] = defaultdict(list)
    for t in ts:
        g[str(getattr(t, key))].append(t)
    return {k: {kk: v for kk, v in metrics(v).items() if kk in ("trades", "win_rate", "expectancy_r", "expectancy_r_90ci", "net_return_pct", "profit_factor")}
            for k, v in sorted(g.items())}


def split(ts: list[Trade], cut: int) -> tuple[list[Trade], list[Trade]]:
    return [t for t in ts if t.t_entry < cut], [t for t in ts if t.t_entry >= cut]


def windows(ts: list[Trade], start: int, end: int, n: int = 4) -> list[dict[str, Any]]:
    step = (end - start) / n
    out = []
    for w in range(n):
        a, b = start + w * step, start + (w + 1) * step
        m = metrics([t for t in ts if a <= t.t_entry < b])
        out.append({"window": w + 1, **{k: m.get(k) for k in ("trades", "win_rate", "expectancy_r", "net_return_pct")}})
    return out


def run(coins: list[str]) -> dict[str, Any]:
    f15 = load("BTC", "15m")
    start, end = f15.t[210], f15.t[-1]
    cut = start + (end - start) // 2
    rep: dict[str, Any] = {"coins": coins, "period_15m": [start, end], "cut": cut,
                           "assumptions": {"fee_per_side": FEE, "slippage_per_side": {"BTC/ETH": 0.0002, "others": 0.0005}, "risk_per_trade_pct": RISK_PCT,
                                           "max_open": MAX_OPEN, "daily_stop_pct": DAILY_STOP_PCT, "funding": "real hourly Hyperliquid funding",
                                           "intrabar": "stop first when a candle touches stop and target", "entry": "next candle open + slippage"}}
    base_all = portfolio(baseline_candidates(coins))
    base_same = [t for t in base_all if t.t_entry >= start]
    rep["baseline_full_1h_history"] = metrics(base_all)
    rep["baseline_same_window"] = metrics(base_same)
    rep["baseline_split"] = {"selection": metrics(split(base_same, cut)[0]), "holdout": metrics(split(base_same, cut)[1])}
    rep["baseline_by_coin"] = by(base_all, "coin")
    rep["baseline_windows"] = windows(base_all, base_all[0].t_entry if base_all else 0, end, 6)
    per: dict[str, Any] = {}
    cands: dict[str, list[Trade]] = {}
    for s in ix.STRATEGIES:
        c = intraday_candidates(coins, (s,))
        cands[s] = c
        p = portfolio(c)
        sel, hold = split(p, cut)
        per[s] = {"all": metrics(p), "selection": metrics(sel), "holdout": metrics(hold), "by_regime": by(p, "regime"),
                  "by_side": by(p, "side"), "by_exit": by(p, "exit"), "windows": windows(p, start, end)}
    rep["strategies"] = per
    # pre-declared variants (decided before looking at the holdout; all reported, kept only if the SELECTION half is positive)
    var: dict[str, Any] = {}
    for name, kw in (("min_stop_0.5pct", {"min_stop_pct": 0.005}), ("min_stop_0.5pct_q60", {"min_stop_pct": 0.005, "min_quality": 60})):
        for st in ix.STRATEGIES:
            p = portfolio(intraday_candidates(coins, (st,), **kw))
            sel, hold = split(p, cut)
            var[f"{st}|{name}"] = {"selection": metrics(sel), "holdout": metrics(hold)}
    for name, kw in (("regime_filter", {"regime_filter": True}), ("intraday_exit_1R_2R_6h", {"max_hold_h": 6, "r_target": 2.0}),
                     ("regime_filter+intraday_exit", {"regime_filter": True, "max_hold_h": 6, "r_target": 2.0})):
        p = portfolio(baseline_candidates(coins, **kw))
        sel, hold = split(p, cut)
        half = (p[0].t_entry + p[-1].t_entry) // 2 if p else 0
        var[f"baseline|{name}"] = {"full": metrics(p), "full_first_half": metrics(split(p, half)[0]), "full_second_half": metrics(split(p, half)[1]),
                                   "selection": metrics(sel), "holdout": metrics(hold)}
    rep["variants"] = var
    # retained = positive expectancy in the SELECTION half with at least 15 trades (decided there); the holdout confirms
    keep = [s for s in ix.STRATEGIES if (per[s]["selection"].get("trades", 0) >= 15 and per[s]["selection"].get("expectancy_r", -1) > 0)]
    rep["selected_on_first_half"] = keep
    if keep:
        combo = portfolio(intraday_candidates(coins, tuple(keep)))
        sel, hold = split(combo, cut)
        rep["combined"] = {"all": metrics(combo), "selection": metrics(sel), "holdout": metrics(hold), "by_strategy": by(combo, "strategy"),
                           "by_regime": by(combo, "regime"), "by_coin": by(combo, "coin"), "windows": windows(combo, start, end)}
        be = portfolio(intraday_candidates(coins, tuple(keep), be=True))
        rep["combined_breakeven_after_tp1"] = {"all": metrics(be), "holdout": metrics(split(be, cut)[1])}
        buckets: dict[str, list[Trade]] = defaultdict(list)
        for t in combo:
            buckets["<50" if t.score < 50 else "50-59" if t.score < 60 else "60-69" if t.score < 70 else "70+"].append(t)
        rep["combined_by_score"] = {k: {kk: v for kk, v in metrics(v).items() if kk in ("trades", "win_rate", "expectancy_r", "expectancy_r_90ci")}
                                    for k, v in sorted(buckets.items())}
    return rep


if __name__ == "__main__":
    coins = [c.upper() for c in sys.argv[1:]] or history.COINS
    report = run(coins)
    (history.DIR / "report.json").write_text(json.dumps(report, indent=1, default=lambda o: asdict(o)))
    print(json.dumps(report, indent=1)[:20000])
